from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse

from .models import Claim, OrganizationSeed, SourceClass


OFFICIAL_CLASSES = {
    SourceClass.OFFICIAL_COURT,
    SourceClass.OFFICIAL_REGISTRY,
    SourceClass.OFFICIAL_GOVERNMENT,
}


def assess_entity_identity(seed: OrganizationSeed, claims: list[Claim]) -> dict:
    """Resolve CNPJ–razão social sem transformar divergência em dado comercial."""
    candidates: dict[str, dict] = {}
    grouped: dict[str, list[Claim]] = defaultdict(list)
    for claim in claims:
        if claim.predicate != "organization.legal_name":
            continue
        if claim.source.extractor != "cnpj_context_legal_name_v1":
            continue
        value = str(claim.value).strip()
        if value:
            grouped[_canonical_name(value)].append(claim)

    for canonical, group in grouped.items():
        hosts = {
            (urlparse(claim.source.url).hostname or claim.source.url).lower()
            for claim in group
        }
        official = any(claim.source.source_class in OFFICIAL_CLASSES for claim in group)
        representative = max(group, key=lambda claim: claim.confidence)
        candidates[canonical] = {
            "name": str(representative.value),
            "source_count": len(hosts),
            "official": official,
            "sources": sorted(hosts),
            "confidence": round(max(claim.confidence for claim in group), 2),
        }

    requested_placeholder = seed.legal_name.upper().startswith("CNPJ ")
    requested = None if requested_placeholder else seed.legal_name.strip()
    ranked = sorted(
        candidates.values(),
        key=lambda item: (item["official"], item["source_count"], item["confidence"]),
        reverse=True,
    )
    matches = [item for item in ranked if requested and _names_match(requested, item["name"])]
    adverse = [item for item in ranked if requested and not _names_match(requested, item["name"])]
    strong_adverse = [item for item in adverse if item["official"] or item["source_count"] >= 2]

    if requested and strong_adverse and not matches:
        resolved = strong_adverse[0]["name"]
        return {
            "status": "ENTITY_CONFLICT",
            "blocked": True,
            "requested_name": requested,
            "resolved_name": resolved,
            "headline_name": resolved,
            "rationale": (
                "O CNPJ foi associado de forma corroborada a razão social diferente da informada. "
                "A pesquisa profunda e a abordagem foram interrompidas."
            ),
            "candidates": ranked,
        }

    if matches:
        best = matches[0]
        status = "VERIFIED" if best["official"] else "CORROBORATED" if best["source_count"] >= 2 else "COHERENT_UNCONFIRMED"
        return {
            "status": status,
            "blocked": False,
            "requested_name": requested,
            "resolved_name": best["name"],
            "headline_name": best["name"],
            "rationale": "O nome informado é coerente com evidência localizada no contexto do CNPJ.",
            "candidates": ranked,
        }

    if not requested and ranked:
        best = ranked[0]
        status = "VERIFIED" if best["official"] else "CORROBORATED" if best["source_count"] >= 2 else "UNRESOLVED"
        return {
            "status": status,
            "blocked": False,
            "requested_name": None,
            "resolved_name": best["name"] if status != "UNRESOLVED" else None,
            "headline_name": best["name"] if status != "UNRESOLVED" else seed.legal_name,
            "rationale": "Razão social inferida apenas quando sustentada por fonte oficial ou por fontes independentes.",
            "candidates": ranked,
        }

    return {
        "status": "UNRESOLVED",
        "blocked": False,
        "requested_name": requested,
        "resolved_name": None,
        "headline_name": requested or seed.legal_name,
        "rationale": "Não houve evidência suficiente para confirmar a associação CNPJ–razão social.",
        "candidates": ranked,
    }


def _names_match(left: str, right: str) -> bool:
    left_tokens = _name_tokens(left)
    right_tokens = _name_tokens(right)
    if not left_tokens or not right_tokens:
        return _canonical_name(left) == _canonical_name(right)
    overlap = len(left_tokens & right_tokens)
    return overlap >= max(1, min(len(left_tokens), len(right_tokens)) // 2)


def _name_tokens(value: str) -> set[str]:
    ignored = {
        "ltda", "limitada", "sa", "sociedade", "anonima", "me", "epp", "eireli",
        "de", "da", "do", "das", "dos", "e", "comercio", "servicos",
    }
    return {
        token
        for token in _canonical_name(value).split()
        if len(token) >= 3 and token not in ignored
    }


def _canonical_name(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in folded if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value.casefold()))
