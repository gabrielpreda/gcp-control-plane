import logging
import asyncio
import json
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .agent import root_agent
from .config import settings
from .policy import check_request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_NAME = "gcp_control_plane"
session_service = InMemorySessionService()
runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
)
app = FastAPI(title="GCP Control Plane Assistant", version="0.1.0")


class QueryRequest(BaseModel):
    prompt: str = Field(min_length=1)
    user_id: str = Field(default="streamlit-user", min_length=1, max_length=200)
    session_id: str | None = Field(default=None, max_length=200)


class QueryResponse(BaseModel):
    request_id: str
    answer: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    decision = check_request(request.prompt)
    request_id = str(uuid.uuid4())
    logger.info("request_id=%s policy_allowed=%s reason=%s", request_id, decision.allowed, decision.reason)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    session_id = request.session_id or str(uuid.uuid4())
    try:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=request.user_id,
            session_id=session_id,
        )
    except Exception:
        # The session may already exist; the runner can continue using it.
        pass

    message = types.Content(
        role="user",
        parts=[types.Part(text=request.prompt)],
    )

    async def collect_response() -> tuple[list[str], list[dict[str, Any]]]:
        response_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        async for event in runner.run_async(
            user_id=request.user_id,
            session_id=session_id,
            new_message=message,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    function_call = getattr(part, "function_call", None)
                    if function_call is not None:
                        tool_calls.append(
                            {
                                "type": "tool_call",
                                "tool": getattr(function_call, "name", None),
                                "id": getattr(function_call, "id", None),
                                "arguments": _safe_json_value(
                                    getattr(function_call, "args", {})
                                ),
                                "author": getattr(event, "author", None),
                            }
                        )

                    function_response = getattr(part, "function_response", None)
                    if function_response is not None:
                        tool_calls.append(
                            {
                                "type": "tool_response",
                                "tool": getattr(function_response, "name", None),
                                "id": getattr(function_response, "id", None),
                                "response": _safe_json_value(
                                    getattr(function_response, "response", {})
                                ),
                                "author": getattr(event, "author", None),
                            }
                        )

                    if getattr(part, "text", None):
                        response_parts.append(part.text)
        return response_parts, tool_calls

    try:
        response_parts, tool_calls = await asyncio.wait_for(
            collect_response(), timeout=settings.request_timeout_seconds
        )
    except asyncio.TimeoutError as exc:
        logger.warning("request_id=%s timed_out=true", request_id)
        raise HTTPException(status_code=504, detail="The agent request timed out.") from exc

    answer = "\n".join(response_parts).strip()
    if not answer:
        answer = "The agent did not return a textual answer. Check the service logs."
    logger.info("request_id=%s completed", request_id)
    return QueryResponse(request_id=request_id, answer=answer, tool_calls=tool_calls)


def _safe_json_value(value: Any, max_chars: int = 12000) -> Any:
    """Return JSON-compatible, bounded tool telemetry for the demo UI."""
    try:
        serialized = json.dumps(value, default=str)
        if len(serialized) > max_chars:
            serialized = serialized[:max_chars] + "... [truncated]"
        return json.loads(serialized)
    except (TypeError, ValueError):
        return str(value)[:max_chars]
