from datetime import UTC, datetime

from vf_osint.evidence import EvidenceReconciler, LegacyCRMExtractor, PublicEvidenceExtractor
from vf_osint.models import ClaimStatus, OrganizationSeed, PublicDocument, SourceClass


def seed() -> OrganizationSeed:
    return OrganizationSeed(
        legal_name="EMPRESA TESTE LTDA", cnpj="12.345.678/0001-90", state="SP"
    )


def test_cnpj_is_normalized():
    assert seed().cnpj == "12345678000190"


def test_public_documents_receive_distinct_content_hashes():
    first = PublicDocument(
        url="https://one.example/company",
        title="one",
        text="EMPRESA TESTE LTDA",
        source_class=SourceClass.AGGREGATOR,
    )
    second = PublicDocument(
        url="https://two.example/company",
        title="two",
        text="EMPRESA TESTE LTDA",
        source_class=SourceClass.AGGREGATOR,
    )
    assert first.content_hash
    assert second.content_hash
    assert first.content_hash != second.content_hash


def test_legacy_claims_remain_hypotheses():
    claims = LegacyCRMExtractor().extract(
        seed(),
        "EMPRESA TESTE LTDA 12.345.678/0001-90\n01/08/2026\nSISBAJUD\n1234567-89.2026.8.26.0001\npolo passivo",
        "file:///seed.txt",
    )
    assert claims
    assert {claim.status for claim in claims} == {ClaimStatus.HYPOTHESIS}
    assert len({claim.claim_id for claim in claims}) == len(claims)


def test_official_document_confirms_explicit_claim():
    document = PublicDocument(
        url="https://esaj.tjsp.jus.br/publico",
        title="Consulta oficial",
        text=(
            "EMPRESA TESTE LTDA CNPJ 12.345.678/0001-90, polo passivo, "
            "processo 1234567-89.2026.8.26.0001. Evento: penhora."
        ),
        source_class=SourceClass.OFFICIAL_COURT,
    )
    claims = PublicEvidenceExtractor().extract(seed(), document)
    assert any(
        claim.predicate == "process.number" and claim.status == ClaimStatus.CONFIRMED
        for claim in claims
    )


def test_official_guarantee_linked_to_process_is_confirmed():
    document = PublicDocument(
        url="https://esaj.tjsp.jus.br/publico",
        title="Decisão oficial",
        text=(
            "EMPRESA TESTE LTDA CNPJ 12.345.678/0001-90, processo "
            "1234567-89.2026.8.26.0001: aceita a substituição de garantia por "
            "seguro garantia judicial em 01/08/2026."
        ),
        source_class=SourceClass.OFFICIAL_COURT,
    )
    claims = EvidenceReconciler().reconcile(PublicEvidenceExtractor().extract(seed(), document))
    guarantee = next(claim for claim in claims if claim.predicate == "process.guarantee_status")
    assert guarantee.status == ClaimStatus.CONFIRMED
    assert guarantee.value["process"] == "1234567-89.2026.8.26.0001"


def test_guarantee_mention_without_process_remains_hypothesis():
    document = PublicDocument(
        url="https://www.gov.br/exemplo/noticia",
        title="Menção oficial",
        text="EMPRESA TESTE LTDA CNPJ 12.345.678/0001-90 menciona seguro garantia judicial.",
        source_class=SourceClass.OFFICIAL_GOVERNMENT,
    )
    claims = EvidenceReconciler().reconcile(PublicEvidenceExtractor().extract(seed(), document))
    guarantee = next(claim for claim in claims if claim.predicate == "process.guarantee_status")
    assert guarantee.status == ClaimStatus.HYPOTHESIS
    assert guarantee.value["process"] is None


def test_extracts_corporate_tax_and_financial_signals_with_evidence():
    document = PublicDocument(
        url="https://empresa.example/relatorio.pdf",
        title="Relatório anual",
        text=(
            "EMPRESA TESTE LTDA CNPJ 12.345.678/0001-90 integra grupo econômico e possui "
            "subsidiária. As notas explicativas registram contingências tributárias de ICMS."
        ),
        source_class=SourceClass.COMPANY_OWNED,
    )
    claims = PublicEvidenceExtractor().extract(seed(), document)
    predicates = {claim.predicate for claim in claims}
    assert "organization.corporate_structure_signal" in predicates
    assert "financial.signal" in predicates
    assert "tax.signal" in predicates
    assert all(claim.excerpt and claim.source.url for claim in claims)


def test_two_independent_aggregators_corroborate():
    extractor = PublicEvidenceExtractor()
    documents = [
        PublicDocument(
            url="https://one.example/company",
            title="one",
            text="EMPRESA TESTE LTDA 12.345.678/0001-90",
            source_class=SourceClass.AGGREGATOR,
        ),
        PublicDocument(
            url="https://two.example/company",
            title="two",
            text="EMPRESA TESTE LTDA 12.345.678/0001-90",
            source_class=SourceClass.AGGREGATOR,
        ),
    ]
    claims = [claim for doc in documents for claim in extractor.extract(seed(), doc)]
    reconciled = EvidenceReconciler().reconcile(claims)
    assert any(claim.status == ClaimStatus.CORROBORATED for claim in reconciled)


def test_legacy_plus_one_aggregator_does_not_corroborate():
    legacy = LegacyCRMExtractor().extract(
        seed(), "EMPRESA TESTE LTDA 12.345.678/0001-90", "file:///legacy.txt"
    )
    document = PublicDocument(
        url="https://one.example/company",
        title="one",
        text="EMPRESA TESTE LTDA 12.345.678/0001-90",
        source_class=SourceClass.AGGREGATOR,
    )
    external = PublicEvidenceExtractor().extract(seed(), document)
    reconciled = EvidenceReconciler().reconcile(legacy + external)
    assert all(claim.status == ClaimStatus.HYPOTHESIS for claim in reconciled)


def test_event_date_is_distinct_from_capture_date():
    document = PublicDocument(
        url="https://esaj.tjsp.jus.br/publico",
        title="oficial",
        text="01/08/2026 penhora processo 1234567-89.2026.8.26.0001 EMPRESA TESTE LTDA",
        source_class=SourceClass.OFFICIAL_COURT,
        captured_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    claims = PublicEvidenceExtractor().extract(seed(), document)
    event = next(claim for claim in claims if claim.predicate == "process.event")
    assert event.observed_event_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert event.source.captured_at == datetime(2026, 8, 18, tzinfo=UTC)


def test_legacy_block_does_not_leak_event_from_neighbor_process():
    text = """01/08/2026
SISBAJUD
polo passivo
1234567-89.2026.8.26.0001
02/08/2026
Penhora
polo passivo
7654321-98.2026.8.26.0001"""
    claims = LegacyCRMExtractor().extract(seed(), text, "file:///legacy.txt")
    events = [(claim.value, claim.tags[0]) for claim in claims if claim.predicate == "process.event"]
    assert ("SISBAJUD", "1234567-89.2026.8.26.0001") in events
    assert ("PENHORA", "7654321-98.2026.8.26.0001") in events


def test_public_company_page_extracts_business_contacts_and_potential_decision_maker():
    document = PublicDocument(
        url="https://empresa.example/contato",
        title="Contato",
        text=(
            "EMPRESA TESTE LTDA CNPJ 12.345.678/0001-90. "
            "Maria Silva - Diretora Financeira. contato@empresa.example "
            "Telefone (11) 3333-4444."
        ),
        links=["https://www.linkedin.com/in/maria-silva"],
        source_class=SourceClass.COMPANY_OWNED,
    )
    claims = PublicEvidenceExtractor().extract(seed(), document)
    predicates = {claim.predicate for claim in claims}
    assert "organization.contact.email" in predicates
    assert "organization.contact.phone" in predicates
    assert "person.linkedin" in predicates
    person = next(claim for claim in claims if claim.predicate == "person.professional_role")
    assert person.value["name"] == "Maria Silva"
    assert person.value["decision_maker"] == "POTENCIAL"


def test_legal_name_can_be_resolved_from_cnpj_context():
    unresolved = OrganizationSeed(legal_name="CNPJ 12345678000190", cnpj="12345678000190")
    document = PublicDocument(
        url="https://public.example/company",
        title="Cadastro",
        text="EMPRESA TESTE LTDA CNPJ 12.345.678/0001-90",
        source_class=SourceClass.AGGREGATOR,
    )
    claims = PublicEvidenceExtractor().extract(unresolved, document)
    assert any(
        claim.predicate == "organization.legal_name" and claim.value == "EMPRESA TESTE LTDA"
        for claim in claims
    )


def test_long_official_document_does_not_join_surnames_or_contacts_from_third_parties():
    seed = OrganizationSeed(
        legal_name="VAZQUEZ & FONSECA CORRETORA DE SEGUROS LTDA",
        cnpj="22703289000148",
    )
    document = PublicDocument(
        url="https://api.tjsp.jus.br/pauta.pdf",
        title="Pauta judicial",
        text=(
            "Processo 1027638-44.2024.8.26.0068. Advogada Karen Mey Vasques. "
            "Apelado Pedro Fonseca. Outra corretora de seguros. Telefone 11 99999-0000."
        ),
        source_class=SourceClass.OFFICIAL_COURT,
    )
    claims = PublicEvidenceExtractor().extract(seed, document)
    assert not any(claim.predicate.startswith("process.") for claim in claims)
    assert not any(claim.predicate == "organization.contact.phone" for claim in claims)


def test_unrelated_long_document_does_not_create_business_signals():
    document = PublicDocument(
        url="https://trf.example/documento",
        title="Documento de terceiros",
        text=(
            "OUTRA EMPRESA S.A. possui grupo econômico, contingências tributárias, ICMS e "
            "seguro garantia judicial no processo 1234567-89.2026.4.03.0001."
        ),
        source_class=SourceClass.OFFICIAL_COURT,
    )
    claims = PublicEvidenceExtractor().extract(seed(), document)
    assert not any(
        claim.predicate in {
            "organization.corporate_structure_signal",
            "financial.signal",
            "tax.signal",
            "process.guarantee_status",
        }
        for claim in claims
    )


def test_ocr_truncated_company_name_still_allows_local_public_phone():
    seed = OrganizationSeed(
        legal_name="VAZQUEZ & FONSECA CORRETORA DE SEGUROS LTDA",
        cnpj="22703289000148",
    )
    document = PublicDocument(
        url="https://diariooficial.prefeitura.sp.gov.br/apolice",
        title="Apólice publicada",
        text="VAZQUEZ & FONSECA CORRETORA DE SEGUROS L SUSEP OFICIAL TELEFONE 11 2389-3926",
        source_class=SourceClass.OFFICIAL_GOVERNMENT,
    )
    claims = PublicEvidenceExtractor().extract(seed, document)
    assert any(claim.predicate == "organization.contact.phone" for claim in claims)


def test_known_entity_anchor_creates_professional_role_claim():
    # A pessoa já é conhecida do banco (QSA/processo). Quando o nome aparece
    # num documento público, a âncora emite person.professional_role (que vira
    # nó PESSOA -> decisor na projeção), sem presumir poder decisório. O nome
    # é casado ignorando acento, mas armazenado com o acento real.
    document = PublicDocument(
        url="https://fortbras.com.br/institucional",
        title="Institucional",
        text=(
            "A area financeira e conduzida pela diretoria. "
            "Joao da Silva Junior atua junto ao grupo desde 2019."
        ),
        source_class=SourceClass.COMPANY_OWNED,
    )
    known = [
        {"nome": "João da Silva Júnior", "cargo": "Sócio/Administrador"},
        {"nome": "Maria Inexistente Souza", "cargo": "Advogado"},
    ]
    claims = PublicEvidenceExtractor.anchor_known_people(seed(), document, known)
    roles = [c for c in claims if c.predicate == "person.professional_role"]
    assert len(roles) == 1  # só o nome presente no texto ancora
    assert roles[0].value["name"] == "João Da Silva Júnior"
    assert roles[0].status == ClaimStatus.HYPOTHESIS  # confirmable=False -> nunca fato


def test_known_entity_anchor_noop_without_people():
    document = PublicDocument(
        url="https://x.example", title="x", text="qualquer texto",
        source_class=SourceClass.COMPANY_OWNED,
    )
    assert PublicEvidenceExtractor.anchor_known_people(seed(), document, None) == []
    assert PublicEvidenceExtractor.anchor_known_people(seed(), document, []) == []


def test_known_entity_anchor_captures_contact_near_name():
    # E-mail/telefone colados ao nome conhecido (assinatura/procuração) devem
    # virar claims person.contact.* vinculados à pessoa.
    document = PublicDocument(
        url="https://esaj.tjsp.jus.br/procuracao.pdf",
        title="Procuração",
        text=("Monique Barros de Lima - OAB/RJ 175520 - "
              "e-mail: monique.lima@escritorio.adv.br - Tel: (21) 99876-5432."),
        source_class=SourceClass.OFFICIAL_COURT,
    )
    known = [{"nome": "Monique Barros de Lima", "cargo": "Advogado"}]
    claims = PublicEvidenceExtractor.anchor_known_people(seed(), document, known)
    emails = [c for c in claims if c.predicate == "person.contact.email"]
    phones = [c for c in claims if c.predicate == "person.contact.phone"]
    assert emails and emails[0].value["value"] == "monique.lima@escritorio.adv.br"
    assert emails[0].value["person"] == "Monique Barros De Lima"
    assert phones and "21" in phones[0].value["value"]
