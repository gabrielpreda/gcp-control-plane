import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2 import id_token

st.set_page_config(page_title="GCP Control Plane Assistant", page_icon="☁️", layout="wide")
st.markdown("""
<style>
.model-armor-alert { background: #fff3cd; border-left: 5px solid #d97706;
  border-radius: .35rem; padding: .8rem 1rem; color: #713f12; }
.application-guard-alert { background: #fee2e2; border-left: 5px solid #dc2626;
  border-radius: .35rem; padding: .8rem 1rem; color: #7f1d1d; }
.status-ok { color: #15803d; font-weight: 700; }
.status-error { color: #b91c1c; font-weight: 700; }
.status-disabled { color: #6b7280; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

backend_url = os.getenv("BACKEND_URL", "http://localhost:8080").rstrip("/")


def authenticated_user() -> str:
    context_headers = getattr(st.context, "headers", {})
    identity = (
        context_headers.get("X-Goog-Authenticated-User-Email")
        or context_headers.get("X-Authenticated-User")
        or context_headers.get("X-Forwarded-User")
    )
    return identity or "local-user"


def backend_headers(correlation_id: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Authenticated-User": authenticated_user(),
        "X-Correlation-ID": correlation_id,
    }
    if not backend_url.startswith("http://localhost"):
        token = id_token.fetch_id_token(Request(), backend_url)
        headers["Authorization"] = f"Bearer {token}"
    return headers


def display_payload(label: str, payload: Any) -> None:
    with st.expander(label, expanded=False):
        st.json(payload if payload is not None else {})


def render_tool_runs(tool_runs: list[dict[str, Any]], *, live: bool = False) -> None:
    if not tool_runs:
        return
    title = f"🔧 Tool activity · {len(tool_runs)} call(s)"
    with st.expander(title, expanded=live):
        for index, item in enumerate(tool_runs, start=1):
            state = item.get("status", "running")
            icon = {"running": "⏳", "completed": "✅", "blocked": "🛑", "error": "❌"}.get(state, "•")
            elapsed = item.get("duration_ms")
            timing = f" · {elapsed:,.0f} ms" if isinstance(elapsed, (int, float)) else ""
            with st.container(border=True):
                st.markdown(f"**{icon} {index}. `{item.get('tool', 'unknown tool')}`** · {state}{timing}")
                if item.get("author"):
                    st.caption(f"Agent: {item['author']}")
                display_payload("Input arguments", item.get("arguments", {}))
                if state in {"completed", "blocked", "error"}:
                    display_payload("Tool output", item.get("response", item.get("error", {})))


def render_usage(usage: dict[str, Any]) -> None:
    if not usage:
        return
    st.caption(
        f"Model: {usage.get('model_name', 'unknown')} ({usage.get('model_version', 'unknown')}) · "
        f"Tokens — input {usage.get('input_tokens', 0):,}, "
        f"reasoning {usage.get('reasoning_tokens', 0):,}, "
        f"output {usage.get('output_tokens', 0):,}, "
        f"total {usage.get('total_tokens', 0):,} · "
        f"model call {usage.get('call_time_ms', 0):,.0f} ms"
    )


def render_block_notice(block: dict[str, Any] | None) -> None:
    if not block:
        return
    if block.get("code") == "APPLICATION_POLICY_BLOCKED":
        st.markdown(
            f'<div class="application-guard-alert">🛑 <b>Guard blocked the request</b><br>{block.get("message", "The application guard blocked the request.")}</div>',
            unsafe_allow_html=True,
        )
    elif block.get("code") in {"MODEL_ARMOR_BLOCKED", "MODEL_ARMOR_UNAVAILABLE"}:
        st.markdown(
            f'<div class="model-armor-alert">🛡️ <b>Model Armor {block.get("stage", "screening")}</b><br>{block.get("message", "The content was blocked by Model Armor.")}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.error(block.get("message", "The request failed."))


def _read_sse(response):
    event_name = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line:
            if data_lines:
                try:
                    yield event_name, json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    yield "error", {"message": "The backend sent an invalid stream event."}
            event_name, data_lines = "message", []
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            yield event_name, json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            yield "error", {"message": "The backend sent an invalid stream event."}


def _upsert_tool_run(tool_runs: list[dict[str, Any]], event: dict[str, Any]) -> None:
    call_id = event.get("id")
    tool_name = event.get("tool")
    index = next(
        (i for i, item in enumerate(tool_runs)
         if (call_id and item.get("id") == call_id)
         or (not call_id and item.get("tool") == tool_name and item.get("status") == "running")),
        None,
    )
    if event.get("type") == "tool_call":
        item = {**event, "status": "running"}
        if index is None:
            tool_runs.append(item)
        else:
            tool_runs[index].update(item)
    elif index is not None:
        tool_runs[index].update({
            "status": "blocked" if isinstance(event.get("response"), dict)
            and event["response"].get("blocked_by") == "application_tool_guard"
            else "completed",
            "response": event.get("response"),
            "duration_ms": event.get("duration_ms"),
            "author": event.get("author") or tool_runs[index].get("author"),
        })
    else:
        tool_runs.append({**event, "status": "completed"})


def show_assistant() -> None:
    st.title("GCP Control Plane Assistant")
    st.caption("Read-only inspection of authorized Google Cloud resources")
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                if message.get("block"):
                    render_block_notice(message["block"])
                else:
                    st.markdown(message.get("content", ""))
                render_tool_runs(message.get("tool_runs", []))
                render_usage(message.get("usage", {}))
            else:
                st.markdown(message["content"])

    prompt = st.chat_input("Ask about buckets, VMs, BigQuery, or GCP projects...")
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        answer_placeholder = st.empty()
        activity_placeholder = st.empty()
        tool_runs: list[dict[str, Any]] = []
        answer = ""
        usage: dict[str, Any] = {}
        block: dict[str, Any] | None = None
        error_message: str | None = None
        try:
            with requests.post(
                f"{backend_url}/query/stream",
                headers=backend_headers(st.session_state.session_id),
                json={"prompt": prompt, "session_id": st.session_state.session_id},
                timeout=(10, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "240"))),
                stream=True,
            ) as response:
                if response.status_code >= 400:
                    try:
                        detail = response.json().get("detail", {})
                    except ValueError:
                        detail = {}
                    block = detail if isinstance(detail, dict) else None
                    error_message = (
                        detail.get("message") if isinstance(detail, dict)
                        else str(detail or response.text)
                    )
                else:
                    for event_name, payload in _read_sse(response):
                        if event_name == "tool_started":
                            _upsert_tool_run(tool_runs, payload)
                            with activity_placeholder.container():
                                render_tool_runs(tool_runs, live=True)
                        elif event_name == "tool_completed":
                            _upsert_tool_run(tool_runs, payload)
                            with activity_placeholder.container():
                                render_tool_runs(tool_runs, live=True)
                        elif event_name == "done":
                            answer = payload.get("answer", "")
                            usage = payload.get("usage", {})
                            block = next(
                                (item for item in payload.get("guard_results", [])), None
                            )
                            if block:
                                block = {"code": "APPLICATION_POLICY_BLOCKED",
                                         "message": block.get("reason", "A tool call was blocked.")}
                            for item in payload.get("tool_calls", []):
                                _upsert_tool_run(tool_runs, item)
                        elif event_name == "error":
                            block = payload if payload.get("code") in {
                                "APPLICATION_POLICY_BLOCKED", "MODEL_ARMOR_BLOCKED",
                                "MODEL_ARMOR_UNAVAILABLE",
                            } else None
                            error_message = payload.get("message", "The agent request failed.")
        except requests.RequestException as exc:
            error_message = f"The backend could not be reached: {exc}"
        except Exception as exc:
            error_message = f"The response stream ended unexpectedly: {exc}"

        if block:
            render_block_notice(block)
        if error_message:
            if block:
                pass
            else:
                st.error(error_message)
            answer = error_message
        elif answer:
            answer_placeholder.markdown(answer)
        with activity_placeholder.container():
            render_tool_runs(tool_runs)
        render_usage(usage)
        st.session_state.messages.append({
            "role": "assistant", "content": answer,
            "tool_runs": tool_runs, "usage": usage, "block": block,
        })


def _status_icon(status: str) -> str:
    return {"ok": "✅", "error": "⚠️", "configured": "✅", "disabled": "⏸️"}.get(status, "ℹ️")


def show_overview() -> None:
    st.title("Cloud overview")
    st.caption("Read-only service, resource, MCP, and safety configuration status")
    refresh = st.button("↻ Refresh status", type="primary")
    correlation_id = str(uuid.uuid4())
    try:
        response = requests.get(
            f"{backend_url}/overview",
            headers=backend_headers(correlation_id),
            params={"refresh": "true"} if refresh else None,
            timeout=(10, 45),
        )
        response.raise_for_status()
        data = response.json()
        st.session_state.overview_data = data
    except requests.RequestException as exc:
        if "overview_data" not in st.session_state:
            st.error(f"Could not load the cloud overview: {exc}")
            return
        st.warning(f"Refresh failed; showing the previous snapshot. {exc}")

    data = st.session_state.overview_data
    refreshed = datetime.fromtimestamp(data["refreshed_at"], timezone.utc).astimezone()
    st.caption(
        f"Project `{data.get('project_id') or 'not configured'}` · "
        f"Model `{data.get('model')}` at `{data.get('model_location')}` · "
        f"Refreshed {refreshed:%Y-%m-%d %H:%M:%S %Z}"
    )

    st.subheader("Google Cloud resources")
    columns = st.columns(2)
    for index, card in enumerate(data.get("resources", [])):
        with columns[index % 2]:
            with st.container(border=True):
                st.markdown(f"### {_status_icon(card['status'])} {card['name']}")
                if card["status"] == "ok":
                    st.metric("Inventory", card.get("summary", "Available"))
                    if card.get("states"):
                        st.caption("VM states: " + " · ".join(
                            f"{state}: {count}" for state, count in sorted(card["states"].items())
                        ))
                    if card.get("instances"):
                        with st.expander("Show virtual machines"):
                            st.dataframe(
                                card["instances"],
                                use_container_width=True,
                                hide_index=True,
                            )
                    elif card["name"] == "Compute Engine":
                        st.caption("No VMs found.")
                    if card.get("buckets"):
                        with st.expander("Show buckets"):
                            st.dataframe(
                                card["buckets"],
                                use_container_width=True,
                                hide_index=True,
                            )
                    if card.get("unreachable_zones"):
                        st.warning(
                            "Some zones could not be inventoried: "
                            + ", ".join(card["unreachable_zones"])
                        )
                    if card.get("has_more"):
                        st.caption("More resources exist than shown in this summary.")
                    if card.get("dataset_ids"):
                        with st.expander("Show dataset IDs"):
                            for dataset_id in card["dataset_ids"]:
                                st.code(dataset_id, language=None)
                    if card.get("services"):
                        with st.expander("Show Cloud Run services"):
                            st.dataframe(
                                card["services"],
                                use_container_width=True,
                                hide_index=True,
                            )
                    if card.get("region"):
                        st.caption(f"Region: {card['region']}")
                else:
                    st.error(card.get("message", "Status check failed."))

    st.subheader("MCP servers and available tools")
    st.caption("Tool lists are discovered from each server during refresh; availability is scoped to the backend identity.")
    mcp_columns = st.columns(2)
    for index, server in enumerate(data.get("mcp_servers", [])):
        with mcp_columns[index % 2]:
            with st.container(border=True):
                st.markdown(f"### {_status_icon(server['status'])} {server['name']}")
                st.caption(server["url"])
                if server["status"] == "ok":
                    st.metric("Tools available", len(server.get("tools", [])))
                    st.caption(f"Discovery took {server.get('duration_ms', 0):,.0f} ms")
                    with st.expander("Show tool names"):
                        for name in server.get("tools", []):
                            st.code(name, language=None)
                else:
                    st.error(server.get("message", "Tool discovery failed."))

    armor = data.get("model_armor", {})
    st.subheader("Model Armor")
    with st.container(border=True):
        st.markdown(
            f"### {_status_icon(armor.get('status', 'unknown'))} "
            f"{'Enabled' if armor.get('enabled') else 'Disabled'}"
        )
        left, right = st.columns(2)
        with left:
            st.write(f"**Template:** `{armor.get('template') or 'not configured'}`")
            st.write(f"**Location:** `{armor.get('location', 'unknown')}`")
        with right:
            st.write(f"**Fail closed:** {'Yes' if armor.get('fail_closed') else 'No'}")
            st.write(f"**Reachability:** {armor.get('reachability', 'not checked')}")
        st.caption("Configured state is shown from backend settings; template reachability is not probed by this dashboard.")


with st.sidebar:
    st.title("GCP Control Plane")
    page = st.radio("Workspace", ["Assistant", "Cloud overview"], label_visibility="collapsed")

if page == "Assistant":
    show_assistant()
else:
    show_overview()
