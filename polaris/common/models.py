"""Pydantic models mirroring contracts/*.schema.json.

Why both a JSON Schema and a pydantic model for the same shape: the JSON
Schema in contracts/ is the durable, language-agnostic contract Claudia and
Viki can read without touching Python (this is what WP0 asks for -- "frozen
JSON handoffs"). The pydantic models here are the enforced runtime version
so a bad record fails fast, in this process, instead of silently landing in
a database and breaking the next stage. tests/test_contracts_in_sync.py
checks the two do not drift apart.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from polaris.common.let_taxonomy import LET


class SourceDocument(BaseModel):
    source_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    raw_uri: str
    file_hash: str
    retrieved_at: datetime
    origin_url: Optional[str] = None
    organisation: Optional[str] = None
    topic_candidate: str
    document_type: str  # pdf | html | docx
    robots_allowed: Optional[bool] = None
    accepted: bool
    acceptance_reason: str
    rejection_reason: Optional[str] = None
    source_quality_tier: Optional[str] = None  # gold | silver | bronze
    version_date: Optional[date] = None
    duplicate_of: Optional[str] = None
    byte_size: Optional[int] = None
    language: Optional[str] = None
    jurisdiction: Optional[str] = None
    requires_ocr: Optional[bool] = None
    document_class: Optional[str] = None  # D1-D11, populated only when the LLM qualifier was consulted

    @field_validator("file_hash")
    @classmethod
    def _hash_shape(cls, v: str) -> str:
        if not v.startswith("sha256:") or len(v) != 71:
            raise ValueError("file_hash must be 'sha256:<64 hex chars>'")
        return v

    @field_validator("document_type")
    @classmethod
    def _doc_type(cls, v: str) -> str:
        allowed = {"pdf", "html", "docx"}
        if v not in allowed:
            raise ValueError(f"document_type must be one of {allowed}")
        return v


class Segment(BaseModel):
    segment_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    text: str
    page_from: Optional[int] = None
    page_to: Optional[int] = None
    bbox: Optional[list[float]] = None
    section_path: Optional[str] = None
    label: LET
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: str = "auto_accepted"  # auto_accepted | needs_review | reviewed_corrected | reviewed_confirmed
    model_id: str
    prompt_version: Optional[str] = None
    rationale: Optional[str] = None

    @field_validator("review_status")
    @classmethod
    def _status(cls, v: str) -> str:
        allowed = {"auto_accepted", "needs_review", "reviewed_corrected", "reviewed_confirmed"}
        if v not in allowed:
            raise ValueError(f"review_status must be one of {allowed}")
        return v


class PolicyObject(BaseModel):
    object_id: str
    source_id: str
    segment_id: str
    source_ref: str
    label: LET
    concept_id: Optional[str] = None
    concept_state: str  # mapped | unmapped | deferred
    parameters: dict = Field(default_factory=dict)
    verification_status: Optional[str] = None
    superseded_by: Optional[str] = None
    pack_origin: str = "customer"  # benchmark | customer

    @field_validator("concept_state")
    @classmethod
    def _concept_state(cls, v: str) -> str:
        allowed = {"mapped", "unmapped", "deferred"}
        if v not in allowed:
            raise ValueError(f"concept_state must be one of {allowed}")
        return v


class ComparisonFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    comparison_state: str
    customer_object_id: Optional[str] = None
    benchmark_concept_id: Optional[str] = None
    fact: str
    recommendation: Optional[str] = None
    evidence_object_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    materiality: float = Field(ge=0.0, le=1.0)
    review_state: str = "draft"

    @field_validator("comparison_state")
    @classmethod
    def _state(cls, v: str) -> str:
        allowed = {"ALIGNED", "STRONGER", "WEAKER", "MISSING", "DIFFERENT", "NOT_COMPARABLE", "EXTRA"}
        if v not in allowed:
            raise ValueError(f"comparison_state must be one of {allowed}")
        return v
