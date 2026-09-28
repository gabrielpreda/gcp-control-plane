"""Optional Model Armor screening for the API boundary.

The application still relies on IAM and deterministic tool guards for
authorization. Model Armor is a content and prompt-injection control, not an
authorization system.
"""

import logging
import time
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.auth import default

from .config import settings

logger = logging.getLogger(__name__)


class ModelArmorError(RuntimeError):
    pass


class ModelArmor:
    def __init__(self) -> None:
        self.template = settings.model_armor_template.strip()
        self.location = settings.model_armor_location.strip() or "us"
        self.enabled = bool(self.template)
        self.session = None
        if self.enabled:
            credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            self.session = AuthorizedSession(credentials)
            logger.info(
                "Model Armor initialized enabled=true location=%s template=%s fail_closed=%s",
                self.location,
                self.template,
                settings.model_armor_fail_closed,
            )
        else:
            logger.warning(
                "Model Armor initialized enabled=false reason=MODEL_ARMOR_TEMPLATE_not_configured"
            )

    @staticmethod
    def _match_states(payload: Any) -> list[str]:
        states: list[str] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                normalized_key = str(key).replace("_", "").replace("-", "").lower()
                if normalized_key in {"filtermatchstate", "matchstate"}:
                    states.append(str(value).upper())
                states.extend(ModelArmor._match_states(value))
        elif isinstance(payload, list):
            for item in payload:
                states.extend(ModelArmor._match_states(item))
        return states

    @staticmethod
    def _is_blocked(payload: Any) -> bool:
        """Handle the camelCase fields returned by the sanitize REST API."""
        if isinstance(payload, dict):
            for key, value in payload.items():
                normalized_key = str(key).replace("_", "").replace("-", "").lower()
                normalized_value = str(value).upper()
                if normalized_key in {"filtermatchstate", "matchstate"} and normalized_value in {"MATCH", "MATCH_FOUND"}:
                    return True
                if normalized_key == "invocationresult" and normalized_value == "FAILURE":
                    return True
                if ModelArmor._is_blocked(value):
                    return True
        elif isinstance(payload, list):
            return any(ModelArmor._is_blocked(item) for item in payload)
        return False

    @staticmethod
    def _matched_filters(payload: Any, path: str = "") -> list[str]:
        filters: list[str] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                current_path = f"{path}.{key}" if path else str(key)
                normalized_key = str(key).replace("_", "").replace("-", "").lower()
                if normalized_key in {"filtermatchstate", "matchstate"} and str(value).upper() in {"MATCH", "MATCH_FOUND"}:
                    filters.append(path or current_path)
                filters.extend(ModelArmor._matched_filters(value, current_path))
        elif isinstance(payload, list):
            for index, item in enumerate(payload):
                filters.extend(ModelArmor._matched_filters(item, f"{path}[{index}]"))
        return filters

    def sanitize(self, text: str, *, response: bool = False) -> dict[str, Any] | None:
        if not self.enabled:
            return
        operation = "sanitizeModelResponse" if response else "sanitizeUserPrompt"
        base = f"https://modelarmor.{self.location}.rep.googleapis.com/v1"
        body = {"modelResponseData": {"text": text}} if response else {"userPromptData": {"text": text}}
        started = time.perf_counter()
        logger.info("Model Armor sanitize started operation=%s", operation)
        result = self.session.post(f"{base}/{self.template}:{operation}", json=body, timeout=15)
        if result.status_code >= 400:
            logger.error(
                "Model Armor sanitize failed operation=%s status_code=%s duration_ms=%.2f",
                operation, result.status_code, (time.perf_counter() - started) * 1000,
            )
            raise ModelArmorError(f"Model Armor returned HTTP {result.status_code}")
        payload: Any = result.json()
        blocked = self._is_blocked(payload)
        match_states = self._match_states(payload)
        result_payload = payload.get("sanitizationResult", payload) if isinstance(payload, dict) else payload
        invocation_result = result_payload.get("invocationResult") if isinstance(result_payload, dict) else None
        matched_filters = self._matched_filters(result_payload)
        logger.info(
            "Model Armor sanitize completed operation=%s status_code=%s blocked=%s "
            "filter_match_states=%s matched_filters=%s invocation_result=%s duration_ms=%.2f",
            operation, result.status_code, blocked, match_states, matched_filters,
            invocation_result,
            (time.perf_counter() - started) * 1000,
        )
        summary = {
            "provider": "model_armor",
            "operation": operation,
            "blocked": blocked,
            "filter_match_states": match_states,
            "matched_filters": matched_filters,
            "invocation_result": invocation_result,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        if blocked:
            raise ModelArmorError("Model Armor blocked the content.")
        return summary


model_armor = ModelArmor()
