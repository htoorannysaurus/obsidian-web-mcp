"""Management tools for the Obsidian vault MCP server."""

import fnmatch
import logging
import os
from pathlib import Path

from .. import config
from ..serialization import json_safe
from ..state import frontmatter_index
from ..vault import (
    delete_path,
    list_directory,
    metadata_from_stat,
    move_path,
    resolve_vault_path,
)
from .read import _frontmatter_for

logger = logging.getLogger(__name__)


def vault_list(
    path: str = "",
    depth: int = 1,
    include_files: bool = True,
    include_dirs: bool = True,
    pattern: str | None = None,
) -> dict:
    """List directory contents in the vault."""
    try:
        items = list_directory(
            path,
            depth=depth,
            include_files=include_files,
            include_dirs=include_dirs,
            pattern=pattern,
        )
        return {"items": items, "total": len(items)}
    except ValueError as e:
        return {"error": str(e)}
    except FileNotFoundError:
        return {"error": f"Directory not found: {path}"}
    except Exception as e:
        logger.error(f"vault_list error: {e}")
        return {"error": str(e)}


def vault_move(source: str, destination: str, create_dirs: bool = True) -> dict:
    """Move a file or directory within the vault."""
    try:
        moved = move_path(source, destination, create_dirs=create_dirs)
        return {"source": source, "destination": destination, "moved": moved}
    except ValueError as e:
        return {"error": str(e), "source": source, "destination": destination}
    except Exception as e:
        logger.error(f"vault_move error: {e}")
        return {"error": str(e), "source": source, "destination": destination}


def vault_delete(path: str, confirm: bool = False) -> dict:
    """Delete a file by moving it to .trash/ in the vault."""
    if not confirm:
        return {
            "error": "Set confirm=true to execute deletion. Files are moved to .trash/, not hard deleted.",
            "path": path,
        }

    try:
        deleted = delete_path(path)
        return {"path": path, "deleted": deleted}
    except ValueError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.error(f"vault_delete error: {e}")
        return {"error": str(e), "path": path}


def vault_recent(
    path_prefix: str | None = None,
    pattern: str = "*.md",
    tag: str | None = None,
    limit: int = 10,
    sort_by: str = "modified",
    date_field: str = "date",
) -> dict:
    """Return recent notes, optionally filtered by folder and tag."""
    try:
        root = resolve_vault_path(path_prefix or "")
        if not root.is_dir():
            return {"error": f"Not a directory: {path_prefix}"}

        vault_root = config.VAULT_PATH.resolve()
        candidates: list[dict] = []
        for current_root, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(current_root)
            dirnames[:] = [
                name
                for name in dirnames
                if not name.startswith(".")
                and name not in config.EXCLUDED_DIRS
                and not (current / name).is_symlink()
            ]
            for filename in filenames:
                path = current / filename
                if (
                    filename.startswith(".")
                    or path.is_symlink()
                    or not fnmatch.fnmatch(filename, pattern)
                ):
                    continue
                relative = str(path.relative_to(vault_root))
                stat = path.stat()
                metadata = metadata_from_stat(stat)
                frontmatter = None
                if tag or sort_by == "frontmatter_date":
                    frontmatter = frontmatter_index.get(relative) or _frontmatter_for(
                        relative
                    )
                if tag:
                    tags = (frontmatter or {}).get("tags", [])
                    if isinstance(tags, str):
                        tags = [tags]
                    if tag not in [str(item) for item in tags]:
                        continue
                if sort_by == "frontmatter_date":
                    sort_value = str((frontmatter or {}).get(date_field, ""))
                else:
                    sort_value = stat.st_mtime
                candidates.append(
                    {
                        "path": relative,
                        "metadata": metadata,
                        "frontmatter": frontmatter,
                        "sort_value": sort_value,
                    }
                )

        candidates.sort(key=lambda item: item["sort_value"], reverse=True)
        results = candidates[:limit]
        for item in results:
            item.pop("sort_value", None)
            if item["frontmatter"] is None:
                item["frontmatter"] = frontmatter_index.get(
                    item["path"]
                ) or _frontmatter_for(item["path"])
            item["frontmatter"] = json_safe(item["frontmatter"])
        return {
            "results": results,
            "returned": len(results),
            "total_candidates": len(candidates),
            "truncated": len(candidates) > limit,
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error("vault_recent error: %s", e)
        return {"error": str(e)}
