from __future__ import annotations

import os
from pathlib import Path

import uuid
import traceback

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .models import OrganizationSeed, PublicDocument, SourceClass
from .graph_models import (
    GraphFeedbackInput,
    GraphNodeType,
    ProcessGraphInput,
    stable_node_id,
)
from .neo4j_export import graph_to_neo4j_batch
from .crm_projection import graph_to_crm_projection
from .pipeline import OSINTPipeline
from .web_ui import SEARCH_PAGE


class InvestigationRequest(BaseModel):
    organization: OrganizationSeed
    legacy_path: str | None = None
    sources: list[tuple[str, SourceClass]] = Field(default_factory=list)
    crawl: bool = False


class FeedbackRequest(BaseModel):
    persona: str
    variant: str
    useful: bool
    note: str = ""


class CNPJSearchRequest(BaseModel):
    cnpj: str
    legal_name: str | None = None
    state: str | None = None
    deep: bool = True
    use_tavily: bool = True
    seed_urls: list[str] = Field(default_factory=list)


class ProcessGraphRequest(BaseModel):
    process: ProcessGraphInput
    enrich_tavily: bool = False
    deep: bool = True


def create_app(database: str | Path | None = None) -> FastAPI:
    db = Path(database or os.environ.get("VF_OSINT_DB", "data/vf_osint.db"))
    pipeline = OSINTPipeline(db)
    app = FastAPI(title="V&F Graph Intelligence System", version="0.5.0")

    # CORS — o CRM (browser) chama /api/search/cnpj de outro domínio.
    # Restrinja allow_origins ao domínio do CRM em produção se quiser.
    _origins = os.environ.get("CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _origins],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Escrita direta no banco (Supabase é a verdade) ----
    def _ingest_supabase(projection: dict) -> str:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            return "sem SUPABASE_URL/SUPABASE_SERVICE_KEY — projeção não gravada (defina no Render)"
        try:
            import httpx
            r = httpx.post(
                f"{url}/rest/v1/rpc/fn_ingest_crm_projection",
                headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"p_projection": projection}, timeout=30,
            )
            return f"ok {r.status_code}" if r.status_code < 300 else f"erro {r.status_code}: {r.text[:120]}"
        except Exception as exc:  # noqa: BLE001
            return f"falha: {exc}"

    # ---- Leitura do banco: pessoas já conhecidas (QSA + advogados do processo) ----
    # A varredura não descobre nomes; ela CONFIRMA/enriquece quem já sabemos.
    def _fetch_known_people(cnpj: str) -> list[dict]:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            return []
        raiz = "".join(c for c in cnpj if c.isdigit())[:8]
        if len(raiz) != 8:
            return []
        try:
            import httpx
            r = httpx.get(
                f"{url}/rest/v1/decisores",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                params={"cnpj_raiz": f"eq.{raiz}", "select": "nome,cargo"},
                timeout=20,
            )
            if r.status_code >= 300:
                return []
            return [row for row in r.json() if isinstance(row, dict) and row.get("nome")]
        except Exception:  # noqa: BLE001
            return []

    # ---- Documentos que já temos no banco: inteiro teor do DJEN (fonte oficial) ----
    # Grounding sem Tavily: os eventos do processo confirmam decisores/eventos e a
    # âncora captura qualquer contato presente. A varredura web vira complemento.
    def _fetch_process_documents(cnpj: str, limit: int = 60) -> list[PublicDocument]:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            return []
        raiz = "".join(c for c in cnpj if c.isdigit())[:8]
        if len(raiz) != 8:
            return []
        try:
            import httpx
            r = httpx.get(
                f"{url}/rest/v1/eventos",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                params={
                    "cnpj_raiz": f"eq.{raiz}",
                    "texto": "not.is.null",
                    "select": "numero_processo,tipo,texto,advogados,link_publicacao,ocorrido_em",
                    "order": "ocorrido_em.desc",
                    "limit": str(limit),
                },
                timeout=25,
            )
            if r.status_code >= 300:
                return []
            docs: list[PublicDocument] = []
            for row in r.json():
                texto = (row.get("texto") or "").strip()
                if not texto:
                    continue
                # Anexa nomes dos advogados intimados (garante que a âncora os confirme
                # mesmo quando o corpo abrevia); nome vem do próprio ato oficial.
                advs = row.get("advogados") or []
                nomes = " ".join(
                    str(a.get("nome") or "") for a in advs if isinstance(a, dict)
                ).strip()
                corpo = f"{texto}\n{nomes}" if nomes else texto
                proc = row.get("numero_processo") or ""
                link = row.get("link_publicacao") or f"djen://evento/{proc}"
                docs.append(
                    PublicDocument(
                        url=link,
                        title=f"DJEN {proc} {row.get('tipo') or ''}".strip(),
                        text=corpo,
                        source_class=SourceClass.OFFICIAL_COURT,
                    )
                )
            return docs
        except Exception:  # noqa: BLE001
            return []

    # ---- Jobs assíncronos: varredura completa em background ----
    # Consultoria = baixo volume; dict em memória basta (1 instância no Render).
    jobs: dict[str, dict] = {}

    def _run_dossie_job(job_id: str, req: CNPJSearchRequest) -> None:
        try:
            known_people = _fetch_known_people(req.cnpj)
            process_docs = _fetch_process_documents(req.cnpj)
            dossier, collection = pipeline.investigate_cnpj(
                req.cnpj, legal_name=req.legal_name, state=req.state,
                deep=req.deep, use_tavily=req.use_tavily, seed_urls=req.seed_urls,
                known_people=known_people, extra_documents=process_docs,
            )
            graph = pipeline.build_graph(dossier)
            projection = graph_to_crm_projection(graph)
            ingested = _ingest_supabase(projection)
            jobs[job_id] = {
                "status": "done",
                "graph_id": graph.graph_id,
                "crm_projection": projection,
                "pessoas_conhecidas": len(known_people),
                "documentos_do_banco": len(process_docs),
                "decisores": len(projection.get("crm_decisores", [])),
                "evidencias": len(projection.get("entity_evidence", [])),
                "banco": ingested,
                "collection": collection,
            }
        except Exception as exc:  # noqa: BLE001
            jobs[job_id] = {"status": "error", "erro": str(exc), "trace": traceback.format_exc()[-800:]}

    @app.post("/api/dossie/jobs")
    def enqueue_dossie(request: CNPJSearchRequest, background: BackgroundTasks) -> dict:
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"status": "processing"}
        background.add_task(_run_dossie_job, job_id, request)
        return {"job_id": job_id, "status": "processing"}

    @app.get("/api/dossie/jobs/{job_id}")
    def get_dossie_job(job_id: str) -> dict:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job nao encontrado")
        return {"job_id": job_id, **job}

    @app.get("/", response_class=HTMLResponse)
    def search_page():
        return SEARCH_PAGE

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/investigations")
    def create_investigation(request: InvestigationRequest) -> dict:
        collected = None
        if request.legacy_path:
            pipeline.ingest_legacy(request.organization, request.legacy_path)
        if request.crawl and request.sources:
            collected = pipeline.crawl(request.organization, request.sources)
        dossier = pipeline.build_dossier(request.organization)
        graph = pipeline.build_graph(dossier)
        return {
            "dossier": dossier,
            "graph": graph,
            "graph_url": f"/api/graphs/{graph.graph_id}",
            "collection": collected,
            "report_pdf": f"/api/dossiers/{dossier.dossier_id}/report.pdf",
        }

    @app.post("/api/search/cnpj")
    def search_by_cnpj(request: CNPJSearchRequest) -> dict:
        try:
            dossier, collection = pipeline.investigate_cnpj(
                request.cnpj,
                legal_name=request.legal_name,
                state=request.state,
                deep=request.deep,
                use_tavily=request.use_tavily,
                seed_urls=request.seed_urls,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        graph = pipeline.build_graph(dossier)
        return {
            "mode": "ENTITY_ENRICHMENT_ONLY",
            "dossier": dossier,
            "graph": graph,
            "graph_url": f"/api/graphs/{graph.graph_id}",
            "collection": collection,
            "report_pdf": f"/api/dossiers/{dossier.dossier_id}/report.pdf",
        }

    @app.get("/api/dossiers/{dossier_id}")
    def get_dossier(dossier_id: str):
        dossier = pipeline.repository.get_dossier(dossier_id)
        if not dossier:
            raise HTTPException(status_code=404, detail="Dossie nao encontrado")
        return dossier

    @app.post("/api/graph/processes")
    def ingest_process(request: ProcessGraphRequest) -> dict:
        try:
            graph, dossier, collection = pipeline.ingest_process_graph(
                request.process,
                enrich_tavily=request.enrich_tavily,
                deep=request.deep,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "mode": "PROCESS_FIRST_GRAPH",
            "graph": graph,
            "dossier": dossier,
            "collection": collection,
            "graph_url": f"/api/graphs/{graph.graph_id}",
            "neo4j_export_url": f"/api/graphs/{graph.graph_id}/neo4j",
            "crm_projection_url": f"/api/graphs/{graph.graph_id}/crm-projection",
        }

    @app.get("/api/graphs/{graph_id}")
    def get_graph(graph_id: str):
        graph = pipeline.repository.get_graph(graph_id)
        if not graph:
            raise HTTPException(status_code=404, detail="Grafo não encontrado")
        return graph

    @app.get("/api/graphs/process/{process_number}")
    def get_graphs_for_process(process_number: str):
        process_id = stable_node_id(GraphNodeType.PROCESS, process_number)
        return pipeline.repository.graphs_for_process(process_id)

    @app.get("/api/graphs/{graph_id}/neo4j")
    def export_graph_to_neo4j(graph_id: str):
        graph = pipeline.repository.get_graph(graph_id)
        if not graph:
            raise HTTPException(status_code=404, detail="Grafo não encontrado")
        return graph_to_neo4j_batch(graph)

    @app.get("/api/graphs/{graph_id}/crm-projection")
    def export_graph_to_crm(graph_id: str):
        graph = pipeline.repository.get_graph(graph_id)
        if not graph:
            raise HTTPException(status_code=404, detail="Grafo não encontrado")
        return graph_to_crm_projection(graph)

    @app.post("/api/graphs/{graph_id}/feedback")
    def submit_graph_feedback(graph_id: str, feedback: GraphFeedbackInput):
        try:
            updated = pipeline.apply_graph_feedback(graph_id, feedback)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "status": "recorded_as_new_snapshot",
            "source_graph_id": graph_id,
            "graph": updated,
            "graph_url": f"/api/graphs/{updated.graph_id}",
        }

    @app.get("/api/dossiers/{dossier_id}/report.pdf")
    def get_dossier_pdf(dossier_id: str):
        dossier = pipeline.repository.get_dossier(dossier_id)
        if not dossier:
            raise HTTPException(status_code=404, detail="Dossie nao encontrado")
        path = Path("output/pdf") / f"dossie_{dossier_id}.pdf"
        pipeline.write_dossier(dossier, path)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"dossie_{dossier_id}.pdf",
        )

    @app.post("/api/dossiers/{dossier_id}/feedback")
    def submit_feedback(dossier_id: str, feedback: FeedbackRequest) -> dict[str, str]:
        if not pipeline.repository.get_dossier(dossier_id):
            raise HTTPException(status_code=404, detail="Dossie nao encontrado")
        pipeline.learner.record_approach_feedback(
            dossier_id,
            feedback.persona,
            feedback.variant,
            feedback.useful,
            feedback.note,
        )
        return {"status": "recorded"}

    return app


app = create_app()
