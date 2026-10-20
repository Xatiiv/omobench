using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace OmoBench.Companion
{
    public enum PanelState
    {
        Stopped,
        Starting,
        Ready,
        Failed,
    }

    /// <summary>
    /// Owns exactly one panel process: the one this companion started.
    ///
    /// Two boundaries matter here. First, ownership: stopping only ever
    /// signals the wsl.exe child this object launched, so a panel someone
    /// started by hand in a terminal is never killed, and `wsl --shutdown` is
    /// never used. Second, the one-panel-per-checkout lock in the panel
    /// itself stays in force -- when it reports "already_running", this class
    /// surfaces that instead of trying to take the checkout over.
    /// </summary>
    public sealed class PanelSession
    {
        private readonly object _gate = new object();
        private Process _process;
        private string _statusFileWindowsPath;

        /// <summary>
        /// The distribution and in-WSL process id of the panel this companion
        /// started, taken from the status document it wrote. Used to stop it
        /// the way Ctrl+C would, rather than killing the wsl.exe session out
        /// from under it -- see <see cref="StopOwnedProcess"/>.
        /// </summary>
        private string _distribution;
        private int _panelProcessId;

        public PanelState State { get; private set; }
        public string Message { get; private set; }
        public string Detail { get; private set; }
        public int Port { get; private set; }

        /// <summary>
        /// The authenticated panel URL. Held in memory only: it carries the
        /// connection token, so it is never logged and never written to a
        /// repository file. <see cref="OpenInBrowser"/> hands it straight to
        /// the default browser.
        /// </summary>
        private string _url;

        private string _token;
        private string _origin;

        public PanelSession()
        {
            State = PanelState.Stopped;
            Message = "Not started.";
        }

        public bool IsRunning
        {
            get
            {
                lock (_gate)
                {
                    return _process != null && !_process.HasExited;
                }
            }
        }

        public bool HasUrl
        {
            get { lock (_gate) { return !string.IsNullOrEmpty(_url); } }
        }

        /// <summary>True when this companion already owns a ready panel.</summary>
        public bool IsReadyAndAlive
        {
            get { return State == PanelState.Ready && IsRunning && HasUrl; }
        }

        public event Action Changed;

        private void Update(PanelState state, string message, string detail = null)
        {
            State = state;
            Message = message;
            Detail = detail;
            Diagnostics.Write("Panel state: " + state + " - " + message
                + (string.IsNullOrEmpty(detail) ? "" : " | " + detail.Replace("\n", " ")));
            Action changed = Changed;
            if (changed != null)
            {
                changed();
            }
        }

        /// <summary>
        /// Pick a free loopback port, starting from the configured one. The
        /// panel refuses to start on an occupied port, so finding out here
        /// turns a confusing failure into an ordinary "it used 8766" note.
        /// </summary>
        public static int FindAvailablePort(int preferred, int attempts = 12)
        {
            for (int candidate = preferred; candidate < preferred + attempts && candidate <= 65535; candidate++)
            {
                if (IsPortFree(candidate))
                {
                    return candidate;
                }
            }
            return 0;
        }

        public static bool IsPortFree(int port)
        {
            TcpListener listener = null;
            try
            {
                listener = new TcpListener(IPAddress.Loopback, port);
                listener.Start();
                return true;
            }
            catch (SocketException)
            {
                return false;
            }
            finally
            {
                if (listener != null)
                {
                    try { listener.Stop(); } catch (Exception) { }
                }
            }
        }

        /// <summary>
        /// Build the exact program the panel runs. The configured interpreter
        /// is invoked directly -- no shell activation, no PATH lookup, no
        /// `source .../activate` -- and `--no-browser` keeps the browser
        /// decision here, where the URL is known.
        /// </summary>
        public static List<string> BuildPanelProgram(Configuration configuration, int port, string statusFileWslPath)
        {
            return new List<string>
            {
                configuration.PythonWslPath,
                "-m", "omobench.cli", "ui",
                "--no-browser",
                "--port", port.ToString(CultureInfo.InvariantCulture),
                "--status-file", statusFileWslPath,
            };
        }

        public void Start(Configuration configuration)
        {
            lock (_gate)
            {
                if (_process != null && !_process.HasExited)
                {
                    return; // Already ours and alive; repeated clicks reuse it.
                }
            }

            string invalid = configuration.Validate();
            if (invalid != null)
            {
                Update(PanelState.Failed, "Setup is incomplete.", invalid);
                return;
            }

            int port = FindAvailablePort(configuration.Port);
            if (port == 0)
            {
                Update(PanelState.Failed, "No free port was available.",
                    "Ports " + configuration.Port + "-" + (configuration.Port + 11)
                    + " are all in use on 127.0.0.1. Close whatever is using them, or choose another port in Setup.");
                return;
            }

            Configuration.EnsureRuntimeDirectory();
            string statusFileWindowsPath = Path.Combine(
                Configuration.RuntimeDirectoryPath,
                "panel-" + Guid.NewGuid().ToString("N") + ".json");
            string statusFileWslPath;
            if (!CommandLine.TryToWslPath(statusFileWindowsPath, out statusFileWslPath))
            {
                Update(PanelState.Failed, "Could not place the startup status file.",
                    "WSL cannot reach " + statusFileWindowsPath + ".");
                return;
            }

            string arguments = Wsl.BuildRunArguments(
                configuration.Distribution, configuration.CheckoutWslPath,
                BuildPanelProgram(configuration, port, statusFileWslPath));

            Update(PanelState.Starting, "Starting the OmoBench panel in " + configuration.Distribution + "…",
                "Port " + port.ToString(CultureInfo.InvariantCulture));
            Diagnostics.Write("Starting panel: wsl.exe " + arguments);

            Process process = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = Wsl.ExecutablePath,
                    Arguments = arguments,
                    UseShellExecute = false,
                    CreateNoWindow = true,     // no terminal window stays open
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                },
                EnableRaisingEvents = true,
            };

            // The panel prints its connection URL; everything captured here is
            // redacted before it reaches the log.
            process.OutputDataReceived += (sender, e) => { if (e.Data != null) Diagnostics.Write("panel: " + e.Data); };
            process.ErrorDataReceived += (sender, e) => { if (e.Data != null) Diagnostics.Write("panel: " + e.Data); };

            try
            {
                process.Start();
            }
            catch (Exception error)
            {
                Update(PanelState.Failed, "Could not start WSL.", Diagnostics.Redact(error.Message));
                return;
            }

            process.BeginOutputReadLine();
            process.BeginErrorReadLine();

            lock (_gate)
            {
                _process = process;
                _statusFileWindowsPath = statusFileWindowsPath;
                _distribution = configuration.Distribution;
                _panelProcessId = 0;
                _url = null;
                _token = null;
                _origin = null;
                Port = port;
            }

            ThreadPool.QueueUserWorkItem(_ => AwaitReady(process, statusFileWindowsPath));
        }

        /// <summary>
        /// Wait for the panel's status document, or for the process to exit
        /// first. Nothing is parsed out of the console text: the document is
        /// the contract.
        /// </summary>
        private void AwaitReady(Process process, string statusFileWindowsPath, int timeoutSeconds = 120)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
            while (DateTime.UtcNow < deadline)
            {
                if (File.Exists(statusFileWindowsPath))
                {
                    string content = TryRead(statusFileWindowsPath);
                    if (!string.IsNullOrEmpty(content))
                    {
                        string error;
                        PanelStatus status = PanelStatus.Parse(content, out error);
                        if (status == null)
                        {
                            Update(PanelState.Failed, "The panel's startup status could not be read.", error);
                            StopOwnedProcess();
                            return;
                        }
                        if (status.IsFailed)
                        {
                            Update(PanelState.Failed, DescribeFailure(status), status.Message);
                            return;
                        }
                        lock (_gate)
                        {
                            _url = status.Url;
                            _token = status.Token;
                            _origin = status.Origin;
                            Port = status.Port;
                            _panelProcessId = status.PanelProcessId;
                        }
                        Update(PanelState.Ready,
                            "The OmoBench panel is running on 127.0.0.1:" + status.Port.ToString(CultureInfo.InvariantCulture) + ".",
                            "Opened in your default browser. Leave this window open while you use the panel.");
                        return;
                    }
                }

                if (process.HasExited)
                {
                    // Give the document a moment in case it was written just
                    // before exit, then report the exit itself.
                    Thread.Sleep(250);
                    if (File.Exists(statusFileWindowsPath))
                    {
                        continue;
                    }
                    Update(PanelState.Failed,
                        "The panel stopped before it finished starting (exit code "
                        + process.ExitCode.ToString(CultureInfo.InvariantCulture) + ").",
                        "See the diagnostic log for the last output:\n" + Diagnostics.LogPath);
                    return;
                }

                Thread.Sleep(200);
            }

            Update(PanelState.Failed, "The panel did not report itself ready within "
                + timeoutSeconds.ToString(CultureInfo.InvariantCulture) + " seconds.",
                "It may still be importing Python packages on a slow first run. "
                + "Check the diagnostic log, then try again:\n" + Diagnostics.LogPath);
            StopOwnedProcess();
        }

        private static string DescribeFailure(PanelStatus status)
        {
            switch (status.Reason)
            {
                case "already_running":
                    return "Another OmoBench panel is already running for this checkout.";
                case "port_unavailable":
                    return "Port " + status.Port.ToString(CultureInfo.InvariantCulture) + " is already in use.";
                case "not_a_checkout":
                    return "The configured folder is not an OmoBench checkout.";
                case "invalid_port":
                    return "The configured port is not valid.";
                default:
                    return "The panel could not start.";
            }
        }

        private static string TryRead(string path)
        {
            for (int attempt = 0; attempt < 3; attempt++)
            {
                try
                {
                    using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                    using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                    {
                        return reader.ReadToEnd();
                    }
                }
                catch (IOException)
                {
                    Thread.Sleep(100);
                }
                catch (UnauthorizedAccessException)
                {
                    return null;
                }
            }
            return null;
        }

        /// <summary>
        /// Hand the authenticated URL to the default browser. The URL is
        /// passed to ShellExecute as a single argument and is never logged.
        /// </summary>
        public bool OpenInBrowser()
        {
            string url;
            lock (_gate)
            {
                url = _url;
            }
            if (string.IsNullOrEmpty(url) || !PanelStatus.IsLoopbackHttpUrl(url))
            {
                return false;
            }
            try
            {
                Process.Start(new ProcessStartInfo { FileName = url, UseShellExecute = true });
                Diagnostics.Write("Opened the panel in the default browser.");
                return true;
            }
            catch (Exception error)
            {
                Diagnostics.Write("Could not open the browser: " + error.Message);
                return false;
            }
        }

        /// <summary>
        /// Ask the running panel whether a benchmark is in flight, so Stop can
        /// warn before cancelling one. Uses the panel's own authenticated
        /// loopback API with the token from the status document.
        /// </summary>
        public bool TryGetActiveRun(out string description)
        {
            description = null;
            string origin, token;
            lock (_gate)
            {
                origin = _origin;
                token = _token;
            }
            if (string.IsNullOrEmpty(origin) || string.IsNullOrEmpty(token))
            {
                return false;
            }

            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(origin + "/api/runs");
                request.Method = "GET";
                request.Timeout = 5000;
                request.Headers["X-OmoBench-Token"] = token;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                {
                    string body = reader.ReadToEnd();
                    foreach (string active in new[] { "\"running\"", "\"cancelling\"", "\"packaging\"", "\"preparing\"", "\"submitting\"" })
                    {
                        if (body.IndexOf("\"status\": " + active, StringComparison.Ordinal) >= 0
                            || body.IndexOf("\"status\":" + active, StringComparison.Ordinal) >= 0)
                        {
                            description = "A benchmark or submission step is currently active in the panel.";
                            return true;
                        }
                    }
                }
            }
            catch (Exception error)
            {
                Diagnostics.Write("Could not check for an active run: " + error.Message);
            }
            return false;
        }

        /// <summary>
        /// Stop the panel this companion started, and nothing else. Never
        /// `wsl --shutdown`, never a scan for other python processes.
        /// </summary>
        public void Stop()
        {
            StopOwnedProcess();
            Update(PanelState.Stopped, "The panel has been stopped.", null);
        }

        /// <summary>
        /// The command that asks the panel to shut down the way Ctrl+C does.
        /// SIGTERM, never SIGKILL: the panel installs a handler that closes
        /// its controllers, which cancels an active benchmark and stops the
        /// runner it started. Killing the wsl.exe session instead would stop
        /// the panel but orphan those children.
        /// </summary>
        public static List<string> BuildShutdownProgram(int panelProcessId)
        {
            if (panelProcessId <= 1)
            {
                throw new ArgumentOutOfRangeException("panelProcessId");
            }
            return new List<string> { "kill", "-TERM", panelProcessId.ToString(CultureInfo.InvariantCulture) };
        }

        /// <summary>
        /// Ask the panel to stop itself, but only after it proves it is the
        /// one this companion started: a stale or tampered status document
        /// cannot point this at some other process, because an unrelated
        /// process will not answer on that port with this session's token.
        /// </summary>
        private bool TryGracefulShutdown(string distribution, int panelProcessId, string origin, string token)
        {
            if (panelProcessId <= 1 || string.IsNullOrEmpty(distribution)
                || string.IsNullOrEmpty(origin) || string.IsNullOrEmpty(token))
            {
                return false;
            }

            if (!RespondsToOurToken(origin, token))
            {
                Diagnostics.Write("Skipping the in-WSL shutdown signal: the panel did not confirm it is ours.");
                return false;
            }

            try
            {
                string arguments = Wsl.BuildRunArguments(distribution, null, BuildShutdownProgram(panelProcessId));
                Diagnostics.Write("Asking the panel to shut down (SIGTERM, in-distribution pid "
                    + panelProcessId.ToString(CultureInfo.InvariantCulture) + ").");
                ProcessResult result = Wsl.Run(Wsl.ExecutablePath, arguments, 15000);
                return !result.TimedOut && result.ExitCode == 0;
            }
            catch (Exception error)
            {
                Diagnostics.Write("Could not signal the panel: " + error.Message);
                return false;
            }
        }

        /// <summary>Proof of ownership: only our panel knows this session's token.</summary>
        private static bool RespondsToOurToken(string origin, string token)
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(origin + "/api/runs");
                request.Method = "GET";
                request.Timeout = 5000;
                request.Headers["X-OmoBench-Token"] = token;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                {
                    return response.StatusCode == HttpStatusCode.OK;
                }
            }
            catch (Exception)
            {
                return false;
            }
        }

        private void StopOwnedProcess()
        {
            Process process;
            string statusFile;
            string distribution;
            int panelProcessId;
            string origin;
            string token;
            lock (_gate)
            {
                process = _process;
                statusFile = _statusFileWindowsPath;
                distribution = _distribution;
                panelProcessId = _panelProcessId;
                origin = _origin;
                token = _token;
                _process = null;
                _url = null;
                _token = null;
                _origin = null;
                _panelProcessId = 0;
            }

            if (process != null)
            {
                try
                {
                    if (!process.HasExited)
                    {
                        Diagnostics.Write("Stopping the panel this companion started (wsl.exe pid "
                            + process.Id.ToString(CultureInfo.InvariantCulture) + ").");

                        // Graceful first, so the panel cancels an active
                        // benchmark and stops the runner it owns. Killing the
                        // wsl.exe session alone would stop the panel but leave
                        // those children running.
                        if (TryGracefulShutdown(distribution, panelProcessId, origin, token))
                        {
                            process.WaitForExit(20000);
                        }
                    }

                    if (!process.HasExited)
                    {
                        Diagnostics.Write("The panel did not stop on request; ending its wsl.exe session.");
                        process.Kill();
                        process.WaitForExit(15000);
                    }
                }
                catch (Exception error)
                {
                    Diagnostics.Write("Stopping the panel reported: " + error.Message);
                }
                finally
                {
                    try { process.Dispose(); } catch (Exception) { }
                }
            }

            if (!string.IsNullOrEmpty(statusFile))
            {
                try
                {
                    if (File.Exists(statusFile))
                    {
                        File.Delete(statusFile); // never leave a token document behind
                    }
                }
                catch (Exception) { }
            }
        }
    }
}
