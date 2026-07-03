$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

Push-Location (Join-Path $Root "frontend")
try {
  if (-not $env:VITE_DEV_PROXY_TARGET) {
    $env:VITE_DEV_PROXY_TARGET = "http://127.0.0.1:8000"
  }
  pnpm install --frozen-lockfile
  pnpm dev --host 127.0.0.1 --port 5173
}
finally {
  Pop-Location
}
