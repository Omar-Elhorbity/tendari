"""Conversations / agent endpoints (the core).

M2: create a conversation, send a message (non-streaming → JSON), read history.
SSE streaming is added in M3.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agent.engine import run_agent
from app.agent.providers import get_provider
from app.auth import CurrentWorkspace, DbSession
from app.db import SessionLocal
from app.models import Conversation, Customer, Message
from app.schemas.conversations import (
    ConversationCreate,
    ConversationCreated,
    ConversationHistory,
    MessageRequest,
    MessageResponse,
    ToolCallSummary,
    UsageSummary,
)
from app.tools import get_registry

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


async def _find_or_create_customer(
    session: DbSession, workspace_id: uuid.UUID, email: str
) -> uuid.UUID:
    # Normalize here so matching is a property of the data layer, not callers.
    email = email.strip().lower()
    customer = await session.scalar(
        select(Customer).where(
            Customer.workspace_id == workspace_id, Customer.email == email
        )
    )
    if customer is None:
        customer = Customer(workspace_id=workspace_id, email=email)
        session.add(customer)
        await session.flush()
    return customer.id


@router.post("", response_model=ConversationCreated, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, workspace: CurrentWorkspace, session: DbSession
) -> ConversationCreated:
    customer_id = None
    if body.customer_email:
        customer_id = await _find_or_create_customer(
            session, workspace.id, body.customer_email
        )
    conversation = Conversation(workspace_id=workspace.id, customer_id=customer_id)
    session.add(conversation)
    await session.flush()
    return ConversationCreated(id=conversation.id)


async def _get_owned_conversation(
    conversation_id: uuid.UUID, workspace: CurrentWorkspace, session: DbSession
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace.id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def post_message(
    conversation_id: uuid.UUID,
    body: MessageRequest,
    workspace: CurrentWorkspace,
    session: DbSession,
) -> MessageResponse:
    conversation = await _get_owned_conversation(conversation_id, workspace, session)

    # Streaming (SSE) lands in M3; M2 always returns the full JSON response.
    result = await run_agent(
        session=session,
        session_factory=SessionLocal,
        workspace=workspace,
        conversation=conversation,
        user_text=body.content,
        registry=get_registry(),
        provider=get_provider(),
        stream=False,
    )

    return MessageResponse(
        message_id=result.final_message_id,
        content=result.text,
        tool_calls=[ToolCallSummary(**tc) for tc in result.tool_calls],
        usage=UsageSummary(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=float(result.cost_usd),
        ),
    )


@router.get("/{conversation_id}", response_model=ConversationHistory)
async def get_conversation(
    conversation_id: uuid.UUID, workspace: CurrentWorkspace, session: DbSession
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace.id,
        )
        .options(
            selectinload(Conversation.messages).selectinload(Message.tool_calls)
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation
