using System;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

namespace OmoBench.Installer
{
    /// <summary>
    /// Entry point for OmoBenchSetup.exe.
    ///
    /// Double-clicking opens the guided window. The switches exist so that the
    /// Apps &amp; features entry has something to call, and so the installer can
    /// be scripted; they are matched as exact literals and take no values, so
    /// nothing a caller passes becomes part of a path or a command.
    /// </summary>
    internal static class InstallerProgram
    {
        /// <summary>The companion, embedded at build time.</summary>
        private const string PayloadResource = "OmoBenchCompanion.exe";

        [STAThread]
        private static int Main(string[] argv)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            bool uninstall = false, silent = false, help = false;
            foreach (string argument in argv)
            {
                switch ((argument ?? "").ToLowerInvariant())
                {
                    case "/uninstall": case "--uninstall": uninstall = true; break;
                    case "/silent": case "--silent": case "/s": silent = true; break;
                    case "/?": case "/help": case "--help": help = true; break;
                    default:
                        // Unknown input is refused rather than guessed at.
                        Show(silent, "OmoBench setup does not understand: " + argument,
                            MessageBoxIcon.Warning);
                        return 2;
                }
            }

            BuildInfo build = BuildInfo.Current();

            if (help)
            {
                Show(false,
                    "OmoBench setup\n\n" + build.Describe().Replace("\r\n", "\n")
                    + "\n\nNo switches   Guided installation\n"
                    + "/uninstall    Remove OmoBench\n"
                    + "/silent       No window (with /uninstall, or for a plain install)\n\n"
                    + "Installs for the current user only. Benchmark evidence, saved settings and "
                    + "WSL are never removed.",
                    MessageBoxIcon.Information);
                return 0;
            }

            if (uninstall)
            {
                return RunUninstall(silent);
            }

            if (silent)
            {
                return RunSilentInstall(build);
            }

            Application.Run(new InstallerForm(build, ReadPayload()));
            return 0;
        }

        private static int RunUninstall(bool silent)
        {
            if (!silent)
            {
                DialogResult choice = MessageBox.Show(
                    "Remove OmoBench from this computer?\n\n"
                    + "Your benchmark evidence, saved settings, downloaded model files and your Linux "
                    + "(WSL) installation are kept. Only the OmoBench program, its Start menu entry and "
                    + "its omobench:// registration are removed.",
                    "Uninstall OmoBench", MessageBoxButtons.OKCancel, MessageBoxIcon.Question);
                if (choice != DialogResult.OK)
                {
                    return 1602;   // the conventional "user cancelled" code
                }
            }

            InstallOutcome outcome = Installation.Uninstall();
            if (!silent)
            {
                MessageBox.Show(outcome.Describe(), "Uninstall OmoBench",
                    MessageBoxButtons.OK,
                    outcome.Ok ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
            }
            return outcome.Ok ? 0 : 1;
        }

        /// <summary>
        /// A plain install with no window: it puts the program in place and
        /// stops there. It deliberately does not prepare an environment --
        /// that can need a restart, a Linux password, or several minutes of
        /// downloading, none of which belong in an unattended run.
        /// </summary>
        private static int RunSilentInstall(BuildInfo build)
        {
            InstallOutcome outcome = Installation.Install(
                ReadPayload(), Application.ExecutablePath, build);
            return outcome.Ok ? 0 : 1;
        }

        private static byte[] ReadPayload()
        {
            try
            {
                Assembly assembly = Assembly.GetExecutingAssembly();
                foreach (string name in assembly.GetManifestResourceNames())
                {
                    if (name.EndsWith(PayloadResource, StringComparison.OrdinalIgnoreCase))
                    {
                        using (Stream stream = assembly.GetManifestResourceStream(name))
                        {
                            if (stream == null) { return null; }
                            byte[] payload = new byte[stream.Length];
                            int read = 0;
                            while (read < payload.Length)
                            {
                                int chunk = stream.Read(payload, read, payload.Length - read);
                                if (chunk <= 0) { break; }
                                read += chunk;
                            }
                            return payload;
                        }
                    }
                }
            }
            catch (Exception) { }
            return null;
        }

        private static void Show(bool silent, string message, MessageBoxIcon icon)
        {
            if (!silent)
            {
                MessageBox.Show(message, "OmoBench setup", MessageBoxButtons.OK, icon);
            }
        }
    }
}
