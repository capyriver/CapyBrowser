# Security

CapyBrowser lets a language model control a real browser on your machine. Treat it like giving someone your keyboard.

**What it does by default**

- Starts a *separate* browser instance with a fresh, temporary profile. Your normal profile, cookies, saved passwords and extensions are not used and not visible to the model.
- Listens for DevTools only on `127.0.0.1` and deletes the temporary profile on exit.
- Refuses `file://`, `javascript:`, `chrome://` and similar URLs unless you start it with `--allow-local-files` (only `file://` can be enabled).
- Sends nothing anywhere except the pages the model opens and, for `web_search`, the search engine (DuckDuckGo, falling back to Bing).

**What you should keep in mind**

- Web pages can contain text written to manipulate AI agents (prompt injection). Review what an agent does before letting it act on accounts, payments or private data.
- Anything you log into inside the agent's browser session is available to the model during that session.
- Run it only with MCP clients and models you trust.

**Reporting a vulnerability**

Please open a private security advisory on GitHub (Security → Report a vulnerability) or contact the maintainer via [Telegram](https://t.me/CapyRiver). Do not file public issues for undisclosed vulnerabilities.
