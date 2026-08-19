from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from .models import Claim, ClaimStatus, SourceClass


@dataclass(frozen=True)
class CollectionPolicy:
    """Limites tecnicos, eticos e juridicos da coleta publica."""

    user_agent: str = "VF-OSINT/0.1 (+contato: compliance@vf.local)"
    timeout_seconds: float = 20.0
    per_host_interval_seconds: float = 1.5
    max_response_bytes: int = 8_000_000
    max_pages_per_run: int = 40
    max_depth: int = 2
    obey_robots: bool = True
    allowed_schemes: tuple[str, ...] = ("http", "https")
    denied_hosts: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "linkedin.com",
                "www.linkedin.com",
                "facebook.com",
                "www.facebook.com",
                "instagram.com",
                "www.instagram.com",
                "google.com",
                "econodata.com.br",
                "cnpj.biz",
                "empresaqui.com.br",
                "solucoes.receita.fazenda.gov.br",
                "jusbrasil.com.br",
                "serasaexperian.com.br",
                "vriconsulting.com.br",
                "consultasocio.com",
                "cnpj.in",
                "advdinamico.com.br",
                "encontre.io",
                "acheiempresa.com.br",
                "cnpj.tradexa.com.br",
            }
        )
    )

    def allows_url(self, url: str) -> tuple[bool, str]:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in self.allowed_schemes:
            return False, "scheme_not_allowed"
        host = (parsed.hostname or "").lower()
        if not host:
            return False, "missing_host"
        if any(host == denied or host.endswith(f".{denied}") for denied in self.denied_hosts):
            return False, "host_policy_denied"
        if parsed.username or parsed.password:
            return False, "embedded_credentials_denied"
        return True, "allowed"


class BusinessRules:
    """Regras de funil e abordagem pertencentes ao negocio V&F."""

    required_confirmed_predicates = {
        "organization.legal_name",
        "organization.cnpj",
        "process.number",
        "process.role",
        "process.event",
    }
    approach_blocking_gaps = {
        "process_source",
        "process_current_phase",
        "legal_objective",
    }

    @staticmethod
    def classify_opportunity(claims: list[Claim], gaps: list[str], score: float) -> str:
        confirmed = {claim.predicate for claim in claims if claim.status in {ClaimStatus.CONFIRMED, ClaimStatus.CORROBORATED}}
        if "reputational_risk" in gaps:
            return "NAO_PROSSEGUIR"
        if BusinessRules.required_confirmed_predicates.issubset(confirmed) and score >= 70:
            return "AVANCAR"
        return "MONITORAR"


class MarketRules:
    """Regras de mercado segurador; nao alteram o funil comercial por inferencia."""

    @staticmethod
    def suitability(claims: list[Claim]) -> dict[str, str]:
        predicates = {claim.predicate: claim for claim in claims}
        guarantee = predicates.get("process.guarantee_status")
        event = predicates.get("process.event")
        if event and str(event.value).upper() in {"SISBAJUD", "PENHORA", "EXECUCAO FISCAL"}:
            hypothesis = "Avaliar seguro garantia judicial ou solucao correlata com o tributarista."
        else:
            hypothesis = "Aderencia securitaria ainda nao demonstrada."
        if guarantee is None or guarantee.status not in {ClaimStatus.CONFIRMED, ClaimStatus.CORROBORATED}:
            validation = "Confirmar nos autos garantia, valor atualizado, fase e finalidade processual."
        else:
            validation = "Revisar clausulado, vigencia, valor, atualizacao e aceitacao pelo juizo."
        return {
            "hypothesis": hypothesis,
            "validation": validation,
            "insurer_direction": "NAO_DEFINIDA_SEM_APETITE_VIGENTE_E_ANALISE_DE_SUBSCRICAO",
        }


def can_promote_to_confirmed(source_class: SourceClass, excerpt: str) -> bool:
    return source_class in {
        SourceClass.OFFICIAL_COURT,
        SourceClass.OFFICIAL_REGISTRY,
        SourceClass.OFFICIAL_GOVERNMENT,
    } and bool(excerpt.strip())
