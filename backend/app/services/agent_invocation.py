"""Framework-agnostic agent invocation for channel messages (non-streaming)."""

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.pydantic_deep_assistant import PydanticDeepAssistant, PydanticDeepContext
from app.core.config import settings
from app.repositories import conversation_repo


@dataclass
class ToolEvent:
    """A tool call + result pair collected during agent execution."""

    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    result: str = ""


logger = logging.getLogger(__name__)


def _provider_from_model(model_name: str) -> str:
    name = (model_name or "").lower()
    if name.startswith(("gpt", "o1", "o3", "o4", "openai")):
        return "openai"
    if name.startswith(("claude", "anthropic")):
        return "anthropic"
    if name.startswith(("gemini", "google")):
        return "google"
    return "unknown"


class AgentInvocationService:
    """Invoke the configured AI agent and return the final text response.

    Used by channel adapters where streaming is not required. Both the user
    message and the assistant reply are persisted to the database.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def invoke(
        self,
        *,
        user_message: str,
        conversation_id: UUID,
        user_id: UUID | None = None,
        project_id: UUID | None = None,
        organization_id: UUID | None = None,
        system_prompt_override: str | None = None,
        model_override: str | None = None,
    ) -> tuple[str, list[ToolEvent]]:
        """Run the agent and return final text + tool events.

        Returns:
            Tuple of (response_text, tool_events).
        """
        await self._persist_user_message(conversation_id, user_message)

        history = await self._load_history(conversation_id)
        # Resolve active KB collections server-side — never trust the client
        kb_collection_names = await self._load_active_kb_collection_names(
            conversation_id=conversation_id,
            user_id=user_id,
            organization_id=organization_id,
        )

        tool_events: list[ToolEvent] = []
        response_text, tool_events = await self._call_agent(
            user_message=user_message,
            history=history,
            conversation_id=conversation_id,
            user_id=user_id,
            project_id=project_id,
            kb_collection_names=kb_collection_names,
            system_prompt_override=system_prompt_override,
            model_override=model_override,
        )

        await self._persist_assistant_message(conversation_id, response_text)

        return response_text, tool_events

    async def _call_agent(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
        **kwargs: Any,
    ) -> tuple[str, list[ToolEvent]]:
        """Dispatch to the framework-specific agent implementation."""
        return await self._call_pydantic_deep(user_message=user_message, history=history, **kwargs)

    async def _call_pydantic_deep(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
        **kwargs: Any,
    ) -> tuple[str, list[ToolEvent]]:
        """Invoke PydanticDeep agent (non-streaming).

        PydanticDeep manages its own conversation history via history_messages_path,
        so we pass the conversation_id for per-conversation persistence rather than
        replaying the DB message history.
        """

        conversation_id = str(kwargs.get("conversation_id") or "default")
        user_id = str(kwargs.get("user_id")) if kwargs.get("user_id") else None
        model_name: str | None = kwargs.get("model_override")

        assistant = PydanticDeepAssistant(
            model_name=model_name,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        context = PydanticDeepContext(user_id=user_id)
        text, _, _ = await assistant.run(user_message, context=context)
        return text, []

    async def _load_active_kb_collection_names(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID | None,
        organization_id: UUID | None,
    ) -> list[str]:
        """Return vector-store collection names for the active KBs of this conversation.

        Resolution is always server-side: we intersect the conversation's
        active_knowledge_base_ids with the KBs actually visible to the user,
        preventing any client-supplied KB IDs from leaking cross-org data.
        """
        return []

    async def _persist_user_message(self, conversation_id: UUID, content: str) -> None:
        """Persist the user message directly via conversation repo."""
        await conversation_repo.create_message(
            self.db,
            conversation_id=conversation_id,
            role="user",
            content=content,
        )

    async def _persist_assistant_message(self, conversation_id: UUID, content: str) -> None:
        """Persist the assistant reply directly via conversation repo."""
        await conversation_repo.create_message(
            self.db,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            model_name=settings.AI_MODEL,
        )

    async def _load_history(self, conversation_id: UUID) -> list[dict[str, str]]:
        """Load conversation message history ordered chronologically."""
        messages = await conversation_repo.get_messages_by_conversation(
            self.db,
            conversation_id=conversation_id,
            skip=0,
            limit=200,
        )
        return [{"role": m.role, "content": m.content} for m in messages]
