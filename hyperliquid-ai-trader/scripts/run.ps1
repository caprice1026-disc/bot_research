param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("preflight", "dry-run", "canary", "run-local", "report")]
    [string]$Command,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path (Split-Path -Parent $projectRoot) ".env"

foreach ($name in "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if ($value -eq "http://127.0.0.1:9") {
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
}

& $python -m hyperliquid_ai_trader.cli --env-file $envFile $Command @RemainingArgs
exit $LASTEXITCODE
