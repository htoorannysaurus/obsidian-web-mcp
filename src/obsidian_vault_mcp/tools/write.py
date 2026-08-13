"""Write tools for the Obsidian vault MCP server."""

import logging

import frontmatter

from ..vault import content_revision, read_file, resolve_vault_path, write_file_atomic

logger = logging.getLogger(__name__)


def vault_write(
    path: str,
    content: str,
    create_dirs: bool = True,
    merge_frontmatter: bool = False,
    expected_revision: str | None = None,
) -> dict:
    """Write a file to the vault, optionally merging frontmatter with existing content."""
    try:
        resolve_vault_path(path)

        if merge_frontmatter:
            try:
                existing_content, _ = read_file(path)
                existing_post = frontmatter.loads(existing_content)
                new_post = frontmatter.loads(content)

                merged_meta = dict(existing_post.metadata)
                merged_meta.update(new_post.metadata)

                new_post.metadata = merged_meta
                content = frontmatter.dumps(new_post)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(
                    f"Frontmatter merge failed for {path}, writing as-is: {e}"
                )

        is_new, size = write_file_atomic(
            path,
            content,
            create_dirs=create_dirs,
            expected_revision=expected_revision,
        )

        return {
            "path": path,
            "created": is_new,
            "size": size,
            "revision": content_revision(content),
        }
    except ValueError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.error(f"vault_write error for {path}: {e}")
        return {"error": str(e), "path": path}


def vault_edit(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    expected_revision: str | None = None,
) -> dict:
    """Edit a file by replacing old_string with new_string.

    Default semantics (replace_all=False): old_string must appear EXACTLY once,
    otherwise the edit is rejected with an error -- this protects against
    accidental ambiguous matches when editing from a phone or web client.
    Use replace_all=True to replace every occurrence.
    """
    try:
        resolve_vault_path(path)

        try:
            existing_content, _ = read_file(path)
        except FileNotFoundError:
            return {"error": "File not found", "path": path}

        if old_string == new_string:
            return {"error": "old_string and new_string are identical", "path": path}

        if old_string == "":
            return {
                "error": "old_string cannot be empty (use vault_write to create a file)",
                "path": path,
            }

        occurrences = existing_content.count(old_string)

        if occurrences == 0:
            return {
                "error": "old_string not found in file",
                "path": path,
                "occurrences": 0,
            }

        if not replace_all and occurrences > 1:
            return {
                "error": f"old_string found {occurrences} times; pass replace_all=true or include more surrounding context to make the match unique",
                "path": path,
                "occurrences": occurrences,
            }

        if replace_all:
            new_content = existing_content.replace(old_string, new_string)
            replaced = occurrences
        else:
            new_content = existing_content.replace(old_string, new_string, 1)
            replaced = 1

        _, size = write_file_atomic(
            path,
            new_content,
            create_dirs=False,
            expected_revision=expected_revision,
        )

        return {
            "path": path,
            "replacements": replaced,
            "size": size,
            "revision": content_revision(new_content),
        }
    except ValueError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.error(f"vault_edit error for {path}: {e}")
        return {"error": str(e), "path": path}


def vault_insert(
    path: str,
    content: str,
    position: str = "append",
    heading: str | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Insert content without requiring a full-file rewrite from the client."""
    try:
        existing_content, _ = read_file(path)
        insertion = content

        if position == "append":
            separator = (
                "" if not existing_content or existing_content.endswith("\n") else "\n"
            )
            new_content = f"{existing_content}{separator}{insertion}"
        elif position == "prepend":
            separator = "" if not insertion or insertion.endswith("\n") else "\n"
            new_content = f"{insertion}{separator}{existing_content}"
        elif position == "after_heading":
            if not heading:
                return {"error": "heading is required for after_heading", "path": path}
            lines = existing_content.splitlines(keepends=True)
            matches = [
                i for i, line in enumerate(lines) if line.rstrip("\r\n") == heading
            ]
            if len(matches) != 1:
                return {
                    "error": f"heading must match exactly once; found {len(matches)} matches",
                    "path": path,
                }
            index = matches[0] + 1
            if insertion and not insertion.endswith("\n"):
                insertion += "\n"
            lines.insert(index, insertion)
            new_content = "".join(lines)
        else:
            return {"error": f"Unsupported position: {position}", "path": path}

        _, size = write_file_atomic(
            path,
            new_content,
            create_dirs=False,
            expected_revision=expected_revision,
        )
        return {
            "path": path,
            "position": position,
            "size": size,
            "revision": content_revision(new_content),
        }
    except FileNotFoundError:
        return {"error": "File not found", "path": path}
    except ValueError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.error("vault_insert error for %s: %s", path, e)
        return {"error": str(e), "path": path}


def vault_batch_frontmatter_update(updates: list[dict]) -> dict:
    """Update frontmatter fields on multiple files without changing body content."""
    results = []

    for update in updates:
        file_path = update.get("path", "")
        fields = update.get("fields", {})
        expected_revision = update.get("expected_revision")

        try:
            content, _ = read_file(file_path)
            post = frontmatter.loads(content)

            for key, value in fields.items():
                post.metadata[key] = value

            new_content = frontmatter.dumps(post)
            write_file_atomic(
                file_path,
                new_content,
                create_dirs=False,
                expected_revision=expected_revision,
            )

            results.append({"path": file_path, "updated": True})
        except FileNotFoundError:
            results.append(
                {"path": file_path, "updated": False, "error": "File not found"}
            )
        except ValueError as e:
            results.append({"path": file_path, "updated": False, "error": str(e)})
        except Exception as e:
            results.append({"path": file_path, "updated": False, "error": str(e)})

    return {"results": results}
