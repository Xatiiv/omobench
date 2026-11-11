using System;
using System.Collections.Generic;
using System.IO;
using OmoBench.Companion;

namespace OmoBench.Installer
{
    public enum StageState { Pending, Running, Done, Failed, Cancelled }

    public sealed class PreparationStage
    {
        public string Name;
        public StageState State = StageState.Pending;
        public string Message;

        public PreparationStage(string name) { Name = name; }

        public override string ToString()
        {
            string mark;
            switch (State)
            {
                case StageState.Done: mark = "[done]  "; break;
                case StageState.Running: mark = "[ ... ] "; break;
                case StageState.Failed: mark = "[failed]"; break;
                case StageState.Cancelled: mark = "[stopped]"; break;
                default: mark = "[      ]"; break;
            }
            return mark + " " + Name + (string.IsNullOrEmpty(Message) ? "" : "\r\n         " + Message);
        }
    }

    public sealed class PreparationResult
    {
        public bool Ok;
        public bool Cancelled;
        public string Summary;
        public string Detail;
        public Configuration Configuration;
        public readonly List<PreparationStage> Stages = new List<PreparationStage>();
    }

    /// <summary>
    /// Preparing an OmoBench-managed environment, for a machine that has WSL2
    /// but no OmoBench.
    ///
    /// Two boundaries define this. It is *managed*: everything it creates
    /// lives under paths this installer owns, so an existing developer
    /// checkout and an existing Python environment are never written to,
    /// upgraded, or repointed -- discovery looks for those first, and this
    /// only runs when there is nothing to reuse. And it is *pinned*: the
    /// source comes from the exact revision this installer was built from, and
    /// the runner from the version that revision pins, so what gets installed
    /// is the thing that was tested rather than whatever is newest today.
    ///
    /// The source is placed on the Windows side even though the work happens
    /// in Linux. That is deliberate: the companion's existing contract is that
    /// a checkout is a Windows folder on a local drive which WSL reads through
    /// /mnt, and this change does not alter it.
    /// </summary>
    public static class ManagedEnvironment
    {
        /// <summary>Replaced by tests only.</summary>
        internal static Func<string, int, DiscoveryCancel, ProcessResult> WslRunner =
            (arguments, timeout, cancel) => Wsl.Run(
                Wsl.ExecutablePath, arguments, timeout, null,
                cancel == null ? (Func<bool>)null : () => cancel.IsCancelled);

        /// <summary>Replaced by tests only.</summary>
        internal static Func<Configuration, EnvironmentCheckResult> Validator = EnvironmentCheck.Run;

        /// <summary>
        /// Every destination the preparation writes to is redirectable, so a
        /// test can exercise the real thing against throwaway locations rather
        /// than the places a person's own installation lives. All are null in
        /// normal use.
        /// </summary>
        internal static string SourceRootOverride = null;

        /// <summary>Replaces ~/.local/share/omobench as the environment root.</summary>
        internal static string EnvironmentRootOverride = null;

        /// <summary>
        /// Where the embedded archive is written before it is unpacked. A
        /// Windows directory: the installer writes the file, and WSL reads it
        /// through /mnt, so nothing has to be created inside the distribution
        /// before the source exists.
        /// </summary>
        internal static string ArchiveDirectoryOverride = null;

        /// <summary>
        /// A directory this preparation created, marked so a later run can
        /// tell its own working area from somebody else's data.
        /// </summary>
        public const string OwnershipMarkerName = ".omobench-managed";

        /// <summary>
        /// Written into a managed source directory, naming the revision it
        /// holds. The directory name says the same thing, but a name is a
        /// label somebody can change and this is the thing the panel reads to
        /// report what it is running.
        /// </summary>
        public const string RevisionMarkerName = ".omobench-source-revision";

        /// <summary>
        /// The root every managed source copy sits directly under. A checkout
        /// elsewhere is somebody's own and is never upgraded, moved or
        /// written to.
        /// </summary>
        public static string SourceRoot()
        {
            return string.IsNullOrEmpty(SourceRootOverride)
                ? Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "OmoBench", "source")
                : SourceRootOverride;
        }

        /// <summary>
        /// Is this checkout one this installer created, or the person's own?
        ///
        /// The distinction decides everything about an upgrade: a managed
        /// source may be replaced by a newer one, and a developer checkout may
        /// not be touched at all. Judged by location, because that is what
        /// this installer actually controls.
        /// </summary>
        public static bool IsManagedSource(string checkoutWindowsPath)
        {
            if (string.IsNullOrWhiteSpace(checkoutWindowsPath)) { return false; }
            try
            {
                string root = Path.GetFullPath(SourceRoot()).TrimEnd(
                    Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                string full = Path.GetFullPath(checkoutWindowsPath).TrimEnd(
                    Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                string parent = Path.GetDirectoryName(full);
                return parent != null && string.Equals(
                    parent.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                    root, StringComparison.OrdinalIgnoreCase);
            }
            catch (ArgumentException) { return false; }
            catch (NotSupportedException) { return false; }
            catch (PathTooLongException) { return false; }
        }

        /// <summary>
        /// Which revision a managed source holds, or null when that cannot be
        /// established. The marker is read first and the directory name is the
        /// fallback; a guess is never returned for a checkout that is not
        /// managed, because this installer does not know what is in one.
        /// </summary>
        public static string RevisionOf(string checkoutWindowsPath)
        {
            if (!IsManagedSource(checkoutWindowsPath)) { return null; }
            try
            {
                string marker = Path.Combine(checkoutWindowsPath, RevisionMarkerName);
                if (File.Exists(marker))
                {
                    string recorded = File.ReadAllText(marker).Trim();
                    if (LooksLikeRevision(recorded)) { return recorded; }
                }
                string name = Path.GetFileName(checkoutWindowsPath.TrimEnd(
                    Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
                return LooksLikeRevision(name) ? name : null;
            }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
        }

        private static bool LooksLikeRevision(string value)
        {
            if (string.IsNullOrEmpty(value) || value.Length < 7 || value.Length > 40) { return false; }
            foreach (char character in value)
            {
                bool hex = (character >= '0' && character <= '9')
                    || (character >= 'a' && character <= 'f')
                    || (character >= 'A' && character <= 'F');
                if (!hex) { return false; }
            }
            return true;
        }

        /// <summary>
        /// Where a managed source copy lives: under the user's local app data,
        /// named by the revision, so two installer versions never fight over
        /// one directory and an older one can simply be deleted.
        /// </summary>
        public static string SourceDirectory(string revision)
        {
            string stamp = string.IsNullOrEmpty(revision) ? "unknown" : Safe(revision);
            return Path.Combine(SourceRoot(), stamp);
        }

        /// <summary>
        /// The managed Linux environment, inside the distribution rather than
        /// on /mnt: a virtual environment on DrvFs is slow and carries Windows
        /// permission semantics the Python tooling does not expect.
        /// </summary>
        public static string EnvironmentRoot(string home)
        {
            return string.IsNullOrEmpty(EnvironmentRootOverride)
                ? (home ?? "").TrimEnd('/') + "/.local/share/omobench"
                : EnvironmentRootOverride.TrimEnd('/');
        }

        public static string EnvironmentDirectory(string home)
        {
            return EnvironmentRoot(home) + "/managed-env";
        }

        /// <summary>
        /// The environment for one revision, used when upgrading.
        ///
        /// An upgrade must not write into the environment that currently
        /// works: the new one has to be built and checked while the old one is
        /// still there to go back to, and a pip install that fails halfway
        /// through the only environment leaves nothing to return to. Each
        /// revision therefore gets its own, and nothing is removed.
        /// </summary>
        public static string EnvironmentDirectory(string home, string revision)
        {
            if (string.IsNullOrEmpty(revision)) { return EnvironmentDirectory(home); }
            return EnvironmentRoot(home) + "/env/" + Safe(revision);
        }

        public static string EnvironmentInterpreter(string home, string revision)
        {
            return EnvironmentDirectory(home, revision) + "/bin/python";
        }

        public static string EnvironmentInterpreter(string home)
        {
            return EnvironmentDirectory(home) + "/bin/python";
        }

        /// <summary>
        /// Where the embedded archive is written before unpacking, as a
        /// Windows path.
        /// </summary>
        public static string ArchiveDirectory()
        {
            return string.IsNullOrEmpty(ArchiveDirectoryOverride)
                ? Path.Combine(Path.GetTempPath(), "omobench-runtime-source")
                : ArchiveDirectoryOverride;
        }

        /// <summary>
        /// A fresh archive path for one attempt.
        ///
        /// Unique per attempt on purpose: a fixed name is shared state between
        /// a run that is still going and one that has just started, and
        /// between this user and anyone else writing to the same temporary
        /// directory. Each attempt unpacks the file it wrote itself.
        /// </summary>
        public static string NewArchivePath()
        {
            return Path.Combine(ArchiveDirectory(),
                "omobench-runtime-" + Guid.NewGuid().ToString("N") + ".tar.gz");
        }

        public static List<string> BuildExistsProgram(string wslPath)
        {
            return new List<string> { "test", "-e", wslPath };
        }

        public static List<string> BuildListDirectoryProgram(string wslPath)
        {
            return new List<string> { "ls", "-A", wslPath };
        }

        public static List<string> BuildTouchProgram(string wslPath)
        {
            return new List<string> { "touch", wslPath };
        }

        public static string LinuxOwnershipMarker(string home)
        {
            return EnvironmentRoot(home) + "/" + OwnershipMarkerName;
        }

        private static string Safe(string revision)
        {
            System.Text.StringBuilder clean = new System.Text.StringBuilder();
            foreach (char character in revision)
            {
                if (char.IsLetterOrDigit(character)) { clean.Append(character); }
                if (clean.Length >= 40) { break; }
            }
            return clean.Length == 0 ? "unknown" : clean.ToString();
        }

        public static List<string> BuildExtractProgram(string archiveWslPath, string destinationWslPath)
        {
            return new List<string>
            {
                "tar", "-xzf", archiveWslPath, "-C", destinationWslPath, "--strip-components=1",
            };
        }

        public static List<string> BuildCreateEnvironmentProgram(string systemPython, string environmentWslPath)
        {
            return new List<string> { systemPython, "-m", "venv", environmentWslPath };
        }

        /// <summary>
        /// Install OmoBench from the managed source, with the pinned runner
        /// extra. Editable, pointed at the managed checkout, so the companion's
        /// existing "imports from the selected checkout" check passes for the
        /// managed pair and for nothing else.
        /// </summary>
        public static List<string> BuildInstallProgram(string environmentInterpreter, string sourceWslPath)
        {
            return new List<string>
            {
                environmentInterpreter, "-m", "pip", "install",
                "--disable-pip-version-check", "--no-input",
                "--constraint", sourceWslPath.TrimEnd('/') + "/cli/src/omobench/runner-constraints.txt",
                "-e", sourceWslPath.TrimEnd('/') + "/cli[ui,runner]",
            };
        }

        // The runner is an HTTP client. Its tensor library does not need CUDA,
        // even when the model server performs GPU inference. A second pip
        // operation is deliberate: do not mix PyPI and the CPU wheel index.
        public static List<string> BuildCpuInstallProgram(string environmentInterpreter)
        {
            return new List<string>
            {
                environmentInterpreter, "-m", "pip", "install",
                "--disable-pip-version-check", "--no-input", "--only-binary=:all:",
                "--index-url", "https://download.pytorch.org/whl/cpu",
                "torch==2.15.0+cpu",
            };
        }

        public static List<string> BuildDependencyCheckProgram(string environmentInterpreter)
        {
            return new List<string> { environmentInterpreter, "-m", "pip", "check" };
        }

        public static List<string> BuildMakeDirectoryProgram(string wslPath)
        {
            return new List<string> { "mkdir", "-p", wslPath };
        }

        public static List<string> BuildHomeProgram() { return new List<string> { "printenv", "HOME" }; }

        public static List<string> BuildSystemPythonProgram() { return new List<string> { "which", "python3" }; }

        // -- running it -------------------------------------------------------

        /// <summary>
        /// Prepare the managed environment for a machine that has none.
        ///
        /// Nothing is reported as finished until the existing environment
        /// check passes against the pair that was just created: the stages
        /// above it only describe what was attempted.
        /// </summary>
        public static PreparationResult Prepare(
            string distribution, BuildInfo build, DiscoveryCancel cancel, Action<PreparationResult> progress)
        {
            return Build(distribution, build, cancel, progress, upgrade: false);
        }

        /// <summary>
        /// Build this installer's revision beside an existing managed one,
        /// without touching it.
        ///
        /// The only difference from a first preparation is where the Python
        /// environment goes: an upgrade gets its own, named by revision, so
        /// the environment that currently works is still there if this fails
        /// or is stopped. Nothing is switched here -- this returns a
        /// configuration and the caller decides, after the check passed,
        /// whether to start using it.
        /// </summary>
        public static PreparationResult Upgrade(
            string distribution, BuildInfo build, DiscoveryCancel cancel, Action<PreparationResult> progress)
        {
            return Build(distribution, build, cancel, progress, upgrade: true);
        }

        private static PreparationResult Build(
            string distribution, BuildInfo build, DiscoveryCancel cancel,
            Action<PreparationResult> progress, bool upgrade)
        {
            // Which environment this writes into. Null means the single
            // managed environment a first install creates; a revision means
            // one of its own, which is what keeps an upgrade reversible.
            string scope = upgrade ? (build == null ? null : build.Revision) : null;
            PreparationResult result = new PreparationResult();
            PreparationStage locate = Add(result, "Locate the Linux environment");
            PreparationStage fetch = Add(result, "Unpack the bundled OmoBench source (pinned revision)");
            PreparationStage create = Add(result, upgrade
                ? "Create an isolated Python environment for the new revision"
                : "Create an isolated Python environment");
            PreparationStage install = Add(result, "Install OmoBench and the pinned benchmark runner");
            PreparationStage verify = Add(result, "Check the finished environment");

            Report(progress, result);

            if (string.IsNullOrWhiteSpace(distribution))
            {
                locate.State = StageState.Failed;
                locate.Message = "No WSL2 distribution was selected.";
                result.Summary = "There is no WSL2 distribution to prepare.";
                return result;
            }

            // 1. Where in Linux are we, and is there a Python to build on?
            locate.State = StageState.Running;
            Report(progress, result);
            string home = FirstLine(Run(distribution, BuildHomeProgram(), 20000, cancel));
            if (Stopped(cancel, result, locate)) { return result; }
            if (string.IsNullOrEmpty(home))
            {
                return Fail(result, locate, "Could not read the home directory of " + distribution + ".",
                    "The distribution may be stopped, or still finishing its first-run setup. "
                    + "Open it once from the Start menu, then try again.");
            }
            string systemPython = FirstLine(Run(distribution, BuildSystemPythonProgram(), 20000, cancel));
            if (Stopped(cancel, result, locate)) { return result; }
            if (string.IsNullOrEmpty(systemPython))
            {
                return Fail(result, locate, "This distribution has no python3.",
                    "Install it from inside " + distribution + ":\r\n"
                    + "    sudo apt update && sudo apt install -y python3 python3-venv\r\n\r\n"
                    + "That needs your Linux password, so it is not something this installer can do "
                    + "for you.");
            }
            locate.State = StageState.Done;
            locate.Message = "Linux home " + home + ", using " + systemPython;
            Report(progress, result);

            // 2. Fetch the pinned source onto the Windows side.
            fetch.State = StageState.Running;
            Report(progress, result);
            string sourceWindows = SourceDirectory(build.Revision);
            string sourceWsl, archiveWsl;

            // Only write into a directory that is empty or that a previous
            // preparation created. Unpacking an archive over somebody's
            // existing folder because its name happened to match would destroy
            // data this program has no claim to.
            string ownership = ClaimDirectory(sourceWindows);
            if (ownership != null)
            {
                return Fail(result, fetch, "The managed source location is already in use.", ownership);
            }
            if (!CommandLine.TryToWslPath(sourceWindows, out sourceWsl))
            {
                return Fail(result, fetch, "WSL cannot reach " + sourceWindows,
                    "The managed source must be on a local drive.");
            }
            if (string.IsNullOrEmpty(build.Revision))
            {
                return Fail(result, fetch, "This installer does not know which revision it was built from.",
                    "Use a released installer, or set up an existing checkout under Advanced settings.");
            }

            // The source travels inside the installer, and is checked against
            // the checksum recorded when it was built before anything unpacks
            // it.
            string archiveWindows = NewArchivePath();
            string payloadFailure;
            if (!RuntimePayload.TryWrite(archiveWindows, out payloadFailure))
            {
                return Fail(result, fetch, "The bundled OmoBench source could not be used.", payloadFailure);
            }
            if (Stopped(cancel, result, fetch)) { return result; }

            if (!CommandLine.TryToWslPath(archiveWindows, out archiveWsl))
            {
                return Fail(result, fetch, "WSL cannot reach " + archiveWindows,
                    "The archive must be unpacked from a local drive.");
            }

            ProcessResult extract = Run(distribution,
                BuildExtractProgram(archiveWsl, sourceWsl), 300000, cancel);
            TryDelete(archiveWindows);
            if (Stopped(cancel, result, fetch)) { return result; }
            if (extract == null || extract.TimedOut || extract.ExitCode != 0)
            {
                return Fail(result, fetch, "The bundled source could not be unpacked.", Describe(extract));
            }
            // What came out of the archive, not just what went into it. A
            // payload built from an allowlist that matched less than it should
            // have is still a valid, correctly checksummed archive; the only
            // way to know it carries a working OmoBench is to look.
            string contentsFailure;
            if (!RuntimeManifest.Validate(sourceWindows, out contentsFailure))
            {
                return Fail(result, fetch, "The bundled OmoBench source is incomplete.", contentsFailure);
            }
            if (Stopped(cancel, result, fetch)) { return result; }

            // The directory is named by revision, but a name is a label. This
            // is what the panel reads to say which source it is running, so it
            // is written where the source is rather than inferred later.
            try
            {
                File.WriteAllText(Path.Combine(sourceWindows, RevisionMarkerName),
                    build.Revision + "\n");
            }
            catch (IOException) { /* The panel falls back to the directory name. */ }
            catch (UnauthorizedAccessException) { }

            fetch.State = StageState.Done;
            fetch.Message = "Revision " + build.ShortRevision + " unpacked into " + sourceWindows
                + (build.SourceClean ? "" :
                    "\r\n         Note: this source is revision " + build.ShortRevision
                    + " exactly, but the installer and companion around it were built from a "
                    + "modified working tree and are not.");
            Report(progress, result);

            // 3. An isolated environment, separate from anything already here.
            create.State = StageState.Running;
            Report(progress, result);
            string environment = EnvironmentDirectory(home, scope);

            // The Windows source directory was already ownership-checked; the
            // Linux side needs the same guarantee before a venv is created or
            // pip is pointed at it. Writing into a directory somebody else
            // made, because its name matched, is the same mistake wherever the
            // filesystem lives.
            string linuxOwnership = ClaimLinuxRoot(distribution, home, cancel);
            if (Stopped(cancel, result, create)) { return result; }
            if (linuxOwnership != null)
            {
                return Fail(result, create, "The managed Linux location is already in use.", linuxOwnership);
            }
            Run(distribution, BuildMakeDirectoryProgram(environment.Substring(0, environment.LastIndexOf('/'))),
                30000, cancel);
            ProcessResult venv = Run(distribution,
                BuildCreateEnvironmentProgram(systemPython, environment), 180000, cancel);
            if (Stopped(cancel, result, create)) { return result; }
            if (venv == null || venv.TimedOut || venv.ExitCode != 0)
            {
                return Fail(result, create, "The isolated Python environment could not be created.",
                    Describe(venv)
                    + "\r\n\r\nOn Ubuntu this usually means the venv package is missing. From inside "
                    + distribution + ":\r\n    sudo apt update && sudo apt install -y python3-venv\r\n\r\n"
                    + "That needs your Linux password, so it is not something this installer can do "
                    + "for you.");
            }
            create.State = StageState.Done;
            create.Message = environment;
            Report(progress, result);

            // 4. Install, pinned.
            install.State = StageState.Running;
            install.Message = "Installing the CPU-only runner tensor library (model-server GPU use is unchanged).";
            Report(progress, result);
            ProcessResult cpu = Run(distribution,
                BuildCpuInstallProgram(EnvironmentInterpreter(home, scope)), 2700000, cancel);
            if (Stopped(cancel, result, install)) { return result; }
            if (cpu == null || cpu.TimedOut || cpu.ExitCode != 0)
            {
                return Fail(result, install, "The CPU-only runner dependency could not be installed.",
                    Describe(cpu) + "\r\n\r\nThis setup requires a compatible torch 2.15.0+cpu wheel. "
                    + "Check your connection and Python/platform compatibility. No CUDA fallback "
                    + "or source compilation was attempted; existing models were not changed.");
            }
            install.Message = "Installing OmoBench with constrained runner dependencies.";
            Report(progress, result);
            // The pinned runner pulls a large dependency set, and this is the
            // one step that is mostly waiting on a download. Fifteen minutes
            // was not enough on an ordinary connection -- a first-time install
            // failed here having already done everything else correctly -- so
            // the budget matches the work rather than an optimistic guess.
            ProcessResult pip = Run(distribution,
                BuildInstallProgram(EnvironmentInterpreter(home, scope), sourceWsl), 2700000, cancel);
            if (Stopped(cancel, result, install)) { return result; }
            if (pip == null || pip.TimedOut || pip.ExitCode != 0)
            {
                return Fail(result, install, "OmoBench could not be installed into the environment.",
                    Describe(pip)
                    + (pip != null && pip.TimedOut
                        ? "\r\n\r\nThe benchmark runner's dependencies are a large download. On a slow "
                          + "or interrupted connection this can run out of time; the work already done "
                          + "is kept, so trying again resumes from pip's cache rather than starting over."
                        : ""));
            }
            ProcessResult dependencies = Run(distribution,
                BuildDependencyCheckProgram(EnvironmentInterpreter(home, scope)), 60000, cancel);
            if (Stopped(cancel, result, install)) { return result; }
            if (dependencies == null || dependencies.TimedOut || dependencies.ExitCode != 0)
            {
                return Fail(result, install, "The installed dependencies are inconsistent.",
                    Describe(dependencies) + "\r\n\r\nSetup has not marked this environment ready. "
                    + "Existing packages have not been removed automatically.");
            }
            install.State = StageState.Done;
            install.Message = "Installed from the managed source with the pinned runner";
            Report(progress, result);

            // 5. The only stage that decides whether this worked.
            verify.State = StageState.Running;
            Report(progress, result);
            Configuration configuration = new Configuration
            {
                Distribution = distribution,
                CheckoutWindowsPath = sourceWindows,
                PythonWslPath = EnvironmentInterpreter(home, scope),
                Port = Configuration.DefaultPort,
            };
            EnvironmentCheckResult check;
            try { check = Validator(configuration); }
            catch (Exception error)
            {
                return Fail(result, verify, "The environment check could not be run.", error.Message);
            }
            if (check == null || !check.Ok)
            {
                return Fail(result, verify,
                    check == null ? "The environment check returned nothing." : check.Summary,
                    check == null ? null : check.Detail);
            }

            // Validation is the slowest step and cannot itself be interrupted,
            // so a cancellation arriving during it would otherwise be noticed
            // only after success had already been decided. Reporting ready
            // here -- and letting the caller save a configuration -- would act
            // on an instruction the person had already withdrawn.
            if (Stopped(cancel, result, verify)) { return result; }

            verify.State = StageState.Done;
            verify.Message = check.Summary;
            result.Ok = true;
            result.Configuration = configuration;
            result.Summary = upgrade
                ? "The new revision is ready to switch to."
                : "OmoBench is ready.";
            result.Detail = check.Detail;
            Report(progress, result);
            return result;
        }

        /// <summary>
        /// What an import of previous runs did, or would do.
        /// </summary>
        public sealed class RunImport
        {
            /// <summary>Run directories copied into the new source.</summary>
            public int Copied;

            /// <summary>Present in both, and therefore left exactly as they were.</summary>
            public readonly List<string> Skipped = new List<string>();

            /// <summary>Why nothing could be done, when that is the answer.</summary>
            public string Failure;

            public bool Ok { get { return Failure == null; } }
        }

        /// <summary>
        /// Where a checkout's run evidence lives.
        ///
        /// The panel writes runs beside the source it is running from, so a
        /// new managed source starts with none. The previous ones are still
        /// on disk, in the previous source directory, and this is how they
        /// come across.
        /// </summary>
        public static string RunsDirectory(string checkoutWindowsPath)
        {
            return string.IsNullOrWhiteSpace(checkoutWindowsPath)
                ? null : Path.Combine(checkoutWindowsPath, ".omobench-runs");
        }

        /// <summary>
        /// Count what an import would bring across, without copying anything.
        /// </summary>
        public static RunImport SurveyRuns(string fromCheckout, string toCheckout)
        {
            return CopyRuns(fromCheckout, toCheckout, dryRun: true);
        }

        /// <summary>
        /// Copy previous run evidence into the new managed source, never over
        /// anything already there.
        ///
        /// Evidence is the point of this program, so the rule is simple and
        /// absolute: a run directory that exists at the destination is left
        /// untouched and reported as skipped. Nothing is merged, nothing is
        /// replaced, and the originals stay where they are either way -- this
        /// copies, so the previous source remains a complete record on its
        /// own.
        /// </summary>
        public static RunImport CopyRuns(string fromCheckout, string toCheckout, bool dryRun)
        {
            RunImport outcome = new RunImport();
            string from = RunsDirectory(fromCheckout);
            string to = RunsDirectory(toCheckout);
            if (from == null || to == null)
            {
                outcome.Failure = "Both the previous and the new source have to be known.";
                return outcome;
            }
            if (!Directory.Exists(from))
            {
                return outcome;   // Nothing to bring across is not a failure.
            }
            try
            {
                foreach (string directory in Directory.GetDirectories(from, "*",
                    SearchOption.TopDirectoryOnly))
                {
                    string name = Path.GetFileName(directory);
                    string destination = Path.Combine(to, name);
                    if (Directory.Exists(destination) || File.Exists(destination))
                    {
                        outcome.Skipped.Add(name);
                        continue;
                    }
                    if (!dryRun) { CopyTree(directory, destination); }
                    outcome.Copied++;
                }
            }
            catch (IOException error) { outcome.Failure = error.Message; }
            catch (UnauthorizedAccessException error) { outcome.Failure = error.Message; }
            return outcome;
        }

        private static void CopyTree(string from, string to)
        {
            Directory.CreateDirectory(to);
            foreach (string file in Directory.GetFiles(from))
            {
                // overwrite: false. A destination file is never replaced, even
                // inside a directory this call created.
                File.Copy(file, Path.Combine(to, Path.GetFileName(file)), false);
            }
            foreach (string directory in Directory.GetDirectories(from))
            {
                CopyTree(directory, Path.Combine(to, Path.GetFileName(directory)));
            }
        }

        /// <summary>
        /// Take ownership of the managed Linux root, or explain why not.
        ///
        /// Returns null when it is ours to use: absent, already marked by a
        /// previous attempt (so a retry continues), or empty. Anything else is
        /// somebody's data and is left exactly as it is -- nothing is moved,
        /// emptied or overwritten to make room.
        /// </summary>
        /// <summary>
        /// What a `test -e` actually established.
        ///
        /// test answers 0 for present and 1 for absent, and nothing else.
        /// Anything else came from the shell or the distribution rather than
        /// from test: 127 is "command not found", 2 is a usage error, and a
        /// stopped distribution can produce others. None of them mean the
        /// directory is absent, so none of them may be read as permission to
        /// create one.
        /// </summary>
        private enum Presence { Exists, Absent, Unknown }

        private static Presence Probe(string distribution, string path, DiscoveryCancel cancel)
        {
            ProcessResult result = Run(distribution, BuildExistsProgram(path), 20000, cancel);
            if (result == null || result.TimedOut) { return Presence.Unknown; }
            if (result.ExitCode == 0) { return Presence.Exists; }
            if (result.ExitCode == 1) { return Presence.Absent; }
            return Presence.Unknown;
        }

        internal static string ClaimLinuxRoot(string distribution, string home, DiscoveryCancel cancel)
        {
            string root = EnvironmentRoot(home);
            string marker = LinuxOwnershipMarker(home);

            Presence rootPresence = Probe(distribution, root, cancel);
            if (rootPresence == Presence.Unknown)
            {
                return root + "\r\n\r\nWhether that directory exists could not be determined, so it "
                    + "was left alone.";
            }

            if (rootPresence == Presence.Exists)
            {
                Presence markerPresence = Probe(distribution, marker, cancel);
                if (markerPresence == Presence.Exists)
                {
                    return null;    // a previous attempt's working area; retry is fine
                }
                if (markerPresence == Presence.Unknown)
                {
                    return root + "\r\n\r\nWhether OmoBench created that directory could not be "
                        + "determined, so it was left alone.";
                }

                ProcessResult listing = Run(distribution, BuildListDirectoryProgram(root), 20000, cancel);
                if (listing == null || listing.TimedOut || listing.ExitCode != 0)
                {
                    return root + "\r\n\r\nIts contents could not be read, so it was left alone.";
                }
                if (!string.IsNullOrWhiteSpace(listing.StandardOutput))
                {
                    return root + "\r\n\r\nThis directory inside " + distribution + " already contains "
                        + "files and was not created by OmoBench, so it will not be written into. "
                        + "Move or remove it if you no longer need what is in it, and try again.";
                }
            }

            Run(distribution, BuildMakeDirectoryProgram(root), 20000, cancel);
            ProcessResult marked = Run(distribution, BuildTouchProgram(marker), 20000, cancel);
            if (marked == null || marked.TimedOut || marked.ExitCode != 0)
            {
                return root + "\r\n\r\nThat directory could not be created inside " + distribution + ".";
            }
            return null;
        }

        /// <summary>
        /// Take ownership of a Windows directory, or explain why it cannot be
        /// taken.
        ///
        /// Returns null when the directory is ours to use -- newly created,
        /// already marked by a previous preparation, or empty. Otherwise it
        /// returns the reason, and the caller refuses rather than writing over
        /// whatever is there.
        /// </summary>
        internal static string ClaimDirectory(string windowsPath)
        {
            try
            {
                if (!Directory.Exists(windowsPath))
                {
                    Directory.CreateDirectory(windowsPath);
                    File.WriteAllText(Path.Combine(windowsPath, OwnershipMarkerName),
                        "Created by the OmoBench installer. Deleting this folder removes the managed copy.\r\n");
                    return null;
                }
                if (File.Exists(Path.Combine(windowsPath, OwnershipMarkerName)))
                {
                    return null;   // a previous preparation's working area
                }
                if (Directory.GetFileSystemEntries(windowsPath).Length == 0)
                {
                    File.WriteAllText(Path.Combine(windowsPath, OwnershipMarkerName),
                        "Created by the OmoBench installer. Deleting this folder removes the managed copy.\r\n");
                    return null;
                }
                return windowsPath + "\r\n\r\nThis folder already contains files and was not created by "
                    + "OmoBench, so it will not be written over. Move or remove it, or remove the folder "
                    + "if you no longer need what is in it, and try again.";
            }
            catch (Exception error)
            {
                return windowsPath + "\r\n\r\n" + error.Message;
            }
        }

        /// <summary>
        /// Remove the unpacked archive. Best effort: it is a copy of something
        /// the installer still carries, so failing to delete it is untidy
        /// rather than consequential.
        /// </summary>
        private static void TryDelete(string path)
        {
            try { if (File.Exists(path)) { File.Delete(path); } }
            catch (Exception) { }
        }

        private static PreparationStage Add(PreparationResult result, string name)
        {
            PreparationStage stage = new PreparationStage(name);
            result.Stages.Add(stage);
            return stage;
        }

        private static bool Stopped(DiscoveryCancel cancel, PreparationResult result, PreparationStage stage)
        {
            if (cancel == null || !cancel.IsCancelled) { return false; }
            stage.State = StageState.Cancelled;
            stage.Message = "Stopped at your request.";
            result.Cancelled = true;
            result.Summary = "Setup was stopped. Nothing further was changed.";
            return true;
        }

        private static PreparationResult Fail(
            PreparationResult result, PreparationStage stage, string summary, string detail)
        {
            stage.State = StageState.Failed;
            stage.Message = summary;
            result.Ok = false;
            result.Summary = summary;
            result.Detail = detail;
            return result;
        }

        private static string Describe(ProcessResult result)
        {
            if (result == null) { return "The command could not be run."; }
            if (result.TimedOut) { return "The command timed out."; }
            string output = Diagnostics.Redact((result.CombinedOutput ?? "").Trim());
            return "Exit code " + result.ExitCode + (output.Length == 0 ? "" : "\r\n" + output);
        }

        private static ProcessResult Run(
            string distribution, List<string> program, int timeout, DiscoveryCancel cancel)
        {
            try
            {
                return WslRunner(Wsl.BuildRunArguments(distribution, null, program), timeout, cancel);
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static string FirstLine(ProcessResult result)
        {
            if (result == null || result.TimedOut || result.ExitCode != 0) { return null; }
            foreach (string line in (result.StandardOutput ?? "").Replace("\r", "").Split('\n'))
            {
                string trimmed = line.Trim();
                if (trimmed.Length > 0) { return trimmed; }
            }
            return null;
        }

        private static void Report(Action<PreparationResult> progress, PreparationResult result)
        {
            if (progress != null)
            {
                try { progress(result); } catch (Exception) { }
            }
        }
    }
}
