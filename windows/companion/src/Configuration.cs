using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;

namespace OmoBench.Companion
{
    /// <summary>
    /// Per-user configuration: which WSL distribution, which checkout, which
    /// Python interpreter, which port. Nothing here is compiled into the
    /// application -- the values below are whatever the person chose during
    /// setup on their own machine.
    ///
    /// Stored as a tiny key=value file under %APPDATA%\OmoBench so it can be
    /// inspected and deleted by hand. The connection token is never written
    /// here; it lives only in the panel's own short-lived status document.
    /// </summary>
    public sealed class Configuration
    {
        public const int DefaultPort = 8765;

        public string Distribution { get; set; }
        public string CheckoutWindowsPath { get; set; }
        public string PythonWslPath { get; set; }
        public int Port { get; set; }

        public Configuration()
        {
            Distribution = string.Empty;
            CheckoutWindowsPath = string.Empty;
            PythonWslPath = string.Empty;
            Port = DefaultPort;
        }

        public static string DirectoryPath
        {
            get
            {
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "OmoBench");
            }
        }

        /// <summary>
        /// Redirects the saved configuration, so a test can exercise saving
        /// and loading without touching the configuration a person depends on.
        /// Null in normal use.
        /// </summary>
        internal static string FilePathOverride = null;

        public static string FilePath
        {
            get
            {
                return string.IsNullOrEmpty(FilePathOverride)
                    ? Path.Combine(DirectoryPath, "companion.config")
                    : FilePathOverride;
            }
        }

        /// <summary>
        /// Redirects the runtime directory for tests, so a test can exercise
        /// privacy enforcement -- including its failure -- against a throwaway
        /// folder instead of the directory the real companion depends on.
        /// Null in normal use.
        /// </summary>
        internal static string RuntimeDirectoryOverride = null;

        /// <summary>
        /// Applies the protected access list. Replaceable so a test can inject
        /// a failure; production never reassigns it.
        /// </summary>
        internal static Action<string> AccessControlApplier = ApplyProtectedAccessControl;

        /// <summary>
        /// Where the panel's status document is written. Under the per-user
        /// local app data folder, not the checkout and not a shared temp
        /// directory, because that document carries the connection token.
        /// </summary>
        public static string RuntimeDirectoryPath
        {
            get
            {
                if (!string.IsNullOrEmpty(RuntimeDirectoryOverride))
                {
                    return RuntimeDirectoryOverride;
                }
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "OmoBench", "companion");
            }
        }

        /// <summary>
        /// Create the runtime directory, without touching its access list.
        ///
        /// Best effort and deliberately silent: the diagnostic log lives in
        /// this directory, so this is called from <see cref="Diagnostics"/>
        /// itself and must never log, never enforce, and never throw. Privacy
        /// enforcement is <see cref="TryEnsureRuntimeDirectory"/>'s job, and a
        /// failure there is reported to the person rather than written here.
        /// </summary>
        internal static void EnsureRuntimeDirectoryExists()
        {
            try
            {
                Directory.CreateDirectory(RuntimeDirectoryPath);
            }
            catch (Exception)
            {
                // The caller is a logger. It has nowhere to report this to.
            }
        }

        /// <summary>
        /// Create the runtime directory and make it readable by this user
        /// alone. Returns false, with a reason, if that cannot be established.
        ///
        /// Neither of the obvious protections can be relied on here. The panel
        /// writes the status document from inside WSL, through /mnt/c, and the
        /// default DrvFs mount has no `metadata` option, so the 0600 mode the
        /// panel asks for is silently discarded (the file reads back as 0777).
        /// Inheritance is not dependable either: %LOCALAPPDATA% normally grants
        /// only SYSTEM, Administrators and the owner, but machines do carry
        /// extra inherited groups, and this directory holds a live connection
        /// token.
        ///
        /// So the access list is set explicitly instead: inheritance is
        /// disabled, and only this user (plus SYSTEM, which can read anything
        /// regardless) is granted access. Files created inside inherit that.
        ///
        /// This reports rather than logs, and never calls the logger: the log
        /// file is inside the directory this is trying to secure, so a logging
        /// failure path here would re-enter directory creation indefinitely.
        /// A caller that cannot establish privacy must not publish a token
        /// document -- see <see cref="PanelSession.Start"/>.
        /// </summary>
        public static bool TryEnsureRuntimeDirectory(out string failure)
        {
            failure = null;
            string path = RuntimeDirectoryPath;
            try
            {
                Directory.CreateDirectory(path);
            }
            catch (Exception error)
            {
                failure = "Could not create " + path + ": " + error.Message;
                return false;
            }

            try
            {
                AccessControlApplier(path);
                return true;
            }
            catch (Exception error)
            {
                failure = "Could not restrict access to " + path + ": " + error.Message;
                return false;
            }
        }

        private static void ApplyProtectedAccessControl(string path)
        {
            DirectoryInfo directory = new DirectoryInfo(path);
            DirectorySecurity security = directory.GetAccessControl();

            // Stop inheriting, and drop whatever was inherited or added.
            security.SetAccessRuleProtection(true, false);
            foreach (FileSystemAccessRule rule in
                security.GetAccessRules(true, false, typeof(SecurityIdentifier)))
            {
                security.RemoveAccessRuleSpecific(rule);
            }

            SecurityIdentifier user = WindowsIdentity.GetCurrent().User;
            security.AddAccessRule(new FileSystemAccessRule(
                user, FileSystemRights.FullControl,
                InheritanceFlags.ObjectInherit | InheritanceFlags.ContainerInherit,
                PropagationFlags.None, AccessControlType.Allow));

            SecurityIdentifier system = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
            security.AddAccessRule(new FileSystemAccessRule(
                system, FileSystemRights.FullControl,
                InheritanceFlags.ObjectInherit | InheritanceFlags.ContainerInherit,
                PropagationFlags.None, AccessControlType.Allow));

            directory.SetAccessControl(security);
        }

        /// <summary>
        /// The identities allowed on the runtime directory, for verification.
        /// </summary>
        public static string[] RuntimeDirectoryAccessIdentities()
        {
            DirectoryInfo directory = new DirectoryInfo(RuntimeDirectoryPath);
            DirectorySecurity security = directory.GetAccessControl();
            AuthorizationRuleCollection rules = security.GetAccessRules(true, true, typeof(NTAccount));
            string[] identities = new string[rules.Count];
            for (int index = 0; index < rules.Count; index++)
            {
                identities[index] = rules[index].IdentityReference.Value;
            }
            return identities;
        }

        public bool IsComplete
        {
            get
            {
                return !string.IsNullOrWhiteSpace(Distribution)
                    && !string.IsNullOrWhiteSpace(CheckoutWindowsPath)
                    && !string.IsNullOrWhiteSpace(PythonWslPath)
                    && Port >= 1 && Port <= 65535;
            }
        }

        /// <summary>Validate a loaded or user-entered configuration, returning why it is unusable.</summary>
        public string Validate()
        {
            if (string.IsNullOrWhiteSpace(Distribution))
            {
                return "No WSL distribution is selected.";
            }
            if (string.IsNullOrWhiteSpace(CheckoutWindowsPath))
            {
                return "No OmoBench checkout is selected.";
            }
            // Shape first, then existence: "WSL cannot reach a network share"
            // is the useful answer for \\server\share, and stays true whether
            // or not that share happens to be mounted right now.
            string wslCheckout;
            if (!CommandLine.TryToWslPath(CheckoutWindowsPath, out wslCheckout))
            {
                return "The checkout must be on a local drive that WSL can reach as /mnt/<drive>: " + CheckoutWindowsPath;
            }
            if (!Directory.Exists(CheckoutWindowsPath))
            {
                return "The configured checkout folder no longer exists: " + CheckoutWindowsPath;
            }
            if (string.IsNullOrWhiteSpace(PythonWslPath))
            {
                return "No Python interpreter is selected.";
            }
            if (!PythonWslPath.StartsWith("/", StringComparison.Ordinal))
            {
                return "The Python interpreter must be an absolute Linux path, for example /home/you/.venvs/omobench-runner/bin/python.";
            }
            if (Port < 1 || Port > 65535)
            {
                return "The panel port must be between 1 and 65535.";
            }
            return null;
        }

        public string CheckoutWslPath
        {
            get
            {
                string wslPath;
                return CommandLine.TryToWslPath(CheckoutWindowsPath, out wslPath) ? wslPath : null;
            }
        }

        public static Configuration Load()
        {
            Configuration configuration = new Configuration();
            string path = FilePath;
            if (!File.Exists(path))
            {
                return configuration;
            }

            foreach (string rawLine in File.ReadAllLines(path, Encoding.UTF8))
            {
                string line = rawLine.Trim();
                if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
                {
                    continue;
                }
                int separator = line.IndexOf('=');
                if (separator <= 0)
                {
                    continue;
                }
                string key = line.Substring(0, separator).Trim();
                string value = line.Substring(separator + 1).Trim();
                switch (key)
                {
                    case "distribution":
                        configuration.Distribution = value;
                        break;
                    case "checkout":
                        configuration.CheckoutWindowsPath = value;
                        break;
                    case "python":
                        configuration.PythonWslPath = value;
                        break;
                    case "port":
                        int port;
                        if (int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out port))
                        {
                            configuration.Port = port;
                        }
                        break;
                }
            }
            return configuration;
        }

        public void Save()
        {
            string directory = Path.GetDirectoryName(FilePath);
            if (!string.IsNullOrEmpty(directory)) { Directory.CreateDirectory(directory); }
            List<string> lines = new List<string>
            {
                "# OmoBench companion configuration (per user).",
                "# Written by the companion's Setup window; safe to delete.",
                "# No connection token is ever stored here.",
                "distribution=" + Distribution,
                "checkout=" + CheckoutWindowsPath,
                "python=" + PythonWslPath,
                "port=" + Port.ToString(CultureInfo.InvariantCulture),
            };
            File.WriteAllLines(FilePath, lines, new UTF8Encoding(false));
        }

        /// <summary>Remove this companion's own configuration. Touches nothing else.</summary>
        public static bool Delete()
        {
            if (!File.Exists(FilePath))
            {
                return false;
            }
            File.Delete(FilePath);
            return true;
        }
    }
}
