"""Connector self-description and agent-orientation tools."""

import os
from pathlib import Path

from .. import config
from ..state import frontmatter_index
from ..version import __version__
from ..vault import content_revision, metadata_from_stat


DEFAULT_NEXT_CALLS = [
    {
        "tool": "vault_recent",
        "arguments": {"path_prefix": "daily-logs", "limit": 3},
        "purpose": "Find the latest daily context before responding about current events.",
    },
    {
        "tool": "vault_find_and_read",
        "arguments": {"query": "<topic>", "max_files": 5},
        "purpose": "Retrieve likely context for a person, project, decision, or recurring topic.",
    },
    {
        "tool": "vault_list",
        "arguments": {"path": "", "depth": 2},
        "purpose": "Inspect the visible vault structure when the guide does not cover a lane.",
    },
]

EXCLUSION_RULES = [
    "Any file or directory whose name begins with '.' is hidden.",
    "Symlinks are never exposed or followed.",
]


def _exclusion_details() -> dict:
    """Describe the same path boundaries enforced by vault operations."""
    return {
        "excluded_path_components": sorted(config.EXCLUDED_DIRS),
        "exclusion_rules": EXCLUSION_RULES,
        "bootstrap_path_exception": config.VAULT_BOOTSTRAP_FILE or None,
    }


def _bootstrap_path(relative_path: str) -> Path:
    """Resolve the configured guide inside the vault.

    This intentionally does not apply EXCLUDED_DIRS: deployments may keep their
    one approved orientation file inside an otherwise private folder. Only the
    exact administrator-configured file is exposed through vault_bootstrap.
    """
    if "\x00" in relative_path:
        raise ValueError("Bootstrap path contains null bytes")

    configured = Path(relative_path)
    if configured.is_absolute():
        raise ValueError("VAULT_BOOTSTRAP_FILE must be vault-relative")
    if ".." in configured.parts:
        raise ValueError("Bootstrap path resolves outside the vault root")
    if any(part.startswith(".") for part in configured.parts):
        raise ValueError("Bootstrap path cannot contain hidden path components")

    vault_root = config.VAULT_PATH.resolve()
    candidate = vault_root / configured

    # Reject symlinks at every existing component before resolving them.
    current = vault_root
    for part in configured.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Bootstrap path cannot traverse symlinks")

    resolved = candidate.resolve()
    if (
        not str(resolved).startswith(str(vault_root) + os.sep)
        and resolved != vault_root
    ):
        raise ValueError("Bootstrap path resolves outside the vault root")
    return resolved


def vault_info() -> dict:
    """Return model-visible connector version and capability information."""
    return {
        "connector": "Notebook",
        "package": "obsidian-web-mcp",
        "version": __version__,
        "vault": config.VAULT_PATH.name,
        "indexed_files": frontmatter_index.file_count,
        "bootstrap_configured": bool(config.VAULT_BOOTSTRAP_FILE),
        "capabilities": [
            "session-enforced agent bootstrap guidance",
            "structured MCP output",
            "partial and batch reads",
            "full-text and frontmatter search",
            "recent-note discovery",
            "combined search and read",
            "revision-protected writes",
            "heading-aware insertion",
            "move and soft delete",
        ],
    }


def vault_bootstrap() -> dict:
    """Return the deployment's curated orientation guide for fresh agents."""
    configured_path = config.VAULT_BOOTSTRAP_FILE
    if not configured_path:
        return {
            "connector": "Notebook",
            "configured": False,
            "guide": (
                "No custom bootstrap guide is configured. Start with vault_info, "
                "vault_recent, and vault_list, then retrieve only the context needed "
                "for the user's request."
            ),
            **_exclusion_details(),
            "recommended_next_calls": DEFAULT_NEXT_CALLS,
        }

    try:
        path = _bootstrap_path(configured_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Configured bootstrap guide was not found: {configured_path}"
            )
        stat = path.stat()
        if stat.st_size > config.MAX_BOOTSTRAP_SIZE:
            raise ValueError(
                f"Bootstrap guide exceeds {config.MAX_BOOTSTRAP_SIZE} byte limit"
            )
        guide = path.read_text(encoding="utf-8")
        metadata = metadata_from_stat(stat)
        metadata["revision"] = content_revision(guide)
        return {
            "connector": "Notebook",
            "configured": True,
            "guide_path": configured_path,
            "guide": guide,
            "metadata": metadata,
            **_exclusion_details(),
            "recommended_next_calls": DEFAULT_NEXT_CALLS,
        }
    except (OSError, UnicodeError, ValueError) as exc:
        return {
            "connector": "Notebook",
            "configured": True,
            "guide_path": configured_path,
            "error": str(exc),
            "recommended_next_calls": DEFAULT_NEXT_CALLS,
        }
