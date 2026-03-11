"""FastAPI application entry point for AWS AgentCore."""

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent import stream_agent_response
from app.auth import _verify_token
from app.rag import load_vectorstore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Suppress noisy third-party loggers that inflate CloudWatch costs
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load FAISS in a background thread so uvicorn starts accepting /ping
    # health checks immediately instead of blocking during startup.
    def _load():
        logger.info("Loading FAISS vector store...")
        load_vectorstore()
        logger.info("Vector store ready.")

    threading.Thread(target=_load, daemon=True).start()
    yield


app = FastAPI(
    title="AI Stock Agent",
    description="AWS AgentCore — Amazon stock price and financial report assistant",
    version="1.0.0",
    lifespan=lifespan,
)


class QueryRequest(BaseModel):
    query: str
    token: str | None = None  # JWT forwarded by AgentCore invoke_agent_runtime


def _stream_response(request: QueryRequest, http_request: Request):
    """Shared streaming logic for /invocations and /query."""
    auth_header = http_request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    elif request.token:
        token = request.token
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    current_user = _verify_token(token)
    user_id = current_user.get("sub") or current_user.get("username")
    session_id = str(uuid.uuid4())

    logger.info(f"Query from user={user_id}: {request.query}")

    async def event_generator():
        async for chunk in stream_agent_response(
            query=request.query,
            user_id=user_id,
            session_id=session_id,
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── AgentCore required endpoints ─────────────────────────────────────────────

@app.get("/ping")
async def ping():
    """AgentCore health check endpoint (required, port 8080)."""
    return {"status": "Healthy", "time_of_last_update": int(time.time())}


@app.post("/invocations")
async def invocations(request: QueryRequest, http_request: Request):
    """AgentCore main invocation endpoint (required, port 8080)."""
    return _stream_response(request, http_request)


# ── Direct HTTP convenience endpoints ────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/query")
async def query(request: QueryRequest, http_request: Request):
    return _stream_response(request, http_request)
