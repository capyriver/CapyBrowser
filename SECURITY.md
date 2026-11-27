# Security

CapyBrowser lets a language model control a real browser on your machine. Treat it like giving someone your keyboard.

**What it does by default**

- Starts a *separate* browser instance with a fresh, temporary profile. Your normal profile, cookies, saved passwords and extensions are not used and not visible to the model. With `profile_dir` the agent's own profile is kept between runs; never point it at your everyday browser profile.
- Listens for DevTools only on `127.0.0.1` and deletes the temporary profile on exit.
- Refuses `file://`, `javascript:`, `chrome://` and similar URLs unless you start it with `--allow-local-files` (only `file://` can be enabled).
- Sends nothing anywhere except the pages the model opens and, for `web_search`, the search engine (DuckDuckGo, falling back to Bing).

**Connectors (other MCP servers from capy.yaml)**

- Put tokens in environment variables or a `.env` file next to `capy.yaml` and reference them as `${VAR}`; `.env` is git-ignored. Never commit real tokens.
- Configured secrets and common token formats (GitHub, OpenAI-style, Google, Slack, AWS, JWT, `Bearer …`) are masked in every connector answer and in CapyBrowser's logs. Output written directly by a connector process to its own stderr is not filtered.
- OAuth tokens are stored in `~/.capybrowser/oauth/` (file mode 600 on macOS/Linux). Delete a file there to sign out of that connector. Set `CAPYBROWSER_HOME` to move the folder.
- A connector runs with your user's permissions. Only add servers you trust.

**What you should keep in mind**

- Web pages can contain text written to manipulate AI agents (prompt injection). Review what an agent does before letting it act on accounts, payments or private data.
- Anything you log into inside the agent's browser session is available to the model during that session.
- Run it only with MCP clients and models you trust.

**Reporting a vulnerability**

Please open a private security advisory on GitHub (Security → Report a vulnerability) or contact the maintainer via [Telegram](https://t.me/CapyRiver). Do not file public issues for undisclosed vulnerabilities.
