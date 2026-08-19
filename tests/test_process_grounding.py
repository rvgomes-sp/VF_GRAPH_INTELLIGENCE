from vf_osint.pipeline import OSINTPipeline
from vf_osint.crm_projection import graph_to_crm_projection
from vf_osint.models import OrganizationSeed, PublicDocument, SourceClass


def test_extra_documents_ground_decisor_without_tavily(tmp_path):
    # Inteiro teor do DJEN (já no banco) confirma o advogado como decisor,
    # SEM Tavily. Contato fica como lacuna (não está no ato publicado).
    pipe = OSINTPipeline(str(tmp_path / "t.db"))
    djen = PublicDocument(
        url="djen://evento/00091044420118220001",
        title="DJEN penhora",
        text=("Tribunal de Justica. Intime-se o advogado FABIO HENRIQUE FURTADO "
              "COELHO DE OLIVEIRA OAB/RO 5105A. FORTBRAS AUTOPECAS S.A. "
              "CNPJ 22.761.584/0001-50. Evento: penhora."),
        source_class=SourceClass.OFFICIAL_COURT,
    )
    dossier, collection = pipe.investigate_cnpj(
        "22761584000150",
        legal_name="FORTBRAS AUTOPECAS S.A.",
        state="SP",
        deep=False,
        use_tavily=False,
        known_people=[{"nome": "FABIO HENRIQUE FURTADO COELHO DE OLIVEIRA", "cargo": "Advogado"}],
        extra_documents=[djen],
    )
    assert collection["documentos_do_banco"] == 1
    projection = graph_to_crm_projection(pipe.build_graph(dossier))
    nomes = [d.get("nome") for d in projection.get("crm_decisores", [])]
    assert any("Fabio Henrique" in (n or "") for n in nomes)


def test_no_extra_documents_is_safe(tmp_path):
    pipe = OSINTPipeline(str(tmp_path / "t.db"))
    dossier, collection = pipe.investigate_cnpj(
        "22761584000150", legal_name="FORTBRAS AUTOPECAS S.A.",
        deep=False, use_tavily=False, known_people=[], extra_documents=None,
    )
    assert collection["documentos_do_banco"] == 0
