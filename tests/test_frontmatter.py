"""Tests for frontmatter_index.py -- indexing, searching, and merging."""

import pytest
import time

from obsidian_vault_mcp.frontmatter_index import FrontmatterIndex


@pytest.fixture
def index(vault_dir):
    """Create and start a frontmatter index against the test vault."""
    idx = FrontmatterIndex()
    idx.start()
    yield idx
    idx.stop()


def test_index_builds_on_startup(index, vault_dir):
    """Index has entries for all .md files (not .obsidian)."""
    assert index.file_count >= 2  # test-note.md, subfolder/nested-note.md
    # no-frontmatter.md may or may not be in index (no frontmatter to parse)


def test_search_exact_match(index, vault_dir):
    """Search for field=status, value=active, match_type=exact."""
    results = index.search_by_field("status", "active", "exact")
    assert len(results) >= 1
    paths = [r["path"] for r in results]
    assert "test-note.md" in paths


def test_search_contains(index, vault_dir):
    """Search for field=client, value=Test, match_type=contains."""
    results = index.search_by_field("client", "Test", "contains")
    assert len(results) >= 1
    paths = [r["path"] for r in results]
    found = any("nested-note.md" in p for p in paths)
    assert found


def test_search_exists(index, vault_dir):
    """Search for field=client, match_type=exists."""
    results = index.search_by_field("client", "", "exists")
    assert len(results) >= 1


def test_search_with_prefix(index, vault_dir):
    """Search limited to subfolder/."""
    results = index.search_by_field(
        "status", "draft", "exact", path_prefix="subfolder/"
    )
    assert len(results) >= 1
    for r in results:
        assert r["path"].startswith("subfolder/")


def test_exact_match_supports_list_membership(index, vault_dir):
    (vault_dir / "tagged.md").write_text("---\ntags: [tech, ai]\n---\n\nTagged.\n")
    index._schedule_debounce(str(vault_dir / "tagged.md"))
    index._flush_pending()
    results = index.search_by_field("tags", "tech", "exact")
    assert any(item["path"] == "tagged.md" for item in results)


def test_index_tracks_file_moves(index, vault_dir, monkeypatch):
    import obsidian_vault_mcp.config as config

    monkeypatch.setattr(config, "FRONTMATTER_INDEX_DEBOUNCE", 0.01)
    source = vault_dir / "test-note.md"
    destination = vault_dir / "renamed-note.md"
    source.rename(destination)

    deadline = time.time() + 1
    while time.time() < deadline:
        if (
            index.get("renamed-note.md") is not None
            and index.get("test-note.md") is None
        ):
            break
        time.sleep(0.02)

    assert index.get("renamed-note.md") is not None
    assert index.get("test-note.md") is None


def test_frontmatter_merge(vault_dir):
    """Existing frontmatter merged with new fields, body preserved."""
    import frontmatter
    from obsidian_vault_mcp.vault import read_file, write_file_atomic

    # Read original
    content, _ = read_file("test-note.md")
    post = frontmatter.loads(content)
    original_body = post.content

    # Merge new field
    post.metadata["new_field"] = "new_value"
    write_file_atomic("test-note.md", frontmatter.dumps(post))

    # Verify
    content2, _ = read_file("test-note.md")
    post2 = frontmatter.loads(content2)
    assert post2.metadata["status"] == "active"  # preserved
    assert post2.metadata["new_field"] == "new_value"  # added
    assert original_body.strip() in post2.content  # body preserved
