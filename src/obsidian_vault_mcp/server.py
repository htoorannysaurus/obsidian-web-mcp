"""Obsidian Vault MCP Server.

Exposes read/write access to an Obsidian vault over Streamable HTTP.
Designed to run behind Cloudflare Tunnel for secure remote access.
"""

import inspect
import logging
import sys
import time
from contextlib import asynccontextmanager
from functools import wraps
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult
from pydantic import Field

from .config import VAULT_MCP_PORT, VAULT_MCP_TOKEN, VAULT_PATH
from .errors import tool_failure_result
from .models import (
    FrontmatterUpdate,
    VaultBatchFrontmatterUpdateInput,
    VaultBatchReadInput,
    VaultDeleteInput,
    VaultEditInput,
    VaultFindAndReadInput,
    VaultInsertInput,
    VaultListInput,
    VaultMoveInput,
    VaultReadInput,
    VaultRecentInput,
    VaultSearchFrontmatterInput,
    VaultSearchInput,
    VaultWriteInput,
)
from .rate_limit import rate_limiter
from .responses import (
    ToolResponse,
    VaultBatchFrontmatterResult,
    VaultBatchReadResult,
    VaultBootstrapResult,
    VaultDeleteResult,
    VaultEditResult,
    VaultFindAndReadResult,
    VaultFrontmatterSearchResult,
    VaultInfoResult,
    VaultInsertResult,
    VaultListResult,
    VaultMoveResult,
    VaultReadResult,
    VaultRecentResult,
    VaultSearchResult,
    VaultWriteResult,
)
from .state import frontmatter_index
from .tools.info import vault_bootstrap as _vault_bootstrap
from .tools.info import vault_info as _vault_info
from .tools.manage import (
    vault_delete as _vault_delete,
)
from .tools.manage import (
    vault_list as _vault_list,
)
from .tools.manage import (
    vault_move as _vault_move,
)
from .tools.manage import (
    vault_recent as _vault_recent,
)
from .tools.read import vault_batch_read as _vault_batch_read
from .tools.read import vault_read as _vault_read
from .tools.search import (
    vault_find_and_read as _vault_find_and_read,
)
from .tools.search import (
    vault_search as _vault_search,
)
from .tools.search import (
    vault_search_frontmatter as _vault_search_frontmatter,
)
from .tools.write import (
    vault_batch_frontmatter_update as _vault_batch_frontmatter_update,
)
from .tools.write import (
    vault_edit as _vault_edit,
)
from .tools.write import (
    vault_insert as _vault_insert,
)
from .tools.write import (
    vault_write as _vault_write,
)
from .version import __version__

logger = logging.getLogger(__name__)

VaultPath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=500,
        description="Relative path from the vault root, such as daily-logs/2026/2026-07/note.md",
    ),
]
PathPrefix = Annotated[
    str | None,
    Field(
        max_length=500,
        description="Optional vault-relative folder prefix, such as daily-logs, personal-context, work-context, or contacts",
    ),
]
ExpectedRevision = Annotated[
    str | None,
    Field(
        min_length=64,
        max_length=64,
        description="Optional SHA-256 revision returned by vault_read; rejects stale writes",
    ),
]

VaultBootstrapResponse = ToolResponse[VaultBootstrapResult]
VaultInfoResponse = ToolResponse[VaultInfoResult]
VaultReadResponse = ToolResponse[VaultReadResult]
VaultBatchReadResponse = ToolResponse[VaultBatchReadResult]
VaultWriteResponse = ToolResponse[VaultWriteResult]
VaultEditResponse = ToolResponse[VaultEditResult]
VaultInsertResponse = ToolResponse[VaultInsertResult]
VaultBatchFrontmatterResponse = ToolResponse[VaultBatchFrontmatterResult]
VaultSearchResponse = ToolResponse[VaultSearchResult]
VaultFrontmatterSearchResponse = ToolResponse[VaultFrontmatterSearchResult]
VaultFindAndReadResponse = ToolResponse[VaultFindAndReadResult]
VaultListResponse = ToolResponse[VaultListResult]
VaultRecentResponse = ToolResponse[VaultRecentResult]
VaultMoveResponse = ToolResponse[VaultMoveResult]
VaultDeleteResponse = ToolResponse[VaultDeleteResult]


@asynccontextmanager
async def lifespan(server):
    """Per-session lifespan. Index is started once in main() at process startup,
    not here -- FastMCP invokes this per MCP session, and re-indexing 3000+
    files on every claude.ai connection caused 60s+ tool-list timeouts.
    """
    logger.debug(f"MCP session opened. Vault: {VAULT_PATH}")
    yield {"frontmatter_index": frontmatter_index, "bootstrapped": False}
    logger.debug("MCP session closed.")


def _session_state(ctx: Context | None) -> dict[str, Any] | None:
    """Return mutable state for the current MCP session when one exists."""
    if ctx is None:
        return None
    state = ctx.request_context.lifespan_context
    return state if isinstance(state, dict) else None


def timed_tool(name, *, write: bool = False, requires_bootstrap: bool = True):
    """Log server-side tool duration without changing the registered signature."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            bound = inspect.signature(func).bind_partial(*args, **kwargs)
            state = _session_state(bound.arguments.get("ctx"))
            if requires_bootstrap and state is not None and not state.get("bootstrapped", False):
                return tool_failure_result(
                    name,
                    "Call vault_bootstrap before using any other Notebook tool in this session",
                    error_type="BOOTSTRAP_REQUIRED",
                    recoverable=True,
                )
            if not rate_limiter.check(write=write):
                kind = "write" if write else "read"
                return tool_failure_result(
                    name,
                    f"{kind.capitalize()} rate limit exceeded; retry shortly",
                    error_type="RATE_LIMITED",
                    recoverable=True,
                    data={"retry_after_seconds": 60},
                )
            try:
                return func(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.info("Tool %s completed in %.2f ms", name, elapsed_ms)
                if write:
                    target = (
                        bound.arguments.get("path")
                        or bound.arguments.get("source")
                        or "batch"
                    )
                    logger.info("Vault write attempt: tool=%s target=%s", name, target)

        return wrapper

    return decorator


def tool_result(
    tool_name: str, result: dict[str, Any]
) -> dict[str, Any] | CallToolResult:
    """Convert top-level tool failures into real MCP error responses."""
    if "error" in result:
        details = {key: value for key, value in result.items() if key != "error"}
        return tool_failure_result(
            tool_name, str(result["error"]), data=details
        )
    return result


# Create the MCP server
mcp = FastMCP(
    "Notebook",
    instructions=(
        "Joe's personal Notebook (Obsidian vault): daily journal plus work and "
        "personal context.\n"
        "\n"
        "Layout:\n"
        "- daily-logs/YYYY/YYYY-MM/YYYY-MM-DD.md - the daily journal. For "
        "questions about today or recent days, read the latest daily log "
        "first.\n"
        "- contacts/ - people and relationship context; check here when a "
        "person is named before asking Joe who they are.\n"
        "- work-context/ - PwC, clients, career, meetings.\n"
        "- personal-context/ - health, money, ideas, reflections, reference "
        "material.\n"
        "- investigations/ - living research documents; check its INDEX.md "
        "before creating a new one.\n"
        "\n"
        "Retrieval: use vault_find_and_read for topic questions (it returns "
        "the matched sections, recency-ranked); vault_search for exact "
        "phrases or names (literal by default, regex=true for patterns); "
        "vault_recent(path_prefix=\"daily-logs\") for the latest entries.\n"
        "\n"
        "Call vault_bootstrap before using any other Notebook tool. The server "
        "enforces this once per MCP session. Follow the returned conventions, "
        "load only what the current task needs, and do not sweep the vault or "
        "expose unrelated personal information."
    ),
    stateless_http=False,
    json_response=True,
    streamable_http_path="/",
    lifespan=lifespan,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
            "vault-mcp.joehtoo.dev",
        ],
    ),
)
# FastMCP does not currently expose a public version constructor argument. Set
# the underlying low-level server version so MCP initialize advertises this
# package release instead of the installed MCP SDK version.
mcp._mcp_server.version = __version__


# --- Register all tools ---


@mcp.tool(
    name="vault_bootstrap",
    description="REQUIRED FIRST CALL in every MCP session. Unlocks the other Notebook tools for this session and returns the curated navigation map, working rules, privacy boundaries, enforced path exclusions, and recommended next tool calls.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_bootstrap", requires_bootstrap=False)
def vault_bootstrap(ctx: Context | None = None) -> VaultBootstrapResponse:
    result = tool_result("vault_bootstrap", _vault_bootstrap())
    if not isinstance(result, CallToolResult):
        state = _session_state(ctx)
        if state is not None:
            state["bootstrapped"] = True
    return result


@mcp.tool(
    name="vault_info",
    description="Use this when the user asks which Notebook connector version is running or what the connector can do. Returns model-visible version and capability metadata.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_info")
def vault_info(ctx: Context | None = None) -> VaultInfoResponse:
    return tool_result("vault_info", _vault_info())


@mcp.tool(
    name="vault_read",
    description="Read a file from the Obsidian vault, returning content, metadata, and parsed YAML frontmatter.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_read")
def vault_read(
    path: VaultPath,
    ctx: Context | None = None,
    include_content: Annotated[bool, Field(description="Return note content")] = True,
    body_only: Annotated[
        bool, Field(description="Strip YAML frontmatter from returned content")
    ] = False,
    start_line: Annotated[int, Field(ge=1, description="First line to return")] = 1,
    max_lines: Annotated[
        int | None, Field(ge=1, le=10_000, description="Maximum lines to return")
    ] = None,
    max_chars: Annotated[
        int | None,
        Field(ge=1, le=1_000_000, description="Maximum characters to return"),
    ] = None,
) -> VaultReadResponse:
    """Read a file from the vault."""
    inp = VaultReadInput(
        path=path,
        include_content=include_content,
        body_only=body_only,
        start_line=start_line,
        max_lines=max_lines,
        max_chars=max_chars,
    )
    return tool_result("vault_read", _vault_read(**inp.model_dump()))


@mcp.tool(
    name="vault_batch_read",
    description="Read multiple files from the vault in one call. Handles missing files gracefully.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_batch_read")
def vault_batch_read(
    paths: Annotated[list[VaultPath], Field(min_length=1, max_length=20)],
    ctx: Context | None = None,
    include_content: Annotated[bool, Field(description="Return each file body")] = True,
    max_chars: Annotated[
        int | None,
        Field(
            ge=1,
            le=1_000_000,
            description="Maximum characters to return per file",
        ),
    ] = None,
) -> VaultBatchReadResponse:
    """Read multiple files at once."""
    inp = VaultBatchReadInput(
        paths=paths, include_content=include_content, max_chars=max_chars
    )
    return tool_result(
        "vault_batch_read",
        _vault_batch_read(inp.paths, inp.include_content, inp.max_chars),
    )


@mcp.tool(
    name="vault_write",
    description="Write a file to the Obsidian vault. Supports frontmatter merging with existing files. Creates parent directories by default.",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@timed_tool("vault_write", write=True)
def vault_write(
    path: VaultPath,
    content: str,
    ctx: Context | None = None,
    create_dirs: bool = True,
    merge_frontmatter: bool = False,
    expected_revision: ExpectedRevision = None,
) -> VaultWriteResponse:
    """Write a file to the vault."""
    inp = VaultWriteInput(
        path=path,
        content=content,
        create_dirs=create_dirs,
        merge_frontmatter=merge_frontmatter,
        expected_revision=expected_revision,
    )
    return tool_result("vault_write", _vault_write(**inp.model_dump()))


@mcp.tool(
    name="vault_edit",
    description="Edit an existing vault file by replacing old_string with new_string. By default old_string must match EXACTLY ONCE -- pass replace_all=true to replace every occurrence. Atomic write; safe alongside Obsidian Sync. Cannot create new files (use vault_write).",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@timed_tool("vault_edit", write=True)
def vault_edit(
    path: VaultPath,
    old_string: str,
    new_string: str,
    ctx: Context | None = None,
    replace_all: bool = False,
    expected_revision: ExpectedRevision = None,
) -> VaultEditResponse:
    """Find-and-replace edit on a vault file."""
    inp = VaultEditInput(
        path=path,
        old_string=old_string,
        new_string=new_string,
        replace_all=replace_all,
        expected_revision=expected_revision,
    )
    return tool_result("vault_edit", _vault_edit(**inp.model_dump()))


@mcp.tool(
    name="vault_insert",
    description="Append, prepend, or insert content immediately after an exact Markdown heading. Supports revision checks to prevent stale writes.",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@timed_tool("vault_insert", write=True)
def vault_insert(
    path: VaultPath,
    content: str,
    ctx: Context | None = None,
    position: Literal["append", "prepend", "after_heading"] = "append",
    heading: Annotated[
        str | None,
        Field(description="Exact Markdown heading required for after_heading"),
    ] = None,
    expected_revision: ExpectedRevision = None,
) -> VaultInsertResponse:
    inp = VaultInsertInput(
        path=path,
        content=content,
        position=position,
        heading=heading,
        expected_revision=expected_revision,
    )
    return tool_result("vault_insert", _vault_insert(**inp.model_dump()))


@mcp.tool(
    name="vault_batch_frontmatter_update",
    description="Update YAML frontmatter fields on multiple files without changing body content. Each update merges new fields into existing frontmatter.",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_batch_frontmatter_update", write=True)
def vault_batch_frontmatter_update(
    updates: list[FrontmatterUpdate],
    ctx: Context | None = None,
) -> VaultBatchFrontmatterResponse:
    """Batch update frontmatter fields."""
    inp = VaultBatchFrontmatterUpdateInput(updates=updates)
    return tool_result(
        "vault_batch_frontmatter_update",
        _vault_batch_frontmatter_update([update.model_dump() for update in inp.updates])
    )


@mcp.tool(
    name="vault_search",
    description="Search for text across vault files. Uses ripgrep if available, falls back to Python. Matches literal text by default; set regex=true to search with a regular expression pattern. Returns matches grouped by file (recency-ranked, capped per file) with context and frontmatter excerpts.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_search")
def vault_search(
    query: Annotated[str, Field(min_length=1, max_length=200)],
    ctx: Context | None = None,
    path_prefix: PathPrefix = None,
    file_pattern: str = "*.md",
    max_results: int = 20,
    context_lines: int = 2,
    regex: bool = False,
) -> VaultSearchResponse:
    """Search vault file contents."""
    inp = VaultSearchInput(
        query=query,
        path_prefix=path_prefix,
        file_pattern=file_pattern,
        max_results=max_results,
        context_lines=context_lines,
        regex=regex,
    )
    return tool_result(
        "vault_search",
        _vault_search(
            inp.query,
            inp.path_prefix,
            inp.file_pattern,
            inp.max_results,
            inp.context_lines,
            inp.regex,
        )
    )


@mcp.tool(
    name="vault_search_frontmatter",
    description="Search vault files by YAML frontmatter field values. Queries an in-memory index for fast results. Supports exact match, contains, and field-exists queries.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_search_frontmatter")
def vault_search_frontmatter(
    field: str,
    ctx: Context | None = None,
    value: str = "",
    match_type: Literal["exact", "contains", "exists"] = "exact",
    path_prefix: PathPrefix = None,
    max_results: int = 20,
) -> VaultFrontmatterSearchResponse:
    """Search by frontmatter fields."""
    inp = VaultSearchFrontmatterInput(
        field=field,
        value=value,
        match_type=match_type,
        path_prefix=path_prefix,
        max_results=max_results,
    )
    return tool_result(
        "vault_search_frontmatter",
        _vault_search_frontmatter(
            inp.field, inp.value, inp.match_type, inp.path_prefix, inp.max_results
        )
    )


@mcp.tool(
    name="vault_find_and_read",
    description="Search the vault and return the enclosing Markdown section of each matching file in one call (recency-ranked), reducing connector round trips. Matches literal text by default; set regex=true to search with a regular expression pattern.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_find_and_read")
def vault_find_and_read(
    query: Annotated[str, Field(min_length=1, max_length=200)],
    ctx: Context | None = None,
    path_prefix: PathPrefix = None,
    max_files: int = 5,
    context_lines: int = 2,
    max_chars_per_file: int = 4_000,
    regex: bool = False,
) -> VaultFindAndReadResponse:
    inp = VaultFindAndReadInput(
        query=query,
        path_prefix=path_prefix,
        max_files=max_files,
        context_lines=context_lines,
        max_chars_per_file=max_chars_per_file,
        regex=regex,
    )
    return tool_result(
        "vault_find_and_read", _vault_find_and_read(**inp.model_dump())
    )


@mcp.tool(
    name="vault_list",
    description="List directory contents in the vault. Supports recursion depth, file/dir filtering, and glob patterns. Excludes .obsidian, .trash, .git directories, plus any configured via EXCLUDED_DIRS_EXTRA.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_list")
def vault_list(
    ctx: Context | None = None,
    path: str = "",
    depth: int = 1,
    include_files: bool = True,
    include_dirs: bool = True,
    pattern: str | None = None,
) -> VaultListResponse:
    """List vault directory contents."""
    inp = VaultListInput(
        path=path,
        depth=depth,
        include_files=include_files,
        include_dirs=include_dirs,
        pattern=pattern,
    )
    return tool_result(
        "vault_list",
        _vault_list(
            inp.path, inp.depth, inp.include_files, inp.include_dirs, inp.pattern
        )
    )


@mcp.tool(
    name="vault_recent",
    description="Return recently modified or frontmatter-dated notes, optionally filtered by folder, glob, and exact tag membership.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@timed_tool("vault_recent")
def vault_recent(
    ctx: Context | None = None,
    path_prefix: PathPrefix = None,
    pattern: str = "*.md",
    tag: str | None = None,
    limit: int = 10,
    sort_by: Literal["modified", "frontmatter_date"] = "modified",
    date_field: str = "date",
) -> VaultRecentResponse:
    inp = VaultRecentInput(
        path_prefix=path_prefix,
        pattern=pattern,
        tag=tag,
        limit=limit,
        sort_by=sort_by,
        date_field=date_field,
    )
    return tool_result("vault_recent", _vault_recent(**inp.model_dump()))


@mcp.tool(
    name="vault_move",
    description="Move a file or directory within the vault. Validates both source and destination paths.",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@timed_tool("vault_move", write=True)
def vault_move(
    source: VaultPath,
    destination: VaultPath,
    ctx: Context | None = None,
    create_dirs: bool = True,
) -> VaultMoveResponse:
    """Move a file or directory."""
    inp = VaultMoveInput(
        source=source, destination=destination, create_dirs=create_dirs
    )
    return tool_result(
        "vault_move", _vault_move(inp.source, inp.destination, inp.create_dirs)
    )


@mcp.tool(
    name="vault_delete",
    description="Delete a file by moving it to .trash/ in the vault root. Requires confirm=true as a safety gate. Does NOT hard delete.",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@timed_tool("vault_delete", write=True)
def vault_delete(
    path: VaultPath,
    ctx: Context | None = None,
    confirm: bool = False,
) -> VaultDeleteResponse:
    """Delete a file (move to .trash/)."""
    inp = VaultDeleteInput(path=path, confirm=confirm)
    return tool_result("vault_delete", _vault_delete(inp.path, inp.confirm))


def main():
    """Entry point. Run with streamable HTTP transport."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not VAULT_PATH.is_dir():
        logger.error(f"Vault path does not exist: {VAULT_PATH}")
        sys.exit(1)

    if not VAULT_MCP_TOKEN:
        logger.warning("VAULT_MCP_TOKEN is not set -- auth will reject all requests")

    # Build the frontmatter index ONCE at process startup. Doing this here
    # (instead of in lifespan) ensures the cost is paid once, not per MCP
    # session -- otherwise claude.ai's tool-list registration times out.
    logger.info(f"Starting vault MCP server. Vault: {VAULT_PATH}")
    frontmatter_index.start()
    logger.info(
        f"Frontmatter index built: {frontmatter_index.file_count} files indexed"
    )

    # Build the Starlette app with auth middleware and OAuth endpoints
    try:
        from .auth import BearerAuthMiddleware
        from .health import health_routes
        from .oauth import oauth_routes

        app = mcp.streamable_http_app()

        # Mount OAuth routes (these are excluded from bearer auth via the middleware)
        for route in [*oauth_routes, *health_routes]:
            app.routes.insert(0, route)

        app.add_middleware(BearerAuthMiddleware)
        logger.info(
            f"Starting server on port {VAULT_MCP_PORT} with bearer auth + OAuth"
        )

        import uvicorn

        uvicorn.run(
            app,
            host="0.0.0.0",
            port=VAULT_MCP_PORT,
            log_level="info",
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
    except Exception as e:
        logger.exception("Could not build authenticated MCP app; refusing to start")
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
