"""Read tools for the Obsidian vault MCP server."""

import logging

import frontmatter

from ..serialization import json_safe
from ..state import frontmatter_index
from ..vault import read_file, stat_file

logger = logging.getLogger(__name__)


def _frontmatter_for(path: str, content: str | None = None) -> dict | None:
    if content is not None:
        try:
            metadata = frontmatter.loads(content).metadata
            return json_safe(metadata) if metadata else None
        except Exception:
            return None

    cached = frontmatter_index.get(path)
    if cached is not None:
        return json_safe(cached)
    try:
        content, _ = read_file(path)
    except Exception:
        return None
    try:
        metadata = frontmatter.loads(content).metadata
        return json_safe(metadata) if metadata else None
    except Exception:
        return None


def _slice_content(
    content: str,
    *,
    start_line: int = 1,
    max_lines: int | None = None,
    max_chars: int | None = None,
) -> tuple[str, dict]:
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    start_index = min(start_line - 1, total_lines)
    end_index = (
        total_lines if max_lines is None else min(total_lines, start_index + max_lines)
    )
    selected = "".join(lines[start_index:end_index])
    truncated_by_chars = max_chars is not None and len(selected) > max_chars
    if truncated_by_chars:
        selected = selected[:max_chars]
    selected_line_count = selected.count("\n")
    if selected and not selected.endswith("\n"):
        selected_line_count += 1
    actual_end_index = min(end_index, start_index + selected_line_count)
    return selected, {
        "line_start": start_index + 1 if total_lines else 0,
        "line_end": actual_end_index,
        "total_lines": total_lines,
        "truncated": start_index > 0 or end_index < total_lines or truncated_by_chars,
    }


def vault_read(
    path: str,
    include_content: bool = True,
    body_only: bool = False,
    start_line: int = 1,
    max_lines: int | None = None,
    max_chars: int | None = None,
) -> dict:
    """Read a file from the vault, returning content, metadata, and parsed frontmatter."""
    try:
        if not include_content:
            return {
                "path": path,
                "metadata": stat_file(path),
                "frontmatter": _frontmatter_for(path),
            }

        content, metadata = read_file(path)
        fm_data = _frontmatter_for(path, content)
        if body_only:
            try:
                content = frontmatter.loads(content).content
            except Exception:
                pass
        selected, window = _slice_content(
            content,
            start_line=start_line,
            max_lines=max_lines,
            max_chars=max_chars,
        )

        return {
            "path": path,
            "content": selected,
            "metadata": json_safe(metadata),
            "frontmatter": fm_data,
            **window,
        }
    except ValueError as e:
        return {"error": str(e), "path": path}
    except FileNotFoundError:
        return {"error": f"File not found: {path}", "path": path}
    except Exception as e:
        logger.error(f"vault_read error for {path}: {e}")
        return {"error": str(e), "path": path}


def vault_batch_read(
    paths: list[str], include_content: bool = True, max_chars: int | None = None
) -> dict:
    """Read multiple files from the vault in one call."""
    results = []
    found = 0
    missing = 0

    for path in paths:
        try:
            if include_content:
                content, metadata = read_file(path)
                fm_data = _frontmatter_for(path, content)
            else:
                content = None
                metadata = stat_file(path)
                fm_data = _frontmatter_for(path)

            entry = {
                "path": path,
                "metadata": json_safe(metadata),
                "frontmatter": fm_data,
            }
            if include_content:
                assert content is not None
                truncated = max_chars is not None and len(content) > max_chars
                entry["content"] = (
                    content[:max_chars] if max_chars is not None else content
                )
                entry["truncated"] = truncated

            results.append(entry)
            found += 1
        except (ValueError, FileNotFoundError) as e:
            results.append({"path": path, "error": str(e)})
            missing += 1
        except Exception as e:
            results.append({"path": path, "error": str(e)})
            missing += 1

    return {"files": results, "found": found, "missing": missing}
