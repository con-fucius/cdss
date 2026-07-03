$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

. (Join-Path $PSScriptRoot "env.ps1")

Push-Location $Root
try {
  $Python = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $Python) {
    & $Python -m app.migrations
  }
  else {
    uv run python -m app.migrations
  }
}
finally {
  Pop-Location
}
