param(
    [string]$Archive = (Join-Path $env:TEMP 'muxivo-core-release.tar.gz'),
    [string]$ModelArchive = (Join-Path $env:TEMP 'muxivo-core-model.tar.gz'),
    [string]$ModelDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) 'models/rubert-tiny2-moderation-trained')
)

$ErrorActionPreference = 'Stop'

if (Test-Path $Archive) {
    Remove-Item -LiteralPath $Archive
}

if (Test-Path $ModelArchive) {
    Remove-Item -LiteralPath $ModelArchive
}

tar --exclude='./.git' `
    --exclude='./venv' `
    --exclude='./.venv' `
    --exclude='./activity/client/node_modules' `
    --exclude='./data' `
    --exclude='./logs' `
    --exclude='./.tmp' `
    --exclude='./.env' `
    --exclude='./__pycache__' `
    --exclude='./.pytest_cache' `
    --exclude='./htmlcov' `
    --exclude='./.coverage' `
    --exclude='./models' `
    --exclude='./data' `
    --exclude='./logs' `
    --exclude='./attachments' `
    --exclude='./.mypy_cache' `
    --exclude='./.ruff_cache' `
    --exclude='./ollama_models' `
    --exclude='./.ollama' `
    -czf $Archive .

Write-Host "Muxivo Core release archive created: $Archive"

if (-not (Test-Path -LiteralPath $ModelDirectory)) {
    throw "Trained model directory was not found: $ModelDirectory"
}

# Production inference needs only the model's root artifacts. Checkpoints and reports
# are training outputs and would make deployments unnecessarily large.
$modelFiles = @(
    'config.json',
    'model.safetensors',
    'pytorch_model.bin',
    'special_tokens_map.json',
    'thresholds.json',
    'tokenizer.json',
    'tokenizer_config.json',
    'vocab.txt'
)
$modelRoot = Split-Path $ModelDirectory -Parent
$availableModelFiles = @($modelFiles | Where-Object { Test-Path -LiteralPath (Join-Path $ModelDirectory $_) })
if ($availableModelFiles.Count -eq 0 -or -not ($availableModelFiles -contains 'config.json') -or -not ($availableModelFiles -contains 'model.safetensors')) {
    throw "The trained model is missing required inference artifacts in: $ModelDirectory"
}

Push-Location $modelRoot
try {
    $modelArchivePaths = @($availableModelFiles | ForEach-Object { "rubert-tiny2-moderation-trained/$_" })
    tar -czf $ModelArchive @modelArchivePaths
}
finally {
    Pop-Location
}

Write-Host "Muxivo Core model archive created: $ModelArchive"
