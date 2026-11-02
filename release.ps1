[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidatePattern('^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [switch]$PackageOnly,
    [switch]$Prerelease,
    [string]$NotesFile,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$versionNumber = $Version.TrimStart('v')
$tag = "v$versionNumber"
$packageName = "Elite-Haptics-$tag"
$projectRoot = $PSScriptRoot
$releaseDirectory = Join-Path $projectRoot 'releases'
$stagingDirectory = Join-Path $releaseDirectory $packageName
$archivePath = Join-Path $releaseDirectory "$packageName.zip"

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Command,
        [Parameter(ValueFromRemainingArguments)]
        [string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $Command $($Arguments -join ' ')"
    }
}

Push-Location $projectRoot
try {
    Invoke-CheckedCommand git 'rev-parse' '--is-inside-work-tree'

    $uncommittedChanges = @(git status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to check the Git working tree.'
    }
    if ($uncommittedChanges.Count -gt 0) {
        throw 'The repository has uncommitted files. Commit or stash them before creating a release.'
    }

    Write-Host "Building Elite Haptics $tag..." -ForegroundColor Cyan
    Invoke-CheckedCommand cmd.exe '/c' 'build-ui.cmd'

    $packageFiles = @(
        @{ Source = 'dist\elite-haptics.exe'; Destination = 'elite-haptics.exe' }
        @{ Source = 'dist\effects.json'; Destination = 'effects.json' }
        @{ Source = 'dist\triggers.json'; Destination = 'triggers.json' }
        @{ Source = 'dist\EFFECTS_GUIDE.md'; Destination = 'EFFECTS_GUIDE.md' }
        @{ Source = 'dist\TRIGGERS_GUIDE.md'; Destination = 'TRIGGERS_GUIDE.md' }
        @{ Source = 'README.md'; Destination = 'README.md' }
        @{ Source = 'LICENSE'; Destination = 'LICENSE' }
    )

    foreach ($file in $packageFiles) {
        if (-not (Test-Path -LiteralPath $file.Source -PathType Leaf)) {
            throw "Required release file is missing: $($file.Source)"
        }
    }

    New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $stagingDirectory) {
        Remove-Item -LiteralPath $stagingDirectory -Recurse -Force
    }
    New-Item -ItemType Directory -Path $stagingDirectory | Out-Null

    foreach ($file in $packageFiles) {
        Copy-Item -LiteralPath $file.Source -Destination (Join-Path $stagingDirectory $file.Destination)
    }

    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Compress-Archive -Path (Join-Path $stagingDirectory '*') -DestinationPath $archivePath -CompressionLevel Optimal
    Remove-Item -LiteralPath $stagingDirectory -Recurse -Force

    Write-Host "Package created: $archivePath" -ForegroundColor Green

    if ($PackageOnly) {
        Write-Host 'Package-only mode selected; nothing was pushed or published.' -ForegroundColor Yellow
        return
    }

    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw 'GitHub CLI (gh) is required to publish. Install it from https://cli.github.com/ and run: gh auth login'
    }
    Invoke-CheckedCommand gh 'auth' 'status'

    if (@(git tag --list $tag).Count -gt 0) {
        throw "Git tag $tag already exists. Choose a new version."
    }
    $remoteTag = @(git ls-remote --tags origin "refs/tags/$tag")
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to check tags on GitHub.'
    }
    if ($remoteTag.Count -gt 0) {
        throw "Git tag $tag already exists on GitHub. Choose a new version."
    }

    if (-not $Yes) {
        $releaseType = if ($Prerelease) { 'prerelease' } else { 'release' }
        $answer = Read-Host "Publish $tag as a GitHub $releaseType and upload $packageName.zip? [y/N]"
        if ($answer -notmatch '^(?i:y|yes)$') {
            Write-Host 'Publishing cancelled. The local ZIP was kept.' -ForegroundColor Yellow
            return
        }
    }

    Invoke-CheckedCommand git 'push' 'origin' 'main'

    $releaseArguments = @(
        'release', 'create', $tag, $archivePath,
        '--target', 'main',
        '--title', "Elite Haptics $tag"
    )
    if ($NotesFile) {
        $resolvedNotesFile = (Resolve-Path -LiteralPath $NotesFile).Path
        $releaseArguments += @('--notes-file', $resolvedNotesFile)
    } else {
        $releaseArguments += '--generate-notes'
    }
    if ($Prerelease) {
        $releaseArguments += '--prerelease'
    }

    Invoke-CheckedCommand gh @releaseArguments
    Write-Host "Published Elite Haptics $tag." -ForegroundColor Green
} finally {
    Pop-Location
}
