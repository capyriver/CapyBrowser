# Changelog

## 1.1.0 — 2026-11-27

- **`capy.yaml` + `capybrowser agent`**: one config file for the model (Ollama or any OpenAI-compatible API: LM Studio, llama.cpp, vLLM, OpenRouter, OpenAI), the built-in browser and any extra MCP servers. Interactive chat or one-shot tasks, no MCP app required.
- `capybrowser init` creates a commented `capy.yaml`.
- `${VAR}` environment expansion in the config (keep API keys out of the file).
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
