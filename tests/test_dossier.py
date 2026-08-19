from pathlib import Path

from vf_osint.dossier import DossierBuilder
from vf_osint.evidence import LegacyCRMExtractor
from vf_osint.learning import GuardedLearner
from vf_osint.models import OrganizationSeed
from vf_osint.storage import Repository


def test_legacy_only_dossier_is_not_sendable(tmp_path: Path):
    repository = Repository(tmp_path / "test.db")
    seed = OrganizationSeed(
        legal_name="EMPRESA TESTE LTDA", cnpj="12.345.678/0001-90", state="SP"
    )
    claims = LegacyCRMExtractor().extract(
        seed,
        "EMPRESA TESTE LTDA 12.345.678/0001-90 01/08/2026 SISBAJUD 1234567-89.2026.8.26.0001 polo passivo",
        "file:///legacy.txt",
    )
    dossier = DossierBuilder(GuardedLearner(repository)).build(seed, claims)
    assert dossier.decision["classification"] == "MONITORAR"
    assert dossier.decision["sendable"] is False
    assert dossier.approach["status"] == "RASCUNHO_INTERNO_CONDICIONADO"
    assert dossier.executive_reading["evidence_score"] != 93.26
    assert list(dossier.intelligence_blocks) == [
        "01_empresa",
        "02_grupo_economico",
        "03_decisores",
        "04_contatos_publicos",
        "05_estrutura_financeira",
        "06_sinais_tributarios",
        "07_sinais_judiciais",
        "08_garantias_identificadas",
        "09_eventos_recentes",
        "10_hipoteses_securitarias",
        "11_lacunas",
        "12_proximo_movimento_comercial",
    ]
    assert dossier.intelligence_blocks["08_garantias_identificadas"]["status"] == "LACUNA"


def test_learning_changes_variant_not_guardrails(tmp_path: Path):
    repository = Repository(tmp_path / "test.db")
    learner = GuardedLearner(repository)
    repository.record_feedback("d1", "approach", "socio:financeiro_primeiro", "useful")
    choice = learner.select_approach_variant("socio")
    assert choice.selected_variant == "financeiro_primeiro"
