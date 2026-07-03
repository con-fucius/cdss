$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

. (Join-Path $PSScriptRoot "env.ps1")

Push-Location $Root
try {
  docker compose up -d postgres
  $Python = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $Python) {
    & $Python -m app.migrations
    & $Python -m scripts.seed_evidence
  }
  else {
    uv sync --frozen
    uv run python -m app.migrations
    uv run python -m scripts.seed_evidence
  }
}
finally {
  Pop-Location
}
