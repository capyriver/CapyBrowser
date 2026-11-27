<p align="center">
  <img src="assets/banner.svg" width="100%" alt="CapyBrowser: give any LLM a real browser" />
</p>

<p align="center">
  A single-file MCP server that drives your system Chrome / Edge / Brave / Chromium over the DevTools Protocol.<br />
  No Playwright. No Selenium. No Puppeteer. One link to install.
</p>

<p align="center">
  <a href="#quick-start"><img src="assets/icons/capy-rocket.svg" width="30" height="30" alt="" /> Quick start</a>
  &nbsp;·&nbsp;
  <a href="#capyyaml"><img src="assets/icons/capy-orange.svg" width="30" height="30" alt="" /> capy.yaml</a>
  &nbsp;·&nbsp;
  <a href="#connect-to-your-app"><img src="assets/icons/capy-plug.svg" width="30" height="30" alt="" /> Setup</a>
  &nbsp;·&nbsp;
  <a href="#faq"><img src="assets/icons/capy-question.svg" width="30" height="30" alt="" /> FAQ</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/capyriver"><img src="assets/icons/capy-heart.svg" width="30" height="30" alt="" /> CapyRiver</a>
  &nbsp;·&nbsp;
  <a href="https://t.me/CapyRiver"><img src="assets/icons/capy-plane.svg" width="30" height="30" alt="" /> Telegram</a>
</p>

<p align="center">
  <img src="assets/tagline.svg" width="720" alt="Give any LLM a real browser · One link to install · Works with small local models · One capy.yaml: model + browser + MCP" />
</p>

<p align="center">
  <img src="assets/icons/capy-globe.svg" width="30" height="30" alt="" align="absmiddle" /> <a href="#en"><b>English</b></a> &nbsp;·&nbsp; <a href="#zh"><b>简体中文</b></a> &nbsp;·&nbsp; <a href="#ru"><b>Русский</b></a> &nbsp;·&nbsp; <a href="#kk"><b>Қазақша</b></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/tested-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-C97752?style=flat-square" alt="Tested on Windows, macOS, Linux" />
  <img src="https://img.shields.io/badge/python-3.10%2B-C97752?style=flat-square" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/MCP-stdio-C97752?style=flat-square" alt="MCP stdio" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-C97752?style=flat-square" alt="MIT License" /></a>
</p>

---

<a id="en"></a>

## English

### <img src="assets/icons/capy-window.svg" width="40" height="40" alt="" align="absmiddle" /> What is it?

CapyBrowser is an [MCP](https://modelcontextprotocol.io) server. You plug it into an AI app (Claude Desktop, Claude Code, Cursor, VS Code, LM Studio, your own Ollama agent…) and the model gets a browser it can actually use: search the web, open pages, read them, click buttons, fill in forms, scroll and go back.

It was built for **small local models** first. Every page is compressed into a short, clean view — visible text plus a numbered list of clickable elements — capped at about 3,500 characters, so even a 4B model on a laptop can browse without drowning in HTML.

```text
URL: https://quotes.toscrape.com/login
Title: Quotes to Scrape
--- TEXT ---
Quotes to Scrape
Login
Username
Password
--- ELEMENTS (use the [id] with click/type_text) ---
[1] link "Quotes to Scrape"
[3] input "Username"
[4] input:password "password"
[5] button "Login"
```

The model answers with `type_text(3, "admin")`, `click(5)` — and a real browser does it.

### <img src="assets/icons/capy-chart.svg" width="40" height="40" alt="" align="absmiddle" /> Why CapyBrowser

| | CapyBrowser | Typical browser MCP servers |
|---|---|---|
| Install | one link, `uvx` downloads everything | Node + Playwright + ~300 MB of bundled browsers |
| Browser | the Chrome / Edge / Brave / Chromium you already have | a separate downloaded Chromium |
| Code | one Python file, three small dependencies (`mcp`, `websockets`, `pyyaml`) | large frameworks |
| Output for the model | compact text + `[id]` list, hard size limit | raw accessibility trees or screenshots, often 10–50k tokens |
| Small local models | designed and tested with a 4B model | usually need a large cloud model |

### <img src="assets/icons/capy-bolt.svg" width="40" height="40" alt="" align="absmiddle" /> Features

- **6 tools:** `web_search`, `open_url`, `click`, `type_text`, `scroll`, `go_back`.
- **Finds your browser automatically** on Windows, macOS and Linux (Chrome, Edge, Brave, Chromium, Vivaldi; stable, beta, dev, Canary, Snap, Flatpak).
- **Lazy start:** the browser launches on the first tool call, on a free port, with a fresh temporary profile. Your normal profile, passwords and cookies are never touched.
- **Works on modern sites:** real mouse and keyboard events through CDP, with verified fallbacks for React/Vue inputs, input masks (phones, cards, dates), overlays, custom checkboxes, shadow DOM, scroll containers and infinite feeds.
- **Handles the messy web:** `target=_blank` and `window.open` popups, `alert` / `confirm` / `beforeunload` dialogs, redirects, 404/500 pages, DNS errors, hanging servers and pages stuck in endless JavaScript loops.
- **Self-healing:** if the browser crashes or hangs, it is restarted and the last page is reopened.
- **Leaves nothing behind:** the browser process tree and its temporary profile are removed on exit, Ctrl+C and `SIGTERM`; leftovers from a hard kill are cleaned up on the next start.
- **Safe defaults:** DevTools listens on `127.0.0.1` only; `file://`, `javascript:` and `chrome://` URLs are refused (`file://` can be enabled explicitly).

<a id="quick-start"></a>

### <img src="assets/icons/capy-rocket.svg" width="40" height="40" alt="" align="absmiddle" /> Quick start

**1. Install `uv`** (a fast Python package manager; it also installs Python for you):

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Have Chrome, Edge, Brave or Chromium installed.** Windows already ships with Edge, so on Windows there is nothing to do.

**3. Add CapyBrowser to your AI app.** This is the whole server — a single link:

```json
{
  "mcpServers": {
    "capybrowser": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/capyriver/CapyBrowser", "capybrowser"]
    }
  }
}
```

Restart the app and ask: *"Search the web for the latest Python release and open the official page."*

<a id="capyyaml"></a>

### <img src="assets/icons/capy-orange.svg" width="40" height="40" alt="" align="absmiddle" /> capy.yaml — one file: model + browser + MCP

Don't have an MCP app? Like `soup.yaml` in Soup, CapyBrowser has **one config file**. Write `capy.yaml`, run `capybrowser agent` — and your model (local or cloud) can browse the web and use any other MCP servers. No Claude Desktop, no Cursor, no code.

```yaml
model:
  provider: ollama            # ollama | openai (LM Studio, llama.cpp, vLLM, OpenRouter, OpenAI…)
  name: qwen3:4b-instruct
  base_url: http://127.0.0.1:11434
browser:
  lang: en-US                 # headed: true — show the window
mcp_servers:                  # any MCP servers, Claude Desktop format
  time:
    command: uvx
    args: ["mcp-server-time"]
```

```bash
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser init     # creates capy.yaml
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser agent    # chat
capybrowser agent "Find the latest Python release and open its changelog"    # one task
capybrowser agent -c work.yaml -m qwen3:8b --headed                          # other file, model, visible browser
```

- **`model`** — `ollama` (default) or `openai` for any OpenAI-compatible API: LM Studio (`http://127.0.0.1:1234/v1`), llama.cpp server, vLLM, OpenRouter, OpenAI. `api_key: ${OPENAI_API_KEY}` reads the key from the environment — never write keys into the file.
- **`browser`** — the built-in CapyBrowser tools; same options as the flags below. `enabled: false` turns them off.
- **`mcp_servers`** — any stdio MCP servers in the same format as `claude_desktop_config.json` (`command`, `args`, `env`): files, databases, GitHub, time… Their tools are added next to the browser.
- **`agent`** — `max_steps`, `keep_full_results` (older tool results are shortened so small models keep a clean context) and an optional `system_prompt`.

The file is looked up as `./capy.yaml`, then `~/.capy.yaml`; `-c` picks any other. A full commented template: [`capy.yaml`](capy.yaml).

<a id="connect-to-your-app"></a>

### <img src="assets/icons/capy-plug.svg" width="40" height="40" alt="" align="absmiddle" /> Connect to your app

<details open>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, or open the file directly:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Paste the JSON from the quick start (merge it into an existing `mcpServers` block if there is one) and restart Claude Desktop.
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add capybrowser -- uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser
# available in every project:
claude mcp add -s user capybrowser -- uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser
```
</details>

<details>
<summary><b>Cursor</b></summary>

Put the quick-start JSON into `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project). Example: [`examples/cursor_mcp.json`](examples/cursor_mcp.json).
</details>

<details>
<summary><b>VS Code (GitHub Copilot agent mode)</b></summary>

Create `.vscode/mcp.json` — note the key is `servers`, not `mcpServers`:

```json
{
  "servers": {
    "capybrowser": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/capyriver/CapyBrowser", "capybrowser"]
    }
  }
}
```
</details>

<details>
<summary><b>LM Studio · Windsurf · Gemini CLI · other MCP clients</b></summary>

All of them use the same `mcpServers` JSON from the quick start:

- LM Studio: Program tab → Install → Edit `mcp.json`
- Windsurf: `~/.codeium/windsurf/mcp_config.json`
- Gemini CLI: `~/.gemini/settings.json`

Any client that can start a **stdio** MCP server works.
</details>

<details>
<summary><b>OpenAI Codex CLI</b></summary>

`~/.codex/config.toml`:

```toml
[mcp_servers.capybrowser]
command = "uvx"
args = ["--from", "git+https://github.com/capyriver/CapyBrowser", "capybrowser"]
```
</details>

<details>
<summary><b>Ollama and other local models</b></summary>

Ollama itself does not speak MCP, so the repository includes a ready-made agent, [`examples/ollama_agent.py`](examples/ollama_agent.py), that connects any Ollama model with tool calling to CapyBrowser:

```bash
ollama pull qwen3:4b-instruct
uv run https://raw.githubusercontent.com/capyriver/CapyBrowser/main/examples/ollama_agent.py "Open https://news.ycombinator.com and tell me the top story"
```

Set `OLLAMA_MODEL` to use another model (Qwen 3, Llama 3.1+, Mistral, Gemma 3 and other models with tool support).
</details>

### <img src="assets/icons/capy-box.svg" width="40" height="40" alt="" align="absmiddle" /> Other ways to install

```bash
# Run without installing (always the latest version from GitHub)
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser

# Install the `capybrowser` command permanently
uv tool install git+https://github.com/capyriver/CapyBrowser
pipx install git+https://github.com/capyriver/CapyBrowser
pip install git+https://github.com/capyriver/CapyBrowser

# It is a single file: run it straight from the URL or from a download
uv run https://raw.githubusercontent.com/capyriver/CapyBrowser/main/browser_mcp.py
uv run browser_mcp.py
```

After `uv tool install`, the MCP config can simply be `"command": "capybrowser"` with no arguments. Update with `uv tool upgrade capybrowser`.

### <img src="assets/icons/capy-wrench.svg" width="40" height="40" alt="" align="absmiddle" /> Tools

| Tool | Arguments | What it does |
|---|---|---|
| `web_search` | `query` | Searches the web (DuckDuckGo, Bing as fallback) and returns up to 10 results: `[id]` title, URL, snippet. |
| `open_url` | `url` | Opens a page and returns its compact view. `example.com` works without `https://`. |
| `click` | `element_id` | Clicks the element with that `[id]` (link, button, checkbox, tab, card…). |
| `type_text` | `element_id`, `text`, `submit=false` | Replaces the text of an input / textarea / editable area. For a `<select>`, picks the option by its text. `submit=true` presses Enter. |
| `scroll` | `direction` | `down`, `up`, `top` or `bottom`. Scrolls the page or the main scroll container. |
| `go_back` | — | Goes back in the browser history. |

Every tool returns the updated page, so the model always sees the result of its action. IDs are renumbered after every action, and errors come back as plain-text messages the model can recover from (for example *"element [12] not found — use an id from this list"* followed by the current page).

### <img src="assets/icons/capy-sliders.svg" width="40" height="40" alt="" align="absmiddle" /> Options

| Flag | Environment variable | Default | Meaning |
|---|---|---|---|
| `--headed` | — | off | Show the browser window (useful to watch or debug the agent). |
| `--browser PATH` | `BROWSER_MCP_PATH` | auto | Use a specific browser executable. |
| `--max-chars N` | `BROWSER_MCP_MAX_CHARS` | `3500` | Maximum size of every tool response. Raise it for large-context models. |
| `--lang ru-RU` | `BROWSER_MCP_LANG` | system locale | Browser UI language and `Accept-Language`. |
| `--allow-local-files` | `BROWSER_MCP_ALLOW_FILE=1` | off | Allow `open_url` with `file://` URLs. |
| — | `BROWSER_MCP_NAV_TIMEOUT` | `25` | Seconds to wait for a page to load. |
| `--log-level` | `BROWSER_MCP_LOG` | `WARNING` | Log level (logs go to stderr). |

Example with options: [`examples/advanced_config.json`](examples/advanced_config.json).

### <img src="assets/icons/capy-gear.svg" width="40" height="40" alt="" align="absmiddle" /> How it works

1. The MCP client starts `capybrowser` and talks to it over stdio.
2. On the first tool call, CapyBrowser finds your browser and starts it with `--remote-debugging-port` on a free local port and a temporary `--user-data-dir` (headless by default; `--no-sandbox` is added automatically for root/containers on Linux).
3. It connects to the page over the Chrome DevTools Protocol (a WebSocket) and drives it with `Page.*`, `Runtime.evaluate` and `Input.*` commands.
4. A small script inside the page marks the visible clickable elements with `data-mcp-id` and extracts the readable text from the current scroll position; the result is trimmed to the size limit.
5. On exit the whole browser process tree is killed and the temporary profile is deleted.

### <img src="assets/icons/capy-shield.svg" width="40" height="40" alt="" align="absmiddle" /> Security

- The model gets a **separate browser with an empty profile**: no access to your usual cookies, logins, passwords or extensions.
- DevTools is bound to `127.0.0.1`; nothing is exposed to the network.
- Web pages may contain text written to manipulate AI agents (prompt injection). Do not let an agent act on bank, email or work accounts unattended.
- More details: [SECURITY.md](SECURITY.md).

<a id="faq"></a>

### <img src="assets/icons/capy-question.svg" width="40" height="40" alt="" align="absmiddle" /> FAQ

<details>
<summary><b>Why do I need this? My chatbot can already search the web.</b></summary>

Built-in search returns snippets. CapyBrowser gives the model a real browser: it can open any page, read the full content, log in to a test account, fill and submit forms, click through a multi-step site, read pages rendered by JavaScript — and it works with **local models** that have no internet access at all.
</details>

<details>
<summary><b>Does it work with local models? Which one should I use?</b></summary>

Yes, that is the main goal. It is tested end-to-end with **Qwen 3 4B Instruct** in Ollama on CPU: web search, multi-page navigation, logging in, filling forms, scrolling long articles, going back, handling 404 and DNS errors. Any model with reliable tool calling works; bigger models (Qwen 3 8B/14B, Llama 3.1 8B+, Mistral Small) are more robust on complex tasks.
</details>

<details>
<summary><b>Do I need to install Python, Node.js or Playwright?</b></summary>

No Node.js and no Playwright. `uv` downloads a suitable Python and the two Python dependencies automatically on the first run. You only need `uv` and a Chromium-based browser.
</details>

<details>
<summary><b>Which browsers are supported? What about Firefox and Safari?</b></summary>

Google Chrome, Microsoft Edge, Brave, Chromium and Vivaldi (including beta/dev/Canary builds, Snap and Flatpak). Firefox and Safari are not supported: CapyBrowser speaks the Chrome DevTools Protocol. If you have several browsers, choose one with `--browser`.
</details>

<details>
<summary><b>Will it use or break my normal browser profile?</b></summary>

No. Every session uses a fresh temporary profile in the system temp folder, deleted on exit. Your running browser is not affected — both can run at the same time.
</details>

<details>
<summary><b>Can I watch what the AI is doing?</b></summary>

Yes: add `--headed` to the `args` and a normal browser window opens. On Linux this requires a desktop session (`DISPLAY`/`WAYLAND_DISPLAY`); without one CapyBrowser falls back to headless mode.
</details>

<details>
<summary><b>Can the model log in to sites?</b></summary>

It can type into login forms like a person would, which is handy for test accounts. Sessions do not persist between runs (the profile is temporary). Think twice before giving an AI real credentials.
</details>

<details>
<summary><b>Does it solve CAPTCHAs or bypass bot protection?</b></summary>

No. CapyBrowser removes the obvious `HeadlessChrome` marker from the user agent, but it is not a stealth or anti-bot tool. If a search engine shows a CAPTCHA, `web_search` falls back to another engine and otherwise reports the problem.
</details>

<details>
<summary><b>Does it send any data anywhere?</b></summary>

Only to the websites the model opens, and to DuckDuckGo/Bing for `web_search`. There is no telemetry, no account and no API key.
</details>

<details>
<summary><b>Can it take screenshots, download files or work with several tabs?</b></summary>

Not in this version, on purpose: the tool set is kept small so that small models use it reliably. Links that open a new tab are opened in the same tab, downloads are blocked, and JavaScript dialogs are accepted automatically.
</details>

<details>
<summary><b>Pages are cut off. Can the model see more?</b></summary>

The model can call `scroll` to read further; every page view says how much of the page has been seen. With a large-context model you can raise the limit with `--max-chars 8000`.
</details>

<details>
<summary><b>How do I update?</b></summary>

With `uvx --from git+…` you get the latest version on every start (uv caches it; `uv cache clean` forces a refresh). With `uv tool install`, run `uv tool upgrade capybrowser`.
</details>

### <img src="assets/icons/capy-bug.svg" width="40" height="40" alt="" align="absmiddle" /> Troubleshooting

| Problem | Fix |
|---|---|
| `No Chrome/Edge/Brave/Chromium found` | Install one of them, or pass the path: `--browser "C:\Program Files\Google\Chrome\Application\chrome.exe"`. |
| The app says the MCP server failed to start / `uvx` not found | GUI apps do not always see your new `PATH`. Restart the computer, or use the full path to `uvx`: Windows `C:\Users\<you>\.local\bin\uvx.exe`, macOS/Linux `~/.local/bin/uvx`. |
| Browser fails to start in Docker / as root on Linux | Handled automatically (`--no-sandbox`, `--disable-dev-shm-usage`). If it still fails, install the browser's system dependencies (e.g. `apt-get install chromium`). |
| Corporate proxy | The browser uses the system proxy settings; on Linux it also reads `HTTPS_PROXY`. |
| Pages load slowly | Raise `BROWSER_MCP_NAV_TIMEOUT` (seconds). |
| You want to see logs | Add `--log-level INFO`; logs go to stderr (in Claude Desktop: Settings → Developer → Open Logs Folder). |

### <img src="assets/icons/capy-terminal.svg" width="40" height="40" alt="" align="absmiddle" /> Development

```bash
git clone https://github.com/capyriver/CapyBrowser
cd CapyBrowser
uv run --extra dev pytest -q      # unit + real-browser + MCP protocol tests
uv run --extra dev ruff check .
uv run capybrowser --headed       # run from source
```

Project layout:

```text
browser_mcp.py          the whole server (also runnable as a single PEP 723 script)
capy.yaml               example config for `capybrowser agent` (model + browser + MCP servers)
pyproject.toml          package metadata and the `capybrowser` command
examples/               MCP configs for popular apps and an Ollama agent
tests/                  pytest suite (runs on Windows, macOS and Linux in CI)
assets/                 banner, tagline, footer, pixel-art capybara icons, social preview image
```

Before the first release the server went through more than 40,000 automated checks: fuzzing on thousands of generated and broken HTML pages, connect/disconnect and crash/kill cycles with leak and zombie-process checks, invalid arguments over the real MCP protocol, and end-to-end runs where a 4B local model completed real tasks on live websites.

### <img src="assets/icons/capy-doc.svg" width="40" height="40" alt="" align="absmiddle" /> License

MIT — see [LICENSE](LICENSE). Not affiliated with Google, Microsoft, Brave or Anthropic.

---

<a id="zh"></a>

## 简体中文

### <img src="assets/icons/capy-window.svg" width="40" height="40" alt="" align="absmiddle" /> 这是什么？

CapyBrowser 是一个 [MCP](https://modelcontextprotocol.io) 服务器。把它接入 AI 应用（Claude Desktop、Claude Code、Cursor、VS Code、LM Studio、你自己的 Ollama 智能体……），模型就能真正使用浏览器：搜索网页、打开页面、阅读内容、点击按钮、填写表单、滚动和后退。

它首先是为**小型本地模型**设计的。每个页面都会被压缩成简短清晰的视图——可见文本加上带编号的可点击元素列表——长度约 3500 个字符以内，因此即使是笔记本上的 4B 模型也能浏览网页而不会被 HTML 淹没。

```text
URL: https://quotes.toscrape.com/login
--- TEXT ---
Login
Username
Password
--- ELEMENTS (use the [id] with click/type_text) ---
[3] input "Username"
[4] input:password "password"
[5] button "Login"
```

模型只需回复 `type_text(3, "admin")`、`click(5)`，真实的浏览器就会执行。

### <img src="assets/icons/capy-chart.svg" width="40" height="40" alt="" align="absmiddle" /> 为什么选择 CapyBrowser

| | CapyBrowser | 常见的浏览器 MCP 服务器 |
|---|---|---|
| 安装 | 一个链接，`uvx` 自动下载一切 | Node + Playwright + 约 300 MB 的捆绑浏览器 |
| 浏览器 | 使用你已安装的 Chrome / Edge / Brave / Chromium | 单独下载的 Chromium |
| 代码 | 一个 Python 文件，三个轻量依赖（`mcp`、`websockets`、`pyyaml`） | 大型框架 |
| 给模型的输出 | 精简文本 + `[id]` 列表，严格长度限制 | 原始无障碍树或截图，常常 1–5 万 token |
| 小型本地模型 | 专门设计并用 4B 模型测试 | 通常需要大型云端模型 |

### <img src="assets/icons/capy-bolt.svg" width="40" height="40" alt="" align="absmiddle" /> 功能

- **6 个工具：** `web_search`、`open_url`、`click`、`type_text`、`scroll`、`go_back`。
- **自动查找浏览器**：支持 Windows、macOS、Linux（Chrome、Edge、Brave、Chromium、Vivaldi；稳定版、Beta、Dev、Canary、Snap、Flatpak）。
- **按需启动**：首次调用工具时才启动浏览器，使用空闲端口和全新的临时配置文件，绝不触碰你平时的配置、密码和 Cookie。
- **适配现代网站**：通过 CDP 发送真实的鼠标和键盘事件，并为 React/Vue 输入框、输入掩码（电话、银行卡、日期）、遮罩层、自定义复选框、Shadow DOM、滚动容器和无限加载列表提供经过验证的后备方案。
- **应对复杂网页**：`target=_blank` 和 `window.open` 弹窗、`alert` / `confirm` / `beforeunload` 对话框、重定向、404/500 页面、DNS 错误、无响应的服务器以及卡死在无限 JavaScript 循环中的页面。
- **自动恢复**：浏览器崩溃或卡死时自动重启并重新打开上一个页面。
- **不留痕迹**：退出、Ctrl+C 和 `SIGTERM` 时会结束整个浏览器进程树并删除临时配置；被强制结束后残留的文件会在下次启动时清理。
- **安全默认值**：DevTools 只监听 `127.0.0.1`；拒绝 `file://`、`javascript:`、`chrome://` 链接（`file://` 可以显式开启）。

### <img src="assets/icons/capy-rocket.svg" width="40" height="40" alt="" align="absmiddle" /> 快速开始

**1. 安装 `uv`**（快速的 Python 包管理器，也会自动为你安装 Python）：

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. 安装 Chrome、Edge、Brave 或 Chromium 之一。** Windows 自带 Edge，无需额外操作。

**3. 把 CapyBrowser 添加到 AI 应用。** 整个服务器就是一个链接：

```json
{
  "mcpServers": {
    "capybrowser": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/capyriver/CapyBrowser", "capybrowser"]
    }
  }
}
```

重启应用后试着问：*“搜索 Python 的最新版本并打开官方页面。”*

### <img src="assets/icons/capy-orange.svg" width="40" height="40" alt="" align="absmiddle" /> capy.yaml —— 一个文件：模型 + 浏览器 + MCP

没有 MCP 应用？就像 Soup 的 `soup.yaml`，CapyBrowser 也只需要**一个配置文件**。写好 `capy.yaml`，运行 `capybrowser agent`，你的模型（本地或云端）就能浏览网页并使用任何其他 MCP 服务器，无需 Claude Desktop、Cursor 或编写代码。

```yaml
model:
  provider: ollama            # ollama | openai (LM Studio, llama.cpp, vLLM, OpenRouter, OpenAI…)
  name: qwen3:4b-instruct
  base_url: http://127.0.0.1:11434
browser:
  lang: en-US                 # headed: true — show the window
mcp_servers:                  # any MCP servers, Claude Desktop format
  time:
    command: uvx
    args: ["mcp-server-time"]
```

```bash
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser init     # 创建 capy.yaml
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser agent    # 对话
capybrowser agent "找到 Python 的最新版本并打开更新日志"                         # 单个任务
capybrowser agent -c work.yaml -m qwen3:8b --headed                          # 其他配置、模型、显示浏览器窗口
```

- **`model`**：`ollama`（默认）或 `openai`（任何 OpenAI 兼容 API：LM Studio `http://127.0.0.1:1234/v1`、llama.cpp server、vLLM、OpenRouter、OpenAI）。`api_key: ${OPENAI_API_KEY}` 从环境变量读取密钥，请勿把密钥写进文件。
- **`browser`**：内置的 CapyBrowser 工具，选项与下方命令行参数相同；`enabled: false` 可关闭。
- **`mcp_servers`**：任何 stdio MCP 服务器，格式与 `claude_desktop_config.json` 相同（`command`、`args`、`env`）。它们的工具会和浏览器工具一起提供给模型。
- **`agent`**：`max_steps`、`keep_full_results`（较早的工具结果会被缩短，让小模型的上下文保持干净）以及可选的 `system_prompt`。

查找顺序：`./capy.yaml`，然后 `~/.capy.yaml`；用 `-c` 指定其他文件。完整带注释的模板：[`capy.yaml`](capy.yaml)。

### <img src="assets/icons/capy-plug.svg" width="40" height="40" alt="" align="absmiddle" /> 接入应用

- **Claude Desktop：** 设置 → 开发者 → 编辑配置，或直接打开 `%APPDATA%\Claude\claude_desktop_config.json`（Windows）/ `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS），粘贴上面的 JSON 并重启。
- **Claude Code：** `claude mcp add capybrowser -- uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser`（加 `-s user` 可用于所有项目）。
- **Cursor：** `~/.cursor/mcp.json` 或 `.cursor/mcp.json`，示例见 [`examples/cursor_mcp.json`](examples/cursor_mcp.json)。
- **VS Code（Copilot 智能体模式）：** `.vscode/mcp.json`，注意键名是 `servers`，示例见 [`examples/vscode_mcp.json`](examples/vscode_mcp.json)。
- **LM Studio / Windsurf / Gemini CLI：** 使用相同的 `mcpServers` JSON（`mcp.json`、`~/.codeium/windsurf/mcp_config.json`、`~/.gemini/settings.json`）。
- **OpenAI Codex CLI：** `~/.codex/config.toml`，示例见 [`examples/codex_config.toml`](examples/codex_config.toml)。
- **Ollama 与其他本地模型：** Ollama 本身不支持 MCP，因此仓库提供了现成的智能体 [`examples/ollama_agent.py`](examples/ollama_agent.py)：

```bash
ollama pull qwen3:4b-instruct
uv run https://raw.githubusercontent.com/capyriver/CapyBrowser/main/examples/ollama_agent.py "打开 https://news.ycombinator.com 告诉我排名第一的新闻"
```

### <img src="assets/icons/capy-box.svg" width="40" height="40" alt="" align="absmiddle" /> 其他安装方式

```bash
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser     # 无需安装，始终是最新版本
uv tool install git+https://github.com/capyriver/CapyBrowser            # 永久安装 capybrowser 命令
pipx install git+https://github.com/capyriver/CapyBrowser
uv run https://raw.githubusercontent.com/capyriver/CapyBrowser/main/browser_mcp.py   # 单文件直接运行
```

使用 `uv tool install` 后，MCP 配置可以简化为 `"command": "capybrowser"`。升级：`uv tool upgrade capybrowser`。

### <img src="assets/icons/capy-wrench.svg" width="40" height="40" alt="" align="absmiddle" /> 工具

| 工具 | 参数 | 作用 |
|---|---|---|
| `web_search` | `query` | 搜索网页（DuckDuckGo，备用 Bing），返回最多 10 条结果：`[id]` 标题、URL、摘要。 |
| `open_url` | `url` | 打开页面并返回精简视图。`example.com` 可省略 `https://`。 |
| `click` | `element_id` | 点击对应 `[id]` 的元素（链接、按钮、复选框、标签页、卡片……）。 |
| `type_text` | `element_id`、`text`、`submit=false` | 替换输入框/文本区域/可编辑区域的内容；对 `<select>` 按文本选择选项；`submit=true` 会按回车。 |
| `scroll` | `direction` | `down`、`up`、`top`、`bottom`。滚动页面或主滚动容器。 |
| `go_back` | — | 浏览器后退。 |

每个工具都会返回更新后的页面。每次操作后 ID 会重新编号；错误以纯文本返回，模型可以据此继续。

### <img src="assets/icons/capy-sliders.svg" width="40" height="40" alt="" align="absmiddle" /> 选项

| 参数 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `--headed` | — | 关 | 显示浏览器窗口（便于观察或调试）。 |
| `--browser PATH` | `BROWSER_MCP_PATH` | 自动 | 指定浏览器可执行文件。 |
| `--max-chars N` | `BROWSER_MCP_MAX_CHARS` | `3500` | 每个工具响应的最大长度。 |
| `--lang zh-CN` | `BROWSER_MCP_LANG` | 系统区域设置 | 浏览器界面语言和 `Accept-Language`。 |
| `--allow-local-files` | `BROWSER_MCP_ALLOW_FILE=1` | 关 | 允许打开 `file://` 链接。 |
| — | `BROWSER_MCP_NAV_TIMEOUT` | `25` | 页面加载等待秒数。 |
| `--log-level` | `BROWSER_MCP_LOG` | `WARNING` | 日志级别（输出到 stderr）。 |

### <img src="assets/icons/capy-question.svg" width="40" height="40" alt="" align="absmiddle" /> 常见问题

<details>
<summary><b>聊天机器人已经能联网搜索了，为什么还需要它？</b></summary>

内置搜索只返回摘要。CapyBrowser 提供的是真正的浏览器：可以打开任意页面、阅读完整内容、登录测试账号、填写并提交表单、在多步骤网站中点击、阅读由 JavaScript 渲染的页面——而且适用于完全没有联网能力的**本地模型**。
</details>

<details>
<summary><b>支持本地模型吗？用哪个？</b></summary>

支持，这正是主要目标。已在 Ollama（CPU）上用 **Qwen 3 4B Instruct** 进行端到端测试：搜索、多页面导航、登录、填写表单、滚动长文、后退、处理 404 和 DNS 错误。任何工具调用可靠的模型都可以；更大的模型在复杂任务上更稳定。
</details>

<details>
<summary><b>需要安装 Python、Node.js 或 Playwright 吗？</b></summary>

不需要 Node.js 和 Playwright。`uv` 会在首次运行时自动下载合适的 Python 和两个依赖。你只需要 `uv` 和一个基于 Chromium 的浏览器。
</details>

<details>
<summary><b>支持哪些浏览器？Firefox 和 Safari 呢？</b></summary>

Chrome、Edge、Brave、Chromium、Vivaldi（含 Beta/Dev/Canary、Snap、Flatpak）。不支持 Firefox 和 Safari，因为 CapyBrowser 使用 Chrome DevTools Protocol。安装了多个浏览器时可以用 `--browser` 指定。
</details>

<details>
<summary><b>会影响我平时使用的浏览器配置吗？</b></summary>

不会。每次会话都使用系统临时目录中的全新配置，退出时删除。你正在使用的浏览器不受影响，两者可以同时运行。
</details>

<details>
<summary><b>能看到 AI 在做什么吗？</b></summary>

可以：在 `args` 中加入 `--headed` 就会打开普通浏览器窗口。Linux 需要图形桌面（`DISPLAY`/`WAYLAND_DISPLAY`），否则自动回退到无头模式。
</details>

<details>
<summary><b>能解决验证码或绕过反爬吗？</b></summary>

不能。它不是隐身或反爬工具。如果搜索引擎出现验证码，`web_search` 会切换到另一个搜索引擎，否则会报告问题。
</details>

<details>
<summary><b>会把数据发送到其他地方吗？</b></summary>

只会访问模型打开的网站，以及 `web_search` 使用的 DuckDuckGo/Bing。没有遥测、不需要账号或 API 密钥。
</details>

<details>
<summary><b>支持截图、下载文件或多标签页吗？</b></summary>

当前版本有意不支持：工具集保持精简，以便小模型稳定使用。新标签页链接会在当前标签页打开，下载会被阻止，JavaScript 对话框会自动确认。
</details>

<details>
<summary><b>页面被截断了怎么办？</b></summary>

模型可以调用 `scroll` 继续阅读；每个页面视图都会显示已查看的比例。对于大上下文模型，可以用 `--max-chars 8000` 提高上限。
</details>

### <img src="assets/icons/capy-bug.svg" width="40" height="40" alt="" align="absmiddle" /> 故障排除

| 问题 | 解决方法 |
|---|---|
| `No Chrome/Edge/Brave/Chromium found` | 安装其中之一，或用 `--browser` 指定路径。 |
| 应用提示 MCP 服务器启动失败 / 找不到 `uvx` | 图形应用不一定能看到新的 `PATH`。重启电脑，或使用 `uvx` 的完整路径：Windows `C:\Users\<用户名>\.local\bin\uvx.exe`，macOS/Linux `~/.local/bin/uvx`。 |
| Linux 下在 Docker 或 root 中无法启动浏览器 | 已自动处理（`--no-sandbox`、`--disable-dev-shm-usage`）；如仍失败，请安装浏览器的系统依赖。 |
| 页面加载慢 | 增大 `BROWSER_MCP_NAV_TIMEOUT`。 |
| 查看日志 | 添加 `--log-level INFO`，日志输出到 stderr。 |

### <img src="assets/icons/capy-shield.svg" width="40" height="40" alt="" align="absmiddle" /> 安全

模型使用的是**独立的、空配置的浏览器**，无法访问你的 Cookie、登录状态、密码和扩展。DevTools 只绑定 `127.0.0.1`。网页可能包含诱导 AI 的文字（提示注入），请勿让智能体在无人看管的情况下操作银行、邮箱或工作账号。详见 [SECURITY.md](SECURITY.md)。

### <img src="assets/icons/capy-doc.svg" width="40" height="40" alt="" align="absmiddle" /> 许可证

MIT，见 [LICENSE](LICENSE)。本项目与 Google、Microsoft、Brave、Anthropic 无关联。

---

<a id="ru"></a>

## Русский

### <img src="assets/icons/capy-window.svg" width="40" height="40" alt="" align="absmiddle" /> Что это?

CapyBrowser — это [MCP](https://modelcontextprotocol.io)-сервер. Подключаете его к AI-приложению (Claude Desktop, Claude Code, Cursor, VS Code, LM Studio, свой агент на Ollama…) — и модель получает настоящий браузер: ищет в интернете, открывает страницы, читает их, нажимает кнопки, заполняет формы, листает и возвращается назад.

В первую очередь он сделан для **маленьких локальных моделей**. Каждая страница сжимается в короткое чистое представление — видимый текст плюс пронумерованный список кликабельных элементов — не длиннее ~3500 символов. Поэтому даже 4B-модель на ноутбуке может пользоваться интернетом и не тонуть в HTML.

```text
URL: https://quotes.toscrape.com/login
--- TEXT ---
Login
Username
Password
--- ELEMENTS (use the [id] with click/type_text) ---
[3] input "Username"
[4] input:password "password"
[5] button "Login"
```

Модель отвечает `type_text(3, "admin")`, `click(5)` — и настоящий браузер это делает.

### <img src="assets/icons/capy-chart.svg" width="40" height="40" alt="" align="absmiddle" /> Почему CapyBrowser

| | CapyBrowser | Обычные браузерные MCP-серверы |
|---|---|---|
| Установка | одна ссылка, `uvx` скачивает всё сам | Node + Playwright + ~300 МБ своих браузеров |
| Браузер | ваш уже установленный Chrome / Edge / Brave / Chromium | отдельно скачанный Chromium |
| Код | один Python-файл, три небольшие зависимости (`mcp`, `websockets`, `pyyaml`) | большие фреймворки |
| Что видит модель | компактный текст + список `[id]`, жёсткий лимит размера | сырое дерево доступности или скриншоты, часто 10–50 тыс. токенов |
| Маленькие локальные модели | сделан и протестирован на 4B-модели | обычно нужна большая облачная модель |

### <img src="assets/icons/capy-bolt.svg" width="40" height="40" alt="" align="absmiddle" /> Возможности

- **6 инструментов:** `web_search`, `open_url`, `click`, `type_text`, `scroll`, `go_back`.
- **Сам находит браузер** на Windows, macOS и Linux (Chrome, Edge, Brave, Chromium, Vivaldi; stable, beta, dev, Canary, Snap, Flatpak).
- **Ленивый запуск:** браузер стартует при первом вызове инструмента, на свободном порту, с новым временным профилем. Ваш обычный профиль, пароли и cookies не затрагиваются.
- **Работает на современных сайтах:** настоящие события мыши и клавиатуры через CDP с проверенными запасными вариантами для полей React/Vue, масок ввода (телефоны, карты, даты), перекрывающих слоёв, кастомных чекбоксов, shadow DOM, контейнеров со своим скроллом и бесконечных лент.
- **Справляется с «грязным» вебом:** всплывающие окна `target=_blank` и `window.open`, диалоги `alert` / `confirm` / `beforeunload`, редиректы, страницы 404/500, ошибки DNS, зависшие серверы и страницы с бесконечным циклом JavaScript.
- **Самовосстановление:** если браузер упал или завис, он перезапускается и открывает последнюю страницу.
- **Не оставляет мусора:** при выходе, Ctrl+C и `SIGTERM` завершается всё дерево процессов браузера и удаляется временный профиль; остатки после жёсткого убийства чистятся при следующем запуске.
- **Безопасные настройки по умолчанию:** DevTools слушает только `127.0.0.1`; ссылки `file://`, `javascript:` и `chrome://` запрещены (`file://` можно включить явно).

### <img src="assets/icons/capy-rocket.svg" width="40" height="40" alt="" align="absmiddle" /> Быстрый старт

**1. Установите `uv`** (быстрый менеджер пакетов Python; он сам поставит и Python):

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Нужен Chrome, Edge, Brave или Chromium.** В Windows уже есть Edge, так что ничего ставить не нужно.

**3. Добавьте CapyBrowser в AI-приложение.** Весь сервер — это одна ссылка:

```json
{
  "mcpServers": {
    "capybrowser": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/capyriver/CapyBrowser", "capybrowser"]
    }
  }
}
```

Перезапустите приложение и попросите: *«Найди в интернете последнюю версию Python и открой официальную страницу».*

### <img src="assets/icons/capy-orange.svg" width="40" height="40" alt="" align="absmiddle" /> capy.yaml — один файл: модель + браузер + MCP

Нет MCP-приложения? Как `soup.yaml` у Soup, у CapyBrowser есть **один файл настроек**. Пишете `capy.yaml`, запускаете `capybrowser agent` — и ваша модель (локальная или облачная) умеет ходить в интернет через браузер и пользоваться любыми другими MCP-серверами. Без Claude Desktop, без Cursor, без кода.

```yaml
model:
  provider: ollama            # ollama | openai (LM Studio, llama.cpp, vLLM, OpenRouter, OpenAI…)
  name: qwen3:4b-instruct
  base_url: http://127.0.0.1:11434
browser:
  lang: en-US                 # headed: true — show the window
mcp_servers:                  # any MCP servers, Claude Desktop format
  time:
    command: uvx
    args: ["mcp-server-time"]
```

```bash
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser init     # создаст capy.yaml
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser agent    # чат
capybrowser agent "Найди последнюю версию Python и открой список изменений"  # одна задача
capybrowser agent -c work.yaml -m qwen3:8b --headed                          # другой файл, модель, видимый браузер
```

- **`model`** — `ollama` (по умолчанию) или `openai` для любого OpenAI-совместимого API: LM Studio (`http://127.0.0.1:1234/v1`), llama.cpp server, vLLM, OpenRouter, OpenAI. `api_key: ${OPENAI_API_KEY}` берёт ключ из переменной окружения — не пишите ключи прямо в файл.
- **`browser`** — встроенные инструменты CapyBrowser, те же параметры, что флаги ниже. `enabled: false` — выключить.
- **`mcp_servers`** — любые MCP-серверы (stdio) в том же формате, что `claude_desktop_config.json` (`command`, `args`, `env`): файлы, базы данных, GitHub, время… Их инструменты добавляются рядом с браузером.
- **`agent`** — `max_steps`, `keep_full_results` (старые результаты инструментов сокращаются, чтобы у маленьких моделей контекст оставался чистым) и необязательный `system_prompt`.

Файл ищется так: `./capy.yaml`, затем `~/.capy.yaml`; через `-c` можно указать любой другой. Полный шаблон с комментариями: [`capy.yaml`](capy.yaml).

### <img src="assets/icons/capy-plug.svg" width="40" height="40" alt="" align="absmiddle" /> Подключение к приложениям

<details open>
<summary><b>Claude Desktop</b></summary>

Настройки → Разработчик → Изменить конфигурацию, или откройте файл напрямую:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Вставьте JSON из быстрого старта (если блок `mcpServers` уже есть — добавьте в него) и перезапустите Claude Desktop.
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add capybrowser -- uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser
# для всех проектов сразу:
claude mcp add -s user capybrowser -- uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser
```
</details>

<details>
<summary><b>Cursor</b></summary>

JSON из быстрого старта — в `~/.cursor/mcp.json` (для всех проектов) или `.cursor/mcp.json` (для одного). Пример: [`examples/cursor_mcp.json`](examples/cursor_mcp.json).
</details>

<details>
<summary><b>VS Code (режим агента GitHub Copilot)</b></summary>

Создайте `.vscode/mcp.json` — здесь ключ `servers`, а не `mcpServers`:

```json
{
  "servers": {
    "capybrowser": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/capyriver/CapyBrowser", "capybrowser"]
    }
  }
}
```
</details>

<details>
<summary><b>LM Studio · Windsurf · Gemini CLI · другие MCP-клиенты</b></summary>

Везде тот же JSON `mcpServers` из быстрого старта:

- LM Studio: вкладка Program → Install → Edit `mcp.json`
- Windsurf: `~/.codeium/windsurf/mcp_config.json`
- Gemini CLI: `~/.gemini/settings.json`

Подойдёт любой клиент, который умеет запускать MCP-сервер через **stdio**.
</details>

<details>
<summary><b>OpenAI Codex CLI</b></summary>

`~/.codex/config.toml`:

```toml
[mcp_servers.capybrowser]
command = "uvx"
args = ["--from", "git+https://github.com/capyriver/CapyBrowser", "capybrowser"]
```
</details>

<details>
<summary><b>Ollama и другие локальные модели</b></summary>

Сам Ollama не умеет MCP, поэтому в репозитории есть готовый агент [`examples/ollama_agent.py`](examples/ollama_agent.py), который соединяет любую модель Ollama с поддержкой инструментов и CapyBrowser:

```bash
ollama pull qwen3:4b-instruct
uv run https://raw.githubusercontent.com/capyriver/CapyBrowser/main/examples/ollama_agent.py "Открой https://news.ycombinator.com и скажи, какая новость первая"
```

Другую модель задайте через `OLLAMA_MODEL` (Qwen 3, Llama 3.1+, Mistral, Gemma 3 и другие модели с поддержкой инструментов).
</details>

### <img src="assets/icons/capy-box.svg" width="40" height="40" alt="" align="absmiddle" /> Другие способы установки

```bash
# Запуск без установки (всегда свежая версия с GitHub)
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser

# Поставить команду `capybrowser` насовсем
uv tool install git+https://github.com/capyriver/CapyBrowser
pipx install git+https://github.com/capyriver/CapyBrowser
pip install git+https://github.com/capyriver/CapyBrowser

# Это один файл: можно запустить прямо по ссылке или из скачанного файла
uv run https://raw.githubusercontent.com/capyriver/CapyBrowser/main/browser_mcp.py
uv run browser_mcp.py
```

После `uv tool install` в конфиге MCP достаточно `"command": "capybrowser"` без аргументов. Обновление — `uv tool upgrade capybrowser`.

### <img src="assets/icons/capy-wrench.svg" width="40" height="40" alt="" align="absmiddle" /> Инструменты

| Инструмент | Аргументы | Что делает |
|---|---|---|
| `web_search` | `query` | Ищет в интернете (DuckDuckGo, запасной — Bing) и возвращает до 10 результатов: `[id]`, заголовок, URL, фрагмент. |
| `open_url` | `url` | Открывает страницу и возвращает её компактное представление. `example.com` можно без `https://`. |
| `click` | `element_id` | Нажимает элемент с этим `[id]` (ссылка, кнопка, чекбокс, вкладка, карточка…). |
| `type_text` | `element_id`, `text`, `submit=false` | Заменяет текст в поле / textarea / редактируемой области. Для `<select>` выбирает вариант по тексту. `submit=true` нажимает Enter. |
| `scroll` | `direction` | `down`, `up`, `top` или `bottom`. Листает страницу или её основной прокручиваемый контейнер. |
| `go_back` | — | Возвращается назад по истории. |

Каждый инструмент возвращает обновлённую страницу, так что модель сразу видит результат своего действия. Номера `[id]` пересчитываются после каждого действия, а ошибки приходят понятным текстом, с которым модель может продолжить (например, *«элемент [12] не найден — используйте id из этого списка»* и текущая страница).

### <img src="assets/icons/capy-sliders.svg" width="40" height="40" alt="" align="absmiddle" /> Параметры

| Флаг | Переменная окружения | По умолчанию | Что делает |
|---|---|---|---|
| `--headed` | — | выкл. | Показывать окно браузера (чтобы смотреть за агентом или отлаживать). |
| `--browser PATH` | `BROWSER_MCP_PATH` | авто | Конкретный исполняемый файл браузера. |
| `--max-chars N` | `BROWSER_MCP_MAX_CHARS` | `3500` | Максимальный размер ответа любого инструмента. Для моделей с большим контекстом можно увеличить. |
| `--lang ru-RU` | `BROWSER_MCP_LANG` | язык системы | Язык интерфейса браузера и `Accept-Language`. |
| `--allow-local-files` | `BROWSER_MCP_ALLOW_FILE=1` | выкл. | Разрешить `open_url` с адресами `file://`. |
| — | `BROWSER_MCP_NAV_TIMEOUT` | `25` | Сколько секунд ждать загрузки страницы. |
| `--log-level` | `BROWSER_MCP_LOG` | `WARNING` | Уровень логов (пишутся в stderr). |

Пример с параметрами: [`examples/advanced_config.json`](examples/advanced_config.json).

### <img src="assets/icons/capy-gear.svg" width="40" height="40" alt="" align="absmiddle" /> Как это работает

1. MCP-клиент запускает `capybrowser` и общается с ним через stdio.
2. При первом вызове инструмента CapyBrowser находит браузер и запускает его с `--remote-debugging-port` на свободном локальном порту и временным `--user-data-dir` (по умолчанию без окна; на Linux под root/в контейнере автоматически добавляется `--no-sandbox`).
3. Подключается к странице по Chrome DevTools Protocol (WebSocket) и управляет ею командами `Page.*`, `Runtime.evaluate` и `Input.*`.
4. Небольшой скрипт внутри страницы помечает видимые кликабельные элементы атрибутом `data-mcp-id` и собирает читаемый текст с текущего места прокрутки; результат обрезается до лимита.
5. При выходе завершается всё дерево процессов браузера и удаляется временный профиль.

### <img src="assets/icons/capy-shield.svg" width="40" height="40" alt="" align="absmiddle" /> Безопасность

- Модель получает **отдельный браузер с пустым профилем**: без ваших обычных cookies, входов в аккаунты, паролей и расширений.
- DevTools привязан к `127.0.0.1`, в сеть ничего не открывается.
- На страницах может быть текст, написанный специально, чтобы манипулировать AI-агентами (prompt injection). Не оставляйте агента без присмотра в банке, почте или рабочих аккаунтах.
- Подробнее: [SECURITY.md](SECURITY.md).

### <img src="assets/icons/capy-question.svg" width="40" height="40" alt="" align="absmiddle" /> Вопросы и ответы

<details>
<summary><b>Зачем это нужно, если чат-бот и так умеет искать в интернете?</b></summary>

Встроенный поиск отдаёт только сниппеты. CapyBrowser даёт модели настоящий браузер: открыть любую страницу, прочитать её целиком, войти в тестовый аккаунт, заполнить и отправить форму, пройти многошаговый сайт, прочитать страницы, которые рисует JavaScript. И всё это работает с **локальными моделями**, у которых интернета нет вообще.
</details>

<details>
<summary><b>Работает с локальными моделями? Какую выбрать?</b></summary>

Да, это главная цель. Сервер проверен от начала до конца с **Qwen 3 4B Instruct** в Ollama на процессоре: поиск, переходы по страницам, вход в аккаунт, заполнение форм, листание длинных статей, возврат назад, обработка 404 и ошибок DNS. Подойдёт любая модель с надёжным вызовом инструментов; модели крупнее (Qwen 3 8B/14B, Llama 3.1 8B+, Mistral Small) стабильнее на сложных задачах.
</details>

<details>
<summary><b>Нужно ставить Python, Node.js или Playwright?</b></summary>

Node.js и Playwright не нужны. `uv` при первом запуске сам скачает подходящий Python и две Python-зависимости. Нужны только `uv` и браузер на основе Chromium.
</details>

<details>
<summary><b>Какие браузеры поддерживаются? А Firefox и Safari?</b></summary>

Google Chrome, Microsoft Edge, Brave, Chromium и Vivaldi (включая beta/dev/Canary, Snap и Flatpak). Firefox и Safari не поддерживаются: CapyBrowser работает через Chrome DevTools Protocol. Если браузеров несколько, нужный можно выбрать через `--browser`.
</details>

<details>
<summary><b>Он не испортит мой обычный профиль браузера?</b></summary>

Нет. Каждая сессия использует новый временный профиль во временной папке системы, который удаляется при выходе. Ваш открытый браузер не затрагивается — они могут работать одновременно.
</details>

<details>
<summary><b>Можно посмотреть, что делает ИИ?</b></summary>

Да: добавьте `--headed` в `args`, и откроется обычное окно браузера. На Linux для этого нужен рабочий стол (`DISPLAY`/`WAYLAND_DISPLAY`); без него CapyBrowser автоматически работает без окна.
</details>

<details>
<summary><b>Может ли модель входить в аккаунты?</b></summary>

Она может вводить логин и пароль в формы, как человек, — удобно для тестовых аккаунтов. Между запусками сессии не сохраняются (профиль временный). Хорошо подумайте, прежде чем давать ИИ настоящие пароли.
</details>

<details>
<summary><b>Он проходит капчи или обходит защиту от ботов?</b></summary>

Нет. CapyBrowser убирает очевидную метку `HeadlessChrome` из user agent, но это не инструмент для скрытности или обхода защиты. Если поисковик показал капчу, `web_search` переключится на другой поисковик, а если не получится — честно сообщит об этом.
</details>

<details>
<summary><b>Он куда-то отправляет мои данные?</b></summary>

Только на сайты, которые открывает модель, и в DuckDuckGo/Bing для `web_search`. Нет телеметрии, аккаунтов и API-ключей.
</details>

<details>
<summary><b>Умеет делать скриншоты, скачивать файлы, работать с несколькими вкладками?</b></summary>

В этой версии — нет, и это сделано намеренно: набор инструментов маленький, чтобы маленькие модели пользовались им без ошибок. Ссылки «в новой вкладке» открываются в той же вкладке, скачивания блокируются, диалоги JavaScript подтверждаются автоматически.
</details>

<details>
<summary><b>Страница обрезана. Модель может увидеть больше?</b></summary>

Модель вызывает `scroll`, чтобы читать дальше; в каждом ответе написано, какая часть страницы уже просмотрена. Для модели с большим контекстом лимит можно поднять: `--max-chars 8000`.
</details>

<details>
<summary><b>Как обновиться?</b></summary>

С `uvx --from git+…` при каждом запуске берётся свежая версия (uv её кэширует; `uv cache clean` принудительно обновит). С `uv tool install` — `uv tool upgrade capybrowser`.
</details>

### <img src="assets/icons/capy-bug.svg" width="40" height="40" alt="" align="absmiddle" /> Решение проблем

| Проблема | Что сделать |
|---|---|
| `No Chrome/Edge/Brave/Chromium found` | Установите один из браузеров или укажите путь: `--browser "C:\Program Files\Google\Chrome\Application\chrome.exe"`. |
| Приложение пишет, что MCP-сервер не запустился / не найден `uvx` | Приложения с окнами не всегда видят обновлённый `PATH`. Перезагрузите компьютер или укажите полный путь к `uvx`: Windows `C:\Users\<имя>\.local\bin\uvx.exe`, macOS/Linux `~/.local/bin/uvx`. |
| Браузер не запускается в Docker / под root на Linux | Это учтено автоматически (`--no-sandbox`, `--disable-dev-shm-usage`). Если всё равно не стартует — поставьте системные зависимости браузера (например, `apt-get install chromium`). |
| Корпоративный прокси | Браузер использует системные настройки прокси; на Linux также читает `HTTPS_PROXY`. |
| Страницы долго грузятся | Увеличьте `BROWSER_MCP_NAV_TIMEOUT` (в секундах). |
| Нужны логи | Добавьте `--log-level INFO`; логи пишутся в stderr (в Claude Desktop: Настройки → Разработчик → Open Logs Folder). |

### <img src="assets/icons/capy-terminal.svg" width="40" height="40" alt="" align="absmiddle" /> Разработка

```bash
git clone https://github.com/capyriver/CapyBrowser
cd CapyBrowser
uv run --extra dev pytest -q      # юнит-тесты, тесты с настоящим браузером и по протоколу MCP
uv run --extra dev ruff check .
uv run capybrowser --headed       # запуск из исходников
```

Перед первым релизом сервер прошёл больше 40 000 автоматических проверок: фаззинг на тысячах сгенерированных и битых HTML-страниц, циклы подключения/отключения и убийства процессов с проверкой утечек и зомби-процессов, некорректные аргументы через настоящий MCP-протокол и сквозные прогоны, где локальная 4B-модель выполняла реальные задачи на живых сайтах.

### <img src="assets/icons/capy-doc.svg" width="40" height="40" alt="" align="absmiddle" /> Лицензия

MIT — см. [LICENSE](LICENSE). Проект не связан с Google, Microsoft, Brave или Anthropic.

---

<a id="kk"></a>

## Қазақша

### <img src="assets/icons/capy-window.svg" width="40" height="40" alt="" align="absmiddle" /> Бұл не?

CapyBrowser — [MCP](https://modelcontextprotocol.io) сервері. Оны AI қолданбасына (Claude Desktop, Claude Code, Cursor, VS Code, LM Studio, Ollama негізіндегі өз агентіңіз…) қоссаңыз, модель нағыз браузерді пайдалана алады: интернеттен іздейді, беттерді ашады, оқиды, батырмаларды басады, формаларды толтырады, бетті айналдырады және артқа қайтады.

Ол ең алдымен **шағын жергілікті модельдерге** арналған. Әр бет қысқа әрі таза көрініске айналады — көрінетін мәтін мен нөмірленген басылатын элементтер тізімі — шамамен 3500 таңбадан аспайды. Сондықтан ноутбуктағы 4B модель де HTML-ге батып қалмай интернетті қолдана алады.

```text
URL: https://quotes.toscrape.com/login
--- TEXT ---
Login
Username
Password
--- ELEMENTS (use the [id] with click/type_text) ---
[3] input "Username"
[4] input:password "password"
[5] button "Login"
```

Модель `type_text(3, "admin")`, `click(5)` деп жауап береді — ал нағыз браузер оны орындайды.

### <img src="assets/icons/capy-chart.svg" width="40" height="40" alt="" align="absmiddle" /> Неге CapyBrowser

| | CapyBrowser | Әдеттегі браузер MCP серверлері |
|---|---|---|
| Орнату | бір сілтеме, `uvx` бәрін өзі жүктейді | Node + Playwright + ~300 МБ қосымша браузерлер |
| Браузер | компьютеріңізде бар Chrome / Edge / Brave / Chromium | бөлек жүктелген Chromium |
| Код | бір Python файлы, үш шағын тәуелділік (`mcp`, `websockets`, `pyyaml`) | үлкен фреймворктер |
| Модельге берілетін нәтиже | ықшам мәтін + `[id]` тізімі, қатаң көлем шегі | шикі қолжетімділік ағашы не скриншоттар, жиі 10–50 мың токен |
| Шағын жергілікті модельдер | 4B модельмен жасалып, тексерілген | әдетте үлкен бұлттық модель керек |

### <img src="assets/icons/capy-bolt.svg" width="40" height="40" alt="" align="absmiddle" /> Мүмкіндіктері

- **6 құрал:** `web_search`, `open_url`, `click`, `type_text`, `scroll`, `go_back`.
- **Браузерді өзі табады** — Windows, macOS және Linux жүйелерінде (Chrome, Edge, Brave, Chromium, Vivaldi; stable, beta, dev, Canary, Snap, Flatpak).
- **Кейінге қалдырылған іске қосу:** браузер құрал алғаш шақырылғанда бос портта, жаңа уақытша профильмен ашылады. Әдеттегі профиліңізге, құпиясөздер мен cookie-ге тиіспейді.
- **Заманауи сайттарда жұмыс істейді:** CDP арқылы нағыз тінтуір мен пернетақта оқиғалары, ал React/Vue өрістеріне, енгізу маскаларына (телефон, карта, күн), үстіңгі қабаттарға, арнайы белгі құсбелгілеріне, shadow DOM-ға, өз айналдыруы бар контейнерлерге және шексіз лентаға тексерілген қосалқы тәсілдер бар.
- **Күрделі вебті жеңеді:** `target=_blank` және `window.open` қалқымалы терезелері, `alert` / `confirm` / `beforeunload` диалогтары, қайта бағыттаулар, 404/500 беттері, DNS қателері, жауап бермейтін серверлер және шексіз JavaScript циклында қатып қалған беттер.
- **Өзін-өзі қалпына келтіру:** браузер құлап не қатып қалса, қайта іске қосылып, соңғы бетті қайта ашады.
- **Артында қоқыс қалдырмайды:** шыққанда, Ctrl+C және `SIGTERM` кезінде браузердің бүкіл процестер ағашы тоқтатылып, уақытша профиль өшіріледі; күшпен тоқтатылғаннан кейінгі қалдықтар келесі іске қосуда тазаланады.
- **Қауіпсіз әдепкі баптаулар:** DevTools тек `127.0.0.1` мекенжайын тыңдайды; `file://`, `javascript:` және `chrome://` сілтемелеріне тыйым салынған (`file://` анық түрде қосуға болады).

### <img src="assets/icons/capy-rocket.svg" width="40" height="40" alt="" align="absmiddle" /> Жылдам бастау

**1. `uv` орнатыңыз** (Python үшін жылдам пакет менеджері; Python-ды да өзі орнатады):

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Chrome, Edge, Brave немесе Chromium керек.** Windows-та Edge бар, сондықтан ештеңе орнатудың қажеті жоқ.

**3. CapyBrowser-ді AI қолданбасына қосыңыз.** Бүкіл сервер — бір сілтеме:

```json
{
  "mcpServers": {
    "capybrowser": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/capyriver/CapyBrowser", "capybrowser"]
    }
  }
}
```

Қолданбаны қайта іске қосып, сұраңыз: *«Python-ның соңғы нұсқасын интернеттен тауып, ресми бетін аш».*

### <img src="assets/icons/capy-orange.svg" width="40" height="40" alt="" align="absmiddle" /> capy.yaml — бір файл: модель + браузер + MCP

MCP қолданбасы жоқ па? Soup-тағы `soup.yaml` сияқты, CapyBrowser-де де **бір баптау файлы** бар. `capy.yaml` жазып, `capybrowser agent` іске қосыңыз — сонда модельіңіз (жергілікті не бұлттық) браузер арқылы интернетке кіріп, кез келген басқа MCP серверлерін пайдалана алады. Claude Desktop, Cursor немесе код қажет емес.

```yaml
model:
  provider: ollama            # ollama | openai (LM Studio, llama.cpp, vLLM, OpenRouter, OpenAI…)
  name: qwen3:4b-instruct
  base_url: http://127.0.0.1:11434
browser:
  lang: en-US                 # headed: true — show the window
mcp_servers:                  # any MCP servers, Claude Desktop format
  time:
    command: uvx
    args: ["mcp-server-time"]
```

```bash
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser init     # capy.yaml жасайды
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser agent    # чат
capybrowser agent "Python-ның соңғы нұсқасын тауып, өзгерістер тізімін аш"    # бір тапсырма
capybrowser agent -c work.yaml -m qwen3:8b --headed                          # басқа файл, модель, көрінетін браузер
```

- **`model`** — `ollama` (әдепкі) немесе кез келген OpenAI-үйлесімді API үшін `openai`: LM Studio (`http://127.0.0.1:1234/v1`), llama.cpp server, vLLM, OpenRouter, OpenAI. `api_key: ${OPENAI_API_KEY}` кілтті орта айнымалысынан алады — кілттерді файлға жазбаңыз.
- **`browser`** — кірістірілген CapyBrowser құралдары, төмендегі жалаушалармен бірдей параметрлер. `enabled: false` — өшіру.
- **`mcp_servers`** — `claude_desktop_config.json` форматындағы кез келген stdio MCP серверлері (`command`, `args`, `env`). Олардың құралдары браузермен қатар қосылады.
- **`agent`** — `max_steps`, `keep_full_results` (шағын модельдердің контексі таза болуы үшін ескі нәтижелер қысқартылады) және қосымша `system_prompt`.

Файл осы ретпен ізделеді: `./capy.yaml`, содан кейін `~/.capy.yaml`; `-c` арқылы басқасын көрсетуге болады. Түсініктемелері бар толық үлгі: [`capy.yaml`](capy.yaml).

### <img src="assets/icons/capy-plug.svg" width="40" height="40" alt="" align="absmiddle" /> Қолданбаларға қосу

- **Claude Desktop:** Баптаулар → Developer → Edit Config, немесе файлды тікелей ашыңыз: `%APPDATA%\Claude\claude_desktop_config.json` (Windows) / `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS). Жоғарыдағы JSON-ды қойып, қолданбаны қайта іске қосыңыз.
- **Claude Code:** `claude mcp add capybrowser -- uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser` (барлық жобаларға `-s user` қосыңыз).
- **Cursor:** `~/.cursor/mcp.json` немесе `.cursor/mcp.json`, мысал: [`examples/cursor_mcp.json`](examples/cursor_mcp.json).
- **VS Code (Copilot агент режимі):** `.vscode/mcp.json`, мұнда кілт `servers` деп аталады, мысал: [`examples/vscode_mcp.json`](examples/vscode_mcp.json).
- **LM Studio / Windsurf / Gemini CLI:** сол `mcpServers` JSON (`mcp.json`, `~/.codeium/windsurf/mcp_config.json`, `~/.gemini/settings.json`).
- **OpenAI Codex CLI:** `~/.codex/config.toml`, мысал: [`examples/codex_config.toml`](examples/codex_config.toml).
- **Ollama және басқа жергілікті модельдер:** Ollama өзі MCP-ны білмейді, сондықтан репозиторийде дайын агент бар — [`examples/ollama_agent.py`](examples/ollama_agent.py):

```bash
ollama pull qwen3:4b-instruct
uv run https://raw.githubusercontent.com/capyriver/CapyBrowser/main/examples/ollama_agent.py "https://news.ycombinator.com ашып, бірінші жаңалықты айт"
```

### <img src="assets/icons/capy-box.svg" width="40" height="40" alt="" align="absmiddle" /> Басқа орнату тәсілдері

```bash
uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser     # орнатусыз, әрқашан соңғы нұсқа
uv tool install git+https://github.com/capyriver/CapyBrowser            # capybrowser командасын тұрақты орнату
pipx install git+https://github.com/capyriver/CapyBrowser
uv run https://raw.githubusercontent.com/capyriver/CapyBrowser/main/browser_mcp.py   # бір файлды тікелей іске қосу
```

`uv tool install` кейін MCP баптауында `"command": "capybrowser"` жеткілікті. Жаңарту: `uv tool upgrade capybrowser`.

### <img src="assets/icons/capy-wrench.svg" width="40" height="40" alt="" align="absmiddle" /> Құралдар

| Құрал | Аргументтер | Не істейді |
|---|---|---|
| `web_search` | `query` | Интернеттен іздейді (DuckDuckGo, қосалқы — Bing) және 10-ға дейін нәтиже қайтарады: `[id]`, тақырып, URL, үзінді. |
| `open_url` | `url` | Бетті ашып, ықшам көрінісін қайтарады. `example.com` деп `https://`-сыз жазуға болады. |
| `click` | `element_id` | Осы `[id]` элементін басады (сілтеме, батырма, құсбелгі, қойынды, карточка…). |
| `type_text` | `element_id`, `text`, `submit=false` | Өріс / textarea / өңделетін аймақтағы мәтінді ауыстырады. `<select>` үшін нұсқаны мәтіні бойынша таңдайды. `submit=true` Enter басады. |
| `scroll` | `direction` | `down`, `up`, `top` немесе `bottom`. Бетті не оның негізгі айналдыру контейнерін айналдырады. |
| `go_back` | — | Тарих бойынша артқа қайтады. |

Әр құрал жаңартылған бетті қайтарады. `[id]` нөмірлері әр әрекеттен кейін қайта беріледі; қателер модель жалғастыра алатын қарапайым мәтінмен келеді.

### <img src="assets/icons/capy-sliders.svg" width="40" height="40" alt="" align="absmiddle" /> Параметрлер

| Жалауша | Орта айнымалысы | Әдепкі | Мағынасы |
|---|---|---|---|
| `--headed` | — | өшірулі | Браузер терезесін көрсету (агентті бақылау не жөндеу үшін). |
| `--browser PATH` | `BROWSER_MCP_PATH` | авто | Нақты браузер файлы. |
| `--max-chars N` | `BROWSER_MCP_MAX_CHARS` | `3500` | Кез келген құрал жауабының ең үлкен көлемі. |
| `--lang kk-KZ` | `BROWSER_MCP_LANG` | жүйе тілі | Браузер интерфейсінің тілі және `Accept-Language`. |
| `--allow-local-files` | `BROWSER_MCP_ALLOW_FILE=1` | өшірулі | `file://` мекенжайларын ашуға рұқсат беру. |
| — | `BROWSER_MCP_NAV_TIMEOUT` | `25` | Беттің жүктелуін қанша секунд күту. |
| `--log-level` | `BROWSER_MCP_LOG` | `WARNING` | Лог деңгейі (stderr-ге жазылады). |

### <img src="assets/icons/capy-question.svg" width="40" height="40" alt="" align="absmiddle" /> Жиі қойылатын сұрақтар

<details>
<summary><b>Чат-бот онсыз да интернеттен іздей алса, бұл не үшін керек?</b></summary>

Кірістірілген іздеу тек үзінділерді береді. CapyBrowser модельге нағыз браузер береді: кез келген бетті ашу, толық оқу, тест аккаунтына кіру, форманы толтырып жіберу, көп қадамды сайттан өту, JavaScript салатын беттерді оқу. Мұның бәрі интернеті мүлде жоқ **жергілікті модельдермен** де жұмыс істейді.
</details>

<details>
<summary><b>Жергілікті модельдермен жұмыс істей ме? Қайсысын таңдау керек?</b></summary>

Иә, бұл басты мақсат. Сервер Ollama-да (процессорда) **Qwen 3 4B Instruct** моделімен басынан аяғына дейін тексерілді: іздеу, беттер арасында өту, аккаунтқа кіру, форма толтыру, ұзын мақалаларды айналдыру, артқа қайту, 404 және DNS қателерін өңдеу. Құралдарды сенімді шақыратын кез келген модель жарайды; үлкенірек модельдер күрделі тапсырмаларда тұрақтырақ.
</details>

<details>
<summary><b>Python, Node.js немесе Playwright орнату керек пе?</b></summary>

Node.js пен Playwright керек емес. `uv` алғашқы іске қосуда лайықты Python-ды және екі тәуелділікті өзі жүктейді. Тек `uv` және Chromium негізіндегі браузер қажет.
</details>

<details>
<summary><b>Қандай браузерлер қолдау табады? Firefox пен Safari ше?</b></summary>

Chrome, Edge, Brave, Chromium, Vivaldi (beta/dev/Canary, Snap, Flatpak қоса). Firefox пен Safari қолдау таппайды, себебі CapyBrowser Chrome DevTools Protocol арқылы жұмыс істейді. Бірнеше браузер болса, қажеттісін `--browser` арқылы таңдауға болады.
</details>

<details>
<summary><b>Әдеттегі браузер профиліме зиян келтірмей ме?</b></summary>

Жоқ. Әр сессия жүйенің уақытша қалтасындағы жаңа профильді пайдаланады, ол шыққанда өшіріледі. Ашық тұрған браузеріңізге әсер етпейді — екеуі қатар жұмыс істей алады.
</details>

<details>
<summary><b>ЖИ не істеп жатқанын көруге бола ма?</b></summary>

Иә: `args` ішіне `--headed` қоссаңыз, қалыпты браузер терезесі ашылады. Linux-та бұл үшін жұмыс үстелі (`DISPLAY`/`WAYLAND_DISPLAY`) керек; онсыз терезесіз режимге автоматты түрде ауысады.
</details>

<details>
<summary><b>Капчаны шеше ме немесе боттардан қорғауды айналып өте ме?</b></summary>

Жоқ. Бұл жасырыну не қорғанысты айналып өту құралы емес. Іздеу жүйесі капча көрсетсе, `web_search` басқа іздеу жүйесіне ауысады, болмаса мәселе туралы хабарлайды.
</details>

<details>
<summary><b>Деректерімді бір жаққа жібере ме?</b></summary>

Тек модель ашқан сайттарға және `web_search` үшін DuckDuckGo/Bing-ке. Телеметрия, аккаунт немесе API кілті жоқ.
</details>

<details>
<summary><b>Бет қиылып қалды. Модель көбірек көре ала ма?</b></summary>

Модель әрі қарай оқу үшін `scroll` шақырады; әр жауапта беттің қанша бөлігі қаралғаны жазылады. Үлкен контексті модель үшін шекті `--max-chars 8000` арқылы көтеруге болады.
</details>

### <img src="assets/icons/capy-bug.svg" width="40" height="40" alt="" align="absmiddle" /> Мәселелерді шешу

| Мәселе | Не істеу керек |
|---|---|
| `No Chrome/Edge/Brave/Chromium found` | Браузерлердің бірін орнатыңыз немесе жолын `--browser` арқылы көрсетіңіз. |
| Қолданба MCP сервері іске қосылмады дейді / `uvx` табылмады | Терезелі қолданбалар жаңартылған `PATH`-ты әрдайым көре бермейді. Компьютерді қайта іске қосыңыз немесе `uvx` толық жолын көрсетіңіз: Windows `C:\Users\<атыңыз>\.local\bin\uvx.exe`, macOS/Linux `~/.local/bin/uvx`. |
| Linux-та Docker ішінде / root ретінде браузер ашылмайды | Бұл автоматты түрде ескерілген (`--no-sandbox`, `--disable-dev-shm-usage`); бәрібір ашылмаса, браузердің жүйелік тәуелділіктерін орнатыңыз. |
| Беттер баяу жүктеледі | `BROWSER_MCP_NAV_TIMEOUT` мәнін үлкейтіңіз. |
| Лог керек | `--log-level INFO` қосыңыз; логтар stderr-ге жазылады. |

### <img src="assets/icons/capy-shield.svg" width="40" height="40" alt="" align="absmiddle" /> Қауіпсіздік

Модель **бос профилі бар бөлек браузер** алады: әдеттегі cookie, аккаунтқа кірулер, құпиясөздер мен кеңейтімдерге қолы жетпейді. DevTools тек `127.0.0.1` мекенжайына байланған. Беттерде ЖИ агенттерін алдауға арналған мәтін болуы мүмкін (prompt injection) — агентті банк, пошта не жұмыс аккаунттарында қараусыз қалдырмаңыз. Толығырақ: [SECURITY.md](SECURITY.md).

### <img src="assets/icons/capy-doc.svg" width="40" height="40" alt="" align="absmiddle" /> Лицензия

MIT — [LICENSE](LICENSE) қараңыз. Жоба Google, Microsoft, Brave немесе Anthropic компанияларымен байланысты емес.

---

<p align="center">
  <img src="assets/icons/capy-terminal.svg" width="56" height="56" alt="" />
  <img src="assets/icons/capy.svg" width="56" height="56" alt="" />
  <img src="assets/icons/capy-rocket.svg" width="56" height="56" alt="" />
</p>

<p align="center">
  If CapyBrowser is useful to you, please ⭐ <b>star the repository</b> — it helps others find it.<br />
  <sub>Made by <a href="https://github.com/capyriver">CapyRiver</a> · <a href="https://t.me/CapyRiver">Telegram</a> · <a href="https://capyriver.pages.dev/">Website</a></sub>
</p>

<p align="center">
  <img src="assets/footer.svg" alt="" width="100%" />
</p>
