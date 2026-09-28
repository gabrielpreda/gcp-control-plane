"""Structured audit events for Cloud Logging."""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

_audit_context: ContextVar[dict[str, str]] = ContextVar("audit_context", default={})

@contextmanager
def audit_context(**fields: str) -> Iterator[None]:
    token = _audit_context.set(fields)
    try:
        yield
    finally:
        _audit_context.reset(token)

def current_audit_context() -> dict[str, str]:
    return dict(_audit_context.get())

def emit_audit(
    event: str,
    *,
    request_id: str,
    correlation_id: str,
    **fields: Any,
) -> None:
    """Write a bounded structured audit record to stdout/Cloud Logging.

    Cloud Run captures stdout in Cloud Logging, making these records durable
    independently of the container lifecycle. Sensitive prompts, arguments,
    responses, and credentials are intentionally excluded by callers.
    """
    record = {
        "audit_event": event,
        "service": "gcp-control-plane-backend",
        "request_id": request_id,
        "correlation_id": correlation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    # Cloud Run's logging agent parses a JSON object written as one stdout line
    # into a durable structured Cloud Logging entry.
    print(json.dumps(record, separators=(",", ":"), default=str), flush=True)

def usage_from_event(event: Any) -> dict[str, int] | None:
    metadata = getattr(event, "usage_metadata", None)
    if metadata is None:
        metadata = getattr(getattr(event, "llm_response", None), "usage_metadata", None)
    if metadata is None:
        return None

    def number(*names: str) -> int:
        for name in names:
            value = getattr(metadata, name, None)
            if value is None and isinstance(metadata, dict):
                value = metadata.get(name)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass
        return 0

    input_tokens = number("prompt_token_count", "input_token_count", "prompt")
    reasoning_tokens = number("thoughts_token_count", "reasoning_token_count", "reasoning")
    output_tokens = number("candidates_token_count", "output_token_count", "completion")
    total_tokens = number("total_token_count", "total") or input_tokens + output_tokens
    if not (input_tokens or output_tokens or total_tokens):
        return None
    return {
        "input_tokens": input_tokens,
        "reasoning_tokens": reasoning_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
