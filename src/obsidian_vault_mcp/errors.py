"""Machine-readable, agent-actionable Notebook tool failures."""

import json
from pathlib import PurePosixPath
from typing import Any

from mcp.types import CallToolResult, TextContent


def _classification(message: str) -> tuple[str, bool]:
    lowered = message.lower()
    if "revision mismatch" in lowered:
        return "REVISION_CONFLICT", True
    if "not found" in lowered or "does not exist" in lowered:
        return "NOT_FOUND", True
    if "confirm=true" in lowered:
        return "CONFIRMATION_REQUIRED", True
    if "exactly once" in lowered or "found" in lowered and "times" in lowered:
        return "AMBIGUOUS_MATCH", True
    if "rate limit" in lowered:
        return "RATE_LIMITED", True
    if "path" in lowered or "directory" in lowered:
        return "INVALID_PATH", True
    if "required" in lowered or "unsupported" in lowered or "cannot be empty" in lowered:
        return "INVALID_ARGUMENT", True
    return "TOOL_ERROR", False


def _next_actions(tool_name: str, error_type: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    path = data.get("path")
    if error_type == "BOOTSTRAP_REQUIRED":
        return [
            {
                "tool": "vault_bootstrap",
                "arguments": {},
                "purpose": "Load the Notebook's organization, working rules, and privacy boundaries before using other tools.",
            }
        ]
    if error_type == "REVISION_CONFLICT" and path:
        return [
            {
                "tool": "vault_read",
                "arguments": {"path": path},
                "purpose": "Read the latest content and revision before retrying the write.",
            }
        ]
    if error_type == "NOT_FOUND":
        parent = str(PurePosixPath(path).parent) if path else ""
        if parent == ".":
            parent = ""
        return [
            {
                "tool": "vault_list",
                "arguments": {"path": parent, "depth": 1},
                "purpose": "Discover the correct vault-relative path.",
            }
        ]
    if error_type == "AMBIGUOUS_MATCH" and path:
        return [
            {
                "tool": "vault_read",
                "arguments": {"path": path},
                "purpose": "Read the file and retry with enough surrounding text to identify one match.",
            }
        ]
    if error_type == "CONFIRMATION_REQUIRED":
        return [
            {
                "tool": tool_name,
                "arguments": {**({"path": path} if path else {}), "confirm": True},
                "purpose": "Retry only after confirming that this is the intended soft-delete target.",
            }
        ]
    if error_type == "INVALID_PATH":
        return [
            {
                "tool": "vault_list",
                "arguments": {"path": "", "depth": 2},
                "purpose": "Discover visible paths within the Notebook root.",
            }
        ]
    return []


def tool_failure_payload(
    tool_name: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
    error_type: str | None = None,
    recoverable: bool | None = None,
) -> dict[str, Any]:
    """Build a structured failure payload shared by MCP and text clients."""
    details = dict(data or {})
    inferred_type, inferred_recoverable = _classification(message)
    kind = error_type or inferred_type
    can_recover = inferred_recoverable if recoverable is None else recoverable
    return {
        "error": {
            "type": kind,
            "message": message,
            "recoverable": can_recover,
            "data": {"tool": tool_name, **details},
            "next_actions": _next_actions(tool_name, kind, details),
        }
    }


def tool_failure_result(
    tool_name: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
    error_type: str | None = None,
    recoverable: bool | None = None,
) -> CallToolResult:
    """Return a native MCP error with structuredContent and a JSON text fallback."""
    payload = tool_failure_payload(
        tool_name,
        message,
        data=data,
        error_type=error_type,
        recoverable=recoverable,
    )
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=text)],
        structuredContent=payload,
    )
