"""Typed MCP response contracts for Notebook tools."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_serializer


class ToolResult(BaseModel):
    """Base response that preserves forward-compatible fields and omits absent ones."""

    model_config = ConfigDict(extra="allow")

    @model_serializer(mode="wrap")
    def _omit_none(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class NextAction(ToolResult):
    tool: str
    arguments: dict[str, Any]
    purpose: str


class ToolFailureDetails(ToolResult):
    type: str
    message: str
    recoverable: bool
    data: dict[str, Any]
    next_actions: list[NextAction]


class ToolFailureResult(ToolResult):
    error: ToolFailureDetails


class ToolResponse[SuccessResult: ToolResult](
    RootModel[SuccessResult | ToolFailureResult]
):
    """Wire contract shared by successful output and native MCP error output."""

    # MCP requires every advertised outputSchema to be rooted at an object.
    # Pydantic otherwise emits a RootModel union as a bare top-level `anyOf`,
    # which strict clients such as Claude Code reject during tools/list.
    model_config = ConfigDict(json_schema_extra={"type": "object"})


class FileMetadata(ToolResult):
    size: int = Field(description="File size in bytes")
    modified: str = Field(description="UTC ISO-8601 modification time")
    created: str = Field(description="UTC ISO-8601 creation time")
    revision: str | None = Field(
        default=None,
        description="SHA-256 content revision used for optimistic write checks",
    )


class RecommendedCall(ToolResult):
    tool: str
    arguments: dict[str, Any]
    purpose: str


class VaultBootstrapResult(ToolResult):
    connector: str
    configured: bool
    guide: str
    excluded_path_components: list[str]
    exclusion_rules: list[str]
    recommended_next_calls: list[RecommendedCall]
    guide_path: str | None = None
    bootstrap_path_exception: str | None = None
    metadata: FileMetadata | None = None


class VaultInfoResult(ToolResult):
    connector: str
    package: str
    version: str
    vault: str
    indexed_files: int
    bootstrap_configured: bool
    capabilities: list[str]


class VaultReadResult(ToolResult):
    path: str
    metadata: FileMetadata
    frontmatter: dict[str, Any] | None
    content: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    total_lines: int | None = None
    truncated: bool | None = None


class BatchFileResult(ToolResult):
    path: str
    metadata: FileMetadata | None = None
    frontmatter: dict[str, Any] | None = None
    content: str | None = None
    truncated: bool | None = None
    error: str | None = None


class VaultBatchReadResult(ToolResult):
    files: list[BatchFileResult]
    found: int
    missing: int


class VaultWriteResult(ToolResult):
    path: str
    created: bool
    size: int
    revision: str


class VaultEditResult(ToolResult):
    path: str
    replacements: int
    size: int
    revision: str


class VaultInsertResult(ToolResult):
    path: str
    position: str
    size: int
    revision: str


class FrontmatterUpdateResult(ToolResult):
    path: str
    updated: bool
    error: str | None = None


class VaultBatchFrontmatterResult(ToolResult):
    results: list[FrontmatterUpdateResult]


class SearchMatch(ToolResult):
    line_number: int
    heading: str | None
    match_context: str


class SearchFileResult(ToolResult):
    path: str
    modified: str | None
    frontmatter_excerpt: dict[str, Any] | None
    matches: list[SearchMatch]


class VaultSearchResult(ToolResult):
    results: list[SearchFileResult]
    total_matches: int
    files: int
    truncated: bool


class FrontmatterSearchResult(ToolResult):
    path: str
    frontmatter: dict[str, Any]
    title: str


class VaultFrontmatterSearchResult(ToolResult):
    results: list[FrontmatterSearchResult]
    total: int
    returned: int
    truncated: bool


class SectionResult(ToolResult):
    heading: str | None
    start_line: int


class FoundFileResult(ToolResult):
    path: str
    modified: str | None
    frontmatter: dict[str, Any] | None
    sections: list[SectionResult]
    content: str
    truncated: bool


class VaultFindAndReadResult(ToolResult):
    query: str
    matches: list[SearchFileResult]
    files: list[FoundFileResult]
    found: int
    truncated_search: bool


class ListEntry(ToolResult):
    name: str
    path: str
    type: str
    size: int
    modified: str


class VaultListResult(ToolResult):
    items: list[ListEntry]
    total: int


class RecentFileResult(ToolResult):
    path: str
    metadata: FileMetadata
    frontmatter: dict[str, Any] | None


class VaultRecentResult(ToolResult):
    results: list[RecentFileResult]
    returned: int
    total_candidates: int
    truncated: bool


class VaultMoveResult(ToolResult):
    source: str
    destination: str
    moved: bool


class VaultDeleteResult(ToolResult):
    path: str
    deleted: bool
