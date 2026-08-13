"""Integration tests for tool functions."""

import os
import time

import pytest

from obsidian_vault_mcp.tools.info import vault_bootstrap
from obsidian_vault_mcp.tools.manage import vault_delete, vault_list, vault_recent
from obsidian_vault_mcp.tools.read import vault_batch_read, vault_read
from obsidian_vault_mcp.tools.search import vault_find_and_read, vault_search
from obsidian_vault_mcp.tools.write import (
    vault_insert,
    vault_write,
)


def test_vault_read_returns_frontmatter(vault_dir):
    """vault_read returns parsed frontmatter."""
    result = vault_read("test-note.md")
    assert "error" not in result
    assert result["frontmatter"]["status"] == "active"
    assert result["frontmatter"]["type"] == "note"
    assert "test note" in result["content"]


def test_vault_bootstrap_returns_configured_guide(vault_dir, monkeypatch):
    from obsidian_vault_mcp import config

    guide_dir = vault_dir / "system"
    guide_dir.mkdir()
    guide = guide_dir / "agent-guide.md"
    guide.write_text("# Start here\n\nUse the daily logs first.\n")
    monkeypatch.setattr(config, "VAULT_BOOTSTRAP_FILE", "system/agent-guide.md")
    monkeypatch.setattr(
        config,
        "EXCLUDED_DIRS",
        {".git", ".obsidian", ".trash", "incoming", "system"},
    )

    result = vault_bootstrap()

    assert result["configured"] is True
    assert result["guide_path"] == "system/agent-guide.md"
    assert "daily logs first" in result["guide"]
    assert result["metadata"]["revision"]
    assert result["excluded_path_components"] == [
        ".git",
        ".obsidian",
        ".trash",
        "incoming",
        "system",
    ]
    assert result["exclusion_rules"] == [
        "Any file or directory whose name begins with '.' is hidden.",
        "Symlinks are never exposed or followed.",
    ]
    assert result["bootstrap_path_exception"] == "system/agent-guide.md"
    assert result["recommended_next_calls"][0]["tool"] == "vault_recent"


def test_vault_bootstrap_has_safe_unconfigured_fallback(vault_dir):
    result = vault_bootstrap()
    assert result["configured"] is False
    assert "vault_info" in result["guide"]
    assert result["bootstrap_path_exception"] is None


def test_vault_bootstrap_rejects_path_escape(vault_dir, monkeypatch):
    from obsidian_vault_mcp import config

    monkeypatch.setattr(config, "VAULT_BOOTSTRAP_FILE", "../outside.md")
    result = vault_bootstrap()
    assert "error" in result
    assert "outside the vault root" in result["error"]


def test_vault_write_creates_file(vault_dir):
    """vault_write creates a new file."""
    result = vault_write("tools-test.md", "---\ntitle: Test\n---\n\nContent.")
    assert result["created"] is True
    assert result["size"] > 0
    assert (vault_dir / "tools-test.md").exists()


def test_vault_write_merge_frontmatter(vault_dir):
    """vault_write with merge_frontmatter preserves existing fields."""
    result = vault_write(
        "test-note.md",
        "---\npriority: high\n---\n\nUpdated body.",
        merge_frontmatter=True,
    )
    assert "error" not in result

    read_result = vault_read("test-note.md")
    assert read_result["frontmatter"]["status"] == "active"  # preserved
    assert read_result["frontmatter"]["priority"] == "high"  # new


def test_vault_search_finds_text(vault_dir):
    """vault_search finds text in files."""
    result = vault_search("test note")
    assert result["total_matches"] >= 1
    assert result["results"][0]["path"] == "test-note.md"


def test_vault_batch_read_handles_missing(vault_dir):
    """vault_batch_read returns errors for missing files without failing."""
    result = vault_batch_read(
        ["test-note.md", "nonexistent.md"],
        include_content=True,
    )
    assert result["found"] == 1
    assert result["missing"] == 1
    assert "error" in result["files"][1]


def test_vault_list_returns_items(vault_dir):
    """vault_list returns directory contents."""
    result = vault_list("")
    assert result["total"] >= 2
    names = [item["name"] for item in result["items"]]
    assert "test-note.md" in names
    assert ".obsidian" not in names
    assert ".claude" not in names


def test_vault_delete_requires_confirm(vault_dir):
    """vault_delete without confirm=true returns error."""
    vault_write("delete-me.md", "temp content")
    result = vault_delete("delete-me.md", confirm=False)
    assert "error" in result
    assert (vault_dir / "delete-me.md").exists()  # still there


def test_vault_read_supports_partial_body_reads(vault_dir):
    result = vault_read(
        "test-note.md",
        body_only=True,
        start_line=1,
        max_lines=1,
        max_chars=20,
    )
    assert result["content"].startswith("This is a test note")
    assert result["truncated"] is True
    assert result["metadata"]["revision"]


def test_metadata_only_batch_read_omits_content(vault_dir):
    result = vault_batch_read(["test-note.md"], include_content=False)
    assert result["found"] == 1
    assert "content" not in result["files"][0]
    assert result["files"][0]["frontmatter"]["status"] == "active"


def test_yaml_dates_are_json_safe(vault_dir):
    (vault_dir / "dated.md").write_text("---\ndate: 2026-07-12\n---\n\nDated note.\n")
    result = vault_read("dated.md")
    assert result["frontmatter"]["date"] == "2026-07-12"


def test_search_returns_real_context_lines(vault_dir):
    (vault_dir / "context.md").write_text("before\nneedle\nafter\n")
    result = vault_search("needle", context_lines=1)
    group = next(item for item in result["results"] if item["path"] == "context.md")
    assert group["matches"][0]["match_context"] == "before\nneedle\nafter"


def test_find_and_read_combines_round_trip(vault_dir):
    result = vault_find_and_read("test note", max_files=1, max_chars_per_file=20)
    assert result["found"] == 1
    assert result["files"][0]["path"] == "test-note.md"
    assert result["files"][0]["truncated"] is True


def test_insert_with_revision_rejects_stale_write(vault_dir):
    first_read = vault_read("test-note.md")
    revision = first_read["metadata"]["revision"]
    success = vault_insert(
        "test-note.md",
        "Appended line.",
        expected_revision=revision,
    )
    assert "error" not in success

    stale = vault_insert(
        "test-note.md",
        "Should not land.",
        expected_revision=revision,
    )
    assert "Revision mismatch" in stale["error"]
    assert "Should not land" not in (vault_dir / "test-note.md").read_text()


def test_insert_after_heading(vault_dir):
    (vault_dir / "headings.md").write_text("# Title\n\n## Notes\nExisting\n")
    result = vault_insert(
        "headings.md",
        "Inserted\n",
        position="after_heading",
        heading="## Notes",
    )
    assert "error" not in result
    assert "## Notes\nInserted\nExisting" in (vault_dir / "headings.md").read_text()


def test_recent_filters_exact_tag_membership(vault_dir):
    (vault_dir / "tagged.md").write_text("---\ntags: [tech, ai]\n---\n\nTagged.\n")
    result = vault_recent(tag="tech", limit=10)
    assert any(item["path"] == "tagged.md" for item in result["results"])


def _use_python_fallback(monkeypatch, force: bool) -> None:
    """Force vault_search onto the Python backend by hiding ripgrep."""
    if force:
        monkeypatch.setattr(
            "obsidian_vault_mcp.tools.search.shutil.which", lambda _: None
        )


@pytest.mark.parametrize("force_python_fallback", [False, True])
def test_search_is_literal_by_default(vault_dir, monkeypatch, force_python_fallback):
    """Regex metacharacters in the query are matched literally unless regex=true."""
    _use_python_fallback(monkeypatch, force_python_fallback)
    (vault_dir / "trt.md").write_text("Dose noted: TRT (Taro) 100mg\n")
    (vault_dir / "cpp.md").write_text("Rewrote the parser in C++ today\n")

    result = vault_search("TRT (Taro)")
    assert "error" not in result
    assert any(item["path"] == "trt.md" for item in result["results"])

    result = vault_search("C++")
    assert "error" not in result
    assert any(item["path"] == "cpp.md" for item in result["results"])


@pytest.mark.parametrize("force_python_fallback", [False, True])
def test_search_regex_true_matches_pattern(vault_dir, monkeypatch, force_python_fallback):
    _use_python_fallback(monkeypatch, force_python_fallback)
    (vault_dir / "codes.md").write_text("error code E123\nerror code E456\nnothing here\n")

    result = vault_search(r"E\d+", regex=True)
    assert "error" not in result
    group = next(item for item in result["results"] if item["path"] == "codes.md")
    assert len(group["matches"]) == 2


@pytest.mark.parametrize("force_python_fallback", [False, True])
def test_search_invalid_regex_returns_error(vault_dir, monkeypatch, force_python_fallback):
    _use_python_fallback(monkeypatch, force_python_fallback)
    result = vault_search("(unclosed", regex=True)
    assert "error" in result


def test_search_caps_matches_per_file_and_ranks_by_recency(vault_dir):
    busy = vault_dir / "busy.md"
    busy.write_text("\n".join(f"needle line {i}" for i in range(5)) + "\n")
    quiet_old = vault_dir / "quiet-old.md"
    quiet_old.write_text("needle here\n")
    quiet_new = vault_dir / "quiet-new.md"
    quiet_new.write_text("needle here too\n")

    now = time.time()
    os.utime(quiet_old, (now - 1000, now - 1000))
    os.utime(busy, (now - 500, now - 500))
    os.utime(quiet_new, (now, now))

    result = vault_search("needle", max_results=50)
    assert "error" not in result

    busy_group = next(item for item in result["results"] if item["path"] == "busy.md")
    assert len(busy_group["matches"]) == 3  # capped at the per-file limit

    paths_in_order = [item["path"] for item in result["results"]]
    assert (
        paths_in_order.index("quiet-new.md")
        < paths_in_order.index("busy.md")
        < paths_in_order.index("quiet-old.md")
    )


def test_search_extracts_nearest_heading(vault_dir):
    (vault_dir / "headed.md").write_text(
        "# Title\n\n"
        "## Section One\n"
        "Intro line\n\n"
        "needle inside section one\n\n"
        "## Section Two\n"
        "Other content\n"
    )
    result = vault_search("needle")
    group = next(item for item in result["results"] if item["path"] == "headed.md")
    assert group["matches"][0]["heading"] == "Section One"


def test_find_and_read_returns_enclosing_section_not_file_head(vault_dir):
    lines = ["# Daily Log", "", "## Morning", "Nothing relevant here.", ""]
    lines += [f"filler line {i}" for i in range(50)]
    lines += [
        "",
        "## Needle Section",
        "needle appears here",
        "more detail",
        "",
        "## Later Section",
        "unrelated content",
    ]
    (vault_dir / "long.md").write_text("\n".join(lines) + "\n")

    result = vault_find_and_read("needle", max_files=1, max_chars_per_file=4_000)
    assert result["found"] == 1
    file_entry = result["files"][0]
    assert file_entry["path"] == "long.md"
    assert "needle appears here" in file_entry["content"]
    assert "Nothing relevant here." not in file_entry["content"]
    assert "unrelated content" not in file_entry["content"]
    assert file_entry["sections"][0]["heading"] == "Needle Section"
