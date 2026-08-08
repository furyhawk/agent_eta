"""Per-connection AI agent session (PydanticDeep).

PydanticDeep manages conversation history internally via the backend
(history_messages_path), so this session does not maintain ``conversation_history``.
"""

import asyncio
import contextlib
import logging
from typing import Any, ClassVar

from fastapi import WebSocket, WebSocketDisconnect
from pydantic_ai import (
    Agent,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ToolCallPartDelta,
)
from pydantic_ai.messages import BinaryContent, TextPart, ThinkingPart, ThinkingPartDelta

from app.agents.pydantic_deep_assistant import PydanticDeepContext, get_agent
from app.api.deps import get_conversation_service
from app.db.models.user import User
from app.db.session import get_db_context
from app.services.agent import (
    persist_assistant_turn,
    persist_user_turn,
    send_event,
)
from app.services.file_storage import get_file_storage

logger = logging.getLogger(__name__)


class AgentSession:
    """One WebSocket session with the PydanticDeep agent."""

    # Control frames this session implements, beyond `stop`. Anything else with
    # a `type` is ignored rather than treated as a prompt.
    _HANDLED_FRAME_TYPES: ClassVar[frozenset[str]] = frozenset({"message"})

    def __init__(
        self,
        websocket: WebSocket,
        user: User,
    ) -> None:
        self.websocket = websocket
        self.user = user
        self.context: PydanticDeepContext = {}
        self.context["user_id"] = str(user.id) if user else None
        self.context["user_name"] = user.email if user else None
        self.current_conversation_id: str | None = None
        self._turn_task: asyncio.Task[None] | None = None

    async def handle_frame(self, data: dict[str, Any]) -> None:
        """Dispatch one incoming WebSocket frame.

        A ``stop`` cancels the running turn; any other frame starts a new turn as
        a cancellable background task. Clients serialize turns, so a frame that
        arrives while a turn is running is ignored.
        """
        msg_type = data.get("type")

        if msg_type == "stop":
            await self._cancel_turn()
            return

        # A frame carrying a `type` this session does not implement is a control
        # frame, not a prompt — the shared frontend hook emits `resume` and
        # `ask_user_response` regardless of which framework is generated. Falling
        # through would start a turn with an empty message and answer the user
        # with "Empty message".
        if msg_type is not None and msg_type not in self._HANDLED_FRAME_TYPES:
            logger.debug("Ignoring unsupported control frame: %s", msg_type)
            return

        if self._turn_task is not None and not self._turn_task.done():
            logger.warning("Ignoring message received while a turn is already in progress")
            return
        task = asyncio.create_task(self._run_turn(data))
        self._turn_task = task
        task.add_done_callback(self._on_turn_done)

    def _on_turn_done(self, task: asyncio.Task[None]) -> None:
        """Clear the turn slot and surface unexpected crashes."""
        if self._turn_task is task:
            self._turn_task = None
        if not task.cancelled():
            exc = task.exception()
            if isinstance(exc, WebSocketDisconnect):
                logger.info("Client disconnected during agent turn")
            elif exc is not None:
                logger.error("Agent turn task crashed", exc_info=exc)

    async def _run_turn(self, data: dict[str, Any]) -> None:
        """Run one turn, emitting a terminal ``complete`` even when stopped."""
        try:
            await self.process_message(data)
        except asyncio.CancelledError:
            await send_event(
                self.websocket,
                "complete",
                {
                    "conversation_id": self.current_conversation_id,
                    "stopped": True,
                },
            )
            raise

    async def _cancel_turn(self) -> None:
        """Cancel the in-flight turn task and wait for it to unwind."""
        task = self._turn_task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def shutdown(self) -> None:
        """Cancel any in-flight turn."""
        await self._cancel_turn()

    async def process_message(self, data: dict[str, Any]) -> None:
        """Process one user turn: persist input, run the agent, stream events, persist output."""
        user_message = data.get("message", "")
        file_ids = data.get("file_ids", [])

        if not user_message and not file_ids:
            await send_event(self.websocket, "error", {"message": "Empty message"})
            return
        self.current_conversation_id, newly_created, organization_id = await persist_user_turn(
            self.user,
            user_message,
            file_ids,
            requested_conversation_id=data.get("conversation_id"),
            current_conversation_id=self.current_conversation_id,
        )
        if newly_created and self.current_conversation_id:
            await send_event(
                self.websocket,
                "conversation_created",
                {"conversation_id": self.current_conversation_id},
            )

        await send_event(self.websocket, "user_prompt", {"content": user_message})

        try:
            assistant = get_agent(
                model_name=data.get("model"),
                thinking_effort=data.get("thinking_effort"),
                conversation_id=self.current_conversation_id or "default",
                user_id=self.context.get("user_id"),
                user_name=self.context.get("user_name"),
            )

            user_input = await self._build_agent_input(user_message, file_ids, assistant)
            collected_tool_calls: list[dict[str, Any]] = []
            collected_thinking: list[str] = []
            async with assistant.agent.iter(user_input, deps=assistant.deps) as agent_run:
                await self._stream_agent_run(
                    agent_run, user_message, collected_tool_calls, collected_thinking
                )
            if self.current_conversation_id and agent_run.result is not None:
                await persist_assistant_turn(
                    self.current_conversation_id,
                    agent_run.result.output,
                    getattr(assistant, "model_name", None),
                    collected_tool_calls,
                    thinking="".join(collected_thinking) or None,
                )

            await send_event(
                self.websocket,
                "complete",
                {"conversation_id": self.current_conversation_id},
            )
        except WebSocketDisconnect:
            raise
        except Exception as e:
            logger.exception("Error processing agent request")
            await send_event(self.websocket, "error", {"message": str(e)})

    async def _build_agent_input(
        self, user_message: str, file_ids: list[Any], assistant: Any
    ) -> str | list[Any]:
        """Fold attached files into the agent input.

        Sandbox backends (Docker/Daytona) get files written to the workspace and a path
        reference appended. ``StateBackend`` falls back to inline content. Images are
        always attached as ``BinaryContent`` parts for vision models.
        """
        if not file_ids:
            return user_message

        storage = get_file_storage()
        file_refs: list[str] = []
        image_parts: list[Any] = []

        backend = assistant.deps.backend
        has_sandbox = (
            hasattr(backend, "container_name")
            or hasattr(backend, "upload_bytes")
            or hasattr(backend, "workspace_id")
        )

        async def _process_files(attached_files: Any) -> None:
            for chat_file in attached_files:
                try:
                    rel_path = f"uploads/{chat_file.filename}"

                    if chat_file.file_type == "image":
                        file_data = await storage.load(chat_file.storage_path)
                        image_parts.append(
                            BinaryContent(data=file_data, media_type=chat_file.mime_type)
                        )
                        if has_sandbox:
                            await assistant.write_file_to_workspace(rel_path, file_data)
                            file_refs.append(
                                f"- {rel_path} (image, also attached inline for vision)"
                            )
                        else:
                            file_refs.append(f"- {chat_file.filename} (image attached inline)")
                    elif chat_file.parsed_content:
                        if has_sandbox:
                            await assistant.write_file_to_workspace(
                                rel_path, chat_file.parsed_content
                            )
                            file_refs.append(f"- {rel_path}")
                        else:
                            file_refs.append(
                                f"- {chat_file.filename}:\n```\n{chat_file.parsed_content}\n```"
                            )
                    else:
                        file_data = await storage.load(chat_file.storage_path)
                        if has_sandbox:
                            await assistant.write_file_to_workspace(rel_path, file_data)
                            file_refs.append(f"- {rel_path}")
                        else:
                            file_refs.append(
                                f"- {chat_file.filename} (binary, not readable as text)"
                            )
                except Exception:
                    logger.warning("Failed to load file %s", chat_file.id, exc_info=True)

        async with get_db_context() as file_db:
            attached_files = await get_conversation_service(file_db).list_attached_files(file_ids)
            await _process_files(attached_files)

        if not file_refs:
            return user_message

        header = (
            "\n\nFiles uploaded to your sandbox workspace (use read_file to access):\n"
            if has_sandbox
            else "\n\nAttached files:\n"
        )
        augmented = user_message + header + "\n".join(file_refs)
        return [augmented, *image_parts] if image_parts else augmented

    async def _stream_agent_run(
        self,
        agent_run: Any,
        user_message: str,
        collected_tool_calls: list[dict[str, Any]],
        collected_thinking: list[str],
    ) -> None:
        """Drive the pydantic-ai agent_run iterator, forwarding all events."""
        async for node in agent_run:
            if Agent.is_user_prompt_node(node):
                prompt_text = (
                    node.user_prompt if isinstance(node.user_prompt, str) else user_message
                )
                await send_event(self.websocket, "user_prompt_processed", {"prompt": prompt_text})
            elif Agent.is_model_request_node(node):
                await send_event(self.websocket, "model_request_start", {})
                async with node.stream(agent_run.ctx) as request_stream:
                    await self._stream_request_events(request_stream, collected_thinking)
            elif Agent.is_call_tools_node(node):
                await send_event(self.websocket, "call_tools_start", {})
                async with node.stream(agent_run.ctx) as handle_stream:
                    await self._stream_tool_events(handle_stream, collected_tool_calls)
            elif Agent.is_end_node(node) and agent_run.result is not None:
                await send_event(
                    self.websocket, "final_result", {"output": agent_run.result.output}
                )

    async def _stream_request_events(
        self, request_stream: Any, collected_thinking: list[str]
    ) -> None:
        """Forward model-request events (text/thinking/tool deltas + final-result start)."""
        async for event in request_stream:
            if isinstance(event, PartStartEvent):
                await send_event(
                    self.websocket,
                    "part_start",
                    {"index": event.index, "part_type": type(event.part).__name__},
                )
                if isinstance(event.part, TextPart) and event.part.content:
                    await send_event(
                        self.websocket,
                        "text_delta",
                        {"index": event.index, "content": event.part.content},
                    )
                elif isinstance(event.part, ThinkingPart) and event.part.content:
                    # Surface the model's reasoning trace to the UI. Anthropic +
                    # OpenAI-reasoning models emit these as the model "thinks".
                    if collected_thinking:
                        collected_thinking.append(" ")
                    collected_thinking.append(event.part.content)
                    await send_event(
                        self.websocket,
                        "thinking_delta",
                        {"index": event.index, "content": event.part.content},
                    )
            elif isinstance(event, PartDeltaEvent):
                if isinstance(event.delta, TextPartDelta):
                    await send_event(
                        self.websocket,
                        "text_delta",
                        {"index": event.index, "content": event.delta.content_delta},
                    )
                elif isinstance(event.delta, ThinkingPartDelta):
                    if event.delta.content_delta:
                        collected_thinking.append(event.delta.content_delta)
                        await send_event(
                            self.websocket,
                            "thinking_delta",
                            {"index": event.index, "content": event.delta.content_delta},
                        )
                elif isinstance(event.delta, ToolCallPartDelta):
                    await send_event(
                        self.websocket,
                        "tool_call_delta",
                        {"index": event.index, "args_delta": event.delta.args_delta},
                    )
            elif isinstance(event, FinalResultEvent):
                await send_event(
                    self.websocket,
                    "final_result_start",
                    {"tool_name": event.tool_name},
                )

    async def _stream_tool_events(
        self,
        handle_stream: Any,
        collected_tool_calls: list[dict[str, Any]],
    ) -> None:
        """Forward tool-call/result events; collect tool calls (with results) for persistence."""
        pending: dict[str, dict[str, Any]] = {}
        async for tool_event in handle_stream:
            if isinstance(tool_event, FunctionToolCallEvent):
                tc = {
                    "tool_call_id": tool_event.part.tool_call_id,
                    "tool_name": tool_event.part.tool_name,
                    "args": tool_event.part.args_as_dict(raise_if_invalid=False),
                }
                collected_tool_calls.append(tc)
                pending[tool_event.part.tool_call_id] = tc
                await send_event(self.websocket, "tool_call", tc)
            elif isinstance(tool_event, FunctionToolResultEvent):
                tc = pending.get(tool_event.tool_call_id)
                if tc is not None:
                    tc["result"] = str(tool_event.result.content)
                await send_event(
                    self.websocket,
                    "tool_result",
                    {
                        "tool_call_id": tool_event.tool_call_id,
                        "content": str(tool_event.result.content),
                    },
                )
