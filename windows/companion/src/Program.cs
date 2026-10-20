using System;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace OmoBench.Companion
{
    /// <summary>
    /// Entry point.
    ///
    /// Started either by double-click (no arguments) or by Windows handing
    /// over an <c>omobench://</c> link. Exactly one instance runs per user
    /// session: a second launch hands its activation to the first through a
    /// private named pipe and exits, so clicking the website button five
    /// times reuses one panel instead of racing five of them.
    /// </summary>
    internal static class Program
    {
        private const string InstanceName = "OmoBench.Companion.SingleInstance";
        private const string PipeName = "OmoBench.Companion.Activation";
        private const string ActivationMessage = "activate";

        private static MainForm _window;

        [STAThread]
        private static int Main(string[] argv)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            // Validate the activation argument before anything else happens.
            // Anything that is not exactly our one supported action is
            // refused here, and never reaches configuration or a process
            // start.
            bool activatedByProtocol = false;
            if (argv.Length > 0)
            {
                if (argv.Length > 1)
                {
                    Refuse("This companion takes a single omobench://open link and nothing else.");
                    return 2;
                }

                // A few local maintenance switches, matched as exact literals.
                // A protocol activation can never reach them: Windows always
                // hands this program the whole URI as one argument, and every
                // URI begins with its scheme, so no link can spell "--check".
                // They exist for support and scripted verification; ordinary
                // setup and launch never need them.
                int maintenance;
                if (TryRunMaintenance(argv[0], out maintenance))
                {
                    return maintenance;
                }

                Protocol.Result parsed = Protocol.Parse(argv[0]);
                if (!parsed.Accepted)
                {
                    Diagnostics.Write("Refused an activation link: " + parsed.Rejection);
                    Refuse(parsed.Rejection + "\n\nOnly omobench://open is accepted.");
                    return 2;
                }
                activatedByProtocol = true;
                Diagnostics.Write("Accepted an omobench://open activation.");
            }

            bool createdNew;
            using (Mutex instanceLock = new Mutex(true, InstanceName, out createdNew))
            {
                if (!createdNew)
                {
                    // Another companion already owns this user session: ask it
                    // to bring its panel forward rather than starting a second.
                    if (SendActivation())
                    {
                        Diagnostics.Write("Handed the activation to the companion already running.");
                        return 0;
                    }
                    Refuse("Another OmoBench companion is running but did not respond.\n\n"
                        + "Use its window, or close it and try again.");
                    return 1;
                }

                // Only the instance that owns this user session cleans up:
                // status documents are named with a fresh GUID per start, so
                // anything still here belongs to a run that ended without a
                // clean stop, and may contain a connection token.
                CleanStaleStatusFiles();

                Configuration configuration;
                try
                {
                    configuration = Configuration.Load();
                }
                catch (Exception error)
                {
                    Diagnostics.Write("Could not read the configuration: " + error.Message);
                    configuration = new Configuration();
                }

                _window = new MainForm(configuration);
                StartActivationListener();

                // A protocol launch goes straight to work; a double-click
                // shows the window and waits for the person to choose.
                _window.Shown += (sender, e) =>
                {
                    if (activatedByProtocol)
                    {
                        _window.Activate(true);
                    }
                    else if (!configuration.IsComplete)
                    {
                        _window.Activate(false);
                    }
                };

                Application.Run(_window);
                return 0;
            }
        }

        private static void Refuse(string message)
        {
            MessageBox.Show(message, "OmoBench companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }

        /// <summary>
        /// Local maintenance switches. Each writes its outcome to the
        /// diagnostic log and reports it as an exit code, so they work without
        /// a console: 0 success, 3 a problem the person must fix, 1 an error.
        /// </summary>
        private static bool TryRunMaintenance(string argument, out int exitCode)
        {
            exitCode = 0;
            switch (argument)
            {
                case "--register":
                    try
                    {
                        ProtocolRegistration.Register(Application.ExecutablePath);
                        Diagnostics.Write("--register succeeded.");
                    }
                    catch (Exception error)
                    {
                        Diagnostics.Write("--register failed: " + error.Message);
                        exitCode = 1;
                    }
                    return true;

                case "--unregister":
                    try
                    {
                        Diagnostics.Write("--unregister: " + ProtocolRegistration.Unregister(Application.ExecutablePath));
                    }
                    catch (Exception error)
                    {
                        Diagnostics.Write("--unregister failed: " + error.Message);
                        exitCode = 1;
                    }
                    return true;

                case "--check":
                    try
                    {
                        Configuration configuration = Configuration.Load();
                        string invalid = configuration.Validate();
                        if (invalid != null)
                        {
                            Diagnostics.Write("--check: configuration problem: " + invalid);
                            exitCode = 3;
                            return true;
                        }
                        EnvironmentCheckResult check = EnvironmentCheck.Run(configuration);
                        Diagnostics.Write("--check: " + check.Summary
                            + (string.IsNullOrEmpty(check.Detail) ? "" : " | " + check.Detail.Replace("\n", " ")));
                        exitCode = check.Ok ? 0 : 3;
                    }
                    catch (Exception error)
                    {
                        Diagnostics.Write("--check failed: " + error.Message);
                        exitCode = 1;
                    }
                    return true;

                default:
                    return false;
            }
        }

        private static void CleanStaleStatusFiles()
        {
            try
            {
                string directory = Configuration.RuntimeDirectoryPath;
                if (!Directory.Exists(directory))
                {
                    return;
                }
                foreach (string path in Directory.GetFiles(directory, "panel-*.json"))
                {
                    try
                    {
                        File.Delete(path);
                        Diagnostics.Write("Removed a status document left by an earlier run.");
                    }
                    catch (Exception) { }
                }
            }
            catch (Exception) { }
        }

        /// <summary>
        /// Listen for activations from later launches. The pipe is created
        /// with default (owner-only) security in the user's own session and
        /// carries one fixed word; it never transports a path, a command, or
        /// a token.
        /// </summary>
        private static void StartActivationListener()
        {
            Thread listener = new Thread(() =>
            {
                while (true)
                {
                    try
                    {
                        using (NamedPipeServerStream pipe = new NamedPipeServerStream(
                            PipeName, PipeDirection.In, 1, PipeTransmissionMode.Byte, PipeOptions.None))
                        {
                            pipe.WaitForConnection();
                            using (StreamReader reader = new StreamReader(pipe, Encoding.UTF8))
                            {
                                string message = reader.ReadLine();
                                if (string.Equals(message, ActivationMessage, StringComparison.Ordinal))
                                {
                                    MainForm window = _window;
                                    if (window != null && window.IsHandleCreated)
                                    {
                                        window.BeginInvoke(new Action(() => window.Activate(true)));
                                    }
                                }
                            }
                        }
                    }
                    catch (Exception)
                    {
                        Thread.Sleep(250); // keep listening; never take the app down
                    }
                }
            });
            listener.IsBackground = true;
            listener.Start();
        }

        private static bool SendActivation()
        {
            try
            {
                using (NamedPipeClientStream pipe = new NamedPipeClientStream(".", PipeName, PipeDirection.Out))
                {
                    pipe.Connect(4000);
                    using (StreamWriter writer = new StreamWriter(pipe, new UTF8Encoding(false)))
                    {
                        writer.WriteLine(ActivationMessage);
                        writer.Flush();
                    }
                }
                return true;
            }
            catch (Exception error)
            {
                Diagnostics.Write("Could not reach the running companion: " + error.Message);
                return false;
            }
        }
    }
}
