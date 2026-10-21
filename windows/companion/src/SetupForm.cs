using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Threading;
using System.Windows.Forms;

namespace OmoBench.Companion
{
    /// <summary>
    /// The one-time graphical setup.
    ///
    /// The ordinary path through this window involves typing nothing: when it
    /// opens without a usable configuration it starts looking for the checkout
    /// and the WSL Python environment itself, and fills them in once it has
    /// validated a pair. Paths remain editable under Advanced settings for the
    /// machines where the search comes up short.
    ///
    /// Discovery only proposes. Saving the configuration and registering the
    /// omobench:// handler stay exactly what they were: one explicit action by
    /// the person, through the button below.
    /// </summary>
    public sealed class SetupForm : Form
    {
        private readonly Configuration _configuration;
        private readonly ComboBox _distributions = new ComboBox();
        private readonly TextBox _checkout = new TextBox();
        private readonly TextBox _python = new TextBox();
        private readonly NumericUpDown _port = new NumericUpDown();
        private readonly TextBox _result = new TextBox();
        private readonly Button _verify = new Button();
        private readonly Button _save = new Button();
        private readonly Button _unregister = new Button();
        private readonly Label _registration = new Label();
        private List<WslDistribution> _found = new List<WslDistribution>();

        // -- discovery ---------------------------------------------------------

        private readonly Label _discoveryStatus = new Label();
        private readonly Button _findAutomatically = new Button();
        private readonly Button _cancelDiscovery = new Button();
        private readonly ListBox _candidates = new ListBox();
        private readonly Label _candidatesLabel = new Label();
        private readonly CheckBox _advancedToggle = new CheckBox();
        private readonly Panel _advanced = new Panel();

        private DiscoveryCancel _cancel;

        /// <summary>Generations, edit tracking and readiness; see SetupState.</summary>
        private readonly SetupState _state = new SetupState();

        /// <summary>
        /// Set while this code is assigning to a text box, so the resulting
        /// TextChanged is not mistaken for the person typing.
        /// </summary>
        private bool _assigning;

        /// <summary>
        /// Set while this code is selecting a list entry, so filling in a
        /// default at the end of a search is not mistaken for the person
        /// choosing that entry.
        /// </summary>
        private bool _selectingDefault;

        private List<InstallationCandidate> _ready = new List<InstallationCandidate>();

        public SetupForm(Configuration configuration)
        {
            _configuration = configuration;

            Text = "OmoBench companion — setup";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(640, 560);
            Font = new Font("Segoe UI", 9f);

            Add(new Label
            {
                Text = "The companion needs your OmoBench checkout and the WSL Python environment that runs it.",
                Bounds = new Rectangle(14, 14, 610, 20),
                ForeColor = SystemColors.GrayText,
            });

            Add(new Label { Text = "WSL distribution", Bounds = new Rectangle(14, 46, 120, 20) });
            _distributions.Bounds = new Rectangle(140, 42, 380, 24);
            _distributions.DropDownStyle = ComboBoxStyle.DropDownList;
            _distributions.SelectedIndexChanged += DistributionChanged;
            Add(_distributions);
            Button refresh = new Button { Text = "Refresh", Bounds = new Rectangle(528, 41, 96, 26) };
            refresh.Click += (s, e) => LoadDistributions();
            Add(refresh);

            _discoveryStatus.Bounds = new Rectangle(14, 80, 400, 20);
            Add(_discoveryStatus);

            _findAutomatically.Text = "Find automatically";
            _findAutomatically.Bounds = new Rectangle(420, 76, 130, 26);
            _findAutomatically.Click += (s, e) => BeginDiscovery();
            Add(_findAutomatically);

            _cancelDiscovery.Text = "Cancel";
            _cancelDiscovery.Bounds = new Rectangle(556, 76, 68, 26);
            _cancelDiscovery.Enabled = false;
            _cancelDiscovery.Click += (s, e) => CancelDiscovery("Search cancelled.");
            Add(_cancelDiscovery);

            _candidatesLabel.Text = "Several installations were found. Choose the one to use:";
            _candidatesLabel.Bounds = new Rectangle(14, 108, 610, 18);
            _candidatesLabel.Visible = false;
            Add(_candidatesLabel);

            _candidates.Bounds = new Rectangle(14, 128, 610, 60);
            _candidates.Visible = false;
            _candidates.SelectedIndexChanged += (s, e) => ApplySelectedCandidate();
            Add(_candidates);

            _advancedToggle.Text = "Advanced settings (enter paths by hand)";
            _advancedToggle.Bounds = new Rectangle(14, 196, 320, 20);
            _advancedToggle.CheckedChanged += (s, e) => Relayout();
            Add(_advancedToggle);

            BuildAdvancedPanel();
            Add(_advanced);

            _verify.Text = "Check this environment";
            _verify.Click += (s, e) => Verify();
            Add(_verify);

            _result.Multiline = true;
            _result.ReadOnly = true;
            _result.ScrollBars = ScrollBars.Vertical;
            _result.BackColor = SystemColors.Window;
            Add(_result);

            _registration.ForeColor = SystemColors.GrayText;
            Add(_registration);

            _save.Text = "Save and register omobench://";
            _save.Click += (s, e) => SaveAndRegister();
            Add(_save);

            _unregister.Text = "Remove registration";
            _unregister.Click += (s, e) => Unregister();
            Add(_unregister);

            Button close = new Button { Text = "Close" };
            close.Click += (s, e) => Close();
            Add(close);
            _closeButton = close;

            LoadDistributions();
            AssignText(_checkout, _configuration.CheckoutWindowsPath);
            AssignText(_python, _configuration.PythonWslPath);
            _port.Value = _configuration.Port >= 1 && _configuration.Port <= 65535
                ? _configuration.Port : Configuration.DefaultPort;
            UpdateRegistrationLabel();
            Relayout();

            if (_configuration.IsComplete)
            {
                // A usable saved setup is the preferred answer; it is not
                // replaced by searching for something else.
                _discoveryStatus.Text = "Using your saved setup. Use \"Find automatically\" to look again.";
                _advancedToggle.Checked = true;
                Relayout();
            }
            else
            {
                Shown += (s, e) => BeginDiscovery();
            }
        }

        private Button _closeButton;

        private void BuildAdvancedPanel()
        {
            _advanced.Bounds = new Rectangle(14, 220, 610, 126);

            _advanced.Controls.Add(new Label { Text = "OmoBench checkout", Bounds = new Rectangle(0, 4, 120, 20) });
            _checkout.Bounds = new Rectangle(126, 0, 380, 24);
            _checkout.TextChanged += (s, e) =>
            {
                if (_assigning) { return; }
                bool hadResult = _state.Validated != null;
                AfterSettingsChanged(_state.NoteCheckoutEdited(), hadResult);
            };
            _advanced.Controls.Add(_checkout);
            Button browse = new Button { Text = "Browse…", Bounds = new Rectangle(514, -1, 96, 26) };
            browse.Click += (s, e) => BrowseForCheckout();
            _advanced.Controls.Add(browse);
            _advanced.Controls.Add(new Label
            {
                Text = "A Windows folder on a local drive (WSL reads it as /mnt/…).",
                Bounds = new Rectangle(126, 26, 484, 18),
                ForeColor = SystemColors.GrayText,
            });

            _advanced.Controls.Add(new Label { Text = "Python interpreter", Bounds = new Rectangle(0, 54, 120, 20) });
            _python.Bounds = new Rectangle(126, 50, 484, 24);
            _python.TextChanged += (s, e) =>
            {
                if (_assigning) { return; }
                // Changing the interpreter invalidates a validation just as
                // surely as changing the checkout: the pairing is what was
                // checked, not either half of it.
                bool hadResult = _state.Validated != null;
                AfterSettingsChanged(_state.NoteInterpreterEdited(), hadResult);
            };
            _advanced.Controls.Add(_python);
            _advanced.Controls.Add(new Label
            {
                Text = "Absolute path inside WSL, for example /home/you/.venvs/omobench-runner/bin/python",
                Bounds = new Rectangle(126, 76, 484, 18),
                ForeColor = SystemColors.GrayText,
            });

            _advanced.Controls.Add(new Label { Text = "Panel port", Bounds = new Rectangle(0, 104, 120, 20) });
            _port.Bounds = new Rectangle(126, 100, 90, 24);
            _port.Minimum = 1;
            _port.Maximum = 65535;
            _advanced.Controls.Add(_port);
            _advanced.Controls.Add(new Label
            {
                Text = "The companion moves to the next free port if this one is busy.",
                Bounds = new Rectangle(226, 104, 384, 18),
                ForeColor = SystemColors.GrayText,
            });
        }

        /// <summary>Position everything below the collapsible sections.</summary>
        private void Relayout()
        {
            int y = 196;
            bool showCandidates = _candidates.Items.Count > 1;
            _candidatesLabel.Visible = showCandidates;
            _candidates.Visible = showCandidates;
            if (showCandidates)
            {
                _candidatesLabel.Bounds = new Rectangle(14, 108, 610, 18);
                _candidates.Bounds = new Rectangle(14, 128, 610, 60);
                y = 196;
            }
            else
            {
                y = 112;
            }

            _advancedToggle.Bounds = new Rectangle(14, y, 320, 20);
            y += 26;

            _advanced.Visible = _advancedToggle.Checked;
            if (_advancedToggle.Checked)
            {
                _advanced.Bounds = new Rectangle(14, y, 610, 126);
                y += 134;
            }

            _verify.Bounds = new Rectangle(14, y, 180, 28);
            y += 36;

            _result.Bounds = new Rectangle(14, y, 610, 150);
            y += 158;

            _registration.Bounds = new Rectangle(14, y, 610, 18);
            y += 24;

            _save.Bounds = new Rectangle(14, y, 230, 30);
            _unregister.Bounds = new Rectangle(254, y, 160, 30);
            if (_closeButton != null) { _closeButton.Bounds = new Rectangle(528, y, 96, 30); }
            y += 42;

            ClientSize = new Size(640, y);
        }

        private void Add(Control control)
        {
            Controls.Add(control);
        }

        private void AssignText(TextBox box, string value)
        {
            _assigning = true;
            try { box.Text = value ?? string.Empty; }
            finally { _assigning = false; }
        }

        // -- discovery ---------------------------------------------------------

        /// <summary>
        /// Anything already in flight now describes a different question.
        /// Bumping the generation makes its result arrive and be discarded.
        /// </summary>
        /// <summary>
        /// Something a search depends on changed: the distribution, the
        /// checkout or the interpreter. Any result in flight is abandoned, and
        /// anything previously validated stops applying to what is on screen.
        /// </summary>
        private void InvalidateDiscovery()
        {
            bool hadResult = _state.Validated != null;
            AfterSettingsChanged(_state.SettingsChanged(), hadResult);
        }

        /// <summary>
        /// Reflect a change to the distribution, checkout or interpreter. The
        /// state has already been updated; this only says what happened.
        /// </summary>
        private void AfterSettingsChanged(bool stoppedSearch, bool hadResult)
        {
            if (stoppedSearch)
            {
                // Stop it rather than letting it finish and be discarded --
                // that would keep probing WSL for nothing, and leave the status
                // line frozen on a stale message.
                DiscoveryCancel cancel = _cancel;
                if (cancel != null) { cancel.Cancel(); }
                _discoveryStatus.Text = "Search stopped because you changed a setting. "
                    + "Use \"Find automatically\" to look again.";
            }
            else if (hadResult)
            {
                // The displayed combination is no longer the one that was
                // checked, so it must stop claiming to be ready.
                _discoveryStatus.Text = "These settings have not been checked. "
                    + "Use \"Check this environment\", or \"Find automatically\".";
            }

            if (hadResult)
            {
                // The detail below the status described the combination that
                // was checked. Leaving it there would go on showing a "Ready"
                // report next to settings it no longer describes.
                _result.Text = "The previous check does not apply to what is now entered.\r\n\r\n"
                    + "Use \"Check this environment\" to test these settings, "
                    + "or \"Find automatically\" to search again.";
            }
            ApplySearchControls();
        }

        private void ApplySearchControls()
        {
            _cancelDiscovery.Enabled = _state.CancelEnabled;
            _findAutomatically.Enabled = _state.FindEnabled;
        }

        private void BeginDiscovery()
        {
            int generation = _state.BeginSearch();
            if (generation == 0)
            {
                return;     // one search at a time
            }
            _cancel = new DiscoveryCancel();

            ApplySearchControls();
            _discoveryStatus.Text = "Looking for your OmoBench installation…";
            _candidates.Items.Clear();
            Relayout();

            Configuration snapshot = Collect();
            string executable = Application.ExecutablePath;
            DiscoveryCancel cancel = _cancel;

            Thread worker = new Thread(() =>
            {
                DiscoveryReport report;
                try
                {
                    report = Discovery.Run(snapshot, executable, new DiscoveryLimits(), cancel,
                        message => Post(() =>
                        {
                            if (_state.Generation == generation) { _discoveryStatus.Text = message; }
                        }));
                }
                catch (Exception error)
                {
                    report = new DiscoveryReport();
                    report.Notes.Add("The search failed: " + error.Message);
                }
                Post(() => FinishDiscovery(generation, report));
            });
            worker.IsBackground = true;
            worker.Start();
        }

        private void Post(Action action)
        {
            try
            {
                if (IsHandleCreated && !IsDisposed)
                {
                    BeginInvoke(action);
                }
            }
            catch (Exception)
            {
                // The window went away while the search was running.
            }
        }

        private void CancelDiscovery(string message)
        {
            DiscoveryCancel cancel = _cancel;
            if (cancel != null) { cancel.Cancel(); }
            _state.CancelSearch();
            ApplySearchControls();
            if (message != null && !IsDisposed) { _discoveryStatus.Text = message; }
        }

        private void FinishDiscovery(int generation, DiscoveryReport report)
        {
            // Nothing above this line. A stale completion must not touch the
            // running flag, the buttons, the status or the results: by the time
            // it arrives those may belong to a newer search, and clearing the
            // running flag here would let a second search start beside it.
            if (!_state.CompleteSearch(generation))
            {
                return;
            }
            ApplySearchControls();

            if (report.Cancelled)
            {
                _discoveryStatus.Text = "Search cancelled.";
                return;
            }

            _ready = report.Ready;
            _candidates.Items.Clear();
            foreach (InstallationCandidate candidate in report.Ready)
            {
                _candidates.Items.Add(candidate);
            }

            if (report.Ready.Count == 1)
            {
                Apply(report.Ready[0], false);
                ReportReadiness(report.Ready[0]);
            }
            else if (report.Ready.Count > 1)
            {
                _discoveryStatus.Text = report.Ready.Count.ToString(CultureInfo.InvariantCulture)
                    + " installations are ready. Choose one.";
                // Selecting the first is this code filling in a default, not
                // the person choosing it, so it must not overwrite an edit.
                _selectingDefault = true;
                try { _candidates.SelectedIndex = 0; }
                finally { _selectingDefault = false; }
                ReportReadiness(report.Ready[0]);
            }
            else
            {
                _discoveryStatus.Text = "No complete OmoBench installation was found.";
                _result.Text = report.Explain();
                // Manual entry is the way forward from here, so show it.
                _advancedToggle.Checked = true;
            }
            Relayout();
        }

        /// <summary>
        /// Say "ready" only when the combination actually on screen is the one
        /// that was validated. A search can validate a pairing while the person
        /// is typing a different interpreter; the result is then a real finding
        /// about a combination they are no longer looking at, and reporting it
        /// as theirs would be wrong.
        /// </summary>
        private void ReportReadiness(InstallationCandidate candidate)
        {
            Configuration displayed = Collect();
            if (_state.IsReadyFor(displayed.Distribution, displayed.CheckoutWindowsPath,
                    displayed.PythonWslPath))
            {
                _discoveryStatus.Text = _candidates.Items.Count > 1
                    ? _candidates.Items.Count.ToString(CultureInfo.InvariantCulture)
                        + " installations are ready. Choose one."
                    : "OmoBench is ready.";
                _result.Text = (candidate.Summary + "\r\n\r\n" + (candidate.Detail ?? ""))
                    .Replace("\n", "\r\n").TrimEnd();
                return;
            }

            _discoveryStatus.Text = "Found a working installation, but it is not what you have entered.";
            _result.Text = ("A validated installation was found:\r\n"
                + "    Distribution: " + candidate.Distribution + "\r\n"
                + "    Checkout:     " + candidate.CheckoutWindowsPath + "\r\n"
                + "    Interpreter:  " + candidate.PythonWslPath + "\r\n\r\n"
                + "Your entries differ from it, and that combination has not been checked. "
                + "Use \"Check this environment\" to test what you entered"
                + (_candidates.Items.Count > 0 ? ", or choose a listed installation." : "."));
            _advancedToggle.Checked = true;
        }

        private void ApplySelectedCandidate()
        {
            InstallationCandidate candidate = _candidates.SelectedItem as InstallationCandidate;
            if (candidate == null) { return; }

            // Choosing from the list is an explicit decision, so it applies the
            // whole candidate. Keeping an earlier hand-typed checkout here would
            // leave a distribution, checkout and interpreter that were never
            // validated together, under a message saying they were.
            //
            // Filling in the first entry at the end of a search is not that
            // decision, even though it arrives through the same event.
            bool explicitChoice = !_selectingDefault;
            Apply(candidate, explicitChoice);
            if (explicitChoice)
            {
                ReportReadiness(candidate);
            }
        }

        /// <summary>
        /// Fill in what was found.
        ///
        /// <paramref name="explicitChoice"/> separates the two ways this
        /// happens: a search finishing, or a default being filled in, must not
        /// step on what the person typed while it ran, whereas picking a
        /// candidate from the list is their own choice and replaces all three
        /// fields together.
        /// </summary>
        private void Apply(InstallationCandidate candidate, bool explicitChoice)
        {
            if (candidate == null) { return; }

            for (int index = 0; index < _found.Count; index++)
            {
                if (string.Equals(_found[index].Name, candidate.Distribution, StringComparison.Ordinal))
                {
                    if (_distributions.SelectedIndex != index)
                    {
                        _distributions.SelectedIndexChanged -= DistributionChanged;
                        _distributions.SelectedIndex = index;
                        _distributions.SelectedIndexChanged += DistributionChanged;
                    }
                    break;
                }
            }

            if (_state.MayApplyCheckout(explicitChoice))
            {
                AssignText(_checkout, candidate.CheckoutWindowsPath);
            }
            if (_state.MayApplyInterpreter(explicitChoice))
            {
                AssignText(_python, candidate.PythonWslPath);
            }

            _state.NoteApplied(candidate, explicitChoice);
        }

        private void DistributionChanged(object sender, EventArgs e)
        {
            InvalidateDiscovery();
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            // Stop searching. Nothing here touches a running panel: discovery
            // only ever read directories and ran short probes of its own.
            CancelDiscovery(null);
            base.OnFormClosing(e);
        }

        // -- the original manual path, unchanged -------------------------------

        private void LoadDistributions()
        {
            _distributions.Items.Clear();
            if (!Wsl.IsAvailable)
            {
                _result.Text = "WSL was not found on this machine (wsl.exe is missing from System32).\r\n\r\n"
                    + "Install WSL2 and a Linux distribution first; this first companion release reuses an "
                    + "existing WSL installation rather than creating one.";
                return;
            }

            _found = Wsl.ListDistributions();
            if (_found.Count == 0)
            {
                _result.Text = "No WSL distributions were found. Install one (for example Ubuntu) and press Refresh.";
                return;
            }

            foreach (WslDistribution distribution in _found)
            {
                _distributions.Items.Add(distribution);
            }

            for (int index = 0; index < _found.Count; index++)
            {
                if (string.Equals(_found[index].Name, _configuration.Distribution, StringComparison.Ordinal))
                {
                    _distributions.SelectedIndex = index;
                    return;
                }
            }
            for (int index = 0; index < _found.Count; index++)
            {
                if (_found[index].IsDefault)
                {
                    _distributions.SelectedIndex = index;
                    return;
                }
            }
            _distributions.SelectedIndex = 0;
        }

        private void BrowseForCheckout()
        {
            using (FolderBrowserDialog dialog = new FolderBrowserDialog())
            {
                dialog.Description = "Select your OmoBench checkout (the folder containing cli/ and benchmarks/).";
                if (Directory.Exists(_checkout.Text))
                {
                    dialog.SelectedPath = _checkout.Text;
                }
                if (dialog.ShowDialog(this) == DialogResult.OK)
                {
                    // Browsing is the person choosing a checkout, so it counts
                    // as an edit even though the text is set from code.
                    bool hadResult = _state.Validated != null;
                    AssignText(_checkout, dialog.SelectedPath);
                    AfterSettingsChanged(_state.NoteCheckoutEdited(), hadResult);
                }
            }
        }

        private WslDistribution SelectedDistribution
        {
            get { return _distributions.SelectedItem as WslDistribution; }
        }

        private Configuration Collect()
        {
            WslDistribution distribution = SelectedDistribution;
            return new Configuration
            {
                Distribution = distribution == null ? string.Empty : distribution.Name,
                CheckoutWindowsPath = _checkout.Text.Trim(),
                PythonWslPath = _python.Text.Trim(),
                Port = (int)_port.Value,
            };
        }

        private bool Verify()
        {
            Configuration candidate = Collect();

            string invalid = candidate.Validate();
            if (invalid != null)
            {
                _result.Text = invalid;
                _advancedToggle.Checked = true;
                Relayout();
                return false;
            }

            if (!Discovery.LooksLikeCheckout(candidate.CheckoutWindowsPath))
            {
                _result.Text = "That folder does not look like an OmoBench checkout:\r\n    "
                    + candidate.CheckoutWindowsPath
                    + "\r\n\r\nExpected to find cli\\src\\omobench and benchmarks\\profiles inside it.";
                return false;
            }

            string wrongVersion = EnvironmentCheck.DescribeDistribution(_found, candidate.Distribution);
            if (wrongVersion != null)
            {
                _result.Text = wrongVersion.Replace("\n", "\r\n");
                return false;
            }

            UseWaitCursor = true;
            _verify.Enabled = false;
            _result.Text = "Checking " + candidate.Distribution + "…\r\n"
                + "Running the configured interpreter and importing OmoBench. "
                + "This can take a few seconds the first time.";
            Application.DoEvents();
            try
            {
                EnvironmentCheckResult check = EnvironmentCheck.Run(candidate);
                _result.Text = (check.Summary + "\r\n\r\n" + (check.Detail ?? "")).Replace("\n", "\r\n").TrimEnd();
                return check.Ok;
            }
            finally
            {
                _verify.Enabled = true;
                UseWaitCursor = false;
            }
        }

        private void SaveAndRegister()
        {
            if (!Verify())
            {
                MessageBox.Show(this,
                    "The environment check did not pass, so nothing was saved.\n\nFix the problem shown below and try again.",
                    "OmoBench companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            Configuration candidate = Collect();
            try
            {
                candidate.Save();
                _configuration.Distribution = candidate.Distribution;
                _configuration.CheckoutWindowsPath = candidate.CheckoutWindowsPath;
                _configuration.PythonWslPath = candidate.PythonWslPath;
                _configuration.Port = candidate.Port;
            }
            catch (Exception error)
            {
                MessageBox.Show(this, "Could not save the configuration:\n\n" + error.Message,
                    "OmoBench companion", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            try
            {
                ProtocolRegistration.Register(Application.ExecutablePath);
            }
            catch (Exception error)
            {
                MessageBox.Show(this,
                    "The configuration was saved, but the omobench:// link could not be registered:\n\n" + error.Message,
                    "OmoBench companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                UpdateRegistrationLabel();
                return;
            }

            UpdateRegistrationLabel();
            MessageBox.Show(this,
                "Setup is complete.\n\nConfiguration file:\n    " + Configuration.FilePath
                + "\n\nRegistry key (your user only):\n    HKEY_CURRENT_USER\\" + ProtocolRegistration.KeyPath
                + "\n\nAny omobench://open link now opens the panel, and so does the "
                + "\"Open panel\" button in this companion's window. The \"Open OmoBench\" "
                + "button on the hosted website works once that page has been deployed with it.",
                "OmoBench companion", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        private void Unregister()
        {
            DialogResult choice = MessageBox.Show(this,
                "Remove this companion's omobench:// registration for your user account?\n\n"
                + "Your checkout, Python environment, benchmark evidence and model files are not touched.\n\n"
                + "Also delete the saved companion configuration?",
                "OmoBench companion", MessageBoxButtons.YesNoCancel, MessageBoxIcon.Question);
            if (choice == DialogResult.Cancel)
            {
                return;
            }

            string message;
            try
            {
                message = ProtocolRegistration.Unregister(Application.ExecutablePath);
            }
            catch (Exception error)
            {
                message = "Could not remove the registration: " + error.Message;
            }

            if (choice == DialogResult.Yes)
            {
                try
                {
                    message += Configuration.Delete()
                        ? "\nDeleted " + Configuration.FilePath
                        : "\nNo saved configuration file was present.";
                }
                catch (Exception error)
                {
                    message += "\nCould not delete the configuration: " + error.Message;
                }
            }

            UpdateRegistrationLabel();
            MessageBox.Show(this, message, "OmoBench companion", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        private void UpdateRegistrationLabel()
        {
            string command = ProtocolRegistration.RegisteredCommand();
            if (string.IsNullOrEmpty(command))
            {
                _registration.Text = "omobench:// is not registered for your user account yet.";
            }
            else if (ProtocolRegistration.IsRegisteredForThisExecutable(Application.ExecutablePath))
            {
                _registration.Text = "omobench:// is registered for your user account and points at this companion.";
            }
            else
            {
                _registration.Text = "omobench:// is registered, but points at another program: " + command;
            }
        }
    }
}
