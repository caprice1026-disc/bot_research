param(
    [string]$Output = 'results/offline-v1',
    [string]$SourceDir = '../swingguard-research/data'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
python tests/run_tests.py
if ($LASTEXITCODE -ne 0) { throw 'Tests failed before research' }
python -u run.py prepare --output $Output --source-dir $SourceDir
if ($LASTEXITCODE -ne 0) { throw 'Preparation failed (use a fresh output directory)' }
foreach ($method in @('fixed_gp','dual_gp','random','noise_gp')) {
    foreach ($seed in @(11,23,37,53,71)) {
        python -u run.py search --output $Output --method $method --seed $seed
        if ($LASTEXITCODE -ne 0) { throw "Search failed: $method / $seed" }
    }
}
python -u run.py evaluate --output $Output
if ($LASTEXITCODE -ne 0) { throw 'One-shot evaluation failed; preserve evidence, do not tune/retry' }
python -u run.py verify --output $Output
if ($LASTEXITCODE -ne 0) { throw 'Artifact verification failed' }
