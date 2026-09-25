import logging
import asyncio
import json
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest, Response
from pydantic import BaseModel, Field
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .agent import root_agent
from .audit import emit_audit
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
    session_id: str | None = Field(default=None, max_length=200)


class QueryResponse(BaseModel):
    request_id: str
    correlation_id: str
    answer: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    http_request: FastAPIRequest,
    response: Response,
) -> QueryResponse:
    request_id = str(uuid.uuid4())
    correlation_id = _correlation_id(http_request, request_id)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Correlation-ID"] = correlation_id
    emit_audit(
        "request_received",
        request_id=request_id,
        correlation_id=correlation_id,
        session_id=request.session_id,
    )
    decision = check_request(request.prompt)
    logger.info("request_id=%s policy_allowed=%s reason=%s", request_id, decision.allowed, decision.reason)
    emit_audit(
        "policy_decision",
        request_id=request_id,
        correlation_id=correlation_id,
        allowed=decision.allowed,
        reason=decision.reason,
    )
    if not decision.allowed:
        emit_audit(
            "request_rejected",
            request_id=request_id,
            correlation_id=correlation_id,
            reason=decision.reason,
        )
        raise HTTPException(status_code=403, detail=decision.reason)

    try:
        user_id = _authenticated_user(http_request)
    except HTTPException as exc:
        emit_audit(
            "identity_rejected",
            request_id=request_id,
            correlation_id=correlation_id,
            status_code=exc.status_code,
        )
        raise

    session_id = request.session_id or str(uuid.uuid4())
    emit_audit(
        "agent_started",
        request_id=request_id,
        correlation_id=correlation_id,
        user_id=user_id,
        session_id=session_id,
    )
    try:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
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
            user_id=user_id,
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
        emit_audit(
            "agent_timed_out",
            request_id=request_id,
            correlation_id=correlation_id,
            user_id=user_id,
            session_id=session_id,
        )
        raise HTTPException(status_code=504, detail="The agent request timed out.") from exc
    except Exception as exc:
        logger.exception("request_id=%s agent_failed=true", request_id)
        emit_audit(
            "agent_failed",
            request_id=request_id,
            correlation_id=correlation_id,
            user_id=user_id,
            session_id=session_id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="The agent request failed.") from exc

    answer = "\n".join(response_parts).strip()
    if not answer:
        answer = "The agent did not return a textual answer. Check the service logs."
    logger.info("request_id=%s completed", request_id)
    emit_audit(
        "agent_completed",
        request_id=request_id,
        correlation_id=correlation_id,
        user_id=user_id,
        session_id=session_id,
        tool_names=sorted(
            {
                str(event.get("tool"))
                for event in tool_calls
                if event.get("tool")
            }
        ),
        tool_event_count=len(tool_calls),
    )
    return QueryResponse(
        request_id=request_id,
        correlation_id=correlation_id,
        answer=answer,
        tool_calls=tool_calls,
    )


def _authenticated_user(request: FastAPIRequest) -> str:
    """Resolve the end-user identity propagated by IAP or the frontend.

    Cloud Run authenticates the frontend service account at the service
    boundary. The frontend separately forwards the end-user identity obtained
    from IAP or an equivalent identity-aware proxy.
    """
    identity = (
        request.headers.get("x-authenticated-user")
        or request.headers.get("x-goog-authenticated-user-email")
        or request.headers.get("x-forwarded-user")
    )
    if identity:
        if identity.startswith("accounts.google.com:"):
            identity = identity.split(":", 1)[1]
        return identity[:200]
    if settings.require_authenticated_identity:
        raise HTTPException(
            status_code=401,
            detail="An authenticated end-user identity is required.",
        )
    return "local-user"


def _correlation_id(request: FastAPIRequest, fallback: str) -> str:
    """Accept a bounded caller correlation ID without trusting arbitrary data."""
    candidate = request.headers.get("x-correlation-id", "").strip()
    if candidate and len(candidate) <= 128 and all(
        character.isalnum() or character in "-_.:"
        for character in candidate
    ):
        return candidate
    return fallback


def _safe_json_value(value: Any, max_chars: int = 12000) -> Any:
    """Return JSON-compatible, bounded tool telemetry for the demo UI."""
    try:
        serialized = json.dumps(value, default=str)
        if len(serialized) > max_chars:
            serialized = serialized[:max_chars] + "... [truncated]"
        return json.loads(serialized)
    except (TypeError, ValueError):
        return str(value)[:max_chars]
