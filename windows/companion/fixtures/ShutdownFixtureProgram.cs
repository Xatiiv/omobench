using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace OmoBench.Companion.Fixtures
{
    /// <summary>
    /// The real companion window, managing a panel that is not a panel.
    ///
    /// One behaviour can only be observed on a running Windows desktop: what
    /// the companion does when something asks it to close while it owns a
    /// running panel. It shows its own question -- stop the panel, keep it, or
    /// go back -- and answering No or Cancel has to keep the companion alive,
    /// because the installer waiting outside treats that refusal as an answer
    /// and postpones its update.
    ///
    /// Observing that used to mean a real panel, a real checkout and a real
    /// benchmark. This fixture needs none of them:
    ///
    ///   - the configuration, runtime directory, log and protocol registration
    ///     are all redirected into a throwaway sandbox, so the person's own
    ///     installation is neither read nor written;
    ///   - the "panel" is a shell script on the Windows side, reached through
    ///     /mnt like any checkout, which writes the status document the
    ///     companion expects and then waits. It installs nothing, downloads
    ///     nothing, runs no benchmark, and creates no file inside the
    ///     distribution;
    ///   - everything above is removed when the window closes.
    ///
    /// Beyond the four redirections, this is the shipped companion: the window,
    /// the session, the closing question and the shutdown are all the
    /// production code.
    /// </summary>
    internal static class ShutdownFixtureProgram
    {
        [STAThread]
        private static int Main(string[] arguments)
        {
            string distribution = arguments.Length > 0 ? arguments[0] : "Ubuntu";
            int port = 17890;
            if (arguments.Length > 1
                && !int.TryParse(arguments[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out port))
            {
                Console.Error.WriteLine("usage: OmoBenchShutdownFixture.exe [distribution] [port]");
                return 2;
            }

            string sandbox = Path.Combine(Path.GetTempPath(),
                "omobench-shutdown-fixture-" + Guid.NewGuid().ToString("N"));
            string checkout = Path.Combine(sandbox, "checkout");

            try
            {
                Directory.CreateDirectory(checkout);

                // Everything the companion would otherwise write into the
                // person's profile.
                Configuration.FilePathOverride = Path.Combine(sandbox, "companion.config");
                Configuration.RuntimeDirectoryOverride = Path.Combine(sandbox, "runtime");
                ProtocolRegistration.KeyPathOverride =
                    @"Software\OmoBench-fixture\" + Guid.NewGuid().ToString("N") + @"\omobench";

                string script = Path.Combine(checkout, "fixture-panel.sh");
                File.WriteAllText(script, FixturePanelScript(), new UTF8Encoding(false));

                string scriptWslPath;
                if (!CommandLine.TryToWslPath(script, out scriptWslPath))
                {
                    Console.Error.WriteLine("WSL cannot reach " + script + ".");
                    return 2;
                }

                Configuration configuration = new Configuration
                {
                    Distribution = distribution,
                    CheckoutWindowsPath = checkout,
                    PythonWslPath = scriptWslPath,
                    Port = port,
                };
                configuration.Save();

                Console.WriteLine("Shutdown fixture");
                Console.WriteLine("  sandbox      : " + sandbox);
                Console.WriteLine("  configuration: " + Configuration.FilePath);
                Console.WriteLine("  runtime      : " + Configuration.RuntimeDirectoryPath);
                Console.WriteLine("  registration : HKCU\\" + ProtocolRegistration.KeyPath);
                Console.WriteLine("  fixture panel: " + scriptWslPath + " in " + distribution);
                Console.WriteLine();
                Console.WriteLine("The companion window is the real one. Nothing outside the sandbox");
                Console.WriteLine("is read or written, and no benchmark is run.");

                Application.EnableVisualStyles();
                using (MainForm form = new MainForm(configuration))
                {
                    // Start the fixture panel as soon as the window is up, and
                    // do not open a browser: there is nothing to open.
                    form.Shown += (sender, e) => form.Activate(false);
                    Application.Run(form);
                }
                return 0;
            }
            finally
            {
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
                try
                {
                    Microsoft.Win32.Registry.CurrentUser.DeleteSubKeyTree(
                        @"Software\OmoBench-fixture", false);
                }
                catch (Exception) { }
            }
        }

        /// <summary>
        /// A panel that only does the two things the companion depends on:
        /// write the status document, and stay running until it is told to
        /// stop. Written with explicit newlines, because it is read by /bin/sh
        /// and a carriage return would end up in the shebang.
        /// </summary>
        private static string FixturePanelScript()
        {
            string[] lines =
            {
                "#!/bin/sh",
                "# OmoBench shutdown fixture. Not the panel, and not a benchmark:",
                "# it reports itself ready and then waits to be stopped.",
                "port=''",
                "status=''",
                "while [ $# -gt 0 ]; do",
                "  case \"$1\" in",
                "    --port) port=\"$2\"; shift 2 ;;",
                "    --status-file) status=\"$2\"; shift 2 ;;",
                "    *) shift ;;",
                "  esac",
                "done",
                "token=\"fixture-$$\"",
                "printf '{\"schema\":1,\"state\":\"ready\",\"reason\":\"\",\"message\":\"fixture panel\","
                    + "\"url\":\"http://127.0.0.1:%s/#token=%s\",\"origin\":\"http://127.0.0.1:%s\","
                    + "\"token\":\"%s\",\"port\":%s,\"pid\":%s}' \\",
                "  \"$port\" \"$token\" \"$port\" \"$token\" \"$port\" \"$$\" > \"$status\"",
                "while true; do sleep 5; done",
                "",
            };
            return string.Join("\n", lines);
        }
    }
}
