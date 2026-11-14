[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidatePattern('^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$generator = Join-Path $projectRoot 'generate_effect_migrations.py'
$manifest = Join-Path $projectRoot 'effect_migrations.json'

Push-Location $projectRoot
try {
    $arguments = @(
        $generator,
        '--current', 'effects.default.json',
        '--output', $manifest
    )
    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        $arguments += @('--to-version', $Version.TrimStart('v'))
    }

    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Effect migration preparation failed with exit code $LASTEXITCODE."
    }

    $preparedManifest = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
    $preparedVersion = $preparedManifest.catalog_version.ToString().TrimStart('v')
    if ([string]::IsNullOrWhiteSpace($preparedVersion)) {
        throw 'effect_migrations.json does not contain a catalog_version.'
    }

    & $python $generator '--current' 'effects.default.json' '--output' $manifest `
        '--to-version' $preparedVersion '--check'
    if ($LASTEXITCODE -ne 0) {
        throw "Effect migration verification failed with exit code $LASTEXITCODE."
    }

    Write-Host ''
    Write-Host "Effect migrations are prepared for v$preparedVersion." -ForegroundColor Green
    Write-Host 'Review and commit the manifest:' -ForegroundColor Cyan
    Write-Host '  git diff -- effect_migrations.json'
    Write-Host '  git add effect_migrations.json'
    Write-Host "  git commit -m `"chore: prepare effect migrations for v$preparedVersion`""
    Write-Host ''
    Write-Host 'Then run:' -ForegroundColor Cyan
    Write-Host "  .\release.ps1 $preparedVersion -PackageOnly"
}
finally {
    Pop-Location
}
