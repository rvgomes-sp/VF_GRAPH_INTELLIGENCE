from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from urllib.parse import urlparse

from .models import (
    SOURCE_PRIORS,
    Claim,
    ClaimStatus,
    OrganizationSeed,
    PublicDocument,
    SourceClass,
    SourceRecord,
)
from .policy import can_promote_to_confirmed


CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
PROCESS_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
CURRENCY_RE = re.compile(r"R\$\s*[\d.]+(?:,\d{2})?", re.IGNORECASE)
DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?55[\s.-]?)?(?:\(?\d{2}\)?[\s.-]?)?(?:9\d{4}|[2-8]\d{3})[\s.-]?\d{4}(?!\d)"
)
LEGAL_NAME_RE = re.compile(
    r"([A-ZÀ-ÖØ-Ý][A-ZÀ-ÖØ-Ý0-9&.' -]{4,100}?\s(?:LTDA|S/?A|SA|EIRELI|S\.A\.))",
    re.I,
)
ROLE_TERMS = (
    r"CEO|CFO|COO|presidente|vice-presidente|diretor(?:a)?(?:\s+(?:financeir[oa]|jur[ií]dic[oa]|"
    r"comercial|administrativ[oa]|executiv[oa]))?|s[oó]ci[oa](?:-administrador(?:a)?)?|"
    r"gerente\s+(?:jur[ií]dic[oa]|tribut[aá]ri[oa]|financeir[oa])|"
    r"head\s+(?:jur[ií]dic[oa]|financeir[oa]|tribut[aá]ri[oa]|compliance)|"
    r"controller|tesoureir[oa]|compliance officer|procurador(?:a)?"
)
PERSON_BEFORE_ROLE_RE = re.compile(
    rf"(?P<name>[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ.'-]+(?:\s+[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ.'-]+){{1,5}})"
    rf"\s*(?:[-–|,]\s*)+(?P<role>{ROLE_TERMS})\b",
    re.I,
)
ROLE_BEFORE_PERSON_RE = re.compile(
    rf"(?P<role>{ROLE_TERMS})\s*(?:da|de|do|na|no|at)?\s*(?:empresa)?\s*[:\-–|,]\s*"
    rf"(?P<name>[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ.'-]+(?:\s+[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ.'-]+){{1,5}})",
    re.I,
)


def repair_mojibake(text: str) -> str:
    if any(marker in text for marker in ("Ã", "Â", "â", "�")):
        try:
            repaired = text.encode("cp1252").decode("utf-8")
            if repaired.count("�") <= text.count("�"):
                return repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def source_record(document: PublicDocument, extractor: str) -> SourceRecord:
    return SourceRecord(
        url=document.url,
        title=document.title,
        source_class=document.source_class,
        captured_at=document.captured_at,
        published_at=document.published_at,
        content_hash=document.content_hash,
        http_status=document.http_status,
        extractor=extractor,
        evidence_layer=document.evidence_layer,
        search_objective=document.search_objective,
        search_score=document.search_score,
    )


def make_claim(
    seed: OrganizationSeed,
    document: PublicDocument,
    predicate: str,
    value,
    excerpt: str,
    extractor: str,
    observed_event_at: datetime | None = None,
    rationale: str = "",
    tags: list[str] | None = None,
    confirmable: bool = True,
) -> Claim:
    source = source_record(document, extractor)
    prior = SOURCE_PRIORS[document.source_class]
    status = (
        ClaimStatus.CONFIRMED
        if confirmable and can_promote_to_confirmed(document.source_class, excerpt)
        else ClaimStatus.HYPOTHESIS
    )
    return Claim(
        subject_id=seed.subject_id,
        predicate=predicate,
        value=value,
        status=status,
        confidence=prior,
        source=source,
        excerpt=excerpt[:700],
        observed_event_at=observed_event_at,
        rationale=rationale,
        tags=tags or [],
    )


class PublicEvidenceExtractor:
    """Extracao conservadora: registra apenas o que aparece em trecho citavel."""

    EVENT_TERMS = {
        "sisbajud": "SISBAJUD",
        "penhora": "PENHORA",
        "execucao fiscal": "EXECUCAO FISCAL",
        "execução fiscal": "EXECUCAO FISCAL",
        "renajud": "RENAJUD",
        "deposito judicial": "DEPOSITO JUDICIAL",
        "depósito judicial": "DEPOSITO JUDICIAL",
        "bloqueio judicial": "BLOQUEIO JUDICIAL",
        "bloqueio de ativos": "BLOQUEIO DE ATIVOS",
        "arresto": "ARRESTO",
        "carta fiança": "CARTA FIANCA",
        "seguro garantia judicial": "SEGURO GARANTIA JUDICIAL",
        "substituição de garantia": "SUBSTITUICAO DE GARANTIA",
        "substituicao de garantia": "SUBSTITUICAO DE GARANTIA",
    }

    CORPORATE_STRUCTURE_TERMS = {
        "grupo econômico": "GRUPO_ECONOMICO",
        "grupo economico": "GRUPO_ECONOMICO",
        "controladora": "CONTROLADORA",
        "controlada": "CONTROLADA",
        "subsidiária": "SUBSIDIARIA",
        "subsidiaria": "SUBSIDIARIA",
        "coligada": "COLIGADA",
        "filial": "FILIAL",
        "unidade operacional": "UNIDADE_OPERACIONAL",
    }
    TAX_SIGNAL_TERMS = {
        "crédito tributário": "CREDITO_TRIBUTARIO",
        "credito tributario": "CREDITO_TRIBUTARIO",
        "auto de infração": "AUTO_DE_INFRACAO",
        "auto de infracao": "AUTO_DE_INFRACAO",
        "passivo fiscal": "PASSIVO_FISCAL",
        "compensação tributária": "COMPENSACAO_TRIBUTARIA",
        "compensacao tributaria": "COMPENSACAO_TRIBUTARIA",
        "carf": "CARF",
        "pgfn": "PGFN",
        "icms": "ICMS",
        "cofins": "COFINS",
        "pis": "PIS",
        "ipi": "IPI",
        "iss": "ISS",
    }
    FINANCIAL_SIGNAL_TERMS = {
        "contingências tributárias": "CONTINGENCIAS_TRIBUTARIAS",
        "contingencias tributarias": "CONTINGENCIAS_TRIBUTARIAS",
        "provisões judiciais": "PROVISOES_JUDICIAIS",
        "provisoes judiciais": "PROVISOES_JUDICIAIS",
        "passivos fiscais": "PASSIVOS_FISCAIS",
        "depósitos judiciais": "DEPOSITOS_JUDICIAIS",
        "depositos judiciais": "DEPOSITOS_JUDICIAIS",
        "demonstrações financeiras": "DEMONSTRACOES_FINANCEIRAS",
        "demonstracoes financeiras": "DEMONSTRACOES_FINANCEIRAS",
        "balanço patrimonial": "BALANCO_PATRIMONIAL",
        "balanco patrimonial": "BALANCO_PATRIMONIAL",
        "notas explicativas": "NOTAS_EXPLICATIVAS",
        "parecer dos auditores": "PARECER_DOS_AUDITORES",
    }
    GUARANTEE_TERMS = {
        "seguro garantia judicial": "SEGURO_GARANTIA_JUDICIAL",
        "garantia judicial": "GARANTIA_JUDICIAL",
        "carta fiança": "CARTA_FIANCA_BANCARIA",
        "carta fianca": "CARTA_FIANCA_BANCARIA",
        "fiança bancária": "FIANCA_BANCARIA",
        "fianca bancaria": "FIANCA_BANCARIA",
        "depósito judicial": "DEPOSITO_JUDICIAL",
        "deposito judicial": "DEPOSITO_JUDICIAL",
        "substituição de garantia": "SUBSTITUICAO_DE_GARANTIA",
        "substituicao de garantia": "SUBSTITUICAO_DE_GARANTIA",
    }

    def extract(self, seed: OrganizationSeed, document: PublicDocument) -> list[Claim]:
        text = repair_mojibake(document.text)
        lowered = text.casefold()
        claims: list[Claim] = []
        for match in CNPJ_RE.finditer(text):
            digits = "".join(character for character in match.group(0) if character.isdigit())
            if digits == seed.cnpj:
                claims.append(
                    make_claim(
                        seed,
                        document,
                        "organization.cnpj",
                        digits,
                        _window(text, match.start()),
                        "regex_cnpj_v1",
                    )
                )
                # Captura o nome vizinho ao CNPJ para testar coerência com o nome informado.
                legal_name = _legal_name_near(text, match.start())
                if legal_name:
                    claims.append(
                        make_claim(
                            seed,
                            document,
                            "organization.legal_name",
                            legal_name,
                            _window(text, match.start()),
                            "cnpj_context_legal_name_v1",
                            rationale="Razão social extraída do contexto público do CNPJ.",
                        )
                    )
                break
        legal_index = lowered.find(seed.legal_name.casefold())
        if legal_index >= 0:
            claims.append(
                make_claim(
                    seed,
                    document,
                    "organization.legal_name",
                    seed.legal_name,
                    _window(text, legal_index),
                    "exact_legal_name_v1",
                )
            )
        for match in PROCESS_RE.finditer(text):
            excerpt = _window(text, match.start(), 380)
            if not _context_relevant(seed, excerpt):
                continue
            claims.append(
                make_claim(
                    seed,
                    document,
                    "process.number",
                    match.group(0),
                    excerpt,
                    "regex_cnj_process_v1",
                    tags=["judicial"],
                )
            )
            excerpt_fold = excerpt.casefold()
            for term, normalized in self.EVENT_TERMS.items():
                if term in excerpt_fold:
                    claims.append(
                        make_claim(
                            seed,
                            document,
                            "process.event",
                            normalized,
                            excerpt,
                            "process_event_context_v1",
                            observed_event_at=_nearest_date(excerpt),
                            tags=[match.group(0), "judicial"],
                        )
                    )
            if any(role in excerpt_fold for role in ("executada", "polo passivo", "réu", "reu")):
                claims.append(
                    make_claim(
                        seed,
                        document,
                        "process.role",
                        "POLO_PASSIVO",
                        excerpt,
                        "process_role_context_v1",
                        tags=[match.group(0), "judicial"],
                    )
                )
            currency = CURRENCY_RE.search(excerpt)
            if currency:
                claims.append(
                    make_claim(
                        seed,
                        document,
                        "process.value",
                        currency.group(0),
                        excerpt,
                        "process_value_context_v1",
                        tags=[match.group(0), "judicial"],
                    )
                )
        claims.extend(self._extract_structured_signals(seed, document, text))
        claims.extend(self._extract_contacts_and_people(seed, document, text))
        return claims

    @staticmethod
    def anchor_known_people(
        seed: OrganizationSeed,
        document: PublicDocument,
        known_people: list[dict] | None,
    ) -> list[Claim]:
        """Âncora por entidade conhecida (custo zero, sem LLM).

        As pessoas já são conhecidas do banco (QSA da Receita + advogados do
        processo). A varredura pública não precisa *descobrir* nomes: precisa
        *confirmar* que eles aparecem em documento público e ancorar a evidência.
        Quando o nome conhecido é localizado no texto, emitimos o mesmo claim
        `person.professional_role` do caminho por regex — gerando nó PESSOA,
        aresta COMPANY-[HAS]->PERSON e evidência com o score da fonte. O poder
        decisório continua NÃO CONFIRMADO; é interlocutor até validação humana.
        """
        if not known_people:
            return []
        text = repair_mojibake(document.text)
        folded_text = _fold(text)
        claims: list[Claim] = []
        seen: set[str] = set()
        for person in known_people:
            nome = " ".join(str(person.get("nome") or "").split())
            if not _valid_person_name(nome):
                continue
            key = nome.casefold()
            if key in seen:
                continue
            needle = _fold(nome)
            position = folded_text.find(needle)
            if position < 0:
                continue
            seen.add(key)
            cargo = str(person.get("cargo") or "").strip() or "VINCULO SOCIETARIO/PROCESSUAL"
            nome_titulo = nome.title()
            value = {
                "name": nome_titulo,
                "role": cargo.upper(),
                "decision_maker": "POTENCIAL",
            }
            claims.append(
                make_claim(
                    seed,
                    document,
                    "person.professional_role",
                    value,
                    _window(text, position, 320),
                    "known_entity_anchor_v1",
                    rationale=(
                        "Pessoa já conhecida (QSA/processo) localizada em documento público; "
                        "confirma presença e vincula evidência, sem presumir poder decisório."
                    ),
                    tags=["known_entity", "potential_decision_maker"],
                    confirmable=False,
                )
            )
            # Contato no bloco de assinatura/procuração ao redor do nome:
            # e-mail/telefone que aparecem colados ao advogado/sócio conhecido.
            claims.extend(
                PublicEvidenceExtractor._contacts_near_name(
                    seed, document, text, position, len(needle), nome_titulo
                )
            )
        return claims

    @staticmethod
    def _contacts_near_name(
        seed: OrganizationSeed,
        document: PublicDocument,
        text: str,
        position: int,
        name_length: int,
        person_name: str,
    ) -> list[Claim]:
        """E-mail/telefone na vizinhança do nome (assinatura/procuração) -> pessoa."""
        start = max(0, position - 160)
        end = min(len(text), position + name_length + 320)
        block = text[start:end]
        claims: list[Claim] = []
        seen_email: set[str] = set()
        for match in EMAIL_RE.finditer(block):
            email = match.group(1).strip(".,;:").lower()
            if _email_is_noise(email) or email in seen_email:
                continue
            seen_email.add(email)
            claims.append(
                make_claim(
                    seed,
                    document,
                    "person.contact.email",
                    {"value": email, "person": person_name},
                    _window(block, match.start(), 200),
                    "known_entity_contact_v1",
                    rationale=(
                        "E-mail publicado junto ao nome conhecido (assinatura/procuração); "
                        "vínculo por proximidade, requer validação humana."
                    ),
                    tags=["known_entity_contact"],
                    confirmable=False,
                )
            )
        seen_phone: set[str] = set()
        for match in PHONE_RE.finditer(block):
            phone = _normalize_phone(match.group(0))
            digits = "".join(character for character in phone if character.isdigit())
            if len(digits) not in {10, 11, 12, 13} or phone in seen_phone:
                continue
            seen_phone.add(phone)
            claims.append(
                make_claim(
                    seed,
                    document,
                    "person.contact.phone",
                    {"value": phone, "person": person_name},
                    _window(block, match.start(), 200),
                    "known_entity_contact_v1",
                    rationale=(
                        "Telefone publicado junto ao nome conhecido (assinatura/procuração); "
                        "vínculo por proximidade, requer validação humana."
                    ),
                    tags=["known_entity_contact"],
                    confirmable=False,
                )
            )
        return claims

    @classmethod
    def _extract_structured_signals(
        cls, seed: OrganizationSeed, document: PublicDocument, text: str
    ) -> list[Claim]:
        claims: list[Claim] = []
        groups = (
            ("organization.corporate_structure_signal", cls.CORPORATE_STRUCTURE_TERMS, "corporate_structure_signal_v1"),
            ("tax.signal", cls.TAX_SIGNAL_TERMS, "tax_signal_v1"),
            ("financial.signal", cls.FINANCIAL_SIGNAL_TERMS, "financial_signal_v1"),
        )
        for predicate, terms, extractor in groups:
            seen: set[str] = set()
            for term, normalized in terms.items():
                if normalized in seen:
                    continue
                index = text.casefold().find(term)
                if index < 0:
                    continue
                excerpt = _window(text, index, 520)
                if not _context_relevant(seed, excerpt):
                    continue
                seen.add(normalized)
                claims.append(
                    make_claim(
                        seed,
                        document,
                        predicate,
                        {"signal": normalized, "term": term},
                        excerpt,
                        extractor,
                        observed_event_at=_nearest_date(excerpt),
                        rationale="Sinal textual atribuído à empresa; requer validação no documento primário e no contexto jurídico.",
                        tags=["signal_only"],
                        confirmable=False,
                    )
                )

        seen_guarantees: set[str] = set()
        for term, normalized in cls.GUARANTEE_TERMS.items():
            if normalized in seen_guarantees:
                continue
            index = text.casefold().find(term)
            if index < 0:
                continue
            excerpt = _window(text, index, 520)
            if not _context_relevant(seed, excerpt):
                continue
            seen_guarantees.add(normalized)
            process_match = PROCESS_RE.search(excerpt)
            tags = ["judicial_guarantee"]
            if process_match:
                tags.append(process_match.group(0))
            else:
                tags.append("signal_only")
            claims.append(
                make_claim(
                    seed,
                    document,
                    "process.guarantee_status",
                    {
                        "type": normalized,
                        "process": process_match.group(0) if process_match else None,
                        "finding": "MENCAO_PUBLICA",
                    },
                    excerpt,
                    "judicial_guarantee_context_v1",
                    observed_event_at=_nearest_date(excerpt),
                    rationale=(
                        "Menção pública de garantia. Só representa garantia identificada para o caso "
                        "quando vinculada a processo e confirmada em fonte oficial."
                    ),
                    tags=tags,
                    confirmable=bool(process_match),
                )
            )
        return claims

    @staticmethod
    def _extract_contacts_and_people(
        seed: OrganizationSeed, document: PublicDocument, text: str
    ) -> list[Claim]:
        claims: list[Claim] = []
        seen_emails: set[str] = set()
        for match in EMAIL_RE.finditer(text):
            email = match.group(1).strip(".,;:").lower()
            if _email_is_noise(email) or email in seen_emails:
                continue
            context = _window(text, match.start(), 520)
            if not _contact_context_allowed(seed, document, text, context):
                continue
            seen_emails.add(email)
            claims.append(
                make_claim(
                    seed,
                    document,
                    "organization.contact.email",
                    {"value": email, "kind": _email_kind(email), "public_professional": True},
                    context,
                    "public_business_email_v1",
                    rationale="Contato publicado em contexto empresarial; requer revisão antes do uso.",
                    tags=["public_business_contact"],
                    confirmable=False,
                )
            )
        seen_phones: set[str] = set()
        for match in PHONE_RE.finditer(text):
            phone = _normalize_phone(match.group(0))
            if (
                len("".join(character for character in phone if character.isdigit()))
                not in {10, 11, 12, 13}
                or phone in seen_phones
            ):
                continue
            context = _window(text, match.start(), 520)
            if not _contact_context_allowed(seed, document, text, context):
                continue
            seen_phones.add(phone)
            claims.append(
                make_claim(
                    seed,
                    document,
                    "organization.contact.phone",
                    {"value": phone, "public_professional": True},
                    context,
                    "public_business_phone_v1",
                    rationale="Telefone publicado em contexto empresarial; requer revisão antes do uso.",
                    tags=["public_business_contact"],
                    confirmable=False,
                )
            )

        linkedin_urls = []
        if "linkedin.com/" in document.url.casefold():
            linkedin_urls.append(document.url)
        linkedin_urls.extend(link for link in document.links if "linkedin.com/" in link.casefold())
        for url in dict.fromkeys(linkedin_urls):
            folded_url = url.casefold()
            if "/in/" not in folded_url and "/company/" not in folded_url:
                continue
            if not _contact_context_allowed(seed, document, text, _window(text, 0, 520)):
                continue
            predicate = "person.linkedin" if "/in/" in folded_url else "organization.linkedin"
            claims.append(
                make_claim(
                    seed,
                    document,
                    predicate,
                    url,
                    _window(text, 0, 260),
                    "public_linkedin_reference_v1",
                    rationale="URL pública ou indexada; o perfil não é tratado como validação autônoma do vínculo.",
                    tags=["indexed_professional_profile"],
                    confirmable=False,
                )
            )

        for url in dict.fromkeys(document.links):
            folded_url = url.casefold()
            if not any(token in folded_url for token in ("contato", "fale-conosco", "fale_conosco", "/ri/")):
                continue
            if not _contact_context_allowed(seed, document, text, _window(text, 0, 520)):
                continue
            claims.append(
                make_claim(
                    seed,
                    document,
                    "organization.contact.form",
                    {"value": url, "public_professional": True},
                    _window(text, 0, 520),
                    "public_contact_form_v1",
                    rationale="Canal institucional público; requer revisão humana antes do contato.",
                    tags=["public_business_contact"],
                    confirmable=False,
                )
            )

        seen_people = set()
        for pattern in (PERSON_BEFORE_ROLE_RE, ROLE_BEFORE_PERSON_RE):
            for match in pattern.finditer(text):
                name = " ".join(match.group("name").split()).title()
                role = " ".join(match.group("role").split()).upper()
                if not _valid_person_name(name):
                    continue
                context = _window(text, match.start(), 520)
                if not _contact_context_allowed(seed, document, text, context):
                    continue
                key = (name.casefold(), role.casefold())
                if key in seen_people:
                    continue
                seen_people.add(key)
                linkedin = next((url for url in linkedin_urls if "/in/" in url.casefold()), None)
                value = {"name": name, "role": role, "decision_maker": "POTENCIAL"}
                if linkedin:
                    value["linkedin"] = linkedin
                claims.append(
                    make_claim(
                        seed,
                        document,
                        "person.professional_role",
                        value,
                        context,
                        "public_professional_role_v2",
                        rationale=(
                            "Papel profissional público; indica interlocutor potencial, não poder decisório confirmado."
                        ),
                        tags=["professional_public_only", "potential_decision_maker"],
                        confirmable=False,
                    )
                )
        return claims


class LegacyCRMExtractor:
    """Converte o painel legado em sinais, nunca em fatos confirmados."""

    def extract(self, seed: OrganizationSeed, text: str, source_url: str) -> list[Claim]:
        clean = repair_mojibake(text)
        document = PublicDocument(
            url=source_url,
            title="Painel CRM fornecido pelo usuario",
            text=clean,
            source_class=SourceClass.LEGACY_CRM,
            content_type="text/plain",
        )
        claims: list[Claim] = []
        base = [
            ("organization.legal_name", seed.legal_name, seed.legal_name),
            ("organization.cnpj", seed.cnpj, seed.cnpj),
        ]
        for predicate, value, needle in base:
            index = clean.casefold().find(needle.casefold())
            excerpt = _window(clean, index if index >= 0 else 0)
            claims.append(make_claim(seed, document, predicate, value, excerpt, "legacy_seed_v1"))

        capital_match = re.search(r"Capital(?: Social)?\s*R\$\s*([\d.,]+\s*[MK]?)", clean, re.I)
        if capital_match:
            claims.append(
                make_claim(
                    seed,
                    document,
                    "organization.capital_social_reported",
                    capital_match.group(1).strip(),
                    _window(clean, capital_match.start()),
                    "legacy_capital_v1",
                )
            )
        status_match = re.search(r"Situa[cç][aã]o CNPJ\s+([A-ZÁÉÍÓÚÇ]+)", clean, re.I)
        if status_match:
            claims.append(
                make_claim(
                    seed,
                    document,
                    "organization.registration_status_reported",
                    status_match.group(1).upper(),
                    _window(clean, status_match.start()),
                    "legacy_status_v1",
                )
            )
        lines = [line.strip() for line in clean.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            process_match = PROCESS_RE.search(line)
            if not process_match:
                continue
            block_start = max(0, index - 6)
            for previous in range(index - 1, block_start - 1, -1):
                if PROCESS_RE.search(lines[previous]):
                    block_start = previous + 1
                    break
            block_lines = lines[block_start : index + 1]
            excerpt = " | ".join(block_lines)
            process = process_match.group(0)
            claims.append(
                make_claim(seed, document, "process.number", process, excerpt, "legacy_process_v1")
            )
            folded = excerpt.casefold()
            for term, normalized in PublicEvidenceExtractor.EVENT_TERMS.items():
                if term in folded:
                    claims.append(
                        make_claim(
                            seed,
                            document,
                            "process.event",
                            normalized,
                            excerpt,
                            "legacy_event_v1",
                            observed_event_at=_nearest_date(excerpt),
                            tags=[process, "legacy"],
                        )
                    )
            if "polo passivo" in folded or "executada" in folded:
                claims.append(
                    make_claim(
                        seed,
                        document,
                        "process.role",
                        "POLO_PASSIVO",
                        excerpt,
                        "legacy_role_v1",
                        tags=[process, "legacy"],
                    )
                )
        for index, line in enumerate(lines):
            if "sócio-administrador" in line.casefold() or "socio-administrador" in line.casefold():
                parts = re.split(r"\s+[·-]\s+", line, maxsplit=1)
                name = parts[0].strip() if len(parts) > 1 else (lines[index - 1] if index else "")
                if name and len(name.split()) >= 2:
                    claims.append(
                        make_claim(
                            seed,
                            document,
                            "person.professional_role",
                            {"name": name.title(), "role": "SOCIO_ADMINISTRADOR"},
                            f"{name} | {line}",
                            "legacy_professional_role_v1",
                            tags=["professional_public_only"],
                        )
                    )
        insurer = re.search(r"Direcionamento de seguradora\s+([A-Z][A-Z0-9 &.-]{2,30})", clean, re.I)
        if insurer:
            claims.append(
                make_claim(
                    seed,
                    document,
                    "market.legacy_insurer_direction",
                    insurer.group(1).strip().upper(),
                    insurer.group(0),
                    "legacy_market_direction_v1",
                    rationale="Sinal historico; exige apetite vigente e analise de subscricao.",
                )
            )
        return claims


class EvidenceReconciler:
    SINGLE_VALUE_PREDICATES = {
        "organization.legal_name",
        "organization.cnpj",
        "organization.registration_status",
        "organization.capital_social",
    }
    HEURISTIC_PREDICATES = {
        "organization.contact.email",
        "organization.contact.phone",
        "organization.linkedin",
        "person.linkedin",
        "person.professional_role",
        "organization.contact.form",
        "organization.corporate_structure_signal",
        "tax.signal",
        "financial.signal",
    }

    def reconcile(self, claims: list[Claim]) -> list[Claim]:
        grouped: dict[tuple[str, str], list[Claim]] = defaultdict(list)
        for claim in claims:
            grouped[(claim.predicate, _canonical_value(claim.value))].append(claim)

        result: list[Claim] = []
        for group in grouped.values():
            hosts = {
                urlparse(claim.source.url).hostname or claim.source.url
                for claim in group
                if claim.source.source_class
                not in {SourceClass.LEGACY_CRM, SourceClass.USER_SUPPLIED}
            }
            has_official = any(
                claim.source.source_class
                in {SourceClass.OFFICIAL_COURT, SourceClass.OFFICIAL_REGISTRY, SourceClass.OFFICIAL_GOVERNMENT}
                for claim in group
            )
            for claim in group:
                if (
                    has_official
                    and claim.excerpt
                    and claim.predicate not in self.HEURISTIC_PREDICATES
                    and "signal_only" not in claim.tags
                ):
                    claim = claim.model_copy(update={"status": ClaimStatus.CONFIRMED})
                elif len(hosts) >= 2:
                    confidence = min(0.90, max(item.confidence for item in group) + 0.12)
                    claim = claim.model_copy(
                        update={"status": ClaimStatus.CORROBORATED, "confidence": confidence}
                    )
                else:
                    claim = claim.model_copy(
                        update={
                            "status": ClaimStatus.HYPOTHESIS,
                            "confidence": SOURCE_PRIORS[claim.source.source_class],
                        }
                    )
                result.append(claim)

        by_predicate: dict[str, set[str]] = defaultdict(set)
        for claim in result:
            if claim.predicate in self.SINGLE_VALUE_PREDICATES:
                by_predicate[claim.predicate].add(_canonical_value(claim.value))
        conflicts = {predicate for predicate, values in by_predicate.items() if len(values) > 1}
        return [
            claim.model_copy(update={"status": ClaimStatus.CONFLICT})
            if claim.predicate in conflicts
            else claim
            for claim in result
        ]


def _fold(value: str) -> str:
    """Minúsculas sem acento, para casar nomes conhecidos com o texto público."""
    normalized = unicodedata.normalize("NFKD", value)
    stripped = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def _window(text: str, index: int, radius: int = 260) -> str:
    start = max(0, index - radius)
    end = min(len(text), index + radius)
    return " ".join(text[start:end].split())


def _nearest_date(text: str) -> datetime | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d/%m/%Y").replace(tzinfo=UTC)
    except ValueError:
        return None


def _canonical_value(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _legal_name_near(text: str, index: int) -> str | None:
    excerpt = text[max(0, index - 150) : min(len(text), index + 150)]
    candidates = [" ".join(match.group(1).split()) for match in LEGAL_NAME_RE.finditer(excerpt)]
    if not candidates:
        return None
    return min(candidates, key=len).upper()


def _email_is_noise(email: str) -> bool:
    local, _, domain = email.partition("@")
    return (
        not local
        or not domain
        or domain.endswith(("example.com", "sentry.io"))
        or local in {"noreply", "no-reply", "mailer-daemon"}
    )


def _email_kind(email: str) -> str:
    local = email.split("@", 1)[0].casefold()
    generic = {
        "contato",
        "comercial",
        "juridico",
        "financeiro",
        "fiscal",
        "vendas",
        "atendimento",
        "sac",
        "info",
    }
    return "corporate_generic" if local in generic else "named_professional_public"


def _normalize_phone(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if digits.startswith("55") and len(digits) in {12, 13}:
        return f"+{digits}"
    return digits


def _valid_person_name(name: str) -> bool:
    words = name.split()
    if not 2 <= len(words) <= 6:
        return False
    noise = {
        "administrador",
        "administradores",
        "aeroporto",
        "agência",
        "analise",
        "análise",
        "brasilia",
        "continuidade",
        "decisões",
        "desde",
        "diretoria",
        "entrada",
        "empresa",
        "favorecido",
        "federal",
        "feridas",
        "geral",
        "governo",
        "internacional",
        "linkedin",
        "ministério",
        "plano",
        "presidir",
        "qualificação",
        "quadro",
        "quotas",
        "recursos",
        "retificação",
        "secretaria",
        "sociedade",
        "societário",
        "trabalhos",
        "tribunal",
        "valor",
        "vencedor",
        "brasil",
        "infotel",
        "importacao",
        "importação",
        "distribuicao",
        "distribuição",
        "diretoria",
    }
    return not any(word.casefold().strip(".,") in noise for word in words)


def _context_relevant(seed: OrganizationSeed, text: str) -> bool:
    digits = "".join(character for character in text if character.isdigit())
    if seed.cnpj in digits:
        return True
    if seed.legal_name.startswith("CNPJ "):
        return False
    folded = text.casefold()
    if seed.legal_name.casefold() in folded:
        return True
    tokens = _distinctive_name_tokens(seed.legal_name)
    return len(tokens) >= 2 and all(token in folded for token in tokens)


def _contact_context_allowed(
    seed: OrganizationSeed,
    document: PublicDocument,
    full_text: str,
    local_context: str,
) -> bool:
    if _context_relevant(seed, local_context):
        return True
    if document.source_class == SourceClass.COMPANY_OWNED:
        return _context_relevant(seed, full_text)
    if document.source_class == SourceClass.PROFESSIONAL_PUBLIC:
        tokens = _distinctive_name_tokens(seed.legal_name)
        folded = full_text.casefold()
        return bool(tokens) and all(token in folded for token in tokens)
    return False


def _distinctive_name_tokens(legal_name: str) -> list[str]:
    ignored = {
        "ltda", "sa", "eireli", "me", "epp", "de", "da", "do", "das", "dos",
        "empresa", "companhia", "comercio", "comércio", "servicos", "serviços",
        "corretora", "seguros", "importacao", "importação", "distribuicao", "distribuição",
    }
    return [
        token.casefold()
        for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", legal_name)
        if len(token) >= 4 and token.casefold() not in ignored
    ]


def parse_brl(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace("R$", "").replace(".", "").replace(",", ".").strip())
    except InvalidOperation:
        return None
