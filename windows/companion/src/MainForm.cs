using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace OmoBench.Companion
{
    /// <summary>
    /// The status window: what the companion is doing, and the two controls
    /// that matter -- Open panel and Stop.
    /// </summary>
    public sealed class MainForm : Form
    {
        private readonly Configuration _configuration;
        private readonly PanelSession _session = new PanelSession();
        private readonly Label _state = new Label();
        private readonly Label _detail = new Label();
        private readonly Panel _indicator = new Panel();
        private readonly Button _open = new Button();
        private readonly Button _stop = new Button();
        private readonly Button _setup = new Button();
        private bool _browserOpenedForThisStart;

        public MainForm(Configuration configuration)
        {
            _configuration = configuration;

            Text = "OmoBench companion";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            ClientSize = new Size(560, 240);
            Font = new Font("Segoe UI", 9f);

            _indicator.Bounds = new Rectangle(18, 22, 12, 12);
            _indicator.BackColor = Color.Gray;
            Controls.Add(_indicator);

            _state.Bounds = new Rectangle(40, 18, 500, 22);
            _state.Font = new Font("Segoe UI", 11f, FontStyle.Regular);
            Controls.Add(_state);

            _detail.Bounds = new Rectangle(40, 46, 500, 96);
            _detail.ForeColor = SystemColors.GrayText;
            Controls.Add(_detail);

            _open.Text = "Open panel";
            _open.Bounds = new Rectangle(18, 160, 130, 32);
            _open.Click += (s, e) => OpenPanel();
            Controls.Add(_open);

            _stop.Text = "Stop";
            _stop.Bounds = new Rectangle(158, 160, 110, 32);
            _stop.Click += (s, e) => StopPanel();
            Controls.Add(_stop);

            _setup.Text = "Setup…";
            _setup.Bounds = new Rectangle(278, 160, 110, 32);
            _setup.Click += (s, e) => OpenSetup();
            Controls.Add(_setup);

            LinkLabel log = new LinkLabel
            {
                Text = "Diagnostic log",
                Bounds = new Rectangle(430, 168, 110, 20),
            };
            log.Click += (s, e) => OpenDiagnosticLog();
            Controls.Add(log);

            Label footer = new Label
            {
                Text = "Launching only opens the panel. Downloads and benchmarks stay explicit actions there.",
                Bounds = new Rectangle(18, 202, 524, 20),
                ForeColor = SystemColors.GrayText,
            };
            Controls.Add(footer);

            _session.Changed += OnSessionChanged;
            Render();
        }

        /// <summary>
        /// Activation: from a protocol link, a second launch, or the window
        /// itself. Starts the panel if this companion does not already own a
        /// running one, and opens the browser once it is ready.
        /// </summary>
        public void Activate(bool openBrowserWhenReady)
        {
            if (!_configuration.IsComplete)
            {
                Show();
                BringToFront();
                MessageBox.Show(this,
                    "This companion has not been set up yet.\n\n"
                    + "Choose your WSL distribution, OmoBench checkout and Python environment, "
                    + "then save. It only has to be done once.",
                    "OmoBench companion", MessageBoxButtons.OK, MessageBoxIcon.Information);
                OpenSetup();
                return;
            }

            if (_session.IsReadyAndAlive)
            {
                // Repeated clicks reuse the panel this companion already owns.
                Diagnostics.Write("Activation reused the running panel.");
                if (openBrowserWhenReady)
                {
                    _session.OpenInBrowser();
                }
                Show();
                BringToFront();
                return;
            }

            _browserOpenedForThisStart = !openBrowserWhenReady;
            Show();
            BringToFront();
            _session.Start(_configuration);
        }

        private void OnSessionChanged()
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action(OnSessionChanged));
                return;
            }
            Render();
            if (_session.State == PanelState.Ready && !_browserOpenedForThisStart)
            {
                _browserOpenedForThisStart = true;
                if (!_session.OpenInBrowser())
                {
                    MessageBox.Show(this,
                        "The panel is running, but your default browser could not be opened.\n\n"
                        + "Use the \"Open panel\" button to try again.",
                        "OmoBench companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            }
        }

        private void Render()
        {
            switch (_session.State)
            {
                case PanelState.Starting:
                    _indicator.BackColor = Color.Goldenrod;
                    break;
                case PanelState.Ready:
                    _indicator.BackColor = Color.SeaGreen;
                    break;
                case PanelState.Failed:
                    _indicator.BackColor = Color.IndianRed;
                    break;
                default:
                    _indicator.BackColor = Color.Gray;
                    break;
            }

            _state.Text = _session.State == PanelState.Stopped && !_configuration.IsComplete
                ? "Not set up yet"
                : _session.Message;
            _detail.Text = _session.Detail ?? string.Empty;

            // Enabled whenever clicking it would do something useful: start the
            // panel when none is running (OpenPanel falls through to Activate),
            // or re-open the browser when one is. Only a start already in
            // flight disables it, so the window is never left with no way to
            // open the panel at all.
            _open.Enabled = _session.State != PanelState.Starting;
            _stop.Enabled = _session.IsRunning;
        }

        private void OpenPanel()
        {
            if (_session.IsReadyAndAlive)
            {
                _session.OpenInBrowser();
                return;
            }
            Activate(true);
        }

        private void StopPanel()
        {
            if (!_session.IsRunning)
            {
                return;
            }

            string activity;
            if (_session.TryGetActiveRun(out activity))
            {
                DialogResult choice = MessageBox.Show(this,
                    activity + "\n\nStopping the panel now cancels it. Partial evidence is kept on disk, "
                    + "but the run will not complete and cannot be submitted.\n\nStop anyway?",
                    "A benchmark is running", MessageBoxButtons.YesNo, MessageBoxIcon.Warning,
                    MessageBoxDefaultButton.Button2);
                if (choice != DialogResult.Yes)
                {
                    return;
                }
            }

            _session.Stop();
        }

        private void OpenSetup()
        {
            using (SetupForm setup = new SetupForm(_configuration))
            {
                setup.ShowDialog(this);
            }
            Render();
        }

        private void OpenDiagnosticLog()
        {
            try
            {
                if (!File.Exists(Diagnostics.LogPath))
                {
                    Diagnostics.Write("Diagnostic log opened from the companion window.");
                }
                Process.Start(new ProcessStartInfo { FileName = Diagnostics.LogPath, UseShellExecute = true });
            }
            catch (Exception error)
            {
                MessageBox.Show(this, "Could not open the log:\n\n" + error.Message,
                    "OmoBench companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_session.IsRunning)
            {
                DialogResult choice = MessageBox.Show(this,
                    "The OmoBench panel this companion started is still running.\n\n"
                    + "Stop it and close?  (No leaves it running; closing this window would then leave it unmanaged.)",
                    "OmoBench companion", MessageBoxButtons.YesNoCancel, MessageBoxIcon.Question);
                if (choice == DialogResult.Cancel)
                {
                    e.Cancel = true;
                    return;
                }
                if (choice == DialogResult.Yes)
                {
                    StopPanel();
                    if (_session.IsRunning)
                    {
                        // The user declined the active-run confirmation.
                        e.Cancel = true;
                        return;
                    }
                }
            }
            base.OnFormClosing(e);
        }
    }
}
