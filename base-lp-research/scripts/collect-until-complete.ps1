param(
    [string]$Root = '',
    [int]$RunSeconds = 90,
    [int]$PauseSeconds = 30,
    [int]$ChunkSize = 500,
    [double]$RequestIntervalSeconds = 0.25,
    [int]$RpcTimeoutSeconds = 20,
    [int]$RpcMaxRetries = 2
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$config = Join-Path $projectRoot 'configs\base_weth_usdc_005.yaml'
$collectionRoot = $projectRoot
if (-not [string]::IsNullOrWhiteSpace($Root)) {
    $collectionRoot = (Resolve-Path -LiteralPath $Root).Path
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment was not found: $python"
}

if ([string]::IsNullOrWhiteSpace($env:BASE_RPC_URL)) {
    $env:BASE_RPC_URL = 'https://mainnet.base.org'
}

Push-Location $projectRoot
try {
    while ($true) {
        Write-Output ("{0} Starting a bounded collection run." -f (Get-Date -Format o))
        & $python -m base_lp.cli --root $collectionRoot collect `
            --config $config `
            --chunk-size $ChunkSize `
            --request-interval-seconds $RequestIntervalSeconds `
            --rpc-timeout-seconds $RpcTimeoutSeconds `
            --rpc-max-retries $RpcMaxRetries `
            --max-seconds $RunSeconds
        $exitCode = $LASTEXITCODE

        if ($exitCode -eq 0) {
            Write-Output ("{0} Collection completed." -f (Get-Date -Format o))
            exit 0
        }
        if ($exitCode -ne 2) {
            Write-Error "Collection stopped with exit code $exitCode."
            exit $exitCode
        }

        Write-Output ("{0} Checkpoint saved; pausing {1} seconds before resuming." -f (Get-Date -Format o), $PauseSeconds)
        Start-Sleep -Seconds $PauseSeconds
    }
}
finally {
    Pop-Location
}
