"""AI Agents module using PydanticDeep.

This module contains a deep agentic coding assistant built with pydantic-deep.
PydanticDeep is built on PydanticAI and provides filesystem operations,
task management, subagent delegation, skills, memory, and Docker sandbox support.
"""

from app.agents.pydantic_deep_assistant import PydanticDeepAssistant, PydanticDeepContext

__all__ = ["PydanticDeepAssistant", "PydanticDeepContext"]
