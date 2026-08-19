from vf_osint.discovery import DiscoveryResult
from vf_osint.models import EvidenceLayer, OrganizationSeed, PublicDocument, SourceClass
from vf_osint.pipeline import OSINTPipeline


class ConflictDiscovery:
    def discover_identity(self, seed):
        documents = [
            PublicDocument(
                url="https://fonte-a.example/cadastro",
                title="Cadastro empresarial",
                text="LPS COMPANY LTDA 08.890.838/0001-00 situação ativa",
                source_class=SourceClass.NEWS,
                evidence_layer=EvidenceLayer.IDENTITY,
                search_objective="exact_cnpj",
            ),
            PublicDocument(
                url="https://fonte-b.example/perfil",
                title="Perfil empresarial",
                text="LPS COMPANY LTDA - CNPJ 08.890.838/0001-00",
                source_class=SourceClass.COMPANY_OWNED,
                evidence_layer=EvidenceLayer.IDENTITY,
                search_objective="cnpj_name_coherence",
            ),
        ]
        return DiscoveryResult(
            documents=documents,
            queries=['"08.890.838/0001-00" razão social'],
            query_specs=[
                {"layer": "identity", "objective": "exact_cnpj", "query": "cnpj"}
            ],
            layer_stats={
                "identity": {
                    "queries": 1,
                    "results": 2,
                    "documents": 2,
                    "domains": ["fonte-a.example", "fonte-b.example"],
                }
            },
        )

    def discover_layers(self, seed, *, deep=False):
        raise AssertionError("As camadas profundas não podem rodar após conflito de identidade")


def test_cnpj_name_conflict_blocks_deep_search_and_relabels_headline(tmp_path, monkeypatch):
    monkeypatch.setattr("vf_osint.pipeline.TavilyDiscovery", ConflictDiscovery)
    pipeline = OSINTPipeline(tmp_path / "gate.db")
    dossier, collection = pipeline.investigate_cnpj(
        "08.890.838/0001-00",
        legal_name="LORENZETTI S/A",
        deep=True,
        use_tavily=True,
    )
    assert collection["identity_resolution"]["status"] == "ENTITY_CONFLICT"
    assert collection["deep_search_executed"] is False
    assert dossier.organization["legal_name"] == "LPS COMPANY LTDA"
    assert dossier.organization["requested_legal_name"] == "LORENZETTI S/A"
    assert dossier.decision["classification"] == "NAO_PROSSEGUIR"
    assert dossier.decision["sendable"] is False
    assert "entity_conflict" in dossier.gaps
