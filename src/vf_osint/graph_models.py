from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import ClaimStatus, SourceClass


def utc_now() -> datetime:
    return datetime.now(UTC)


class GraphNodeType(StrEnum):
    PROCESS = "PROCESSO"
    PROCEDURAL_EVENT = "EVENTO_PROCESSUAL"
    COMPANY = "EMPRESA"
    PERSON = "PESSOA"
    ROLE = "CARGO"
    OFFICE = "ESCRITORIO"
    DOCUMENT = "DOCUMENTO"
    DOMAIN = "DOMINIO"
    EMAIL = "EMAIL"
    PHONE = "TELEFONE"
    GUARANTEE = "GARANTIA"
    SECURITARIAN_HYPOTHESIS = "HIPOTESE_SECURITARIA"
    EVIDENCE = "EVIDENCIA"


class GraphRelationshipType(StrEnum):
    AFFECTS = "AFETA"
    GENERATED = "GEROU"
    SUGGESTS = "SUGERE"
    HAS = "POSSUI"
    HOLDS = "OCUPA"
    SIGNED = "ASSINOU"
    REPRESENTS = "REPRESENTA"
    APPEARED_IN = "APARECEU_EM"
    CONTAINS = "CONTEM"
    ASSOCIATED_WITH = "ASSOCIADO_A"
    BELONGS_TO = "PERTENCE_A"
    USES = "UTILIZA"
    CONFIRMS = "CONFIRMA"
    SUPPORTS = "SUPORTA"
    HAS_GUARANTEE = "POSSUI_GARANTIA"
    COMMERCIAL_VALIDATION = "VALIDADA_PELO_COMERCIAL"


class GraphNode(BaseModel):
    node_id: str
    node_type: GraphNodeType
    properties: dict[str, Any] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class GraphRelationship(BaseModel):
    relationship_id: str = ""
    from_node: str
    to_node: str
    relationship_type: GraphRelationshipType
    evidence_node: str
    source_url: str
    evidence_excerpt: str
    observed_at: datetime | None = None
    score: float = Field(ge=0, le=100)
    classification: ClaimStatus
    justification: str
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def build_relationship_id(self):
        if self.relationship_id:
            return self
        canonical = "|".join(
            [
                self.from_node,
                self.relationship_type.value,
                self.to_node,
                self.evidence_node,
                self.evidence_excerpt,
            ]
        )
        self.relationship_id = sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return self


class ProcessEventInput(BaseModel):
    event_type: str
    event_date: datetime | None = None
    description: str


class ProcessGraphInput(BaseModel):
    process_number: str
    tribunal: str | None = None
    instance: str | None = None
    process_class: str | None = None
    subject: str | None = None
    amount: float | None = Field(default=None, ge=0)
    phase: str | None = None
    active: bool | None = None
    company_cnpj: str
    company_legal_name: str
    company_role: str | None = None
    events: list[ProcessEventInput] = Field(default_factory=list)
    source_system: str
    source_url: str
    source_title: str = ""
    source_excerpt: str
    source_class: SourceClass = SourceClass.OFFICIAL_COURT
    source_date: datetime | None = None
    evidence_score: float = Field(default=75, ge=0, le=100)

    @field_validator("company_cnpj")
    @classmethod
    def normalize_cnpj(cls, value: str) -> str:
        digits = "".join(character for character in value if character.isdigit())
        if len(digits) != 14:
            raise ValueError("CNPJ deve conter 14 digitos")
        return digits

    @field_validator("process_number")
    @classmethod
    def normalize_process(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Número do processo é obrigatório")
        return normalized

    @model_validator(mode="after")
    def require_auditable_source(self):
        if not self.source_url.startswith(("http://", "https://", "file://")):
            raise ValueError("A origem processual exige URL pública ou arquivo local auditável")
        if not self.source_excerpt.strip():
            raise ValueError("A origem processual exige trecho de evidência")
        excerpt_digits = "".join(character for character in self.source_excerpt if character.isdigit())
        process_digits = "".join(character for character in self.process_number if character.isdigit())
        if process_digits and process_digits not in excerpt_digits:
            raise ValueError("O trecho da evidência deve conter o número do processo")
        if (
            self.company_cnpj not in excerpt_digits
            and self.company_legal_name.casefold() not in self.source_excerpt.casefold()
        ):
            raise ValueError("O trecho da evidência deve identificar a empresa ou o CNPJ")
        return self


class GraphFeedbackInput(BaseModel):
    feedback_type: str
    target_node_id: str
    value: str
    operator: str
    note: str = ""
    occurred_at: datetime = Field(default_factory=utc_now)

    @field_validator("feedback_type")
    @classmethod
    def validate_feedback_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {
            "DECISOR_CONFIRMADO",
            "DECISOR_REJEITADO",
            "CARGO_ATUALIZADO",
            "TRIBUTARISTA_IDENTIFICADO",
            "EMAIL_VALIDO",
            "EMAIL_INVALIDO",
            "TELEFONE_VALIDO",
            "TELEFONE_INCORRETO",
            "GARANTIA_EXISTENTE",
            "GARANTIA_NAO_CONFIRMADA",
            "CONTRATO_ASSINADO",
            "OPORTUNIDADE_PERDIDA",
        }
        if normalized not in allowed:
            raise ValueError("Tipo de feedback comercial não suportado")
        return normalized


class GraphOpportunity(BaseModel):
    graph_id: str
    parent_graph_id: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)
    origin_process_ids: list[str] = Field(default_factory=list)
    origin_status: str
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]
    opportunity_score: float = Field(ge=0)
    opportunity_classification: str
    score_components: dict[str, float] = Field(default_factory=dict)
    critical_events: list[dict[str, Any]] = Field(default_factory=list)
    validation_gaps: list[str] = Field(default_factory=list)
    views: dict[str, Any] = Field(default_factory=dict)
    next_action: str


def stable_node_id(node_type: GraphNodeType, value: str) -> str:
    digest = sha256(value.strip().casefold().encode("utf-8")).hexdigest()[:24]
    return f"{node_type.value.casefold()}:{digest}"
