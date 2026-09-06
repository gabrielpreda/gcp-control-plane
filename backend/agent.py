"""ADK Web entry point.

Run `adk web backend` from the repository root, or use the equivalent command
for the installed ADK version.
"""

from gcp_control_plane.agent import root_agent

__all__ = ["root_agent"]
