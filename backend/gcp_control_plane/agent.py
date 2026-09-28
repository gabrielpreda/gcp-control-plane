import logging

from google.adk.agents import Agent
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from google.adk.tools.mcp_tool.mcp_toolset import (
    MCPToolset,
    StreamableHTTPConnectionParams,
)
from google.adk.tools.agent_tool import AgentTool

from .config import settings
from .mcp_auth import mcp_header_provider
from .policy import check_request, check_tool_call
from .audit import current_audit_context, emit_audit
from .model_armor import ModelArmorError, model_armor

logger = logging.getLogger(__name__)


def guard_tool_call(tool, args, tool_context):
    tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", "unknown")
    decision = check_tool_call(str(tool_name), args)
    context = current_audit_context()
    if context.get("request_id"):
        emit_audit("tool_guard_decision", request_id=context["request_id"],
                   correlation_id=context.get("correlation_id", context["request_id"]),
                   tool_name=str(tool_name), allowed=decision.allowed, reason=decision.reason)
    if not decision.allowed:
        return {"error": decision.reason, "blocked_by": "application_tool_guard"}
    return None


def _model_text(content) -> str:
    return "\n".join(
        part.text for part in (getattr(content, "parts", None) or [])
        if getattr(part, "text", None)
    )


def _armor_block(message: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text=f"🛡️ MODEL ARMOR BLOCKED\n\n{message}")],
        )
    )


def _content_block(prefix: str, message: str) -> types.Content:
    return types.Content(
        role="model",
        parts=[types.Part(text=f"{prefix}\n\n{message}")],
    )


def before_agent_guard(callback_context):
    """Apply the deterministic request policy before agent instructions run."""
    if current_audit_context().get("request_id"):
        return None
    prompt = _model_text(getattr(callback_context, "user_content", None))
    decision = check_request(prompt)
    if not decision.allowed:
        return _content_block("🛑 APPLICATION POLICY BLOCKED", decision.reason)
    return None


def before_agent_safety(callback_context):
    """Screen the raw ADK Web user message before model instructions are built."""
    if current_audit_context().get("request_id") or not model_armor.enabled:
        return None
    prompt = _model_text(getattr(callback_context, "user_content", None))
    try:
        model_armor.sanitize(prompt)
    except ModelArmorError:
        return _content_block("🛡️ MODEL ARMOR BLOCKED", "The prompt was blocked by the configured safety policy.")
    except Exception:
        if settings.model_armor_fail_closed:
            return _content_block("🛡️ MODEL ARMOR BLOCKED", "Safety screening is unavailable, so the request was stopped.")
    return None


def before_model_safety(callback_context, llm_request):
    # The API boundary performs typed input screening. Avoid screening twice;
    # this callback is what protects direct `adk web` runs.
    if current_audit_context().get("request_id") or not model_armor.enabled:
        return None
    prompt = "\n".join(_model_text(content) for content in (llm_request.contents or []))
    try:
        model_armor.sanitize(prompt)
    except ModelArmorError:
        return _armor_block("The prompt was blocked by the configured safety policy.")
    except Exception:
        if settings.model_armor_fail_closed:
            return _armor_block("Safety screening is unavailable, so the request was stopped.")
    return None


def after_model_safety(callback_context, llm_response):
    if current_audit_context().get("request_id") or not model_armor.enabled:
        return None
    try:
        model_armor.sanitize(_model_text(llm_response.content), response=True)
    except ModelArmorError:
        return _armor_block("The model response was blocked by the configured safety policy.")
    except Exception:
        if settings.model_armor_fail_closed:
            return _armor_block("Safety screening is unavailable, so the response was stopped.")
    return None


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
    before_tool_callback=guard_tool_call,
    before_agent_callback=[before_agent_guard, before_agent_safety],
    before_model_callback=before_model_safety,
    after_model_callback=after_model_safety,
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
            ("https://www.googleapis.com/auth/bigquery.readonly",),
        )
    ],
    before_tool_callback=guard_tool_call,
    before_agent_callback=[before_agent_guard, before_agent_safety],
    before_model_callback=before_model_safety,
    after_model_callback=after_model_safety,
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
    before_tool_callback=guard_tool_call,
    before_agent_callback=[before_agent_guard, before_agent_safety],
    before_model_callback=before_model_safety,
    after_model_callback=after_model_safety,
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
    before_tool_callback=guard_tool_call,
    before_agent_callback=[before_agent_guard, before_agent_safety],
    before_model_callback=before_model_safety,
    after_model_callback=after_model_safety,
)


root_agent = Agent(
    name="gcp_control_plane_agent",
    model=settings.model,
    description="Coordinates read-only inspection across four Google Cloud domains.",
    instruction=f"""
You are the read-only coordinator for Google Cloud inspection.

Delegate requests to the appropriate specialist worker:
- storage_specialist: Cloud Storage buckets and objects
- bigquery_specialist: BigQuery datasets, tables, and read-only SQL
- compute_specialist: Compute Engine VMs and related resources
- cloud_run_specialist: Cloud Run services, revisions, and jobs

The specialists are task workers, not conversational agents. Invoke every
relevant worker in the same turn. For a request covering multiple domains,
invoke all relevant workers (in parallel when possible), then combine their
results into one answer. Never ask the user to split a supported multi-domain
inventory request, and never transfer a worker back to the coordinator.

Do not answer Resource Manager, project hierarchy, Cloud Asset Inventory,
Monitoring, IAM, or other unsupported requests. Say that the capability is
currently disabled and do not call an unrelated specialist.

Treat "Cloud Engine" as a user shorthand or typo for Compute Engine and route
those requests to compute_specialist. Do not reject a supported request merely
because it contains the word "project", "my project", or "in the project";
those phrases normally identify the scope in which the supported resource
should be inspected. Reject only requests that actually ask for project
inventory, project hierarchy, or another unsupported Resource Manager task.

When a request concerns more than one supported domain, delegate to each
relevant specialist and combine their results. Treat words such as "project"
or "my project" as context, not as a Resource Manager request.

{COMMON_INSTRUCTIONS}
""",
    tools=[
        AgentTool(storage_agent),
        AgentTool(bigquery_agent),
        AgentTool(compute_agent),
        AgentTool(cloud_run_agent),
    ],
    before_tool_callback=guard_tool_call,
    before_agent_callback=[before_agent_guard, before_agent_safety],
    before_model_callback=before_model_safety,
    after_model_callback=after_model_safety,
)
