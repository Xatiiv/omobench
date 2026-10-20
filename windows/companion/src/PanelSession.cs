using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace OmoBench.Companion
{
    public enum PanelState
    {
        Stopped,
        Starting,
        Ready,
        Failed,

        /// <summary>
        /// Stop ran but could not confirm the panel is gone. Distinct from
        /// Stopped because saying "stopped" here would be a lie, and distinct
        /// from Failed because the panel may still be working: the companion
        /// keeps owning it so Stop can be tried again.
        /// </summary>
        StopFailed,
    }

    public enum ShutdownOutcome
    {
        /// <summary>There was nothing of ours left to stop.</summary>
        NothingToStop,

        /// <summary>The panel is confirmed gone.</summary>
        Stopped,

        /// <summary>Not confirmed gone. Ownership is retained so Stop can retry.</summary>
        NotConfirmed,
    }

    public enum PanelActivity
    {
        /// <summary>The panel answered, and nothing is in flight.</summary>
        Idle,

        /// <summary>The panel answered, and a run or submission step is active.</summary>
        Active,

        /// <summary>
        /// The panel could not be asked. Explicitly not the same as Idle: an
        /// unanswered question is not evidence that no benchmark is running.
        /// </summary>
        Unknown,
    }

    /// <summary>
    /// Owns exactly one panel process: the one this companion started.
    ///
    /// Two boundaries matter here. First, ownership: stopping only ever
    /// touches the process this object launched and the Linux processes
    /// descended from the panel it proved is its own, so a panel someone
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

        // -- seams, so shutdown can be tested without starting WSL -----------

        /// <summary>Runs one wsl.exe command line. Replaced by tests only.</summary>
        internal static Func<string, int, ProcessResult> WslRunner =
            (arguments, timeoutMilliseconds) => Wsl.Run(Wsl.ExecutablePath, arguments, timeoutMilliseconds);

        /// <summary>Proves the panel on this origin answers to this token. Replaced by tests only.</summary>
        internal static Func<string, string, bool> OwnershipProbe = RespondsToOurToken;

        /// <summary>How long the panel is given to honour SIGTERM before escalation.</summary>
        internal static int GracePeriodMilliseconds = 20000;

        /// <summary>How long the process tree is given to disappear after SIGKILL.</summary>
        internal static int ForcePeriodMilliseconds = 10000;

        internal static int LivenessPollMilliseconds = 500;

        /// <summary>Counts panel launches, so a test can prove none happened.</summary>
        internal static int ProcessLaunchCount;

        /// <summary>
        /// Bounds on the process tree this is willing to signal: a panel and
        /// its runner are a handful of processes, so anything beyond this is a
        /// sign the enumeration is wrong, and it stops rather than widening.
        /// </summary>
        private const int MaximumTreeDepth = 4;
        private const int MaximumTreeSize = 64;

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
                    return _process != null && !HasExited(_process);
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

        private static bool HasExited(Process process)
        {
            try
            {
                return process.HasExited;
            }
            catch (Exception)
            {
                // A process object that can no longer be queried is not
                // something this can claim is alive.
                return true;
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
                if (_process != null && !HasExited(_process))
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

            // The panel is about to be told to write its connection token into
            // this directory. If it cannot be made private, nothing starts:
            // publishing the token to a folder other accounts can read is a
            // worse outcome than not launching, and it is reported here rather
            // than logged, because the log lives in that same directory.
            string privacyFailure;
            if (!Configuration.TryEnsureRuntimeDirectory(out privacyFailure))
            {
                Update(PanelState.Failed,
                    "The panel was not started, because its connection token could not be kept private.",
                    privacyFailure
                    + "\n\nThe panel writes a short-lived file containing its connection token into that "
                    + "folder. Until only your account can read it, starting the panel would expose the "
                    + "token to anyone else who can read the folder.");
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
                ProcessLaunchCount++;
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
                            // The document was refused, so its process id is not
                            // trustworthy either and nothing in the distribution
                            // can be signalled by it. Ending the wsl.exe child is
                            // all that is left, and it is not proof the panel
                            // itself went: say so rather than imply a clean stop.
                            Update(PanelState.Failed, "The panel's startup status could not be read.",
                                error
                                + "\n\nThe panel may still be running inside the distribution, still "
                                + "holding this checkout's panel lock. The diagnostic log has its "
                                + "output:\n" + Diagnostics.LogPath);
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

                if (HasExited(process))
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

        // -- is anything in flight? -------------------------------------------

        /// <summary>
        /// The job states the panel treats as in flight (ACTIVE in ui.py).
        /// </summary>
        private static readonly string[] ActiveStatuses =
            { "running", "cancelling", "packaging", "preparing", "submitting" };

        /// <summary>
        /// Ask the running panel whether anything is in flight, so Stop can
        /// warn before cancelling it. Uses the panel's own authenticated
        /// loopback API with the token from the status document.
        ///
        /// Returns <see cref="PanelActivity.Unknown"/> when the panel cannot be
        /// asked. That distinction is the point: an unreachable panel is the
        /// case where a benchmark is *most* likely still running, so it must
        /// not be reported as "nothing is running".
        /// </summary>
        public PanelActivity CheckActivity(out string description)
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
                description = "The companion cannot tell whether a benchmark is running: "
                    + "it has no connection details for this panel.";
                return PanelActivity.Unknown;
            }

            // Both listings: an ordinary run and a guided (managed) run live
            // on different endpoints, and either can be in flight.
            bool anyActive = false;
            foreach (string endpoint in new[] { "/api/runs", "/api/managed/runs" })
            {
                string body;
                try
                {
                    body = GetAuthenticated(origin, token, endpoint);
                }
                catch (Exception error)
                {
                    Diagnostics.Write("Could not check " + endpoint + " for an active run: " + error.Message);
                    description = "The companion could not check whether a benchmark is running "
                        + "(the panel did not answer on " + endpoint + ").";
                    return PanelActivity.Unknown;
                }

                bool active;
                if (!TryFindActiveStatus(body, out active))
                {
                    description = "The companion could not read the panel's answer about running work.";
                    return PanelActivity.Unknown;
                }
                anyActive = anyActive || active;
            }

            if (anyActive)
            {
                description = "A benchmark or submission step is currently active in the panel.";
                return PanelActivity.Active;
            }
            return PanelActivity.Idle;
        }

        /// <summary>
        /// Look for an active job in a listing, by parsing it rather than by
        /// matching substrings. The listings differ in shape (one is an array
        /// of jobs, one an object), and both may grow fields, so this walks the
        /// parsed document for any object carrying an active "status".
        /// </summary>
        internal static bool TryFindActiveStatus(string json, out bool active)
        {
            active = false;
            if (string.IsNullOrWhiteSpace(json))
            {
                return false;
            }
            object parsed;
            try
            {
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                serializer.MaxJsonLength = int.MaxValue;
                parsed = serializer.DeserializeObject(json);
            }
            catch (Exception)
            {
                return false;
            }
            active = ContainsActiveStatus(parsed, 0);
            return true;
        }

        private static bool ContainsActiveStatus(object node, int depth)
        {
            if (node == null || depth > 12)
            {
                return false;
            }

            Dictionary<string, object> map = node as Dictionary<string, object>;
            if (map != null)
            {
                object status;
                if (map.TryGetValue("status", out status))
                {
                    string text = status as string;
                    if (text != null)
                    {
                        foreach (string candidate in ActiveStatuses)
                        {
                            if (string.Equals(text, candidate, StringComparison.Ordinal))
                            {
                                return true;
                            }
                        }
                    }
                }
                foreach (KeyValuePair<string, object> entry in map)
                {
                    if (ContainsActiveStatus(entry.Value, depth + 1))
                    {
                        return true;
                    }
                }
                return false;
            }

            object[] items = node as object[];
            if (items != null)
            {
                foreach (object item in items)
                {
                    if (ContainsActiveStatus(item, depth + 1))
                    {
                        return true;
                    }
                }
            }
            return false;
        }

        /// <summary>
        /// A token-authenticated request to the panel's own loopback API.
        ///
        /// Redirects are not followed: the token travels in a header, and a
        /// redirect is the one way a request aimed at loopback could be made
        /// to repeat itself somewhere else. A redirect is treated as an error.
        /// </summary>
        private static string GetAuthenticated(string origin, string token, string path)
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(origin + path);
            request.Method = "GET";
            request.Timeout = 5000;
            request.ReadWriteTimeout = 5000;
            request.AllowAutoRedirect = false;
            request.Proxy = null;   // never route a loopback request through a proxy
            request.Headers["X-OmoBench-Token"] = token;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            {
                if (response.StatusCode != HttpStatusCode.OK)
                {
                    throw new WebException("The panel answered " + (int)response.StatusCode + ".");
                }
                using (Stream stream = response.GetResponseStream())
                using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                {
                    return reader.ReadToEnd();
                }
            }
        }

        // -- stopping ----------------------------------------------------------

        /// <summary>
        /// Stop the panel this companion started, and nothing else. Never
        /// `wsl --shutdown`, never a scan for other python processes.
        ///
        /// Reports what actually happened: if the panel cannot be confirmed
        /// gone, this says so and keeps owning it, so Stop can be pressed
        /// again rather than the window claiming a stop that did not occur.
        /// </summary>
        public ShutdownOutcome Stop()
        {
            ShutdownOutcome outcome = StopOwnedProcess();
            switch (outcome)
            {
                case ShutdownOutcome.Stopped:
                case ShutdownOutcome.NothingToStop:
                    Update(PanelState.Stopped, "The panel has been stopped.", null);
                    break;
                default:
                    Update(PanelState.StopFailed,
                        "The panel could not be confirmed stopped.",
                        (_lastShutdownReport ?? "The panel did not confirm it stopped.")
                        + "\n\nIt may still be running, and this companion still owns it. "
                        + "Press Stop again to retry. The diagnostic log has the details:\n"
                        + Diagnostics.LogPath);
                    break;
            }
            return outcome;
        }

        private string _lastShutdownReport;

        /// <summary>
        /// The command that asks the panel to shut down the way Ctrl+C does.
        /// SIGTERM, never SIGKILL: the panel installs a handler that closes
        /// its controllers, which cancels an active benchmark and stops the
        /// runner it started. Killing the wsl.exe session instead would stop
        /// the panel but orphan those children.
        /// </summary>
        public static List<string> BuildShutdownProgram(int panelProcessId)
        {
            return new List<string> { "kill", "-TERM", RequireSignallablePid(panelProcessId) };
        }

        /// <summary>
        /// Does this process still exist? `kill -0` signals nothing; it only
        /// reports whether the process is there and signallable by us.
        /// </summary>
        public static List<string> BuildAliveProbeProgram(int processId)
        {
            return new List<string> { "kill", "-0", RequireSignallablePid(processId) };
        }

        /// <summary>
        /// List the direct children of one process. Used to walk down from the
        /// panel this companion proved is its own, so escalation reaches the
        /// runner the panel started and nothing else. No shell: `ps` is run
        /// directly and its output parsed here.
        /// </summary>
        public static List<string> BuildChildListProgram(int parentProcessId)
        {
            return new List<string>
            {
                "ps", "-o", "pid=", "--no-headers", "--ppid", RequireSignallablePid(parentProcessId),
            };
        }

        /// <summary>
        /// Ask about one specific process. Used to establish that `ps` works at
        /// all before its silence is believed: "this parent has no children"
        /// and "ps is not installed, or does not take these options" are the
        /// same non-zero exit, and treating the second as the first would mean
        /// force-stopping a panel while leaving the runner beneath it alive --
        /// exactly the orphaning this code exists to prevent.
        /// </summary>
        public static List<string> BuildProcessExistsProgram(int processId)
        {
            return new List<string>
            {
                "ps", "-o", "pid=", "--no-headers", "-p", RequireSignallablePid(processId),
            };
        }

        /// <summary>
        /// Force-stop an explicit list of process ids. Every id must have been
        /// reached by walking down from the proven-ours panel process; this
        /// never takes a pattern, a name, or a process group.
        /// </summary>
        public static List<string> BuildForceKillProgram(IEnumerable<int> processIds)
        {
            List<string> program = new List<string> { "kill", "-KILL" };
            int count = 0;
            foreach (int processId in processIds)
            {
                program.Add(RequireSignallablePid(processId));
                count++;
            }
            if (count == 0)
            {
                throw new ArgumentException("No process ids to stop.", "processIds");
            }
            return program;
        }

        private static string RequireSignallablePid(int processId)
        {
            // 0 would mean "every process in my group" and 1 is init; a
            // negative value would mean a process group. A stale or tampered
            // status document must not be able to widen a signal that way.
            if (processId <= 1)
            {
                throw new ArgumentOutOfRangeException("processId");
            }
            return processId.ToString(CultureInfo.InvariantCulture);
        }

        private enum Liveness
        {
            Alive,
            Gone,

            /// <summary>The question could not be answered. Never treated as Gone.</summary>
            Unknown,
        }

        /// <summary>
        /// Bring down the Linux side of the panel, and confirm it.
        ///
        /// Ordering is the whole point. The panel is asked to stop first, so it
        /// cancels its own benchmark and reaps the runner it started. Only once
        /// the panel process is confirmed gone is the wsl.exe child this
        /// companion launched allowed to be ended -- killing that first was
        /// what previously left the runner orphaned inside the distribution.
        ///
        /// If the panel will not go, escalation stays inside the process tree
        /// rooted at the pid the panel proved is its own, and is bounded in
        /// both depth and count. If even that cannot be confirmed, this
        /// reports failure rather than widening its aim.
        /// </summary>
        internal ShutdownOutcome ShutdownLinuxSide(
            string distribution, int panelProcessId, string origin, string token, out string report)
        {
            report = null;

            if (string.IsNullOrEmpty(distribution) || panelProcessId <= 1)
            {
                report = "The companion has no in-distribution process id for this panel, "
                    + "so it cannot stop it safely.";
                return ShutdownOutcome.NotConfirmed;
            }

            // Ownership proof. A stale or tampered status document cannot aim
            // this at some other process, because an unrelated process will not
            // answer on that port with this session's token.
            bool ours = !string.IsNullOrEmpty(origin) && !string.IsNullOrEmpty(token)
                && OwnershipProbe(origin, token);
            if (!ours)
            {
                // The benign explanation is that it already exited, and that is
                // checkable. Anything else is left strictly alone.
                Liveness liveness = CheckLiveness(distribution, panelProcessId);
                if (liveness == Liveness.Gone)
                {
                    return ShutdownOutcome.Stopped;
                }
                report = "The panel did not confirm it is the one this companion started, "
                    + "so no signal was sent to it.";
                Diagnostics.Write("Ownership unproven for in-distribution pid "
                    + panelProcessId.ToString(CultureInfo.InvariantCulture) + "; leaving it alone.");
                return ShutdownOutcome.NotConfirmed;
            }

            Diagnostics.Write("Asking the panel to shut down (SIGTERM, in-distribution pid "
                + panelProcessId.ToString(CultureInfo.InvariantCulture) + ").");
            RunInDistribution(distribution, BuildShutdownProgram(panelProcessId), 15000);

            if (WaitForDeparture(distribution, panelProcessId, GracePeriodMilliseconds))
            {
                return ShutdownOutcome.Stopped;
            }

            // It did not honour SIGTERM. Escalate, but only within the tree
            // below the process we proved is ours.
            List<int> tree;
            if (!TryCollectOwnedTree(distribution, panelProcessId, out tree))
            {
                report = "The panel ignored the request to stop, and the companion could not list "
                    + "its processes to end them safely, so it stopped rather than guessing.";
                return ShutdownOutcome.NotConfirmed;
            }

            Diagnostics.Write("The panel ignored SIGTERM; force-stopping its own process tree ("
                + tree.Count.ToString(CultureInfo.InvariantCulture) + " process(es)).");
            RunInDistribution(distribution, BuildForceKillProgram(tree), 15000);

            if (WaitForDeparture(distribution, panelProcessId, ForcePeriodMilliseconds))
            {
                return ShutdownOutcome.Stopped;
            }

            report = "The panel process is still present inside " + distribution
                + " after being asked and then forced to stop.";
            return ShutdownOutcome.NotConfirmed;
        }

        private bool WaitForDeparture(string distribution, int processId, int timeoutMilliseconds)
        {
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
            while (true)
            {
                if (CheckLiveness(distribution, processId) == Liveness.Gone)
                {
                    return true;
                }
                if (DateTime.UtcNow >= deadline)
                {
                    return false;
                }
                Thread.Sleep(LivenessPollMilliseconds);
            }
        }

        private Liveness CheckLiveness(string distribution, int processId)
        {
            ProcessResult result = RunInDistribution(distribution, BuildAliveProbeProgram(processId), 10000);
            if (result == null || result.TimedOut)
            {
                return Liveness.Unknown;
            }
            // `kill -0` exits 0 when the process exists and can be signalled,
            // and non-zero when it does not exist.
            return result.ExitCode == 0 ? Liveness.Alive : Liveness.Gone;
        }

        /// <summary>
        /// Collect the panel process and its descendants, breadth-first and
        /// bounded. Returns false if the tree cannot be established, which the
        /// caller turns into an honest "could not confirm" rather than a
        /// broader kill.
        /// </summary>
        private bool TryCollectOwnedTree(string distribution, int rootProcessId, out List<int> tree)
        {
            tree = new List<int> { rootProcessId };

            // Establish that the process listing works before trusting it to
            // say "no children". The root is known to still be there -- it just
            // ignored SIGTERM -- so ps must be able to report it.
            ProcessResult capability = RunInDistribution(
                distribution, BuildProcessExistsProgram(rootProcessId), 10000);
            if (capability == null || capability.TimedOut || capability.ExitCode != 0
                || ParseProcessIds(capability.StandardOutput).Count == 0)
            {
                return false;
            }

            List<int> frontier = new List<int> { rootProcessId };

            for (int depth = 0; depth < MaximumTreeDepth && frontier.Count > 0; depth++)
            {
                List<int> next = new List<int>();
                foreach (int parent in frontier)
                {
                    ProcessResult result = RunInDistribution(distribution, BuildChildListProgram(parent), 10000);
                    if (result == null || result.TimedOut)
                    {
                        return false;
                    }
                    if (result.ExitCode != 0)
                    {
                        // `ps` exits non-zero when a parent simply has no
                        // children, which is the common case and not an error.
                        continue;
                    }
                    foreach (int child in ParseProcessIds(result.StandardOutput))
                    {
                        if (child <= 1 || tree.Contains(child))
                        {
                            continue;   // never init, and never loop on a cycle
                        }
                        if (tree.Count >= MaximumTreeSize)
                        {
                            return false;   // implausible for a panel; refuse to widen
                        }
                        tree.Add(child);
                        next.Add(child);
                    }
                }
                frontier = next;
            }
            return true;
        }

        internal static List<int> ParseProcessIds(string output)
        {
            List<int> identifiers = new List<int>();
            if (string.IsNullOrEmpty(output))
            {
                return identifiers;
            }
            foreach (string rawLine in output.Split('\n'))
            {
                string line = rawLine.Trim();
                if (line.Length == 0)
                {
                    continue;
                }
                int value;
                if (int.TryParse(line, NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
                    && value > 1)
                {
                    identifiers.Add(value);
                }
            }
            return identifiers;
        }

        private ProcessResult RunInDistribution(string distribution, List<string> program, int timeoutMilliseconds)
        {
            try
            {
                string arguments = Wsl.BuildRunArguments(distribution, null, program);
                return WslRunner(arguments, timeoutMilliseconds);
            }
            catch (Exception error)
            {
                Diagnostics.Write("A command inside " + distribution + " could not be run: " + error.Message);
                return null;
            }
        }

        /// <summary>
        /// Stop what this session owns, in the safe order, and only release
        /// ownership once the panel is confirmed gone. Holding on to the
        /// distribution, pid, token and status-file path after a failed stop is
        /// what makes a retry possible.
        /// </summary>
        public ShutdownOutcome StopOwnedProcess()
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
            }

            if (process == null)
            {
                ReleaseOwnership(statusFile);
                return ShutdownOutcome.NothingToStop;
            }

            if (!HasExited(process))
            {
                Diagnostics.Write("Stopping the panel this companion started (wsl.exe pid "
                    + TryDescribe(process) + ").");

                if (panelProcessId > 1)
                {
                    string report;
                    ShutdownOutcome linux = ShutdownLinuxSide(
                        distribution, panelProcessId, origin, token, out report);
                    if (linux != ShutdownOutcome.Stopped)
                    {
                        _lastShutdownReport = report;
                        Diagnostics.Write("Shutdown was not confirmed: " + report);
                        // Ownership deliberately retained, status document
                        // deliberately left in place: the panel it describes is
                        // still there, and deleting it would strand the pid and
                        // token a retry needs.
                        return ShutdownOutcome.NotConfirmed;
                    }
                }
                else
                {
                    // The panel never reported itself ready, so it never served
                    // a request and no benchmark can have been started through
                    // it. There is no in-distribution pid to signal, and ending
                    // the wsl.exe child cannot orphan a runner that was never
                    // started.
                    Diagnostics.Write("The panel never reported ready; ending the wsl.exe child directly.");
                }
            }

            // The Linux side is confirmed gone, or never came up. Now the child
            // process this companion started may be ended.
            try
            {
                if (!HasExited(process))
                {
                    process.WaitForExit(2000);
                }
                if (!HasExited(process))
                {
                    process.Kill();
                    process.WaitForExit(15000);
                }
            }
            catch (Exception error)
            {
                Diagnostics.Write("Ending the wsl.exe child reported: " + Diagnostics.Redact(error.Message));
            }

            if (!HasExited(process))
            {
                _lastShutdownReport = "The wsl.exe process this companion started is still running.";
                Diagnostics.Write(_lastShutdownReport);
                return ShutdownOutcome.NotConfirmed;
            }

            try { process.Dispose(); } catch (Exception) { }
            _lastShutdownReport = null;
            ReleaseOwnership(statusFile);
            return ShutdownOutcome.Stopped;
        }

        private static string TryDescribe(Process process)
        {
            try
            {
                return process.Id.ToString(CultureInfo.InvariantCulture);
            }
            catch (Exception)
            {
                return "unknown";
            }
        }

        /// <summary>
        /// Drop everything that identified the stopped panel, and remove the
        /// status document. Only called once the panel is confirmed gone, so a
        /// token document is never deleted while the panel it describes is
        /// still running.
        /// </summary>
        private void ReleaseOwnership(string statusFile)
        {
            lock (_gate)
            {
                _process = null;
                _statusFileWindowsPath = null;
                _url = null;
                _token = null;
                _origin = null;
                _panelProcessId = 0;
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

        /// <summary>Proof of ownership: only our panel knows this session's token.</summary>
        private static bool RespondsToOurToken(string origin, string token)
        {
            try
            {
                GetAuthenticated(origin, token, "/api/runs");
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }
}
