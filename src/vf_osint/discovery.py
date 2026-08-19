from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .models import EvidenceLayer, OrganizationSeed, PublicDocument, SourceClass
from .sources import classify_source


SOCIAL_HOSTS = {"linkedin.com", "www.linkedin.com"}

# Fontes cadastrais e agregadores são excluídos na consulta e novamente na saída.
# A segunda barreira impede que um provedor devolva um domínio solicitado como excluído.
GLOBAL_EXCLUDED_DOMAINS = (
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
    "instagram.com",
    "facebook.com",
    "acheiempresa.com.br",
    "cnpj.tradexa.com.br",
)

DIRECTORY_HOST_TOKENS = ("cnpj", "consultaempresa", "acheiempresa", "dadoscadastrais")

# Fontes primárias e regulatórias recebem prioridade adicional, sem dispensar
# a validação local de identidade e de contexto do resultado.
HIGH_PRIORITY_HOST_TOKENS = (
    "carf",
    "cvm.gov.br",
    "b3.com.br",
    "pgfn.gov.br",
    "receita",
    "fazenda",
    "in.gov.br",
    "stf.jus.br",
    "stj.jus.br",
    "tst.jus.br",
    "trf",
    "trt",
    "tj",
)


@dataclass(frozen=True)
class QuerySpec:
    layer: EvidenceLayer
    objective: str
    query: str
    include_domains: tuple[str, ...] = ()
    time_range: str | None = None


@dataclass
class DiscoveryResult:
    documents: list[PublicDocument] = field(default_factory=list)
    candidate_urls: list[tuple[str, SourceClass]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    query_specs: list[dict[str, Any]] = field(default_factory=list)
    layer_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    rejected: list[dict[str, str]] = field(default_factory=list)
    provider: str = "tavily"

    def merge(self, other: "DiscoveryResult") -> "DiscoveryResult":
        self.documents.extend(other.documents)
        self.candidate_urls.extend(other.candidate_urls)
        self.queries.extend(other.queries)
        self.query_specs.extend(other.query_specs)
        self.rejected.extend(other.rejected)
        for layer, values in other.layer_stats.items():
            current = self.layer_stats.setdefault(
                layer, {"queries": 0, "results": 0, "documents": 0, "domains": []}
            )
            for key in ("queries", "results", "documents"):
                current[key] = int(current.get(key, 0)) + int(values.get(key, 0))
            current["domains"] = sorted(
                set(current.get("domains", [])) | set(values.get("domains", []))
            )
        self.documents = _deduplicate_documents(self.documents)
        self.candidate_urls = list(dict.fromkeys(self.candidate_urls))
        return self


class TavilyDiscovery:
    """Pesquisa por camadas com exclusão defensiva e diversidade de domínio."""

    def __init__(self, client: Any | None = None):
        self._client = client

    @property
    def available(self) -> bool:
        return self._client is not None or bool(os.environ.get("TAVILY_API_KEY"))

    def discover_identity(
        self, seed: OrganizationSeed, *, max_results_per_query: int = 6
    ) -> DiscoveryResult:
        return self._search_specs(
            seed,
            _identity_query_plan(seed),
            deep=False,
            max_results_per_query=max_results_per_query,
        )

    def discover_layers(
        self,
        seed: OrganizationSeed,
        *,
        deep: bool = False,
        max_results_per_query: int = 6,
    ) -> DiscoveryResult:
        return self._search_specs(
            seed,
            _layered_query_plan(seed, deep=deep),
            deep=deep,
            max_results_per_query=max_results_per_query,
        )

    def discover(
        self,
        seed: OrganizationSeed,
        *,
        deep: bool = False,
        max_results_per_query: int = 6,
    ) -> DiscoveryResult:
        """Compatibilidade: executa pré-validação e as camadas de inteligência."""
        identity = self.discover_identity(seed, max_results_per_query=max_results_per_query)
        layers = self.discover_layers(
            seed, deep=deep, max_results_per_query=max_results_per_query
        )
        return identity.merge(layers)

    def _search_specs(
        self,
        seed: OrganizationSeed,
        specs: list[QuerySpec],
        *,
        deep: bool,
        max_results_per_query: int,
    ) -> DiscoveryResult:
        result = DiscoveryResult(
            queries=[spec.query for spec in specs],
            query_specs=[
                {
                    "layer": spec.layer.value,
                    "objective": spec.objective,
                    "query": spec.query,
                    "include_domains": list(spec.include_domains),
                    "time_range": spec.time_range,
                }
                for spec in specs
            ],
        )
        if not self.available:
            result.rejected.append(
                {"provider": "tavily", "reason": "TAVILY_API_KEY_nao_configurada"}
            )
            return result

        client = self._client or self._build_client()
        indexed: dict[tuple[str, str], dict[str, Any]] = {}
        for spec in specs:
            stats = result.layer_stats.setdefault(
                spec.layer.value,
                {"queries": 0, "results": 0, "documents": 0, "domains": []},
            )
            stats["queries"] += 1
            try:
                search_kwargs = dict(
                    query=spec.query[:399],
                    search_depth="advanced",
                    max_results=max_results_per_query,
                    country="brazil",
                    include_raw_content=False,
                    include_answer=False,
                    exclude_domains=list(GLOBAL_EXCLUDED_DOMAINS),
                )
                if spec.include_domains:
                    search_kwargs["include_domains"] = list(spec.include_domains)
                if spec.time_range:
                    search_kwargs["time_range"] = spec.time_range
                response = client.search(**search_kwargs)
            except Exception as exc:  # SDK/network boundary
                result.rejected.append(
                    {
                        "provider": "tavily",
                        "layer": spec.layer.value,
                        "query": spec.query,
                        "reason": _safe_error(exc),
                    }
                )
                continue

            for raw_item in response.get("results", []):
                item = dict(raw_item)
                url = str(item.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    continue
                if _host_is_excluded(url):
                    result.rejected.append(
                        {
                            "provider": "tavily",
                            "layer": spec.layer.value,
                            "url": url,
                            "reason": "global_domain_blacklist",
                        }
                    )
                    continue
                if not _result_relevant(seed, item):
                    continue
                source_class = _classify_discovered_url(url, seed)
                if not _layer_accepts_source(spec.layer, source_class):
                    result.rejected.append(
                        {
                            "provider": "tavily",
                            "layer": spec.layer.value,
                            "url": url,
                            "reason": "layer_source_policy",
                        }
                    )
                    continue
                item["_layer"] = spec.layer
                item["_objective"] = spec.objective
                item["_rank"] = _rank_item(item, seed, spec.layer)
                key = (spec.layer.value, url)
                previous = indexed.get(key)
                if previous is None or item["_rank"] > previous["_rank"]:
                    indexed[key] = item
                stats["results"] += 1

        ordered = _diversify_results(indexed.values(), per_domain=2, per_layer=10)
        for item in ordered:
            url = str(item["url"])
            layer = item["_layer"]
            source_class = _classify_discovered_url(url, seed)
            snippet = "\n".join(
                part
                for part in (str(item.get("title") or ""), str(item.get("content") or ""))
                if part
            )
            result.documents.append(
                PublicDocument(
                    url=url,
                    title=str(item.get("title") or url),
                    text=snippet,
                    source_class=source_class,
                    content_type="text/x-search-snippet",
                    evidence_layer=layer,
                    search_objective=str(item["_objective"]),
                    search_score=float(item.get("score") or 0),
                )
            )
            result.candidate_urls.append((url, source_class))
            stats = result.layer_stats[layer.value]
            stats["documents"] += 1
            stats["domains"] = sorted(
                set(stats["domains"]) | {(urlparse(url).hostname or "").lower()}
            )

        if deep:
            result.documents.extend(self._extract_by_layer(client, seed, ordered, result.rejected))
            result.documents.extend(
                self._crawl_corporate_candidates(client, seed, ordered, result.rejected)
            )

        result.documents = _deduplicate_documents(result.documents)
        result.candidate_urls = list(dict.fromkeys(result.candidate_urls))
        return result

    @staticmethod
    def _build_client():
        from tavily import TavilyClient

        return TavilyClient()

    @staticmethod
    def _extract_by_layer(client, seed, ordered, rejected):
        documents: list[PublicDocument] = []
        by_layer: dict[EvidenceLayer, list[dict[str, Any]]] = {}
        for item in ordered:
            url = str(item["url"])
            if _can_extract(url):
                by_layer.setdefault(item["_layer"], []).append(item)

        for layer, items in by_layer.items():
            selected = items[:6]
            try:
                payload = client.extract(
                    urls=[str(item["url"]) for item in selected],
                    extract_depth="advanced",
                    format="markdown",
                    query=_extraction_focus(layer),
                    chunks_per_source=4,
                )
            except Exception as exc:
                rejected.append(
                    {
                        "provider": "tavily_extract",
                        "layer": layer.value,
                        "reason": _safe_error(exc),
                    }
                )
                continue
            metadata = {str(item["url"]): item for item in selected}
            for item in payload.get("results", []):
                url = str(item.get("url") or "")
                text = str(item.get("raw_content") or "")
                if not url or not text or _host_is_excluded(url):
                    continue
                meta = metadata.get(url, {})
                documents.append(
                    PublicDocument(
                        url=url,
                        title=str(item.get("title") or url),
                        text=text,
                        source_class=_classify_discovered_url(url, seed),
                        content_type="text/markdown",
                        evidence_layer=layer,
                        search_objective=str(meta.get("_objective") or ""),
                        search_score=float(meta.get("score") or 0),
                    )
                )
            for failure in payload.get("failed_results", []):
                rejected.append(
                    {
                        "provider": "tavily_extract",
                        "layer": layer.value,
                        "url": str(failure.get("url") or ""),
                        "reason": str(failure.get("error") or "extract_failed")[:300],
                    }
                )
        return documents

    @staticmethod
    def _crawl_corporate_candidates(client, seed, ordered, rejected):
        roots = _corporate_roots(seed, ordered)[:2]
        documents = []
        for root in roots:
            try:
                payload = client.crawl(
                    url=root,
                    max_depth=2,
                    max_breadth=16,
                    limit=16,
                    instructions=(
                        f"Localize páginas públicas da empresa associada ao CNPJ {seed.cnpj}: "
                        "relatórios, demonstrações, governança, jurídico, finanças e contatos institucionais."
                    ),
                    chunks_per_source=4,
                    extract_depth="advanced",
                    format="markdown",
                    allow_external=False,
                )
            except Exception as exc:
                rejected.append(
                    {"provider": "tavily_crawl", "url": root, "reason": _safe_error(exc)}
                )
                continue
            for item in payload.get("results", []):
                url = str(item.get("url") or "")
                text = str(item.get("raw_content") or "")
                if url and text and not _host_is_excluded(url):
                    documents.append(
                        PublicDocument(
                            url=url,
                            title=str(item.get("title") or url),
                            text=text,
                            source_class=SourceClass.COMPANY_OWNED,
                            content_type="text/markdown",
                            evidence_layer=EvidenceLayer.GOVERNANCE,
                            search_objective="corporate_site_deepening",
                        )
                    )
        return documents


# ---------------------------------------------------------------------------
# Modo LEAN (default ligado): consultoria = baixo volume + Tavily grátis.
# Reduz o dossiê de ~34 para ~9 buscas de alto rendimento, priorizando as
# camadas onde os DECISORES conhecidos aparecem (governança/contatos/evento)
# — o que faz o crédito grátis render ~3-4x mais dossiês. Desligue com
# VF_LEAN_SEARCH=0 quando o Tavily tiver plano pago (volta o plano completo).
# ---------------------------------------------------------------------------
_LEAN_IDENTITY_OBJECTIVES = {"exact_cnpj", "cnpj_name_coherence"}
_LEAN_LAYERED_OBJECTIVES = {
    "economic_group",          # estrutura/grupo — ancora identidade e coligadas
    "corporate_contacts",      # e-mail/telefone/domínio (padrão de e-mail)
    "finance_leadership",      # CFO / diretor financeiro / tesouraria
    "legal_tax_leadership",    # jurídico interno / gerente tributário
    "external_tax_counsel",    # tributarista / advogado externo
    "asset_constraints",       # penhora / SISBAJUD / substituição (evento securitário)
    "recent_official_events",  # movimentação recente
}


def _lean_search_enabled() -> bool:
    return os.environ.get("VF_LEAN_SEARCH", "1").strip().lower() not in {"0", "false", "no", "off"}


def _identity_query_plan(seed: OrganizationSeed) -> list[QuerySpec]:
    cnpj = _format_cnpj(seed.cnpj)
    specs = [
        QuerySpec(EvidenceLayer.IDENTITY, "exact_cnpj", f'"{cnpj}" razão social'),
        QuerySpec(
            EvidenceLayer.IDENTITY,
            "official_entity_confirmation",
            f'"{cnpj}" site:gov.br OR site:jus.br',
        ),
    ]
    if not _is_placeholder_name(seed.legal_name):
        specs.append(
            QuerySpec(
                EvidenceLayer.IDENTITY,
                "cnpj_name_coherence",
                f'"{cnpj}" "{seed.legal_name}"',
            )
        )
    if _lean_search_enabled():
        # exact_cnpj está sempre presente e sustenta o gate de identidade.
        specs = [spec for spec in specs if spec.objective in _LEAN_IDENTITY_OBJECTIVES] or specs[:1]
    return specs


def _layered_query_plan(seed: OrganizationSeed, *, deep: bool) -> list[QuerySpec]:
    target = _target(seed)
    cnpj = _format_cnpj(seed.cnpj)
    specs = [
        QuerySpec(EvidenceLayer.CORPORATE_STRUCTURE, "economic_group", f'{target} "grupo econômico" OR controladora OR controlada'),
        QuerySpec(EvidenceLayer.CORPORATE_STRUCTURE, "corporate_perimeter", f'{target} subsidiária OR coligada OR filiais OR "unidades operacionais"'),
        QuerySpec(EvidenceLayer.INSTITUTIONAL, "government_footprint", f'{target} site:gov.br OR site:in.gov.br'),
        QuerySpec(EvidenceLayer.INSTITUTIONAL, "public_contracts", f'{target} licitação OR "contrato público" OR incentivo fiscal'),
        QuerySpec(EvidenceLayer.INSTITUTIONAL, "regulatory_footprint", f'{target} regulador OR associação setorial OR "Diário Oficial"'),
        QuerySpec(EvidenceLayer.PUBLIC_CONTACTS, "corporate_contacts", f'{target} e-mail telefone contato jurídico financeiro'),
        QuerySpec(EvidenceLayer.PUBLIC_CONTACTS, "public_documents_contacts", f'{target} procuração OR assinatura OR protocolo e-mail filetype:pdf'),
        QuerySpec(EvidenceLayer.FISCAL_LITIGATION, "tax_exposure", f'{target} "crédito tributário" OR "auto de infração" OR "passivo fiscal"'),
        QuerySpec(EvidenceLayer.FISCAL_LITIGATION, "tax_litigation", f'{target} ICMS OR ISS OR IPI OR PIS OR COFINS OR compensação'),
        QuerySpec(EvidenceLayer.FISCAL_LITIGATION, "administrative_tax", f'{target} CARF OR PGFN OR "Receita Federal" OR SEFAZ'),
        QuerySpec(EvidenceLayer.OFFICIAL_DOCUMENTS, "judicial_proceedings", f'{target} "execução fiscal" OR "mandado de segurança tributário"'),
        QuerySpec(EvidenceLayer.OFFICIAL_DOCUMENTS, "judicial_guarantees", f'{target} "seguro garantia judicial" OR "carta fiança" OR "depósito judicial"'),
        QuerySpec(EvidenceLayer.OFFICIAL_DOCUMENTS, "asset_constraints", f'{target} SISBAJUD OR RENAJUD OR penhora OR bloqueio OR "substituição de garantia"'),
        QuerySpec(EvidenceLayer.FINANCIAL_STATEMENTS, "tax_contingencies", f'{target} "contingências tributárias" OR provisões OR "passivos fiscais"'),
        QuerySpec(EvidenceLayer.FINANCIAL_STATEMENTS, "financial_statements", f'{target} "demonstrações financeiras" OR balanço OR "notas explicativas" filetype:pdf'),
        QuerySpec(EvidenceLayer.FINANCIAL_STATEMENTS, "audit_and_regulatory_filings", f'{target} "relatório anual" OR "parecer dos auditores" OR CVM OR B3'),
        QuerySpec(EvidenceLayer.GOVERNANCE, "finance_leadership", f'{target} CFO OR "diretor financeiro" OR tesouraria OR controller'),
        QuerySpec(EvidenceLayer.GOVERNANCE, "legal_tax_leadership", f'{target} "diretor jurídico" OR "gerente jurídico" OR "gerente tributário" OR compliance'),
        QuerySpec(EvidenceLayer.GOVERNANCE, "external_tax_counsel", f'{target} "escritório tributarista" OR "advogado tributário" OR procurador'),
        QuerySpec(EvidenceLayer.RECENT_EVENTS, "recent_official_events", f'{target} decisão OR publicação OR processo OR garantia', time_range="year"),
    ]
    if deep:
        specs.extend(
            [
                QuerySpec(EvidenceLayer.CORPORATE_STRUCTURE, "cnpj_group_crosscheck", f'"{cnpj}" controladora OR subsidiária OR filial'),
                QuerySpec(EvidenceLayer.INSTITUTIONAL, "gazettes_and_tax_authorities", f'{target} DOU OR "Diário Oficial" OR PGFN OR SEFAZ'),
                QuerySpec(EvidenceLayer.PUBLIC_CONTACTS, "investor_relations_contacts", f'{target} "relações com investidores" OR "fale conosco" OR contato RI'),
                QuerySpec(EvidenceLayer.FISCAL_LITIGATION, "tax_remedies", f'{target} "decisão CARF" OR parcelamento OR transação tributária'),
                QuerySpec(
                    EvidenceLayer.OFFICIAL_DOCUMENTS,
                    "higher_courts",
                    f'{target} decisão processo tributário',
                    include_domains=("stf.jus.br", "stj.jus.br", "tst.jus.br"),
                ),
                QuerySpec(
                    EvidenceLayer.OFFICIAL_DOCUMENTS,
                    "regional_federal_courts",
                    f'{target} execução fiscal decisão',
                    include_domains=("trf1.jus.br", "trf2.jus.br", "trf3.jus.br", "trf4.jus.br", "trf5.jus.br", "trf6.jus.br"),
                ),
                QuerySpec(
                    EvidenceLayer.OFFICIAL_DOCUMENTS,
                    "state_and_labor_courts",
                    f'{target} processo garantia decisão',
                    include_domains=("tjsp.jus.br", "tjrj.jus.br", "tjmg.jus.br", "trt2.jus.br", "trt3.jus.br"),
                ),
                QuerySpec(EvidenceLayer.FINANCIAL_STATEMENTS, "cnpj_financial_crosscheck", f'"{cnpj}" contingências OR "depósitos judiciais" OR demonstrações'),
                QuerySpec(EvidenceLayer.GOVERNANCE, "corporate_governance_documents", f'{target} diretoria conselho administração governança filetype:pdf'),
                QuerySpec(EvidenceLayer.RECENT_EVENTS, "recent_tax_events", f'{target} CARF OR execução fiscal OR auto de infração', time_range="year"),
            ]
        )
    if _lean_search_enabled():
        specs = [spec for spec in specs if spec.objective in _LEAN_LAYERED_OBJECTIVES]
    if any(len(spec.query) >= 400 for spec in specs):
        raise ValueError("Consulta Tavily excede o limite de 399 caracteres")
    return specs


def _query_plan(seed: OrganizationSeed) -> list[str]:
    """Compatibilidade para consumidores antigos e testes externos."""
    return [spec.query for spec in _identity_query_plan(seed) + _layered_query_plan(seed, deep=False)]


def _target(seed: OrganizationSeed) -> str:
    if _is_placeholder_name(seed.legal_name):
        return f'"{_format_cnpj(seed.cnpj)}"'
    return f'"{seed.legal_name}"'


def _is_placeholder_name(value: str) -> bool:
    return value.upper().startswith("CNPJ ")


def _classify_discovered_url(
    url: str, seed: OrganizationSeed | None = None
) -> SourceClass:
    host = (urlparse(url).hostname or "").lower()
    if host in SOCIAL_HOSTS or host.endswith(".linkedin.com"):
        return SourceClass.PROFESSIONAL_PUBLIC
    if _is_directory_host(host):
        return SourceClass.AGGREGATOR
    if seed and _host_matches_company(host, seed):
        return classify_source(url, SourceClass.COMPANY_OWNED)
    return classify_source(url, SourceClass.NEWS)


def _layer_accepts_source(layer: EvidenceLayer, source: SourceClass) -> bool:
    if source == SourceClass.AGGREGATOR:
        return False
    if layer == EvidenceLayer.OFFICIAL_DOCUMENTS:
        return source in {
            SourceClass.OFFICIAL_COURT,
            SourceClass.OFFICIAL_REGISTRY,
            SourceClass.OFFICIAL_GOVERNMENT,
        }
    if layer == EvidenceLayer.GOVERNANCE:
        return True
    if layer == EvidenceLayer.PUBLIC_CONTACTS:
        return source != SourceClass.PROFESSIONAL_PUBLIC
    return source != SourceClass.PROFESSIONAL_PUBLIC


def _is_directory_host(host: str) -> bool:
    compact = "".join(character for character in host.casefold() if character.isalnum())
    return any(token in compact for token in DIRECTORY_HOST_TOKENS)


def _host_matches_company(host: str, seed: OrganizationSeed) -> bool:
    if _is_placeholder_name(seed.legal_name):
        return False
    compact_host = "".join(character for character in host.casefold() if character.isalnum())
    tokens = _distinctive_name_tokens(seed.legal_name)
    company_tokens = [token for token in tokens if token not in {"corretora", "seguros", "comercio", "servicos"}]
    if not company_tokens:
        company_tokens = tokens
    required = 1 if len(company_tokens) == 1 else 2
    return sum(token in compact_host for token in company_tokens) >= required


def _host_is_excluded(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == denied or host.endswith(f".{denied}") for denied in GLOBAL_EXCLUDED_DOMAINS)


def _can_extract(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and not _host_is_excluded(url) and not (
        host in SOCIAL_HOSTS or host.endswith(".linkedin.com")
    )


def _corporate_roots(seed: OrganizationSeed, ordered: list[dict[str, Any]]) -> list[str]:
    roots = []
    for item in ordered:
        if item.get("_layer") not in {
            EvidenceLayer.GOVERNANCE,
            EvidenceLayer.FINANCIAL_STATEMENTS,
            EvidenceLayer.CORPORATE_STRUCTURE,
            EvidenceLayer.PUBLIC_CONTACTS,
        }:
            continue
        url = str(item.get("url") or "")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host or _host_is_excluded(url) or not _host_matches_company(host, seed):
            continue
        if host in SOCIAL_HOSTS or host.endswith((".gov.br", ".jus.br", ".linkedin.com")):
            continue
        roots.append(f"{parsed.scheme}://{parsed.netloc}")
    return list(dict.fromkeys(roots))


def _result_relevant(seed: OrganizationSeed, item: dict[str, Any]) -> bool:
    url = str(item.get("url") or "")
    if _host_is_excluded(url):
        return False
    haystack = f"{item.get('title', '')} {item.get('content', '')}".casefold()
    digits = "".join(character for character in haystack if character.isdigit())
    if seed.cnpj in digits:
        return True
    if not _is_placeholder_name(seed.legal_name):
        legal = seed.legal_name.casefold()
        if legal in haystack:
            return True
        tokens = _distinctive_name_tokens(seed.legal_name)
        present = sum(token in haystack for token in tokens)
        host = (urlparse(url).hostname or "").lower()
        if host.endswith("linkedin.com") and present >= 1:
            return True
        required = max(2, (len(tokens) * 3 + 4) // 5)
        return bool(tokens) and present >= min(required, len(tokens))
    return False


def _rank_item(
    item: dict[str, Any], seed: OrganizationSeed, layer: EvidenceLayer | None = None
) -> float:
    url = str(item.get("url") or "")
    source = _classify_discovered_url(url, seed)
    source_bonus = {
        SourceClass.OFFICIAL_COURT: 0.30,
        SourceClass.OFFICIAL_REGISTRY: 0.28,
        SourceClass.OFFICIAL_GOVERNMENT: 0.26,
        SourceClass.COMPANY_OWNED: 0.18,
        SourceClass.PROFESSIONAL_PUBLIC: 0.10,
        SourceClass.NEWS: 0.08,
        SourceClass.AGGREGATOR: -0.25,
    }.get(source, 0.0)
    pdf_bonus = 0.06 if urlparse(url).path.casefold().endswith(".pdf") else 0.0
    host = (urlparse(url).hostname or "").casefold()
    primary_bonus = 0.09 if any(token in host for token in HIGH_PRIORITY_HOST_TOKENS) else 0.0
    corporate_bonus = (
        0.05
        if source == SourceClass.COMPANY_OWNED
        and layer in {
            EvidenceLayer.CORPORATE_STRUCTURE,
            EvidenceLayer.PUBLIC_CONTACTS,
            EvidenceLayer.FINANCIAL_STATEMENTS,
            EvidenceLayer.GOVERNANCE,
        }
        else 0.0
    )
    return float(item.get("score") or 0) + source_bonus + pdf_bonus + primary_bonus + corporate_bonus


def _diversify_results(items, *, per_domain: int, per_layer: int) -> list[dict[str, Any]]:
    ordered = sorted(items, key=lambda item: float(item.get("_rank") or 0), reverse=True)
    domain_counts: Counter[tuple[str, str]] = Counter()
    layer_counts: Counter[str] = Counter()
    selected = []
    for item in ordered:
        layer = item["_layer"].value
        host = (urlparse(str(item.get("url") or "")).hostname or "").lower()
        if layer_counts[layer] >= per_layer or domain_counts[(layer, host)] >= per_domain:
            continue
        selected.append(item)
        layer_counts[layer] += 1
        domain_counts[(layer, host)] += 1
    return selected


def _distinctive_name_tokens(legal_name: str) -> list[str]:
    ignored = {"ltda", "sa", "s", "a", "eireli", "me", "epp", "de", "da", "do", "das", "dos"}
    return [
        token.casefold().strip(".,&")
        for token in legal_name.replace("-", " ").split()
        if len(token.strip(".,&")) >= 4 and token.casefold().strip(".,&") not in ignored
    ]


def _extraction_focus(layer: EvidenceLayer) -> str:
    return {
        EvidenceLayer.IDENTITY: "CNPJ, razão social, nome empresarial e situação cadastral",
        EvidenceLayer.INSTITUTIONAL: "relações institucionais, contratos públicos, incentivos e atos oficiais",
        EvidenceLayer.CORPORATE_STRUCTURE: "grupo econômico, controle, controladora, controladas, subsidiárias, filiais e unidades operacionais",
        EvidenceLayer.PUBLIC_CONTACTS: "e-mails e telefones corporativos, RI, jurídico, formulários, procurações, assinaturas e protocolos públicos",
        EvidenceLayer.FISCAL_LITIGATION: "contingências tributárias, processos fiscais, depósitos e garantias judiciais",
        EvidenceLayer.OFFICIAL_DOCUMENTS: "número do processo, partes, evento, data, valor e garantia em documento oficial",
        EvidenceLayer.FINANCIAL_STATEMENTS: "contingências, passivo fiscal, provisões, notas explicativas e demonstrações financeiras",
        EvidenceLayer.GOVERNANCE: "diretoria financeira, jurídico interno, compliance, tesouraria e assessores tributários",
        EvidenceLayer.RECENT_EVENTS: "eventos fiscais, judiciais, financeiros ou societários recentes com data e fonte",
    }[layer]


def _deduplicate_documents(documents: list[PublicDocument]) -> list[PublicDocument]:
    unique: dict[tuple[str, str, str], PublicDocument] = {}
    for document in documents:
        layer = document.evidence_layer.value if document.evidence_layer else ""
        unique.setdefault((document.url, document.content_hash, layer), document)
    return list(unique.values())


def _format_cnpj(value: str) -> str:
    return f"{value[:2]}.{value[2:5]}.{value[5:8]}/{value[8:12]}-{value[12:]}"


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300] or exc.__class__.__name__
