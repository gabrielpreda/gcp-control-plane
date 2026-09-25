"""Refreshable, scoped ADC authentication for Google-managed MCP servers."""

import threading
from typing import Any

import google.auth
from google.auth.transport.requests import Request

from .config import settings

_credentials_by_scope: dict[tuple[str, ...], Any] = {}
_credentials_lock = threading.Lock()


def _adc_credentials(scopes: tuple[str, ...]) -> Any:
    credentials = _credentials_by_scope.get(scopes)
    if credentials is None:
        with _credentials_lock:
            credentials = _credentials_by_scope.get(scopes)
            if credentials is None:
                credentials, _ = google.auth.default(scopes=list(scopes))
                _credentials_by_scope[scopes] = credentials
    return credentials


def mcp_header_provider(scopes: tuple[str, ...]):
    """Create a refreshable header provider for one MCP product scope.

    Google-managed MCP servers authorize both the MCP call and the product
    operation. The product-specific OAuth scope avoids relying on the generic
    cloud-platform scope, and x-goog-user-project tells the MCP gateway which
    project owns the MCP access and quota.
    """

    normalized_scopes = tuple(sorted(set(scopes)))

    def provider(_context: Any) -> dict[str, str]:
        credentials = _adc_credentials(normalized_scopes)
        with _credentials_lock:
            if not credentials.valid or credentials.expired:
                credentials.refresh(Request())
            token = credentials.token
        if not token:
            raise RuntimeError("ADC did not provide an access token for MCP")
        headers = {"Authorization": f"Bearer {token}"}
        if settings.project_id:
            headers["x-goog-user-project"] = settings.project_id
        return headers

    return provider
