"""Helpers for returning JSON-compatible MCP tool results."""

from datetime import date, datetime, time
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    """Recursively convert common YAML/Python values to JSON-safe values."""
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value
