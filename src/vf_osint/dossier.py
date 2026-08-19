from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from .learning import GuardedLearner
from .models import Claim, ClaimStatus, Dossier, OrganizationSeed
from .policy import BusinessRules, MarketRules


class DossierBuilder:
    def __init__(self, learner: GuardedLearner):
        self.learner = learner

    def build(
        self,
        seed: OrganizationSeed,
        claims: list[Claim],
        investigation_context: dict | None = None,
    ) -> Dossier:
        context = investigation_context or {}
        layer_claims = _deduplicate_claims(claims, preserve_layer=True)
        claims = _deduplicate_claims(claims, preserve_layer=False)
        identity = context.get("identity_resolution") or {
            "status": "UNRESOLVED",
            "blocked": False,
            "headline_name": seed.legal_name,
            "requested_name": None,
            "resolved_name": None,
            "candidates": [],
        }
        best = _best_claims(claims)
        gaps = self._gaps(best)
        if identity.get("status") == "ENTITY_CONFLICT":
            gaps = sorted(set(gaps) | {"entity_conflict"})
        conflicts = sorted(
            {claim.predicate for claim in claims if claim.status == ClaimStatus.CONFLICT}
        )
        score, components = self._score(best, gaps)
        market = MarketRules.suitability(claims)
        decision = (
            "NAO_PROSSEGUIR"
            if identity.get("blocked")
            else BusinessRules.classify_opportunity(claims, gaps, score)
        )
        persona = self._persona(best)
        learning = self.learner.select_approach_variant(persona)
        approach = self._approach(seed, best, gaps, persona, learning.selected_variant)
        contacts = self._contacts(claims)
        interlocutors = self._interlocutors(claims)
        if identity.get("blocked"):
            approach = {
                "persona": persona,
                "status": "BLOQUEADO_POR_CONFLITO_DE_IDENTIDADE",
                "subject": "Não gerar abordagem",
                "message": (
                    "A razão social informada diverge da entidade associada ao CNPJ. "
                    "Corrija ou confirme o par CNPJ–razão social antes de pesquisar a oportunidade."
                ),
                "missing_before_send": ["entity_conflict"],
                "protected_terms": [
                    "não atribuir fatos de uma pessoa jurídica a outra",
                    "não iniciar contato enquanto a identidade estiver em conflito",
                ],
            }
        dossier_id = sha256(
            f"{seed.subject_id}|{datetime.now(UTC).isoformat()}".encode("utf-8")
        ).hexdigest()[:20]
        decision_payload = {
            "classification": decision,
            "reason": "ENTITY_CONFLICT" if identity.get("blocked") else "EVIDENCE_RULES",
            "next_action": (
                "Corrigir ou confirmar o par CNPJ–razão social e executar nova pesquisa."
                if identity.get("blocked")
                else self._next_action(decision, gaps)
            ),
            "sendable": (
                False
                if identity.get("blocked")
                else not any(gap in BusinessRules.approach_blocking_gaps for gap in gaps)
            ),
        }
        organization = {
            "legal_name": identity.get("headline_name") or _claim_value(best.get("organization.legal_name"), seed.legal_name),
            "cnpj": seed.cnpj,
            "state": seed.state,
            "trade_name": seed.trade_name,
            "linkedin": _claim_value(best.get("organization.linkedin")),
            "requested_legal_name": identity.get("requested_name"),
        }
        intelligence_blocks = self._intelligence_blocks(
            organization=organization,
            identity=identity,
            claims=claims,
            contacts=contacts,
            interlocutors=interlocutors,
            gaps=gaps,
            market=market,
            decision=decision_payload,
            approach=approach,
        )
        return Dossier(
            dossier_id=dossier_id,
            organization=organization,
            executive_reading={
                "evidence_score": round(score, 2),
                "score_components": components,
                "thesis": market["hypothesis"],
                "market_validation": market["validation"],
                "insurer_direction": market["insurer_direction"],
            },
            claims=sorted(claims, key=lambda item: (item.predicate, -item.confidence)),
            gaps=gaps,
            conflicts=conflicts,
            contacts=contacts,
            interlocutors=interlocutors,
            approach=approach
            | {
                "learning_variant": learning.selected_variant,
                "learning_probability": round(learning.probability, 3),
                "learning_basis": learning.basis,
            },
            decision=decision_payload,
            provenance={
                "source_count": len({claim.source.content_hash for claim in claims}),
                "claim_count": len(claims),
                "confirmed_count": sum(claim.status == ClaimStatus.CONFIRMED for claim in claims),
                "corroborated_count": sum(claim.status == ClaimStatus.CORROBORATED for claim in claims),
                "hypothesis_count": sum(claim.status == ClaimStatus.HYPOTHESIS for claim in claims),
                "rule_boundary": {
                    "business": "funil, evidencias minimas, interlocutor e mensagem",
                    "market": "aderencia, capacidade, clausulado, contragarantia e apetite",
                },
            },
            identity_resolution=identity,
            evidence_layers=self._evidence_layer_summary(layer_claims, context),
            intelligence_blocks=intelligence_blocks,
        )

    @staticmethod
    def _evidence_layer_summary(claims: list[Claim], context: dict) -> dict[str, Any]:
        expected = (
            ("corporate_structure", "Estrutura societária e grupo"),
            ("institutional_context", "Contexto institucional"),
            ("public_contacts", "Contatos públicos"),
            ("fiscal_litigation", "Sinais de litigância fiscal"),
            ("official_documents", "Documentos oficiais"),
            ("financial_statements", "Demonstrações financeiras"),
            ("governance_decision_makers", "Governança e decisores"),
            ("recent_events", "Eventos recentes"),
        )
        specs = context.get("query_specs", [])
        stats = context.get("layer_stats", {})
        summary: dict[str, Any] = {}
        for key, label in expected:
            layer_claims = [
                claim
                for claim in claims
                if claim.source.evidence_layer and claim.source.evidence_layer.value == key
            ]
            layer_stats = stats.get(key, {})
            objectives = sorted(
                {spec.get("objective", "") for spec in specs if spec.get("layer") == key}
                - {""}
            )
            status_counts = {
                status.value: sum(claim.status == status for claim in layer_claims)
                for status in ClaimStatus
            }
            summary[key] = {
                "label": label,
                "queries": int(layer_stats.get("queries", 0)),
                "results": int(layer_stats.get("results", 0)),
                "documents": int(layer_stats.get("documents", 0)),
                "domains": layer_stats.get("domains", []),
                "objectives": objectives,
                "claims": len(layer_claims),
                "status_counts": status_counts,
                "coverage": "LACUNA" if not layer_stats.get("documents") else "COLETADO",
            }
        return summary

    @staticmethod
    def _gaps(best: dict[str, Claim]) -> list[str]:
        gaps = []
        confirmed = {
            predicate
            for predicate, claim in best.items()
            if claim.status == ClaimStatus.CONFIRMED
        }
        if "organization.legal_name" not in confirmed or "organization.cnpj" not in confirmed:
            gaps.append("official_entity_confirmation")
        if "process.number" not in confirmed:
            gaps.append("process_source")
        if "process.event" not in confirmed:
            gaps.append("process_current_phase")
        if "process.value" not in confirmed:
            gaps.append("updated_exposure_value")
        if "process.guarantee_status" not in confirmed:
            gaps.append("guarantee_status")
        gaps.append("legal_objective")
        return sorted(set(gaps))

    @staticmethod
    def _score(best: dict[str, Claim], gaps: list[str]) -> tuple[float, dict[str, float]]:
        components = {
            "entity": 0.0,
            "judicial": 0.0,
            "recency": 0.0,
            "guarantee_clarity": 0.0,
            "decision_context": 0.0,
        }
        valid = {ClaimStatus.CONFIRMED, ClaimStatus.CORROBORATED}
        entity_claims = [best.get(key) for key in ("organization.legal_name", "organization.cnpj")]
        if all(claim and claim.status == ClaimStatus.CONFIRMED for claim in entity_claims):
            components["entity"] = 20.0
        elif all(claim and claim.status == ClaimStatus.CORROBORATED for claim in entity_claims):
            components["entity"] = 10.0
        if best.get("process.number") and best["process.number"].status in valid:
            components["judicial"] += 18.0
        if best.get("process.event") and best["process.event"].status in valid:
            components["judicial"] += 17.0
            event_at = best["process.event"].observed_event_at
            if event_at:
                days = max(0, (datetime.now(UTC) - event_at).days)
                components["recency"] = 20.0 if days <= 15 else 12.0 if days <= 60 else 5.0
        if best.get("process.guarantee_status") and best["process.guarantee_status"].status in valid:
            components["guarantee_clarity"] = 15.0
        if best.get("process.value") and best["process.value"].status in valid:
            components["decision_context"] += 5.0
        if best.get("person.professional_role") and best["person.professional_role"].status in valid:
            components["decision_context"] += 5.0
        penalty = min(10.0, 1.5 * len(gaps))
        score = max(0.0, sum(components.values()) - penalty)
        components["gap_penalty"] = -penalty
        return score, components

    @staticmethod
    def _persona(best: dict[str, Claim]) -> str:
        role = str(best.get("person.professional_role", "")).casefold()
        if "jurid" in role:
            return "juridico"
        if "cfo" in role or "finance" in role or "tesour" in role:
            return "financeiro"
        if "tribut" in role:
            return "tributarista"
        return "socio"

    @staticmethod
    def _approach(
        seed: OrganizationSeed,
        best: dict[str, Claim],
        gaps: list[str],
        persona: str,
        variant: str,
    ) -> dict[str, Any]:
        event_claim = best.get("process.event")
        process_claim = best.get("process.number")
        evidence_ready = event_claim and event_claim.status in {
            ClaimStatus.CONFIRMED,
            ClaimStatus.CORROBORATED,
        }
        if not evidence_ready:
            return {
                "persona": persona,
                "status": "RASCUNHO_INTERNO_CONDICIONADO",
                "subject": f"Preparacao de diagnostico - {seed.legal_name}",
                "message": (
                    "Mapeamos sinais publicos que merecem validacao documental antes de qualquer contato. "
                    "O proximo passo interno e confirmar os autos, a fase atual, a existencia de garantia e "
                    "o objetivo definido pelo tributarista. Somente depois disso a abordagem comercial pode "
                    "mencionar uma alternativa de garantia."
                ),
                "missing_before_send": gaps,
                "protected_terms": [
                    "nao afirmar bloqueio ou penhora sem ato oficial",
                    "nao substituir o tributarista",
                    "nao prometer aceitacao judicial ou emissao de apolice",
                    "nao indicar seguradora sem apetite vigente",
                ],
            }
        event = str(event_claim.value)
        process = str(process_claim.value) if process_claim else "processo a confirmar"
        openings = {
            "consultiva_direta": "Identificamos em fonte publica um movimento processual que pode exigir decisao sobre garantia.",
            "juridico_primeiro": "Nossa leitura parte do processo e do objetivo juridico, nunca de uma cotacao isolada.",
            "financeiro_primeiro": "Ha um movimento processual que pode afetar caixa ou ativos e merece avaliacao coordenada.",
        }
        message = (
            f"{openings[variant]} O registro consultado indica {event} no processo {process}. "
            "Nao discutimos a tese nem substituimos o tributarista: estruturamos, ao lado dele, os requisitos "
            "tecnicos para avaliar seguro garantia judicial ou solucao correlata. Podemos validar o contexto "
            "com o juridico e o financeiro antes de qualquer proposta?"
        )
        return {
            "persona": persona,
            "status": "PRONTO_PARA_REVISAO_HUMANA",
            "subject": f"Leitura de garantia judicial - {seed.legal_name}",
            "message": message,
            "missing_before_send": [gap for gap in gaps if gap in BusinessRules.approach_blocking_gaps],
            "protected_terms": [
                "nao substituir o tributarista",
                "nao prometer aceitacao judicial ou emissao de apolice",
                "submeter aderencia a validacao tecnica e de mercado",
            ],
        }

    @staticmethod
    def _interlocutors(claims: list[Claim]) -> list[dict[str, Any]]:
        people = []
        seen = set()
        for claim in claims:
            if claim.predicate != "person.professional_role" or not isinstance(claim.value, dict):
                continue
            key = (claim.value.get("name"), claim.value.get("role"))
            if key in seen:
                continue
            seen.add(key)
            people.append(
                {
                    "name": claim.value.get("name"),
                    "public_role": claim.value.get("role"),
                    "evidence_status": claim.status.value,
                    "commercial_interpretation": "interlocutor_potencial_nao_decisor_confirmado",
                    "linkedin": claim.value.get("linkedin"),
                    "source": claim.source.url,
                }
            )
        people.extend(
            [
                {"role": "juridico_interno", "objective": "validar fatos, governanca e tributarista"},
                {"role": "tributarista", "objective": "definir objetivo processual e requisitos"},
                {"role": "cfo_tesouraria", "objective": "avaliar caixa, limite e contragarantia"},
            ]
        )
        return people

    @staticmethod
    def _contacts(claims: list[Claim]) -> dict[str, list[dict[str, Any]]]:
        contacts: dict[str, list[dict[str, Any]]] = {
            "emails": [],
            "phones": [],
            "linkedin": [],
            "forms": [],
        }
        mapping = {
            "organization.contact.email": "emails",
            "organization.contact.phone": "phones",
            "organization.linkedin": "linkedin",
            "person.linkedin": "linkedin",
            "organization.contact.form": "forms",
        }
        seen = set()
        for claim in sorted(claims, key=lambda item: (-item.confidence, item.source.url)):
            bucket = mapping.get(claim.predicate)
            if not bucket:
                continue
            raw = claim.value.get("value") if isinstance(claim.value, dict) else claim.value
            key = (bucket, str(raw).casefold())
            if key in seen:
                continue
            seen.add(key)
            contacts[bucket].append(
                {
                    "value": raw,
                    "status": claim.status.value,
                    "confidence": round(claim.confidence, 2),
                    "source": claim.source.url,
                    "kind": claim.value.get("kind") if isinstance(claim.value, dict) else None,
                }
            )
        return contacts

    @staticmethod
    def _intelligence_blocks(
        *,
        organization: dict[str, Any],
        identity: dict[str, Any],
        claims: list[Claim],
        contacts: dict[str, list[dict[str, Any]]],
        interlocutors: list[dict[str, Any]],
        gaps: list[str],
        market: dict[str, Any],
        decision: dict[str, Any],
        approach: dict[str, Any],
    ) -> dict[str, Any]:
        def evidence(*prefixes: str) -> list[dict[str, Any]]:
            selected = [
                claim
                for claim in claims
                if any(claim.predicate == prefix or claim.predicate.startswith(prefix) for prefix in prefixes)
            ]
            return [_claim_evidence(claim) for claim in _rank_claims(selected)]

        group_evidence = evidence("organization.corporate_structure_signal")
        financial_evidence = evidence("financial.", "organization.capital_")
        tax_evidence = evidence("tax.")
        judicial_evidence = evidence("process.number", "process.event", "process.role", "process.value")
        guarantee_evidence = evidence("process.guarantee_status")
        recent_claims = [
            claim
            for claim in claims
            if claim.observed_event_at or claim.source.published_at
        ]
        recent_evidence = [_claim_evidence(claim) for claim in _rank_recent_claims(recent_claims)]
        public_people = [item for item in interlocutors if item.get("name")]

        return {
            "01_empresa": {
                "title": "Empresa",
                "status": identity.get("status", "UNRESOLVED"),
                "data": organization,
                "evidence": evidence("organization.cnpj", "organization.legal_name"),
            },
            "02_grupo_economico": {
                "title": "Grupo Econômico",
                "status": _block_status(group_evidence),
                "evidence": group_evidence,
            },
            "03_decisores": {
                "title": "Decisores",
                "status": "LOCALIZADO_COM_VALIDACAO_PENDENTE" if public_people else "LACUNA",
                "data": public_people,
                "rule": "Cargo público indica interlocutor potencial; poder decisório não é presumido.",
            },
            "04_contatos_publicos": {
                "title": "Contatos Públicos",
                "status": "LOCALIZADO_COM_VALIDACAO_PENDENTE" if any(contacts.values()) else "LACUNA",
                "data": contacts,
            },
            "05_estrutura_financeira": {
                "title": "Estrutura Financeira",
                "status": _block_status(financial_evidence),
                "evidence": financial_evidence,
            },
            "06_sinais_tributarios": {
                "title": "Sinais Tributários",
                "status": _block_status(tax_evidence),
                "evidence": tax_evidence,
            },
            "07_sinais_judiciais": {
                "title": "Sinais Judiciais",
                "status": _block_status(judicial_evidence),
                "evidence": judicial_evidence,
            },
            "08_garantias_identificadas": {
                "title": "Garantias Identificadas",
                "status": _block_status(guarantee_evidence),
                "evidence": guarantee_evidence,
                "rule": "Menção sem processo e fonte oficial permanece hipótese.",
            },
            "09_eventos_recentes": {
                "title": "Eventos Recentes",
                "status": _block_status(recent_evidence),
                "evidence": recent_evidence,
            },
            "10_hipoteses_securitarias": {
                "title": "Hipóteses Securitárias",
                "status": "HIPOTESE_CONDICIONADA",
                "data": {
                    "hypothesis": market.get("hypothesis"),
                    "validation": market.get("validation"),
                    "insurer_direction": market.get("insurer_direction"),
                },
                "supporting_evidence": guarantee_evidence + judicial_evidence[:4],
            },
            "11_lacunas": {
                "title": "Lacunas",
                "status": "LACUNA" if gaps else "SEM_LACUNAS_BLOQUEADORAS",
                "data": gaps,
            },
            "12_proximo_movimento_comercial": {
                "title": "Próximo Movimento Comercial",
                "status": approach.get("status"),
                "data": {
                    "classification": decision.get("classification"),
                    "next_action": decision.get("next_action"),
                    "sendable": decision.get("sendable"),
                    "persona": approach.get("persona"),
                },
            },
        }

    @staticmethod
    def _next_action(decision: str, gaps: list[str]) -> str:
        if decision == "AVANCAR":
            return "Revisao humana do dossie e reuniao conjunta com juridico e financeiro."
        if decision == "NAO_PROSSEGUIR":
            return "Registrar justificativa e encerrar a oportunidade."
        return "Coletar fontes oficiais para: " + ", ".join(gaps)


def _best_claims(claims: list[Claim]) -> dict[str, Claim]:
    rank = {
        ClaimStatus.CONFIRMED: 4,
        ClaimStatus.CORROBORATED: 3,
        ClaimStatus.HYPOTHESIS: 2,
        ClaimStatus.GAP: 1,
        ClaimStatus.CONFLICT: 0,
    }
    best: dict[str, Claim] = {}
    for claim in claims:
        current = best.get(claim.predicate)
        if current is None or (rank[claim.status], claim.confidence) > (
            rank[current.status],
            current.confidence,
        ):
            best[claim.predicate] = claim
    return best


def _deduplicate_claims(claims: list[Claim], *, preserve_layer: bool) -> list[Claim]:
    status_rank = {
        ClaimStatus.CONFIRMED: 4,
        ClaimStatus.CORROBORATED: 3,
        ClaimStatus.HYPOTHESIS: 2,
        ClaimStatus.GAP: 1,
        ClaimStatus.CONFLICT: 0,
    }
    unique: dict[tuple, Claim] = {}
    for claim in claims:
        layer = (
            claim.source.evidence_layer.value
            if preserve_layer and claim.source.evidence_layer
            else ""
        )
        key = (claim.predicate, str(claim.value), claim.source.url, layer)
        current = unique.get(key)
        candidate_rank = (status_rank[claim.status], claim.confidence, len(claim.excerpt))
        current_rank = (
            status_rank[current.status],
            current.confidence,
            len(current.excerpt),
        ) if current else (-1, -1.0, -1)
        if candidate_rank > current_rank:
            unique[key] = claim
    return list(unique.values())


def _claim_value(claim: Claim | None, default=None):
    return claim.value if claim is not None else default


def _claim_evidence(claim: Claim) -> dict[str, Any]:
    event_at = claim.observed_event_at or claim.source.published_at
    return {
        "predicate": claim.predicate,
        "value": claim.value,
        "classification": claim.status.value,
        "confidence": round(claim.confidence, 2),
        "source_type": claim.source.source_class.value,
        "source": claim.source.title,
        "url": claim.source.url,
        "date": event_at.isoformat() if event_at else None,
        "captured_at": claim.source.captured_at.isoformat(),
        "original_excerpt": claim.excerpt,
    }


def _rank_claims(claims: list[Claim], limit: int = 20) -> list[Claim]:
    rank = {
        ClaimStatus.CONFIRMED: 4,
        ClaimStatus.CORROBORATED: 3,
        ClaimStatus.HYPOTHESIS: 2,
        ClaimStatus.CONFLICT: 1,
        ClaimStatus.GAP: 0,
    }
    ordered = sorted(
        claims,
        key=lambda claim: (rank[claim.status], claim.confidence, len(claim.excerpt)),
        reverse=True,
    )
    unique: dict[tuple[str, str, str], Claim] = {}
    for claim in ordered:
        unique.setdefault((claim.predicate, str(claim.value), claim.source.url), claim)
    return list(unique.values())[:limit]


def _rank_recent_claims(claims: list[Claim], limit: int = 20) -> list[Claim]:
    floor = datetime.min.replace(tzinfo=UTC)
    return sorted(
        _rank_claims(claims, limit=100),
        key=lambda claim: claim.observed_event_at or claim.source.published_at or floor,
        reverse=True,
    )[:limit]


def _block_status(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "LACUNA"
    classifications = {item.get("classification") for item in evidence}
    if "confirmed" in classifications:
        return "CONFIRMADO"
    if "corroborated" in classifications:
        return "CORROBORADO"
    if "conflict" in classifications:
        return "CONFLITO"
    return "HIPOTESE"


def dossier_to_markdown(dossier: Dossier) -> str:
    org = dossier.organization
    executive = dossier.executive_reading
    lines = [
        "# DOSSIÊ DE OPORTUNIDADE FISCAL",
        "",
        f"**Empresa-alvo:** {org['legal_name']}",
        f"**CNPJ:** {org['cnpj']}",
        f"**Gerado em:** {dossier.generated_at.isoformat()}",
        "",
        "## 1. Leitura executiva",
        "",
        f"- Score de evidência: **{executive['evidence_score']}/100**",
        f"- Tese: {executive['thesis']}",
        f"- Validação de mercado: {executive['market_validation']}",
        f"- Seguradora: {executive['insurer_direction']}",
        "",
        "## Produto final em 12 blocos",
        "",
    ]
    for block in dossier.intelligence_blocks.values():
        lines.append(f"### {block['title']}")
        lines.append("")
        lines.append(f"- Status: **{block.get('status', 'LACUNA')}**")
        data = block.get("data")
        if data:
            lines.append(f"- Dados: {data}")
        for evidence in block.get("evidence", [])[:5]:
            lines.append(
                f"- {evidence['classification'].upper()} `{evidence['predicate']}` = "
                f"{evidence['value']} (confiança {evidence['confidence']:.2f}; "
                f"fonte: {evidence['url']})"
            )
            if evidence.get("original_excerpt"):
                lines.append(f"  - Trecho: {' '.join(evidence['original_excerpt'].split())[:220]}")
        lines.append("")
    lines.extend(["## Evidências consolidadas", ""])
    grouped: dict[tuple, dict[str, Any]] = {}
    for claim in dossier.claims:
        key = (
            claim.status.value,
            claim.predicate,
            str(claim.value),
            claim.source.url,
            round(claim.confidence, 2),
        )
        if key not in grouped:
            grouped[key] = {"claim": claim, "count": 0}
        grouped[key]["count"] += 1
    for item in grouped.values():
        claim = item["claim"]
        count = item["count"]
        suffix = f"; ocorrências: {count}" if count > 1 else ""
        lines.append(
            f"- **{claim.status.value.upper()}** `{claim.predicate}` = {claim.value} "
            f"(confiança {claim.confidence:.2f}; fonte: {claim.source.url}{suffix})"
        )
        excerpt = " ".join(claim.excerpt.split())[:220]
        if excerpt:
            lines.append(f"  - Trecho: {excerpt}")
    lines.extend(["", "## 3. Contatos públicos e interlocutores", ""])
    for bucket, label in (("emails", "E-mail"), ("phones", "Telefone"), ("linkedin", "LinkedIn")):
        for contact in dossier.contacts.get(bucket, []):
            lines.append(
                f"- **{label}:** {contact['value']} "
                f"({contact['status']}; fonte: {contact['source']})"
            )
    if not any(dossier.contacts.values()):
        lines.append("- NAO LOCALIZADO")
    for person in dossier.interlocutors:
        if not person.get("name"):
            continue
        lines.append(
            f"- **Interlocutor potencial:** {person['name']} — {person.get('public_role', '')} "
            f"({person.get('evidence_status', '')}; fonte: {person.get('source', '')})"
        )
    lines.extend(["", "## 4. Lacunas", ""])
    lines.extend(f"- {gap}" for gap in dossier.gaps)
    lines.extend(
        [
            "",
            "## 5. Abordagem",
            "",
            f"**Status:** {dossier.approach['status']}",
            "",
            dossier.approach["message"],
            "",
            "## 6. Decisão",
            "",
            f"- Classificação: **{dossier.decision['classification']}**",
            f"- Próxima ação: {dossier.decision['next_action']}",
            f"- Enviável: {'SIM' if dossier.decision['sendable'] else 'NÃO'}",
            "",
            "## 7. Termos protegidos",
            "",
        ]
    )
    lines.extend(f"- {term}" for term in dossier.approach["protected_terms"])
    return "\n".join(lines) + "\n"
