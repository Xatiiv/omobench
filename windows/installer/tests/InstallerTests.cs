using System;
using System.Collections.Generic;
using System.IO;
using OmoBench.Companion;

namespace OmoBench.Installer.Tests
{
    /// <summary>
    /// Tests for the installer's decisions, run as a plain console program
    /// under the same constraints as the companion's own harness: no test
    /// framework, no package downloads, no network.
    ///
    /// Everything that touches the filesystem does so inside a throwaway
    /// directory through <see cref="InstallPaths.RootOverride"/>, so running
    /// these never installs anything, never writes to the Start menu, and
    /// never touches the real registry.
    /// </summary>
    internal static class InstallerTestProgram
    {
        private static int _passed;
        private static readonly List<string> Failures = new List<string>();

        private static int Main()
        {
            InstallPathTests();
            SupportDetectionTests();
            ManagedEnvironmentCommandTests();
            ManagedEnvironmentStageTests();
            BuildIdentityTests();

            Console.WriteLine();
            Console.WriteLine(_passed + " passed, " + Failures.Count + " failed");
            foreach (string failure in Failures) { Console.WriteLine("  FAILED: " + failure); }
            return Failures.Count == 0 ? 0 : 1;
        }

        // -- where things go ---------------------------------------------------

        private static void InstallPathTests()
        {
            Section("Install locations");

            Check("the program directory is per user, under local app data",
                InstallPaths.ProgramDirectory.IndexOf(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    StringComparison.OrdinalIgnoreCase) == 0);
            Check("it is not under Program Files",
                InstallPaths.ProgramDirectory.IndexOf("Program Files", StringComparison.OrdinalIgnoreCase) < 0);
            Check("the uninstall entry is per user",
                InstallPaths.UninstallKeyPath.StartsWith(@"Software\Microsoft\Windows", StringComparison.Ordinal));

            // The registration must name the installed copy, never wherever
            // the installer was run from.
            string command = ProtocolRegistration.BuildCommand(InstallPaths.CompanionPath);
            Check("the protocol command points at the installed companion",
                command.Contains(InstallPaths.ProgramDirectory));
            Check("it is not a Downloads or temporary path",
                command.IndexOf("\\Downloads\\", StringComparison.OrdinalIgnoreCase) < 0
                && command.IndexOf("\\Temp\\", StringComparison.OrdinalIgnoreCase) < 0);
            // Quoting is applied where it is needed: an install path under a
            // user name containing a space must still arrive as one argument.
            string spacedCommand = ProtocolRegistration.BuildCommand(
                @"C:\Users\Sam Smith\AppData\Local\Programs\OmoBench\OmoBenchCompanion.exe");
            Check("an installed path containing spaces is quoted, so it cannot split",
                spacedCommand.StartsWith("\"C:\\Users\\Sam Smith\\", StringComparison.Ordinal));
            Check("and the URI is still passed as its own argument",
                spacedCommand.EndsWith("\"%1\"", StringComparison.Ordinal));

            // Deletion is bounded by the program directory.
            Check("a path inside the program directory is recognised",
                InstallPaths.IsInsideProgramDirectory(InstallPaths.CompanionPath));
            Check("the user's documents are not inside it",
                !InstallPaths.IsInsideProgramDirectory(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments)));
            Check("a traversal out of it is refused",
                !InstallPaths.IsInsideProgramDirectory(
                    Path.Combine(InstallPaths.ProgramDirectory, "..", "..", "Something")));
            Check("a sibling with a shared prefix is not inside it",
                !InstallPaths.IsInsideProgramDirectory(InstallPaths.ProgramDirectory + "-old"));

            // Data that must outlive both upgrade and uninstall.
            string[] preserved = InstallPaths.PreservedDataLocations();
            Check("preserved data locations are listed", preserved.Length >= 2);
            foreach (string location in preserved)
            {
                Check("preserved data is outside the program directory: " + location,
                    !InstallPaths.IsInsideProgramDirectory(location));
            }

            // Install and uninstall against a throwaway root.
            string sandbox = Path.Combine(Path.GetTempPath(),
                "omobench-installer-test-" + Guid.NewGuid().ToString("N"));
            string savedRoot = InstallPaths.RootOverride;
            try
            {
                InstallPaths.RootOverride = sandbox;
                Check("the override redirects the program directory",
                    InstallPaths.ProgramDirectory.StartsWith(sandbox, StringComparison.OrdinalIgnoreCase));
                Check("and the Start menu location",
                    InstallPaths.StartMenuDirectory.StartsWith(sandbox, StringComparison.OrdinalIgnoreCase));
                Check("paths with spaces survive",
                    InstallPaths.IsInsideProgramDirectory(
                        Path.Combine(InstallPaths.ProgramDirectory, "a folder with spaces", "x.txt")));
            }
            finally
            {
                InstallPaths.RootOverride = savedRoot;
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
            }
        }

        // -- which machines are supported --------------------------------------

        private static void SupportDetectionTests()
        {
            Section("Supported machine detection");

            Func<int> build = SystemCheck.WindowsBuildProvider;
            Func<string> name = SystemCheck.WindowsNameProvider;
            Func<bool> bits = SystemCheck.Is64BitProvider;
            Func<bool> wsl = SystemCheck.WslPresentProvider;
            Func<List<WslDistribution>> distributions = SystemCheck.DistributionProvider;

            try
            {
                SystemCheck.WindowsNameProvider = () => "Windows 11 Pro 24H2";
                SystemCheck.Is64BitProvider = () => true;

                // 32-bit Windows cannot run the runner at all.
                SystemCheck.WindowsBuildProvider = () => 22631;
                SystemCheck.Is64BitProvider = () => false;
                SystemReport thirtyTwo = SystemCheck.Inspect();
                Check("32-bit Windows is unsupported, and says why",
                    thirtyTwo.Level == SupportLevel.Unsupported
                    && thirtyTwo.Explanation.Contains("64-bit"));

                // Too old for wsl --install.
                SystemCheck.Is64BitProvider = () => true;
                SystemCheck.WindowsBuildProvider = () => 18363;
                SystemReport old = SystemCheck.Inspect();
                Check("Windows older than 2004 is unsupported",
                    old.Level == SupportLevel.Unsupported);
                Check("and the explanation names the required build and what to do",
                    old.Explanation.Contains("19041") && old.Explanation.Contains("Windows Update"));

                // Supported, but WSL missing.
                SystemCheck.WindowsBuildProvider = () => 22631;
                SystemCheck.WslPresentProvider = () => false;
                SystemReport noWsl = SystemCheck.Inspect();
                Check("a supported machine without WSL needs setup, not rejection",
                    noWsl.Level == SupportLevel.NeedsSetup);
                Check("the guidance names the supported Microsoft command",
                    noWsl.Explanation.Contains("wsl --install"));
                Check("it warns about administrator rights and the restart",
                    noWsl.Explanation.Contains("administrator") && noWsl.Explanation.Contains("Restart"));
                Check("it explains the Linux account is separate from Windows",
                    noWsl.Explanation.Contains("not your Windows account"));
                Check("and it points at Microsoft's own instructions",
                    noWsl.Explanation.Contains("learn.microsoft.com"));

                // WSL present, no distribution.
                SystemCheck.WslPresentProvider = () => true;
                SystemCheck.DistributionProvider = () => new List<WslDistribution>();
                SystemReport noDistribution = SystemCheck.Inspect();
                Check("WSL without a distribution needs setup",
                    noDistribution.Level == SupportLevel.NeedsSetup
                    && noDistribution.Explanation.Contains("wsl --install -d Ubuntu"));

                // WSL1 only.
                SystemCheck.DistributionProvider = () => new List<WslDistribution>
                {
                    new WslDistribution { Name = "Legacy", Version = 1, IsDefault = true },
                };
                SystemReport wsl1 = SystemCheck.Inspect();
                Check("a WSL1-only machine is told how to convert",
                    wsl1.Level == SupportLevel.NeedsSetup
                    && wsl1.Explanation.Contains("--set-version"));

                // Ready.
                SystemCheck.DistributionProvider = () => new List<WslDistribution>
                {
                    new WslDistribution { Name = "Ubuntu", Version = 2, IsDefault = true },
                };
                SystemReport ready = SystemCheck.Inspect();
                Check("a machine with WSL2 is ready", ready.Level == SupportLevel.Ready);
                Check("and it is reported as having a WSL2 distribution", ready.HasWsl2Distribution);

                // An unreadable build number must not reject a good machine.
                SystemCheck.WindowsBuildProvider = () => 0;
                Check("an unknown Windows build is not treated as unsupported",
                    SystemCheck.Inspect().Level == SupportLevel.Ready);
            }
            finally
            {
                SystemCheck.WindowsBuildProvider = build;
                SystemCheck.WindowsNameProvider = name;
                SystemCheck.Is64BitProvider = bits;
                SystemCheck.WslPresentProvider = wsl;
                SystemCheck.DistributionProvider = distributions;
            }
        }

        // -- what the managed preparation would run ----------------------------

        private static void ManagedEnvironmentCommandTests()
        {
            Section("Managed environment: commands");

            string revision = "b81e907dec8398778cce46c225d4abb0695b1f80";

            List<string> download = ManagedEnvironment.BuildDownloadProgram(revision, "/home/sam/a.tar.gz");
            Check("the source is fetched at the pinned revision",
                string.Join(" ", download.ToArray()).Contains(revision));
            Check("it is fetched from the project's own repository",
                string.Join(" ", download.ToArray()).Contains("codeload.github.com/Xatiiv/omobench"));
            Check("curl is told to fail on an HTTP error rather than save the error page",
                download.Contains("--fail"));

            List<string> extract = ManagedEnvironment.BuildExtractProgram("/home/sam/a.tar.gz", "/mnt/c/x");
            Check("the archive is unpacked into the managed directory",
                extract.Contains("-C") && extract.Contains("/mnt/c/x"));

            List<string> venv = ManagedEnvironment.BuildCreateEnvironmentProgram(
                "/usr/bin/python3", "/home/sam/.local/share/omobench/managed-env");
            Check("the environment is created with the system python",
                venv[0] == "/usr/bin/python3" && venv.Contains("venv"));

            List<string> install = ManagedEnvironment.BuildInstallProgram(
                "/home/sam/.local/share/omobench/managed-env/bin/python", "/mnt/c/x");
            Check("OmoBench is installed into the managed environment, not the system one",
                install[0] == "/home/sam/.local/share/omobench/managed-env/bin/python");
            Check("it is installed from the managed source with the pinned runner extra",
                string.Join(" ", install.ToArray()).Contains("/mnt/c/x/cli[ui,runner]"));
            Check("pip is not allowed to prompt",
                install.Contains("--no-input"));

            // No shell, anywhere -- the same rule the companion follows.
            foreach (List<string> program in new[] { download, extract, venv, install })
            {
                string arguments = Wsl.BuildRunArguments("Ubuntu", null, program);
                Check("runs directly with no shell: " + program[0],
                    arguments.Contains(" --exec ") && !arguments.Contains("bash")
                    && !arguments.Contains("sh -c"));
            }

            string spaced = Wsl.BuildRunArguments("My Distro", null,
                ManagedEnvironment.BuildExtractProgram("/home/a b/x.tar.gz", "/mnt/c/My Files/omobench"));
            Check("paths with spaces are quoted rather than split",
                spaced.Contains("\"/home/a b/x.tar.gz\"") && spaced.Contains("\"/mnt/c/My Files/omobench\""));

            // Managed locations are the installer's own, never a user's.
            string source = ManagedEnvironment.SourceDirectory(revision);
            Check("the managed source is under this user's local app data",
                source.IndexOf(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    StringComparison.OrdinalIgnoreCase) == 0);
            Check("and is named by the revision, so builds do not collide",
                source.Contains(revision));
            Check("a revision with odd characters cannot escape the directory",
                !ManagedEnvironment.SourceDirectory("../../../etc").Contains(".."));
            Check("the managed environment is inside Linux, not on /mnt",
                ManagedEnvironment.EnvironmentDirectory("/home/sam").StartsWith("/home/sam/",
                    StringComparison.Ordinal));
            Check("the managed interpreter keeps its own venv path",
                ManagedEnvironment.EnvironmentInterpreter("/home/sam").EndsWith(
                    "managed-env/bin/python", StringComparison.Ordinal));
        }

        // -- how the managed preparation behaves -------------------------------

        /// <summary>A distribution that answers the bootstrap's commands.</summary>
        private sealed class FakeWsl
        {
            public readonly List<string> Commands = new List<string>();
            public string Home = "/home/sam";
            public string SystemPython = "/usr/bin/python3";
            public string FailOn;
            public bool Cancelled;
            public DiscoveryCancel Cancel;

            public ProcessResult Run(string arguments, int timeout, DiscoveryCancel cancel)
            {
                Commands.Add(arguments);
                if (Cancelled && Cancel != null) { Cancel.Cancel(); }
                if (cancel != null && cancel.IsCancelled)
                {
                    return new ProcessResult { ExitCode = -1, TimedOut = true };
                }
                if (arguments.Contains(" printenv HOME"))
                {
                    return Home == null ? Bad("no home") : Ok(Home);
                }
                if (arguments.Contains(" which python3"))
                {
                    return SystemPython == null ? Bad("not found") : Ok(SystemPython);
                }
                if (FailOn != null && arguments.Contains(FailOn)) { return Bad("simulated failure"); }
                return Ok("");
            }

            private static ProcessResult Ok(string output)
            {
                return new ProcessResult { ExitCode = 0, StandardOutput = output + "\n" };
            }

            private static ProcessResult Bad(string error)
            {
                return new ProcessResult { ExitCode = 1, StandardError = error };
            }
        }

        private static void ManagedEnvironmentStageTests()
        {
            Section("Managed environment: stages, failure and cancellation");

            Func<string, int, DiscoveryCancel, ProcessResult> savedRunner = ManagedEnvironment.WslRunner;
            Func<Configuration, EnvironmentCheckResult> savedValidator = ManagedEnvironment.Validator;
            string savedSourceRoot = ManagedEnvironment.SourceRootOverride;

            // Preparation really creates its source directory, so point it at
            // a throwaway location: running the tests must not leave anything
            // in the user's own profile.
            string sandbox = Path.Combine(Path.GetTempPath(),
                "omobench-managed-test-" + Guid.NewGuid().ToString("N"));
            ManagedEnvironment.SourceRootOverride = sandbox;

            BuildInfo build = new BuildInfo
            {
                Version = "0.1.0",
                Revision = "b81e907dec8398778cce46c225d4abb0695b1f80",
                BuiltAt = "2026-10-21T00:00:00Z",
            };

            try
            {
                // The happy path, with the real check standing in as passing.
                FakeWsl good = new FakeWsl();
                ManagedEnvironment.WslRunner = good.Run;
                ManagedEnvironment.Validator = configuration => new EnvironmentCheckResult
                {
                    Ok = true,
                    Summary = "Ready: OmoBench imports from the selected checkout.",
                };
                PreparationResult prepared = ManagedEnvironment.Prepare("Ubuntu", build, null, null);
                Check("a complete preparation reports ready", prepared.Ok);
                Check("it produced a configuration for the managed pair",
                    prepared.Configuration != null
                    && prepared.Configuration.Distribution == "Ubuntu"
                    && prepared.Configuration.PythonWslPath.Contains("managed-env"));
                Check("every stage is accounted for", prepared.Stages.Count == 5);
                foreach (PreparationStage stage in prepared.Stages)
                {
                    Check("stage finished: " + stage.Name, stage.State == StageState.Done);
                }

                // The check at the end is what decides -- not the steps above it.
                ManagedEnvironment.Validator = configuration => new EnvironmentCheckResult
                {
                    Ok = false,
                    Summary = "That interpreter imports OmoBench from a different checkout.",
                    Detail = "...",
                };
                PreparationResult unverified = ManagedEnvironment.Prepare("Ubuntu", build, null, null);
                Check("every install step can succeed and the result still not be ready",
                    !unverified.Ok);
                Check("and the failure is the check's own words",
                    (unverified.Summary ?? "").Contains("different checkout"));
                Check("the verify stage is the one marked failed",
                    unverified.Stages[4].State == StageState.Failed);

                ManagedEnvironment.Validator = configuration => new EnvironmentCheckResult { Ok = true, Summary = "ok" };

                // A missing python3 is explained rather than retried blindly.
                FakeWsl noPython = new FakeWsl { SystemPython = null };
                ManagedEnvironment.WslRunner = noPython.Run;
                PreparationResult missingPython = ManagedEnvironment.Prepare("Ubuntu", build, null, null);
                Check("a distribution without python3 fails at the first stage",
                    !missingPython.Ok && missingPython.Stages[0].State == StageState.Failed);
                Check("and the fix names the package and the password it needs",
                    (missingPython.Detail ?? "").Contains("python3-venv")
                    && (missingPython.Detail ?? "").Contains("Linux password"));

                // A failed venv points at the usual Ubuntu cause.
                FakeWsl noVenv = new FakeWsl { FailOn = "-m venv" };
                ManagedEnvironment.WslRunner = noVenv.Run;
                PreparationResult venvFailed = ManagedEnvironment.Prepare("Ubuntu", build, null, null);
                Check("a failed environment creation stops at that stage",
                    !venvFailed.Ok && venvFailed.Stages[2].State == StageState.Failed);
                Check("and explains the python3-venv package",
                    (venvFailed.Detail ?? "").Contains("python3-venv"));
                Check("nothing was installed afterwards",
                    venvFailed.Stages[3].State == StageState.Pending);

                // A failed download is reported with the revision.
                FakeWsl offline = new FakeWsl { FailOn = "curl" };
                ManagedEnvironment.WslRunner = offline.Run;
                PreparationResult offlineResult = ManagedEnvironment.Prepare("Ubuntu", build, null, null);
                Check("a failed download stops at the fetch stage",
                    !offlineResult.Ok && offlineResult.Stages[1].State == StageState.Failed);
                Check("and names the revision it was trying to fetch",
                    (offlineResult.Detail ?? "").Contains(build.ShortRevision));

                // Cancellation stops promptly and says nothing more changed.
                DiscoveryCancel cancel = new DiscoveryCancel();
                FakeWsl stopping = new FakeWsl { Cancelled = true, Cancel = cancel };
                ManagedEnvironment.WslRunner = stopping.Run;
                PreparationResult cancelled = ManagedEnvironment.Prepare("Ubuntu", build, cancel, null);
                Check("cancelling stops the preparation", cancelled.Cancelled && !cancelled.Ok);
                Check("and says nothing further was changed",
                    (cancelled.Summary ?? "").Contains("Nothing further was changed"));

                // An installer with no revision cannot fetch pinned sources.
                ManagedEnvironment.WslRunner = new FakeWsl().Run;
                PreparationResult unstamped = ManagedEnvironment.Prepare(
                    "Ubuntu", new BuildInfo { Version = "0.0.0", Revision = "" }, null, null);
                Check("an unstamped build refuses rather than guessing a revision",
                    !unstamped.Ok && (unstamped.Summary ?? "").Contains("revision"));

                // Progress is reported as it goes.
                int updates = 0;
                ManagedEnvironment.WslRunner = new FakeWsl().Run;
                ManagedEnvironment.Prepare("Ubuntu", build, null, snapshot => updates++);
                Check("progress is reported stage by stage", updates >= 5);
            }
            finally
            {
                ManagedEnvironment.WslRunner = savedRunner;
                ManagedEnvironment.Validator = savedValidator;
                ManagedEnvironment.SourceRootOverride = savedSourceRoot;
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
            }
        }

        // -- identity and honesty about signing ---------------------------------

        private static void BuildIdentityTests()
        {
            Section("Build identity");

            BuildInfo current = BuildInfo.Current();
            Check("the build knows its version", !string.IsNullOrEmpty(current.Version));
            Check("the build describes itself", current.Describe().Contains("Version"));

            BuildInfo unsigned = new BuildInfo { Version = "0.1.0", Revision = "abc", Signed = false };
            Check("an unsigned build says so plainly",
                unsigned.SigningStatement().Contains("not signed"));
            Check("and warns about the unknown-publisher prompt",
                unsigned.SigningStatement().Contains("unknown publisher"));
            Check("and points at the checksum instead",
                unsigned.SigningStatement().Contains("SHA-256"));
            Check("an unsigned build claims no verified publisher",
                BuildInfo.PublisherDisplayName.Contains("unsigned")
                    || BuildStamp.Signed);

            BuildInfo signed = new BuildInfo { Version = "0.1.0", Signed = true };
            Check("a signed build reports the identity it was signed with",
                signed.SigningStatement().Contains("signed as"));

            Check("the revision is shortened for display, not invented",
                new BuildInfo { Revision = "b81e907dec8398778cce46c225d4abb0695b1f80" }
                    .ShortRevision == "b81e907dec83");
            Check("an unknown revision stays empty rather than becoming a guess",
                new BuildInfo { Revision = null }.ShortRevision == "");
        }

        // -- harness -------------------------------------------------------------

        private static void Section(string title)
        {
            Console.WriteLine();
            Console.WriteLine("== " + title + " ==");
        }

        private static void Check(string description, bool condition)
        {
            if (condition)
            {
                _passed++;
                Console.WriteLine("  ok    " + description);
            }
            else
            {
                Failures.Add(description);
                Console.WriteLine("  FAIL  " + description);
            }
        }
    }
}
