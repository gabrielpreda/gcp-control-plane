"""Read-only Compute Engine inventory shared by chat and the overview."""

from typing import Any
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession

from .config import settings


def list_compute_instances() -> dict[str, Any]:
    """List VM name, status, zone, and machine type in the configured project."""
    project = quote(settings.project_id, safe="")
    url = f"https://compute.googleapis.com/compute/v1/projects/{project}/aggregated/instances"
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    instances: list[dict[str, Any]] = []
    unreachable: set[str] = set()
    page_token: str | None = None
    with AuthorizedSession(credentials) as session:
        while True:
            params: dict[str, Any] = {
                "returnPartialSuccess": "true",
                "maxResults": 500,
            }
            if page_token:
                params["pageToken"] = page_token
            response = session.get(url, params=params, timeout=12)
            response.raise_for_status()
            payload = response.json()
            instances.extend(
                instance
                for scope in payload.get("items", {}).values()
                for instance in scope.get("instances", [])
            )
            unreachable.update(str(zone) for zone in payload.get("unreachables", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

    vm_rows = [
        {
            "name": str(instance.get("name", "unknown")),
            "status": str(instance.get("status", "UNKNOWN")).upper(),
            "zone": str(instance.get("zone", "")).rsplit("/", 1)[-1],
            "machine_type": str(instance.get("machineType", "")).rsplit("/", 1)[-1],
        }
        for instance in instances
    ]
    vm_rows.sort(key=lambda vm: (vm["zone"], vm["name"]))
    states: dict[str, int] = {}
    for vm in vm_rows:
        states[vm["status"]] = states.get(vm["status"], 0) + 1
    return {
        "project_id": settings.project_id,
        "instance_count": len(vm_rows),
        "states": states,
        "instances": vm_rows,
        "unreachable_zones": sorted(unreachable),
    }
