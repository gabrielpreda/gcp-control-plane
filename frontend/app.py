import os
from typing import Any

import requests
import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2 import id_token

st.set_page_config(page_title="GCP Control Plane Assistant", page_icon="☁️")
st.title("GCP Control Plane Assistant")
st.caption("Read-only inspection of authorized Google Cloud resources")

backend_url = os.getenv("BACKEND_URL", "http://localhost:8080").rstrip("/")


def backend_headers(correlation_id: str) -> dict[str, str]:
    # Cloud Run IAM-protected services require an OIDC identity token whose
    # audience is the backend service URL.
    headers = {
        "Content-Type": "application/json",
        "X-Authenticated-User": authenticated_user(),
        "X-Correlation-ID": correlation_id,
    }
    if not backend_url.startswith("http://localhost"):
        token = id_token.fetch_id_token(Request(), backend_url)
        headers["Authorization"] = f"Bearer {token}"
    return headers


def authenticated_user() -> str:
    """Read the identity supplied by IAP or another identity-aware proxy."""
    context_headers = getattr(st.context, "headers", {})
    identity = (
        context_headers.get("X-Goog-Authenticated-User-Email")
        or context_headers.get("X-Authenticated-User")
        or context_headers.get("X-Forwarded-User")
    )
    if identity:
        return identity
    return "local-user"


def render_tool_calls(tool_calls: list[dict[str, Any]]) -> None:
    if not tool_calls:
        return

    with st.expander(f"🔧 Tool activity ({len(tool_calls)} events)", expanded=False):
        for index, tool_event in enumerate(tool_calls, start=1):
            event_type = tool_event.get("type", "tool_event")
            tool_name = tool_event.get("tool") or "unknown tool"
            label = "📤 Call" if event_type == "tool_call" else "📥 Response"
            with st.container(border=True):
                st.markdown(f"**{index}. {label}: `{tool_name}`**")
                payload_key = "arguments" if event_type == "tool_call" else "response"
                payload = tool_event.get(payload_key, {})
                st.json(payload)


if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    # Keep one ADK conversation per browser session. The backend uses this ID
    # to restore the conversation context between Streamlit requests.
    import uuid

    st.session_state.session_id = str(uuid.uuid4())

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_tool_calls(message.get("tool_calls", []))

prompt = st.chat_input("Ask about buckets, VMs, BigQuery, or GCP projects...")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Inspecting Google Cloud..."):
            try:
                response = requests.post(
                    f"{backend_url}/query",
                    headers=backend_headers(st.session_state.session_id),
                    json={
                        "prompt": prompt,
                        "session_id": st.session_state.session_id,
                    },
                    timeout=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "180")),
                )
                response.raise_for_status()
                response_data = response.json()
                answer = response_data["answer"]
                tool_calls = response_data.get("tool_calls", [])
            except requests.HTTPError as exc:
                answer = f"Request rejected by the backend: {exc.response.text}"
                tool_calls = []
            except Exception as exc:
                answer = f"The backend could not be reached: {exc}"
                tool_calls = []
            st.markdown(answer)
            render_tool_calls(tool_calls)
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "tool_calls": tool_calls,
                }
            )
