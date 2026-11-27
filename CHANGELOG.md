# Changelog

## 1.1.0 — 2026-11-27

- **`capy.yaml`** — one config file: browser settings plus any other MCP servers. The AI app starts only CapyBrowser; CapyBrowser starts the servers from the file and exposes their tools next to the browser tools.
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
