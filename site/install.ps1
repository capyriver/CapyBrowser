# CapyBrowser installer for Windows (PowerShell):
#   irm capybrowser.pages.dev | iex
$ErrorActionPreference = 'Stop'
$src = $env:CAPYBROWSER_SOURCE
if (-not $src) { $src = 'https://github.com/capyriver/CapyBrowser/archive/refs/heads/main.zip' }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host '==> Installing uv (fast Python package manager)' -ForegroundColor DarkYellow
  powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host '==> Installing CapyBrowser' -ForegroundColor DarkYellow
uv tool install --force --quiet $src
if ($LASTEXITCODE -ne 0) { throw 'CapyBrowser install failed' }
uv tool update-shell *> $null

$bin = Join-Path (uv tool dir --bin) 'capybrowser.exe'
Write-Host ''
Write-Host 'CapyBrowser is installed.' -ForegroundColor Green
Write-Host '  Control panel:   capybrowser ui'
Write-Host '  Add to any AI app (mcpServers):'
Write-Host ('    "capybrowser": { "command": "' + ($bin -replace '\\', '\\') + '" }')
Write-Host '  Open a new terminal if "capybrowser" is not found yet.'
