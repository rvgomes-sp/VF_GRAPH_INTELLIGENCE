from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class ClaimStatus(StrEnum):
    CONFIRMED = "confirmed"
    CORROBORATED = "corroborated"
    HYPOTHESIS = "hypothesis"
    GAP = "gap"
    CONFLICT = "conflict"


class EvidenceLayer(StrEnum):
    IDENTITY = "identity"
    INSTITUTIONAL = "institutional_context"
    CORPORATE_STRUCTURE = "corporate_structure"
    PUBLIC_CONTACTS = "public_contacts"
    FISCAL_LITIGATION = "fiscal_litigation"
    OFFICIAL_DOCUMENTS = "official_documents"
    FINANCIAL_STATEMENTS = "financial_statements"
    GOVERNANCE = "governance_decision_makers"
    RECENT_EVENTS = "recent_events"


class SourceClass(StrEnum):
    OFFICIAL_COURT = "official_court"
    OFFICIAL_REGISTRY = "official_registry"
    OFFICIAL_GOVERNMENT = "official_government"
    COMPANY_OWNED = "company_owned"
    PROFESSIONAL_PUBLIC = "professional_public"
    NEWS = "news"
    AGGREGATOR = "aggregator"
    LEGACY_CRM = "legacy_crm"
    USER_SUPPLIED = "user_supplied"


SOURCE_PRIORS: dict[SourceClass, float] = {
    SourceClass.OFFICIAL_COURT: 0.97,
    SourceClass.OFFICIAL_REGISTRY: 0.95,
    SourceClass.OFFICIAL_GOVERNMENT: 0.92,
    SourceClass.COMPANY_OWNED: 0.72,
    SourceClass.PROFESSIONAL_PUBLIC: 0.58,
    SourceClass.NEWS: 0.55,
    SourceClass.AGGREGATOR: 0.42,
    SourceClass.USER_SUPPLIED: 0.40,
    SourceClass.LEGACY_CRM: 0.32,
}


class SourceRecord(BaseModel):
    url: str
    title: str = ""
    source_class: SourceClass
    captured_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None
    content_hash: str
    http_status: int | None = None
    robots_allowed: bool = True
    extractor: str = "unknown"
    raw_path: str | None = None
    evidence_layer: EvidenceLayer | None = None
    search_objective: str = ""
    search_score: float | None = None


class Claim(BaseModel):
    subject_id: str
    predicate: str
    value: Any
    status: ClaimStatus
    confidence: float = Field(ge=0, le=1)
    source: SourceRecord
    excerpt: str = ""
    observed_event_at: datetime | None = None
    rationale: str = ""
    tags: list[str] = Field(default_factory=list)
    claim_id: str = ""

    @model_validator(mode="after")
    def build_claim_id(self):
        if self.claim_id:
            return self
        canonical = "|".join(
            [
                self.subject_id,
                self.predicate,
                str(self.value),
                self.source.content_hash,
                self.excerpt,
            ]
        )
        self.claim_id = sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return self


class PublicDocument(BaseModel):
    url: str
    title: str
    text: str
    source_class: SourceClass
    captured_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None
    http_status: int = 200
    content_type: str = "text/html"
    links: list[str] = Field(default_factory=list)
    content_hash: str = ""
    evidence_layer: EvidenceLayer | None = None
    search_objective: str = ""
    search_score: float | None = None

    @model_validator(mode="after")
    def hash_content(self):
        if not self.content_hash:
            canonical = f"{self.url}|{self.text}"
            self.content_hash = sha256(canonical.encode("utf-8")).hexdigest()
        return self


class OrganizationSeed(BaseModel):
    legal_name: str
    cnpj: str
    state: str | None = None
    trade_name: str | None = None
    seed_urls: list[str] = Field(default_factory=list)
    legacy_text: str | None = None

    @field_validator("cnpj")
    @classmethod
    def normalize_cnpj(cls, value: str) -> str:
        digits = "".join(character for character in value if character.isdigit())
        if len(digits) != 14:
            raise ValueError("CNPJ deve conter 14 digitos")
        return digits

    @property
    def subject_id(self) -> str:
        return f"cnpj:{self.cnpj}"


class Dossier(BaseModel):
    dossier_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    organization: dict[str, Any]
    executive_reading: dict[str, Any]
    claims: list[Claim]
    gaps: list[str]
    conflicts: list[str]
    contacts: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    interlocutors: list[dict[str, Any]]
    approach: dict[str, Any]
    decision: dict[str, Any]
    provenance: dict[str, Any]
    identity_resolution: dict[str, Any] = Field(default_factory=dict)
    evidence_layers: dict[str, Any] = Field(default_factory=dict)
    intelligence_blocks: dict[str, Any] = Field(default_factory=dict)
