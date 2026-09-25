import logging

from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import (
    MCPToolset,
    StreamableHTTPConnectionParams,
)

from .config import settings
from .mcp_auth import mcp_header_provider

logger = logging.getLogger(__name__)


def _managed_mcp(url: str, scopes: tuple[str, ...]) -> MCPToolset:
    """Create one managed MCP connection with refreshable ADC auth."""
    return MCPToolset(
        connection_params=StreamableHTTPConnectionParams(url=url),
        header_provider=mcp_header_provider(scopes),
    )


COMMON_INSTRUCTIONS = f"""
You are a read-only Google Cloud operations specialist.

The configured project is {settings.project_id or '(not configured)'}.
You may inspect only these project IDs:
{', '.join(settings.allowed_project_ids) or '(no project allowlist configured)'}.

Never create, delete, update, stop, restart, resize, or otherwise mutate a
resource. Never change IAM, billing, networking, retention policies, or data.
Resolve the exact project, region, zone, dataset, table, bucket, service, or
instance before making a tool call. If the request is ambiguous, ask for
clarification. State which project and resources were inspected. Do not expose
credentials or access tokens.
"""


storage_agent = Agent(
    name="storage_specialist",
    model=settings.model,
    description="Inspects Cloud Storage buckets, objects, and bucket configuration.",
    instruction=COMMON_INSTRUCTIONS
    + "\nUse only Cloud Storage tools. Answer only Storage-related requests.",
    tools=[
        _managed_mcp(
            "https://storage.googleapis.com/storage/mcp",
            ("https://www.googleapis.com/auth/devstorage.read_only",),
        )
    ],
)

bigquery_agent = Agent(
    name="bigquery_specialist",
    model=settings.model,
    description="Inspects BigQuery datasets and tables and runs read-only SQL.",
    instruction=COMMON_INSTRUCTIONS
    + "\nUse only BigQuery tools. Use read-only SQL only. Answer only BigQuery-related requests.",
    tools=[
        _managed_mcp(
            "https://bigquery.googleapis.com/mcp",
            ("https://www.googleapis.com/auth/bigquery",),
        )
    ],
)

compute_agent = Agent(
    name="compute_specialist",
    model=settings.model,
    description="Inspects Compute Engine VMs and related compute resources.",
    instruction=COMMON_INSTRUCTIONS
    + "\nUse only Compute Engine tools. Answer only VM and Compute-related requests.",
    tools=[
        _managed_mcp(
            "https://compute.googleapis.com/mcp",
            ("https://www.googleapis.com/auth/compute.read-only",),
        )
    ],
)

cloud_run_agent = Agent(
    name="cloud_run_specialist",
    model=settings.model,
    description="Inspects Cloud Run services, revisions, jobs, and service IAM state.",
    instruction=COMMON_INSTRUCTIONS
    + "\nUse only Cloud Run tools. Answer only Cloud Run-related requests.",
    tools=[
        _managed_mcp(
            "https://run.googleapis.com/mcp",
            ("https://www.googleapis.com/auth/run.readonly",),
        )
    ],
)


root_agent = Agent(
    name="gcp_control_plane_agent",
    model=settings.model,
    description="Coordinates read-only inspection across four Google Cloud domains.",
    instruction=f"""
You are the read-only coordinator for Google Cloud inspection.

Delegate requests to the appropriate specialist sub-agent:
- storage_specialist: Cloud Storage buckets and objects
- bigquery_specialist: BigQuery datasets, tables, and read-only SQL
- compute_specialist: Compute Engine VMs and related resources
- cloud_run_specialist: Cloud Run services, revisions, and jobs

Do not answer Resource Manager, project hierarchy, Cloud Asset Inventory,
Monitoring, IAM, or other unsupported requests. Say that the capability is
currently disabled and do not call an unrelated specialist.

When a request concerns more than one supported domain, delegate to each
relevant specialist and combine their results. Treat words such as "project"
or "my project" as context, not as a Resource Manager request.

{COMMON_INSTRUCTIONS}
""",
    sub_agents=[storage_agent, bigquery_agent, compute_agent, cloud_run_agent],
)
