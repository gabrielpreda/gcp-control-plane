import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from gcp_control_plane.policy import check_request, check_tool_call


def test_read_request_is_allowed():
    assert check_request("List the buckets in my project").allowed
    assert check_request("List Compute Engine instances in the project").allowed
    assert check_request("List Cloud Engine instances in my project").allowed


def test_project_context_is_not_an_unauthorized_project_reference():
    assert check_request("Show the buckets in my project").allowed
    assert check_request("Show the VMs in the project").allowed


def test_destructive_request_is_rejected():
    decision = check_request("Delete bucket temporary-export-123")
    assert not decision.allowed


def test_bigquery_mutation_is_rejected():
    assert not check_request("DROP TABLE analytics.events").allowed


def test_tool_guard_blocks_mutating_tool_and_sql():
    assert not check_tool_call("delete_bucket", {"name": "example"}).allowed
    assert not check_tool_call("query", {"query": "DELETE FROM t WHERE id = 1"}).allowed
    assert check_tool_call("query", {"query": "SELECT * FROM t"}).allowed
