from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse

from .graph_models import (
    GraphFeedbackInput,
    GraphNode,
    GraphNodeType,
    GraphOpportunity,
    GraphRelationship,
    GraphRelationshipType,
    ProcessGraphInput,
    stable_node_id,
)
from .models import Claim, ClaimStatus, Dossier, SourceClass
from .sources import classify_source


VALID_EVIDENCE = {ClaimStatus.CONFIRMED, ClaimStatus.CORROBORATED}
CRITICAL_EVENTS = {
    "PENHORA",
    "BLOQUEIO JUDICIAL",
    "BLOQUEIO DE ATIVOS",
    "SISBAJUD",
    "RENAJUD",
    "ARRESTO",
    "DEPOSITO JUDICIAL",
    "EXECUCAO FISCAL",
    "INTIMACAO",
}
HYPOTHESES_BY_EVENT = {
    "PENHORA": "SUBSTITUICAO_DE_PENHORA",
    "BLOQUEIO JUDICIAL": "SUBSTITUICAO_DE_CONSTRICAO",
    "BLOQUEIO DE ATIVOS": "SUBSTITUICAO_DE_CONSTRICAO",
    "SISBAJUD": "SUBSTITUICAO_DE_CONSTRICAO",
    "RENAJUD": "SUBSTITUICAO_DE_CONSTRICAO",
    "ARRESTO": "SUBSTITUICAO_DE_CONSTRICAO",
    "DEPOSITO JUDICIAL": "SUBSTITUICAO_DE_DEPOSITO",
    "EXECUCAO FISCAL": "GARANTIA_INICIAL",
    "CARTA FIANCA": "RENOVACAO_OU_ADEQUACAO",
    "SEGURO GARANTIA JUDICIAL": "RENOVACAO_OU_ADEQUACAO",
    "SUBSTITUICAO DE GARANTIA": "REESTRUTURACAO_DE_GARANTIA",
}


class FiscalOpportunityGraphBuilder:
    """Projeta fatos e hipóteses em grafo; nenhuma relação nasce sem evidência."""

    def from_dossier(self, dossier: Dossier) -> GraphOpportunity:
        graph = _GraphAccumulator()
        company_id = stable_node_id(GraphNodeType.COMPANY, dossier.organization["cnpj"])
        graph.add_node(
            GraphNode(
                node_id=company_id,
                node_type=GraphNodeType.COMPANY,
                properties={
                    "cnpj": dossier.organization.get("cnpj"),
                    "razao_social": dossier.organization.get("legal_name"),
                    "nome_fantasia": dossier.organization.get("trade_name"),
                    "uf": dossier.organization.get("state"),
                    "identity_status": (dossier.identity_resolution or {}).get("status"),
                },
            )
        )

        process_nodes: dict[str, str] = {}
        for claim in dossier.claims:
            if claim.predicate != "process.number":
                continue
            number = str(claim.value)
            process_id = stable_node_id(GraphNodeType.PROCESS, number)
            process_nodes[number] = process_id
            graph.add_node(
                GraphNode(
                    node_id=process_id,
                    node_type=GraphNodeType.PROCESS,
                    properties={"numero": number},
                )
            )
        event_nodes: list[str] = []
        for claim in dossier.claims:
            evidence_id, document_id = graph.add_claim_evidence(claim)
            target_id = self._claim_target(
                graph,
                claim,
                company_id=company_id,
                process_nodes=process_nodes,
                event_nodes=event_nodes,
                evidence_id=evidence_id,
                document_id=document_id,
            )
            if target_id:
                graph.link_evidence(claim, evidence_id, target_id)

        opportunity = graph.finalize(
            origin_process_ids=list(process_nodes.values()),
            parent_graph_id=None,
        )
        return opportunity

    def from_process_input(self, record: ProcessGraphInput) -> GraphOpportunity:
        graph = _GraphAccumulator()
        source_status = _structured_source_status(record)
        evidence_score = _structured_evidence_score(record)
        evidence_id, document_id = graph.add_structured_evidence(record)
        company_id = stable_node_id(GraphNodeType.COMPANY, record.company_cnpj)
        process_id = stable_node_id(GraphNodeType.PROCESS, record.process_number)
        graph.add_node(
            GraphNode(
                node_id=company_id,
                node_type=GraphNodeType.COMPANY,
                properties={
                    "cnpj": record.company_cnpj,
                    "razao_social": record.company_legal_name,
                    "origem": record.source_system,
                },
            )
        )
        graph.add_node(
            GraphNode(
                node_id=process_id,
                node_type=GraphNodeType.PROCESS,
                properties={
                    "numero": record.process_number,
                    "tribunal": record.tribunal,
                    "instancia": record.instance,
                    "classe": record.process_class,
                    "assunto": record.subject,
                    "valor": record.amount,
                    "fase": record.phase,
                    "ativo": record.active,
                    "papel_empresa": record.company_role,
                },
            )
        )
        graph.add_relation(
            process_id,
            company_id,
            GraphRelationshipType.AFFECTS,
            evidence_id=evidence_id,
            source_url=record.source_url,
            excerpt=record.source_excerpt,
            observed_at=record.source_date,
            score=evidence_score,
            classification=source_status,
            justification="Processo e empresa vinculados na fonte processual informada.",
        )
        graph.add_relation(
            evidence_id,
            process_id,
            GraphRelationshipType.CONFIRMS,
            evidence_id=evidence_id,
            source_url=record.source_url,
            excerpt=record.source_excerpt,
            observed_at=record.source_date,
            score=evidence_score,
            classification=source_status,
            justification="Evidência estruturada confirma a existência do processo informado.",
        )
        graph.add_relation(
            evidence_id,
            company_id,
            GraphRelationshipType.CONFIRMS,
            evidence_id=evidence_id,
            source_url=record.source_url,
            excerpt=record.source_excerpt,
            observed_at=record.source_date,
            score=evidence_score,
            classification=source_status,
            justification="Evidência estruturada identifica a empresa afetada.",
        )

        for index, event in enumerate(record.events):
            normalized = _normalize_event(event.event_type)
            event_id = stable_node_id(
                GraphNodeType.PROCEDURAL_EVENT,
                f"{record.process_number}|{normalized}|{event.event_date}|{index}",
            )
            graph.add_node(
                GraphNode(
                    node_id=event_id,
                    node_type=GraphNodeType.PROCEDURAL_EVENT,
                    properties={
                        "tipo": normalized,
                        "data": event.event_date.isoformat() if event.event_date else None,
                        "descricao": event.description,
                    },
                )
            )
            graph.add_relation(
                process_id,
                event_id,
                GraphRelationshipType.GENERATED,
                evidence_id=evidence_id,
                source_url=record.source_url,
                excerpt=record.source_excerpt,
                observed_at=event.event_date or record.source_date,
                score=evidence_score,
                classification=source_status,
                justification="Movimentação vinculada ao processo na fonte de origem.",
            )
            graph.add_hypothesis_for_event(
                event_id,
                normalized,
                evidence_id=evidence_id,
                source_url=record.source_url,
                excerpt=record.source_excerpt,
                observed_at=event.event_date or record.source_date,
                evidence_score=evidence_score,
                classification=source_status,
            )
        return graph.finalize(origin_process_ids=[process_id], parent_graph_id=None)

    def merge(self, primary: GraphOpportunity, enrichment: GraphOpportunity) -> GraphOpportunity:
        graph = _GraphAccumulator()
        for node in primary.nodes + enrichment.nodes:
            graph.add_node(node)
        for relationship in primary.relationships + enrichment.relationships:
            graph.add_existing_relation(relationship)
        return graph.finalize(
            origin_process_ids=primary.origin_process_ids or enrichment.origin_process_ids,
            parent_graph_id=primary.graph_id,
        )

    def apply_feedback(
        self, graph_snapshot: GraphOpportunity, feedback: GraphFeedbackInput
    ) -> GraphOpportunity:
        graph = _GraphAccumulator()
        for node in deepcopy(graph_snapshot.nodes):
            if node.node_id == feedback.target_node_id:
                history = list(node.properties.get("commercial_feedback", []))
                history.append(
                    {
                        "type": feedback.feedback_type,
                        "value": feedback.value,
                        "operator": feedback.operator,
                        "occurred_at": feedback.occurred_at.isoformat(),
                    }
                )
                node.properties["commercial_feedback"] = history
            graph.add_node(node)
        if feedback.target_node_id not in graph.nodes:
            raise ValueError("Nó alvo do feedback não existe no grafo")
        for relationship in deepcopy(graph_snapshot.relationships):
            graph.add_existing_relation(relationship)

        evidence_id = stable_node_id(
            GraphNodeType.EVIDENCE,
            f"feedback|{graph_snapshot.graph_id}|{feedback.target_node_id}|{feedback.occurred_at.isoformat()}",
        )
        graph.add_node(
            GraphNode(
                node_id=evidence_id,
                node_type=GraphNodeType.EVIDENCE,
                properties={
                    "tipo": "FEEDBACK_COMERCIAL",
                    "feedback_type": feedback.feedback_type,
                    "value": feedback.value,
                    "operator": feedback.operator,
                    "note": feedback.note,
                    "date": feedback.occurred_at.isoformat(),
                    "public_evidence": False,
                },
            )
        )
        graph.add_relation(
            evidence_id,
            feedback.target_node_id,
            GraphRelationshipType.COMMERCIAL_VALIDATION,
            evidence_id=evidence_id,
            source_url=f"crm://feedback/{graph_snapshot.graph_id}",
            excerpt=feedback.note or feedback.value,
            observed_at=feedback.occurred_at,
            score=100,
            classification=ClaimStatus.CONFIRMED,
            justification="Validação operacional registrada pelo comercial; não substitui prova pública ou judicial.",
        )
        return graph.finalize(
            origin_process_ids=graph_snapshot.origin_process_ids,
            parent_graph_id=graph_snapshot.graph_id,
        )

    def _claim_target(
        self,
        graph: "_GraphAccumulator",
        claim: Claim,
        *,
        company_id: str,
        process_nodes: dict[str, str],
        event_nodes: list[str],
        evidence_id: str,
        document_id: str,
    ) -> str | None:
        predicate = claim.predicate
        if predicate.startswith("organization.") and predicate not in {
            "organization.contact.email",
            "organization.contact.phone",
            "organization.contact.form",
        }:
            return company_id
        if predicate == "process.number":
            number = str(claim.value)
            process_id = process_nodes.setdefault(
                number, stable_node_id(GraphNodeType.PROCESS, number)
            )
            graph.add_node(
                GraphNode(
                    node_id=process_id,
                    node_type=GraphNodeType.PROCESS,
                    properties={"numero": number},
                )
            )
            graph.add_claim_relation(
                claim,
                process_id,
                company_id,
                GraphRelationshipType.AFFECTS,
                evidence_id,
                "O processo aparece em trecho local atribuído à empresa.",
            )
            return process_id
        if predicate == "process.event":
            process_id = _process_id_from_claim(claim, process_nodes)
            normalized = _normalize_event(str(claim.value))
            event_id = stable_node_id(
                GraphNodeType.PROCEDURAL_EVENT,
                f"{process_id or company_id}|{normalized}|{claim.claim_id}",
            )
            graph.add_node(
                GraphNode(
                    node_id=event_id,
                    node_type=GraphNodeType.PROCEDURAL_EVENT,
                    properties={
                        "tipo": normalized,
                        "data": claim.observed_event_at.isoformat() if claim.observed_event_at else None,
                        "status_evidencia": claim.status.value,
                    },
                )
            )
            event_nodes.append(event_id)
            if process_id:
                graph.add_claim_relation(
                    claim,
                    process_id,
                    event_id,
                    GraphRelationshipType.GENERATED,
                    evidence_id,
                    "Evento extraído no contexto do processo indicado.",
                )
            graph.add_hypothesis_for_event(
                event_id,
                normalized,
                evidence_id=evidence_id,
                source_url=claim.source.url,
                excerpt=claim.excerpt,
                observed_at=claim.observed_event_at or claim.source.published_at,
                evidence_score=_claim_evidence_score(claim),
                classification=claim.status,
            )
            return event_id
        if predicate in {"process.role", "process.value"}:
            process_id = _process_id_from_claim(claim, process_nodes)
            if process_id:
                key = "papel_empresa" if predicate == "process.role" else "valor"
                graph.nodes[process_id].properties[key] = claim.value
            return process_id
        if predicate == "process.guarantee_status":
            value = claim.value if isinstance(claim.value, dict) else {"type": claim.value}
            guarantee_type = str(value.get("type") or "GARANTIA_NAO_ESPECIFICADA")
            guarantee_id = stable_node_id(
                GraphNodeType.GUARANTEE, f"{guarantee_type}|{value.get('process')}|{claim.claim_id}"
            )
            graph.add_node(
                GraphNode(
                    node_id=guarantee_id,
                    node_type=GraphNodeType.GUARANTEE,
                    properties={
                        "tipo": guarantee_type,
                        "finding": value.get("finding"),
                        "status_evidencia": claim.status.value,
                    },
                )
            )
            process_number = value.get("process")
            process_id = process_nodes.get(str(process_number)) if process_number else None
            if process_id and claim.status in VALID_EVIDENCE:
                graph.add_claim_relation(
                    claim,
                    process_id,
                    guarantee_id,
                    GraphRelationshipType.HAS_GUARANTEE,
                    evidence_id,
                    "Garantia vinculada ao processo somente porque número e fonte sustentam a relação.",
                )
            return guarantee_id
        if predicate == "person.professional_role" and isinstance(claim.value, dict):
            name = str(claim.value.get("name") or "").strip()
            role = str(claim.value.get("role") or "").strip()
            if not name or not role:
                return None
            person_id = stable_node_id(GraphNodeType.PERSON, name)
            role_id = stable_node_id(GraphNodeType.ROLE, role)
            graph.add_node(
                GraphNode(
                    node_id=person_id,
                    node_type=GraphNodeType.PERSON,
                    properties={
                        "nome": name,
                        "classificacao": _decision_class(role),
                        "poder_decisorio": "NAO_CONFIRMADO",
                    },
                )
            )
            graph.add_node(
                GraphNode(
                    node_id=role_id,
                    node_type=GraphNodeType.ROLE,
                    properties={"nome": role},
                )
            )
            graph.add_claim_relation(
                claim,
                company_id,
                person_id,
                GraphRelationshipType.HAS,
                evidence_id,
                "Pessoa publicada em contexto corporativo; vínculo e poder decisório exigem validação.",
            )
            graph.add_claim_relation(
                claim,
                person_id,
                role_id,
                GraphRelationshipType.HOLDS,
                evidence_id,
                "Cargo extraído do mesmo trecho público atribuído à pessoa.",
            )
            graph.add_claim_relation(
                claim,
                person_id,
                document_id,
                GraphRelationshipType.APPEARED_IN,
                evidence_id,
                "Pessoa identificada no documento de origem.",
            )
            return person_id
        if predicate in {"organization.contact.email", "organization.contact.phone"}:
            raw = claim.value.get("value") if isinstance(claim.value, dict) else claim.value
            node_type = GraphNodeType.EMAIL if predicate.endswith("email") else GraphNodeType.PHONE
            contact_id = stable_node_id(node_type, str(raw))
            graph.add_node(
                GraphNode(
                    node_id=contact_id,
                    node_type=node_type,
                    properties={
                        "endereco" if node_type == GraphNodeType.EMAIL else "numero": raw,
                        "score": _claim_evidence_score(claim),
                        "status_evidencia": claim.status.value,
                    },
                )
            )
            graph.add_claim_relation(
                claim,
                document_id,
                contact_id,
                GraphRelationshipType.CONTAINS,
                evidence_id,
                "Contato aparece literalmente no documento público.",
            )
            if node_type == GraphNodeType.EMAIL and "@" in str(raw):
                domain = str(raw).split("@", 1)[1].casefold()
                domain_id = stable_node_id(GraphNodeType.DOMAIN, domain)
                graph.add_node(
                    GraphNode(
                        node_id=domain_id,
                        node_type=GraphNodeType.DOMAIN,
                        properties={"dominio": domain},
                    )
                )
                graph.add_claim_relation(
                    claim,
                    contact_id,
                    domain_id,
                    GraphRelationshipType.ASSOCIATED_WITH,
                    evidence_id,
                    "O domínio é parte literal do endereço de e-mail publicado.",
                )
                graph.add_claim_relation(
                    claim,
                    domain_id,
                    company_id,
                    GraphRelationshipType.BELONGS_TO,
                    evidence_id,
                    "Domínio associado à empresa no mesmo contexto documental; requer validação de propriedade.",
                )
            return contact_id
        return None


class _GraphAccumulator:
    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.relationships: dict[str, GraphRelationship] = {}

    def add_node(self, node: GraphNode) -> None:
        current = self.nodes.get(node.node_id)
        if not current:
            self.nodes[node.node_id] = node
            return
        merged = dict(current.properties)
        for key, value in node.properties.items():
            if value not in (None, "", [], {}):
                merged[key] = value
        self.nodes[node.node_id] = current.model_copy(update={"properties": merged})

    def add_existing_relation(self, relationship: GraphRelationship) -> None:
        self.relationships.setdefault(relationship.relationship_id, relationship)

    def add_claim_evidence(self, claim: Claim) -> tuple[str, str]:
        document_id = stable_node_id(GraphNodeType.DOCUMENT, claim.source.content_hash)
        evidence_id = stable_node_id(GraphNodeType.EVIDENCE, claim.claim_id)
        self.add_node(
            GraphNode(
                node_id=document_id,
                node_type=GraphNodeType.DOCUMENT,
                properties={
                    "titulo": claim.source.title,
                    "url": claim.source.url,
                    "tipo_fonte": claim.source.source_class.value,
                    "hash": claim.source.content_hash,
                    "data_publicacao": claim.source.published_at.isoformat() if claim.source.published_at else None,
                    "data_captura": claim.source.captured_at.isoformat(),
                },
            )
        )
        self.add_node(
            GraphNode(
                node_id=evidence_id,
                node_type=GraphNodeType.EVIDENCE,
                properties={
                    "claim_id": claim.claim_id,
                    "fonte": claim.source.title,
                    "url": claim.source.url,
                    "data": (claim.observed_event_at or claim.source.published_at or claim.source.captured_at).isoformat(),
                    "score": _claim_evidence_score(claim),
                    "trecho": claim.excerpt,
                    "classificacao": claim.status.value,
                    "justificativa": claim.rationale,
                    "public_evidence": claim.source.source_class != SourceClass.LEGACY_CRM,
                },
            )
        )
        self.add_claim_relation(
            claim,
            evidence_id,
            document_id,
            GraphRelationshipType.APPEARED_IN,
            evidence_id,
            "A evidência foi extraída deste documento, preservando proveniência e trecho.",
        )
        return evidence_id, document_id

    def add_structured_evidence(self, record: ProcessGraphInput) -> tuple[str, str]:
        digest = sha256(f"{record.source_url}|{record.source_excerpt}".encode("utf-8")).hexdigest()
        document_id = stable_node_id(GraphNodeType.DOCUMENT, digest)
        evidence_id = stable_node_id(GraphNodeType.EVIDENCE, f"{digest}|{record.source_system}")
        source_status = _structured_source_status(record)
        evidence_score = _structured_evidence_score(record)
        actual_source_class = classify_source(record.source_url, record.source_class)
        self.add_node(
            GraphNode(
                node_id=document_id,
                node_type=GraphNodeType.DOCUMENT,
                properties={
                    "titulo": record.source_title or record.source_system,
                    "url": record.source_url,
                    "tipo_fonte": actual_source_class.value,
                    "sistema_origem": record.source_system,
                    "data_publicacao": record.source_date.isoformat() if record.source_date else None,
                    "hash": digest,
                },
            )
        )
        self.add_node(
            GraphNode(
                node_id=evidence_id,
                node_type=GraphNodeType.EVIDENCE,
                properties={
                    "fonte": record.source_title or record.source_system,
                    "url": record.source_url,
                    "data": record.source_date.isoformat() if record.source_date else None,
                    "score": evidence_score,
                    "trecho": record.source_excerpt,
                    "classificacao": source_status.value,
                    "justificativa": "Registro estruturado recebido com fonte e trecho auditáveis.",
                    "public_evidence": True,
                },
            )
        )
        self.add_relation(
            evidence_id,
            document_id,
            GraphRelationshipType.APPEARED_IN,
            evidence_id=evidence_id,
            source_url=record.source_url,
            excerpt=record.source_excerpt,
            observed_at=record.source_date,
            score=evidence_score,
            classification=source_status,
            justification="Evidência estruturada vinculada ao documento de origem.",
        )
        return evidence_id, document_id

    def link_evidence(self, claim: Claim, evidence_id: str, target_id: str) -> None:
        relationship_type = (
            GraphRelationshipType.CONFIRMS
            if claim.status in VALID_EVIDENCE
            else GraphRelationshipType.SUPPORTS
        )
        self.add_claim_relation(
            claim,
            evidence_id,
            target_id,
            relationship_type,
            evidence_id,
            "A evidência confirma o nó" if relationship_type == GraphRelationshipType.CONFIRMS else "A evidência sustenta apenas hipótese ou pista sobre o nó",
        )

    def add_claim_relation(
        self,
        claim: Claim,
        from_node: str,
        to_node: str,
        relationship_type: GraphRelationshipType,
        evidence_id: str,
        justification: str,
    ) -> None:
        self.add_relation(
            from_node,
            to_node,
            relationship_type,
            evidence_id=evidence_id,
            source_url=claim.source.url,
            excerpt=claim.excerpt,
            observed_at=claim.observed_event_at or claim.source.published_at,
            score=_claim_evidence_score(claim),
            classification=claim.status,
            justification=justification,
        )

    def add_relation(
        self,
        from_node: str,
        to_node: str,
        relationship_type: GraphRelationshipType,
        *,
        evidence_id: str,
        source_url: str,
        excerpt: str,
        observed_at: datetime | None,
        score: float,
        classification: ClaimStatus,
        justification: str,
    ) -> None:
        if not evidence_id or not source_url or not excerpt.strip() or not justification.strip():
            raise ValueError("Toda relação exige fonte, evidência e justificativa")
        relationship = GraphRelationship(
            from_node=from_node,
            to_node=to_node,
            relationship_type=relationship_type,
            evidence_node=evidence_id,
            source_url=source_url,
            evidence_excerpt=excerpt,
            observed_at=observed_at,
            score=score,
            classification=classification,
            justification=justification,
        )
        self.relationships.setdefault(relationship.relationship_id, relationship)

    def add_hypothesis_for_event(
        self,
        event_id: str,
        event_type: str,
        *,
        evidence_id: str,
        source_url: str,
        excerpt: str,
        observed_at: datetime | None,
        evidence_score: float,
        classification: ClaimStatus,
    ) -> None:
        hypothesis_type = HYPOTHESES_BY_EVENT.get(event_type)
        if not hypothesis_type:
            return
        hypothesis_id = stable_node_id(
            GraphNodeType.SECURITARIAN_HYPOTHESIS, f"{event_id}|{hypothesis_type}"
        )
        self.add_node(
            GraphNode(
                node_id=hypothesis_id,
                node_type=GraphNodeType.SECURITARIAN_HYPOTHESIS,
                properties={
                    "tipo": hypothesis_type,
                    "status": "HIPOTESE_PENDENTE_VALIDACAO",
                    "nao_representa_aderencia_confirmada": True,
                },
            )
        )
        self.add_relation(
            event_id,
            hypothesis_id,
            GraphRelationshipType.SUGGESTS,
            evidence_id=evidence_id,
            source_url=source_url,
            excerpt=excerpt,
            observed_at=observed_at,
            score=min(70, evidence_score),
            classification=ClaimStatus.HYPOTHESIS,
            justification=(
                "O tipo de evento permite formular hipótese securitária, sujeita à obrigação, fase, "
                "valor, beneficiário, objetivo jurídico, clausulado, crédito e aceite judicial."
            ),
        )

    def finalize(
        self, *, origin_process_ids: list[str], parent_graph_id: str | None
    ) -> GraphOpportunity:
        nodes = list(self.nodes.values())
        relationships = list(self.relationships.values())
        score, components, critical_events, gaps = _score_graph(
            nodes, relationships, origin_process_ids
        )
        classification = _classify_score(score)
        next_action = _next_graph_action(
            origin_process_ids, critical_events, gaps, classification
        )
        generated_at = datetime.now(UTC)
        digest = sha256(
            f"{generated_at.isoformat()}|{parent_graph_id}|{len(nodes)}|{len(relationships)}".encode("utf-8")
        ).hexdigest()[:20]
        opportunity = GraphOpportunity(
            graph_id=digest,
            parent_graph_id=parent_graph_id,
            generated_at=generated_at,
            origin_process_ids=list(dict.fromkeys(origin_process_ids)),
            origin_status=(
                "PROCESSO_COMO_ORIGEM"
                if origin_process_ids
                and any(
                    relationship.relationship_type == GraphRelationshipType.AFFECTS
                    and relationship.classification in VALID_EVIDENCE
                    for relationship in relationships
                )
                else "PROCESSO_ORIGEM_NAO_VALIDADA"
                if origin_process_ids
                else "AGUARDANDO_ORIGEM_PROCESSUAL"
            ),
            nodes=sorted(nodes, key=lambda node: (node.node_type.value, node.node_id)),
            relationships=sorted(
                relationships,
                key=lambda relationship: (
                    relationship.relationship_type.value,
                    relationship.relationship_id,
                ),
            ),
            opportunity_score=score,
            opportunity_classification=classification,
            score_components=components,
            critical_events=critical_events,
            validation_gaps=gaps,
            views={},
            next_action=next_action,
        )
        opportunity.views = _build_views(opportunity)
        return opportunity


def _process_id_from_claim(claim: Claim, process_nodes: dict[str, str]) -> str | None:
    for tag in claim.tags:
        if tag in process_nodes:
            return process_nodes[tag]
    return next(iter(process_nodes.values()), None) if len(process_nodes) == 1 else None


def _normalize_event(value: str) -> str:
    folded = value.strip().upper().replace("Ç", "C").replace("Ã", "A").replace("Õ", "O")
    folded = folded.replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U")
    return re.sub(r"\s+", " ", folded)


def _decision_class(role: str) -> str:
    folded = role.casefold()
    if any(term in folded for term in ("cfo", "financeir", "tesour", "controller")):
        return "DECISOR_FINANCEIRO_POTENCIAL"
    if any(term in folded for term in ("jurid", "tribut")) and "advog" not in folded:
        return "DECISOR_JURIDICO_POTENCIAL"
    if any(term in folded for term in ("sócio", "socio", "fundador", "administrador")):
        return "PATROCINADOR_INTERNO_POTENCIAL"
    if any(term in folded for term in ("advog", "tributarista", "procurador")):
        return "INFLUENCIADOR_POTENCIAL"
    return "INTERLOCUTOR_POTENCIAL"


def _claim_evidence_score(claim: Claim) -> float:
    title_url = f"{claim.source.title} {claim.source.url}".casefold()
    if claim.source.source_class == SourceClass.LEGACY_CRM:
        return 0
    if "procuração" in title_url or "procuracao" in title_url or "substabelecimento" in title_url:
        return 95
    if claim.source.source_class == SourceClass.OFFICIAL_REGISTRY:
        return 95
    if claim.source.source_class == SourceClass.COMPANY_OWNED:
        if "relatório anual" in title_url or "relatorio anual" in title_url:
            return 85
        if "ri." in title_url or "/ri/" in title_url:
            return 90
        return 90
    if claim.source.source_class == SourceClass.OFFICIAL_GOVERNMENT:
        return 80
    if claim.source.source_class == SourceClass.OFFICIAL_COURT:
        return 75
    if claim.source.source_class == SourceClass.PROFESSIONAL_PUBLIC:
        return 50
    if claim.source.source_class == SourceClass.NEWS:
        return 50
    return round(claim.confidence * 100, 2)


def _structured_source_status(record: ProcessGraphInput) -> ClaimStatus:
    actual = classify_source(record.source_url, record.source_class)
    if actual in {
        SourceClass.OFFICIAL_COURT,
        SourceClass.OFFICIAL_REGISTRY,
        SourceClass.OFFICIAL_GOVERNMENT,
    }:
        return ClaimStatus.CONFIRMED
    return ClaimStatus.HYPOTHESIS


def _structured_evidence_score(record: ProcessGraphInput) -> float:
    actual = classify_source(record.source_url, record.source_class)
    title_url = f"{record.source_title} {record.source_url}".casefold()
    if "procuração" in title_url or "procuracao" in title_url or "substabelecimento" in title_url:
        return 95
    if actual == SourceClass.OFFICIAL_REGISTRY:
        return 95
    if actual == SourceClass.COMPANY_OWNED:
        return 85 if "relatório anual" in title_url or "relatorio anual" in title_url else 70
    if actual == SourceClass.OFFICIAL_GOVERNMENT:
        return 80
    if actual == SourceClass.OFFICIAL_COURT:
        return 75
    if actual in {SourceClass.NEWS, SourceClass.PROFESSIONAL_PUBLIC}:
        return 50
    return min(50, record.evidence_score)


def _score_graph(
    nodes: list[GraphNode],
    relationships: list[GraphRelationship],
    origin_process_ids: list[str],
) -> tuple[float, dict[str, float], list[dict[str, Any]], list[str]]:
    valid_process_ids = {
        relationship.from_node
        for relationship in relationships
        if relationship.relationship_type == GraphRelationshipType.AFFECTS
        and relationship.classification in VALID_EVIDENCE
    }
    valid_event_ids = {
        relationship.to_node
        for relationship in relationships
        if relationship.relationship_type == GraphRelationshipType.GENERATED
        and relationship.classification in VALID_EVIDENCE
        and relationship.from_node in valid_process_ids
    }
    validated_people = {
        relationship.to_node
        for relationship in relationships
        if relationship.relationship_type == GraphRelationshipType.HAS
        and relationship.classification in VALID_EVIDENCE
    }
    components = {
        "process_origin": 10.0 if set(origin_process_ids) & valid_process_ids else 0.0,
        "critical_event": 0.0,
        "materiality": 0.0,
        "financial_decision_maker": 0.0,
        "legal_decision_maker": 0.0,
        "tax_counsel": 0.0,
        "active_process": 0.0,
        "recency": 0.0,
        "guarantee_absence": 0.0,
    }
    critical_events: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for node in nodes:
        if node.node_type == GraphNodeType.PROCESS:
            if node.node_id not in valid_process_ids:
                continue
            amount = node.properties.get("valor")
            numeric = _numeric_amount(amount)
            if numeric is not None and numeric > 5_000_000:
                components["materiality"] = 20.0
            if node.properties.get("ativo") is True:
                components["active_process"] = 15.0
        elif node.node_type == GraphNodeType.PROCEDURAL_EVENT:
            if node.node_id not in valid_event_ids:
                continue
            event_type = str(node.properties.get("tipo") or "")
            if event_type in CRITICAL_EVENTS:
                components["critical_event"] = 25.0
                critical_events.append(
                    {
                        "node_id": node.node_id,
                        "type": event_type,
                        "date": node.properties.get("data"),
                        "description": node.properties.get("descricao"),
                    }
                )
                event_date = _parse_datetime(node.properties.get("data"))
                if event_date:
                    days = max(0, (now - event_date).days)
                    components["recency"] = max(
                        components["recency"], 15.0 if days <= 60 else 5.0 if days <= 365 else 0.0
                    )
        elif node.node_type == GraphNodeType.PERSON:
            if node.node_id not in validated_people:
                continue
            decision_class = str(node.properties.get("classificacao") or "")
            if decision_class == "DECISOR_FINANCEIRO_POTENCIAL":
                components["financial_decision_maker"] = 10.0
            elif decision_class == "DECISOR_JURIDICO_POTENCIAL":
                components["legal_decision_maker"] = 10.0
            elif decision_class == "INFLUENCIADOR_POTENCIAL":
                components["tax_counsel"] = 15.0

    gaps = []
    if not origin_process_ids:
        gaps.append("process_origin")
    if not valid_event_ids:
        gaps.append("current_procedural_event")
    if not any(
        relationship.relationship_type == GraphRelationshipType.HAS_GUARANTEE
        and relationship.classification in VALID_EVIDENCE
        for relationship in relationships
    ):
        gaps.append("guarantee_status_not_validated")
    if not any(
        node.node_type == GraphNodeType.PERSON
        and node.node_id in validated_people
        and str(node.properties.get("classificacao", "")).startswith("DECISOR_")
        for node in nodes
    ):
        gaps.append("decision_maker_not_validated")
    if not any(
        relationship.relationship_type == GraphRelationshipType.SUGGESTS
        and relationship.from_node in valid_event_ids
        for relationship in relationships
    ):
        gaps.append("securitarian_hypothesis_not_supported")
    if origin_process_ids and not any(
        relationship.relationship_type == GraphRelationshipType.AFFECTS
        and relationship.classification in VALID_EVIDENCE
        for relationship in relationships
    ):
        gaps.append("company_process_link_not_validated")

    # Ausência de garantia nunca pontua apenas porque a garantia não foi localizada.
    score = round(sum(components.values()), 2)
    return score, components, critical_events, sorted(set(gaps))


def _classify_score(score: float) -> str:
    if score <= 40:
        return "MONITORAR"
    if score <= 70:
        return "QUALIFICAR"
    if score <= 100:
        return "PRIORIDADE"
    return "ACAO_IMEDIATA"


def _next_graph_action(
    origin_process_ids: list[str],
    critical_events: list[dict[str, Any]],
    gaps: list[str],
    classification: str,
) -> str:
    if not origin_process_ids:
        return "Ingerir processo e movimentação com fonte auditável antes de tratar a empresa como oportunidade."
    if not critical_events:
        return "Monitorar DataJud, DJEN e tribunal para identificar evento econômico ou exigência de garantia."
    if gaps:
        return "Validar no processo: " + ", ".join(gaps)
    if classification in {"PRIORIDADE", "ACAO_IMEDIATA"}:
        return "Revisão humana conjunta por jurídico e financeiro antes da abordagem consultiva."
    return "Qualificar obrigação, valor, fase, garantia atual e objetivo do tributarista."


def _build_views(graph: GraphOpportunity) -> dict[str, Any]:
    node_map = {node.node_id: node for node in graph.nodes}

    def slice_graph(types: set[GraphNodeType]) -> dict[str, Any]:
        node_ids = {node.node_id for node in graph.nodes if node.node_type in types}
        return {
            "nodes": [node.model_dump(mode="json") for node in graph.nodes if node.node_id in node_ids],
            "relationships": [
                relationship.model_dump(mode="json")
                for relationship in graph.relationships
                if relationship.from_node in node_ids and relationship.to_node in node_ids
            ],
        }

    decision_makers = [
        {
            "node_id": node.node_id,
            **node.properties,
            "priority": (
                1
                if node.properties.get("classificacao") == "DECISOR_FINANCEIRO_POTENCIAL"
                else 2
                if node.properties.get("classificacao") == "DECISOR_JURIDICO_POTENCIAL"
                else 3
            ),
        }
        for node in graph.nodes
        if node.node_type == GraphNodeType.PERSON
    ]
    evidence = [
        node.model_dump(mode="json")
        for node in graph.nodes
        if node.node_type == GraphNodeType.EVIDENCE
    ]
    hypotheses = [
        node.model_dump(mode="json")
        for node in graph.nodes
        if node.node_type == GraphNodeType.SECURITARIAN_HYPOTHESIS
    ]
    timeline = sorted(
        [
            {
                "node_id": node.node_id,
                "type": node.properties.get("tipo"),
                "date": node.properties.get("data"),
                "description": node.properties.get("descricao"),
            }
            for node in graph.nodes
            if node.node_type == GraphNodeType.PROCEDURAL_EVENT
        ],
        key=lambda item: item.get("date") or "",
        reverse=True,
    )
    return {
        "01_grafo_corporativo": slice_graph(
            {GraphNodeType.COMPANY, GraphNodeType.PERSON, GraphNodeType.ROLE, GraphNodeType.OFFICE, GraphNodeType.DOMAIN}
        ),
        "02_grafo_processual": slice_graph(
            {GraphNodeType.PROCESS, GraphNodeType.PROCEDURAL_EVENT, GraphNodeType.COMPANY}
        ),
        "03_grafo_influencia": slice_graph(
            {GraphNodeType.COMPANY, GraphNodeType.PERSON, GraphNodeType.ROLE, GraphNodeType.OFFICE}
        ),
        "04_grafo_contatos": slice_graph(
            {GraphNodeType.PERSON, GraphNodeType.DOCUMENT, GraphNodeType.EMAIL, GraphNodeType.PHONE, GraphNodeType.DOMAIN}
        ),
        "05_grafo_garantias": slice_graph(
            {GraphNodeType.PROCESS, GraphNodeType.PROCEDURAL_EVENT, GraphNodeType.GUARANTEE, GraphNodeType.SECURITARIAN_HYPOTHESIS}
        ),
        "06_linha_tempo": timeline,
        "07_eventos_criticos": graph.critical_events,
        "08_decisores_prioritarios": sorted(decision_makers, key=lambda item: item["priority"]),
        "09_evidencias_publicas": [item for item in evidence if item["properties"].get("public_evidence")],
        "10_hipotese_securitaria": hypotheses,
        "11_lacunas_validacao": graph.validation_gaps,
        "12_proxima_acao_comercial": {
            "classification": graph.opportunity_classification,
            "score": graph.opportunity_score,
            "action": graph.next_action,
        },
    }


def _numeric_amount(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    raw = re.sub(r"[^0-9,.]", "", str(value))
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
