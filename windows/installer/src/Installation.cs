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

        /// <summary>Finds companions to coordinate with. Replaced by tests only.</summary>
        internal static Func<List<CompanionProcess>> CompanionEnumerator = EnumerateInstalledCompanions;

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
        public static List<CompanionProcess> RunningInstalledCompanions()
        {
            List<CompanionProcess> found;
            try { found = CompanionEnumerator() ?? new List<CompanionProcess>(); }
            catch (Exception) { return new List<CompanionProcess>(); }

            // An enumeration is a snapshot. Asking each one whether it has
            // actually gone is what makes waiting for a close request mean
            // something -- and a process we cannot ask is treated as still
            // running, because "probably gone" is not an answer worth
            // deleting a program on.
            List<CompanionProcess> running = new List<CompanionProcess>();
            foreach (CompanionProcess process in found)
            {
                if (process == null) { continue; }
                bool exited;
                try { exited = process.HasExited != null && process.HasExited(); }
                catch (Exception) { exited = false; }
                if (!exited) { running.Add(process); }
            }
            return running;
        }

        private static List<CompanionProcess> EnumerateInstalledCompanions()
        {
            List<CompanionProcess> running = new List<CompanionProcess>();
            string installed;
            try { installed = Path.GetFullPath(InstallPaths.CompanionPath); }
            catch (Exception) { return running; }

            Process[] candidates;
            try { candidates = Process.GetProcessesByName("OmoBenchCompanion"); }
            catch (Exception) { return running; }

            foreach (Process process in candidates)
            {
                string path = null;
                try { path = process.MainModule == null ? null : process.MainModule.FileName; }
                catch (Exception)
                {
                    // A process we cannot inspect is not one we can attribute
                    // to this installation, so it is left alone.
                    continue;
                }
                if (path == null || !string.Equals(
                        Path.GetFullPath(path), installed, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                Process captured = process;
                running.Add(new CompanionProcess
                {
                    Id = SafeId(captured),
                    HasExited = () => { try { return captured.HasExited; } catch (Exception) { return true; } },
                    RequestClose = () => { try { captured.CloseMainWindow(); } catch (Exception) { } },
                });
            }
            return running;
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
            List<CompanionProcess> running = RunningInstalledCompanions();
            if (running.Count == 0)
            {
                return true;
            }

            foreach (CompanionProcess process in running)
            {
                try { process.RequestClose(); }
                catch (Exception) { }
            }

            DateTime deadline = DateTime.UtcNow.AddMilliseconds(waitMilliseconds);
            while (DateTime.UtcNow < deadline)
            {
                if (RunningInstalledCompanions().Count == 0)
                {
                    return true;
                }
                System.Threading.Thread.Sleep(250);
            }
            return RunningInstalledCompanions().Count == 0;
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

            // Files first. The Apps & features entry and the omobench://
            // registration are how a person reaches this program -- and how
            // they would reach the uninstaller again -- so they are removed
            // only once the files they point at are actually gone.
            try
            {
                RemoveProgramFiles(outcome);
            }
            catch (Exception error)
            {
                outcome.Warn("Some program files could not be removed: " + error.Message);
            }

            // The companion executable and the manifest beside it are what make
            // this an installation. If either is still there, the removal did
            // not happen, whatever else succeeded -- and saying otherwise
            // leaves a person with a program they were told was gone and no
            // entry to retry from.
            List<string> stillPresent = new List<string>();
            foreach (string required in new string[] { InstallPaths.CompanionPath, InstallPaths.ManifestPath })
            {
                try { if (File.Exists(required)) { stillPresent.Add(required); } }
                catch (Exception) { stillPresent.Add(required); }
            }

            if (stillPresent.Count > 0)
            {
                outcome.Ok = false;
                outcome.Summary = "OmoBench could not be completely removed.";
                foreach (string remaining in stillPresent)
                {
                    outcome.Step("Still present: " + remaining);
                }
                outcome.Step("This usually means the program is still open. Its Apps & features entry "
                    + "and omobench:// registration have been left in place, so close it and "
                    + "uninstall again from there.");
                return outcome;
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

            outcome.Ok = true;
            outcome.Summary = "OmoBench has been removed.";
            outcome.Step("Your benchmark evidence, saved settings, model files and WSL installation were not touched.");
            return outcome;
        }

        /// <summary>
        /// Delete what was installed. Every path is checked to be inside the
        /// program directory first, so a corrupted or tampered manifest cannot
        /// turn an uninstall into a wider deletion. The running uninstaller
        /// itself cannot delete its own file, so it is scheduled instead.
        /// </summary>
        private static void RemoveProgramFiles(InstallOutcome outcome)
        {
            if (!Directory.Exists(InstallPaths.ProgramDirectory))
            {
                return;
            }

            string self = null;
            try { self = Path.GetFullPath(System.Reflection.Assembly.GetEntryAssembly().Location); }
            catch (Exception) { }

            foreach (string file in Directory.GetFiles(InstallPaths.ProgramDirectory))
            {
                if (!InstallPaths.IsInsideProgramDirectory(file))
                {
                    continue;   // never reached in practice; refuses anyway
                }
                if (self != null && string.Equals(file, self, StringComparison.OrdinalIgnoreCase))
                {
                    continue;   // handled below
                }
                try { File.Delete(file); }
                catch (Exception error) { outcome.Warn("Could not delete " + file + ": " + error.Message); }
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
