<#
.SYNOPSIS
    Build the runtime source archive the installer carries, from one git
    revision's committed bytes.

.DESCRIPTION
    The payload is the source a managed installation will run. It therefore
    has to be the source that was committed -- not the bytes that happen to be
    sitting in the build machine's working tree.

    That distinction is not academic. With core.autocrlf=true, git rewrites
    text files to CRLF on checkout, so a working-tree copy of a profile is a
    different file from the blob it came from. A result bundle records
    benchmark.profile_sha256 by hashing the profile it actually ran, and the
    validator compares that against the repository's committed profile. Ship a
    CRLF copy and every submission from that installation is rejected for a
    mismatch nobody can see. Sixteen of twenty-four payload files were
    affected on the machine where this was found.

    So nothing here reads the working tree. git lists the files in the
    revision, git exports their exact bytes, and each staged file's blob id is
    checked back against the revision before anything is packed. Bytes never
    pass through PowerShell text handling, which would reintroduce exactly the
    newline and encoding changes this exists to prevent.

    What is still checked, as before: the allowlist, the required paths, the
    file-count floor, the manifest, and the contents of the finished archive
    read back out of the archive itself.

.PARAMETER Repository
    The git repository to export from.

.PARAMETER Revision
    The revision whose committed bytes become the payload. This is the
    revision the installer stamps and reports.

.PARAMETER Destination
    Where to write the .tar.gz.

.PARAMETER StagingDirectory
    A scratch directory. Created, used and removed.

.PARAMETER AllowList
    Repository-relative paths (files or directories) the payload may contain.

.PARAMETER RequiredPathsFile
    A C# file carrying the required payload paths between "required-begin" and
    "required-end" markers -- in production, the installer's own
    RuntimeManifest.cs, so the build and the installer share one list.

.PARAMETER MinimumFiles
    Fewer files than this cannot be a working OmoBench.

.PARAMETER TarExecutable
    The tar to use. Defaults to the one in Windows. A test points this at a
    stub that does nothing, to prove a silently incomplete extraction is
    caught rather than packed.

.OUTPUTS
    A hashtable: Archive, Sha256, Bytes, FileCount, Paths, Revision.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$Revision,
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][string]$StagingDirectory,
    [Parameter(Mandatory = $true)][string[]]$AllowList,
    [Parameter(Mandatory = $true)][string]$RequiredPathsFile,
    [int]$MinimumFiles = 20,
    [string]$TarExecutable
)

$ErrorActionPreference = 'Stop'

if (-not $TarExecutable) {
    $TarExecutable = Join-Path $env:WINDIR 'System32\tar.exe'
}
if (-not (Test-Path -LiteralPath $TarExecutable)) {
    throw "tar.exe was not found at $TarExecutable; Windows 10 1803 or later is required to build."
}

# The paths a working installation actually reads, parsed from the installer's
# own source so there is exactly one list: the build checks the archive it
# produced against it, and the installer checks the unpacked copy against it at
# install time. A payload that satisfied one but not the other would defeat the
# point of both.
if (-not (Test-Path -LiteralPath $RequiredPathsFile)) {
    throw "Missing $RequiredPathsFile; cannot determine required payload paths."
}
$requiredPaths = @()
$insideRequired = $false
foreach ($line in (Get-Content -LiteralPath $RequiredPathsFile)) {
    if ($line -match 'required-begin') { $insideRequired = $true; continue }
    if ($line -match 'required-end') { $insideRequired = $false; continue }
    if ($insideRequired -and $line -match '"([^"]+)"') { $requiredPaths += $Matches[1] }
}
if ($requiredPaths.Count -lt 1) {
    throw ("No required payload paths were found in $RequiredPathsFile. " +
        "The build cannot validate a payload it has no expectations for.")
}

# The revision has to exist and name a commit. A build that silently fell back
# to "whatever is checked out" would be the original defect in another form.
$commit = (& git -C $Repository rev-parse --verify --quiet "$Revision^{commit}" 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $commit) {
    throw ("git could not resolve $Revision to a commit in $Repository. The runtime payload is " +
        "built from a committed revision, so there is nothing to build without one.")
}

# What that revision contains under the allowlist. ls-tree, not ls-files: the
# question is what was committed, not what the index or the working tree holds.
$tracked = @(& git -C $Repository -c core.quotePath=false ls-tree -r --name-only $commit -- $AllowList)
if ($LASTEXITCODE -ne 0) {
    throw "git ls-tree failed while listing the runtime payload for $commit."
}

# Even inside an allowed tree, some committed things are not runtime source.
$payloadRelative = @($tracked |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ } |
    Where-Object {
        $_ -notmatch '(^|/)tests/' -and
        $_ -notmatch '(^|/)__pycache__/' -and
        $_ -notmatch '\.egg-info/' -and
        $_ -notmatch '\.pyc$' -and
        $_ -notmatch '\.pyo$'
    } |
    Sort-Object)

$missingRequired = @($requiredPaths | Where-Object { $payloadRelative -notcontains $_ })
if ($missingRequired.Count -gt 0) {
    throw ("The runtime payload would be missing files OmoBench needs at run time:`n    " +
        ($missingRequired -join "`n    ") +
        "`nRefusing to build a payload that would fail at install time.")
}
if ($payloadRelative.Count -lt $MinimumFiles) {
    throw ("The runtime source archive would contain only $($payloadRelative.Count) files, which cannot " +
        "be a working OmoBench. Refusing to build a payload that would fail at install time.")
}

if (Test-Path -LiteralPath $StagingDirectory) {
    Remove-Item -LiteralPath $StagingDirectory -Recurse -Force
}
$payloadRoot = Join-Path $StagingDirectory 'omobench'
New-Item -ItemType Directory -Force -Path $payloadRoot | Out-Null

# git writes the blobs, tar unpacks them, and neither of them is PowerShell.
# -o, not a pipeline: piping a tar through PowerShell would decode it as text.
#
# The two -c settings are the point of this whole script. git archive runs its
# output through the working-tree conversion, so on a machine with
# core.autocrlf=true it hands out CRLF copies -- the very files this exists to
# avoid shipping. Overriding them for this one command asks for the blobs as
# they were committed, and changes nothing about the repository. The blob-id
# check below is what proves it worked, and would fail the build loudly if
# some other conversion (a .gitattributes eol rule, say) still applied.
$exportTar = Join-Path $StagingDirectory 'revision-export.tar'
& git -C $Repository -c core.autocrlf=false -c core.eol=lf archive --format=tar -o $exportTar $commit -- $payloadRelative
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exportTar)) {
    throw "git archive failed to export the runtime payload from $commit."
}
& $TarExecutable -xf $exportTar -C $payloadRoot
if ($LASTEXITCODE -ne 0) {
    throw "The exported runtime payload could not be unpacked into $payloadRoot."
}
Remove-Item -LiteralPath $exportTar -Force

# Exactly the files that were asked for: no fewer, in case the export or the
# unpacking stopped early and said nothing, and no more, in case a pathspec
# matched something unintended.
#
# The relative paths come from the provider rather than from arithmetic on the
# staging path. Taking Substring($payloadRoot.Length + 1) assumes the path
# handed in is spelled exactly as the provider spells it back, and that is not
# always true: on a GitHub Windows runner TEMP is the 8.3 form
# C:\Users\RUNNER~1\... while enumeration reports C:\Users\runneradmin\...,
# three characters longer, so every path came back with two characters of the
# staging directory still attached. Resolve-Path -Relative compares like with
# like, whatever either end is called.
$separator = [System.IO.Path]::DirectorySeparatorChar
Push-Location -LiteralPath $payloadRoot
try {
    $staged = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -Force -File |
        ForEach-Object {
            $relative = Resolve-Path -LiteralPath $_.FullName -Relative
            if ($relative.StartsWith('.' + $separator)) { $relative = $relative.Substring(2) }
            $relative.Replace($separator, '/')
        } |
        Sort-Object)
} finally {
    Pop-Location
}
$absent = @($payloadRelative | Where-Object { $staged -notcontains $_ })
$unexpected = @($staged | Where-Object { $payloadRelative -notcontains $_ })
if ($absent.Count -gt 0 -or $unexpected.Count -gt 0) {
    throw ("The exported runtime payload is not what was asked for." +
        $(if ($absent.Count -gt 0) { "`n  missing:`n    " + ($absent -join "`n    ") } else { '' }) +
        $(if ($unexpected.Count -gt 0) { "`n  unexpected:`n    " + ($unexpected -join "`n    ") } else { '' }))
}

# And every staged file is the revision's blob, byte for byte. hash-object
# --no-filters hashes what is on disk with no attribute or autocrlf handling,
# so this compares bytes rather than git's opinion of them.
foreach ($relative in $payloadRelative) {
    $stagedFile = Join-Path $payloadRoot $relative.Replace('/', $separator)
    $expected = (& git -C $Repository rev-parse --verify --quiet "${commit}:${relative}" 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $expected) {
        throw "git could not read the committed object for $relative at $commit."
    }
    $actual = (& git -C $Repository hash-object --no-filters -- $stagedFile | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $actual) {
        throw "git could not hash the staged copy of $relative."
    }
    if ($actual -ne $expected) {
        throw ("The staged copy of $relative is not the committed file.`n" +
            "    committed $expected`n    staged    $actual`n" +
            "The payload must carry the revision's own bytes.")
    }
}

# A manifest of what was staged, in sha256sum's format, so the installer can
# check the unpacked copy against it and a person can check it by hand.
# Written byte by byte with LF endings: a manifest that described its files
# correctly but varied with the build machine's newline settings would be an
# odd thing to ship alongside this.
$manifestLines = foreach ($relative in $payloadRelative) {
    $file = Join-Path $payloadRoot $relative.Replace('/', $separator)
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$manifestPath = Join-Path $payloadRoot 'MANIFEST.sha256'
[System.IO.File]::WriteAllText($manifestPath, (($manifestLines -join "`n") + "`n"),
    (New-Object System.Text.UTF8Encoding $false))

$payloadFiles = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -Force -File)
Write-Host ("Runtime source payload: {0} files from {1}, {2:N0} bytes" -f $payloadRelative.Count,
    $commit.Substring(0, [Math]::Min(12, $commit.Length)),
    (($payloadFiles | Measure-Object -Sum Length).Sum))

if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
& $TarExecutable -czf $Destination -C $StagingDirectory omobench
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination)) {
    throw "Could not create the runtime source archive."
}
Remove-Item -LiteralPath $StagingDirectory -Recurse -Force

# What the archive actually contains, read back out of the archive itself.
# Everything above describes intent; this is the only step that knows what was
# produced. An earlier version of this staged an empty tree and built a
# perfectly valid 332-byte archive of nothing.
$archiveEntries = @(& $TarExecutable -tzf $Destination)
if ($LASTEXITCODE -ne 0) { throw "The runtime source archive could not be read back." }
$archivePaths = @($archiveEntries |
    ForEach-Object { $_.Trim().Replace('\', '/') } |
    Where-Object { $_ -and -not $_.EndsWith('/') } |
    ForEach-Object { if ($_.StartsWith('omobench/')) { $_.Substring(9) } else { $_ } })

$archiveMissing = @(@($requiredPaths + 'MANIFEST.sha256') | Where-Object { $archivePaths -notcontains $_ })
if ($archiveMissing.Count -gt 0) {
    throw ("The runtime source archive that was just built is missing required files:`n    " +
        ($archiveMissing -join "`n    "))
}
if ($archivePaths.Count -ne ($payloadRelative.Count + 1)) {
    throw ("The archive holds $($archivePaths.Count) files but $($payloadRelative.Count + 1) were staged. " +
        "Refusing to embed an archive whose contents are not what was prepared.")
}
Write-Host "Runtime source archive contents verified: $($archivePaths.Count) files, including every required path."

@{
    Archive   = $Destination
    Sha256    = (Get-FileHash -Path $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
    Bytes     = (Get-Item $Destination).Length
    FileCount = $payloadRelative.Count
    Paths     = $payloadRelative
    Revision  = $commit
}
