"""What the assistant endpoints send and receive (M1.13)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AiToolOut(BaseModel):
    name: str
    description: str
    risk: str
    input_schema: dict[str, Any]


class AiActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: uuid.UUID
    tool: str
    risk: str
    status: str
    prompt: str | None
    args: dict[str, Any]
    result: dict[str, Any] | None
    error_code: str | None
    decided_at: datetime | None
    created_at: datetime


class AiChatIn(BaseModel):
    business_id: uuid.UUID
    text: str = Field(min_length=1, max_length=1000)
    lang: str = Field(default="en", pattern="^(en|ar)$")


class AiCallIn(BaseModel):
    business_id: uuid.UUID
    tool: str = Field(min_length=1, max_length=60)
    args: dict[str, Any] = Field(default_factory=dict)


class AiDecideIn(BaseModel):
    business_id: uuid.UUID


class AiChatOut(BaseModel):
    trace_id: uuid.UUID
    message: str | None = Field(
        description="A stable code when nothing ran: not_understood, party_not_found…"
    )
    message_facts: dict[str, Any] = Field(default_factory=dict)
    actions: list[AiActionOut]
