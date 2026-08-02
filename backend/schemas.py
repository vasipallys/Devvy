from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    id: str
    name: str
    content_type: str
    size: int


class MessageOut(BaseModel):
    id: UUID
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    created_at: datetime
    attachments: list[Attachment] = []
    metadata: dict = {}


class ConversationOut(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatRequest(BaseModel):
    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=100_000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    mode: Literal["auto", "chat", "code", "research", "image", "document"] = "auto"


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)

