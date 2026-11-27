#!/bin/sh
# CapyBrowser installer for macOS and Linux:
#   curl -fsSL capybrowser.pages.dev | sh
set -e
SRC="$CAPYBROWSER_SOURCE"
[ -n "$SRC" ] || SRC="https://github.com/capyriver/CapyBrowser/archive/refs/heads/main.zip"
say() { printf '\033[38;5;173m==>\033[0m %s\n' "$1"; }

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (fast Python package manager)"
  if command -v curl >/dev/null 2>&1; then curl -LsSf https://astral.sh/uv/install.sh | sh
  else wget -qO- https://astral.sh/uv/install.sh | sh; fi
  PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; export PATH
fi

say "Installing CapyBrowser"
uv tool install --force --quiet "$SRC"
uv tool update-shell >/dev/null 2>&1 || true

BIN="$(uv tool dir --bin 2>/dev/null)/capybrowser"
printf '\n\033[1mCapyBrowser is installed.\033[0m\n\n'
printf '  Control panel:   capybrowser ui\n'
printf '  Add to any AI app (mcpServers):\n'
printf '    "capybrowser": { "command": "%s" }\n\n' "$BIN"
printf '  Open a new terminal if "capybrowser" is not found yet.\n'
