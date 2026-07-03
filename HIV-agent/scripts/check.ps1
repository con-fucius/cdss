$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

. (Join-Path $PSScriptRoot "env.ps1")
$env:CDSS_AUDIT_DB_PATH = ":memory:"
$env:CDSS_SESSION_STORAGE_BACKEND = "memory"

Push-Location $Root
try {
  $Python = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $Python) {
    & $Python -B -m unittest discover -p "test_*.py" -q
  }
  else {
    uv run python -B -m unittest discover -p "test_*.py" -q
  }
  Push-Location (Join-Path $Root "frontend")
  try {
    pnpm lint
    pnpm build
  }
  finally {
    Pop-Location
  }
}
finally {
  Pop-Location
}
