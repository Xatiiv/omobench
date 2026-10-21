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

        public const string SourceArchiveHost = "https://codeload.github.com/Xatiiv/omobench/tar.gz/";

        /// <summary>
        /// Where a managed source copy lives: under the user's local app data,
        /// named by the revision, so two installer versions never fight over
        /// one directory and an older one can simply be deleted.
        /// </summary>
        /// <summary>
        /// Redirects the managed source location, so a test can exercise the
        /// preparation without creating directories in the user's profile.
        /// Null in normal use.
        /// </summary>
        internal static string SourceRootOverride = null;

        public static string SourceDirectory(string revision)
        {
            string stamp = string.IsNullOrEmpty(revision) ? "unknown" : Safe(revision);
            string root = string.IsNullOrEmpty(SourceRootOverride)
                ? Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "OmoBench", "source")
                : SourceRootOverride;
            return Path.Combine(root, stamp);
        }

        /// <summary>
        /// The managed Linux environment, inside the distribution rather than
        /// on /mnt: a virtual environment on DrvFs is slow and carries Windows
        /// permission semantics the Python tooling does not expect.
        /// </summary>
        public static string EnvironmentDirectory(string home)
        {
            return (home ?? "").TrimEnd('/') + "/.local/share/omobench/managed-env";
        }

        public static string EnvironmentInterpreter(string home)
        {
            return EnvironmentDirectory(home) + "/bin/python";
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

        // -- the programs this runs, all without a shell ----------------------

        public static List<string> BuildDownloadProgram(string revision, string archiveWslPath)
        {
            return new List<string>
            {
                "curl", "--fail", "--silent", "--show-error", "--location",
                "--output", archiveWslPath, SourceArchiveHost + Safe(revision),
            };
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
                "-e", sourceWslPath.TrimEnd('/') + "/cli[ui,runner]",
            };
        }

        public static List<string> BuildMakeDirectoryProgram(string wslPath)
        {
            return new List<string> { "mkdir", "-p", wslPath };
        }

        public static List<string> BuildHomeProgram() { return new List<string> { "printenv", "HOME" }; }

        public static List<string> BuildSystemPythonProgram() { return new List<string> { "which", "python3" }; }

        // -- running it -------------------------------------------------------

        /// <summary>
        /// Prepare the managed environment, reporting each stage as it goes.
        ///
        /// Nothing is reported as finished until the existing environment
        /// check passes against the pair that was just created: the stages
        /// above it only describe what was attempted.
        /// </summary>
        public static PreparationResult Prepare(
            string distribution, BuildInfo build, DiscoveryCancel cancel, Action<PreparationResult> progress)
        {
            PreparationResult result = new PreparationResult();
            PreparationStage locate = Add(result, "Locate the Linux environment");
            PreparationStage fetch = Add(result, "Download the OmoBench source (pinned revision)");
            PreparationStage create = Add(result, "Create an isolated Python environment");
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
            try
            {
                Directory.CreateDirectory(sourceWindows);
            }
            catch (Exception error)
            {
                return Fail(result, fetch, "Could not create " + sourceWindows, error.Message);
            }
            if (!CommandLine.TryToWslPath(sourceWindows, out sourceWsl))
            {
                return Fail(result, fetch, "WSL cannot reach " + sourceWindows,
                    "The managed source must be on a local drive.");
            }
            archiveWsl = home.TrimEnd('/') + "/.omobench-source.tar.gz";

            if (string.IsNullOrEmpty(build.Revision))
            {
                return Fail(result, fetch, "This installer does not know which revision it was built from.",
                    "Use a released installer, or set up an existing checkout under Advanced settings.");
            }

            ProcessResult download = Run(distribution, BuildDownloadProgram(build.Revision, archiveWsl), 300000, cancel);
            if (Stopped(cancel, result, fetch)) { return result; }
            if (download == null || download.TimedOut || download.ExitCode != 0)
            {
                return Fail(result, fetch, "The source could not be downloaded.",
                    "Revision " + build.ShortRevision + "\r\n"
                    + Describe(download)
                    + "\r\n\r\nCheck the machine's internet connection and try again.");
            }
            ProcessResult extract = Run(distribution,
                BuildExtractProgram(archiveWsl, sourceWsl), 300000, cancel);
            if (Stopped(cancel, result, fetch)) { return result; }
            if (extract == null || extract.TimedOut || extract.ExitCode != 0)
            {
                return Fail(result, fetch, "The downloaded source could not be unpacked.", Describe(extract));
            }
            fetch.State = StageState.Done;
            fetch.Message = "Revision " + build.ShortRevision + " in " + sourceWindows;
            Report(progress, result);

            // 3. An isolated environment, separate from anything already here.
            create.State = StageState.Running;
            Report(progress, result);
            string environment = EnvironmentDirectory(home);
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
            Report(progress, result);
            ProcessResult pip = Run(distribution,
                BuildInstallProgram(EnvironmentInterpreter(home), sourceWsl), 900000, cancel);
            if (Stopped(cancel, result, install)) { return result; }
            if (pip == null || pip.TimedOut || pip.ExitCode != 0)
            {
                return Fail(result, install, "OmoBench could not be installed into the environment.",
                    Describe(pip));
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
                PythonWslPath = EnvironmentInterpreter(home),
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

            verify.State = StageState.Done;
            verify.Message = check.Summary;
            result.Ok = true;
            result.Configuration = configuration;
            result.Summary = "OmoBench is ready.";
            result.Detail = check.Detail;
            Report(progress, result);
            return result;
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
