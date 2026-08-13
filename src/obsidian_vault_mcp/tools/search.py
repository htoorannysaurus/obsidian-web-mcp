"""Search tools for the Obsidian vault MCP server."""

import logging
import re
import shutil
import subprocess
from pathlib import Path

import frontmatter

from .. import config
from ..serialization import json_safe
from ..state import frontmatter_index
from ..vault import metadata_from_stat, resolve_vault_path

logger = logging.getLogger(__name__)

# A single file with many hits shouldn't crowd out every other file in the
# result set, so raw collection is capped per file, with a hard stop across
# the whole raw scan so a pathological query can't walk the entire vault.
_PER_FILE_MATCH_CAP = 3
_RAW_MATCH_LIMIT = 300

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _search_ripgrep(
    query: str,
    search_path: Path,
    file_pattern: str,
    per_file_cap: int,
    raw_limit: int,
    regex: bool,
) -> list[dict]:
    """Search using ripgrep for performance.

    Raises ValueError if regex=True and the query is not a valid regex.
    """
    cmd = [
        "rg",
        "--json",
        f"--max-count={per_file_cap}",
        f"--glob={file_pattern}",
        "-i",
    ]
    if not regex:
        cmd.append("-F")

    for excluded in config.EXCLUDED_DIRS:
        cmd.append(f"--glob=!**/{excluded}/**")
    cmd.extend(["--", query, str(search_path)])

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    matches = []
    assert process.stdout is not None
    try:
        import json

        for line in process.stdout:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if data.get("type") != "match":
                continue
            match_data = data["data"]
            file_path = match_data["path"]["text"]
            try:
                rel_path = str(Path(file_path).relative_to(config.VAULT_PATH))
            except ValueError:
                continue

            matches.append(
                {
                    "path": rel_path,
                    "line_number": match_data["line_number"],
                    "match_context": match_data["lines"]["text"].rstrip("\n"),
                }
            )
            if len(matches) >= raw_limit:
                process.terminate()
                break
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    if regex and process.returncode == 2 and not matches:
        stderr_output = process.stderr.read().strip() if process.stderr else ""
        raise ValueError(f"Invalid regex: {stderr_output or 'pattern error'}")

    return matches


def _search_python(
    query: str,
    search_path: Path,
    file_pattern: str,
    per_file_cap: int,
    raw_limit: int,
    regex: bool,
) -> list[dict]:
    """Fallback Python-based search.

    Raises ValueError if regex=True and the query is not a valid regex.
    """
    import fnmatch

    pattern: re.Pattern | None = None
    if regex:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"Invalid regex: {e}") from e
    query_lower = query.lower()

    matches: list[dict] = []

    for file_path in search_path.rglob("*"):
        if not file_path.is_file():
            continue

        try:
            relative_parts = file_path.relative_to(config.VAULT_PATH).parts
        except ValueError:
            continue
        if any(
            part.startswith(".") or part in config.EXCLUDED_DIRS
            for part in relative_parts
        ):
            continue

        if not fnmatch.fnmatch(file_path.name, file_pattern):
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        try:
            rel_path = str(file_path.relative_to(config.VAULT_PATH))
        except ValueError:
            continue

        file_matches = 0
        for i, line in enumerate(content.splitlines()):
            hit = pattern.search(line) if pattern else query_lower in line.lower()
            if not hit:
                continue

            matches.append(
                {"path": rel_path, "line_number": i + 1, "match_context": line}
            )
            file_matches += 1
            if len(matches) >= raw_limit:
                return matches
            if file_matches >= per_file_cap:
                break

    return matches


def _get_frontmatter_excerpt(file_path: Path, max_keys: int = 3) -> dict | None:
    """Read frontmatter from a file, returning first N key-value pairs."""
    try:
        content = file_path.read_text(encoding="utf-8")
        post = frontmatter.loads(content)
        if not post.metadata:
            return None
        keys = list(post.metadata.keys())[:max_keys]
        return json_safe({k: post.metadata[k] for k in keys})
    except Exception:
        return None


def _full_frontmatter(path: str, full_path: Path) -> dict | None:
    """Return a file's complete frontmatter, preferring the in-memory index."""
    indexed = frontmatter_index.get(path)
    if indexed is not None:
        return json_safe(indexed)
    try:
        content = full_path.read_text(encoding="utf-8")
        post = frontmatter.loads(content)
        return json_safe(post.metadata) if post.metadata else None
    except Exception:
        return None


def _read_lines_cached(path: str, cache: dict[str, list[str]]) -> list[str]:
    """Read a vault file's lines once per search, reusing them across matches."""
    if path not in cache:
        try:
            cache[path] = (
                (config.VAULT_PATH / path).read_text(encoding="utf-8").splitlines()
            )
        except (OSError, UnicodeDecodeError):
            cache[path] = []
    return cache[path]


def _heading_at_or_above(lines: list[str], index: int) -> str | None:
    """Return the nearest Markdown heading text at or above line `index` (0-based)."""
    for i in range(index, -1, -1):
        m = _HEADING_RE.match(lines[i])
        if m:
            return m.group(2).strip()
    return None


def _attach_context(
    matches: list[dict],
    context_lines: int,
    cache: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Populate real surrounding context and heading, reading each hit file once.

    Returns the per-file line cache so callers (vault_find_and_read's section
    extraction) can reuse it instead of re-reading files from disk.
    """
    if cache is None:
        cache = {}
    for match in matches:
        path = match["path"]
        lines = _read_lines_cached(path, cache)
        index = match["line_number"] - 1
        match.setdefault("heading", None)
        if not lines or index < 0 or index >= len(lines):
            continue
        start = max(0, index - context_lines)
        end = min(len(lines), index + context_lines + 1)
        match["match_context"] = "\n".join(lines[start:end])
        match["heading"] = _heading_at_or_above(lines, index)
    return cache


def vault_search(
    query: str,
    path_prefix: str | None = None,
    file_pattern: str = "*.md",
    max_results: int = 20,
    context_lines: int = 2,
    regex: bool = False,
) -> dict:
    """Search for text across vault files, grouped by file and ranked by recency."""
    try:
        if path_prefix:
            search_path = resolve_vault_path(path_prefix)
        else:
            search_path = config.VAULT_PATH

        if not search_path.is_dir():
            return {"error": f"Search path is not a directory: {path_prefix}"}

        if shutil.which("rg"):
            raw_matches = _search_ripgrep(
                query,
                search_path,
                file_pattern,
                _PER_FILE_MATCH_CAP,
                _RAW_MATCH_LIMIT,
                regex,
            )
        else:
            raw_matches = _search_python(
                query,
                search_path,
                file_pattern,
                _PER_FILE_MATCH_CAP,
                _RAW_MATCH_LIMIT,
                regex,
            )

        _attach_context(raw_matches, context_lines)

        # Group matches by file, preserving each file's match order (already
        # capped upstream to _PER_FILE_MATCH_CAP).
        grouped: dict[str, list[dict]] = {}
        for match in raw_matches:
            grouped.setdefault(match["path"], []).append(
                {
                    "line_number": match["line_number"],
                    "heading": match.get("heading"),
                    "match_context": match["match_context"],
                }
            )

        def _mtime(path: str) -> float:
            try:
                return (config.VAULT_PATH / path).stat().st_mtime
            except OSError:
                return 0.0

        # Newest-modified files first.
        ordered_paths = sorted(grouped.keys(), key=_mtime, reverse=True)

        results: list[dict] = []
        total_matches = 0
        truncated = len(raw_matches) >= _RAW_MATCH_LIMIT
        for path in ordered_paths:
            if total_matches >= max_results:
                truncated = True
                break

            file_matches = grouped[path]
            remaining = max_results - total_matches
            included = file_matches[:remaining]
            if len(included) < len(file_matches):
                truncated = True

            full_path = config.VAULT_PATH / path
            try:
                modified = metadata_from_stat(full_path.stat())["modified"]
            except OSError:
                modified = None

            indexed = frontmatter_index.get(path)
            if indexed is not None:
                keys = list(indexed)[:3]
                frontmatter_excerpt = json_safe({key: indexed[key] for key in keys})
            else:
                frontmatter_excerpt = _get_frontmatter_excerpt(full_path)

            results.append(
                {
                    "path": path,
                    "modified": modified,
                    "frontmatter_excerpt": frontmatter_excerpt,
                    "matches": included,
                }
            )
            total_matches += len(included)

        return {
            "results": results,
            "total_matches": total_matches,
            "files": len(results),
            "truncated": truncated,
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"vault_search error: {e}")
        return {"error": str(e)}


def vault_search_frontmatter(
    field: str,
    value: str = "",
    match_type: str = "exact",
    path_prefix: str | None = None,
    max_results: int = 20,
) -> dict:
    """Search vault files by frontmatter field values using the in-memory index."""
    try:
        results = frontmatter_index.search_by_field(
            field=field,
            value=value,
            match_type=match_type,
            path_prefix=path_prefix,
        )

        formatted = []
        for item in results[:max_results]:
            path = item["path"]
            fm = item["frontmatter"]
            title = fm.get("title", Path(path).stem)
            formatted.append(
                {
                    "path": path,
                    "frontmatter": json_safe(fm),
                    "title": title,
                }
            )

        truncated = len(results) > max_results

        return {
            "results": formatted,
            "total": len(results),
            "returned": len(formatted),
            "truncated": truncated,
        }
    except Exception as e:
        logger.error(f"vault_search_frontmatter error: {e}")
        return {"error": str(e)}


def _sections_for_matches(
    lines: list[str], match_line_numbers: list[int]
) -> list[tuple[int, int, str | None]]:
    """Return the enclosing section (start_index, end_index, heading) per match.

    A section starts at the nearest heading at-or-above the match line and ends
    just before the next heading of the same or higher level (fewer or equal
    #'s), or EOF. Matches with no heading above them fall back to a window of
    max(0, match_line-10) to match_line+30. Overlapping/duplicate sections are
    merged. `end_index` is exclusive; both indices are 0-based.
    """
    total = len(lines)
    raw_sections: list[tuple[int, int, str | None]] = []

    for line_number in match_line_numbers:
        index = line_number - 1
        if index < 0 or index >= total:
            continue

        heading_index: int | None = None
        heading_level: int | None = None
        heading_text: str | None = None
        for i in range(index, -1, -1):
            m = _HEADING_RE.match(lines[i])
            if m:
                heading_index = i
                heading_level = len(m.group(1))
                heading_text = m.group(2).strip()
                break

        if heading_index is not None:
            start_index = heading_index
            end_index = total
            for j in range(heading_index + 1, total):
                m2 = _HEADING_RE.match(lines[j])
                if m2 and len(m2.group(1)) <= heading_level:
                    end_index = j
                    break
            raw_sections.append((start_index, end_index, heading_text))
        else:
            start_index = max(0, index - 10)
            end_index = min(total, index + 30 + 1)
            raw_sections.append((start_index, end_index, None))

    raw_sections.sort(key=lambda s: (s[0], s[1]))
    merged: list[tuple[int, int, str | None]] = []
    for start, end, heading in raw_sections:
        if merged and start <= merged[-1][1]:
            prev_start, prev_end, prev_heading = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end), prev_heading)
        else:
            merged.append((start, end, heading))
    return merged


def _extract_file_section(
    path: str,
    matches: list[dict],
    max_chars_per_file: int,
    cache: dict[str, list[str]],
) -> dict:
    """Build the section-aware read payload for one file's grouped matches."""
    lines = _read_lines_cached(path, cache)
    full_path = config.VAULT_PATH / path

    sections = _sections_for_matches(lines, [m["line_number"] for m in matches])
    pieces: list[str] = []
    section_meta: list[dict] = []
    for start, end, heading in sections:
        pieces.append("\n".join(lines[start:end]))
        section_meta.append({"heading": heading, "start_line": start + 1})

    content = "\n\n[...]\n\n".join(pieces)
    truncated = len(content) > max_chars_per_file
    if truncated:
        content = content[:max_chars_per_file]

    try:
        modified = metadata_from_stat(full_path.stat())["modified"]
    except OSError:
        modified = None

    return {
        "path": path,
        "modified": modified,
        "frontmatter": _full_frontmatter(path, full_path),
        "sections": section_meta,
        "content": content,
        "truncated": truncated,
    }


def vault_find_and_read(
    query: str,
    path_prefix: str | None = None,
    max_files: int = 5,
    context_lines: int = 2,
    max_chars_per_file: int = 4_000,
    regex: bool = False,
) -> dict:
    """Search once and return the enclosing sections of the top matching files.

    Uses vault_search's recency-ranked, per-file-capped grouping, then returns
    the Markdown section enclosing each match (instead of the head of the
    file), which is what actually contains the matched content for long notes
    such as daily logs.
    """
    search_result = vault_search(
        query=query,
        path_prefix=path_prefix,
        max_results=max_files * _PER_FILE_MATCH_CAP,
        context_lines=context_lines,
        regex=regex,
    )
    if "error" in search_result:
        return search_result

    file_groups = search_result["results"][:max_files]

    cache: dict[str, list[str]] = {}
    files_payload = [
        _extract_file_section(group["path"], group["matches"], max_chars_per_file, cache)
        for group in file_groups
    ]

    return {
        "query": query,
        "matches": search_result["results"],
        "files": files_payload,
        "found": len(files_payload),
        "truncated_search": search_result["truncated"],
    }
