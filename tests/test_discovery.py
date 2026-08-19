from vf_osint.discovery import (
    GLOBAL_EXCLUDED_DOMAINS,
    TavilyDiscovery,
    _layered_query_plan,
)
from vf_osint.models import EvidenceLayer, OrganizationSeed, SourceClass


class FakeTavily:
    def search(self, **kwargs):
        return {
            "results": [
                {
                    "title": "EMPRESA TESTE LTDA — Diretoria",
                    "url": "https://www.linkedin.com/in/maria-silva",
                    "content": "Maria Silva - Diretora Financeira - EMPRESA TESTE LTDA",
                    "score": 0.91,
                },
                {
                    "title": "Cadastro público",
                    "url": "https://dados.gov.br/empresa-teste",
                    "content": "EMPRESA TESTE LTDA 12.345.678/0001-90",
                    "score": 0.88,
                },
            ]
        }


def test_tavily_discovery_keeps_indexed_linkedin_without_crawling_it():
    seed = OrganizationSeed(legal_name="EMPRESA TESTE LTDA", cnpj="12345678000190")
    result = TavilyDiscovery(client=FakeTavily()).discover(seed, deep=False)
    linkedin = next(doc for doc in result.documents if "linkedin.com/in" in doc.url)
    assert linkedin.source_class == SourceClass.PROFESSIONAL_PUBLIC
    assert len(result.queries) >= 6
    assert any("dados.gov.br" in url for url, _ in result.candidate_urls)


def test_query_plan_covers_all_intelligence_layers():
    seed = OrganizationSeed(legal_name="EMPRESA TESTE LTDA", cnpj="12345678000190")
    specs = _layered_query_plan(seed, deep=True)
    layers = {spec.layer for spec in specs}
    assert layers == {
        EvidenceLayer.CORPORATE_STRUCTURE,
        EvidenceLayer.INSTITUTIONAL,
        EvidenceLayer.PUBLIC_CONTACTS,
        EvidenceLayer.FISCAL_LITIGATION,
        EvidenceLayer.OFFICIAL_DOCUMENTS,
        EvidenceLayer.FINANCIAL_STATEMENTS,
        EvidenceLayer.GOVERNANCE,
        EvidenceLayer.RECENT_EVENTS,
    }
    joined = " ".join(spec.query.casefold() for spec in specs)
    assert "contingências" in joined
    assert "garantia judicial" in joined
    assert "diretor financeiro" in joined
    assert "grupo econômico" in joined
    assert "carta fiança" in joined
    assert "parecer dos auditores" in joined
    included = {domain for spec in specs for domain in spec.include_domains}
    assert {"stf.jus.br", "trf3.jus.br", "tjsp.jus.br", "trt2.jus.br"} <= included
    assert all(len(spec.query) < 400 for spec in specs)


def test_recent_queries_send_time_range_to_tavily():
    client = BlacklistTavily()
    seed = OrganizationSeed(legal_name="EMPRESA TESTE LTDA", cnpj="12345678000190")
    TavilyDiscovery(client=client).discover_layers(seed, deep=True)
    assert any(call.get("time_range") == "year" for call in client.calls)


class BlacklistTavily:
    def __init__(self):
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "results": [
                {
                    "title": "EMPRESA TESTE LTDA",
                    "url": "https://cnpj.biz/empresa-teste",
                    "content": "EMPRESA TESTE LTDA 12.345.678/0001-90",
                    "score": 0.99,
                },
                {
                    "title": "Ato oficial EMPRESA TESTE LTDA",
                    "url": "https://dados.gov.br/empresa-teste",
                    "content": "EMPRESA TESTE LTDA 12.345.678/0001-90",
                    "score": 0.80,
                },
            ]
        }


def test_blacklist_is_sent_to_tavily_and_enforced_on_results():
    client = BlacklistTavily()
    seed = OrganizationSeed(legal_name="EMPRESA TESTE LTDA", cnpj="12345678000190")
    result = TavilyDiscovery(client=client).discover_identity(seed)
    assert client.calls
    assert all(set(GLOBAL_EXCLUDED_DOMAINS) <= set(call["exclude_domains"]) for call in client.calls)
    assert not any("cnpj.biz" in document.url for document in result.documents)
    assert any("global_domain_blacklist" == item["reason"] for item in result.rejected)
