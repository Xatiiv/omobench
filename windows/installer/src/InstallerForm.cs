using System;
using System.Drawing;
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
        private DiscoveryCancel _cancel;
        private bool _working;
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
                    if (_working) { CancelWork(); } else { ShowEnvironment(true); }
                    break;
            }
        }

        private void CancelWork()
        {
            DiscoveryCancel cancel = _cancel;
            if (cancel != null) { cancel.Cancel(); }
            _secondary.Enabled = false;
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

            System.Collections.Generic.List<System.Diagnostics.Process> running =
                Installation.RunningCompanions();
            if (running.Count > 0)
            {
                DialogResult choice = MessageBox.Show(this,
                    "The OmoBench companion is running, and its program file cannot be replaced while "
                    + "it is open.\n\nClose it and continue?",
                    "Install OmoBench", MessageBoxButtons.OKCancel, MessageBoxIcon.Information);
                if (choice != DialogResult.OK)
                {
                    _close.Enabled = true;
                    ShowWelcome();
                    return;
                }
                foreach (System.Diagnostics.Process process in running)
                {
                    try { process.CloseMainWindow(); process.WaitForExit(5000); } catch (Exception) { }
                    try { if (!process.HasExited) { process.Kill(); process.WaitForExit(5000); } }
                    catch (Exception) { }
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

        private void ShowEnvironment(bool recheck)
        {
            _step = Step.Environment;
            _heading.Text = "Setting up the benchmark environment";
            _primary.Enabled = false;
            _secondary.Visible = true;
            _secondary.Text = "Check again";
            _secondary.Enabled = true;
            _close.Text = "Close";
            _subheading.Text = "Looking at what this machine already has…";
            _detail.Text = "";
            Application.DoEvents();

            _system = SystemCheck.Inspect();

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

            // WSL2 is present. Reuse an existing installation if there is one.
            _subheading.Text = "Looking for an OmoBench installation you already have…";
            Application.DoEvents();

            _cancel = new DiscoveryCancel();
            DiscoveryReport found = Discovery.Run(
                _configuration, InstallPaths.CompanionPath, new DiscoveryLimits(), _cancel, null);

            if (found.Ready.Count > 0)
            {
                InstallationCandidate best = found.Ready[0];
                _configuration = new Configuration
                {
                    Distribution = best.Distribution,
                    CheckoutWindowsPath = best.CheckoutWindowsPath,
                    PythonWslPath = best.PythonWslPath,
                    Port = Configuration.DefaultPort,
                };
                SaveConfiguration();
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
            _working = true;
            _cancel = new DiscoveryCancel();
            _primary.Enabled = false;
            _secondary.Text = "Stop";
            _secondary.Enabled = true;
            _close.Enabled = false;
            _heading.Text = "Preparing the OmoBench environment";
            _subheading.Text = "This can take several minutes. You can stop at any point.";
            _detail.Text = "";

            string distribution = FirstWsl2Name();
            BuildInfo build = _build;
            DiscoveryCancel cancel = _cancel;

            Thread worker = new Thread(() =>
            {
                PreparationResult result = ManagedEnvironment.Prepare(
                    distribution, build, cancel, snapshot => Post(() => RenderStages(snapshot)));
                Post(() => FinishPreparation(result));
            });
            worker.IsBackground = true;
            worker.Start();
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

        private void FinishPreparation(PreparationResult result)
        {
            _working = false;
            _close.Enabled = true;
            _secondary.Text = "Check again";
            _secondary.Enabled = true;
            RenderStages(result);

            if (result.Cancelled)
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
            SaveConfiguration();
            ShowFinished("OmoBench is ready.", _detail.Text + "\r\n" + (result.Detail ?? ""));
        }

        /// <summary>
        /// Save the companion's configuration -- only ever reached after a
        /// real environment check has passed, so the companion never starts
        /// life pointed at something that was never verified.
        /// </summary>
        private void SaveConfiguration()
        {
            try
            {
                _configuration.Save();
            }
            catch (Exception error)
            {
                MessageBox.Show(this,
                    "OmoBench is set up, but its settings could not be saved:\n\n" + error.Message
                    + "\n\nYou can set them from the companion's Setup window.",
                    "Install OmoBench", MessageBoxButtons.OK, MessageBoxIcon.Warning);
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

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_working)
            {
                DialogResult choice = MessageBox.Show(this,
                    "Setup is still preparing the environment.\n\nStop it and close?",
                    "Install OmoBench", MessageBoxButtons.YesNo, MessageBoxIcon.Question,
                    MessageBoxDefaultButton.Button2);
                if (choice != DialogResult.Yes)
                {
                    e.Cancel = true;
                    return;
                }
                CancelWork();
            }
            base.OnFormClosing(e);
        }
    }
}
