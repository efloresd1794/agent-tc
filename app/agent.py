"""LangGraph ReAct agent with stock tools and RAG knowledge base."""

import logging
from typing import AsyncIterator

from langchain_aws import ChatBedrock
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from langfuse.callback import CallbackHandler
from langgraph.prebuilt import create_react_agent

from app.config import settings
from app.rag import load_vectorstore, retrieve_from_knowledge_base
from app.tools import retrieve_historical_stock_price, retrieve_realtime_stock_price

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a financial research assistant specializing in Amazon (AMZN).

You have access to the following tools:
- retrieve_realtime_stock_price: get the current AMZN stock price
- retrieve_historical_stock_price: get historical daily prices for a date range
- retrieve_from_knowledge_base: search Amazon financial reports (2024 Annual Report, Q2/Q3 2025 Earnings)

Guidelines:
- Always use tools to fetch data before answering — do not guess prices or financial figures.
- When asked about analyst predictions, use the knowledge base to find relevant earnings guidance.
- Be concise and cite the source document when using the knowledge base.
- For date ranges, infer reasonable defaults if the user is vague (e.g. "Q4 last year" = Oct 1 – Dec 31 of prior year).
"""

TOOLS = [
    retrieve_realtime_stock_price,
    retrieve_historical_stock_price,
    retrieve_from_knowledge_base,
]


def _build_llm() -> ChatBedrock:
    return ChatBedrock(
        model_id=settings.bedrock_model_id,
        region_name=settings.aws_region,
        streaming=True,
    )


def _build_langfuse_handler(user_id: str | None = None, session_id: str | None = None) -> CallbackHandler:
    return CallbackHandler(
        secret_key=settings.langfuse_secret_key,
        public_key=settings.langfuse_public_key,
        host=settings.langfuse_host,
        user_id=user_id,
        session_id=session_id,
    )


def build_agent():
    """Build and return a compiled LangGraph ReAct agent."""
    llm = _build_llm()
    agent = create_react_agent(
        model=llm,
        tools=TOOLS,
        state_modifier=SYSTEM_PROMPT,
    )
    return agent


async def stream_agent_response(
    query: str,
    user_id: str | None = None,
    session_id: str | None = None,
) -> AsyncIterator[str]:
    """
    Stream agent responses using .astream() with stream_mode="messages".

    Yields LLM text tokens as they arrive and emits a [tool_call] line
    whenever a tool is invoked, filtered to LLM output only per:
    https://langchain-ai.github.io/langgraph/how-tos/streaming/#filter-by-llm-invocation
    """
    agent = build_agent()
    langfuse_handler = _build_langfuse_handler(user_id=user_id, session_id=session_id)

    input_messages = {"messages": [HumanMessage(content=query)]}
    config = {"callbacks": [langfuse_handler]}

    async for chunk, metadata in agent.astream(
        input_messages,
        stream_mode="messages",
        config=config,
    ):
        # Stream LLM text tokens (filter by LLM invocation)
        if isinstance(chunk, AIMessageChunk) and chunk.content:
            content = chunk.content
            # Bedrock returns content as a list of typed blocks
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        yield block["text"]
                    elif isinstance(block, str):
                        yield block
            else:
                yield content

        # Emit tool call notification after the tool completes
        elif isinstance(chunk, ToolMessage):
            yield f"\n[tool_call] {chunk.name}\n"
