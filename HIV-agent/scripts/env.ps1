param(
  [string]$EnvPath = (Join-Path (Split-Path $PSScriptRoot -Parent) ".env")
)

if (Test-Path -LiteralPath $EnvPath) {
  Get-Content -LiteralPath $EnvPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
      return
    }
    $key, $value = $line.Split("=", 2)
    if ($key) {
      [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim(), "Process")
    }
  }
}

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
if (-not $env:UV_CACHE_DIR) {
  $Root = Split-Path $PSScriptRoot -Parent
  $env:UV_CACHE_DIR = Join-Path $Root ".runtime\uv-cache"
}
