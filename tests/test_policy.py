from vf_osint.models import Claim, ClaimStatus, SourceClass, SourceRecord
from vf_osint.policy import CollectionPolicy, MarketRules
from vf_osint.sources import classify_source


def test_social_network_is_denied_by_default():
    allowed, reason = CollectionPolicy().allows_url("https://www.linkedin.com/company/x")
    assert not allowed
    assert reason == "host_policy_denied"


def test_market_never_auto_selects_insurer():
    result = MarketRules.suitability([])
    assert result["insurer_direction"].startswith("NAO_DEFINIDA")


def test_legacy_event_only_generates_market_hypothesis():
    source = SourceRecord(
        url="file:///legacy.txt",
        source_class=SourceClass.LEGACY_CRM,
        content_hash="abc",
    )
    claim = Claim(
        subject_id="cnpj:1",
        predicate="process.event",
        value="SISBAJUD",
        status=ClaimStatus.HYPOTHESIS,
        confidence=0.32,
        source=source,
        excerpt="sinal legado",
    )
    result = MarketRules.suitability([claim])
    assert "Avaliar" in result["hypothesis"]
    assert "Confirmar" in result["validation"]


def test_private_domain_cannot_be_claimed_as_official():
    assert (
        classify_source("https://example.com/data", SourceClass.OFFICIAL_COURT)
        == SourceClass.AGGREGATOR
    )


def test_jus_br_is_classified_as_official_court():
    assert (
        classify_source("https://esaj.tjsp.jus.br/cpopg/open.do", SourceClass.AGGREGATOR)
        == SourceClass.OFFICIAL_COURT
    )
