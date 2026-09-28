"""ADK Web entry point.

Run `adk web backend` from the repository root, or use the equivalent command
for the installed ADK version.
"""

# ADK loads this file as ``backend.agent`` when invoked from the repository
# root. The application package lives one directory below it, so make that
# directory importable without requiring users to set PYTHONPATH manually.
import sys
from pathlib import Path

backend_dir = str(Path(__file__).resolve().parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from gcp_control_plane.agent import root_agent

__all__ = ["root_agent"]
