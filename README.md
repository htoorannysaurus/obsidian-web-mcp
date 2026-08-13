# obsidian-web-mcp

A secure, remote-accessible MCP server that gives LLMs read/write access to your Obsidian vault from anywhere -- your desktop, your phone, a hotel Wi-Fi network. Unlike local-only Obsidian MCP servers, this one runs over HTTPS with real authentication, so Claude (or any MCP client) can reach your vault whether you're at your desk or not.

It reads and writes markdown files on disk, parses YAML frontmatter, maintains an in-memory frontmatter index for fast queries, and handles full-text search -- all behind OAuth 2.0 authentication and a Cloudflare Tunnel that never exposes your machine directly to the internet.

## Why This Exists

There are many Obsidian MCP servers. Most are local stdio servers -- they work when Claude Code is running on the same machine as your vault. That's useful, but it means:

- **Claude.ai (web) can't reach your vault.** The browser-based Claude has no way to connect to a local stdio server.
- **Claude on your phone can't reach your vault.** Same problem.
- **If you use Obsidian Sync, local MCP servers can corrupt files.** Non-atomic writes create partial files that Sync propagates to every device.

This server solves all three. It runs as a persistent HTTP service on the machine where your vault lives, tunneled securely through Cloudflare, and authenticates via OAuth 2.0 -- the same protocol Claude uses for Gmail, Google Calendar, and other integrations. The result: your vault becomes a first-class MCP connector available everywhere Claude is.

## Architecture

```
+----------+     +------------+     +-----------------+     +------------------+
| Obsidian | <-> | Filesystem | <-> | obsidian-web-mcp| <-> | Cloudflare       |
| (app)    |     | (*.md)     |     | (MCP over HTTPS)|     | Tunnel           |
+----------+     +------------+     +-----------------+     +------------------+
                                                                   |
                                                            +------+-------+
                                                            | Claude       |
                                                            | (web/desktop/|
                                                            |  mobile)     |
                                                            +--------------+
```

Your vault files never leave your machine. Cloudflare Tunnel creates an outbound-only connection from your server to Cloudflare's edge -- no inbound ports opened, no public IP exposed, no port forwarding. Claude connects to the Cloudflare edge, which relays requests through the tunnel to your server.

Obsidian and the MCP server both operate on the same directory of markdown files. The server uses atomic writes (write-to-temp-then-rename) so Obsidian Sync and the server never conflict.

## Security Model

This is a server that provides network access to your personal notes. Security is not optional.

**Authentication is enforced on every request.** The server implements OAuth 2.0 authorization code flow with mandatory S256 PKCE for initial client authentication (what Claude uses when you connect the integration), then issues a distinct signed, expiring bearer token for each authorization. The authorization endpoint is intended to sit behind a Cloudflare Access identity policy; machine-to-machine discovery and token exchange remain reachable without an interactive login. The `client_credentials` grant is disabled so it cannot bypass that human authorization step. Existing deployments can continue accepting the configured root token during migration, while new OAuth sessions receive expiring client tokens. No request reaches a tool function without a valid token.

**Your vault is never exposed directly to the internet.** The recommended deployment uses a Cloudflare Tunnel -- an outbound-only encrypted connection. Your machine opens no inbound ports. You can layer Cloudflare Access on top for additional authentication (SSO, device posture checks, IP restrictions) if you want defense in depth.

**Path traversal is blocked at the filesystem layer.** Every file operation resolves paths against the vault root directory and rejects any attempt to escape it -- `..` traversal, symlink following, null byte injection, and dotfile access (`.obsidian`, `.git`, `.trash`) are all caught before they reach the filesystem. The server will never read or write outside your vault directory.

**Writes are atomic.** Every file write goes to a temporary file first, then atomically replaces the target via `os.replace()`. This guarantees that neither Obsidian nor Obsidian Sync ever sees a partially-written file -- the operation either completes fully or doesn't happen at all.

**Safety limits prevent abuse.** Writes are capped at 1MB per file, batch operations at 20 files per request, and search results at 50 matches. Deletions are soft -- files move to `.trash/` rather than being permanently removed, matching Obsidian's own behavior. The delete tool also requires an explicit `confirm=true` parameter as a safety gate.

## Tools

| Tool | Description |
|------|-------------|
| `vault_bootstrap` | Required first call in each MCP session; unlock the other tools and return the configured vault map, working rules, privacy boundaries, live path exclusions, and recommended next calls |
| `vault_info` | Return model-visible connector version and capability metadata |
| `vault_read` | Read a file, returning content, metadata, and parsed YAML frontmatter |
| `vault_batch_read` | Read multiple files in one call; handles missing files gracefully |
| `vault_write` | Write a file with optional frontmatter merging; creates parent dirs |
| `vault_edit` | Safely replace an exact string, with optional revision protection |
| `vault_insert` | Append, prepend, or insert beneath an exact Markdown heading |
| `vault_batch_frontmatter_update` | Update YAML frontmatter fields on multiple files without touching body content |
| `vault_search` | Full-text search across vault files (uses ripgrep if available, falls back to Python). Literal by default; set `regex=true` for a pattern. Results are grouped by file, capped at 3 matches per file, and ranked newest-modified first |
| `vault_search_frontmatter` | Query the in-memory frontmatter index by field value, substring, or field existence |
| `vault_find_and_read` | Search and return the enclosing Markdown section around each match (not just the head of the file) for the top matching files, recency-ranked, in one connector round trip |
| `vault_list` | List directory contents with recursion depth, glob filtering, and file/dir toggles |
| `vault_recent` | Find recently modified or frontmatter-dated notes by folder, glob, or tag |
| `vault_move` | Move or rename a file or directory within the vault |
| `vault_delete` | Soft-delete a file by moving it to `.trash/` (requires explicit confirmation) |

Read and write tools return structured MCP output. Reads support body-only,
line-range, and character-limit options. Read metadata includes a SHA-256
`revision` that can be passed to write, edit, or insert operations to reject a
stale update after an Obsidian Sync change.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- An Obsidian vault (any directory of markdown files)
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (only needed for remote access)
- A domain managed by Cloudflare (only needed for remote access)

## Quick Start

### Local development

```bash
# Clone and enter the project
git clone https://github.com/yourname/obsidian-web-mcp.git
cd obsidian-web-mcp

# Generate auth tokens
export VAULT_MCP_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
export VAULT_OAUTH_CLIENT_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")

# Point at your vault
export VAULT_PATH="$HOME/Obsidian/MyVault"

# Run the server
uv run vault-mcp
```

The server starts on port 8420 by default. It serves MCP over Streamable HTTP at `/`.
Operational health is available at `/health`, including the advertised package version.

## Configuration

All configuration is via environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VAULT_PATH` | Yes | `~/Obsidian/MyVault` | Absolute path to your Obsidian vault directory |
| `VAULT_BOOTSTRAP_FILE` | No | (none) | Vault-relative Markdown guide returned by `vault_bootstrap`; may name one exact file inside an otherwise excluded folder |
| `VAULT_MCP_TOKEN` | Yes | (none) | 256-bit bearer token for authenticating MCP requests |
| `VAULT_MCP_PORT` | No | `8420` | Port the HTTP server listens on |
| `VAULT_OAUTH_CLIENT_ID` | No | `vault-mcp-client` | OAuth 2.0 client ID for Claude integration |
| `VAULT_OAUTH_CLIENT_SECRET` | Yes | (none) | OAuth 2.0 client secret for Claude integration |
| `OAUTH_TOKEN_TTL` | No | `86400` | Lifetime in seconds for newly issued OAuth access tokens |
| `REVOKED_OAUTH_CLIENT_IDS` | No | (none) | Comma-separated OAuth client IDs whose signed tokens should be rejected |

Generate tokens with: `python -c "import secrets; print(secrets.token_hex(32))"`

## Connecting to Claude

The Claude desktop and mobile apps can connect to remote MCP servers via OAuth.

1. Start the server (locally or behind a tunnel)
2. Open Claude and go to **Settings > Integrations > Add Integration**
3. Enter your server URL (e.g. `https://vault-mcp.yourdomain.com`)
4. Enter the OAuth client ID and client secret you configured
5. Claude will discover the OAuth endpoints automatically and open a browser window
6. The server auto-approves the authorization (single-user model) and redirects back
7. Claude now has access to all fifteen vault tools -- on desktop and mobile

On initialization, the server tells fresh agents to call `vault_bootstrap`, and
every other tool returns `BOOTSTRAP_REQUIRED` until they do. The gate resets for
each MCP session, so one agent cannot unlock the Notebook for another. Configure
`VAULT_BOOTSTRAP_FILE` to keep the deployment-specific navigation and working
guidance editable inside the vault instead of hardcoding it in the server. If it
is unset, the tool returns a safe
generic starting sequence.

The bootstrap response also reports `excluded_path_components`, the universal
dot-path and symlink exclusion rules, and the configured bootstrap-file exception.
These values are generated from the live server configuration rather than copied
into the guide, so an agent sees the same boundaries the filesystem tools enforce.

For local-only use (no tunnel), point Claude at `http://localhost:8420`.

## Remote Access with Cloudflare Tunnel

To make the server accessible from anywhere:

```bash
# Install cloudflared
brew install cloudflare/cloudflare/cloudflared

# Set your desired hostname and run the interactive setup
export VAULT_MCP_HOSTNAME="vault-mcp.yourdomain.com"
./scripts/setup-tunnel.sh
```

The script authenticates with Cloudflare, creates a tunnel, writes the config, and sets up the DNS record. You will need a domain managed by Cloudflare.

After setup, add your tunnel hostname to the `allowed_hosts` list in `server.py` so the MCP library's DNS rebinding protection accepts requests from your domain:

```python
allowed_hosts=[
    "127.0.0.1:*",
    "localhost:*",
    "[::1]:*",
    "vault-mcp.yourdomain.com",  # add your hostname here
],
```

## Production Deployment (macOS)

For always-on operation, use launchd to run both the MCP server and the Cloudflare Tunnel as persistent background services that start at login and restart on failure.

### 1. Edit the plist templates

```bash
cp scripts/launchd/com.example.vault-mcp.plist ~/Library/LaunchAgents/
cp scripts/launchd/com.example.cloudflared-vault.plist ~/Library/LaunchAgents/
```

Open each plist and replace the placeholder tokens:
- `REPLACE_WITH_UV_PATH` -- path to `uv` binary (run `which uv`)
- `REPLACE_WITH_PROJECT_PATH` -- absolute path to this project directory
- `REPLACE_WITH_VAULT_PATH` -- absolute path to your Obsidian vault
- `REPLACE_WITH_TOKEN` -- your `VAULT_MCP_TOKEN` value
- `REPLACE_WITH_OAUTH_SECRET` -- your `VAULT_OAUTH_CLIENT_SECRET` value
- `REPLACE_WITH_HOME` -- your home directory (e.g. `/Users/yourname`)
- `REPLACE_WITH_CLOUDFLARED_PATH` -- path to `cloudflared` binary (run `which cloudflared`)

### 2. Load the services

```bash
launchctl load ~/Library/LaunchAgents/com.example.vault-mcp.plist
launchctl load ~/Library/LaunchAgents/com.example.cloudflared-vault.plist
```

Both services are configured with `RunAtLoad` (start at login) and `KeepAlive` (restart on failure). They will survive reboots.

### 3. Verify

```bash
# Check both services are running
launchctl list | grep vault

# Test the server responds
curl -s http://localhost:8420/.well-known/oauth-authorization-server

# Check logs
tail -f ~/Library/Logs/vault-mcp-error.log
```

## Obsidian Sync Compatibility

The server coexists with Obsidian Sync (or any file-based sync mechanism) without conflict. All writes use atomic file replacement (`write-to-temp-then-rename`), which means:

- Obsidian never sees a half-written file
- If Sync and the MCP server write to the same file simultaneously, the last write wins (standard filesystem semantics) but neither write is corrupted
- The frontmatter index watches for filesystem changes via `watchdog` and updates automatically when Sync brings in new files

## Development

### Running tests

```bash
PYTHONPATH=src uv run --with pytest --with pytest-asyncio pytest tests/ -v
```

Tests use temporary directories and never touch your real vault.

### Project structure

```
src/obsidian_vault_mcp/
    auth.py                 # Bearer token middleware (Starlette)
    config.py               # Environment variable configuration
    frontmatter_index.py    # In-memory YAML frontmatter index with filesystem watcher
    health.py               # Operational health endpoint
    models.py               # Pydantic input validation models
    oauth.py                # OAuth 2.0 authorization code flow with PKCE
    rate_limit.py           # In-process read/write rate limiting
    serialization.py        # YAML/Python to JSON-safe value conversion
    server.py               # FastMCP server setup, tool registration, entry point
    state.py                # Process-wide frontmatter index and uptime state
    tokens.py               # Signed, expiring, client-bound OAuth access tokens
    version.py              # Package version advertised during MCP initialization
    vault.py                # Core filesystem operations (path security, atomic writes)
    tools/
        info.py             # Connector metadata and configurable agent bootstrap
        manage.py           # list, move, delete tools
        read.py             # read, batch_read tools
        search.py           # full-text search, frontmatter search tools
        write.py            # write, batch_frontmatter_update tools
tests/
    conftest.py             # Shared fixtures (temp vault with sample files)
    test_frontmatter.py     # Frontmatter index and query tests
    test_tools.py           # Integration tests for tool functions
    test_vault.py           # Path resolution and file operation tests
scripts/
    setup-tunnel.sh         # Interactive Cloudflare Tunnel setup
    launchd/                # macOS launchd plist templates
```

## License

MIT -- see [LICENSE](LICENSE).
