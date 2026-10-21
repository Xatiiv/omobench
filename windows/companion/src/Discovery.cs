using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;

namespace OmoBench.Companion
{
    /// <summary>
    /// How a candidate was found. Shown to the person, so the wording is the
    /// explanation they read, not an internal label.
    /// </summary>
    public static class CandidateSource
    {
        public const string Saved = "your saved setup";
        public const string Executable = "next to this companion";
        public const string Project = "your project folders";
        public const string Conventional = "the usual location";
        public const string CheckoutVirtualEnv = "inside the checkout";
        public const string Path = "this distribution's PATH";
    }

    /// <summary>
    /// Bounds on everything discovery does. Searching a developer machine is
    /// open-ended by nature, so every dimension that could grow without limit
    /// -- depth, breadth, candidate count, and time -- is capped here rather
    /// than left to chance.
    /// </summary>
    public sealed class DiscoveryLimits
    {
        public int MaximumDirectoryDepth = 3;
        public int MaximumDirectoriesVisited = 3000;
        public int MaximumCheckouts = 8;
        public int MaximumInterpreters = 10;
        public int MaximumDistributions = 4;
        public int ProbeTimeoutMilliseconds = 30000;
        public int ListingTimeoutMilliseconds = 10000;
        public int OverallTimeoutMilliseconds = 120000;
    }

    /// <summary>
    /// Cooperative cancellation. Deliberately tiny: discovery checks it
    /// between steps, and the process runner polls it so a probe already in
    /// flight is ended rather than outliving the window that started it.
    /// </summary>
    public sealed class DiscoveryCancel
    {
        private volatile bool _cancelled;
        public void Cancel() { _cancelled = true; }
        public bool IsCancelled { get { return _cancelled; } }
    }

    public sealed class CheckoutCandidate
    {
        public string WindowsPath;
        public string WslPath;
        public string Source;
    }

    public sealed class InterpreterCandidate
    {
        public string WslPath;
        public string Source;
    }

    /// <summary>One distribution + checkout + interpreter, and its verdict.</summary>
    public sealed class InstallationCandidate
    {
        public string Distribution;
        public string CheckoutWindowsPath;
        public string PythonWslPath;
        public bool Ready;
        public string Summary;
        public string Detail;
        public string CheckoutSource;
        public string InterpreterSource;

        /// <summary>One readable line for a selection list.</summary>
        public override string ToString()
        {
            return Distribution + "  —  " + CheckoutWindowsPath;
        }
    }

    public sealed class DiscoveryReport
    {
        public readonly List<InstallationCandidate> Ready = new List<InstallationCandidate>();
        public readonly List<InstallationCandidate> Rejected = new List<InstallationCandidate>();
        public readonly List<CheckoutCandidate> Checkouts = new List<CheckoutCandidate>();
        public readonly List<InterpreterCandidate> Interpreters = new List<InterpreterCandidate>();
        public readonly List<string> Notes = new List<string>();
        public bool Cancelled;
        public bool TimedOut;

        /// <summary>
        /// What to tell someone when nothing was validated: what was found, and
        /// what is missing. Deliberately specific -- "no installation found" on
        /// its own gives a person nothing to act on.
        /// </summary>
        public string Explain()
        {
            List<string> lines = new List<string>();
            if (Cancelled) { lines.Add("Search cancelled."); }
            if (TimedOut) { lines.Add("The search ran out of time before checking everything."); }

            if (Checkouts.Count == 0)
            {
                lines.Add("No OmoBench checkout was found.");
                lines.Add("A checkout is a folder containing cli\\src\\omobench and benchmarks\\profiles.");
            }
            else
            {
                lines.Add("Checkouts found (" + Checkouts.Count.ToString(CultureInfo.InvariantCulture) + "):");
                foreach (CheckoutCandidate checkout in Checkouts)
                {
                    lines.Add("    " + checkout.WindowsPath + "   (" + checkout.Source + ")");
                }
            }

            if (Interpreters.Count == 0)
            {
                lines.Add("No Linux Python environment was found to try.");
            }
            else
            {
                lines.Add("Python environments tried (" + Interpreters.Count.ToString(CultureInfo.InvariantCulture) + "):");
                foreach (InterpreterCandidate interpreter in Interpreters)
                {
                    lines.Add("    " + interpreter.WslPath + "   (" + interpreter.Source + ")");
                }
            }

            if (Rejected.Count > 0)
            {
                lines.Add("");
                lines.Add("Why none of them was ready:");
                foreach (InstallationCandidate rejected in Rejected)
                {
                    lines.Add("  " + rejected.Distribution + " / " + rejected.PythonWslPath);
                    lines.Add("    " + rejected.Summary);
                    if (!string.IsNullOrEmpty(rejected.Detail))
                    {
                        foreach (string detail in rejected.Detail.Replace("\r", "").Split('\n'))
                        {
                            lines.Add("    " + detail);
                        }
                    }
                }
            }

            foreach (string note in Notes) { lines.Add(note); }
            return string.Join("\r\n", lines.ToArray());
        }
    }

    /// <summary>
    /// Finds the OmoBench checkout and the WSL Python environment that runs
    /// it, so an ordinary user never has to know or type either path.
    ///
    /// Discovery only ever *proposes*. It writes no configuration, creates no
    /// registry entry, installs nothing and changes no environment -- saving
    /// stays the person's explicit action in Setup. Everything it reports has
    /// been validated by the same <see cref="EnvironmentCheck"/> the manual
    /// path already used, so "ready" means the same thing either way.
    /// </summary>
    public static class Discovery
    {
        /// <summary>Runs one wsl.exe command line. Replaced by tests only.</summary>
        internal static Func<string, int, DiscoveryCancel, ProcessResult> WslRunner =
            (arguments, timeoutMilliseconds, cancel) => Wsl.Run(
                Wsl.ExecutablePath, arguments, timeoutMilliseconds, null,
                cancel == null ? (Func<bool>)null : () => cancel.IsCancelled);

        /// <summary>Lists distributions. Replaced by tests only.</summary>
        internal static Func<List<WslDistribution>> DistributionLister = Wsl.ListDistributions;

        /// <summary>True when wsl.exe exists. Replaced by tests only.</summary>
        internal static Func<bool> WslAvailable = () => Wsl.IsAvailable;

        /// <summary>Where the bounded search starts. Replaced by tests only.</summary>
        internal static Func<List<string>> SearchRootProvider = SearchRoots;

        // -- recognising a checkout -------------------------------------------

        /// <summary>
        /// Is this really an OmoBench checkout?
        ///
        /// Judged by the files that make it one, never by the folder's name: a
        /// directory called "omobench" holding a download, a tarball or an
        /// unrelated project is not a checkout, and a checkout named anything
        /// else still is one.
        /// </summary>
        public static bool LooksLikeCheckout(string windowsPath)
        {
            if (string.IsNullOrWhiteSpace(windowsPath))
            {
                return false;
            }
            try
            {
                if (!File.Exists(Path.Combine(windowsPath, "cli", "src", "omobench", "cli.py")))
                {
                    return false;
                }
                string profiles = Path.Combine(windowsPath, "benchmarks", "profiles");
                if (!Directory.Exists(profiles))
                {
                    return false;
                }
                return Directory.GetFiles(profiles, "*.yaml").Length > 0;
            }
            catch (Exception)
            {
                // Unreadable is not a checkout, and is not an error either.
                return false;
            }
        }

        /// <summary>
        /// Directory names never descended into. Model caches, dependency
        /// trees and evidence directories are large, slow, and cannot contain
        /// a checkout root.
        /// </summary>
        private static readonly string[] SkippedDirectories =
        {
            "node_modules", ".git", ".omobench-runs", "results", "__pycache__",
            ".cache", "huggingface", "site-packages", ".venv", "venv", "env",
            ".next", ".nuxt", "dist", "out", "target", "obj", "bin",
            "AppData", "OneDriveTemp", "$RECYCLE.BIN", "System Volume Information",
            ".gradle", ".m2", ".tox", ".mypy_cache", ".pytest_cache",
        };

        private static bool IsSkipped(string name)
        {
            foreach (string skipped in SkippedDirectories)
            {
                if (string.Equals(name, skipped, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        /// <summary>
        /// The checkout the companion is sitting inside, if any.
        ///
        /// A build lives at windows\companion\build\OmoBenchCompanion.exe, so
        /// walking up from the executable finds the checkout it was built from
        /// without any searching at all -- the common case, and the fastest.
        /// </summary>
        public static List<CheckoutCandidate> CheckoutsFromExecutable(string executablePath)
        {
            List<CheckoutCandidate> found = new List<CheckoutCandidate>();
            if (string.IsNullOrWhiteSpace(executablePath))
            {
                return found;
            }
            string directory;
            try
            {
                directory = Path.GetDirectoryName(Path.GetFullPath(executablePath));
            }
            catch (Exception)
            {
                return found;
            }

            // Bounded: a checkout is never more than a handful of levels above
            // the build output, and this must not walk to the drive root.
            for (int level = 0; level < 6 && !string.IsNullOrEmpty(directory); level++)
            {
                if (LooksLikeCheckout(directory))
                {
                    found.Add(new CheckoutCandidate
                    {
                        WindowsPath = directory,
                        Source = CandidateSource.Executable,
                    });
                    break;
                }
                try
                {
                    DirectoryInfo parent = Directory.GetParent(directory);
                    directory = parent == null ? null : parent.FullName;
                }
                catch (Exception)
                {
                    break;
                }
            }
            return found;
        }

        /// <summary>
        /// Where people actually keep projects. Only folders under this user's
        /// own profile: discovery never scans a whole drive.
        /// </summary>
        public static List<string> SearchRoots()
        {
            List<string> roots = new List<string>();
            string profile;
            try
            {
                profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            }
            catch (Exception)
            {
                return roots;
            }
            if (string.IsNullOrEmpty(profile))
            {
                return roots;
            }

            string[] names =
            {
                "Code", "code", "source", "Projects", "projects", "Documents",
                "Developer", "dev", "git", "repos", "Desktop", "Downloads", "work",
            };
            foreach (string name in names)
            {
                string candidate = Path.Combine(profile, name);
                try
                {
                    if (Directory.Exists(candidate)) { roots.Add(candidate); }
                }
                catch (Exception) { }
            }
            return roots;
        }

        /// <summary>
        /// Breadth-first search below one root, bounded in depth and in the
        /// number of directories examined. Inaccessible directories are
        /// skipped rather than failing the search, and reparse points are not
        /// followed, so a junction pointing at an ancestor cannot loop.
        /// </summary>
        public static List<CheckoutCandidate> SearchForCheckouts(
            IEnumerable<string> roots, DiscoveryLimits limits, DiscoveryCancel cancel, DateTime deadline)
        {
            List<CheckoutCandidate> found = new List<CheckoutCandidate>();
            int visited = 0;

            List<string> frontier = new List<string>();
            foreach (string root in roots) { frontier.Add(root); }

            for (int depth = 0; depth <= limits.MaximumDirectoryDepth && frontier.Count > 0; depth++)
            {
                List<string> next = new List<string>();
                foreach (string directory in frontier)
                {
                    if (IsDone(cancel, deadline) || visited >= limits.MaximumDirectoriesVisited
                        || found.Count >= limits.MaximumCheckouts)
                    {
                        return found;
                    }
                    visited++;

                    if (LooksLikeCheckout(directory))
                    {
                        found.Add(new CheckoutCandidate
                        {
                            WindowsPath = directory,
                            Source = CandidateSource.Project,
                        });
                        continue;   // a checkout contains no other checkout
                    }

                    string[] children;
                    try
                    {
                        children = Directory.GetDirectories(directory);
                    }
                    catch (Exception)
                    {
                        continue;   // unreadable: skip, do not fail the search
                    }

                    foreach (string child in children)
                    {
                        string name = Path.GetFileName(child);
                        if (IsSkipped(name) || name.StartsWith(".", StringComparison.Ordinal))
                        {
                            continue;
                        }
                        if (IsReparsePoint(child))
                        {
                            continue;   // never follow a junction or symlink
                        }
                        next.Add(child);
                    }
                }
                frontier = next;
            }
            return found;
        }

        private static bool IsReparsePoint(string path)
        {
            try
            {
                FileAttributes attributes = File.GetAttributes(path);
                return (attributes & FileAttributes.ReparsePoint) == FileAttributes.ReparsePoint;
            }
            catch (Exception)
            {
                return true;   // cannot tell: do not descend
            }
        }

        private static bool IsDone(DiscoveryCancel cancel, DateTime deadline)
        {
            return (cancel != null && cancel.IsCancelled) || DateTime.UtcNow >= deadline;
        }

        /// <summary>
        /// Every checkout worth trying, in a deterministic order: what is
        /// already saved, then the one this companion lives inside, then
        /// whatever the bounded search turns up. Only checkouts WSL can
        /// actually reach as /mnt/&lt;drive&gt; are kept -- the companion's
        /// existing contract, unchanged.
        /// </summary>
        public static List<CheckoutCandidate> FindCheckouts(
            Configuration saved, string executablePath, DiscoveryLimits limits,
            DiscoveryCancel cancel, DateTime deadline)
        {
            List<CheckoutCandidate> ordered = new List<CheckoutCandidate>();

            if (saved != null && !string.IsNullOrWhiteSpace(saved.CheckoutWindowsPath)
                && LooksLikeCheckout(saved.CheckoutWindowsPath))
            {
                ordered.Add(new CheckoutCandidate
                {
                    WindowsPath = saved.CheckoutWindowsPath,
                    Source = CandidateSource.Saved,
                });
            }

            foreach (CheckoutCandidate candidate in CheckoutsFromExecutable(executablePath))
            {
                ordered.Add(candidate);
            }

            if (!IsDone(cancel, deadline))
            {
                foreach (CheckoutCandidate candidate in
                    SearchForCheckouts(SearchRootProvider(), limits, cancel, deadline))
                {
                    ordered.Add(candidate);
                }
            }

            // De-duplicate by path, keep the first (highest-priority) source,
            // and drop anything WSL could not read as /mnt/<drive>.
            List<CheckoutCandidate> kept = new List<CheckoutCandidate>();
            foreach (CheckoutCandidate candidate in ordered)
            {
                string wslPath;
                if (!CommandLine.TryToWslPath(candidate.WindowsPath, out wslPath))
                {
                    continue;
                }
                bool duplicate = false;
                foreach (CheckoutCandidate existing in kept)
                {
                    if (string.Equals(existing.WindowsPath.TrimEnd('\\'),
                            candidate.WindowsPath.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                    {
                        duplicate = true;
                        break;
                    }
                }
                if (duplicate) { continue; }
                candidate.WslPath = wslPath;
                kept.Add(candidate);
                if (kept.Count >= limits.MaximumCheckouts) { break; }
            }
            return kept;
        }

        // -- Python environments inside the distribution ----------------------

        /// <summary>
        /// Could this path be a Linux interpreter at all?
        ///
        /// Two mistakes are worth refusing outright. A Windows interpreter
        /// reached through /mnt (".venv/Scripts/python.exe") is not the Linux
        /// runner environment and cannot run the benchmark; and a relative
        /// path is not something that can be invoked directly.
        /// </summary>
        public static bool IsPlausibleLinuxInterpreter(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return false;
            }
            string candidate = path.Trim();
            if (!candidate.StartsWith("/", StringComparison.Ordinal))
            {
                return false;
            }
            if (candidate.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            if (candidate.IndexOf("/Scripts/", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return false;
            }
            return true;
        }

        /// <summary>
        /// Assemble the interpreters worth probing, in order, from values the
        /// caller has already read out of the distribution.
        ///
        /// Pure, so the ordering and de-duplication rules are testable without
        /// WSL. Note what is *not* done: a virtual environment's bin/python is
        /// kept exactly as it is. Resolving that symlink would collapse every
        /// environment onto the one system interpreter underneath and pick the
        /// wrong one.
        /// </summary>
        public static List<InterpreterCandidate> BuildInterpreterCandidates(
            string savedInterpreter, string home, IEnumerable<string> virtualEnvNames,
            IEnumerable<string> checkoutWslPaths, string pathInterpreter, DiscoveryLimits limits)
        {
            List<InterpreterCandidate> ordered = new List<InterpreterCandidate>();

            if (IsPlausibleLinuxInterpreter(savedInterpreter))
            {
                ordered.Add(new InterpreterCandidate
                {
                    WslPath = savedInterpreter.Trim(),
                    Source = CandidateSource.Saved,
                });
            }

            if (!string.IsNullOrWhiteSpace(home))
            {
                string trimmed = home.TrimEnd('/');
                ordered.Add(new InterpreterCandidate
                {
                    WslPath = trimmed + "/.venvs/omobench-runner/bin/python",
                    Source = CandidateSource.Conventional,
                });
                if (virtualEnvNames != null)
                {
                    foreach (string name in virtualEnvNames)
                    {
                        if (string.IsNullOrWhiteSpace(name)) { continue; }
                        ordered.Add(new InterpreterCandidate
                        {
                            WslPath = name.Trim(),
                            Source = CandidateSource.Conventional,
                        });
                    }
                }
            }

            if (checkoutWslPaths != null)
            {
                foreach (string checkout in checkoutWslPaths)
                {
                    if (string.IsNullOrWhiteSpace(checkout)) { continue; }
                    string trimmed = checkout.TrimEnd('/');
                    ordered.Add(new InterpreterCandidate
                    {
                        WslPath = trimmed + "/.venv/bin/python",
                        Source = CandidateSource.CheckoutVirtualEnv,
                    });
                    ordered.Add(new InterpreterCandidate
                    {
                        WslPath = trimmed + "/venv/bin/python",
                        Source = CandidateSource.CheckoutVirtualEnv,
                    });
                }
            }

            if (IsPlausibleLinuxInterpreter(pathInterpreter))
            {
                ordered.Add(new InterpreterCandidate
                {
                    WslPath = pathInterpreter.Trim(),
                    Source = CandidateSource.Path,
                });
            }

            List<InterpreterCandidate> kept = new List<InterpreterCandidate>();
            foreach (InterpreterCandidate candidate in ordered)
            {
                if (!IsPlausibleLinuxInterpreter(candidate.WslPath)) { continue; }
                bool duplicate = false;
                foreach (InterpreterCandidate existing in kept)
                {
                    if (string.Equals(existing.WslPath, candidate.WslPath, StringComparison.Ordinal))
                    {
                        duplicate = true;
                        break;
                    }
                }
                if (duplicate) { continue; }
                kept.Add(candidate);
                if (kept.Count >= limits.MaximumInterpreters) { break; }
            }
            return kept;
        }

        /// <summary>Programs run inside a distribution. No shell, ever.</summary>
        public static List<string> BuildHomeProgram() { return new List<string> { "printenv", "HOME" }; }

        public static List<string> BuildUserProgram() { return new List<string> { "whoami" }; }

        public static List<string> BuildListProgram(string directory)
        {
            return new List<string> { "ls", "-1", directory };
        }

        public static List<string> BuildPathPythonProgram()
        {
            return new List<string> { "which", "python3" };
        }

        private static string FirstLine(ProcessResult result)
        {
            if (result == null || result.TimedOut || result.ExitCode != 0)
            {
                return null;
            }
            string output = (result.StandardOutput ?? "").Replace("\r", "");
            foreach (string line in output.Split('\n'))
            {
                string trimmed = line.Trim();
                if (trimmed.Length > 0) { return trimmed; }
            }
            return null;
        }

        private static List<string> Lines(ProcessResult result)
        {
            List<string> lines = new List<string>();
            if (result == null || result.TimedOut || result.ExitCode != 0)
            {
                return lines;
            }
            string output = (result.StandardOutput ?? "").Replace("\r", "");
            foreach (string line in output.Split('\n'))
            {
                string trimmed = line.Trim();
                if (trimmed.Length > 0) { lines.Add(trimmed); }
            }
            return lines;
        }

        private static ProcessResult RunIn(
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

        // -- the whole search --------------------------------------------------

        /// <summary>
        /// Find every validated installation on this machine.
        ///
        /// Probing is arranged so the expensive step happens once per
        /// interpreter rather than once per combination: each interpreter is
        /// asked a single question (which OmoBench does it import, and which
        /// GuideLLM does it have), and that one answer is then judged against
        /// every candidate checkout by the existing EnvironmentCheck logic.
        /// </summary>
        public static DiscoveryReport Run(
            Configuration saved, string executablePath, DiscoveryLimits limits,
            DiscoveryCancel cancel, Action<string> progress)
        {
            if (limits == null) { limits = new DiscoveryLimits(); }
            DiscoveryReport report = new DiscoveryReport();
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(limits.OverallTimeoutMilliseconds);
            Report(progress, "Looking for your OmoBench installation…");

            if (!WslAvailable())
            {
                report.Notes.Add("WSL was not found on this machine (wsl.exe is missing from System32).");
                report.Notes.Add("Install WSL2 and a Linux distribution; this release reuses an existing one.");
                return report;
            }

            List<WslDistribution> distributions;
            try
            {
                distributions = DistributionLister();
            }
            catch (Exception error)
            {
                report.Notes.Add("The list of WSL distributions could not be read: " + error.Message);
                return report;
            }

            List<WslDistribution> usable = new List<WslDistribution>();
            int version1 = 0;
            foreach (WslDistribution distribution in distributions)
            {
                if (distribution.Version == 2) { usable.Add(distribution); } else { version1++; }
            }
            if (usable.Count == 0)
            {
                report.Notes.Add(distributions.Count == 0
                    ? "No WSL distributions are installed."
                    : "No WSL2 distribution was found; OmoBench needs WSL2. Convert one with:\r\n"
                      + "    wsl --set-version <name> 2");
                return report;
            }
            if (version1 > 0)
            {
                report.Notes.Add(version1.ToString(CultureInfo.InvariantCulture)
                    + " WSL1 distribution(s) were skipped: OmoBench needs WSL2.");
            }

            // Deterministic order: the saved distribution, then the default,
            // then the rest as the system listed them.
            usable = Order(usable, saved == null ? null : saved.Distribution);

            Report(progress, "Looking for your OmoBench checkout…");
            foreach (CheckoutCandidate checkout in
                FindCheckouts(saved, executablePath, limits, cancel, deadline))
            {
                report.Checkouts.Add(checkout);
            }
            if (cancel != null && cancel.IsCancelled) { report.Cancelled = true; return report; }

            List<string> checkoutWslPaths = new List<string>();
            foreach (CheckoutCandidate checkout in report.Checkouts)
            {
                checkoutWslPaths.Add(checkout.WslPath);
            }

            int examined = 0;
            foreach (WslDistribution distribution in usable)
            {
                if (examined++ >= limits.MaximumDistributions) { break; }
                if (IsDone(cancel, deadline)) { break; }

                Report(progress, "Checking " + distribution.Name + "…");

                string home = FirstLine(RunIn(
                    distribution.Name, BuildHomeProgram(), limits.ListingTimeoutMilliseconds, cancel));
                if (string.IsNullOrEmpty(home))
                {
                    report.Notes.Add("Could not read the home directory of '" + distribution.Name
                        + "'. It may be stopped, or it may need to finish first-run setup.");
                    continue;
                }

                List<string> virtualEnvironments = new List<string>();
                string[] environmentRoots = { "/.venvs", "/venvs", "/.virtualenvs" };
                foreach (string suffix in environmentRoots)
                {
                    if (IsDone(cancel, deadline)) { break; }
                    string directory = home.TrimEnd('/') + suffix;
                    foreach (string name in Lines(RunIn(distribution.Name, BuildListProgram(directory),
                        limits.ListingTimeoutMilliseconds, cancel)))
                    {
                        virtualEnvironments.Add(directory + "/" + name + "/bin/python");
                    }
                }

                string pathPython = FirstLine(RunIn(distribution.Name, BuildPathPythonProgram(),
                    limits.ListingTimeoutMilliseconds, cancel));

                List<InterpreterCandidate> interpreters = BuildInterpreterCandidates(
                    saved == null ? null : saved.PythonWslPath,
                    home, virtualEnvironments, checkoutWslPaths, pathPython, limits);

                foreach (InterpreterCandidate interpreter in interpreters)
                {
                    bool already = false;
                    foreach (InterpreterCandidate seen in report.Interpreters)
                    {
                        if (seen.WslPath == interpreter.WslPath) { already = true; break; }
                    }
                    if (!already) { report.Interpreters.Add(interpreter); }
                }

                foreach (InterpreterCandidate interpreter in interpreters)
                {
                    if (IsDone(cancel, deadline)) { break; }
                    Report(progress, "Checking " + interpreter.WslPath + "…");

                    ProcessResult probe = RunIn(distribution.Name,
                        EnvironmentCheck.BuildProbeProgram(interpreter.WslPath),
                        limits.ProbeTimeoutMilliseconds, cancel);
                    if (probe == null)
                    {
                        continue;
                    }

                    bool matchedAny = false;
                    // Report the failure against the highest-priority checkout:
                    // that is the one the person is most likely asking about,
                    // and "imports from somewhere else" only reads clearly when
                    // the checkout it names is the one they meant.
                    EnvironmentCheckResult firstFailure = null;
                    CheckoutCandidate firstFailureCheckout = null;
                    foreach (CheckoutCandidate checkout in report.Checkouts)
                    {
                        EnvironmentCheckResult check = EnvironmentCheck.Interpret(
                            probe.ExitCode, probe.CombinedOutput, checkout.WslPath, probe.TimedOut);
                        if (check.Ok)
                        {
                            matchedAny = true;
                            report.Ready.Add(new InstallationCandidate
                            {
                                Distribution = distribution.Name,
                                CheckoutWindowsPath = checkout.WindowsPath,
                                PythonWslPath = interpreter.WslPath,
                                Ready = true,
                                Summary = check.Summary,
                                Detail = check.Detail,
                                CheckoutSource = checkout.Source,
                                InterpreterSource = interpreter.Source,
                            });
                        }
                        else if (firstFailure == null)
                        {
                            firstFailure = check;
                            firstFailureCheckout = checkout;
                        }
                    }

                    if (!matchedAny)
                    {
                        // With no checkout at all there is still something to
                        // say about the interpreter itself.
                        if (firstFailure == null)
                        {
                            firstFailure = EnvironmentCheck.Interpret(
                                probe.ExitCode, probe.CombinedOutput, "", probe.TimedOut);
                        }
                        report.Rejected.Add(new InstallationCandidate
                        {
                            Distribution = distribution.Name,
                            CheckoutWindowsPath = firstFailureCheckout == null
                                ? null : firstFailureCheckout.WindowsPath,
                            PythonWslPath = interpreter.WslPath,
                            Ready = false,
                            Summary = firstFailure.Summary,
                            Detail = firstFailure.Detail,
                            InterpreterSource = interpreter.Source,
                        });
                    }
                }
            }

            if (cancel != null && cancel.IsCancelled) { report.Cancelled = true; }
            if (DateTime.UtcNow >= deadline) { report.TimedOut = true; }
            return report;
        }

        private static List<WslDistribution> Order(List<WslDistribution> distributions, string preferred)
        {
            List<WslDistribution> ordered = new List<WslDistribution>();
            foreach (WslDistribution distribution in distributions)
            {
                if (!string.IsNullOrEmpty(preferred)
                    && string.Equals(distribution.Name, preferred, StringComparison.Ordinal))
                {
                    ordered.Add(distribution);
                }
            }
            foreach (WslDistribution distribution in distributions)
            {
                if (distribution.IsDefault && !ordered.Contains(distribution)) { ordered.Add(distribution); }
            }
            foreach (WslDistribution distribution in distributions)
            {
                if (!ordered.Contains(distribution)) { ordered.Add(distribution); }
            }
            return ordered;
        }

        private static void Report(Action<string> progress, string message)
        {
            if (progress != null)
            {
                try { progress(message); } catch (Exception) { }
            }
        }
    }
}
