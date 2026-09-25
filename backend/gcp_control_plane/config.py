import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    project_id: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location: str = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    model: str = os.getenv("AGENT_MODEL", "gemini-2.5-flash")
    allowed_project_ids: tuple[str, ...] = tuple(
        value.strip()
        for value in os.getenv("ALLOWED_PROJECT_IDS", "").split(",")
        if value.strip()
    )
    max_prompt_chars: int = int(os.getenv("MAX_PROMPT_CHARS", "12000"))
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "180"))
    require_authenticated_identity: bool = os.getenv(
        "REQUIRE_AUTHENTICATED_IDENTITY", "false"
    ).lower() == "true"


settings = Settings()
