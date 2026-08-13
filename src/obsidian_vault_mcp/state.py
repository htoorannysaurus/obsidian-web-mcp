"""Process-wide state shared by the MCP server and tools."""

import time

from .frontmatter_index import FrontmatterIndex


frontmatter_index = FrontmatterIndex()
started_at = time.time()
