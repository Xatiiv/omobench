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
        public static List<Process> RunningInstalledCompanions()
        {
            List<Process> running = new List<Process>();
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
                if (path != null && string.Equals(
                        Path.GetFullPath(path), installed, StringComparison.OrdinalIgnoreCase))
                {
                    running.Add(process);
                }
            }
            return running;
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
            List<Process> running = RunningInstalledCompanions();
            if (running.Count == 0)
            {
                return true;
            }

            foreach (Process process in running)
            {
                try { process.CloseMainWindow(); }
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

            try
            {
                RemoveProgramFiles(outcome);
            }
            catch (Exception error)
            {
                outcome.Warn("Some program files could not be removed: " + error.Message);
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
                ScheduleSelfDeletion(self, outcome);
                return;
            }

            try
            {
                Directory.Delete(InstallPaths.ProgramDirectory, true);
                outcome.Step("Removed " + InstallPaths.ProgramDirectory);
            }
            catch (Exception error)
            {
                outcome.Warn("Could not remove " + InstallPaths.ProgramDirectory + ": " + error.Message);
            }
        }

        private const int MoveFileDelayUntilReboot = 0x4;

        [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true,
            CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
        private static extern bool MoveFileEx(string existingFileName, string newFileName, int flags);

        /// <summary>
        /// A running executable cannot delete its own file, so the last two
        /// items are handed to Windows to remove at the next restart.
        ///
        /// This used to spawn cmd.exe with the directory path in the command
        /// line. Even quoted, that path then went through a shell: it comes
        /// from the user's profile, so a name containing an ampersand would
        /// have been read as a command separator and a percent sign as a
        /// variable reference -- turning a cleanup into something else
        /// entirely. MoveFileEx takes the path as an argument, not as text to
        /// be parsed, so no character in it can mean anything.
        /// </summary>
        private static void ScheduleSelfDeletion(string self, InstallOutcome outcome)
        {
            string directory = InstallPaths.ProgramDirectory;
            bool scheduledFile = false, scheduledDirectory = false;

            try
            {
                scheduledFile = MoveFileEx(self, null, MoveFileDelayUntilReboot);
            }
            catch (Exception) { }

            try
            {
                // An empty directory can be scheduled the same way; it is only
                // removed if nothing remains inside it at restart.
                scheduledDirectory = MoveFileEx(directory, null, MoveFileDelayUntilReboot);
            }
            catch (Exception) { }

            if (scheduledFile && scheduledDirectory)
            {
                outcome.Step("The uninstaller is still running, so " + directory
                    + " will be removed when you next restart Windows.");
                return;
            }

            // Say what actually happened rather than implying success.
            //
            // Delaying a delete until restart is recorded in a machine-wide
            // registry value, so it needs administrator rights -- which this
            // per-user uninstaller deliberately never asks for. The usual
            // outcome is therefore that everything is removed except the
            // uninstaller's own file, which cannot delete itself while it is
            // running. That is a leftover of one file, and it is said plainly
            // rather than glossed over.
            outcome.Warn("OmoBench has been removed, but its own uninstaller file is still in\r\n    "
                + directory + "\r\nWindows does not let a running program delete itself, and removing "
                + "it at restart would need administrator rights this uninstaller does not ask for. "
                + "Nothing there is used any more -- you can delete that folder whenever you like.");
        }
    }
}
