from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .dossier import DossierBuilder, dossier_to_markdown
from .discovery import TavilyDiscovery, _host_is_excluded
from .evidence import (
    EvidenceReconciler,
    LegacyCRMExtractor,
    PublicEvidenceExtractor,
    _context_relevant,
)
from .fetch import PublicWebFetcher, SmartCrawler
from .learning import GuardedLearner
from .identity import assess_entity_identity
from .graph_engine import FiscalOpportunityGraphBuilder
from .graph_models import GraphFeedbackInput, GraphOpportunity, ProcessGraphInput
from .models import Dossier, OrganizationSeed, PublicDocument, SourceClass
from .policy import CollectionPolicy
from .pdf_report import render_dossier_pdf
from .storage import Repository
from .sources import classify_source


class OSINTPipeline:
    def __init__(self, database: str | Path, policy: CollectionPolicy | None = None):
        self.repository = Repository(database)
        self.policy = policy or CollectionPolicy()
        self.reconciler = EvidenceReconciler()
        self.public_extractor = PublicEvidenceExtractor()
        self.legacy_extractor = LegacyCRMExtractor()
        self.learner = GuardedLearner(self.repository)
        self.graph_builder = FiscalOpportunityGraphBuilder()
        # Pessoas já conhecidas (QSA/processo) para ancoragem por entidade durante a coleta.
        self._known_people: list[dict] = []

    def ingest_legacy(self, seed: OrganizationSeed, source_path: str | Path) -> int:
        path = Path(source_path)
        text = path.read_text(encoding="utf-8", errors="replace")
        claims = self.legacy_extractor.extract(seed, text, path.resolve().as_uri())
        for claim in claims:
            self.repository.save_claim(claim)
        return len(claims)

    def crawl(self, seed: OrganizationSeed, classified_urls: list[tuple[str, SourceClass]]) -> dict:
        fetcher = PublicWebFetcher(self.policy)
        try:
            normalized_sources = [(url, classify_source(url, source_class)) for url, source_class in classified_urls]
            result = SmartCrawler(fetcher).crawl(
                normalized_sources,
                [seed.legal_name, seed.cnpj, seed.trade_name or "", "processo", "garantia"],
            )
            claims = []
            for document in result.documents:
                self.repository.save_document(document)
                claims.extend(self.public_extractor.extract(seed, document))
            existing = self.repository.claims_for(seed.subject_id)
            reconciled = self.reconciler.reconcile(existing + claims)
            for claim in reconciled:
                self.repository.save_claim(claim)
            return {
                "documents": len(result.documents),
                "claims": len(claims),
                "rejected": result.rejected,
            }
        finally:
            fetcher.close()

    def investigate_cnpj(
        self,
        cnpj: str,
        *,
        legal_name: str | None = None,
        state: str | None = None,
        deep: bool = False,
        use_tavily: bool = True,
        seed_urls: list[str] | None = None,
        known_people: list[dict] | None = None,
        extra_documents: list[PublicDocument] | None = None,
    ) -> tuple[Dossier, dict]:
        normalized = "".join(character for character in cnpj if character.isdigit())
        seed = OrganizationSeed(
            legal_name=(legal_name or f"CNPJ {normalized}").strip(),
            cnpj=normalized,
            state=state,
            seed_urls=seed_urls or [],
        )
        # Ancoragem por entidade conhecida em toda a coleta desta investigação.
        self._known_people = known_people or []
        requested_seed = seed
        collection = {
            "providers": [],
            "queries": [],
            "query_specs": [],
            "layer_stats": {},
            "documents": 0,
            "claims_extracted": 0,
            "direct_crawl": None,
            "identity_resolution": {},
            "deep_search_executed": False,
            "rejected": [],
            "documentos_do_banco": 0,
        }
        candidate_urls: list[tuple[str, SourceClass]] = []

        # Documentos que já temos no banco (inteiro teor DJEN, etc.) — fonte oficial,
        # ingerida ANTES do Tavily. Garante decisores/eventos mesmo com Tavily seco.
        if extra_documents:
            collection["claims_extracted"] += self._ingest_documents(seed, extra_documents)
            collection["documents"] += len(extra_documents)
            collection["documentos_do_banco"] = len(extra_documents)

        if use_tavily:
            discovery_client = TavilyDiscovery()
            identity_discovery = discovery_client.discover_identity(seed)
            self._merge_collection(collection, identity_discovery)
            candidate_urls.extend(identity_discovery.candidate_urls)
            collection["claims_extracted"] += self._ingest_documents(
                seed, identity_discovery.documents
            )

            identity = assess_entity_identity(seed, self._eligible_claims(seed))
            collection["identity_resolution"] = identity
            if not identity["blocked"]:
                # Sem divergência corroborada, o nome informado pode orientar hipóteses;
                # a identidade continua marcada como não confirmada e impede abordagem.
                effective_name = identity.get("resolved_name") or seed.legal_name
                effective_seed = seed.model_copy(update={"legal_name": effective_name})
                layered = discovery_client.discover_layers(effective_seed, deep=deep)
                self._merge_collection(collection, layered)
                candidate_urls.extend(layered.candidate_urls)
                collection["claims_extracted"] += self._ingest_documents(
                    effective_seed, layered.documents
                )
                final_identity = assess_entity_identity(
                    requested_seed, self._eligible_claims(requested_seed)
                )
                collection["identity_resolution"] = final_identity
                collection["deep_search_executed"] = True
                seed = requested_seed.model_copy(
                    update={
                        "legal_name": final_identity.get("resolved_name")
                        or requested_seed.legal_name
                    }
                )
            else:
                collection["rejected"].append(
                    {
                        "provider": "pipeline",
                        "reason": "deep_search_blocked_by_entity_conflict",
                    }
                )
        else:
            collection["identity_resolution"] = assess_entity_identity(
                seed, self._eligible_claims(seed)
            )

        candidate_urls.extend(
            (url, classify_source(url, SourceClass.COMPANY_OWNED)) for url in seed.seed_urls
        )
        allowed_candidates = []
        for url, source_class in candidate_urls:
            allowed, reason = self.policy.allows_url(url)
            if allowed and source_class in {
                SourceClass.OFFICIAL_COURT,
                SourceClass.OFFICIAL_REGISTRY,
                SourceClass.OFFICIAL_GOVERNMENT,
                SourceClass.COMPANY_OWNED,
            }:
                allowed_candidates.append((url, source_class))
            elif reason != "host_policy_denied":
                collection["rejected"].append({"url": url, "reason": reason})

        if allowed_candidates and not collection["identity_resolution"].get("blocked"):
            crawl_policy = replace(
                self.policy,
                max_pages_per_run=60 if deep else 25,
                max_depth=3 if deep else 1,
            )
            fetcher = PublicWebFetcher(crawl_policy)
            try:
                direct = SmartCrawler(fetcher).crawl(
                    allowed_candidates[:20 if deep else 12],
                    [
                        seed.legal_name,
                        seed.cnpj,
                        "contato",
                        "email",
                        "telefone",
                        "diretoria",
                        "juridico",
                        "financeiro",
                        "processo",
                        "garantia",
                    ],
                )
                direct_claims = self._ingest_documents(seed, direct.documents)
                collection["documents"] += len(direct.documents)
                collection["claims_extracted"] += direct_claims
                collection["direct_crawl"] = {
                    "documents": len(direct.documents),
                    "claims": direct_claims,
                }
                collection["rejected"].extend(direct.rejected)
            finally:
                fetcher.close()

        dossier = self.build_dossier(seed, investigation_context=collection)
        self._known_people = []
        return dossier, collection

    @staticmethod
    def _merge_collection(collection: dict, discovery) -> None:
        if discovery.provider not in collection["providers"]:
            collection["providers"].append(discovery.provider)
        collection["queries"].extend(discovery.queries)
        collection["query_specs"].extend(discovery.query_specs)
        collection["rejected"].extend(discovery.rejected)
        collection["documents"] += len(discovery.documents)
        for layer, values in discovery.layer_stats.items():
            current = collection["layer_stats"].setdefault(
                layer, {"queries": 0, "results": 0, "documents": 0, "domains": []}
            )
            for key in ("queries", "results", "documents"):
                current[key] += int(values.get(key, 0))
            current["domains"] = sorted(
                set(current["domains"]) | set(values.get("domains", []))
            )

    def _eligible_claims(self, seed: OrganizationSeed):
        return [
            claim
            for claim in self.repository.claims_for(seed.subject_id)
            if not _host_is_excluded(claim.source.url)
            and self._claim_belongs_to_seed(seed, claim)
        ]

    @staticmethod
    def _claim_belongs_to_seed(seed: OrganizationSeed, claim) -> bool:
        sensitive = claim.predicate.startswith("process.") or claim.predicate.startswith(
            "organization.contact."
        ) or claim.predicate.startswith("person.")
        if not sensitive:
            return True
        # Âncora por entidade conhecida: o nome já está vinculado a este CNPJ
        # (QSA/processo, vindo do banco). O documento foi puxado pelo próprio CNPJ,
        # então o vínculo não depende de o nome da empresa cair na mesma janela.
        tags = getattr(claim, "tags", None) or []
        if "known_entity" in tags or "known_entity_contact" in tags:
            return True
        if _context_relevant(seed, claim.excerpt):
            return True
        return (
            claim.source.source_class == SourceClass.COMPANY_OWNED
            and not claim.predicate.startswith("process.")
        )

    def _ingest_documents(self, seed: OrganizationSeed, documents) -> int:
        claims = []
        for document in documents:
            self.repository.save_document(document)
            claims.extend(self.public_extractor.extract(seed, document))
            claims.extend(
                self.public_extractor.anchor_known_people(seed, document, self._known_people)
            )
        existing = self.repository.claims_for(seed.subject_id)
        for claim in self.reconciler.reconcile(existing + claims):
            self.repository.save_claim(claim)
        return len(claims)

    def build_dossier(
        self, seed: OrganizationSeed, investigation_context: dict | None = None
    ) -> Dossier:
        claims = self.reconciler.reconcile(self._eligible_claims(seed))
        for claim in claims:
            self.repository.save_claim(claim)
        dossier = DossierBuilder(self.learner).build(
            seed, claims, investigation_context=investigation_context
        )
        self.repository.save_dossier(seed.subject_id, dossier)
        return dossier

    def build_graph(self, dossier: Dossier) -> GraphOpportunity:
        graph = self.graph_builder.from_dossier(dossier)
        self.repository.save_graph(graph)
        return graph

    def ingest_process_graph(
        self,
        record: ProcessGraphInput,
        *,
        enrich_tavily: bool = False,
        deep: bool = True,
    ) -> tuple[GraphOpportunity, Dossier | None, dict | None]:
        """Fluxo principal: processo primeiro; enriquecimento empresarial depois."""
        process_graph = self.graph_builder.from_process_input(record)
        dossier = None
        collection = None
        graph = process_graph
        if enrich_tavily:
            dossier, collection = self.investigate_cnpj(
                record.company_cnpj,
                legal_name=record.company_legal_name,
                deep=deep,
                use_tavily=True,
            )
            enrichment_graph = self.graph_builder.from_dossier(dossier)
            graph = self.graph_builder.merge(process_graph, enrichment_graph)
        self.repository.save_graph(graph)
        return graph, dossier, collection

    def apply_graph_feedback(
        self, graph_id: str, feedback: GraphFeedbackInput
    ) -> GraphOpportunity:
        current = self.repository.get_graph(graph_id)
        if not current:
            raise ValueError("Grafo não encontrado")
        updated = self.graph_builder.apply_feedback(current, feedback)
        self.repository.save_graph(updated)
        self.repository.record_graph_feedback(graph_id, updated.graph_id, feedback)
        return updated

    def write_dossier(self, dossier: Dossier, output: str | Path) -> None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".json":
            path.write_text(dossier.model_dump_json(indent=2), encoding="utf-8")
        elif path.suffix.lower() == ".pdf":
            render_dossier_pdf(dossier, path)
        else:
            path.write_text(dossier_to_markdown(dossier), encoding="utf-8")
