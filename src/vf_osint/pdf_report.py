from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .models import Claim, ClaimStatus, Dossier


INK = colors.HexColor("#1B1820")
PURPLE = colors.HexColor("#75628A")
CORAL = colors.HexColor("#B56E5A")
LAVENDER = colors.HexColor("#EAE1EF")
WARM_GRAY = colors.HexColor("#F2F0ED")
MUTED = colors.HexColor("#6C6872")
WHITE = colors.white
GREEN = colors.HexColor("#2F7658")
AMBER = colors.HexColor("#A46620")
RED = colors.HexColor("#9A3D3D")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "VFTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=21,
            leading=25,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=5 * mm,
        ),
        "eyebrow": ParagraphStyle(
            "VFEyebrow",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=CORAL,
            spaceAfter=2 * mm,
        ),
        "h1": ParagraphStyle(
            "VFH1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=INK,
            spaceBefore=5 * mm,
            spaceAfter=3 * mm,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "VFH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=PURPLE,
            spaceBefore=3 * mm,
            spaceAfter=2 * mm,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "VFBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=INK,
            spaceAfter=2 * mm,
        ),
        "small": ParagraphStyle(
            "VFSmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.4,
            leading=10,
            textColor=MUTED,
        ),
        "callout": ParagraphStyle(
            "VFCallout",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=14,
            textColor=WHITE,
        ),
        "center": ParagraphStyle(
            "VFCenter",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            textColor=INK,
        ),
    }


class VFReportTemplate(BaseDocTemplate):
    def __init__(self, filename: str, **kwargs):
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=21 * mm,
            bottomMargin=18 * mm,
            title="Dossiê de Oportunidade Fiscal",
            author="Vazquez & Fonseca",
            **kwargs,
        )
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="content",
        )
        self.addPageTemplates(PageTemplate(id="vf", frames=[frame], onPage=self._page))

    @staticmethod
    def _page(canvas, document):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#D9D2C9"))
        canvas.setLineWidth(0.6)
        canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(PURPLE)
        canvas.drawString(18 * mm, height - 10.5 * mm, "V&F  |  INTELIGÊNCIA DE GARANTIAS FISCAIS")
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm, "Relatório confidencial de prospecção consultiva")
        canvas.drawRightString(width - 18 * mm, 9 * mm, f"Página {document.page}")
        canvas.restoreState()


def render_dossier_pdf(dossier: Dossier, output: str | Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    story = []
    organization = dossier.organization
    executive = dossier.executive_reading

    story.append(Paragraph("DOSSIÊ DE OPORTUNIDADE FISCAL", styles["eyebrow"]))
    story.append(Paragraph(escape(str(organization.get("legal_name", "Empresa"))), styles["title"]))
    meta = [
        ["CNPJ", _format_cnpj(str(organization.get("cnpj", ""))), "UF", str(organization.get("state") or "NAO LOCALIZADO")],
        ["Classificação", dossier.decision["classification"], "Enviável", "SIM" if dossier.decision["sendable"] else "NÃO"],
        ["Score de evidência", f"{executive['evidence_score']}/100", "Gerado em", dossier.generated_at.strftime("%d/%m/%Y %H:%M")],
    ]
    story.append(_table(meta, [31 * mm, 58 * mm, 31 * mm, 51 * mm], styles, header_columns={0, 2}))
    story.append(Spacer(1, 4 * mm))
    thesis = (
        f"<font color='#D89580'>TESE DE OPORTUNIDADE</font><br/>"
        f"{escape(_humanize(str(executive['thesis'])))}<br/>"
        f"<font name='Helvetica' size='8'>Validação: {escape(_humanize(str(executive['market_validation'])))}</font>"
    )
    story.append(_callout(thesis, styles))

    story.append(Paragraph("1. Leitura executiva", styles["h1"]))
    counts = Counter(claim.status.value for claim in dossier.claims)
    status_table = [
        ["Confirmados", str(counts[ClaimStatus.CONFIRMED.value]), "Corroborados", str(counts[ClaimStatus.CORROBORATED.value])],
        ["Hipóteses", str(counts[ClaimStatus.HYPOTHESIS.value]), "Conflitos", str(counts[ClaimStatus.CONFLICT.value])],
        ["Fontes", str(dossier.provenance["source_count"]), "Claims", str(dossier.provenance["claim_count"])],
    ]
    story.append(_table(status_table, [37 * mm, 22 * mm, 37 * mm, 22 * mm], styles, header_columns={0, 2}, centered_columns={1, 3}))
    identity = dossier.identity_resolution or {}
    story.append(Paragraph("Validação da identidade", styles["h2"]))
    identity_rows = [
        ["Status", _humanize(str(identity.get("status", "UNRESOLVED")))],
        ["Nome informado", str(identity.get("requested_name") or "NÃO INFORMADO")],
        ["Nome resolvido", str(identity.get("resolved_name") or "NÃO CONFIRMADO")],
        ["Regra", str(identity.get("rationale") or "Validação CNPJ–razão social pendente.")],
    ]
    story.append(_table(identity_rows, [42 * mm, 123 * mm], styles, header_columns={0}))
    story.append(Paragraph("Cobertura por camadas de evidência", styles["h2"]))
    layer_rows = [["Camada", "Cobertura", "Buscas", "Docs", "Fontes", "Achados"]]
    for layer in dossier.evidence_layers.values():
        layer_rows.append(
            [
                layer.get("label", ""),
                layer.get("coverage", "LACUNA"),
                layer.get("queries", 0),
                layer.get("documents", 0),
                len(layer.get("domains", [])),
                layer.get("claims", 0),
            ]
        )
    story.append(
        _table(
            layer_rows,
            [55 * mm, 28 * mm, 19 * mm, 18 * mm, 20 * mm, 25 * mm],
            styles,
            first_row_header=True,
        )
    )
    story.append(Paragraph("Lacunas que controlam o próximo movimento", styles["h2"]))
    gap_rows = [[str(index), _gap_label(gap)] for index, gap in enumerate(dossier.gaps, start=1)]
    story.append(_table(gap_rows or [["-", "Nenhuma lacuna bloqueadora"]], [11 * mm, 154 * mm], styles))

    story.append(Paragraph("Produto final - 12 blocos", styles["h1"]))
    block_rows = [["Bloco", "Status", "Síntese verificável"]]
    for block in dossier.intelligence_blocks.values():
        block_rows.append(
            [
                block.get("title", ""),
                _humanize(str(block.get("status", "LACUNA"))),
                _block_summary(block),
            ]
        )
    story.append(
        _table(
            block_rows,
            [43 * mm, 37 * mm, 85 * mm],
            styles,
            first_row_header=True,
        )
    )

    story.append(Paragraph("2. Empresa e pessoas", styles["h1"]))
    organization_rows = _organization_rows(dossier)
    story.append(_table(organization_rows, [48 * mm, 69 * mm, 48 * mm], styles, first_row_header=True))
    story.append(Paragraph("Contatos públicos", styles["h2"]))
    story.append(
        _table(
            _contact_rows(dossier),
            [28 * mm, 55 * mm, 29 * mm, 53 * mm],
            styles,
            first_row_header=True,
        )
    )
    story.append(Paragraph("Interlocutores", styles["h2"]))
    interlocutor_rows = [["Papel / nome", "Condição", "Objetivo / perfil"]]
    for item in dossier.interlocutors:
        label = item.get("name") or item.get("role") or "NAO LOCALIZADO"
        role = item.get("public_role") or item.get("role") or ""
        condition = item.get("evidence_status") or "papel_a_validar"
        objective = item.get("linkedin") or item.get("commercial_interpretation") or item.get("objective") or ""
        interlocutor_rows.append(
            [f"{label}\n{role}", _humanize(condition), _humanize(objective)]
        )
    story.append(_table(interlocutor_rows, [54 * mm, 38 * mm, 73 * mm], styles, first_row_header=True))

    story.append(Paragraph("3. Sinais processuais", styles["h1"]))
    process_rows = _process_rows(dossier.claims)
    story.append(_table(process_rows, [42 * mm, 28 * mm, 29 * mm, 31 * mm, 35 * mm], styles, first_row_header=True))

    story.append(Paragraph("4. Hipótese securitária", styles["h1"]))
    market_rows = [
        ["Hipótese", _humanize(str(executive["thesis"]))],
        ["Validação indispensável", _humanize(str(executive["market_validation"]))],
        ["Direcionamento de seguradora", _humanize(str(executive["insurer_direction"]))],
    ]
    story.append(_table(market_rows, [49 * mm, 116 * mm], styles, header_columns={0}))

    story.append(Paragraph("5. Abordagem comercial", styles["h1"]))
    approach_status = _humanize(str(dossier.approach["status"]))
    status_color = GREEN if dossier.decision["sendable"] else AMBER
    story.append(
        Table(
            [[Paragraph(f"STATUS: {escape(approach_status)}", styles["callout"])]],
            colWidths=[165 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), status_color),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            ),
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(escape(_humanize(str(dossier.approach["message"]))), styles["body"]))
    story.append(Paragraph("Termos protegidos", styles["h2"]))
    protected = [[str(index), _humanize(term)] for index, term in enumerate(dossier.approach["protected_terms"], start=1)]
    story.append(_table(protected, [11 * mm, 154 * mm], styles))

    story.append(Paragraph("6. Decisão e próximo movimento", styles["h1"]))
    decision_rows = [
        ["Classificação", dossier.decision["classification"]],
        ["Próxima ação", _next_action_display(dossier.decision["next_action"])],
        ["Liberado para envio", "SIM" if dossier.decision["sendable"] else "NÃO"],
    ]
    story.append(_table(decision_rows, [49 * mm, 116 * mm], styles, header_columns={0}))

    story.append(Paragraph("7. Evidências e fontes", styles["h1"]))
    evidence_rows = [["Status", "Campo e valor", "Fonte / trecho"]]
    representative = _representative_claims(dossier.claims, limit=18)
    for claim in representative:
        source = urlparse(claim.source.url).netloc or "arquivo fornecido"
        excerpt = " ".join(claim.excerpt.split())[:180]
        evidence_rows.append(
            [
                _status_label(claim.status),
                f"{claim.predicate}\n{_claim_display(claim)}",
                f"{source}\n{excerpt}",
            ]
        )
    if not representative:
        evidence_rows.append(["LACUNA", "NAO LOCALIZADO", "Nenhuma evidência coletada para este caso."])
    story.append(_table(evidence_rows, [28 * mm, 57 * mm, 80 * mm], styles, first_row_header=True))
    sources = _unique_sources(dossier.claims)
    if sources:
        story.append(Paragraph("Fontes únicas", styles["h2"]))
    for index, url in enumerate(sources, start=1):
        if index == 14:
            story.append(PageBreak())
            story.append(Paragraph("Fontes únicas — continuação", styles["h2"]))
        story.append(Paragraph(f"{index}. {escape(url)}", styles["small"]))
        story.append(Spacer(1, 1.2 * mm))

    document = VFReportTemplate(str(path))
    document.build(story)
    return path


def _table(
    rows,
    widths,
    styles,
    first_row_header: bool = False,
    header_columns: set[int] | None = None,
    centered_columns: set[int] | None = None,
):
    header_columns = header_columns or set()
    centered_columns = centered_columns or set()
    converted = []
    for row_index, row in enumerate(rows):
        converted_row = []
        for column_index, value in enumerate(row):
            style = styles["small"]
            if first_row_header and row_index == 0:
                style = ParagraphStyle("TableHeader", parent=styles["small"], textColor=WHITE, fontName="Helvetica-Bold")
            elif column_index in header_columns:
                style = ParagraphStyle("KeyCell", parent=styles["small"], fontName="Helvetica-Bold", textColor=INK)
            elif column_index in centered_columns:
                style = styles["center"]
            converted_row.append(Paragraph(escape(str(value)).replace("\n", "<br/>"), style))
        converted.append(converted_row)
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7D1CA")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, WARM_GRAY]),
    ]
    if first_row_header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ]
        )
    for column in header_columns:
        commands.append(("BACKGROUND", (column, 0), (column, -1), LAVENDER))
    return Table(
        converted,
        colWidths=widths,
        repeatRows=1 if first_row_header else 0,
        splitByRow=1,
        splitInRow=0,
        style=TableStyle(commands),
    )


def _callout(text: str, styles):
    return Table(
        [[Paragraph(text, styles["callout"])]],
        colWidths=[165 * mm],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), INK),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        ),
    )


def _organization_rows(dossier: Dossier):
    labels = {
        "organization.legal_name": "Razao social",
        "organization.cnpj": "CNPJ",
        "organization.registration_status_reported": "Situacao cadastral",
        "organization.capital_social_reported": "Capital informado",
    }
    rows = [["Campo", "Valor", "Evidencia"]]
    seen = set()
    for claim in sorted(dossier.claims, key=lambda item: (-item.confidence, item.predicate)):
        if claim.predicate not in labels or claim.predicate in seen:
            continue
        seen.add(claim.predicate)
        rows.append(
            [
                labels[claim.predicate],
                claim.value,
                f"{_status_label(claim.status)} ({claim.confidence:.2f})",
            ]
        )
    for predicate, label in labels.items():
        if predicate not in seen:
            fallback = None
            if predicate == "organization.legal_name":
                fallback = dossier.organization.get("legal_name")
            elif predicate == "organization.cnpj":
                fallback = _format_cnpj(str(dossier.organization.get("cnpj") or ""))
            rows.append(
                [
                    label,
                    fallback or "NAO LOCALIZADO",
                    "INFORMADO - validação oficial pendente" if fallback else "LACUNA",
                ]
            )
    return rows


def _process_rows(claims: Iterable[Claim]):
    process_claims: dict[str, Claim] = {}
    events: dict[str, list[Claim]] = defaultdict(list)
    roles: dict[str, list[Claim]] = defaultdict(list)
    for claim in claims:
        if claim.predicate == "process.number":
            process_claims.setdefault(str(claim.value), claim)
        for process in claim.tags:
            if not str(process).replace(".", "").replace("-", "").isdigit():
                continue
            if claim.predicate == "process.event":
                events[process].append(claim)
            elif claim.predicate == "process.role":
                roles[process].append(claim)
    rows = [["Processo", "Evento", "Data", "Papel", "Status"]]
    ordered = sorted(
        process_claims.items(),
        key=lambda item: item[1].observed_event_at
        or _event_date(events.get(item[0], []))
        or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    for process, claim in ordered[:24]:
        event_claims = events.get(process, [])
        event = event_claims[0] if event_claims else None
        role = roles.get(process, [None])[0]
        date = (event.observed_event_at if event else claim.observed_event_at)
        rows.append(
            [
                process,
                event.value if event else "NAO LOCALIZADO",
                date.strftime("%d/%m/%Y") if date else "NAO LOCALIZADO",
                role.value if role else "NAO LOCALIZADO",
                _status_label(claim.status),
            ]
        )
    if len(rows) == 1:
        rows.append(["NAO LOCALIZADO", "-", "-", "-", "gap"])
    return rows


def _event_date(claims: list[Claim]):
    dates = [claim.observed_event_at for claim in claims if claim.observed_event_at]
    return max(dates) if dates else None


def _contact_rows(dossier: Dossier):
    rows = [["Tipo", "Valor", "Evidência", "Fonte"]]
    labels = {"emails": "E-mail", "phones": "Telefone", "linkedin": "LinkedIn", "forms": "Formulário"}
    for bucket in ("emails", "phones", "linkedin", "forms"):
        items = dossier.contacts.get(bucket, [])[:8]
        if not items:
            rows.append([labels[bucket], "NAO LOCALIZADO", "LACUNA", "-"])
            continue
        for item in items:
            value = item.get("value") or "NAO LOCALIZADO"
            if bucket == "phones":
                value = _format_phone(str(value))
            rows.append(
                [
                    labels[bucket],
                    value,
                    f"{_status_label(item.get('status', 'gap'))} ({item.get('confidence', 0):.2f})",
                    urlparse(str(item.get("source") or "")).netloc or "arquivo fornecido",
                ]
            )
    return rows


def _representative_claims(claims: list[Claim], limit: int):
    rank = {
        ClaimStatus.CONFIRMED: 0,
        ClaimStatus.CORROBORATED: 1,
        ClaimStatus.CONFLICT: 2,
        ClaimStatus.HYPOTHESIS: 3,
        ClaimStatus.GAP: 4,
    }
    unique_by_predicate = defaultdict(list)
    per_predicate_limit = {
        "organization.cnpj": 3,
        "organization.legal_name": 3,
        "organization.contact.email": 6,
        "organization.contact.phone": 6,
        "person.professional_role": 6,
        "person.linkedin": 4,
        "organization.linkedin": 2,
        "process.number": 8,
        "process.event": 8,
        "process.role": 8,
    }
    for claim in sorted(claims, key=lambda item: (rank[item.status], -item.confidence)):
        key = (claim.predicate, str(claim.value), claim.source.url)
        existing = {
            (item.predicate, str(item.value), item.source.url)
            for item in unique_by_predicate[claim.predicate]
        }
        if key in existing:
            continue
        cap = per_predicate_limit.get(claim.predicate, 4)
        if len(unique_by_predicate[claim.predicate]) >= cap:
            continue
        unique_by_predicate[claim.predicate].append(claim)

    priority = [
        "organization.cnpj",
        "organization.legal_name",
        "organization.contact.email",
        "organization.contact.phone",
        "organization.linkedin",
        "person.linkedin",
        "person.professional_role",
        "process.number",
        "process.event",
        "process.role",
    ]
    predicates = priority + sorted(set(unique_by_predicate) - set(priority))
    selected = []
    round_index = 0
    while len(selected) < limit:
        added = False
        for predicate in predicates:
            bucket = unique_by_predicate[predicate]
            if round_index < len(bucket):
                selected.append(bucket[round_index])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        round_index += 1
    return selected


def _unique_sources(claims: Iterable[Claim]):
    return sorted({claim.source.url for claim in claims})


def _format_cnpj(value: str):
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) != 14:
        return value
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def _format_phone(value: str):
    digits = "".join(character for character in value if character.isdigit())
    prefix = "+55 " if digits.startswith("55") and len(digits) in {12, 13} else ""
    if prefix:
        digits = digits[2:]
    if len(digits) == 10:
        return f"{prefix}({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    if len(digits) == 11:
        return f"{prefix}({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    return value


def _status_label(status):
    raw = status.value if isinstance(status, ClaimStatus) else str(status)
    return {
        "confirmed": "CONFIRMADO",
        "corroborated": "CORROBORADO",
        "hypothesis": "HIPÓTESE",
        "conflict": "CONFLITO",
        "gap": "LACUNA",
    }.get(raw, raw.upper())


def _humanize(value: str):
    replacements = {
        "RASCUNHO_INTERNO_CONDICIONADO": "RASCUNHO INTERNO CONDICIONADO",
        "NAO_DEFINIDA_SEM_APETITE_VIGENTE_E_ANALISE_DE_SUBSCRICAO": (
            "NÃO DEFINIDA — exige apetite vigente e análise de subscrição"
        ),
        "Aderencia securitaria ainda nao demonstrada.": "Aderência securitária ainda não demonstrada.",
        "nao ": "não ",
        "Nao ": "Não ",
        "validacao": "validação",
        "Validacao": "Validação",
        "existencia": "existência",
        "apolice": "apólice",
        "aceitacao": "aceitação",
        "revisao": "revisão",
        "proxima": "próxima",
        "hypothesis": "hipótese",
        "corroborated": "corroborado",
        "confirmed": "confirmado",
        "papel_a_validar": "papel a validar",
        "interlocutor_potencial_nao_decisor_confirmado": (
            "interlocutor potencial — poder decisório não confirmado"
        ),
        "juridico_interno": "jurídico interno",
        "cfo_tesouraria": "CFO / tesouraria",
        "contragarantia": "contragarantia",
    }
    for before, after in replacements.items():
        value = value.replace(before, after)
    return value.replace("_", " ")


def _claim_display(claim: Claim):
    if isinstance(claim.value, dict):
        if claim.predicate in {"organization.contact.email", "organization.contact.phone"}:
            value = str(claim.value.get("value") or "NAO LOCALIZADO")
            return _format_phone(value) if claim.predicate.endswith("phone") else value
        if claim.predicate == "person.professional_role":
            return f"{claim.value.get('name', '')} — {claim.value.get('role', '')} — potencial"
    return str(claim.value)


def _next_action_display(value: str):
    prefix = "Coletar fontes oficiais para:"
    if not value.startswith(prefix):
        return _humanize(value)
    codes = [item.strip() for item in value[len(prefix) :].split(",") if item.strip()]
    return "Coletar fontes oficiais para: " + "; ".join(_gap_label(code) for code in codes)


def _block_summary(block: dict) -> str:
    evidence = block.get("evidence") or []
    if evidence:
        parts = []
        for item in evidence[:2]:
            value = item.get("value")
            if isinstance(value, dict):
                value = value.get("signal") or value.get("type") or value.get("value") or str(value)
            parts.append(f"{item.get('classification', '').upper()}: {value}")
        return " | ".join(parts)
    data = block.get("data")
    if isinstance(data, dict):
        compact = [f"{key}: {value}" for key, value in data.items() if value not in (None, "", [], {})]
        return " | ".join(compact[:3]) or "NAO LOCALIZADO"
    if isinstance(data, list):
        return "; ".join(str(item) for item in data[:3]) or "NAO LOCALIZADO"
    return str(data or block.get("rule") or "NAO LOCALIZADO")


def _gap_label(gap: str):
    labels = {
        "entity_conflict": "Corrigir o conflito entre o CNPJ e a razão social antes de prosseguir.",
        "guarantee_status": "Confirmar existência e modalidade de garantia nos autos.",
        "legal_objective": "Registrar o objetivo processual definido pelo tributarista.",
        "official_entity_confirmation": "Confirmar razão social e CNPJ em fonte oficial.",
        "process_current_phase": "Confirmar a fase processual atual em fonte oficial.",
        "process_source": "Obter os autos ou publicação judicial oficial.",
        "updated_exposure_value": "Obter o valor atualizado da exposição.",
    }
    return labels.get(gap, gap.replace("_", " ").capitalize())
