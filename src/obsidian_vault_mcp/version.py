"""Package version shared by MCP initialization and health output."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("obsidian-web-mcp")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "unknown"
