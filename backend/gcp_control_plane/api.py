import logging
import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest, Response
from pydantic import BaseModel, Field
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .agent import root_agent
from .audit import audit_context, emit_audit, usage_from_event
from .config import settings
from .policy import check_request
from .model_armor import ModelArmorError, model_armor

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
    usage: dict[str, Any] = Field(default_factory=lambda: {
        "model_name": settings.model,
        "model_version": settings.model_version,
        "input_tokens": 0,
        "reasoning_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "call_time_ms": 0,
    })
    safety_results: list[dict[str, Any]] = Field(default_factory=list)
    guard_results: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "model_armor": "enabled" if model_armor.enabled else "disabled",
    }


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
        raise HTTPException(status_code=403, detail={
            "code": "APPLICATION_POLICY_BLOCKED",
            "stage": "guard",
            "message": decision.reason,
        })

    safety_results: list[dict[str, Any]] = []
    try:
        safety_result = model_armor.sanitize(request.prompt)
        if safety_result:
            safety_results.append({"stage": "input", **safety_result})
    except ModelArmorError as exc:
        emit_audit("model_armor_blocked", request_id=request_id, correlation_id=correlation_id,
                   stage="input", error=str(exc))
        raise HTTPException(status_code=403, detail={
            "code": "MODEL_ARMOR_BLOCKED", "stage": "input",
            "message": "The request was blocked by the AI safety policy.",
        }) from exc
    except Exception as exc:
        emit_audit("model_armor_error", request_id=request_id, correlation_id=correlation_id,
                   stage="input", error_type=type(exc).__name__)
        if settings.model_armor_fail_closed:
            raise HTTPException(status_code=503, detail="AI safety screening is unavailable.") from exc

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

    async def collect_response() -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
        response_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        usage = {
            "model_name": settings.model,
            "model_version": settings.model_version,
            "input_tokens": 0,
            "reasoning_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "call_time_ms": 0,
        }
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
        ):
            event_usage = usage_from_event(event)
            if event_usage:
                for key in ("input_tokens", "reasoning_tokens", "output_tokens", "total_tokens"):
                    usage[key] += event_usage[key]
                event_model_version = (
                    getattr(event, "model_version", None)
                    or getattr(getattr(event, "llm_response", None), "model_version", None)
                    or settings.model_version
                )
                usage["model_version"] = str(event_model_version)
                emit_audit(
                    "llm_usage", request_id=request_id, correlation_id=correlation_id,
                    user_id=user_id, session_id=session_id,
                    model_name=settings.model, model_version=str(event_model_version),
                    call_time_ms=round((time.perf_counter() - call_started) * 1000, 2),
                    **event_usage,
                )
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
        return response_parts, tool_calls, usage

    call_started = time.perf_counter()
    try:
        with audit_context(request_id=request_id, correlation_id=correlation_id):
            response_parts, tool_calls, usage = await asyncio.wait_for(
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
            model_name=settings.model,
            model_version=settings.model_version,
            call_time_ms=round((time.perf_counter() - call_started) * 1000, 2),
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
            model_name=settings.model,
            model_version=settings.model_version,
            call_time_ms=round((time.perf_counter() - call_started) * 1000, 2),
        )
        raise HTTPException(status_code=502, detail="The agent request failed.") from exc

    answer = "\n".join(response_parts).strip()
    usage["call_time_ms"] = round((time.perf_counter() - call_started) * 1000, 2)
    if not answer:
        answer = "The agent did not return a textual answer. Check the service logs."
    try:
        safety_result = model_armor.sanitize(answer, response=True)
        if safety_result:
            safety_results.append({"stage": "output", **safety_result})
    except ModelArmorError as exc:
        emit_audit("model_armor_blocked", request_id=request_id, correlation_id=correlation_id,
                   stage="output", error=str(exc))
        raise HTTPException(status_code=502, detail={
            "code": "MODEL_ARMOR_BLOCKED", "stage": "output",
            "message": "The response was blocked by the AI safety policy.",
        }) from exc
    except Exception as exc:
        emit_audit("model_armor_error", request_id=request_id, correlation_id=correlation_id,
                   stage="output", error_type=type(exc).__name__)
        if settings.model_armor_fail_closed:
            raise HTTPException(status_code=503, detail="AI safety screening is unavailable.") from exc
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
        model_name=usage["model_name"],
        model_version=usage["model_version"],
        input_tokens=usage["input_tokens"],
        reasoning_tokens=usage["reasoning_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=usage["total_tokens"],
        call_time_ms=usage["call_time_ms"],
    )
    guard_results = [
        {
            "stage": "tool",
            "tool": event.get("tool"),
            "status": "blocked",
            "reason": event.get("response", {}).get("error"),
        }
        for event in tool_calls
        if event.get("type") == "tool_response"
        and isinstance(event.get("response"), dict)
        and event["response"].get("blocked_by") == "application_tool_guard"
    ]
    return QueryResponse(
        request_id=request_id,
        correlation_id=correlation_id,
        answer=answer,
        tool_calls=tool_calls,
        usage=usage,
        safety_results=safety_results,
        guard_results=guard_results,
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
