from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .models import OrganizationSeed, SourceClass
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
