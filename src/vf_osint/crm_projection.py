from __future__ import annotations

from typing import Any

from .graph_models import (
    GraphNodeType,
    GraphOpportunity,
    GraphRelationshipType,
)


def graph_to_crm_projection(graph: GraphOpportunity) -> dict[str, Any]:
    """Projeta o grafo para estruturas operacionais; o CRM não vira fonte relacional."""
    nodes = {node.node_id: node for node in graph.nodes}
    company_ids = {
        node.node_id for node in graph.nodes if node.node_type == GraphNodeType.COMPANY
    }
    process_company = {
        relationship.from_node: relationship.to_node
        for relationship in graph.relationships
        if relationship.relationship_type == GraphRelationshipType.AFFECTS
        and relationship.to_node in company_ids
    }
    companies = [
        {
            "cnpj": node.properties.get("cnpj"),
            "razao_social": node.properties.get("razao_social"),
            "segmento": node.properties.get("setor"),
            "status": graph.opportunity_classification,
            "score_atual": graph.opportunity_score,
            "graph_node_id": node.node_id,
            "graph_snapshot_id": graph.graph_id,
        }
        for node in graph.nodes
        if node.node_type == GraphNodeType.COMPANY
    ]
    hypothesis_types = [
        node.properties.get("tipo")
        for node in graph.nodes
        if node.node_type == GraphNodeType.SECURITARIAN_HYPOTHESIS
    ]
    opportunities = []
    if graph.origin_status == "PROCESSO_COMO_ORIGEM":
        for process_id in graph.origin_process_ids:
            process = nodes.get(process_id)
            if not process:
                continue
            opportunities.append(
                {
                    "empresa_graph_node_id": process_company.get(process_id),
                    "processo": process.properties.get("numero"),
                    "tipo_garantia": hypothesis_types[0] if hypothesis_types else None,
                    "score": graph.opportunity_score,
                    "status": graph.opportunity_classification,
                    "graph_snapshot_id": graph.graph_id,
                }
            )
    decision_makers = [
        {
            "empresa_graph_node_id": relationship.from_node,
            "pessoa_graph_node_id": relationship.to_node,
            "nome": nodes[relationship.to_node].properties.get("nome"),
            "classificacao": nodes[relationship.to_node].properties.get("classificacao"),
            "poder_decisorio": nodes[relationship.to_node].properties.get("poder_decisorio"),
            "prioridade": _decision_priority(
                str(nodes[relationship.to_node].properties.get("classificacao") or "")
            ),
            "evidence_relationship_id": relationship.relationship_id,
        }
        for relationship in graph.relationships
        if relationship.relationship_type == GraphRelationshipType.HAS
        and relationship.to_node in nodes
        and nodes[relationship.to_node].node_type == GraphNodeType.PERSON
    ]
    evidence = [
        {
            "entity_type": "RELATIONSHIP",
            "entity_id": relationship.relationship_id,
            "source": relationship.source_url,
            "confidence": relationship.score,
            "status": relationship.classification.value,
            "last_seen": relationship.observed_at.isoformat() if relationship.observed_at else None,
            "excerpt": relationship.evidence_excerpt,
            "justification": relationship.justification,
            "graph_snapshot_id": graph.graph_id,
        }
        for relationship in graph.relationships
    ]
    return {
        "source_of_truth": "GRAPH",
        "projection_target": "CRM_OPERATIONAL",
        "graph_snapshot_id": graph.graph_id,
        "crm_empresas": companies,
        "crm_oportunidades": opportunities,
        "crm_decisores": decision_makers,
        "entity_evidence": evidence,
        "crm_interacoes": [],
        "write_policy": (
            "Projeção pronta para sincronização. Nenhuma escrita em Supabase foi executada; "
            "interações e feedback devem retornar ao grafo como novos eventos."
        ),
    }


def _decision_priority(classification: str) -> int:
    return {
        "DECISOR_FINANCEIRO_POTENCIAL": 1,
        "DECISOR_JURIDICO_POTENCIAL": 2,
        "PATROCINADOR_INTERNO_POTENCIAL": 3,
        "INFLUENCIADOR_POTENCIAL": 4,
    }.get(classification, 5)
