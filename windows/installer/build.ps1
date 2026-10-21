<#
.SYNOPSIS
    Build OmoBenchSetup.exe, its metadata, and its checksums.

.DESCRIPTION
    Builds the companion first, then compiles the installer with that exact
    companion embedded inside it. Both come from one git revision, which is
    stamped into the installer and reported by it, so "which build is this?"
    has a single answer that can be checked against a release.

    Like the companion, this uses the C# compiler that ships inside Windows
    (the in-box .NET Framework csc.exe). There is no SDK, no NuGet restore, no
    network access, and no installer toolchain to install first -- the whole
    installer is ordinary C# in this repository.

    Nothing produced here is committed: the build directory is ignored.

.PARAMETER Test
    Also build and run the installer's own test harness.

.PARAMETER Version
    Product version. Defaults to the contents of VERSION next to this script.

.PARAMETER Revision
    Source revision to stamp in. Defaults to the current git HEAD.

.PARAMETER SigningIdentity
    Not supported in this milestone, and rejected if supplied. This script
    neither signs nor verifies the executable, so it has no basis for claiming
    a publisher; stamping one in would assert exactly the thing a person
    cannot check for themselves. Sign the output as a separate step and
    publish the checksum of the signed file.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File windows\installer\build.ps1 -Test
#>
[CmdletBinding()]
param(
    [switch]$Test,
    [string]$OutputDirectory,
    [string]$Version,
    [string]$Revision,
    [string]$SigningIdentity
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$repository = Resolve-Path (Join-Path $root '..\..')
$companionRoot = Join-Path $repository 'windows\companion'
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root 'build' }

if (-not $Version) {
    $versionFile = Join-Path $root 'VERSION'
    if (-not (Test-Path $versionFile)) { throw "No version given and $versionFile is missing." }
    $Version = (Get-Content $versionFile -Raw).Trim()
}

# Signing is not supported in this milestone. The switch exists so the
# intent is visible, but accepting it would stamp "signed" into a build that
# was never signed or verified -- a claim the operating system immediately
# contradicts, and the one piece of provenance a person cannot check for
# themselves. Refuse rather than lie.
if ($SigningIdentity) {
    throw ("-SigningIdentity is not supported: this build process neither signs nor verifies the " +
        "executable, so it cannot honestly claim a publisher. Sign the output separately and " +
        "publish the resulting checksum.")
}
$signed = $false

# What the revision is, and whether it describes this binary.
#
# `git rev-parse HEAD` answers "which commit is checked out", not "does the
# working tree match it". A build from a modified tree would otherwise claim a
# revision it does not contain -- and its managed environment would then
# download that revision's source, which is not the source embedded here.
#
# A git query that fails tells us nothing, so it must never be read as "clean".
$sourceClean = $false
$gitAnswered = $false
$headRevision = ''
try {
    $headRevision = (& git -C $repository rev-parse HEAD 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $headRevision) {
        $pending = (& git -C $repository status --porcelain 2>$null | Out-String)
        if ($LASTEXITCODE -eq 0) {
            $gitAnswered = $true
            $sourceClean = [string]::IsNullOrWhiteSpace($pending)
        }
    }
} catch {
    $gitAnswered = $false
}

if ($Revision) {
    # An explicitly supplied revision is a claim about this source tree, so it
    # is checked rather than trusted. CI passes the commit it checked out; a
    # mismatch there means the workflow is building something else.
    if ($gitAnswered -and $Revision -ne $headRevision) {
        throw ("-Revision $Revision does not match the checked-out commit $headRevision. " +
            "The installer would claim a revision it was not built from.")
    }
    if (-not $gitAnswered) {
        Write-Warning ("Git could not confirm the checked-out commit, so the supplied revision " +
            "$Revision cannot be verified. This build is marked as not source-clean.")
    }
} else {
    $Revision = $headRevision
}

if (-not $Revision) {
    Write-Warning "No git revision available; the installer will not be able to fetch pinned sources."
}
if (-not $sourceClean) {
    Write-Warning ("This build is marked as not matching revision $Revision" +
        $(if ($gitAnswered) { ' (the working tree has uncommitted changes)' } else { ' (git could not be queried)' }) +
        ". Its managed environment would download that revision's source, which is not necessarily " +
        "what is embedded here. Commit, and build from a clean tree, before producing a build for anyone else.")
}

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $csc)) {
    throw "The in-box C# compiler was not found. Expected $csc (part of the .NET Framework that ships with Windows)."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

# 1. The companion, built from the same sources this installer will carry.
$companionOutput = Join-Path $OutputDirectory 'payload'
Write-Host "Building the companion payload"
& (Join-Path $companionRoot 'build.ps1') -OutputDirectory $companionOutput | Out-Null
$companionExe = Join-Path $companionOutput 'OmoBenchCompanion.exe'
if (-not (Test-Path $companionExe)) { throw "The companion build produced no executable." }

# 2. Stamp the identity in. Generated, never committed.
$stamp = @"
// Generated by windows/installer/build.ps1. Do not commit.
namespace OmoBench.Installer
{
    internal static class BuildStamp
    {
        public const string Version = "$Version";
        public const string Revision = "$Revision";
        public const string BuiltAt = "$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))";
        public const bool Signed = $(if ($signed) { 'true' } else { 'false' });
        public const string SigningIdentity = "$SigningIdentity";
        public const bool SourceClean = $(if ($sourceClean) { 'true' } else { 'false' });
    }
}
"@
$stampPath = Join-Path $OutputDirectory 'BuildStamp.cs'
Set-Content -Path $stampPath -Value $stamp -Encoding utf8

# 3. The installer: its own sources, the companion's sources (it reuses the
#    existing discovery, validation and protocol registration), and the
#    companion executable embedded as a resource.
$installerSources = Get-ChildItem -Path (Join-Path $root 'src') -Filter *.cs | Sort-Object Name
$companionSources = Get-ChildItem -Path (Join-Path $companionRoot 'src') -Filter *.cs | Sort-Object Name
if (-not $installerSources) { throw "No installer sources found under $root\src." }

$references = @(
    '/reference:System.dll',
    '/reference:System.Core.dll',
    '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll',
    '/reference:System.Web.Extensions.dll'
)

$setupExe = Join-Path $OutputDirectory 'OmoBenchSetup.exe'
Write-Host "Compiling the installer -> $setupExe"
$arguments = @(
    '/nologo',
    '/target:winexe',
    '/platform:anycpu',
    '/langversion:5',
    '/warnaserror+',
    '/optimize+',
    '/main:OmoBench.Installer.InstallerProgram',
    "/resource:$companionExe,OmoBenchCompanion.exe",
    "/out:$setupExe"
) + $references + $stampPath +
    ($installerSources | ForEach-Object { $_.FullName }) +
    ($companionSources | ForEach-Object { $_.FullName })

& $csc @arguments
if ($LASTEXITCODE -ne 0) { throw "Installer build failed with exit code $LASTEXITCODE." }

$setupSize = (Get-Item $setupExe).Length
Write-Host "Built $setupExe ($([math]::Round($setupSize / 1KB, 1)) KB)"

# 4. Metadata and checksums, so a download can be verified.
$checksum = (Get-FileHash -Path $setupExe -Algorithm SHA256).Hash.ToLowerInvariant()
$companionChecksum = (Get-FileHash -Path $companionExe -Algorithm SHA256).Hash.ToLowerInvariant()

$metadata = [ordered]@{
    product            = 'OmoBench for Windows'
    version            = $Version
    revision           = $Revision
    built_at           = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    installer          = 'OmoBenchSetup.exe'
    installer_sha256   = $checksum
    installer_bytes    = $setupSize
    companion_sha256   = $companionChecksum
    signed             = $signed
    signing_identity   = $SigningIdentity
    # False when the working tree had uncommitted changes, so the revision
    # above describes the checkout but not necessarily this binary.
    source_clean       = $sourceClean
    # These checksums identify this particular build. The in-box compiler
    # stamps a fresh module id into every output, so rebuilding the same
    # revision produces a different hash: use them to confirm a download
    # matches the published asset, not to re-derive the binary from source.
    reproducible       = $false
}
$metadata | ConvertTo-Json | Set-Content -Path (Join-Path $OutputDirectory 'version.json') -Encoding utf8

"$checksum *OmoBenchSetup.exe" | Set-Content -Path (Join-Path $OutputDirectory 'SHA256SUMS') -Encoding utf8

Write-Host ""
Write-Host "Version  : $Version"
Write-Host "Revision : $Revision$(if (-not $sourceClean) { '  (working tree had uncommitted changes)' })"
Write-Host "SHA-256  : $checksum"
if ($signed) {
    Write-Host "Signing  : signed as $SigningIdentity"
} else {
    Write-Host "Signing  : NOT SIGNED. Windows will report an unknown publisher."
}

# 5. The installer's own tests.
if ($Test) {
    $testSources = Get-ChildItem -Path (Join-Path $root 'tests') -Filter *.cs | Sort-Object Name
    if (-not $testSources) { throw "No test sources found under $root\tests." }

    $testExe = Join-Path $OutputDirectory 'OmoBenchSetup.Tests.exe'
    Write-Host ""
    Write-Host "Compiling the installer test harness -> $testExe"
    $testArguments = @(
        '/nologo',
        '/target:exe',
        '/platform:anycpu',
        '/langversion:5',
        '/warnaserror+',
        '/main:OmoBench.Installer.Tests.InstallerTestProgram',
        "/out:$testExe"
    ) + $references + $stampPath +
        ($installerSources | ForEach-Object { $_.FullName }) +
        ($companionSources | ForEach-Object { $_.FullName }) +
        ($testSources | ForEach-Object { $_.FullName })

    & $csc @testArguments
    if ($LASTEXITCODE -ne 0) { throw "Installer test build failed with exit code $LASTEXITCODE." }

    Write-Host ''
    & $testExe
    if ($LASTEXITCODE -ne 0) { throw "Installer tests failed with exit code $LASTEXITCODE." }
}
