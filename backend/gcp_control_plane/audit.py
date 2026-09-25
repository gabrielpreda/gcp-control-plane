"""Structured audit events for Cloud Logging."""

import json
from datetime import datetime, timezone
from typing import Any

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
