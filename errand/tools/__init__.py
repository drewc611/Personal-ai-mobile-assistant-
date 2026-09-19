"""Importing this package registers every tool exactly once.

The registry refuses a duplicate name, so this is also the list of what Errand
can do. If a capability is not imported here, the planner cannot reach it.
"""

from errand.tools import browser, calendar, connections, gmail, search, tasks  # noqa: F401

__all__ = ["browser", "calendar", "connections", "gmail", "search", "tasks"]
