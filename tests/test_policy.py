import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from gcp_control_plane.policy import check_request


def test_read_request_is_allowed():
    assert check_request("List the buckets in my project").allowed


def test_destructive_request_is_rejected():
    decision = check_request("Delete bucket temporary-export-123")
    assert not decision.allowed


def test_bigquery_mutation_is_rejected():
    assert not check_request("DROP TABLE analytics.events").allowed
