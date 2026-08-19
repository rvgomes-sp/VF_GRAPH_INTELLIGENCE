from __future__ import annotations

from urllib.parse import urlparse

from .models import SourceClass


def classify_source(url: str, claimed: SourceClass | None = None) -> SourceClass:
    """Classificacao por dominio; uma configuracao nao pode promover fonte privada a oficial."""

    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".jus.br") or host == "jus.br":
        return SourceClass.OFFICIAL_COURT
    if host.endswith(".gov.br") or host == "gov.br":
        if any(token in host for token in ("receita", "jucesp", "fazenda")):
            return SourceClass.OFFICIAL_REGISTRY
        return SourceClass.OFFICIAL_GOVERNMENT
    if claimed in {
        SourceClass.OFFICIAL_COURT,
        SourceClass.OFFICIAL_REGISTRY,
        SourceClass.OFFICIAL_GOVERNMENT,
    }:
        return SourceClass.AGGREGATOR
    return claimed or SourceClass.AGGREGATOR
