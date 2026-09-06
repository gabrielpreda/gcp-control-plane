from dataclasses import dataclass
import re

from .config import settings


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


def check_request(prompt: str) -> PolicyDecision:
    """Application-level guardrails for the read-only prototype.

    This is intentionally conservative. It is not a replacement for Google
    Cloud IAM and must not be weakened to make the model more convenient.
    """
    if not prompt or not prompt.strip():
        return PolicyDecision(False, "The request is empty.")
    if len(prompt) > settings.max_prompt_chars:
        return PolicyDecision(False, "The request is longer than the configured limit.")

    lowered = prompt.lower()
    mutation_patterns = (
        r"\b(delete|destroy|drop|truncate|restart|resize)\b",
        r"\b(stop|start)\s+(the\s+)?(vm|instance|server)\b",
        r"\b(remove|create|alter|update|insert|merge)\s+(bucket|table|dataset|object|iam|role|policy)\b",
        r"\b(grant|revoke)\s+.*\b(owner|editor|iam|permission|role)\b",
    )
    if any(re.search(pattern, lowered) for pattern in mutation_patterns):
        return PolicyDecision(
            False,
            "This prototype is read-only. The request appears to ask for a mutation.",
        )

    # This is an application-level check for explicit project references. IAM
    # remains authoritative, but an agent must not be allowed to intentionally
    # target a project outside the configured workshop scope.
    project_ids = re.findall(
        r"\bprojects?\s*(?:id\s*)?(?:is|:)?\s*([a-z][a-z0-9-]{4,28}[a-z0-9])\b",
        lowered,
    )
    if settings.allowed_project_ids:
        allowed = {project_id.lower() for project_id in settings.allowed_project_ids}
        unexpected = sorted(set(project_ids) - allowed)
        if unexpected:
            return PolicyDecision(
                False,
                "The request references a project outside the configured allowlist.",
            )
    return PolicyDecision(True, "Read-only request accepted.")
