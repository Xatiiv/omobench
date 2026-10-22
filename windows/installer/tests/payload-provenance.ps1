<#
.SYNOPSIS
    Prove the runtime payload carries committed bytes, not working-tree bytes.

.DESCRIPTION
    Builds a throwaway git repository whose committed files are LF and whose
    working-tree copies have been rewritten to CRLF -- the state core.autocrlf
    leaves a Windows checkout in, and the state that made sixteen of this
    product's twenty-four payload files differ from the blobs they claimed to
    be. It then runs the real payload builder against it and checks every file
    in the resulting archive against the git object it came from.

    Nothing here touches the OmoBench checkout, its git settings or its files:
    the fixture repository is created under the temporary directory and
    removed afterwards.

.PARAMETER WorkDirectory
    Where to build the fixture. Defaults to a new directory under TEMP.
#>
[CmdletBinding()]
param([string]$WorkDirectory)

$ErrorActionPreference = 'Stop'

$testsRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$installerRoot = Split-Path -Parent $testsRoot
$builder = Join-Path $installerRoot 'build-payload.ps1'
if (-not (Test-Path -LiteralPath $builder)) { throw "Missing $builder." }

$script:passed = 0
$script:failures = New-Object System.Collections.Generic.List[string]

function Section([string]$title) {
    Write-Host ''
    Write-Host "== $title =="
}

function Check([string]$description, [bool]$condition) {
    if ($condition) {
        $script:passed++
        Write-Host "  ok    $description"
    } else {
        $script:failures.Add($description)
        Write-Host "  FAIL  $description"
    }
}

# A copy of the common arguments with one or two of them changed. Hashtable
# addition refuses duplicate keys, which is exactly what an override is.
function With([hashtable]$base, [hashtable]$overrides) {
    $copy = $base.Clone()
    foreach ($key in $overrides.Keys) { $copy[$key] = $overrides[$key] }
    return $copy
}

# Returns the error message if the builder refused, or $null if it did not.
function Refusal([hashtable]$arguments) {
    try {
        & $builder @arguments | Out-Null
        return $null
    } catch {
        return $_.Exception.Message
    }
}

function WriteLf([string]$path, [string]$text) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $false))
}

# Not named "Git": PowerShell resolves command names case-insensitively, so a
# function by that name would call itself instead of the program.
function RunGit([string]$repository, [string[]]$arguments) {
    # stderr is left alone on purpose: redirecting a native command's stderr in
    # Windows PowerShell turns ordinary git hints into terminating errors.
    $output = & git.exe -C $repository @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($arguments -join ' ') failed in $repository."
    }
    return $output
}

if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path $env:TEMP ("omobench-payload-test-" + [Guid]::NewGuid().ToString('N'))
}
$repository = Join-Path $WorkDirectory 'fixture-repo'
$output = Join-Path $WorkDirectory 'out'
$archive = Join-Path $output 'payload.tar.gz'
$staging = Join-Path $output 'staging'
$unpacked = Join-Path $WorkDirectory 'unpacked'
$tar = Join-Path $env:WINDIR 'System32\tar.exe'

try {
    New-Item -ItemType Directory -Force -Path $repository | Out-Null
    New-Item -ItemType Directory -Force -Path $output | Out-Null

    # -- a repository in the state a Windows checkout is actually in ---------

    # Committed with LF, as everything in this project is.
    $payloadFiles = [ordered]@{
        'cli/pyproject.toml'           = "[project]`nname = `"fixture`"`nversion = `"0.1.0`"`n"
        'cli/src/pkg/__init__.py'      = "__version__ = `"0.1.0`"`n"
        'cli/src/pkg/cli.py'           = "def main():`n    return 0`n"
        'cli/src/pkg/ui.py'            = "PORT = 8765`n`ndef serve():`n    return PORT`n"
        'cli/src/pkg/web/index.html'   = "<!doctype html>`n<title>fixture</title>`n"
        'cli/src/pkg/web/app.js'       = "export const ready = true;`n"
        'cli/src/pkg/web/style.css'    = "body { margin: 0; }`n"
        'profiles/p1.yaml'             = "id: p1`nacceptance:`n  minimum_successful_requests: 40`n"
        'profiles/p2.yaml'             = "id: p2`nacceptance:`n  minimum_successful_requests: 50`n"
        'profiles/p3.yaml'             = "id: p3`nacceptance:`n  minimum_successful_requests: 60`n"
        'tools/validate.py'            = "import sys`n`ndef main():`n    return 0`n"
        'results/README.md'            = "# results`n`nNothing submitted yet.`n"
    }
    foreach ($index in 1..10) {
        $payloadFiles["cli/src/pkg/mod$('{0:00}' -f $index).py"] = "VALUE = $index`n`ndef get():`n    return VALUE`n"
    }

    # Committed, inside allowed directories, but never runtime source.
    $excludedFiles = [ordered]@{
        'cli/src/pkg/tests/test_cli.py'                 = "def test_nothing():`n    assert True`n"
        'cli/src/pkg/__pycache__/cli.cpython-313.pyc'   = "not really bytecode`n"
        'cli/src/pkg/fixture.egg-info/PKG-INFO'         = "Metadata-Version: 2.1`n"
    }

    foreach ($relative in @($payloadFiles.Keys) + @($excludedFiles.Keys)) {
        $contents = if ($payloadFiles.Contains($relative)) { $payloadFiles[$relative] } else { $excludedFiles[$relative] }
        WriteLf (Join-Path $repository $relative.Replace('/', '\')) $contents
    }

    RunGit $repository @('-c', 'init.defaultBranch=main', 'init', '--quiet')
    RunGit $repository @('config', 'user.name', 'OmoBench payload test')
    RunGit $repository @('config', 'user.email', 'payload-test@localhost')
    # The setting that caused the defect, set here and nowhere else.
    RunGit $repository @('config', 'core.autocrlf', 'true')
    RunGit $repository @('add', '--all')
    RunGit $repository @('commit', '--quiet', '-m', 'fixture: committed with LF endings')
    $revision = (RunGit $repository @('rev-parse', 'HEAD') | Out-String).Trim()

    # Now the checkout as autocrlf leaves it: the same files, CRLF on disk, and
    # one with a byte-order mark for good measure. git still calls the tree
    # clean, because it normalises on the way back in -- which is exactly why
    # copying these bytes was never noticed.
    foreach ($relative in $payloadFiles.Keys) {
        $path = Join-Path $repository $relative.Replace('/', '\')
        $crlf = $payloadFiles[$relative].Replace("`n", "`r`n")
        [System.IO.File]::WriteAllText($path, $crlf, (New-Object System.Text.UTF8Encoding $false))
    }
    $bomFile = Join-Path $repository 'cli\src\pkg\web\index.html'
    [System.IO.File]::WriteAllText($bomFile,
        $payloadFiles['cli/src/pkg/web/index.html'].Replace("`n", "`r`n"),
        (New-Object System.Text.UTF8Encoding $true))

    # Something the person is working on, inside an allowed directory.
    WriteLf (Join-Path $repository 'cli\src\pkg\local_notes.py') "SECRET = 'not for release'`n"

    $requiredPathsFile = Join-Path $WorkDirectory 'RequiredPaths.cs'
    WriteLf $requiredPathsFile @'
// A stand-in for the installer's RuntimeManifest.cs.
// required-begin
"cli/pyproject.toml",
"cli/src/pkg/__init__.py",
"cli/src/pkg/cli.py",
"cli/src/pkg/web/index.html",
"profiles/p1.yaml",
"tools/validate.py",
// required-end
'@

    $allowList = @('cli/pyproject.toml', 'cli/src', 'profiles', 'tools/validate.py', 'results/README.md')
    $common = @{
        Repository        = $repository
        Revision          = $revision
        Destination       = $archive
        StagingDirectory  = $staging
        AllowList         = $allowList
        RequiredPathsFile = $requiredPathsFile
    }

    Section 'The fixture is in the state the defect needs'

    # git sees no change in the CRLF-rewritten files, because it normalises
    # them on the way back in. That is exactly why copying working-tree bytes
    # was never noticed: the checkout looks clean and is not the committed
    # bytes. (index.html also gained a byte-order mark, which is a real
    # content change, so git does report that one.)
    $changed = @((RunGit $repository @('diff', '--name-only')) |
        ForEach-Object { $_.Trim() } | Where-Object { $_ })
    Check 'git sees the CRLF-rewritten files as unchanged, as it did in the real repository' `
        (@($changed | Where-Object { $_ -ne 'cli/src/pkg/web/index.html' }).Count -eq 0)
    Check 'and it does notice the one file whose bytes really differ' `
        ($changed -contains 'cli/src/pkg/web/index.html')
    $worktreeBytes = [System.IO.File]::ReadAllBytes((Join-Path $repository 'profiles\p1.yaml'))
    Check 'and the working-tree copy of a profile really is CRLF' `
        ([System.Text.Encoding]::ASCII.GetString($worktreeBytes).Contains("`r`n"))

    # -- the real builder, against that repository ---------------------------

    Section 'Every payload file is the committed blob, byte for byte'

    $result = & $builder @common
    Check 'the payload builds' ($null -ne $result -and $result.FileCount -gt 0)
    Check "it carries the $($payloadFiles.Count) allowed files and nothing else" `
        ($result.FileCount -eq $payloadFiles.Count)

    if (Test-Path -LiteralPath $unpacked) { Remove-Item -LiteralPath $unpacked -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $unpacked | Out-Null
    & $tar -xzf $archive -C $unpacked --strip-components=1
    Check 'the archive unpacks' ($LASTEXITCODE -eq 0)

    $mismatched = New-Object System.Collections.Generic.List[string]
    $differentFromWorktree = 0
    foreach ($relative in $payloadFiles.Keys) {
        $extracted = Join-Path $unpacked $relative.Replace('/', '\')
        if (-not (Test-Path -LiteralPath $extracted)) { $mismatched.Add("$relative (absent)"); continue }
        $committed = (RunGit $repository @('rev-parse', "${revision}:${relative}") | Out-String).Trim()
        $actual = (RunGit $repository @('hash-object', '--no-filters', '--', $extracted) | Out-String).Trim()
        if ($actual -ne $committed) { $mismatched.Add("$relative ($actual vs $committed)") }

        $worktree = (Get-FileHash -LiteralPath (Join-Path $repository $relative.Replace('/', '\')) -Algorithm SHA256).Hash
        $shipped = (Get-FileHash -LiteralPath $extracted -Algorithm SHA256).Hash
        if ($worktree -ne $shipped) { $differentFromWorktree++ }
    }
    Check "all $($payloadFiles.Count) files match their committed blob: $($mismatched -join '; ')" `
        ($mismatched.Count -eq 0)
    Check "and $differentFromWorktree of them differ from the working-tree copy, so the trap was set" `
        ($differentFromWorktree -eq $payloadFiles.Count)

    $shippedIndex = [System.IO.File]::ReadAllBytes((Join-Path $unpacked 'cli\src\pkg\web\index.html'))
    Check 'no byte-order mark travelled with the file that had one on disk' `
        (-not ($shippedIndex.Length -ge 3 -and $shippedIndex[0] -eq 0xEF -and $shippedIndex[1] -eq 0xBB -and $shippedIndex[2] -eq 0xBF))
    Check 'and no CRLF survived into the archive' `
        (-not [System.Text.Encoding]::ASCII.GetString($shippedIndex).Contains("`r`n"))

    Section 'What the payload must not contain'

    Check 'an untracked file in an allowed directory is left behind' `
        (-not (Test-Path -LiteralPath (Join-Path $unpacked 'cli\src\pkg\local_notes.py')))
    foreach ($relative in $excludedFiles.Keys) {
        Check "committed but not runtime source: $relative" `
            (-not (Test-Path -LiteralPath (Join-Path $unpacked $relative.Replace('/', '\'))))
    }

    Section 'The manifest describes the bytes that shipped'

    $manifestPath = Join-Path $unpacked 'MANIFEST.sha256'
    Check 'the archive carries a manifest' (Test-Path -LiteralPath $manifestPath)
    $manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
    Check 'written with LF endings, whatever the build machine prefers' `
        (-not [System.Text.Encoding]::ASCII.GetString($manifestBytes).Contains("`r`n"))
    $entries = @([System.Text.Encoding]::UTF8.GetString($manifestBytes) -split "`n" |
        Where-Object { $_.Trim() })
    Check "it lists every payload file ($($entries.Count))" ($entries.Count -eq $payloadFiles.Count)
    $manifestWrong = 0
    foreach ($entry in $entries) {
        $parts = $entry.Trim() -split '\s+', 2
        $file = Join-Path $unpacked $parts[1].Trim().Replace('/', '\')
        if (-not (Test-Path -LiteralPath $file)) { $manifestWrong++; continue }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $parts[0]) {
            $manifestWrong++
        }
    }
    Check 'and every checksum in it is the checksum of the file that shipped' ($manifestWrong -eq 0)

    # -- nothing may produce a payload that looks fine but is not ------------

    Section 'A failure cannot look like a payload'

    Remove-Item -LiteralPath $archive -Force
    $unknown = Refusal (With $common @{ Revision = '0123456789abcdef0123456789abcdef01234567' })
    Check 'a revision that does not exist is refused' `
        ($null -ne $unknown -and $unknown -match 'could not resolve')
    Check '  and no archive is left behind' (-not (Test-Path -LiteralPath $archive))

    $notARepository = Join-Path $WorkDirectory 'not-a-repo'
    New-Item -ItemType Directory -Force -Path $notARepository | Out-Null
    $noGit = Refusal (With $common @{ Repository = $notARepository })
    Check 'a directory that is not a git repository is refused' ($null -ne $noGit)
    Check '  and no archive is left behind' (-not (Test-Path -LiteralPath $archive))

    $missingRequiredFile = Join-Path $WorkDirectory 'MissingRequired.cs'
    WriteLf $missingRequiredFile @'
// required-begin
"cli/pyproject.toml",
"cli/src/pkg/was_never_committed.py",
// required-end
'@
    $missing = Refusal (With $common @{ RequiredPathsFile = $missingRequiredFile })
    Check 'a revision missing a required file is refused' `
        ($null -ne $missing -and $missing -match 'was_never_committed')
    Check '  and no archive is left behind' (-not (Test-Path -LiteralPath $archive))

    $tooFew = Refusal (With $common @{ MinimumFiles = 500 })
    Check 'a payload too small to be OmoBench is refused' `
        ($null -ne $tooFew -and $tooFew -match 'cannot')
    Check '  and no archive is left behind' (-not (Test-Path -LiteralPath $archive))

    # A tar that reports success and unpacks nothing: the one failure that
    # would otherwise produce a perfectly valid archive of almost nothing.
    $stubTar = Join-Path $WorkDirectory 'stub-tar.cmd'
    WriteLf $stubTar "@exit /b 0`r`n"
    $silent = Refusal (With $common @{ TarExecutable = $stubTar })
    Check 'an extraction that silently does nothing is refused' `
        ($null -ne $silent -and $silent -match 'not what was asked for')
    Check '  and no archive is left behind' (-not (Test-Path -LiteralPath $archive))

    Section 'And it still builds after all that'

    $again = & $builder @common
    Check 'the payload builds again from the same revision' `
        ($null -ne $again -and $again.FileCount -eq $payloadFiles.Count)

    # Contents, not container. The .tar.gz records a modification time, so two
    # archives of identical files are not the same bytes; the manifest is a
    # checksum of every file in the payload, so comparing it compares what was
    # actually shipped.
    $secondUnpacked = Join-Path $WorkDirectory 'unpacked-again'
    New-Item -ItemType Directory -Force -Path $secondUnpacked | Out-Null
    & $tar -xzf $archive -C $secondUnpacked --strip-components=1
    $firstManifest = [System.IO.File]::ReadAllBytes($manifestPath)
    $secondManifest = [System.IO.File]::ReadAllBytes((Join-Path $secondUnpacked 'MANIFEST.sha256'))
    Check 'and the contents are identical to the first build, file for file' `
        (@(Compare-Object $firstManifest $secondManifest -SyncWindow 0).Count -eq 0)
}
finally {
    try { Remove-Item -LiteralPath $WorkDirectory -Recurse -Force -ErrorAction SilentlyContinue } catch { }
}

Write-Host ''
Write-Host "$script:passed passed, $($script:failures.Count) failed"
foreach ($failure in $script:failures) { Write-Host "  FAILED: $failure" }
if ($script:failures.Count -gt 0) { exit 1 }
exit 0
