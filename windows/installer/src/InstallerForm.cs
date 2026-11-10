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
        private enum Step { Welcome, Installing, Environment, Upgrade, Finished }

        /// <summary>
        /// Set while an upgrade of a managed installation is being offered or
        /// performed. A first preparation and an upgrade run the same stages;
        /// what differs is that an upgrade has something to go back to.
        /// </summary>
        private bool _upgrading;

        /// <summary>The managed source in use before an upgrade, for rollback
        /// and for bringing previous runs across. Null when not upgrading.</summary>
        private Configuration _replacing;

        /// <summary>Previous runs an import would bring across, once known.</summary>
        private int _runsToImport;

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
                + "OmoBench is open source under Apache License 2.0. The licence and third-party "
                + "notices are included in the installed program folder.\r\n\r\n"
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
                case Step.Upgrade: StartUpgrade(); break;
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
                case Step.Upgrade:
                    KeepCurrentRevision();
                    break;
                case Step.Finished:
                    ImportPreviousRuns();
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

            // A saved setup that still works. What happens next depends on
            // whose it is: a source this installer created may be upgraded to
            // the one it carries, and a checkout the person chose may not.
            if (answer.Existing != null)
            {
                _configuration = answer.Existing;
                ShowExistingInstallation(answer.Existing);
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

        /// <summary>
        /// What to say about an installation that is already here, and what to
        /// offer for it.
        ///
        /// Three cases, and they are genuinely different. A managed source at
        /// an older revision can be upgraded, because this installer owns it
        /// and carries a newer one. A managed source already at this revision
        /// needs nothing. A checkout the person selected is theirs: this
        /// installer did not create it, does not know what is in it, and will
        /// not change it -- which it says rather than leaving somebody to
        /// discover that the panel is still running their old code.
        /// </summary>
        private void ShowExistingInstallation(Configuration existing)
        {
            string where =
                "    Distribution: " + existing.Distribution + "\r\n"
                + "    Checkout:     " + existing.CheckoutWindowsPath + "\r\n"
                + "    Interpreter:  " + existing.PythonWslPath + "\r\n"
                + "    Panel port:   " + existing.Port.ToString(CultureInfo.InvariantCulture);

            if (!ManagedEnvironment.IsManagedSource(existing.CheckoutWindowsPath))
            {
                ShowFinished("OmoBench is ready.",
                    "Your existing settings were checked and still work:\r\n" + where
                    + "\r\n\r\nThat checkout is yours, not one this installer created, so it has "
                    + "not been changed and will not be. The panel runs the code in it, which "
                    + "means installing this build has not moved the panel to revision "
                    + _build.ShortRevision + ".\r\n\r\n"
                    + "To run this revision from that checkout, update it yourself. To use the "
                    + "revision this installer carries instead, point the companion's Setup "
                    + "window at a different checkout; nothing here is removed either way.");
                return;
            }

            string current = ManagedEnvironment.RevisionOf(existing.CheckoutWindowsPath);
            if (!string.IsNullOrEmpty(current) && !string.IsNullOrEmpty(_build.Revision)
                && string.Equals(current, _build.Revision, StringComparison.OrdinalIgnoreCase))
            {
                ShowFinished("OmoBench is ready.",
                    "Your existing settings were checked and still work:\r\n" + where
                    + "\r\n\r\nThis is already revision " + _build.ShortRevision
                    + ", the one this installer carries. Nothing was downloaded or changed.");
                return;
            }

            _step = Step.Upgrade;
            _replacing = existing;
            _heading.Text = "An OmoBench installation is already here";
            _subheading.Text = "It can be upgraded to the revision this installer carries.";
            _detail.Text =
                "This installer created the source the panel is running, so it can replace it:"
                + "\r\n" + where + "\r\n"
                + "    Revision:     " + (string.IsNullOrEmpty(current) ? "not recorded" : current)
                + "\r\n\r\nThis installer carries revision " + _build.ShortRevision + ".\r\n\r\n"
                + "Upgrading unpacks the new source beside the current one and builds it its own "
                + "Python environment, then checks that environment before anything switches over. "
                + "The environment you are using now is left exactly as it is, so a failure, a "
                + "stop, or a change of mind all leave you where you started.\r\n\r\n"
                + "Your models, storage folder and benchmark evidence are not touched. Previous "
                + "runs stay in the current source directory and can be brought across afterwards, "
                + "which this will offer once the upgrade has finished.";
            _primary.Visible = true;
            _primary.Text = "Upgrade to " + _build.ShortRevision;
            _primary.Enabled = true;
            _secondary.Visible = true;
            _secondary.Text = "Keep current";
            _secondary.Enabled = true;
            _close.Enabled = true;
        }

        /// <summary>Leave everything as it is, and say so.</summary>
        private void KeepCurrentRevision()
        {
            _upgrading = false;
            ShowFinished("OmoBench is ready.",
                "Nothing was changed. The panel still runs the revision it was running before, "
                + "and this installer's copy was not unpacked.\r\n\r\n"
                + "Run this installer again whenever you want to upgrade.");
        }

        private void StartUpgrade()
        {
            _upgrading = true;
            PrepareEnvironment();
        }

        private void PrepareEnvironment()
        {
            if (_wizard.Working) { return; }

            _primary.Enabled = false;
            _secondary.Text = "Stop";
            _secondary.Enabled = true;
            _close.Enabled = false;
            _heading.Text = _upgrading
                ? "Building revision " + _build.ShortRevision
                : "Preparing the OmoBench environment";
            _subheading.Text = _upgrading
                ? "Nothing switches over until this has been checked. You can stop at any point."
                : "This can take several minutes. You can stop at any point.";
            _detail.Text = "";

            string distribution = FirstWsl2Name();
            BuildInfo build = _build;
            bool upgrading = _upgrading;
            DiscoveryCancel cancel = new DiscoveryCancel();
            int generation = _wizard.Begin(cancel);
            _worker = new Thread(() =>
            {
                PreparationResult result = upgrading
                    ? ManagedEnvironment.Upgrade(distribution, build, cancel,
                        snapshot => Post(() =>
                        {
                            if (_wizard.IsCurrent(generation)) { RenderStages(snapshot); }
                        }))
                    : ManagedEnvironment.Prepare(
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
                // An upgrade that stops has switched nothing: the running
                // configuration was never written to, so there is nothing to
                // undo and nothing to explain beyond saying so.
                _detail.Text += _upgrading
                    ? "\r\nNothing was switched over. The panel is still running the revision it "
                      + "was running before, and the environment it uses was not touched."
                    : "\r\nNothing further was changed. You can start again with "
                      + "\"Prepare it for me\".";
                _primary.Enabled = true;
                _primary.Text = _upgrading ? "Upgrade to " + _build.ShortRevision : "Prepare it for me";
                if (_upgrading) { _step = Step.Upgrade; _secondary.Text = "Keep current"; }
                return;
            }

            if (!result.Ok)
            {
                _heading.Text = _upgrading
                    ? "The new revision is not ready, so nothing was switched"
                    : "The environment is not ready yet";
                _subheading.Text = result.Summary;
                _detail.Text += "\r\n" + (result.Detail ?? "");
                if (_upgrading)
                {
                    _detail.Text += "\r\n\r\nThe panel is still running the revision it was running "
                        + "before. Its source and its Python environment were not changed, so it "
                        + "works exactly as it did.";
                    _step = Step.Upgrade;
                    _secondary.Text = "Keep current";
                }
                _primary.Enabled = true;
                _primary.Text = _upgrading ? "Try again" : "Try again";
                return;
            }

            // Only now, with a check that actually passed against the pair
            // that was just built. On an upgrade the configuration being
            // replaced is written down first, so going back is reading a file
            // rather than remembering two Linux paths.
            bool rollback = false;
            if (_upgrading) { rollback = Configuration.SavePrevious(); }
            _configuration = result.Configuration;
            if (!SaveConfiguration())
            {
                return;
            }
            if (!_upgrading)
            {
                ShowFinished("OmoBench is ready.", _detail.Text + "\r\n" + (result.Detail ?? ""));
                return;
            }
            ShowUpgraded(result, rollback);
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

        /// <summary>
        /// The upgrade switched over. What is left to say is what became of
        /// everything that was not part of it.
        /// </summary>
        private void ShowUpgraded(PreparationResult result, bool rollback)
        {
            string previous = _replacing == null ? null : _replacing.CheckoutWindowsPath;
            ManagedEnvironment.RunImport survey = previous == null
                ? new ManagedEnvironment.RunImport()
                : ManagedEnvironment.SurveyRuns(previous, _configuration.CheckoutWindowsPath);
            _runsToImport = survey.Ok ? survey.Copied : 0;

            System.Text.StringBuilder detail = new System.Text.StringBuilder();
            detail.AppendLine(_detail.Text);
            detail.AppendLine(result.Detail ?? "");
            detail.AppendLine();
            detail.AppendLine("The panel now runs revision " + _build.ShortRevision + ".");
            detail.AppendLine();
            detail.AppendLine("Left exactly as they were:");
            detail.AppendLine("    The previous source and its Python environment, at");
            detail.AppendLine("    " + (previous ?? "their existing location") + ".");
            detail.AppendLine("    Your downloaded models and the storage folder they are in.");
            detail.AppendLine("    Every benchmark run already recorded.");
            detail.AppendLine();
            if (rollback)
            {
                detail.AppendLine("To go back, run this installer again and choose \"Keep current\", "
                    + "or re-select the previous checkout and interpreter in the companion's "
                    + "Setup window. The previous settings are saved at");
                detail.AppendLine("    " + Configuration.PreviousFilePath);
            }
            else
            {
                detail.AppendLine("No previous settings file was found to record, so going back "
                    + "means re-selecting the previous checkout and interpreter in the "
                    + "companion's Setup window. Neither was removed.");
            }
            if (_runsToImport > 0)
            {
                detail.AppendLine();
                detail.AppendLine(_runsToImport.ToString(CultureInfo.InvariantCulture)
                    + " previous run(s) are still in the old source directory. The panel lists "
                    + "runs beside the source it is running, so they will not appear until they "
                    + "are copied across. Nothing is copied unless you ask.");
            }

            ShowFinished("OmoBench is ready at revision " + _build.ShortRevision, detail.ToString());
            if (_runsToImport > 0)
            {
                _secondary.Visible = true;
                _secondary.Enabled = true;
                _secondary.Text = "Copy " + _runsToImport.ToString(CultureInfo.InvariantCulture)
                    + " previous run(s)";
            }
        }

        /// <summary>
        /// Copy previous run evidence into the new source, on request.
        ///
        /// Never over anything: a run already present at the destination is
        /// skipped and named. The originals stay where they are, so the old
        /// source remains a complete record whatever happens here.
        /// </summary>
        private void ImportPreviousRuns()
        {
            if (_replacing == null || _configuration == null) { return; }
            _secondary.Enabled = false;
            ManagedEnvironment.RunImport outcome = ManagedEnvironment.CopyRuns(
                _replacing.CheckoutWindowsPath, _configuration.CheckoutWindowsPath, false);
            System.Text.StringBuilder text = new System.Text.StringBuilder(_detail.Text);
            text.AppendLine();
            if (!outcome.Ok)
            {
                text.AppendLine("The previous runs could not be copied: " + outcome.Failure);
                text.AppendLine("Nothing was changed, and they are still in the old source "
                    + "directory.");
                _secondary.Enabled = true;
            }
            else
            {
                text.AppendLine("Copied " + outcome.Copied.ToString(CultureInfo.InvariantCulture)
                    + " previous run(s). The originals are still in the old source directory.");
                if (outcome.Skipped.Count > 0)
                {
                    text.AppendLine("Left untouched because a run of the same name was already "
                        + "here (" + outcome.Skipped.Count.ToString(CultureInfo.InvariantCulture)
                        + "): " + string.Join(", ", outcome.Skipped.ToArray()));
                }
                _secondary.Visible = false;
            }
            _detail.Text = text.ToString();
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
