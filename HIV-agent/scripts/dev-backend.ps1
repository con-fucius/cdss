$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

. (Join-Path $PSScriptRoot "env.ps1")

Push-Location $Root
try {
  $UvicornArgs = @("app.api:app", "--host", "127.0.0.1", "--port", "8000")
  if ($env:CDSS_BACKEND_RELOAD -eq "1") {
    $UvicornArgs += "--reload"
  }

  $Python = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $Python) {
    & $Python -m uvicorn @UvicornArgs
  }
  else {
    uv sync --frozen
    uv run uvicorn @UvicornArgs
  }
}
finally {
  Pop-Location
}
