[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidatePattern('^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [switch]$PackageOnly,
    [switch]$Prerelease,
    [string]$NotesFile,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = $PSScriptRoot
$migrationManifestPath = Join-Path $projectRoot 'effect_migrations.json'
$versionPattern = '^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$'
$currentVersion = 'unknown'
$currentVersionNumber = $null
$usingManifestVersion = $false

if (Test-Path -LiteralPath $migrationManifestPath -PathType Leaf) {
    try {
        $migrationManifest = Get-Content -LiteralPath $migrationManifestPath -Raw | ConvertFrom-Json
        if ($migrationManifest.catalog_version) {
            $currentVersionNumber = $migrationManifest.catalog_version.ToString().TrimStart('v')
            $currentVersion = "v$currentVersionNumber"
        }
    }
    catch {
        Write-Warning "Could not read the current version from effect_migrations.json: $($_.Exception.Message)"
    }
}

if ([string]::IsNullOrWhiteSpace($Version)) {
    Write-Host ''
    Write-Host "Current catalog version: $currentVersion" -ForegroundColor Green

    if ($Yes) {
        if (-not $currentVersionNumber) {
            throw 'No version was supplied and effect_migrations.json does not contain a catalog_version.'
        }
        $Version = $currentVersionNumber
        $usingManifestVersion = $true
    }
    else {
        while ($true) {
            $prompt = if ($currentVersionNumber) {
                "Enter release version (press Enter to use $currentVersion)"
            } else {
                'Enter release version (e.g., 1.2.0)'
            }

            $userInput = (Read-Host $prompt).Trim()

            if ([string]::IsNullOrWhiteSpace($userInput)) {
                if ($currentVersionNumber) {
                    $Version = $currentVersionNumber
                    $usingManifestVersion = $true
                    break
                }
                Write-Warning 'A version number is required because effect_migrations.json has no catalog_version.'
                continue
            }

            if ($userInput -match $versionPattern) {
                $Version = $userInput
                break
            }

            Write-Warning "Invalid version format '$userInput'. Expected format like 1.2.0 or v1.2.0-beta.1."
        }
    }
}

$versionNumber = $Version.TrimStart('v')
$tag = "v$versionNumber"
$packageName = "Elite-Haptics-$tag"
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

    Write-Host ''
    Write-Host "Current version: $currentVersion" -ForegroundColor Green
    Write-Host '  Recorded in effect_migrations.json as catalog_version.' -ForegroundColor DarkGray
    if ($usingManifestVersion) {
        Write-Host '  No version supplied; using the recorded catalog version.' -ForegroundColor Yellow
    }
    Write-Host "Requested release: $tag" -ForegroundColor Cyan
    Write-Host ''

    $previousTag = @(git describe --tags --abbrev=0)
    if ($LASTEXITCODE -ne 0 -or $previousTag.Count -ne 1) {
        throw 'Unable to determine the previous release tag for effect migration checks.'
    }
    $migrationArguments = @(
        'generate_effect_migrations.py',
        '--previous-git-ref', $previousTag[0],
        '--current', 'effects.default.json',
        '--to-version', $versionNumber,
        '--output', 'effect_migrations.json'
    )
    & '.\.venv\Scripts\python.exe' @migrationArguments '--check'
    if ($LASTEXITCODE -ne 0) {
        $preparationChanges = @(git status --porcelain --untracked-files=all)
        if ($LASTEXITCODE -ne 0) {
            throw 'Unable to check the Git working tree before preparing effect migrations.'
        }
        if ($preparationChanges.Count -gt 0) {
            throw ('Effect migrations need preparation, but the repository already has ' +
                   'uncommitted files. Commit or stash them, then rerun this command.')
        }

        Write-Host "Preparing effect migrations for $tag..." -ForegroundColor Yellow
        Invoke-CheckedCommand '.\.venv\Scripts\python.exe' @migrationArguments
        Write-Host ''
        Write-Host 'effect_migrations.json was updated.' -ForegroundColor Green
        Write-Host 'Review and commit it, then rerun release.ps1 to build the package.' `
            -ForegroundColor Yellow
        throw 'Release stopped after preparing effect migrations.'
    }

    # The normal build generates both changelog.json and the normalized
    # GitHub release notes before the repository cleanliness check.
    Write-Host "Building Elite Haptics $tag..." -ForegroundColor Cyan
    Invoke-CheckedCommand cmd.exe '/c' 'build-ui.cmd'

    $uncommittedChanges = @(git status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to check the Git working tree.'
    }
    if ($uncommittedChanges.Count -gt 0) {
        throw 'The repository has uncommitted files. Commit or stash them before creating a release.'
    }

    $packageFiles = @(
        @{ Source = 'dist\elite-haptics.exe'; Destination = 'elite-haptics.exe' }
        @{ Source = 'dist\effects.default.json'; Destination = 'effects.default.json' }
        @{ Source = 'dist\effects.legacy-manifest.json'; Destination = 'effects.legacy-manifest.json' }
        @{ Source = 'dist\effect_migrations.json'; Destination = 'effect_migrations.json' }
        @{ Source = 'dist\CHANGELOG.md'; Destination = 'CHANGELOG.md' }
        @{ Source = 'dist\changelog.json'; Destination = 'changelog.json' }
        @{ Source = 'dist\triggers.json'; Destination = 'triggers.json' }
        @{ Source = 'dist\EFFECTS_GUIDE.md'; Destination = 'EFFECTS_GUIDE.md' }
        @{ Source = 'dist\effects-guide.json'; Destination = 'effects-guide.json' }
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
        $generatedNotesFile = (Resolve-Path -LiteralPath 'build\release-notes.md').Path
        $releaseArguments += @('--notes-file', $generatedNotesFile)
    }
    if ($Prerelease) {
        $releaseArguments += '--prerelease'
    }

    Invoke-CheckedCommand gh @releaseArguments
    Write-Host "Published Elite Haptics $tag." -ForegroundColor Green
} finally {
    Pop-Location
}
