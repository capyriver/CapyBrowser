# Contributing to CapyBrowser

Thanks for helping! Bug reports, site-compatibility reports and pull requests are all welcome.

## Quick setup

```bash
git clone https://github.com/capyriver/CapyBrowser
cd CapyBrowser
uv run --extra dev pytest -q      # needs Chrome, Edge, Brave or Chromium installed
uv run --extra dev ruff check .
uv run capybrowser --headed       # watch the browser while you test
```

## Guidelines

- The server stays **one file** (`browser_mcp.py`) with **three small dependencies** (`mcp`, `websockets`, `pyyaml`). Please keep it that way.
- Keep tool output compact: every response must fit `--max-chars` (3500 by default) because small local models depend on it.
- New behaviour needs a test in `tests/` — preferably a tiny local HTML page in the test server rather than a live website.
- Tests run on Windows, macOS and Linux in CI; avoid OS-specific assumptions.
- Write commit messages that explain *why*, not only *what*.

## Reporting a site that does not work

Open an issue with the URL, what you asked the model to do, the tool calls it made and the page view CapyBrowser returned. Running with `--headed --log-level INFO` usually shows what went wrong.

Security problems: please follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
