using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using Microsoft.Win32;
using OmoBench.Companion;

namespace OmoBench.Installer
{
    public sealed class InstallOutcome
    {
        public bool Ok;
        public string Summary;
        public readonly List<string> Steps = new List<string>();
        public readonly List<string> Warnings = new List<string>();

        public void Step(string message) { Steps.Add(message); }
        public void Warn(string message) { Warnings.Add(message); }

        public string Describe()
        {
            StringBuilder text = new StringBuilder();
            foreach (string step in Steps) { text.AppendLine(step); }
            if (Warnings.Count > 0)
            {
                text.AppendLine();
                foreach (string warning in Warnings) { text.AppendLine("Note: " + warning); }
            }
            return text.ToString().TrimEnd();
        }
    }

    /// <summary>
    /// Putting the companion on the machine, and taking it off again.
    ///
    /// The whole of this is per-user and reversible. It writes a program
    /// directory, a Start menu shortcut, an uninstall entry and the
    /// omobench:// registration -- and on the way out it removes exactly those
    /// four things. Benchmark evidence, saved configuration, model caches and
    /// the WSL installation are deliberately outside its reach: an uninstall
    /// that destroyed a contributor's evidence would be unforgivable, and a
    /// Windows installer has no business removing a Linux distribution that
    /// other software may be using too.
    /// </summary>
    public static class Installation
    {
        /// <summary>What an existing installation says about itself.</summary>
        public sealed class Manifest
        {
            public string Version;
            public string Revision;
            public string InstalledAt;

            public override string ToString()
            {
                return "{\"version\": \"" + Escape(Version)
                    + "\", \"revision\": \"" + Escape(Revision)
                    + "\", \"installed_at\": \"" + Escape(InstalledAt) + "\"}";
            }

            private static string Escape(string value)
            {
                return (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
            }
        }

        public static bool IsInstalled()
        {
            try { return File.Exists(InstallPaths.CompanionPath); }
            catch (Exception) { return false; }
        }

        public static Manifest ReadManifest()
        {
            try
            {
                if (!File.Exists(InstallPaths.ManifestPath)) { return null; }
                string text = File.ReadAllText(InstallPaths.ManifestPath, Encoding.UTF8);
                return new Manifest
                {
                    Version = Read(text, "version"),
                    Revision = Read(text, "revision"),
                    InstalledAt = Read(text, "installed_at"),
                };
            }
            catch (Exception) { return null; }
        }

        private static string Read(string json, string key)
        {
            System.Text.RegularExpressions.Match match = System.Text.RegularExpressions.Regex.Match(
                json, "\"" + key + "\"\\s*:\\s*\"([^\"]*)\"");
            return match.Success ? match.Groups[1].Value : null;
        }

        /// <summary>
        /// One running companion, reduced to what coordination needs: who it
        /// is, whether it has gone, and how to ask it to close.
        ///
        /// Introduced so tests can supply their own. Enumerating real
        /// processes from a test meant the suite reached out and touched the
        /// application actually installed on the machine.
        /// </summary>
        public sealed class CompanionProcess
        {
            public int Id;
            public Func<bool> HasExited;
            public Action RequestClose;
        }

        /// <summary>
        /// What looking for running companions established.
        ///
        /// <see cref="Answered"/> is false when the question could not be
        /// answered at all -- the enumeration itself failed. That is not the
        /// same as "none are running", and the difference decides whether a
        /// program is about to be replaced or deleted underneath one.
        /// </summary>
        public sealed class CompanionScan
        {
            public readonly List<CompanionProcess> Running = new List<CompanionProcess>();
            public bool Answered = true;

            /// <summary>True unless it is established that none are running.</summary>
            public bool AnyPossiblyRunning
            {
                get { return !Answered || Running.Count > 0; }
            }
        }

        /// <summary>
        /// One process that might be an installed companion.
        ///
        /// Every question about it can fail: what it is running, whether it
        /// has gone, whether it will accept a close request. The enumeration
        /// is written in terms of these three so a test can make any of them
        /// fail without a real process to fail against.
        /// </summary>
        public sealed class CompanionCandidate
        {
            public int Id;

            /// <summary>The executable it is running. May throw, or be null.</summary>
            public Func<string> ExecutablePath;

            public Func<bool> HasExited;
            public Action RequestClose;
        }

        /// <summary>The process name an installed companion runs under.</summary>
        public const string CompanionProcessName = "OmoBenchCompanion";

        /// <summary>
        /// Lists the processes that might be installed companions. Replaced
        /// by tests only -- this is the half of the enumeration that talks to
        /// the operating system.
        /// </summary>
        internal static Func<List<CompanionCandidate>> CandidateLister = ListCandidateProcesses;

        /// <summary>Finds companions to coordinate with. Replaced by tests only.</summary>
        internal static Func<CompanionScan> CompanionEnumerator = EnumerateInstalledCompanions;

        /// <summary>
        /// How long an uninstall waits for the companion to close. Long
        /// enough for a person to answer a question about a running benchmark.
        /// Shortened by tests only.
        /// </summary>
        internal static int CloseWaitMilliseconds = 30000;

        /// <summary>
        /// Companions running *from this installation*, which are the only
        /// ones whose program file this installer is about to replace.
        ///
        /// Matched by executable path rather than process name: a developer
        /// build running from a checkout is a different program that happens
        /// to share a name, and it is none of the installer's business.
        ///
        /// Returned rather than terminated. A running companion may own a
        /// panel with a benchmark in flight, and it already asks about that
        /// properly when told to close; killing it would bypass that question
        /// and orphan the work it was told to protect.
        /// </summary>
        public static CompanionScan ScanInstalledCompanions()
        {
            CompanionScan found;
            try { found = CompanionEnumerator(); }
            catch (Exception)
            {
                // The question was not answered. Saying "nothing is running"
                // here is how a failed enumeration used to become permission
                // to replace or delete the program -- without anyone ever
                // having looked.
                return Unanswered();
            }
            if (found == null) { return Unanswered(); }

            // An enumeration is a snapshot. Asking each one whether it has
            // actually gone is what makes waiting for a close request mean
            // something -- and a process we cannot ask is treated as still
            // running, because "probably gone" is not an answer worth
            // deleting a program on.
            CompanionScan scan = new CompanionScan();
            scan.Answered = found.Answered;
            foreach (CompanionProcess process in found.Running)
            {
                if (process == null) { continue; }
                bool exited;
                try { exited = process.HasExited != null && process.HasExited(); }
                catch (Exception) { exited = false; }
                if (!exited) { scan.Running.Add(process); }
            }
            return scan;
        }

        private static CompanionScan Unanswered()
        {
            CompanionScan scan = new CompanionScan();
            scan.Answered = false;
            return scan;
        }

        public static List<CompanionProcess> RunningInstalledCompanions()
        {
            return ScanInstalledCompanions().Running;
        }

        private static List<CompanionCandidate> ListCandidateProcesses()
        {
            List<CompanionCandidate> candidates = new List<CompanionCandidate>();
            foreach (Process process in Process.GetProcessesByName(CompanionProcessName))
            {
                candidates.Add(Describe(process));
            }
            return candidates;
        }

        /// <summary>
        /// One real process, as the enumeration sees it.
        ///
        /// None of these deliberately swallow: a query that fails has not
        /// established anything, and answering it anyway is how an unreadable
        /// process became "it has exited" and an unreadable path became "not
        /// ours". The caller decides what a failure means.
        /// </summary>
        internal static CompanionCandidate Describe(Process process)
        {
            Process captured = process;
            return new CompanionCandidate
            {
                Id = SafeId(captured),
                ExecutablePath = () => captured.MainModule == null ? null : captured.MainModule.FileName,
                HasExited = () => captured.HasExited,
                RequestClose = () => { try { captured.CloseMainWindow(); } catch (Exception) { } },
            };
        }

        /// <summary>
        /// Which processes are this installation's companion -- and whether
        /// that could be established at all.
        ///
        /// Three outcomes, kept apart on purpose. A process whose executable
        /// is a different file is confirmed to be somebody else's program and
        /// is excluded. A process running the installed executable is this
        /// installation's, and can be asked to close. Anything the enumeration
        /// could not read -- the process list, a process's executable, the
        /// installed path to compare against -- leaves the answer open: it is
        /// named like the companion and might be this one, so it is neither
        /// excluded nor signalled, and the scan reports that nothing was
        /// established.
        ///
        /// Every one of those failures used to end in the same place: an empty
        /// list, which coordination read as "none are running", which is
        /// permission to replace the program file or delete it. The only thing
        /// that may be ignored outright is a process confirmed to have exited,
        /// and a question that failed is not that confirmation.
        /// </summary>
        internal static CompanionScan EnumerateInstalledCompanions()
        {
            CompanionScan scan = new CompanionScan();

            string installed;
            try { installed = Path.GetFullPath(InstallPaths.CompanionPath); }
            catch (Exception)
            {
                // Without the path to compare against, nothing can be
                // attributed to this installation either way.
                scan.Answered = false;
                return scan;
            }

            List<CompanionCandidate> candidates;
            try { candidates = CandidateLister(); }
            catch (Exception) { scan.Answered = false; return scan; }
            if (candidates == null) { scan.Answered = false; return scan; }

            foreach (CompanionCandidate candidate in candidates)
            {
                if (candidate == null) { scan.Answered = false; continue; }

                // A process that has definitely gone is nobody's business.
                // Only a definite answer counts here: a liveness query that
                // failed has not established an exit, and falls through to be
                // identified like any other.
                bool exited = false;
                try { exited = candidate.HasExited != null && candidate.HasExited(); }
                catch (Exception) { exited = false; }
                if (exited) { continue; }

                string path;
                try { path = candidate.ExecutablePath == null ? null : candidate.ExecutablePath(); }
                catch (Exception) { path = null; }
                if (string.IsNullOrEmpty(path)) { scan.Answered = false; continue; }

                string full;
                try { full = Path.GetFullPath(path); }
                catch (Exception) { scan.Answered = false; continue; }

                if (!string.Equals(full, installed, StringComparison.OrdinalIgnoreCase))
                {
                    continue;   // confirmed to be a different program
                }

                scan.Running.Add(new CompanionProcess
                {
                    Id = candidate.Id,
                    HasExited = candidate.HasExited,
                    RequestClose = candidate.RequestClose,
                });
            }
            return scan;
        }
        private static int SafeId(Process process)
        {
            try { return process.Id; }
            catch (Exception) { return 0; }
        }

        /// <summary>
        /// Ask the installed companion to close itself, and report whether it
        /// did.
        ///
        /// This only ever sends a close request. The companion decides what to
        /// do with it -- including asking the person about a running benchmark,
        /// and declining -- and a decline is an answer to be respected, not an
        /// obstacle to work around.
        /// </summary>
        public static bool TryCloseInstalledCompanions(int waitMilliseconds)
        {
            CompanionScan scan = ScanInstalledCompanions();
            if (!scan.Answered)
            {
                // Nothing is asked to close: there is no identified process to
                // ask. The caller postpones, which is the honest outcome of a
                // question that could not be answered.
                return false;
            }
            if (scan.Running.Count == 0)
            {
                return true;
            }

            foreach (CompanionProcess process in scan.Running)
            {
                try { process.RequestClose(); }
                catch (Exception) { }
            }

            DateTime deadline = DateTime.UtcNow.AddMilliseconds(waitMilliseconds);
            while (DateTime.UtcNow < deadline)
            {
                CompanionScan again = ScanInstalledCompanions();
                if (!again.Answered) { return false; }
                if (again.Running.Count == 0) { return true; }
                System.Threading.Thread.Sleep(250);
            }
            CompanionScan last = ScanInstalledCompanions();
            return last.Answered && last.Running.Count == 0;
        }

        /// <summary>
        /// Install or upgrade. The payload is the companion executable; the
        /// uninstaller is this running installer, copied in beside it so the
        /// Add/Remove entry has something to call.
        /// </summary>
        public static InstallOutcome Install(byte[] companion, string installerSelfPath, BuildInfo build)
        {
            InstallOutcome outcome = new InstallOutcome();
            Manifest previous = ReadManifest();

            try
            {
                if (companion == null || companion.Length == 0)
                {
                    outcome.Summary = "This installer has no companion payload; the build is incomplete.";
                    return outcome;
                }

                Directory.CreateDirectory(InstallPaths.ProgramDirectory);
                outcome.Step(previous == null
                    ? "Created " + InstallPaths.ProgramDirectory
                    : "Upgrading the installation in " + InstallPaths.ProgramDirectory
                      + " (was version " + (previous.Version ?? "unknown") + ")");

                File.WriteAllBytes(InstallPaths.CompanionPath, companion);
                outcome.Step("Installed " + InstallPaths.CompanionFileName);

                if (!string.IsNullOrEmpty(installerSelfPath) && File.Exists(installerSelfPath))
                {
                    try
                    {
                        File.Copy(installerSelfPath, InstallPaths.UninstallerPath, true);
                        outcome.Step("Installed the uninstaller");
                    }
                    catch (Exception error)
                    {
                        outcome.Warn("Could not place the uninstaller: " + error.Message);
                    }
                }

                File.WriteAllText(InstallPaths.ManifestPath, new Manifest
                {
                    Version = build.Version,
                    Revision = build.Revision,
                    InstalledAt = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                }.ToString(), new UTF8Encoding(false));

                CreateShortcut(outcome);
                WriteUninstallEntry(build, outcome);

                // Always against the installed copy -- never the path this
                // installer happened to be run from, which is usually the
                // Downloads folder or a temporary directory that will not
                // exist later.
                try
                {
                    ProtocolRegistration.Register(InstallPaths.CompanionPath);
                    outcome.Step("Registered omobench:// for your user account");
                }
                catch (Exception error)
                {
                    outcome.Warn("Could not register omobench://: " + error.Message
                        + " You can still open the companion from the Start menu.");
                }

                outcome.Ok = true;
                outcome.Summary = previous == null
                    ? "OmoBench is installed."
                    : "OmoBench is up to date.";
                return outcome;
            }
            catch (UnauthorizedAccessException error)
            {
                outcome.Summary = "Windows refused access while installing: " + error.Message;
                return outcome;
            }
            catch (IOException error)
            {
                outcome.Summary = "A file was in use or could not be written: " + error.Message
                    + "\r\n\r\nClose the OmoBench companion if it is running, then try again.";
                return outcome;
            }
            catch (Exception error)
            {
                outcome.Summary = "The installation did not complete: " + error.Message;
                return outcome;
            }
        }

        private static void CreateShortcut(InstallOutcome outcome)
        {
            try
            {
                Directory.CreateDirectory(InstallPaths.StartMenuDirectory);
                // Late-bound WScript.Shell: present on every Windows install,
                // and avoids a COM reference the in-box compiler cannot add.
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null)
                {
                    outcome.Warn("Could not create the Start menu shortcut (WScript.Shell is unavailable).");
                    return;
                }
                object shell = Activator.CreateInstance(shellType);
                object shortcut = shellType.InvokeMember("CreateShortcut",
                    System.Reflection.BindingFlags.InvokeMethod, null, shell,
                    new object[] { InstallPaths.ShortcutPath });
                Type shortcutType = shortcut.GetType();
                Set(shortcutType, shortcut, "TargetPath", InstallPaths.CompanionPath);
                Set(shortcutType, shortcut, "WorkingDirectory", InstallPaths.ProgramDirectory);
                Set(shortcutType, shortcut, "Description", "Open the OmoBench benchmark panel");
                shortcutType.InvokeMember("Save",
                    System.Reflection.BindingFlags.InvokeMethod, null, shortcut, new object[0]);
                outcome.Step("Added a Start menu entry");
            }
            catch (Exception error)
            {
                outcome.Warn("Could not create the Start menu shortcut: " + error.Message);
            }
        }

        private static void Set(Type type, object instance, string property, string value)
        {
            type.InvokeMember(property, System.Reflection.BindingFlags.SetProperty,
                null, instance, new object[] { value });
        }

        private static void WriteUninstallEntry(BuildInfo build, InstallOutcome outcome)
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(InstallPaths.UninstallKeyPath))
                {
                    if (key == null)
                    {
                        outcome.Warn("Could not create the Add/Remove Programs entry.");
                        return;
                    }
                    key.SetValue("DisplayName", "OmoBench");
                    key.SetValue("DisplayVersion", build.Version ?? "0.0.0");
                    key.SetValue("Publisher", BuildInfo.PublisherDisplayName);
                    key.SetValue("InstallLocation", InstallPaths.ProgramDirectory);
                    key.SetValue("DisplayIcon", InstallPaths.CompanionPath);
                    key.SetValue("UninstallString",
                        CommandLine.Quote(InstallPaths.UninstallerPath) + " /uninstall");
                    key.SetValue("QuietUninstallString",
                        CommandLine.Quote(InstallPaths.UninstallerPath) + " /uninstall /silent");
                    key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                    key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    key.SetValue("URLInfoAbout", "https://github.com/Xatiiv/omobench");
                }
                outcome.Step("Added an entry to Apps & features");
            }
            catch (Exception error)
            {
                outcome.Warn("Could not create the Add/Remove Programs entry: " + error.Message);
            }
        }

        /// <summary>
        /// Remove the program, and only the program.
        ///
        /// Benchmark evidence, saved configuration, model caches and WSL are
        /// left exactly as they are. There is no option here to delete the
        /// evidence: it is the output of work the person did, and removing a
        /// Windows launcher is not a reason to destroy it.
        /// </summary>
        public static InstallOutcome Uninstall()
        {
            InstallOutcome outcome = new InstallOutcome();

            // Ask first, and remove nothing until it has gone. Unregistering
            // the handler or deleting the Start menu entry underneath a
            // running companion would leave the person with a half-removed
            // program they can no longer launch -- and if it is protecting a
            // benchmark, that question is still theirs to answer.
            if (!TryCloseInstalledCompanions(CloseWaitMilliseconds))
            {
                outcome.Ok = false;
                outcome.Summary = "OmoBench is still running, so nothing was removed.";
                outcome.Step("The OmoBench companion was asked to close and is still open. That "
                    + "usually means a benchmark is running and it asked you about it, or you chose "
                    + "to keep it open.");
                outcome.Step("Nothing was changed: the program, its Start menu entry, its "
                    + "Apps & features entry and its omobench:// registration are all still in place. "
                    + "Close the companion when you are ready and uninstall again.");
                return outcome;
            }

            string self = SelfPath();
            string uninstaller = FullPathOrNull(InstallPaths.UninstallerPath);

            // Files first, and the uninstaller last of all. The Apps &
            // features entry and the omobench:// registration are how a person
            // reaches this program -- and how they would reach the uninstaller
            // again -- so they go only once the files they point at are gone.
            try
            {
                DeleteInstalledFiles(outcome, self, uninstaller);
            }
            catch (Exception error)
            {
                outcome.Warn("Some program files could not be removed: " + error.Message);
            }

            // Everything that is still there, the uninstaller aside. If any of
            // it remains the removal did not happen, whatever else succeeded --
            // and saying otherwise leaves a person with a program they were
            // told was gone and no entry to retry from.
            List<string> blocking = new List<string>();
            foreach (string file in RemainingInstalledFiles(self))
            {
                if (uninstaller != null && SamePath(file, uninstaller)) { continue; }
                blocking.Add(file);
            }

            if (blocking.Count > 0)
            {
                outcome.Ok = false;
                outcome.Summary = "OmoBench could not be completely removed.";
                foreach (string remaining in blocking)
                {
                    outcome.Step("Still present: " + remaining);
                }
                outcome.Step("This usually means the program is still open. Its Apps & features entry, "
                    + "its omobench:// registration and " + InstallPaths.UninstallerFileName
                    + " have all been left in place, so close it and uninstall again from there.");
                return outcome;
            }

            // Now the uninstaller, which until this moment was the way back.
            if (uninstaller != null && !SamePath(uninstaller, self))
            {
                try
                {
                    if (File.Exists(uninstaller)) { File.Delete(uninstaller); }
                }
                catch (Exception error)
                {
                    outcome.Warn("Could not delete " + uninstaller + ": " + error.Message);
                }

                bool uninstallerRemains;
                try { uninstallerRemains = File.Exists(uninstaller); }
                catch (Exception) { uninstallerRemains = true; }
                if (uninstallerRemains)
                {
                    // Reported rather than waved through: a file of this
                    // installation is still on the machine. The entry that
                    // reaches it is kept, so trying again actually works.
                    outcome.Ok = false;
                    outcome.Summary = "OmoBench could not be completely removed.";
                    outcome.Step("Still present: " + uninstaller);
                    outcome.Step("Everything else has gone. Its Apps & features entry has been left in "
                        + "place so you can uninstall again once that file is no longer in use.");
                    return outcome;
                }
            }

            try
            {
                outcome.Step(ProtocolRegistration.Unregister(InstallPaths.CompanionPath).Replace("\n", " "));
            }
            catch (Exception error)
            {
                outcome.Warn("Could not remove the omobench:// registration: " + error.Message);
            }

            try
            {
                if (File.Exists(InstallPaths.ShortcutPath))
                {
                    File.Delete(InstallPaths.ShortcutPath);
                    outcome.Step("Removed the Start menu entry");
                }
            }
            catch (Exception error)
            {
                outcome.Warn("Could not remove the Start menu entry: " + error.Message);
            }

            try
            {
                Registry.CurrentUser.DeleteSubKeyTree(InstallPaths.UninstallKeyPath, false);
                outcome.Step("Removed the Apps & features entry");
            }
            catch (Exception error)
            {
                outcome.Warn("Could not remove the Apps & features entry: " + error.Message);
            }

            RemoveProgramDirectory(outcome, self);

            outcome.Ok = true;
            outcome.Summary = "OmoBench has been removed.";
            outcome.Step("Your benchmark evidence, saved settings, model files and WSL installation were not touched.");
            return outcome;
        }

        private static string SelfPath()
        {
            try { return Path.GetFullPath(System.Reflection.Assembly.GetEntryAssembly().Location); }
            catch (Exception) { return null; }
        }

        private static string FullPathOrNull(string path)
        {
            try { return Path.GetFullPath(path); }
            catch (Exception) { return null; }
        }

        private static bool SamePath(string left, string right)
        {
            if (left == null || right == null) { return false; }
            try
            {
                return string.Equals(Path.GetFullPath(left), Path.GetFullPath(right),
                    StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception) { return false; }
        }

        /// <summary>
        /// Every installed file still on disk, except the one this program is
        /// running from -- which Windows will not let it delete, and which the
        /// relaunch-from-temp path exists to avoid.
        /// </summary>
        private static List<string> RemainingInstalledFiles(string self)
        {
            List<string> remaining = new List<string>();
            try
            {
                if (!Directory.Exists(InstallPaths.ProgramDirectory)) { return remaining; }
                foreach (string file in Directory.GetFiles(
                    InstallPaths.ProgramDirectory, "*", SearchOption.AllDirectories))
                {
                    if (self != null && SamePath(file, self)) { continue; }
                    remaining.Add(file);
                }
            }
            catch (Exception)
            {
                // Unable to look is not the same as nothing there.
                remaining.Add(InstallPaths.ProgramDirectory);
            }
            return remaining;
        }
        /// <summary>
        /// Delete the installed files, keeping the way back until the end.
        ///
        /// Every path is checked to be inside the program directory first, so
        /// a corrupted or tampered manifest cannot turn an uninstall into a
        /// wider deletion. Two files are skipped: the one this program is
        /// running from, which Windows will not let it delete, and the
        /// uninstaller, which the Apps &amp; features entry points at. Deleting
        /// the uninstaller first and then finding the companion locked left
        /// that entry aimed at a program that no longer existed -- an offer to
        /// try again with nothing behind it.
        /// </summary>
        private static void DeleteInstalledFiles(InstallOutcome outcome, string self, string uninstaller)
        {
            if (!Directory.Exists(InstallPaths.ProgramDirectory))
            {
                return;
            }

            foreach (string file in Directory.GetFiles(
                InstallPaths.ProgramDirectory, "*", SearchOption.AllDirectories))
            {
                if (!InstallPaths.IsInsideProgramDirectory(file))
                {
                    continue;   // never reached in practice; refuses anyway
                }
                if (self != null && SamePath(file, self))
                {
                    continue;   // a running program cannot delete its own file
                }
                if (uninstaller != null && SamePath(file, uninstaller))
                {
                    continue;   // kept until everything else has gone
                }
                try { File.Delete(file); }
                catch (Exception error) { outcome.Warn("Could not delete " + file + ": " + error.Message); }
            }
        }

        /// <summary>
        /// Remove the now-empty program directory. Reached only once every
        /// installed file is gone, so what is left is the folder itself --
        /// unless this program is running from inside it.
        /// </summary>
        private static void RemoveProgramDirectory(InstallOutcome outcome, string self)
        {
            if (!Directory.Exists(InstallPaths.ProgramDirectory))
            {
                return;
            }

            if (self != null && InstallPaths.IsInsideProgramDirectory(self))
            {
                // Running from inside what is being removed: the caller should
                // have relaunched a copy elsewhere. Say so rather than
                // pretending the folder is gone.
                outcome.Warn("OmoBench has been removed, but its own uninstaller file is still in\r\n    "
                    + InstallPaths.ProgramDirectory
                    + "\r\nWindows does not let a running program delete itself. "
                    + "You can delete that folder whenever you like.");
                return;
            }

            try
            {
                Directory.Delete(InstallPaths.ProgramDirectory, true);
                outcome.Step("Removed " + InstallPaths.ProgramDirectory);
            }
            catch (Exception error)
            {
                // Name what is still there, rather than reporting a clean
                // removal that did not happen.
                string[] remaining;
                try { remaining = Directory.GetFiles(InstallPaths.ProgramDirectory); }
                catch (Exception) { remaining = new string[0]; }
                outcome.Warn("Could not completely remove " + InstallPaths.ProgramDirectory
                    + ": " + error.Message
                    + (remaining.Length == 0 ? "" :
                        "\r\nStill present: " + string.Join(", ", remaining)
                        + "\r\nThese are usually files still open in another program."));
            }
        }
        /// <summary>
        /// Hand the uninstall to a copy of this program running somewhere
        /// else, so that nothing is left behind.
        ///
        /// A running executable cannot delete its own file, and the two
        /// obvious ways round that are both wrong here: scheduling a delete
        /// for the next restart needs administrator rights this per-user
        /// uninstaller never asks for, and handing the path to cmd.exe puts a
        /// profile-derived path through a shell, where an ampersand or a
        /// percent sign stops being part of the name. Copying this program to
        /// a fresh temporary directory and letting the copy finish the job
        /// needs neither elevation nor a restart, and every path is passed as
        /// an argument rather than as text to be parsed.
        ///
        /// Returns false when the relaunch could not be arranged, in which
        /// case the caller does the cleanup in place and says what was left.
        /// </summary>
        public static bool TryRelaunchForUninstall(string self, bool silent)
        {
            try
            {
                // Only ever relaunch to remove an installation this program is
                // actually part of.
                if (string.IsNullOrEmpty(self) || !InstallPaths.IsInsideProgramDirectory(self))
                {
                    return false;
                }

                string staging = Path.Combine(Path.GetTempPath(),
                    "OmoBench-uninstall-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(staging);
                string copy = Path.Combine(staging, InstallPaths.UninstallerFileName);
                File.Copy(self, copy, true);

                // The pid is passed inside one token, so the switch takes no
                // separate value that could be confused with a path.
                ProcessStartInfo start = new ProcessStartInfo
                {
                    FileName = copy,
                    Arguments = "/uninstall /finalize:"
                        + Process.GetCurrentProcess().Id.ToString(CultureInfo.InvariantCulture)
                        + (silent ? " /silent" : ""),
                    UseShellExecute = false,
                    WorkingDirectory = staging,
                };
                Process.Start(start);
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>
        /// Run by the relaunched copy: wait for the original to let go, then
        /// remove the installation.
        /// </summary>
        public static InstallOutcome FinalizeUninstall(int originalProcessId)
        {
            if (!WaitForExit(originalProcessId, 30000))
            {
                // The original is still holding its own file. Deleting around
                // it would report a clean removal that did not happen.
                InstallOutcome waiting = new InstallOutcome();
                waiting.Ok = false;
                waiting.Summary = "The previous uninstaller has not exited, so nothing was removed.";
                waiting.Step("Nothing was changed. Close any OmoBench window that is still open and "
                    + "run the uninstaller again from Apps & features.");
                return waiting;
            }
            return Uninstall();
        }

        /// <summary>
        /// Wait for a process to exit. Returns false when it is still running,
        /// or when we could not tell -- never "probably gone".
        /// </summary>
        private static bool WaitForExit(int processId, int timeoutMilliseconds)
        {
            if (processId <= 0) { return true; }
            Process original;
            try
            {
                original = Process.GetProcessById(processId);
            }
            catch (ArgumentException)
            {
                return true;    // already gone, which is what we waited for
            }
            catch (Exception)
            {
                return false;   // cannot tell; do not assume
            }
            try
            {
                return original.WaitForExit(timeoutMilliseconds);
            }
            catch (Exception)
            {
                return false;
            }
        }
    }
}
