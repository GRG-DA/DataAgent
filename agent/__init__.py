"""
Agent Package
LLM-powered analytical agent with tools
"""

from .llm_client import LLMClient, MultiProviderConfig, ProviderType, OllamaConfig, OpenAIConfig, AnthropicConfig, LMStudioConfig, OpenRouterConfig, OpencodeConfig, OpencodeProvider, Message, CompletionResponse, quick_generate, quick_chat
from .tools_sql import SQLTool, SQLResult, SQLToolOpenAI
from .tools_pandas import PandasTool, PandasResult, PandasToolOpenAI
from .tools_viz import VizTool, VizResult, VizToolOpenAI
from .planner import Planner, PlanStep, ExecutionPlan, ToolType, create_planner
from .orchestrator import Orchestrator, AgentResponse, StepResult, AgentState, create_orchestrator
from .schema_intelligence import (
    SchemaProfile, classify_schema, classify_schema_from_context,
    format_schema_for_prompt,
)

# Backward compatibility alias
OllamaClient = LLMClient

__all__ = [
    "LLMClient",
    "MultiProviderConfig",
    "ProviderType",
    "OllamaClient",
    "OllamaConfig",
    "OpenAIConfig",
    "AnthropicConfig",
    "LMStudioConfig",
    "OpenRouterConfig",
    "OpencodeConfig",
    "OpencodeProvider",
    "Message",
    "CompletionResponse",
    "quick_generate",
    "quick_chat",
    "SQLTool",
    "SQLResult",
    "SQLToolOpenAI",
    "PandasTool",
    "PandasResult",
    "PandasToolOpenAI",
    "VizTool",
    "VizResult",
    "VizToolOpenAI",
    "Planner",
    "PlanStep",
    "ExecutionPlan",
    "ToolType",
    "create_planner",
    "Orchestrator",
    "AgentResponse",
    "StepResult",
    "AgentState",
    "create_orchestrator",
    "SchemaProfile",
    "classify_schema",
    "classify_schema_from_context",
    "format_schema_for_prompt",
]