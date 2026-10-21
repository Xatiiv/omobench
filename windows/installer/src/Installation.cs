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
        /// Processes that would keep the program files locked. Returned rather
        /// than killed: closing someone's running panel without asking is not
        /// the installer's decision to make.
        /// </summary>
        public static List<Process> RunningCompanions()
        {
            List<Process> running = new List<Process>();
            try
            {
                foreach (Process process in Process.GetProcessesByName("OmoBenchCompanion"))
                {
                    running.Add(process);
                }
            }
            catch (Exception) { }
            return running;
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

        /// <summary>
        /// A running executable cannot delete itself, so a short-lived cmd.exe
        /// waits for this process to exit and then removes the directory. It
        /// is given a fixed command with one quoted path and nothing derived
        /// from user input.
        /// </summary>
        private static void ScheduleSelfDeletion(string self, InstallOutcome outcome)
        {
            try
            {
                string directory = InstallPaths.ProgramDirectory;
                ProcessStartInfo start = new ProcessStartInfo
                {
                    FileName = Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.System), "cmd.exe"),
                    Arguments = "/c ping 127.0.0.1 -n 3 >nul & rd /s /q "
                        + CommandLine.Quote(directory),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                Process.Start(start);
                outcome.Step("Removing " + directory + " as the uninstaller exits");
            }
            catch (Exception error)
            {
                outcome.Warn("The program folder could not be scheduled for removal: " + error.Message
                    + " You can delete " + InstallPaths.ProgramDirectory + " by hand.");
            }
        }
    }
}
