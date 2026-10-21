<#
.SYNOPSIS
    Build the OmoBench Windows companion (and optionally run its tests).

.DESCRIPTION
    Compiles a single, self-contained Windows executable with the C# compiler
    that ships inside Windows itself (the in-box .NET Framework 4.x csc.exe).

    Why that compiler rather than the .NET SDK: every supported Windows 10/11
    install already has the .NET Framework runtime, so the result runs with no
    SDK on the build machine, no NuGet restore, no network access, and nothing
    for a user to install before double-clicking it. The output is ~70 KB.
    A later stage that needs signing, an installer or self-contained .NET can
    revisit this; the source is ordinary C# and does not depend on the choice.

.PARAMETER Test
    Also build and run the test harness (windows/companion/tests).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File windows\companion\build.ps1 -Test
#>
[CmdletBinding()]
param(
    [switch]$Test,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root 'build' }

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) {
    $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path $csc)) {
    throw "The in-box C# compiler was not found. Expected $csc (part of the .NET Framework that ships with Windows)."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$sources = Get-ChildItem -Path (Join-Path $root 'src') -Filter *.cs | Sort-Object Name
if (-not $sources) { throw "No sources found under $root\src." }

$references = @(
    '/reference:System.dll',
    '/reference:System.Core.dll',
    '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll',
    # JavaScriptSerializer, for parsing the panel's startup document and API
    # answers. Part of the in-box .NET Framework, so this adds no dependency
    # to install: it ships in the same place as the compiler above.
    '/reference:System.Web.Extensions.dll'
)

$exePath = Join-Path $OutputDirectory 'OmoBenchCompanion.exe'
Write-Host "Compiling $($sources.Count) source files -> $exePath"

$arguments = @(
    '/nologo',
    '/target:winexe',          # a GUI app: no console window ever appears
    '/platform:anycpu',
    '/langversion:5',
    '/warnaserror+',
    '/optimize+',
    "/out:$exePath"
) + $references + ($sources | ForEach-Object { $_.FullName })

& $csc @arguments
if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE." }
Write-Host "Built $exePath ($([math]::Round((Get-Item $exePath).Length / 1KB, 1)) KB)"

if ($Test) {
    $testSources = Get-ChildItem -Path (Join-Path $root 'tests') -Filter *.cs | Sort-Object Name
    if (-not $testSources) { throw "No test sources found under $root\tests." }

    # The harness links the same source files, so it tests the shipped code
    # rather than a copy.
    $testExe = Join-Path $OutputDirectory 'OmoBenchCompanion.Tests.exe'
    Write-Host "Compiling test harness -> $testExe"
    $testArguments = @(
        '/nologo',
        '/target:exe',          # console app: the harness prints its results
        '/platform:anycpu',
        '/langversion:5',
        '/warnaserror+',
        # Both the app and the harness define a Main; pick the harness's.
        '/main:OmoBench.Companion.Tests.TestProgram',
        "/out:$testExe"
    ) + $references + ($sources | ForEach-Object { $_.FullName }) + ($testSources | ForEach-Object { $_.FullName })

    & $csc @testArguments
    if ($LASTEXITCODE -ne 0) { throw "Test build failed with exit code $LASTEXITCODE." }

    Write-Host ''
    & $testExe
    if ($LASTEXITCODE -ne 0) { throw "Companion tests failed with exit code $LASTEXITCODE." }
}
