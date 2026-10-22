using System;
using System.Collections.Generic;
using System.IO;
using Microsoft.Win32;
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

        /// <summary>Every registry key this suite is allowed to write.</summary>
        private const string TestRegistryRoot = @"Software\OmoBench-tests";

        private static List<Installation.CompanionProcess> NoCompanions()
        {
            return new List<Installation.CompanionProcess>();
        }

        /// <summary>
        /// What the real installation looked like before the suite ran.
        ///
        /// The isolation above is the fix; this is the proof. It reads the
        /// fixed registry locations and the real program directory directly --
        /// never through the overridden properties -- so a test that quietly
        /// escaped its sandbox is reported rather than discovered later by the
        /// person whose Apps &amp; features entry went missing.
        /// </summary>
        private sealed class Sentinel
        {
            private readonly string _uninstallEntry;
            private readonly string _protocolEntry;
            private readonly string _programDirectory;
            private readonly string _programContents;
            private readonly string _runtimeDirectory;
            private readonly string _runtimeContents;
            private readonly string _logPath;
            private readonly string _log;
            private readonly int _companions;

            private Sentinel(string uninstallEntry, string protocolEntry,
                string programDirectory, string programContents,
                string runtimeDirectory, string runtimeContents,
                string logPath, string log, int companions)
            {
                _uninstallEntry = uninstallEntry;
                _protocolEntry = protocolEntry;
                _programDirectory = programDirectory;
                _programContents = programContents;
                _runtimeDirectory = runtimeDirectory;
                _runtimeContents = runtimeContents;
                _logPath = logPath;
                _log = log;
                _companions = companions;
            }

            public static Sentinel Capture()
            {
                // Captured before any override is set, so these are the real
                // locations whatever the suite does to the properties later.
                string programDirectory = InstallPaths.ProgramDirectory;
                string runtimeDirectory = Configuration.RuntimeDirectoryPath;
                string logPath = Diagnostics.LogPath;
                return new Sentinel(
                    DescribeKey(InstallPaths.DefaultUninstallKeyPath),
                    DescribeKey(ProtocolRegistration.DefaultKeyPath),
                    programDirectory,
                    DescribeDirectory(programDirectory),
                    runtimeDirectory,
                    DescribeDirectory(runtimeDirectory),
                    logPath,
                    DescribeFile(logPath),
                    CountCompanionProcesses());
            }

            public void Verify()
            {
                Section("Sentinels: the real installation was not touched");

                Check("the real Apps & features entry is exactly as it was"
                    + (_uninstallEntry == "(absent)" ? " (it was absent, and still is)" : ""),
                    DescribeKey(InstallPaths.DefaultUninstallKeyPath) == _uninstallEntry);
                Check("the real omobench:// registration is exactly as it was"
                    + (_protocolEntry == "(absent)" ? " (it was absent, and still is)" : ""),
                    DescribeKey(ProtocolRegistration.DefaultKeyPath) == _protocolEntry);
                Check("the real program directory is exactly as it was",
                    DescribeDirectory(_programDirectory) == _programContents);
                Check("the real diagnostic log is exactly as it was"
                    + (_log == "(absent)" ? " (it was absent, and still is)" : ""),
                    DescribeFile(_logPath) == _log);
                Check("and the rest of the real runtime directory with it",
                    DescribeDirectory(_runtimeDirectory) == _runtimeContents);
                Check("no OmoBench companion process was closed",
                    CountCompanionProcesses() >= _companions);
                Check("and the suite left none of its own registry keys behind",
                    !TestRegistryRootExists());
            }

            private static bool TestRegistryRootExists()
            {
                try
                {
                    using (RegistryKey key = Registry.CurrentUser.OpenSubKey(TestRegistryRoot))
                    {
                        return key != null;
                    }
                }
                catch (Exception) { return false; }
            }

            /// <summary>Read-only: the suite never asks a real process anything.</summary>
            private static int CountCompanionProcesses()
            {
                try
                {
                    return System.Diagnostics.Process.GetProcessesByName("OmoBenchCompanion").Length;
                }
                catch (Exception) { return 0; }
            }

            private static string DescribeKey(string path)
            {
                try
                {
                    using (RegistryKey key = Registry.CurrentUser.OpenSubKey(path))
                    {
                        if (key == null) { return "(absent)"; }
                        return Describe(key, path);
                    }
                }
                catch (Exception error) { return "(unreadable: " + error.Message + ")"; }
            }

            private static string Describe(RegistryKey key, string path)
            {
                System.Text.StringBuilder text = new System.Text.StringBuilder();
                string[] values = key.GetValueNames();
                Array.Sort(values, StringComparer.OrdinalIgnoreCase);
                foreach (string name in values)
                {
                    object value = key.GetValue(name);
                    text.Append(path).Append('|').Append(name).Append('=')
                        .Append(value == null ? "" : value.ToString()).Append('\n');
                }
                string[] children = key.GetSubKeyNames();
                Array.Sort(children, StringComparer.OrdinalIgnoreCase);
                foreach (string child in children)
                {
                    using (RegistryKey sub = key.OpenSubKey(child))
                    {
                        if (sub != null) { text.Append(Describe(sub, path + "\\" + child)); }
                    }
                }
                return text.ToString();
            }

            /// <summary>
            /// Length and last-write time, so an append or a rotation shows up
            /// even when the file is there both times.
            /// </summary>
            private static string DescribeFile(string path)
            {
                try
                {
                    if (string.IsNullOrEmpty(path) || !File.Exists(path)) { return "(absent)"; }
                    FileInfo file = new FileInfo(path);
                    return file.Length.ToString(System.Globalization.CultureInfo.InvariantCulture)
                        + "|" + file.LastWriteTimeUtc.Ticks.ToString(
                            System.Globalization.CultureInfo.InvariantCulture);
                }
                catch (Exception error) { return "(unreadable: " + error.Message + ")"; }
            }

            private static string DescribeDirectory(string path)
            {
                try
                {
                    if (!Directory.Exists(path)) { return "(absent)"; }
                    string[] files = Directory.GetFileSystemEntries(path, "*", SearchOption.AllDirectories);
                    Array.Sort(files, StringComparer.OrdinalIgnoreCase);
                    System.Text.StringBuilder text = new System.Text.StringBuilder();
                    foreach (string file in files)
                    {
                        text.Append(file);
                        try
                        {
                            if (File.Exists(file)) { text.Append('|').Append(new FileInfo(file).Length); }
                        }
                        catch (Exception) { }
                        text.Append('\n');
                    }
                    return text.ToString();
                }
                catch (Exception error) { return "(unreadable: " + error.Message + ")"; }
            }
        }

        private static int Main()
        {
            // Isolation is arranged before a single test runs, and proved
            // afterwards. Four things in this suite used to reach the real
            // machine: the Apps & features entry and the omobench://
            // registration are at fixed registry paths that RootOverride does
            // not move; the upgrade tests enumerated and sent close requests
            // to whatever OmoBench was actually running; and the register and
            // unregister tests call Diagnostics.Write, which appends to -- and
            // rotates, and so can delete -- the real companion.log.
            Sentinel sentinel = Sentinel.Capture();

            string savedUninstallKey = InstallPaths.UninstallKeyPathOverride;
            string savedProtocolKey = ProtocolRegistration.KeyPathOverride;
            string savedRuntimeDirectory = Configuration.RuntimeDirectoryOverride;
            string savedConfigurationFile = Configuration.FilePathOverride;
            Func<List<Installation.CompanionProcess>> savedEnumerator = Installation.CompanionEnumerator;
            string isolation = TestRegistryRoot + @"\" + Guid.NewGuid().ToString("N");
            string sandbox = Path.Combine(Path.GetTempPath(),
                "omobench-suite-" + Guid.NewGuid().ToString("N"));

            try
            {
                InstallPaths.UninstallKeyPathOverride = isolation + @"\Uninstall\OmoBench";
                ProtocolRegistration.KeyPathOverride = isolation + @"\Classes\omobench";
                // The diagnostic log and the panel's runtime directory both
                // live here, so nothing the suite logs touches the real ones.
                Configuration.RuntimeDirectoryOverride = Path.Combine(sandbox, "runtime");
                Configuration.FilePathOverride = Path.Combine(sandbox, "companion.config");
                Installation.CompanionEnumerator = NoCompanions;

                InstallPathTests();
                SupportDetectionTests();
                ManagedEnvironmentCommandTests();
                ManagedEnvironmentStageTests();
                LinuxOwnershipTests();
                ReviewRegressionTests();
                RuntimePayloadTests();
                PayloadContentsTests();
                UninstallCompletionTests();
                UninstallCoordinationTests();
                CompanionProcessTests();
                DelayedDiscoveryTests();
                WizardCompletionTests();
                BuildIdentityTests();
            }
            finally
            {
                InstallPaths.UninstallKeyPathOverride = savedUninstallKey;
                ProtocolRegistration.KeyPathOverride = savedProtocolKey;
                Configuration.RuntimeDirectoryOverride = savedRuntimeDirectory;
                Configuration.FilePathOverride = savedConfigurationFile;
                Installation.CompanionEnumerator = savedEnumerator;
                try { Registry.CurrentUser.DeleteSubKeyTree(TestRegistryRoot, false); }
                catch (Exception) { }
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
            }

            sentinel.Verify();

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
                InstallPaths.DefaultUninstallKeyPath.StartsWith(
                    @"Software\Microsoft\Windows", StringComparison.Ordinal));

            // And this suite is not pointed at it, nor at the real handler.
            Check("the tests write their uninstall entry somewhere else entirely",
                InstallPaths.UninstallKeyPath.StartsWith(TestRegistryRoot, StringComparison.Ordinal));
            Check("and their omobench:// registration too",
                ProtocolRegistration.KeyPath.StartsWith(TestRegistryRoot, StringComparison.Ordinal)
                && ProtocolRegistration.KeyPath != ProtocolRegistration.DefaultKeyPath);

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

            /// <summary>
            /// Where the "tar" command puts its result. Unpacking is the one
            /// step whose effects the next steps depend on, so a fixture that
            /// did nothing here would be testing a preparation that never has
            /// any source -- which the real validation would, correctly,
            /// refuse.
            /// </summary>
            public string UnpackInto;

            /// <summary>
            /// Unpack the real archive the installer wrote, with tar.exe,
            /// rather than planting a known-good tree. Used by the payload
            /// regression tests, which are about what is actually inside it.
            /// </summary>
            public bool UnpackForReal;

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
                if (arguments.Contains(" tar ") && UnpackInto != null)
                {
                    if (UnpackForReal)
                    {
                        if (!ExtractNewestArchive(UnpackInto)) { return Bad("could not unpack"); }
                    }
                    else
                    {
                        PlantCompletePayload(UnpackInto);
                    }
                }
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

        /// <summary>
        /// A complete payload, as a correct build produces one: every required
        /// path, a few extras, and a manifest covering all of them.
        /// </summary>
        private static void PlantCompletePayload(string root)
        {
            List<string> written = new List<string>();
            foreach (string required in RuntimeManifest.RequiredPaths)
            {
                WritePayloadFile(root, required, "contents of " + required);
                written.Add(required);
            }
            foreach (string extra in new string[]
                { "results/README.md", "benchmarks/profiles/chat-small-extended-v1.yaml", "cli/src/omobench/telemetry.py" })
            {
                if (!written.Contains(extra))
                {
                    WritePayloadFile(root, extra, "contents of " + extra);
                    written.Add(extra);
                }
            }
            WriteManifest(root, written);
        }

        private static void WritePayloadFile(string root, string relative, string contents)
        {
            string path = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, contents);
        }

        private static void WriteManifest(string root, IEnumerable<string> relativePaths)
        {
            System.Text.StringBuilder manifest = new System.Text.StringBuilder();
            foreach (string relative in relativePaths)
            {
                string path = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
                manifest.Append(RuntimePayload.Sha256(File.ReadAllBytes(path)))
                    .Append("  ").Append(relative).Append("\n");
            }
            File.WriteAllText(Path.Combine(root, RuntimeManifest.FileName), manifest.ToString());
        }

        /// <summary>
        /// Unpack the archive the installer just wrote, the way the real tar
        /// command would: strip the leading directory and write into the
        /// destination.
        /// </summary>
        private static bool ExtractNewestArchive(string destination)
        {
            try
            {
                string[] archives = Directory.GetFiles(ManagedEnvironment.ArchiveDirectory(), "*.tar.gz");
                if (archives.Length == 0) { return false; }
                Array.Sort(archives, (left, right) =>
                    File.GetLastWriteTimeUtc(right).CompareTo(File.GetLastWriteTimeUtc(left)));
                Directory.CreateDirectory(destination);
                return RunTar("-xzf \"" + archives[0] + "\" -C \"" + destination + "\" --strip-components=1");
            }
            catch (Exception) { return false; }
        }

        private static bool RunTar(string arguments)
        {
            System.Diagnostics.Process process = new System.Diagnostics.Process
            {
                StartInfo = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.System), "tar.exe"),
                    Arguments = arguments,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardError = true,
                    RedirectStandardOutput = true,
                },
            };
            process.Start();
            process.StandardOutput.ReadToEnd();
            process.StandardError.ReadToEnd();
            process.WaitForExit(60000);
            return process.ExitCode == 0;
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

            // Where the fixture's "tar" leaves a complete payload, so the
            // contents check that runs next sees what a correct build ships.
            string unpacked = ManagedEnvironment.SourceDirectory(build.Revision);

            try
            {
                // The happy path, with the real check standing in as passing.
                FakeWsl good = new FakeWsl { UnpackInto = unpacked };
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
                FakeWsl noPython = new FakeWsl { SystemPython = null, UnpackInto = unpacked };
                ManagedEnvironment.WslRunner = noPython.Run;
                PreparationResult missingPython = ManagedEnvironment.Prepare("Ubuntu", build, null, null);
                Check("a distribution without python3 fails at the first stage",
                    !missingPython.Ok && missingPython.Stages[0].State == StageState.Failed);
                Check("and the fix names the package and the password it needs",
                    (missingPython.Detail ?? "").Contains("python3-venv")
                    && (missingPython.Detail ?? "").Contains("Linux password"));

                // A failed venv points at the usual Ubuntu cause.
                FakeWsl noVenv = new FakeWsl { FailOn = "-m venv", UnpackInto = unpacked };
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
                FakeWsl stopping = new FakeWsl { Cancelled = true, Cancel = cancel, UnpackInto = unpacked };
                ManagedEnvironment.WslRunner = stopping.Run;
                PreparationResult cancelled = ManagedEnvironment.Prepare("Ubuntu", build, cancel, null);
                Check("cancelling stops the preparation", cancelled.Cancelled && !cancelled.Ok);
                Check("and says nothing further was changed",
                    (cancelled.Summary ?? "").Contains("Nothing further was changed"));

                // An installer with no revision cannot fetch pinned sources.
                ManagedEnvironment.WslRunner = new FakeWsl { UnpackInto = unpacked }.Run;
                PreparationResult unstamped = ManagedEnvironment.Prepare(
                    "Ubuntu", new BuildInfo { Version = "0.0.0", Revision = "" }, null, null);
                Check("an unstamped build refuses rather than guessing a revision",
                    !unstamped.Ok && (unstamped.Summary ?? "").Contains("revision"));

                // Progress is reported as it goes.
                int updates = 0;
                ManagedEnvironment.WslRunner = new FakeWsl { UnpackInto = unpacked }.Run;
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

        /// <summary>
        /// A companion that exists only here: it counts the close requests it
        /// receives and decides for itself whether to go.
        /// </summary>
        private sealed class FakeCompanion
        {
            public int Id;
            public int CloseRequests;

            /// <summary>Polls after being asked before it goes. Never, by default.</summary>
            public int ExitAfterPolls = -1;

            /// <summary>Stands in for a process we are not allowed to inspect.</summary>
            public bool ExitThrows;

            private int _polls;

            public List<Installation.CompanionProcess> Enumerate()
            {
                List<Installation.CompanionProcess> running = new List<Installation.CompanionProcess>();
                running.Add(new Installation.CompanionProcess
                {
                    Id = Id,
                    HasExited = () =>
                    {
                        if (ExitThrows) { throw new InvalidOperationException("access denied"); }
                        _polls++;
                        return ExitAfterPolls >= 0 && CloseRequests > 0 && _polls >= ExitAfterPolls;
                    },
                    RequestClose = () => { CloseRequests++; },
                });
                return running;
            }
        }

        // -- review findings: upgrade safety, redirection, ownership ------------

        private static void ReviewRegressionTests()
        {
            Section("Review findings");

            // 1. Upgrade coordination asks, waits, and never terminates
            //    anything -- against processes this test invented. It used to
            //    enumerate the machine's real ones and send them close
            //    requests.
            Func<List<Installation.CompanionProcess>> savedEnumerator = Installation.CompanionEnumerator;
            try
            {
                Check("with no companion running, closing trivially succeeds",
                    Installation.TryCloseInstalledCompanions(0));
                Check("and nothing is reported as running",
                    Installation.RunningInstalledCompanions().Count == 0);

                FakeCompanion obliging = new FakeCompanion { Id = 4101, ExitAfterPolls = 2 };
                Installation.CompanionEnumerator = obliging.Enumerate;
                Check("a companion that closes when asked lets the work proceed",
                    Installation.TryCloseInstalledCompanions(5000));
                Check("it was asked once, politely",
                    obliging.CloseRequests == 1);

                FakeCompanion stubborn = new FakeCompanion { Id = 4102 };   // never exits
                Installation.CompanionEnumerator = stubborn.Enumerate;
                Check("a companion that declines is not forced",
                    !Installation.TryCloseInstalledCompanions(600));
                Check("and declining is an answer, asked for only once",
                    stubborn.CloseRequests == 1);

                // There is no second, harsher option anywhere in the API: a
                // companion can be asked to close and nothing else.
                Check("coordination offers no way to terminate a companion",
                    typeof(Installation.CompanionProcess).GetField("Kill") == null
                    && !HasMember(typeof(Installation), "KillInstalledCompanions"));

                // A process that cannot be asked whether it has gone is not
                // treated as gone.
                FakeCompanion unknowable = new FakeCompanion { Id = 4103, ExitThrows = true };
                Installation.CompanionEnumerator = unknowable.Enumerate;
                Check("a companion whose state cannot be read counts as still running",
                    Installation.RunningInstalledCompanions().Count == 1);
            }
            finally
            {
                Installation.CompanionEnumerator = savedEnumerator;
            }

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
                    ManagedEnvironment.ArchiveDirectory() == "/tmp/omobench-test-dl");

                // Each attempt writes its own file. A fixed name is shared
                // state between a run that is still going and one that has
                // just started.
                string firstArchive = ManagedEnvironment.NewArchivePath();
                string secondArchive = ManagedEnvironment.NewArchivePath();
                Check("every attempt gets its own archive path",
                    firstArchive != secondArchive);
                Check("and both are inside the archive directory",
                    firstArchive.StartsWith("/tmp/omobench-test-dl", StringComparison.Ordinal)
                    && secondArchive.StartsWith("/tmp/omobench-test-dl", StringComparison.Ordinal));

                ManagedEnvironment.EnvironmentRootOverride = null;
                ManagedEnvironment.ArchiveDirectoryOverride = null;
                Check("without redirection the environment is under the Linux home",
                    ManagedEnvironment.EnvironmentDirectory("/home/sam")
                        == "/home/sam/.local/share/omobench/managed-env");
                Check("and the archive defaults to a temporary Windows location",
                    ManagedEnvironment.NewArchivePath().StartsWith(
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
                FakeWsl fake = new FakeWsl
                {
                    UnpackInto = ManagedEnvironment.SourceDirectory(
                        "b81e907dec8398778cce46c225d4abb0695b1f80"),
                };
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

            }
            finally
            {
                Discovery.WslRunner = savedRunner;
                Discovery.DistributionLister = savedLister;
                Discovery.WslAvailable = savedAvailable;
                Discovery.SearchRootProvider = savedRoots;
            }
        }

        // -- the Linux side is somebody's home directory --------------------------

        /// <summary>
        /// A distribution whose filesystem answers questions about one
        /// directory. Nothing here reaches a real WSL installation.
        /// </summary>
        private sealed class LinuxFixture
        {
            public readonly List<string> Commands = new List<string>();
            public bool RootExists;
            public bool MarkerExists;
            public string Listing = "";
            public bool TouchFails;
            public bool ExistsUnanswerable;

            /// <summary>
            /// What `test -e` exits with instead of answering. 0 and 1 are the
            /// only two answers test gives; 127 is "command not found" and 2 a
            /// usage error, and neither means the directory is absent.
            /// </summary>
            public int ExistsExitCode = -1;

            /// <summary>Only the marker probe fails this way, not the root one.</summary>
            public int MarkerExitCode = -1;

            public ProcessResult Run(string arguments, int timeout, DiscoveryCancel cancel)
            {
                Commands.Add(arguments);
                if (arguments.Contains(" test -e "))
                {
                    if (ExistsUnanswerable) { return new ProcessResult { ExitCode = -1, TimedOut = true }; }
                    bool marker = arguments.Contains(ManagedEnvironment.OwnershipMarkerName);
                    if (marker && MarkerExitCode >= 0)
                    {
                        return new ProcessResult { ExitCode = MarkerExitCode, StandardError = "failed" };
                    }
                    if (!marker && ExistsExitCode >= 0)
                    {
                        return new ProcessResult { ExitCode = ExistsExitCode, StandardError = "failed" };
                    }
                    bool present = marker ? MarkerExists : RootExists;
                    return new ProcessResult { ExitCode = present ? 0 : 1 };
                }
                if (arguments.Contains(" ls -A "))
                {
                    return new ProcessResult { ExitCode = 0, StandardOutput = Listing };
                }
                if (arguments.Contains(" touch "))
                {
                    if (TouchFails)
                    {
                        return new ProcessResult { ExitCode = 1, StandardError = "read-only file system" };
                    }
                    MarkerExists = true;
                    RootExists = true;
                    return new ProcessResult { ExitCode = 0 };
                }
                return new ProcessResult { ExitCode = 0 };
            }
        }

        private static bool Ran(List<string> commands, string fragment)
        {
            foreach (string command in commands)
            {
                if (command.Contains(fragment)) { return true; }
            }
            return false;
        }

        private static void LinuxOwnershipTests()
        {
            Section("The managed Linux location is claimed before it is used");

            Func<string, int, DiscoveryCancel, ProcessResult> savedRunner = ManagedEnvironment.WslRunner;
            string savedEnvironmentRoot = ManagedEnvironment.EnvironmentRootOverride;
            string savedSourceRoot = ManagedEnvironment.SourceRootOverride;
            string savedArchive = ManagedEnvironment.ArchiveDirectoryOverride;

            try
            {
                ManagedEnvironment.EnvironmentRootOverride = "/home/sam/.local/share/omobench-test";

                // Nothing there yet: created and marked, so a later attempt
                // can tell its own working area from somebody's data.
                LinuxFixture fresh = new LinuxFixture();
                ManagedEnvironment.WslRunner = fresh.Run;
                Check("an absent Linux location is claimed",
                    ManagedEnvironment.ClaimLinuxRoot("Ubuntu", "/home/sam", null) == null);
                Check("and marked as ours", fresh.MarkerExists && Ran(fresh.Commands, " touch "));

                // A previous attempt's working area: a retry continues into it.
                LinuxFixture ours = new LinuxFixture { RootExists = true, MarkerExists = true };
                ManagedEnvironment.WslRunner = ours.Run;
                Check("an environment a previous attempt created can be retried into",
                    ManagedEnvironment.ClaimLinuxRoot("Ubuntu", "/home/sam", null) == null);
                Check("without re-reading or re-marking what is already ours",
                    !Ran(ours.Commands, " ls -A ") && !Ran(ours.Commands, " touch "));

                // Somebody else's directory, which happens to share a name.
                LinuxFixture theirs = new LinuxFixture
                {
                    RootExists = true,
                    Listing = "notes.md\nvenv\n",
                };
                ManagedEnvironment.WslRunner = theirs.Run;
                string refusal = ManagedEnvironment.ClaimLinuxRoot("Ubuntu", "/home/sam", null);
                Check("a Linux directory with somebody's files in it is refused", refusal != null);
                Check("the refusal names it and says what to do",
                    refusal != null
                    && refusal.Contains("/home/sam/.local/share/omobench-test")
                    && refusal.Contains("will not be written into"));
                Check("and nothing of theirs was written, moved or marked",
                    !Ran(theirs.Commands, " touch ")
                    && !Ran(theirs.Commands, " mkdir ")
                    && !Ran(theirs.Commands, " rm "));

                // An empty directory is fair game.
                LinuxFixture empty = new LinuxFixture { RootExists = true, Listing = "" };
                ManagedEnvironment.WslRunner = empty.Run;
                Check("an empty Linux directory is claimed",
                    ManagedEnvironment.ClaimLinuxRoot("Ubuntu", "/home/sam", null) == null);

                // A question that cannot be answered is not an answer.
                LinuxFixture silent = new LinuxFixture { ExistsUnanswerable = true };
                ManagedEnvironment.WslRunner = silent.Run;
                string unknown = ManagedEnvironment.ClaimLinuxRoot("Ubuntu", "/home/sam", null);
                Check("a location whose existence cannot be determined is left alone",
                    unknown != null && unknown.Contains("left alone"));
                Check("and nothing was created there on the strength of a guess",
                    !Ran(silent.Commands, " touch "));

                // A probe that failed rather than answered. test says 0 or 1
                // and nothing else, so anything else is the question going
                // wrong -- and used to be read as "absent, go ahead".
                foreach (int code in new int[] { 2, 126, 127, 255 })
                {
                    LinuxFixture broken = new LinuxFixture { ExistsExitCode = code };
                    ManagedEnvironment.WslRunner = broken.Run;
                    string answer = ManagedEnvironment.ClaimLinuxRoot("Ubuntu", "/home/sam", null);
                    Check("exit " + code + " from the existence probe is not an answer",
                        answer != null && answer.Contains("could not be determined"));
                    Check("  and nothing was created on the strength of it",
                        !Ran(broken.Commands, " touch ") && !Ran(broken.Commands, " mkdir "));
                }

                // The same for the probe that decides whether the directory is
                // ours: not knowing is not permission to write into it.
                foreach (int code in new int[] { 2, 127 })
                {
                    LinuxFixture unsure = new LinuxFixture { RootExists = true, MarkerExitCode = code };
                    ManagedEnvironment.WslRunner = unsure.Run;
                    string answer = ManagedEnvironment.ClaimLinuxRoot("Ubuntu", "/home/sam", null);
                    Check("exit " + code + " from the ownership probe leaves the directory alone",
                        answer != null && answer.Contains("could not be determined"));
                    Check("  and its contents were not even listed",
                        !Ran(unsure.Commands, " ls -A ")
                        && !Ran(unsure.Commands, " touch ") && !Ran(unsure.Commands, " mkdir "));
                }

                // A directory that cannot be created is reported, not assumed.
                LinuxFixture readOnly = new LinuxFixture { TouchFails = true };
                ManagedEnvironment.WslRunner = readOnly.Run;
                Check("a location that cannot be created is reported",
                    ManagedEnvironment.ClaimLinuxRoot("Ubuntu", "/home/sam", null) != null);

                // And the whole preparation refuses before a venv exists or
                // pip has been pointed anywhere.
                string sandbox = Path.Combine(Path.GetTempPath(),
                    "omobench-linux-claim-" + Guid.NewGuid().ToString("N"));
                string archives = Path.Combine(sandbox, "archives");
                ManagedEnvironment.SourceRootOverride = Path.Combine(sandbox, "source");
                ManagedEnvironment.ArchiveDirectoryOverride = archives;
                BuildInfo build = new BuildInfo
                {
                    Version = "0.1.0",
                    Revision = "b81e907dec8398778cce46c225d4abb0695b1f80",
                };
                try
                {
                    OccupiedLinux occupied = new OccupiedLinux
                    {
                        UnpackInto = ManagedEnvironment.SourceDirectory(build.Revision),
                    };
                    ManagedEnvironment.WslRunner = occupied.Run;
                    PreparationResult refused = ManagedEnvironment.Prepare("Ubuntu", build, null, null);
                    Check("preparation refuses an occupied Linux location",
                        !refused.Ok && refused.Stages[2].State == StageState.Failed);
                    Check("and says which location is in use",
                        (refused.Summary ?? "").Contains("already in use"));
                    Check("no environment was created and nothing was installed",
                        !Ran(occupied.Commands, "-m venv")
                        && !Ran(occupied.Commands, "-m pip")
                        && refused.Stages[3].State == StageState.Pending);

                    // And the same when the probe fails rather than answers.
                    foreach (int code in new int[] { 2, 127 })
                    {
                        OccupiedLinux broken = new OccupiedLinux
                        {
                            UnpackInto = ManagedEnvironment.SourceDirectory(build.Revision),
                            ExistsExitCode = code,
                        };
                        ManagedEnvironment.WslRunner = broken.Run;
                        PreparationResult stopped = ManagedEnvironment.Prepare("Ubuntu", build, null, null);
                        Check("preparation refuses when the probe exits " + code,
                            !stopped.Ok && stopped.Stages[2].State == StageState.Failed);
                        Check("  with no mkdir, touch, venv or pip after it",
                            !Ran(broken.Commands, " mkdir ") && !Ran(broken.Commands, " touch ")
                            && !Ran(broken.Commands, "-m venv") && !Ran(broken.Commands, "-m pip"));
                        Check("  and nothing was installed",
                            stopped.Stages[3].State == StageState.Pending
                            && stopped.Configuration == null);
                    }
                }
                finally
                {
                    try { Directory.Delete(sandbox, true); } catch (Exception) { }
                }
            }
            finally
            {
                ManagedEnvironment.WslRunner = savedRunner;
                ManagedEnvironment.EnvironmentRootOverride = savedEnvironmentRoot;
                ManagedEnvironment.SourceRootOverride = savedSourceRoot;
                ManagedEnvironment.ArchiveDirectoryOverride = savedArchive;
            }
        }

        /// <summary>
        /// A distribution that is ready for OmoBench in every respect except
        /// that the managed location already belongs to somebody.
        /// </summary>
        private sealed class OccupiedLinux
        {
            public readonly List<string> Commands = new List<string>();
            public string UnpackInto;

            /// <summary>What the existence probe exits with instead of answering.</summary>
            public int ExistsExitCode = -1;

            public ProcessResult Run(string arguments, int timeout, DiscoveryCancel cancel)
            {
                Commands.Add(arguments);
                if (arguments.Contains(" printenv HOME"))
                {
                    return new ProcessResult { ExitCode = 0, StandardOutput = "/home/sam\n" };
                }
                if (arguments.Contains(" which python3"))
                {
                    return new ProcessResult { ExitCode = 0, StandardOutput = "/usr/bin/python3\n" };
                }
                if (arguments.Contains(" tar "))
                {
                    if (UnpackInto != null) { PlantCompletePayload(UnpackInto); }
                    return new ProcessResult { ExitCode = 0 };
                }
                if (arguments.Contains(" test -e "))
                {
                    if (ExistsExitCode >= 0)
                    {
                        return new ProcessResult { ExitCode = ExistsExitCode, StandardError = "failed" };
                    }
                    // The root is there; the ownership marker is not.
                    return new ProcessResult
                    {
                        ExitCode = arguments.Contains(ManagedEnvironment.OwnershipMarkerName) ? 1 : 0,
                    };
                }
                if (arguments.Contains(" ls -A "))
                {
                    return new ProcessResult { ExitCode = 0, StandardOutput = "my-own-work\n" };
                }
                return new ProcessResult { ExitCode = 0 };
            }
        }

        // -- what is actually inside the payload ---------------------------------

        private static void PayloadContentsTests()
        {
            Section("Payload contents: required files, not just a file count");

            string sandbox = Path.Combine(Path.GetTempPath(),
                "omobench-contents-" + Guid.NewGuid().ToString("N"));
            Func<Stream> savedOpener = RuntimePayload.Opener;
            string savedSha = RuntimePayload.ExpectedSha256;
            long savedBytes = RuntimePayload.ExpectedBytes;
            Func<string, int, DiscoveryCancel, ProcessResult> savedRunner = ManagedEnvironment.WslRunner;
            Func<Configuration, EnvironmentCheckResult> savedValidator = ManagedEnvironment.Validator;
            string savedSourceRoot = ManagedEnvironment.SourceRootOverride;
            string savedArchiveRoot = ManagedEnvironment.ArchiveDirectoryOverride;

            try
            {
                // 1. The archive this installer actually carries, unpacked the
                //    way the installer unpacks it, checked the way the
                //    installer checks it.
                string realArchive = Path.Combine(sandbox, "real.tar.gz");
                string realUnpacked = Path.Combine(sandbox, "real-unpacked");
                string payloadFailure;
                bool wrote = RuntimePayload.TryWrite(realArchive, out payloadFailure);
                Check("the embedded payload is written for inspection: " + (payloadFailure ?? "ok"), wrote);

                if (wrote)
                {
                    Directory.CreateDirectory(realUnpacked);
                    bool unpacked = RunTar("-xzf \"" + realArchive + "\" -C \"" + realUnpacked
                        + "\" --strip-components=1");
                    Check("the embedded payload is a real archive that unpacks", unpacked);

                    string contentsFailure;
                    Check("and its contents satisfy the installer's own check: "
                        + (RuntimeManifest.Validate(realUnpacked, out contentsFailure)
                            ? "ok" : contentsFailure),
                        RuntimeManifest.Validate(realUnpacked, out contentsFailure));

                    string[] unpackedFiles = Directory.GetFiles(
                        realUnpacked, "*", SearchOption.AllDirectories);
                    Check("it carries a working OmoBench rather than a token few files: "
                        + unpackedFiles.Length + " files", unpackedFiles.Length > 20);
                    Check("including the results validator",
                        File.Exists(Path.Combine(realUnpacked, @"tools\validate_results.py")));
                    Check("and the panel's web assets",
                        File.Exists(Path.Combine(realUnpacked, @"cli\src\omobench\web\index.html")));

                    // Tampering with an unpacked file is caught by the manifest.
                    File.AppendAllText(
                        Path.Combine(realUnpacked, @"tools\validate_results.py"), "\n# changed\n");
                    string tampered;
                    Check("a file changed after unpacking fails the manifest",
                        !RuntimeManifest.Validate(realUnpacked, out tampered)
                        && tampered.Contains("does not match its own manifest"));
                }

                // 2. The regression this check exists for: a payload with
                //    plenty of files that is still not OmoBench. The byte
                //    count and the checksum both pass; it is the contents that
                //    do not.
                string deficientRoot = Path.Combine(sandbox, "deficient", "omobench");
                List<string> planted = new List<string>();
                foreach (string required in RuntimeManifest.RequiredPaths)
                {
                    if (required == "tools/validate_results.py"
                        || required == "cli/src/omobench/web/index.html")
                    {
                        continue;    // the two the build was supposed to carry
                    }
                    WritePayloadFile(deficientRoot, required, "contents of " + required);
                    planted.Add(required);
                }
                for (int extra = 0; extra < 6; extra++)
                {
                    string filler = "results/sample-" + extra + ".md";
                    WritePayloadFile(deficientRoot, filler, "filler " + extra);
                    planted.Add(filler);
                }
                WriteManifest(deficientRoot, planted);

                Check("the regression payload is large enough to pass a file count: "
                    + (planted.Count + 1) + " files", planted.Count + 1 > 20);

                string deficientArchive = Path.Combine(sandbox, "deficient.tar.gz");
                Check("the regression payload is packed into a real archive",
                    RunTar("-czf \"" + deficientArchive + "\" -C \""
                        + Path.Combine(sandbox, "deficient") + "\" omobench"));

                byte[] deficientBytes = File.ReadAllBytes(deficientArchive);
                RuntimePayload.Opener = () => new MemoryStream(deficientBytes);
                RuntimePayload.ExpectedSha256 = RuntimePayload.Sha256(deficientBytes);
                RuntimePayload.ExpectedBytes = deficientBytes.Length;

                string regressionSandbox = Path.Combine(sandbox, "regression");
                ManagedEnvironment.SourceRootOverride = Path.Combine(regressionSandbox, "source");
                ManagedEnvironment.ArchiveDirectoryOverride = Path.Combine(regressionSandbox, "archives");
                ManagedEnvironment.Validator = configuration =>
                    new EnvironmentCheckResult { Ok = true, Summary = "should never be reached" };

                BuildInfo build = new BuildInfo
                {
                    Version = "0.1.0",
                    Revision = "b81e907dec8398778cce46c225d4abb0695b1f80",
                };
                FakeWsl distribution = new FakeWsl
                {
                    UnpackInto = ManagedEnvironment.SourceDirectory(build.Revision),
                    UnpackForReal = true,
                };
                ManagedEnvironment.WslRunner = distribution.Run;

                PreparationResult result = ManagedEnvironment.Prepare("Ubuntu", build, null, null);

                Check("an incomplete payload is not reported as ready", !result.Ok);
                Check("it fails at the source stage, before anything is installed",
                    result.Stages[1].State == StageState.Failed
                    && result.Stages[2].State == StageState.Pending
                    && result.Stages[3].State == StageState.Pending);
                Check("no Python environment was created and no pip was run",
                    !Ran(distribution.Commands, "-m venv") && !Ran(distribution.Commands, "-m pip"));
                Check("and it names the files that are missing",
                    (result.Detail ?? "").Contains("tools/validate_results.py")
                    && (result.Detail ?? "").Contains("cli/src/omobench/web/index.html"));
                Check("no configuration is handed back to be saved",
                    result.Configuration == null);

                // 3. A manifest is data, and is read as data.
                string parseFailure;
                Check("a manifest naming a path outside the source is refused",
                    RuntimeManifest.Parse(
                        new string('a', 64) + "  ../../../etc/passwd", out parseFailure) == null
                    && parseFailure.Contains("outside"));
                Check("a line that is not \"checksum path\" is refused",
                    RuntimeManifest.Parse("not a manifest line", out parseFailure) == null);
                Check("an empty manifest is refused",
                    RuntimeManifest.Parse("   \n\n", out parseFailure) == null);
                Check("a well-formed manifest parses",
                    RuntimeManifest.Parse(
                        new string('b', 64) + "  cli/pyproject.toml", out parseFailure) != null);
                Check("an archive listing is checked against the required paths",
                    RuntimeManifest.MissingFrom(new string[] { "omobench/cli/pyproject.toml" }).Count
                        == RuntimeManifest.RequiredPaths.Length - 1);

                // An unpacked copy with no manifest at all is refused rather
                // than trusted.
                string bare = Path.Combine(sandbox, "bare");
                Directory.CreateDirectory(bare);
                string bareFailure;
                Check("an unpacked copy with no manifest is refused",
                    !RuntimeManifest.Validate(bare, out bareFailure)
                    && bareFailure.Contains(RuntimeManifest.FileName));
            }
            finally
            {
                RuntimePayload.Opener = savedOpener;
                RuntimePayload.ExpectedSha256 = savedSha;
                RuntimePayload.ExpectedBytes = savedBytes;
                ManagedEnvironment.WslRunner = savedRunner;
                ManagedEnvironment.Validator = savedValidator;
                ManagedEnvironment.SourceRootOverride = savedSourceRoot;
                ManagedEnvironment.ArchiveDirectoryOverride = savedArchiveRoot;
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
            }
        }

        // -- uninstall coordinates before it removes anything --------------------

        private static void UninstallCoordinationTests()
        {
            Section("Uninstall: coordination, refusal and accurate outcomes");

            string savedRoot = InstallPaths.RootOverride;
            Func<List<Installation.CompanionProcess>> savedEnumerator = Installation.CompanionEnumerator;
            int savedWait = Installation.CloseWaitMilliseconds;
            string sandbox = Path.Combine(Path.GetTempPath(),
                "omobench-uninstall-" + Guid.NewGuid().ToString("N"));

            try
            {
                InstallPaths.RootOverride = sandbox;
                // The real wait gives a person time to answer a question about
                // a running benchmark; the fixture answers immediately.
                Installation.CloseWaitMilliseconds = 600;
                BuildInfo build = new BuildInfo { Version = "0.1.0", Revision = "abc123" };

                // A complete, entirely test-owned installation: files under a
                // throwaway root, registry entries under the test's own keys,
                // and an uninstaller beside the companion -- which is what the
                // Apps & features entry points at, and what a retry needs.
                string stand = Path.Combine(sandbox, "stand-in-installer.exe");
                Directory.CreateDirectory(sandbox);
                File.WriteAllText(stand, "not really an installer");
                InstallOutcome installed = Installation.Install(
                    System.Text.Encoding.UTF8.GetBytes("not really a program"), stand, build);
                Check("a sandboxed install succeeds", installed.Ok);
                Check("  it wrote an Apps & features entry", UninstallEntryExists());
                Check("  it placed an uninstaller", File.Exists(InstallPaths.UninstallerPath));
                Check("  and registered omobench:// at the test-owned location",
                    ProtocolRegistration.RegisteredCommand() != null);
                Check("  the Apps & features entry points at that uninstaller",
                    SamePathValue(RegisteredUninstallTarget(), InstallPaths.UninstallerPath));

                // The companion is open and declines to close. Nothing may be
                // removed -- not the files, and not the two entries a person
                // would need to try again.
                FakeCompanion open = new FakeCompanion { Id = 5001 };
                Installation.CompanionEnumerator = open.Enumerate;
                InstallOutcome postponed = Installation.Uninstall();
                Check("an uninstall is postponed while the companion is open", !postponed.Ok);
                Check("  it was asked, not terminated", open.CloseRequests == 1);
                Check("  and says nothing was removed",
                    (postponed.Summary ?? "").Contains("nothing was removed"));
                Check("  the program is still there", File.Exists(InstallPaths.CompanionPath));
                Check("  its Apps & features entry is still there", UninstallEntryExists());
                Check("  and its omobench:// registration is still there",
                    ProtocolRegistration.RegisteredCommand() != null);

                // The companion has gone, but a required file cannot be
                // deleted: an incomplete removal, reported as one, with the
                // entry to retry from left in place.
                Installation.CompanionEnumerator = NoCompanions;
                using (FileStream held = new FileStream(InstallPaths.CompanionPath,
                    FileMode.Open, FileAccess.Read, FileShare.None))
                {
                    InstallOutcome blocked = Installation.Uninstall();
                    Check("a required file that cannot be deleted is a failed uninstall",
                        !blocked.Ok);
                    Check("  it names what is still present",
                        blocked.Describe().Contains(InstallPaths.CompanionPath));
                    Check("  the Apps & features entry is kept so it can be retried",
                        UninstallEntryExists());
                    Check("  and so is the omobench:// registration",
                        ProtocolRegistration.RegisteredCommand() != null);
                    Check("  it does not claim OmoBench was removed",
                        !blocked.Describe().Contains("has been removed"));

                    // The point of keeping the entry is that it still reaches
                    // a program that exists. Deleting the uninstaller first
                    // and then failing on the companion left the entry aimed
                    // at nothing at all.
                    string target = RegisteredUninstallTarget();
                    Check("  the retry target named in the registry still exists on disk: "
                        + (target ?? "(none)"),
                        target != null && File.Exists(target));
                    Check("  and it is the uninstaller, not something else",
                        SamePathValue(target, InstallPaths.UninstallerPath));
                    GC.KeepAlive(held);
                }

                // The companion is free now, but the uninstaller itself is
                // held open. Everything else goes; the leftover is reported
                // rather than waved through, and the entry stays so the person
                // can finish later.
                using (FileStream heldUninstaller = new FileStream(InstallPaths.UninstallerPath,
                    FileMode.Open, FileAccess.Read, FileShare.None))
                {
                    InstallOutcome partial = Installation.Uninstall();
                    Check("a locked uninstaller is not reported as a clean removal", !partial.Ok);
                    Check("  it names the file that is still there",
                        partial.Describe().Contains(InstallPaths.UninstallerFileName));
                    Check("  the companion went anyway", !File.Exists(InstallPaths.CompanionPath));
                    Check("  and the Apps & features entry is kept to finish from",
                        UninstallEntryExists());
                    GC.KeepAlive(heldUninstaller);
                }

                // Now nothing is in the way.
                InstallOutcome removed = Installation.Uninstall();
                Check("the retry succeeds once the files are free", removed.Ok);
                Check("  the program is gone", !File.Exists(InstallPaths.CompanionPath));
                Check("  so is the uninstaller", !File.Exists(InstallPaths.UninstallerPath));
                Check("  the Apps & features entry is gone", !UninstallEntryExists());
                Check("  the omobench:// registration is gone",
                    ProtocolRegistration.RegisteredCommand() == null);
                Check("  and it says what was left alone",
                    removed.Describe().Contains("were not touched"));
            }
            finally
            {
                InstallPaths.RootOverride = savedRoot;
                Installation.CompanionEnumerator = savedEnumerator;
                Installation.CloseWaitMilliseconds = savedWait;
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
                try { Registry.CurrentUser.DeleteSubKeyTree(InstallPaths.UninstallKeyPath, false); }
                catch (Exception) { }
                try { Registry.CurrentUser.DeleteSubKeyTree(ProtocolRegistration.KeyPath, false); }
                catch (Exception) { }
            }
        }

        /// <summary>
        /// The program the Apps &amp; features entry would actually run, read
        /// back out of the registry the way Windows reads it.
        /// </summary>
        private static string RegisteredUninstallTarget()
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(InstallPaths.UninstallKeyPath))
                {
                    if (key == null) { return null; }
                    string command = key.GetValue("UninstallString") as string;
                    if (string.IsNullOrEmpty(command)) { return null; }
                    if (command[0] == '"')
                    {
                        int closing = command.IndexOf('"', 1);
                        return closing < 0 ? null : command.Substring(1, closing - 1);
                    }
                    int space = command.IndexOf(' ');
                    return space < 0 ? command : command.Substring(0, space);
                }
            }
            catch (Exception) { return null; }
        }

        private static bool SamePathValue(string left, string right)
        {
            if (left == null || right == null) { return false; }
            try
            {
                return string.Equals(Path.GetFullPath(left), Path.GetFullPath(right),
                    StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception) { return false; }
        }

        private static bool UninstallEntryExists()
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(InstallPaths.UninstallKeyPath))
                {
                    return key != null;
                }
            }
            catch (Exception) { return false; }
        }

        // -- coordinating with a real process ------------------------------------

        /// <summary>
        /// The production adapter and enumerator, not only the fixture that
        /// stands in for them.
        ///
        /// Everything here is read-only or uses a process this test owns: the
        /// enumerator is pointed at a throwaway install location, so it
        /// matches nothing on the machine, and the adapter is exercised
        /// against a Process object that was never started.
        /// </summary>
        private static void CompanionProcessTests()
        {
            Section("Companion coordination: the real adapter, and failed questions");

            string savedRoot = InstallPaths.RootOverride;
            Func<List<Installation.CompanionProcess>> savedEnumerator = Installation.CompanionEnumerator;
            int savedWait = Installation.CloseWaitMilliseconds;
            string sandbox = Path.Combine(Path.GetTempPath(),
                "omobench-coordination-" + Guid.NewGuid().ToString("N"));

            try
            {
                InstallPaths.RootOverride = sandbox;
                Installation.CloseWaitMilliseconds = 400;

                // The production enumerator, against an install location
                // nothing is running from. It looks at the machine's process
                // list and matches by executable path, so this finds nothing
                // and touches nothing.
                Installation.CompanionEnumerator = Installation.EnumerateInstalledCompanions;
                Installation.CompanionScan real = Installation.ScanInstalledCompanions();
                Check("the production enumerator answers for an empty install location",
                    real.Answered && real.Running.Count == 0);
                Check("so closing trivially succeeds", Installation.TryCloseInstalledCompanions(0));

                // The production adapter around a Process that cannot answer.
                // A Process object with nothing behind it throws on HasExited,
                // which is what an access-denied query does too.
                using (System.Diagnostics.Process unanswerable = new System.Diagnostics.Process())
                {
                    Installation.CompanionProcess adapted = Installation.Adapt(unanswerable);
                    bool threw = false;
                    try { adapted.HasExited(); }
                    catch (Exception) { threw = true; }
                    Check("the adapter does not answer \"it has exited\" for a question that failed",
                        threw);

                    // And the caller reads that as still running, because it is
                    // the only safe reading when the next step is deleting the
                    // program.
                    Installation.CompanionEnumerator = () =>
                        new List<Installation.CompanionProcess> { adapted };
                    Check("a companion whose state cannot be read counts as still running",
                        Installation.RunningInstalledCompanions().Count == 1);
                    Check("and closing is not reported as successful",
                        !Installation.TryCloseInstalledCompanions(300));

                    // RequestClose on the same process cannot deliver anything
                    // either, and must not throw its way out of coordination.
                    bool requestThrew = false;
                    try { adapted.RequestClose(); }
                    catch (Exception) { requestThrew = true; }
                    Check("asking it to close fails quietly rather than crashing the uninstall",
                        !requestThrew);
                }

                // Enumeration itself failing is the case that used to become
                // "nothing is running" -- and so permission to delete.
                int closeRequests = 0;
                Installation.CompanionEnumerator = () =>
                {
                    closeRequests++;    // nothing may reach a process from here
                    throw new InvalidOperationException("the process list could not be read");
                };
                Installation.CompanionScan failed = Installation.ScanInstalledCompanions();
                Check("a failed enumeration is not an answer", !failed.Answered);
                Check("and it is not read as \"none are running\"", failed.AnyPossiblyRunning);
                Check("closing reports failure rather than success",
                    !Installation.TryCloseInstalledCompanions(300));

                // Which postpones the uninstall, with nothing removed.
                Directory.CreateDirectory(InstallPaths.ProgramDirectory);
                File.WriteAllText(InstallPaths.CompanionPath, "companion");
                File.WriteAllText(InstallPaths.ManifestPath, "{}");
                InstallOutcome postponed = Installation.Uninstall();
                Check("an uninstall postpones when the question cannot be answered", !postponed.Ok);
                Check("  and the program is still there", File.Exists(InstallPaths.CompanionPath));
                Check("  and it says nothing was removed",
                    (postponed.Summary ?? "").Contains("nothing was removed"));

                // An enumerator that returns nothing at all is a different
                // thing from one that could not look.
                Installation.CompanionEnumerator = () => null;
                Check("a null answer is treated as no answer",
                    !Installation.ScanInstalledCompanions().Answered);
            }
            finally
            {
                InstallPaths.RootOverride = savedRoot;
                Installation.CompanionEnumerator = savedEnumerator;
                Installation.CloseWaitMilliseconds = savedWait;
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
            }
        }
        // -- the installer's own completion logic --------------------------------

        /// <summary>
        /// Whether a finished piece of background work still describes what
        /// the installer window is doing, and what it is allowed to do about
        /// it.
        ///
        /// This is <see cref="WizardState"/>, which is what
        /// <see cref="InstallerForm"/> actually uses. An earlier version of
        /// this test drove the companion's SetupState instead -- a similar
        /// class in a different window -- so the installer's own logic had no
        /// test at all.
        /// </summary>
        private static void WizardCompletionTests()
        {
            Section("Installer completion: stale results, cancellation and the way back");

            // A result from a check that has since been replaced.
            WizardState superseded = new WizardState();
            int first = superseded.Begin(new DiscoveryCancel());
            int second = superseded.Begin(new DiscoveryCancel());
            Check("a superseded completion is refused",
                superseded.Complete(first) == Completion.Stale);
            Check("and it does not clear the newer operation's working flag",
                superseded.Working);
            Check("the newer completion is accepted",
                superseded.Complete(second) == Completion.Accepted);
            Check("and only then is the window idle", !superseded.Working);
            Check("a second completion for the same operation is refused too",
                superseded.Complete(second) == Completion.Stale);

            // Stop, and then the long wait while the work winds up. This is
            // the case that left the wizard with every button disabled: Stop
            // used to end the operation on the spot, so the completion that
            // restores the controls was thrown away as stale and the code that
            // puts them back never ran.
            WizardState stopping = new WizardState();
            DiscoveryCancel token = new DiscoveryCancel();
            int running = stopping.Begin(token);
            stopping.Cancel();
            Check("pressing Stop cancels the token the work is watching", token.IsCancelled);
            Check("and records that stopping was asked for", stopping.CancellationRequested);
            Check("the operation is still the current one while it winds up",
                stopping.Generation == running);
            Check("and the window is still working, so closing still waits for the worker",
                stopping.Working);
            Check("a progress update from it is still current", stopping.IsCurrent(running));

            Completion cancelled = stopping.Complete(running);
            Check("its eventual completion is not thrown away", cancelled != Completion.Stale);
            Check("it is delivered as cancelled, so nothing is saved or announced",
                cancelled == Completion.Cancelled);
            Check("and the window is finally idle, with its controls to restore",
                !stopping.Working);
            Check("a progress update arriving after that is refused",
                !stopping.IsCurrent(running));

            // Retry: the next operation behaves as though nothing happened.
            DiscoveryCancel retryToken = new DiscoveryCancel();
            int retry = stopping.Begin(retryToken);
            Check("a retry starts cleanly", stopping.Working && !stopping.CancellationRequested);
            Check("and its completion is accepted",
                stopping.Complete(retry) == Completion.Accepted);

            // Success queued for the UI thread, Stop pressed before it is
            // applied. The work succeeded; the instruction was withdrawn.
            WizardState withdrawn = new WizardState();
            int queued = withdrawn.Begin(new DiscoveryCancel());
            withdrawn.Cancel();
            Check("a success queued before Stop is not applied",
                withdrawn.Complete(queued) == Completion.Cancelled);
            Check("and the window is not left believing work is still running",
                !withdrawn.Working);

            // The same withdrawal expressed through the token alone, which is
            // how cancellation reaches the background work itself.
            WizardState tokenOnly = new WizardState();
            DiscoveryCancel outside = new DiscoveryCancel();
            int generation = tokenOnly.Begin(outside);
            outside.Cancel();
            Check("a cancelled token refuses its own operation's success",
                tokenOnly.Complete(generation) == Completion.Cancelled);

            // Cancelling when nothing is running changes nothing.
            WizardState idle = new WizardState();
            idle.Cancel();
            Check("cancelling an idle wizard does nothing",
                !idle.Working && !idle.CancellationRequested && idle.Generation == 0);

            // And the window keeps no second copy of any of this. Two records
            // of "is work running" is how a stale result cleared a flag that
            // belonged to a newer one in the first place.
            Check("the installer window keeps no completion state of its own",
                typeof(InstallerForm).GetField("_working",
                    System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance) == null
                && typeof(InstallerForm).GetField("_generation",
                    System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance) == null);

            // The window's completion handlers carry the generation they
            // belong to, so a queued one can be recognised as stale at all.
            Check("the window's completion handlers carry their generation",
                Parameters(typeof(InstallerForm), "FinishPreparation") == 2
                && Parameters(typeof(InstallerForm), "ApplyEnvironment") == 2);
        }

        private static int Parameters(Type type, string method)
        {
            System.Reflection.MethodInfo found = type.GetMethod(method,
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            return found == null ? -1 : found.GetParameters().Length;
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
            // revision. The source it carries is exported from the revision
            // and is that revision exactly; the two programs carrying it were
            // compiled from the working tree and are not.
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
