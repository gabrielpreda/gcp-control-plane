import logging
import google.auth
from google.auth.transport.requests import Request
from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import (
    MCPToolset,
    StreamableHTTPConnectionParams,
)

from .config import settings

logger = logging.getLogger(__name__)


def _access_token() -> str:
    """Get a token from ADC; never use a service-account key in the container."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    return credentials.token


def _managed_mcp(url: str) -> MCPToolset:
    return MCPToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url,
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
    )


# Keep toolset creation at import time, matching the existing prototypes.
# The deployment process must recycle revisions before the ADC token expires.
# Token refresh/reconnection will be made explicit before mutation support is
# introduced; this prototype remains read-only.
storage_mcp = _managed_mcp("https://storage.googleapis.com/storage/mcp")
bigquery_mcp = _managed_mcp("https://bigquery.googleapis.com/mcp")
compute_mcp = _managed_mcp("https://compute.googleapis.com/mcp")
resource_mcp = _managed_mcp("https://cloudresourcemanager.googleapis.com/mcp")

root_agent = Agent(
    name="gcp_control_plane_agent",
    model=settings.model,
    instruction=f"""
You are a read-only Google Cloud operations assistant.

Use the managed MCP tools to inspect Cloud Storage, BigQuery, Compute Engine,
and Resource Manager resources. You may inspect only these project IDs:
{', '.join(settings.allowed_project_ids) or '(no project allowlist configured)'}.

The configured application project is {settings.project_id or '(not configured)'}.
When the user says "current project", "this project", or "my project", use
that configured project if it is in the allowlist. Preserve project, bucket,
dataset, table, instance, region, and zone context from earlier conversation
turns when the user refers to a resource indirectly.

Rules:
- Never create, delete, update, stop, restart, resize, or mutate a resource.
- Never change IAM, billing, networking, retention policies, or BigQuery data.
- Resolve the exact project, region, zone, dataset, table, bucket, or instance
  before making a tool call.
- If the request is ambiguous, ask a clarification question.
- State which resources and projects were inspected.
- Do not expose credentials, access tokens, or unnecessary sensitive data.
- Prefer concise tables and explain operational implications.
""",
    tools=[storage_mcp, bigquery_mcp, compute_mcp, resource_mcp],
)
