using System;
using System.Drawing;
using System.Globalization;
using System.Threading;
using System.Windows.Forms;
using OmoBench.Companion;

namespace OmoBench.Installer
{
    /// <summary>
    /// The guided setup window.
    ///
    /// It moves through four things in order: say what this is, put the
    /// program on the machine, get a working benchmark environment, and
    /// finish. The third is the one that can fail in interesting ways, so it
    /// shows real stages and real errors rather than a spinner, and it never
    /// says the setup is complete until the existing environment check has
    /// actually passed.
    /// </summary>
    public sealed class InstallerForm : Form
    {
        private enum Step { Welcome, Installing, Environment, Finished }

        private readonly BuildInfo _build;
        private readonly byte[] _payload;

        private readonly Label _heading = new Label();
        private readonly Label _subheading = new Label();
        private readonly TextBox _detail = new TextBox();
        private readonly Button _primary = new Button();
        private readonly Button _secondary = new Button();
        private readonly Button _close = new Button();

        private Step _step = Step.Welcome;

        /// <summary>The background check or preparation currently running.</summary>
        private Thread _worker;

        /// <summary>
        /// Whether work is in flight, and whether a result arriving from the
        /// background still describes what this window is doing. It is the
        /// only record of either, so a stale completion cannot clear a flag
        /// the newer operation is relying on.
        /// </summary>
        private readonly WizardState _wizard = new WizardState();
        private Configuration _configuration = new Configuration();
        private SystemReport _system;

        public InstallerForm(BuildInfo build, byte[] payload)
        {
            _build = build;
            _payload = payload;

            Text = "Install OmoBench";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            ClientSize = new Size(660, 460);
            Font = new Font("Segoe UI", 9f);

            _heading.Bounds = new Rectangle(18, 16, 624, 26);
            _heading.Font = new Font("Segoe UI", 13f, FontStyle.Regular);
            Controls.Add(_heading);

            _subheading.Bounds = new Rectangle(18, 46, 624, 38);
            _subheading.ForeColor = SystemColors.GrayText;
            Controls.Add(_subheading);

            _detail.Bounds = new Rectangle(18, 92, 624, 290);
            _detail.Multiline = true;
            _detail.ReadOnly = true;
            _detail.ScrollBars = ScrollBars.Vertical;
            _detail.BackColor = SystemColors.Window;
            _detail.Font = new Font("Consolas", 9f);
            Controls.Add(_detail);

            _primary.Bounds = new Rectangle(18, 398, 190, 32);
            _primary.Click += (s, e) => OnPrimary();
            Controls.Add(_primary);

            _secondary.Bounds = new Rectangle(218, 398, 190, 32);
            _secondary.Click += (s, e) => OnSecondary();
            Controls.Add(_secondary);

            _close.Text = "Close";
            _close.Bounds = new Rectangle(546, 398, 96, 32);
            _close.Click += (s, e) => Close();
            Controls.Add(_close);

            ShowWelcome();
        }

        private void ShowWelcome()
        {
            _step = Step.Welcome;
            _heading.Text = "Install OmoBench for Windows";
            _subheading.Text = "Installs for your user account only. No administrator rights are needed.";
            _detail.Text =
                _build.Describe() + "\r\n\r\n"
                + "This will:\r\n"
                + "  - install the OmoBench companion into\r\n      " + InstallPaths.ProgramDirectory + "\r\n"
                + "  - add a Start menu entry and an entry in Apps & features\r\n"
                + "  - register omobench:// links against the installed program\r\n"
                + "  - then help you get a working benchmark environment\r\n\r\n"
                + "It will not change your benchmark evidence, saved settings, model files, or any "
                + "Linux distribution you already have.\r\n\r\n"
                + "Downloading a model and running a benchmark stay explicit actions inside the "
                + "OmoBench panel afterwards; this installer does neither.";

            Installation.Manifest existing = Installation.ReadManifest();
            if (existing != null)
            {
                _subheading.Text = "OmoBench " + (existing.Version ?? "unknown")
                    + " is already installed. This will update it in place.";
            }

            _primary.Text = existing == null ? "Install" : "Update";
            _primary.Enabled = true;
            _secondary.Visible = false;
            _close.Text = "Cancel";
        }

        private void OnPrimary()
        {
            switch (_step)
            {
                case Step.Welcome: RunInstall(); break;
                case Step.Environment: PrepareEnvironment(); break;
                case Step.Finished: LaunchCompanion(); break;
            }
        }

        private void OnSecondary()
        {
            switch (_step)
            {
                case Step.Environment:
                    if (_wizard.Working) { CancelWork(); } else { ShowEnvironment(true); }
                    break;
            }
        }

        /// <summary>
        /// Ask the work to stop. It is a request, not the end of it: the
        /// worker keeps running until it reaches its next checkpoint, and the
        /// completion it then delivers is what puts these controls back.
        /// </summary>
        private void CancelWork()
        {
            _wizard.Cancel();
            _secondary.Enabled = false;
            _secondary.Text = "Stopping…";
            _primary.Enabled = false;
        }

        // -- installing the program -------------------------------------------

        private void RunInstall()
        {
            _step = Step.Installing;
            _heading.Text = "Installing";
            _subheading.Text = "";
            _primary.Enabled = false;
            _close.Enabled = false;
            Application.DoEvents();

            // Including the case where that question could not be answered:
            // "we could not tell" is a reason to coordinate, not to proceed.
            if (Installation.ScanInstalledCompanions().AnyPossiblyRunning)
            {
                DialogResult choice = MessageBox.Show(this,
                    "The installed OmoBench companion may be running, and its program file cannot be "
                    + "replaced while it is open.\n\n"
                    + "It will be asked to close. If a benchmark is running it will ask you first — "
                    + "answer that question however you like; this update will wait, and will stop if "
                    + "you decide to keep it open.\n\nAsk it to close now?",
                    "Install OmoBench", MessageBoxButtons.OKCancel, MessageBoxIcon.Information);
                if (choice != DialogResult.OK)
                {
                    _close.Enabled = true;
                    ShowWelcome();
                    return;
                }

                _subheading.Text = "Waiting for the OmoBench companion to close…";
                Application.DoEvents();

                // Only a request. If the companion declines -- because the
                // person said No, or because it is protecting a running
                // benchmark -- that is an answer, and the update is postponed
                // rather than forced through.
                if (!Installation.TryCloseInstalledCompanions(60000))
                {
                    _heading.Text = "Update postponed";
                    _subheading.Text = "The OmoBench companion is still running.";
                    _detail.Text =
                        "Nothing has been changed.\r\n\r\n"
                        + "The companion was asked to close and is still open. That usually means "
                        + "either a benchmark is running and it asked you whether to stop it, or you "
                        + "chose to keep it open.\r\n\r\n"
                        + "Its panel and any benchmark it is running have been left alone. Close the "
                        + "companion yourself when you are ready — or let the benchmark finish — and "
                        + "then run this installer again.";
                    _primary.Text = "Try again";
                    _primary.Enabled = true;
                    _close.Enabled = true;
                    _step = Step.Welcome;
                    return;
                }
            }

            InstallOutcome outcome = Installation.Install(
                _payload, Application.ExecutablePath, _build);
            _detail.Text = outcome.Describe();
            _close.Enabled = true;

            if (!outcome.Ok)
            {
                _heading.Text = "The installation did not finish";
                _subheading.Text = outcome.Summary;
                _primary.Text = "Try again";
                _primary.Enabled = true;
                _step = Step.Welcome;
                return;
            }

            ShowEnvironment(false);
        }

        // -- getting a working environment -------------------------------------

        /// <summary>The answer a background environment check produces.</summary>
        private sealed class EnvironmentAnswer
        {
            public SystemReport System;
            public DiscoveryReport Discovery;
            public Configuration Existing;      // a saved setup that still works
            public bool Cancelled;
        }

        /// <summary>
        /// Work out where this machine stands, off the UI thread.
        ///
        /// Reading the registry, listing distributions and probing interpreters
        /// take seconds on a warm machine and a minute on a cold one. Doing
        /// that on the UI thread froze the window for the whole of it, which
        /// also meant the Cancel button could not be pressed during the one
        /// operation it exists for.
        /// </summary>
        private void ShowEnvironment(bool recheck)
        {
            // One operation at a time. Starting a second while the first is
            // still winding up would leave its worker unwaited for at closing
            // time and its completion silently stale.
            if (_wizard.Working) { return; }

            _step = Step.Environment;
            _heading.Text = "Setting up the benchmark environment";
            _primary.Visible = true;
            _primary.Enabled = false;
            _secondary.Visible = true;
            _secondary.Text = "Stop";
            _secondary.Enabled = true;
            _close.Text = "Close";
            _subheading.Text = "Looking at what this machine already has…";
            _detail.Text = "";

            DiscoveryCancel cancel = new DiscoveryCancel();
            int generation = _wizard.Begin(cancel);
            Configuration current = _configuration;

            _worker = new Thread(() =>
            {
                EnvironmentAnswer answer = InspectEnvironment(current, cancel);
                Post(() => ApplyEnvironment(generation, answer));
            });
            _worker.IsBackground = true;
            _worker.Start();
        }

        /// <summary>
        /// Runs on a background thread. Order matters: a configuration that is
        /// already saved and still works is the answer, and searching for
        /// something else would only risk replacing a deliberate choice -- a
        /// hand-picked interpreter, or a port chosen to avoid a clash -- with
        /// whatever happened to be found first.
        /// </summary>
        private static EnvironmentAnswer InspectEnvironment(Configuration current, DiscoveryCancel cancel)
        {
            EnvironmentAnswer answer = new EnvironmentAnswer();

            Configuration saved;
            try { saved = Configuration.Load(); }
            catch (Exception) { saved = new Configuration(); }

            if (saved.IsComplete && saved.Validate() == null)
            {
                EnvironmentCheckResult check = null;
                try { check = EnvironmentCheck.Run(saved); }
                catch (Exception) { }
                if (cancel != null && cancel.IsCancelled) { answer.Cancelled = true; return answer; }
                if (check != null && check.Ok)
                {
                    answer.Existing = saved;
                    return answer;
                }
            }

            answer.System = SystemCheck.Inspect();
            if (cancel != null && cancel.IsCancelled) { answer.Cancelled = true; return answer; }
            if (answer.System.Level != SupportLevel.Ready)
            {
                return answer;
            }

            answer.Discovery = Discovery.Run(
                current, InstallPaths.CompanionPath, new DiscoveryLimits(), cancel, null);
            if (cancel != null && cancel.IsCancelled) { answer.Cancelled = true; }
            return answer;
        }

        private void ApplyEnvironment(int generation, EnvironmentAnswer answer)
        {
            // Nothing above this line. A stale completion belongs to an
            // operation that has been replaced, and the controls, the working
            // flag and the status it would change belong to a newer one.
            Completion completion = _wizard.Complete(generation);
            if (completion == Completion.Stale)
            {
                return;
            }

            // Reached for a cancelled operation too: this is where the window
            // stops being busy. Stop was pressed, the work has now wound up,
            // and the buttons have to come back -- which is exactly what
            // throwing the completion away used to prevent.
            _secondary.Text = "Check again";
            _secondary.Enabled = true;
            _close.Enabled = true;

            if (completion == Completion.Cancelled || answer.Cancelled)
            {
                _heading.Text = "Check stopped";
                _subheading.Text = "Nothing was changed.";
                _detail.Text = "Press \"Check again\" when you are ready.";
                _primary.Visible = false;
                return;
            }

            // A saved setup that still works, kept exactly as it is.
            if (answer.Existing != null)
            {
                _configuration = answer.Existing;
                ShowFinished("OmoBench is ready.",
                    "Your existing settings were checked and still work:\r\n"
                    + "    Distribution: " + answer.Existing.Distribution + "\r\n"
                    + "    Checkout:     " + answer.Existing.CheckoutWindowsPath + "\r\n"
                    + "    Interpreter:  " + answer.Existing.PythonWslPath + "\r\n"
                    + "    Panel port:   " + answer.Existing.Port.ToString(CultureInfo.InvariantCulture)
                    + "\r\n\r\nThey were left unchanged, and nothing was downloaded.");
                return;
            }

            _system = answer.System;

            if (_system.Level == SupportLevel.Unsupported)
            {
                _heading.Text = _system.Headline;
                _subheading.Text = "OmoBench is installed, but cannot run benchmarks here.";
                _detail.Text = _system.Explanation;
                _primary.Visible = false;
                _secondary.Visible = false;
                return;
            }

            if (_system.Level == SupportLevel.NeedsSetup)
            {
                _heading.Text = _system.Headline;
                _subheading.Text = "Follow these steps, then press \"Check again\".";
                _detail.Text = _system.Explanation;
                _primary.Visible = false;
                return;
            }

            DiscoveryReport found = answer.Discovery ?? new DiscoveryReport();

            if (found.Ready.Count > 0)
            {
                InstallationCandidate best = found.Ready[0];
                // Keep a port the person already chose; only fall back to the
                // default when there is no saved configuration to respect.
                int port = _configuration != null && _configuration.Port >= 1
                    && _configuration.Port <= 65535
                    ? _configuration.Port : Configuration.DefaultPort;
                _configuration = new Configuration
                {
                    Distribution = best.Distribution,
                    CheckoutWindowsPath = best.CheckoutWindowsPath,
                    PythonWslPath = best.PythonWslPath,
                    Port = port,
                };
                if (!SaveConfiguration())
                {
                    return;
                }
                ShowFinished("OmoBench is ready.",
                    "Using the installation already on this machine:\r\n"
                    + "    Distribution: " + best.Distribution + "\r\n"
                    + "    Checkout:     " + best.CheckoutWindowsPath + "\r\n"
                    + "    Interpreter:  " + best.PythonWslPath + "\r\n\r\n"
                    + "Nothing was downloaded or changed to use it.");
                return;
            }

            _heading.Text = "No OmoBench environment found yet";
            _subheading.Text = "One can be prepared for you, without touching anything already here.";
            _detail.Text =
                "Nothing on this machine is ready to run benchmarks yet.\r\n\r\n"
                + found.Explain() + "\r\n\r\n"
                + "--------------------------------------------------------------\r\n"
                + "\"Prepare it for me\" will, inside "
                + (_system.Distributions.Count > 0 ? FirstWsl2Name() : "your WSL2 distribution") + ":\r\n"
                + "  1. download the OmoBench source at revision " + _build.ShortRevision + "\r\n"
                + "       into " + ManagedEnvironment.SourceDirectory(_build.Revision) + "\r\n"
                + "  2. create a separate Python environment used only by OmoBench\r\n"
                + "  3. install OmoBench and the pinned benchmark runner into it\r\n"
                + "  4. check the result before calling it finished\r\n\r\n"
                + "It needs an internet connection and a few minutes. It does not download any model, "
                + "run any benchmark, or modify a checkout or Python environment you already have.";
            _primary.Visible = true;
            _primary.Text = "Prepare it for me";
            _primary.Enabled = true;
        }

        private string FirstWsl2Name()
        {
            foreach (WslDistribution distribution in _system.Distributions)
            {
                if (distribution.Version == 2) { return distribution.Name; }
            }
            return "your WSL2 distribution";
        }

        private void PrepareEnvironment()
        {
            if (_wizard.Working) { return; }

            _primary.Enabled = false;
            _secondary.Text = "Stop";
            _secondary.Enabled = true;
            _close.Enabled = false;
            _heading.Text = "Preparing the OmoBench environment";
            _subheading.Text = "This can take several minutes. You can stop at any point.";
            _detail.Text = "";

            string distribution = FirstWsl2Name();
            BuildInfo build = _build;
            DiscoveryCancel cancel = new DiscoveryCancel();
            int generation = _wizard.Begin(cancel);
            _worker = new Thread(() =>
            {
                PreparationResult result = ManagedEnvironment.Prepare(
                    distribution, build, cancel,
                    // Progress is queued, so it outlives the work that
                    // produced it: a stage update from a replaced operation
                    // would otherwise redraw a newer one's list.
                    snapshot => Post(() =>
                    {
                        if (_wizard.IsCurrent(generation)) { RenderStages(snapshot); }
                    }));
                Post(() => FinishPreparation(generation, result));
            });
            _worker.IsBackground = true;
            _worker.Start();
        }

        private void RenderStages(PreparationResult result)
        {
            System.Text.StringBuilder text = new System.Text.StringBuilder();
            foreach (PreparationStage stage in result.Stages)
            {
                text.AppendLine(stage.ToString());
            }
            _detail.Text = text.ToString();
        }

        private void FinishPreparation(int generation, PreparationResult result)
        {
            Completion completion = _wizard.Complete(generation);
            if (completion == Completion.Stale)
            {
                return;
            }

            _close.Enabled = true;
            _secondary.Text = "Check again";
            _secondary.Enabled = true;
            _primary.Enabled = true;
            RenderStages(result);

            if (completion == Completion.Cancelled || result.Cancelled)
            {
                _heading.Text = "Setup stopped";
                _subheading.Text = result.Summary;
                _detail.Text += "\r\nNothing further was changed. You can start again with "
                    + "\"Prepare it for me\".";
                _primary.Enabled = true;
                _primary.Text = "Prepare it for me";
                return;
            }

            if (!result.Ok)
            {
                _heading.Text = "The environment is not ready yet";
                _subheading.Text = result.Summary;
                _detail.Text += "\r\n" + (result.Detail ?? "");
                _primary.Enabled = true;
                _primary.Text = "Try again";
                return;
            }

            _configuration = result.Configuration;
            if (!SaveConfiguration())
            {
                return;
            }
            ShowFinished("OmoBench is ready.", _detail.Text + "\r\n" + (result.Detail ?? ""));
        }

        /// <summary>
        /// Save the companion's configuration -- only ever reached after a
        /// real environment check has passed, so the companion never starts
        /// life pointed at something that was never verified.
        /// </summary>
        private bool SaveConfiguration()
        {
            try
            {
                _configuration.Save();
                return true;
            }
            catch (Exception error)
            {
                // Without saved settings the companion opens to its Setup
                // window, so this is not a working installation and must not
                // be announced as one.
                _heading.Text = "Settings could not be saved";
                _subheading.Text = "OmoBench is installed, but it does not yet know how to start.";
                _detail.Text =
                    "The environment checked out, but its settings could not be written to\r\n    "
                    + Configuration.FilePath + "\r\n\r\n"
                    + error.Message + "\r\n\r\n"
                    + "Open the OmoBench companion and use its Setup window to enter them, or fix "
                    + "whatever is preventing the file from being written and press \"Check again\".";
                _primary.Visible = false;
                _secondary.Visible = true;
                _secondary.Text = "Check again";
                _secondary.Enabled = true;
                _close.Enabled = true;
                return false;
            }
        }

        private void ShowFinished(string headline, string detail)
        {
            _step = Step.Finished;
            _heading.Text = headline;
            _subheading.Text = "Open OmoBench from the Start menu, or from an omobench:// link.";
            _detail.Text = detail + "\r\n\r\n" + _build.SigningStatement();
            _primary.Visible = true;
            _primary.Text = "Open OmoBench";
            _primary.Enabled = true;
            _secondary.Visible = false;
            _close.Text = "Close";
            _close.Enabled = true;
        }

        private void LaunchCompanion()
        {
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = InstallPaths.CompanionPath,
                    UseShellExecute = true,
                });
                Close();
            }
            catch (Exception error)
            {
                MessageBox.Show(this, "Could not start the companion:\n\n" + error.Message,
                    "Install OmoBench", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void Post(Action action)
        {
            try
            {
                if (IsHandleCreated && !IsDisposed) { BeginInvoke(action); }
            }
            catch (Exception) { }
        }

        /// <summary>
        /// Closing while work is in flight waits for that work to stop.
        ///
        /// Returning from here ends the process, and the background thread
        /// would go with it -- mid-download, mid-pip, or between creating a
        /// directory and recording that it owns it. Waiting lets the
        /// cancellation reach its next checkpoint, so what is left behind is a
        /// state the preparation chose rather than wherever it was cut off.
        /// </summary>
        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_wizard.Working)
            {
                DialogResult choice = MessageBox.Show(this,
                    "Setup is still working.\n\nStop it and close?",
                    "Install OmoBench", MessageBoxButtons.YesNo, MessageBoxIcon.Question,
                    MessageBoxDefaultButton.Button2);
                if (choice != DialogResult.Yes)
                {
                    e.Cancel = true;
                    return;
                }

                CancelWork();
                _heading.Text = "Stopping…";
                _subheading.Text = "Waiting for the current step to stop cleanly.";
                Application.DoEvents();

                // The worker this window owns -- the one the operation above
                // started. Waiting is what lets its cancellation reach a
                // checkpoint instead of being cut off wherever it happened
                // to be when the process ended.
                Thread worker = _worker;
                if (worker != null && worker.IsAlive)
                {
                    // Bounded: a step that will not stop must not hold the
                    // window open indefinitely either.
                    if (!worker.Join(TimeSpan.FromSeconds(45)))
                    {
                        DialogResult force = MessageBox.Show(this,
                            "The current step has not stopped yet.\n\n"
                            + "Closing now may leave a partly prepared environment in the OmoBench "
                            + "managed folders. Nothing of yours is affected.\n\nClose anyway?",
                            "Install OmoBench", MessageBoxButtons.YesNo, MessageBoxIcon.Warning,
                            MessageBoxDefaultButton.Button2);
                        if (force != DialogResult.Yes)
                        {
                            e.Cancel = true;
                            return;
                        }
                    }
                }
            }
            base.OnFormClosing(e);
        }
    }
}
