"""Read-only service and resource overview for the dashboard."""

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession

from .agent import MCP_SERVERS
from .compute_inventory import list_compute_instances
from .config import settings
from .model_armor import model_armor

_CACHE_TTL_SECONDS = 30
_overview_cache: dict[str, Any] = {}
_overview_cache_at = 0.0
_overview_lock = asyncio.Lock()


def _get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    with AuthorizedSession(credentials) as session:
        response = session.get(url, params=params, timeout=12)
        response.raise_for_status()
        return response.json()


def _error_message(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            error = response.json().get("error", {})
            return str(error.get("message") or response.reason)[:300]
        except Exception:
            return str(getattr(response, "reason", "Request failed"))[:300]
    return str(exc)[:300]


def _status_card(name: str, check) -> dict[str, Any]:
    try:
        data = check()
        return {"name": name, "status": "ok", **data}
    except Exception as exc:
        return {"name": name, "status": "error", "message": _error_message(exc)}


def _storage_summary() -> dict[str, Any]:
    buckets: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"project": settings.project_id, "maxResults": 1000}
        if page_token:
            params["pageToken"] = page_token
        payload = _get_json("https://storage.googleapis.com/storage/v1/b", params)
        buckets.extend(payload.get("items", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    bucket_rows = sorted(
        ({
            "name": str(bucket.get("name", "unknown")),
            "location": str(bucket.get("location", "")),
            "storage_class": str(bucket.get("storageClass", "")),
            "created": str(bucket.get("timeCreated", "")),
        } for bucket in buckets),
        key=lambda bucket: bucket["name"],
    )
    return {"summary": f"{len(bucket_rows)} buckets", "bucket_count": len(bucket_rows),
            "buckets": bucket_rows, "has_more": False}


def _compute_summary() -> dict[str, Any]:
    inventory = list_compute_instances()
    return {"summary": f"{inventory['instance_count']} VMs", **inventory,
            "has_more": False}


def _bigquery_summary() -> dict[str, Any]:
    project = quote(settings.project_id, safe="")
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets"
    datasets: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"maxResults": 1000}
        if page_token:
            params["pageToken"] = page_token
        # Omitting `all=true` follows the BigQuery UI default and excludes
        # hidden datasets (whose IDs begin with an underscore).
        payload = _get_json(url, params)
        datasets.extend(payload.get("datasets", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    dataset_ids = sorted(
        str(item.get("datasetReference", {}).get("datasetId"))
        for item in datasets
        if item.get("datasetReference", {}).get("datasetId")
    )
    return {"summary": f"{len(dataset_ids)} datasets", "dataset_count": len(dataset_ids),
            "dataset_ids": dataset_ids, "has_more": False}


def _cloud_run_summary() -> dict[str, Any]:
    project = quote(settings.project_id, safe="")
    region = quote(settings.cloud_run_region, safe="")
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/services"
    services: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"pageSize": 1000}
        if page_token:
            params["pageToken"] = page_token
        payload = _get_json(url, params)
        services.extend(payload.get("services", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    ready = 0
    service_rows: list[dict[str, str]] = []
    for service in services:
        condition = service.get("terminalCondition") or {}
        state = str(condition.get("state", ""))
        if state == "CONDITION_SUCCEEDED":
            ready += 1
            readiness = "Ready"
        elif state == "CONDITION_FAILED":
            readiness = "Failed"
        elif service.get("reconciling"):
            readiness = "Reconciling"
        elif service.get("latestReadyRevision"):
            # Cloud Run can omit the terminal condition for a healthy service;
            # latestReadyRevision is the serving revision in that case.
            readiness = "Ready"
            ready += 1
        else:
            readiness = "Unknown"
        service_rows.append({
            "name": str(service.get("name", "")).rsplit("/", 1)[-1],
            "status": readiness,
            "latest_ready_revision": str(service.get("latestReadyRevision", "")),
            "url": str(service.get("uri", "")),
        })
    service_rows.sort(key=lambda service: service["name"])
    return {
        "summary": f"{len(services)} services · {ready} ready in {settings.cloud_run_region}",
        "service_count": len(services), "ready_count": ready,
        "services": service_rows,
        "region": settings.cloud_run_region,
        "has_more": False,
    }


async def _discover_server(server: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": server["id"], "name": server["name"], "url": server["url"]
    }
    started = time.perf_counter()
    try:
        # Compute Engine and Cloud Run expose tools/list without authentication.
        # Omit the context for those public discovery calls so ADK doesn't ask
        # the metadata server for an access token just to list tool metadata.
        # Other configured MCP servers retain their authenticated discovery.
        context = None if server.get("public_tool_discovery") else SimpleNamespace()
        tools = await asyncio.wait_for(
            server["toolset"].get_tools(context), timeout=15
        )
        result.update({
            "status": "ok",
            "tools": sorted(str(getattr(tool, "name", "unknown")) for tool in tools),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        })
    except Exception as exc:
        result.update({
            "status": "error", "tools": [], "message": _error_message(exc),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        })
    return result


async def get_overview(*, force_refresh: bool = False) -> dict[str, Any]:
    global _overview_cache, _overview_cache_at
    async with _overview_lock:
        now = time.monotonic()
        if not force_refresh and _overview_cache and now - _overview_cache_at < _CACHE_TTL_SECONDS:
            return _overview_cache

        resource_cards = await asyncio.gather(
            asyncio.to_thread(_status_card, "Cloud Storage", _storage_summary),
            asyncio.to_thread(_status_card, "Compute Engine", _compute_summary),
            asyncio.to_thread(_status_card, "BigQuery", _bigquery_summary),
            asyncio.to_thread(_status_card, "Cloud Run", _cloud_run_summary),
        )
        mcp_servers = await asyncio.gather(
            *(_discover_server(server) for server in MCP_SERVERS)
        )
        armor = {
            "enabled": model_armor.enabled,
            "status": "configured" if model_armor.enabled else "disabled",
            "template": settings.model_armor_template or None,
            "location": settings.model_armor_location,
            "fail_closed": settings.model_armor_fail_closed,
            "reachability": "not checked",
        }
        _overview_cache = {
            "project_id": settings.project_id,
            "model": settings.model,
            "model_location": settings.location,
            "cloud_run_region": settings.cloud_run_region,
            "refreshed_at": time.time(),
            "resources": resource_cards,
            "mcp_servers": mcp_servers,
            "model_armor": armor,
        }
        _overview_cache_at = time.monotonic()
        return _overview_cache
