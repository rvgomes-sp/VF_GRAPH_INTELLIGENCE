from datetime import UTC, datetime
from pathlib import Path

from vf_osint.graph_engine import FiscalOpportunityGraphBuilder
from vf_osint.graph_models import (
    GraphFeedbackInput,
    GraphNodeType,
    GraphRelationshipType,
    ProcessEventInput,
    ProcessGraphInput,
)
from vf_osint.neo4j_export import graph_to_neo4j_batch
from vf_osint.crm_projection import graph_to_crm_projection
from vf_osint.storage import Repository


def process_input() -> ProcessGraphInput:
    return ProcessGraphInput(
        process_number="5001234-22.2026.4.03.6100",
        tribunal="TRF3",
        instance="1",
        process_class="Execução Fiscal",
        subject="ICMS",
        amount=12_000_000,
        phase="Garantia do juízo",
        active=True,
        company_cnpj="12.345.678/0001-90",
        company_legal_name="EMPRESA TESTE LTDA",
        company_role="EXECUTADA",
        events=[
            ProcessEventInput(
                event_type="Penhora",
                event_date=datetime(2026, 8, 19, tzinfo=UTC),
                description="Penhora registrada nos autos.",
            )
        ],
        source_system="DATAJUD",
        source_url="https://api-publica.datajud.cnj.jus.br/processo/5001234",
        source_title="DataJud - movimentação processual",
        source_excerpt=(
            "Processo 5001234-22.2026.4.03.6100 afeta EMPRESA TESTE LTDA, "
            "CNPJ 12.345.678/0001-90. Penhora registrada em 19/08/2026."
        ),
        source_date=datetime(2026, 8, 19, tzinfo=UTC),
        evidence_score=75,
    )


def test_process_is_graph_origin_and_event_suggests_only_hypothesis():
    graph = FiscalOpportunityGraphBuilder().from_process_input(process_input())
    assert graph.origin_status == "PROCESSO_COMO_ORIGEM"
    assert graph.origin_process_ids
    assert graph.opportunity_classification == "PRIORIDADE"
    assert any(node.node_type == GraphNodeType.SECURITARIAN_HYPOTHESIS for node in graph.nodes)
    suggestion = next(
        relationship
        for relationship in graph.relationships
        if relationship.relationship_type == GraphRelationshipType.SUGGESTS
    )
    assert suggestion.classification.value == "hypothesis"
    assert "sujeita" in suggestion.justification


def test_every_graph_relationship_has_auditable_evidence():
    graph = FiscalOpportunityGraphBuilder().from_process_input(process_input())
    evidence_ids = {
        node.node_id for node in graph.nodes if node.node_type == GraphNodeType.EVIDENCE
    }
    assert graph.relationships
    assert all(relationship.evidence_node in evidence_ids for relationship in graph.relationships)
    assert all(relationship.source_url for relationship in graph.relationships)
    assert all(relationship.evidence_excerpt for relationship in graph.relationships)
    assert all(relationship.justification for relationship in graph.relationships)
    assert all(relationship.score >= 0 for relationship in graph.relationships)


def test_graph_exposes_twelve_decision_views():
    graph = FiscalOpportunityGraphBuilder().from_process_input(process_input())
    assert list(graph.views) == [
        "01_grafo_corporativo",
        "02_grafo_processual",
        "03_grafo_influencia",
        "04_grafo_contatos",
        "05_grafo_garantias",
        "06_linha_tempo",
        "07_eventos_criticos",
        "08_decisores_prioritarios",
        "09_evidencias_publicas",
        "10_hipotese_securitaria",
        "11_lacunas_validacao",
        "12_proxima_acao_comercial",
    ]


def test_feedback_creates_new_temporal_snapshot(tmp_path: Path):
    builder = FiscalOpportunityGraphBuilder()
    original = builder.from_process_input(process_input())
    company = next(node for node in original.nodes if node.node_type == GraphNodeType.COMPANY)
    feedback = GraphFeedbackInput(
        feedback_type="DECISOR_CONFIRMADO",
        target_node_id=company.node_id,
        value="Validação em reunião",
        operator="ana@vf.local",
        note="Confirmação operacional; documento público ainda pendente.",
    )
    updated = builder.apply_feedback(original, feedback)
    assert updated.graph_id != original.graph_id
    assert updated.parent_graph_id == original.graph_id
    assert any(
        relationship.relationship_type == GraphRelationshipType.COMMERCIAL_VALIDATION
        for relationship in updated.relationships
    )
    repository = Repository(tmp_path / "graph.db")
    repository.save_graph(original)
    repository.save_graph(updated)
    repository.record_graph_feedback(original.graph_id, updated.graph_id, feedback)
    assert repository.get_graph(updated.graph_id).parent_graph_id == original.graph_id


def test_neo4j_export_is_parameterized():
    graph = FiscalOpportunityGraphBuilder().from_process_input(process_input())
    export = graph_to_neo4j_batch(graph)
    assert export["counts"]["nodes"] == len(graph.nodes)
    node_statement = next(
        item for item in export["statements"] if "MERGE (n:" in item["query"]
    )
    assert "$properties" in node_statement["query"]
    assert "EMPRESA TESTE LTDA" not in node_statement["query"]


def test_private_url_cannot_be_promoted_to_official_process_evidence():
    record = process_input().model_copy(
        update={"source_url": "https://example.com/processo/5001234"}
    )
    graph = FiscalOpportunityGraphBuilder().from_process_input(record)
    assert graph.origin_status == "PROCESSO_ORIGEM_NAO_VALIDADA"
    assert graph.score_components["process_origin"] == 0
    affects = next(
        relationship
        for relationship in graph.relationships
        if relationship.relationship_type == GraphRelationshipType.AFFECTS
    )
    assert affects.classification.value == "hypothesis"


def test_crm_is_projection_and_only_receives_valid_process_opportunity():
    graph = FiscalOpportunityGraphBuilder().from_process_input(process_input())
    projection = graph_to_crm_projection(graph)
    assert projection["source_of_truth"] == "GRAPH"
    assert len(projection["crm_empresas"]) == 1
    assert len(projection["crm_oportunidades"]) == 1
    assert projection["crm_interacoes"] == []

    unvalidated = FiscalOpportunityGraphBuilder().from_process_input(
        process_input().model_copy(
            update={"source_url": "https://example.com/processo/5001234"}
        )
    )
    assert graph_to_crm_projection(unvalidated)["crm_oportunidades"] == []
