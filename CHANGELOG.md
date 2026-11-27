# Changelog

## 1.2.0 — 2026-11-27

- **Connector router.** The model now gets a fixed, compact tool set: the 6 browser tools plus `list_connectors`, `find_tools` and `call_tool`, instead of every tool of every MCP server. `find_tools` understands English and Russian with synonyms (email/почта/gmail, tasks/задачи/notion, code/код/github…) and returns one-line signatures; `call_tool` accepts arguments as an object or a JSON string, connects lazily, reconnects once after a crash, masks secrets and stays within `max_chars`.
- **Remote connectors:** `streamable-http` and `sse` servers with custom headers, Bearer tokens or OAuth 2.0 + PKCE (browser sign-in, tokens stored in `~/.capybrowser/oauth` with mode 600 and refreshed automatically).
- **capy.yaml:** new `browser` options `profile_dir` (persistent profile, closed gracefully so data is flushed) and `nav_timeout`; `browser` is the executable path (`path` still accepted); `.env` next to the file (or `env_file:`) for `${VAR}`; strict validation with the exact field in every error; hot reload on change without restarting the MCP client — an invalid edit keeps the last valid config.
- `capybrowser check [--connect]` validates the config and tests the connectors.
- Secrets from the config and common token formats are masked in connector answers and logs.
- Connector processes are tracked and killed together with the browser on exit and on `SIGINT`/`SIGTERM`/`SIGHUP`/`SIGBREAK`.
- `--profile-dir` flag; the MCP server reports the CapyBrowser version.
- Dependencies: `mcp>=1.13`, `httpx` added; `uv.lock` committed.

## 1.1.0 — 2026-11-27

- **`capy.yaml`** — one config file: browser settings plus any other MCP servers. The AI app starts only CapyBrowser; CapyBrowser starts the servers from the file and exposes their tools.
- `--config PATH`, `CAPYBROWSER_CONFIG`, `./capy.yaml` or `~/.capy.yaml`; command-line flags override the file.
- `${VAR}` environment expansion, so tokens stay out of the file; unknown sections, options and invalid YAML stop the start with a clear message.
- Tool-name clashes between servers are resolved as `server__tool`; a server that fails to start is skipped with a warning.
- `capybrowser init` creates a commented `capy.yaml`.
- New dependency: `pyyaml`.

## 1.0.0 — 2026-11-27

First public release.

- Six MCP tools: `web_search`, `open_url`, `click`, `type_text`, `scroll`, `go_back`.
- Drives the system Chrome / Edge / Brave / Chromium directly over the Chrome DevTools Protocol (WebSocket); no Playwright, Selenium or Puppeteer.
- Automatic browser discovery on Windows, macOS and Linux; lazy start on a free port with a temporary profile.
- Compact page view for small models: visible text plus numbered clickable elements, hard limit of 3500 characters (configurable).
- Reliable input on SPAs: real mouse events with a DOM-click fallback, `Input.insertText` with a native-setter fallback, input masks preserved.
- Handles popups, `target=_blank`, `alert`/`confirm`/`beforeunload`, overlays, scroll containers, shadow DOM, redirects, HTTP errors, hung pages.
- Self-healing: a crashed or hung browser is restarted and the last page is reopened.
- Guaranteed cleanup of the browser process tree and temporary profile on exit, `SIGINT`, `SIGTERM`; stale profiles from killed runs are removed.
- `file://` URLs disabled by default (`--allow-local-files`); configurable `--lang`, `--max-chars`, `--browser`, `--headed`.
- One-line install from GitHub: `uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser`.
