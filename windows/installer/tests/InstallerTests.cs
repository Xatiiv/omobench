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
            ReviewRegressionTests();
            RuntimePayloadTests();
            UninstallCompletionTests();
            DelayedDiscoveryTests();
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

            // The source is no longer fetched at install time; it travels in
            // the installer and is checked before use.
            Check("the managed source directory is named by the pinned revision",
                ManagedEnvironment.SourceDirectory(revision).Contains(revision));

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
            foreach (List<string> program in new[] { extract, venv, install })
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

                // A failed unpack of the bundled source is reported there.
                FakeWsl unpackFails = new FakeWsl { FailOn = "tar" };
                ManagedEnvironment.WslRunner = unpackFails.Run;
                PreparationResult unpackResult = ManagedEnvironment.Prepare("Ubuntu", build, null, null);
                Check("a failed unpack stops at the source stage",
                    !unpackResult.Ok && unpackResult.Stages[1].State == StageState.Failed);
                Check("and says the bundled source could not be unpacked",
                    (unpackResult.Summary ?? "").Contains("could not be unpacked"));

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

        // -- review findings: upgrade safety, redirection, ownership ------------

        private static void ReviewRegressionTests()
        {
            Section("Review findings");

            // 1. Upgrade coordination never terminates anything.
            Check("with no installed companion running, closing trivially succeeds",
                Installation.TryCloseInstalledCompanions(0));
            Check("only companions running from the install location are considered",
                Installation.RunningInstalledCompanions().Count == 0);

            // 2. Uninstall removes what it owns, through paths that no shell
            //    ever sees -- including names a shell would have mangled.
            string savedRoot = InstallPaths.RootOverride;
            string awkward = Path.Combine(Path.GetTempPath(),
                "omo & bench %PATH% test " + Guid.NewGuid().ToString("N"));
            try
            {
                InstallPaths.RootOverride = awkward;
                Directory.CreateDirectory(InstallPaths.ProgramDirectory);
                string planted = Path.Combine(InstallPaths.ProgramDirectory, "payload & data %TEMP%.txt");
                File.WriteAllText(planted, "x");
                Check("a path containing & and % is still inside the program directory",
                    InstallPaths.IsInsideProgramDirectory(planted));

                InstallOutcome removal = Installation.Uninstall();
                Check("uninstall reports success", removal.Ok);
                Check("and the awkward file is gone", !File.Exists(planted));
                Check("the summary does not claim data was removed",
                    removal.Describe().Contains("were not touched"));
            }
            finally
            {
                InstallPaths.RootOverride = savedRoot;
                try { Directory.Delete(awkward, true); } catch (Exception) { }
            }

            // 3. Every managed destination is redirectable.
            string savedSource = ManagedEnvironment.SourceRootOverride;
            string savedEnvironment = ManagedEnvironment.EnvironmentRootOverride;
            string savedArchive = ManagedEnvironment.ArchiveDirectoryOverride;
            try
            {
                ManagedEnvironment.EnvironmentRootOverride = "/tmp/omobench-test-env";
                ManagedEnvironment.ArchiveDirectoryOverride = "/tmp/omobench-test-dl";
                Check("the Linux environment can be redirected",
                    ManagedEnvironment.EnvironmentDirectory("/home/sam")
                        == "/tmp/omobench-test-env/managed-env");
                Check("so can the interpreter path inside it",
                    ManagedEnvironment.EnvironmentInterpreter("/home/sam")
                        == "/tmp/omobench-test-env/managed-env/bin/python");
                Check("and where the bundled archive is written",
                    ManagedEnvironment.ArchiveWindowsPath()
                        == Path.Combine("/tmp/omobench-test-dl", "omobench-runtime-source.tar.gz"));

                ManagedEnvironment.EnvironmentRootOverride = null;
                ManagedEnvironment.ArchiveDirectoryOverride = null;
                Check("without redirection the environment is under the Linux home",
                    ManagedEnvironment.EnvironmentDirectory("/home/sam")
                        == "/home/sam/.local/share/omobench/managed-env");
                Check("and the archive defaults to a temporary Windows location",
                    ManagedEnvironment.ArchiveWindowsPath().StartsWith(
                        Path.GetTempPath(), StringComparison.OrdinalIgnoreCase));
            }
            finally
            {
                ManagedEnvironment.SourceRootOverride = savedSource;
                ManagedEnvironment.EnvironmentRootOverride = savedEnvironment;
                ManagedEnvironment.ArchiveDirectoryOverride = savedArchive;
            }

            // 4. Ownership: never write over somebody else's directory.
            string sandbox = Path.Combine(Path.GetTempPath(),
                "omobench-ownership-" + Guid.NewGuid().ToString("N"));
            try
            {
                string fresh = Path.Combine(sandbox, "fresh");
                Check("a directory that does not exist yet is claimed",
                    ManagedEnvironment.ClaimDirectory(fresh) == null);
                Check("and is marked as ours",
                    File.Exists(Path.Combine(fresh, ManagedEnvironment.OwnershipMarkerName)));
                Check("claiming it again is fine",
                    ManagedEnvironment.ClaimDirectory(fresh) == null);

                string empty = Path.Combine(sandbox, "empty");
                Directory.CreateDirectory(empty);
                Check("an empty directory is claimed", ManagedEnvironment.ClaimDirectory(empty) == null);

                string occupied = Path.Combine(sandbox, "someone-elses-work");
                Directory.CreateDirectory(occupied);
                File.WriteAllText(Path.Combine(occupied, "thesis.txt"), "do not delete");
                string refusal = ManagedEnvironment.ClaimDirectory(occupied);
                Check("a directory with somebody's files in it is refused", refusal != null);
                Check("the refusal names the folder and says what to do",
                    refusal != null && refusal.Contains(occupied) && refusal.Contains("will not be written over"));
                Check("and their file is untouched",
                    File.Exists(Path.Combine(occupied, "thesis.txt")));
            }
            finally
            {
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
            }

            // 5. Configuration can be written somewhere harmless in a test.
            string savedConfigPath = Configuration.FilePathOverride;
            string configSandbox = Path.Combine(Path.GetTempPath(),
                "omobench-config-" + Guid.NewGuid().ToString("N"));
            try
            {
                string target = Path.Combine(configSandbox, "companion.config");
                Configuration.FilePathOverride = target;
                Check("the configuration destination is redirectable",
                    Configuration.FilePath == target);
                new Configuration
                {
                    Distribution = "Ubuntu",
                    CheckoutWindowsPath = @"C:\somewhere\omobench",
                    PythonWslPath = "/home/sam/.venvs/x/bin/python",
                    Port = 9123,
                }.Save();
                Check("saving writes to the redirected location", File.Exists(target));
                Configuration reloaded = Configuration.Load();
                Check("a custom port survives a save and load", reloaded.Port == 9123);
            }
            finally
            {
                Configuration.FilePathOverride = savedConfigPath;
                try { Directory.Delete(configSandbox, true); } catch (Exception) { }
            }

            // 6. Cancellation arriving during final validation must not be
            //    reported as success.
            Func<string, int, DiscoveryCancel, ProcessResult> runner = ManagedEnvironment.WslRunner;
            Func<Configuration, EnvironmentCheckResult> validator = ManagedEnvironment.Validator;
            string sourceRoot = ManagedEnvironment.SourceRootOverride;
            string lateSandbox = Path.Combine(Path.GetTempPath(),
                "omobench-late-cancel-" + Guid.NewGuid().ToString("N"));
            try
            {
                ManagedEnvironment.SourceRootOverride = lateSandbox;
                FakeWsl fake = new FakeWsl();
                ManagedEnvironment.WslRunner = fake.Run;
                DiscoveryCancel cancel = new DiscoveryCancel();
                // The check passes, but the person cancelled while it ran.
                ManagedEnvironment.Validator = configuration =>
                {
                    cancel.Cancel();
                    return new EnvironmentCheckResult { Ok = true, Summary = "ok" };
                };
                PreparationResult late = ManagedEnvironment.Prepare(
                    "Ubuntu",
                    new BuildInfo { Version = "0.1.0", Revision = "b81e907dec8398778cce46c225d4abb0695b1f80" },
                    cancel, null);
                Check("a cancellation during validation is not reported as ready",
                    !late.Ok && late.Cancelled);
                Check("and no configuration is handed back to be saved",
                    late.Configuration == null);
            }
            finally
            {
                ManagedEnvironment.WslRunner = runner;
                ManagedEnvironment.Validator = validator;
                ManagedEnvironment.SourceRootOverride = sourceRoot;
                try { Directory.Delete(lateSandbox, true); } catch (Exception) { }
            }
        }

        // -- the embedded runtime payload ---------------------------------------

        private static void RuntimePayloadTests()
        {
            Section("Embedded runtime source");

            Func<System.IO.Stream> opener = RuntimePayload.Opener;
            string expected = RuntimePayload.ExpectedSha256;
            long bytes = RuntimePayload.ExpectedBytes;
            string sandbox = Path.Combine(Path.GetTempPath(), "omobench-payload-" + Guid.NewGuid().ToString("N"));

            try
            {
                string target = Path.Combine(sandbox, "payload.tar.gz");
                byte[] good = System.Text.Encoding.UTF8.GetBytes("pretend archive contents");
                string goodHash = RuntimePayload.Sha256(good);

                // The real thing: this build carries an archive and knows its
                // checksum, so the two must agree.
                string failure;
                bool wrote = RuntimePayload.TryWrite(Path.Combine(sandbox, "real.tar.gz"), out failure);
                Check("this installer carries a verifiable runtime source: " + (failure ?? "ok"), wrote);
                Check("and the build recorded its checksum",
                    !string.IsNullOrEmpty(BuildStamp.RuntimeSourceSha256));
                Check("and its size", BuildStamp.RuntimeSourceBytes > 0);

                // Missing payload.
                RuntimePayload.Opener = () => null;
                Check("a missing payload is refused, not treated as empty",
                    !RuntimePayload.TryWrite(target, out failure)
                    && failure.Contains("does not carry"));

                // Empty payload.
                RuntimePayload.Opener = () => new System.IO.MemoryStream(new byte[0]);
                Check("an empty payload is refused",
                    !RuntimePayload.TryWrite(target, out failure) && failure.Contains("empty"));

                // Corrupted payload: right length, wrong contents.
                byte[] corrupted = (byte[])good.Clone();
                corrupted[0] ^= 0xFF;
                RuntimePayload.Opener = () => new System.IO.MemoryStream(corrupted);
                RuntimePayload.ExpectedSha256 = goodHash;
                RuntimePayload.ExpectedBytes = good.Length;
                Check("a corrupted payload fails the checksum",
                    !RuntimePayload.TryWrite(target, out failure) && failure.Contains("checksum"));
                Check("and nothing was written for something else to unpack",
                    !File.Exists(target));

                // Truncated payload.
                RuntimePayload.Opener = () => new System.IO.MemoryStream(good, 0, good.Length - 3);
                Check("a truncated payload is refused on length",
                    !RuntimePayload.TryWrite(target, out failure) && failure.Contains("bytes"));

                // The honest one.
                RuntimePayload.Opener = () => new System.IO.MemoryStream(good);
                Check("a payload matching the recorded checksum is written",
                    RuntimePayload.TryWrite(target, out failure) && File.Exists(target));

                // A build with no recorded checksum cannot verify anything.
                RuntimePayload.ExpectedSha256 = "";
                Check("a payload with no recorded checksum is refused rather than trusted",
                    !RuntimePayload.TryWrite(target, out failure) && failure.Contains("cannot be verified"));
            }
            finally
            {
                RuntimePayload.Opener = opener;
                RuntimePayload.ExpectedSha256 = expected;
                RuntimePayload.ExpectedBytes = bytes;
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
            }

            // Revision consistency: the companion payload and the runtime
            // source are stamped by one build, so the managed source directory
            // is named by the same revision the installer reports.
            BuildInfo build = BuildInfo.Current();
            Check("the installer and its runtime payload name one revision",
                ManagedEnvironment.SourceDirectory(build.Revision).Contains(build.Revision)
                || string.IsNullOrEmpty(build.Revision));
            Check("no anonymous download remains in the preparation",
                !HasMember(typeof(ManagedEnvironment), "BuildDownloadProgram"));
        }

        private static bool HasMember(Type type, string name)
        {
            return type.GetMethod(name, System.Reflection.BindingFlags.Public
                | System.Reflection.BindingFlags.Static) != null;
        }

        // -- uninstall completes without elevation or a restart ------------------

        private static void UninstallCompletionTests()
        {
            Section("Uninstall completion");

            string savedRoot = InstallPaths.RootOverride;
            // Ordinary, and the characters a shell would have mangled.
            string[] roots =
            {
                "omobench-plain-",
                "omo bench spaces-",
                "omo & bench-",
                "omo %PATH% bench-",
                "omo & %TEMP% bench with spaces-",
            };

            foreach (string prefix in roots)
            {
                string root = Path.Combine(Path.GetTempPath(), prefix + Guid.NewGuid().ToString("N"));
                try
                {
                    InstallPaths.RootOverride = root;
                    Directory.CreateDirectory(InstallPaths.ProgramDirectory);
                    File.WriteAllText(InstallPaths.CompanionPath, "companion");
                    File.WriteAllText(InstallPaths.ManifestPath, "{}");
                    File.WriteAllText(Path.Combine(InstallPaths.ProgramDirectory, "extra & file %X%.txt"), "x");

                    InstallOutcome outcome = Installation.Uninstall();
                    Check("uninstall completes under " + prefix.TrimEnd('-'), outcome.Ok);
                    Check("  and the program directory is gone",
                        !Directory.Exists(InstallPaths.ProgramDirectory));
                    Check("  without needing a restart or elevation",
                        !outcome.Describe().Contains("restart")
                        && !outcome.Describe().Contains("administrator"));
                }
                finally
                {
                    InstallPaths.RootOverride = savedRoot;
                    try { Directory.Delete(root, true); } catch (Exception) { }
                }
            }

            // The relaunch only ever applies to a program inside the
            // installation it is removing.
            Check("an uninstaller running from outside the installation does not relaunch",
                !Installation.TryRelaunchForUninstall(
                    Path.Combine(Path.GetTempPath(), "SomewhereElse.exe"), true));
            Check("and neither does an empty path",
                !Installation.TryRelaunchForUninstall("", true));
        }

        // -- a deliberately slow search, cancelled on purpose --------------------

        /// <summary>
        /// Cancellation and stale-result handling, driven by a fixture that
        /// controls how long each probe takes.
        ///
        /// The timing is the fixture's, not the machine's: a test that waited
        /// for a real cold search would be slow, flaky, and would prove
        /// nothing on a fast machine.
        /// </summary>
        private static void DelayedDiscoveryTests()
        {
            Section("Delayed discovery: cancellation and stale results");

            Func<string, int, DiscoveryCancel, ProcessResult> savedRunner = Discovery.WslRunner;
            Func<List<WslDistribution>> savedLister = Discovery.DistributionLister;
            Func<bool> savedAvailable = Discovery.WslAvailable;
            Func<List<string>> savedRoots = Discovery.SearchRootProvider;

            try
            {
                Discovery.WslAvailable = () => true;
                Discovery.DistributionLister = () => new List<WslDistribution>
                {
                    new WslDistribution { Name = "Ubuntu", Version = 2, IsDefault = true },
                };
                Discovery.SearchRootProvider = () => new List<string>();

                DiscoveryCancel cancel = new DiscoveryCancel();
                int probes = 0;

                // Each call takes a controlled 400ms, and honours the token the
                // way the real runner does.
                Discovery.WslRunner = (arguments, timeout, token) =>
                {
                    probes++;
                    for (int slice = 0; slice < 8; slice++)
                    {
                        if (token != null && token.IsCancelled)
                        {
                            return new ProcessResult { ExitCode = -1, TimedOut = true };
                        }
                        System.Threading.Thread.Sleep(50);
                    }
                    return new ProcessResult { ExitCode = 0, StandardOutput = "/home/sam\n" };
                };

                // Cancel shortly after starting, from another thread, exactly
                // as the Stop button does.
                System.Threading.Thread canceller = new System.Threading.Thread(() =>
                {
                    System.Threading.Thread.Sleep(250);
                    cancel.Cancel();
                });
                canceller.IsBackground = true;

                DateTime started = DateTime.UtcNow;
                canceller.Start();
                DiscoveryReport report = Discovery.Run(
                    new Configuration(), null, new DiscoveryLimits(), cancel, null);
                TimeSpan elapsed = DateTime.UtcNow - started;

                Check("a cancelled search reports itself cancelled", report.Cancelled);
                Check("it stops promptly rather than running every probe to completion: "
                    + ((int)elapsed.TotalMilliseconds) + "ms",
                    elapsed < TimeSpan.FromSeconds(5));
                Check("and it found nothing to act on", report.Ready.Count == 0);

                // Stale-result protection: the window's own state machine is
                // what decides, and a result from a superseded search changes
                // nothing. (The companion harness covers this in depth.)
                SetupState state = new SetupState();
                int first = state.BeginSearch();
                state.CancelSearch();
                int second = state.BeginSearch();
                Check("a superseded search's completion is refused",
                    !state.CompleteSearch(first) && state.SearchRunning);
                Check("while the current one is still accepted", state.CompleteSearch(second));
            }
            finally
            {
                Discovery.WslRunner = savedRunner;
                Discovery.DistributionLister = savedLister;
                Discovery.WslAvailable = savedAvailable;
                Discovery.SearchRootProvider = savedRoots;
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

            // A build from a modified tree must not present itself as that
            // revision: the revision is what the managed environment would
            // download, and it is not what such a build contains.
            BuildInfo modified = new BuildInfo
            {
                Version = "0.1.0",
                Revision = "b81e907dec8398778cce46c225d4abb0695b1f80",
                SourceClean = false,
            };
            Check("a build from a modified tree says so where the revision is shown",
                modified.Describe().Contains("modified working tree"));
            Check("a clean build makes no such claim",
                !new BuildInfo { Version = "0.1.0", Revision = "abc", SourceClean = true }
                    .Describe().Contains("modified working tree"));
            Check("this build reports its own source-clean state",
                BuildInfo.Current().SourceClean == BuildStamp.SourceClean);

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
