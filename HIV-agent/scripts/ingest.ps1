param(
  [Parameter(Mandatory = $true)]
  [string[]]$Disease
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

. (Join-Path $PSScriptRoot "env.ps1")

Push-Location $Root
try {
  $Python = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $Python) {
    & $Python -m app.ingest --disease $Disease
  }
  else {
    uv run python -m app.ingest --disease $Disease
  }
}
finally {
  Pop-Location
}
