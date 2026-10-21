using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Windows.Forms;

namespace OmoBench.Companion
{
    /// <summary>
    /// The one-time graphical setup: pick the WSL distribution, the checkout
    /// and the Python environment, verify them, save, and register the
    /// omobench:// handler. No terminal, and no values baked into the build --
    /// everything here is discovered on this machine or chosen by the person
    /// running it.
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

        public SetupForm(Configuration configuration)
        {
            _configuration = configuration;

            Text = "OmoBench companion — setup";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(640, 470);
            Font = new Font("Segoe UI", 9f);

            int y = 14;
            Add(new Label
            {
                Text = "Point the companion at the WSL environment that already has OmoBench installed.",
                Bounds = new Rectangle(14, y, 610, 20),
                ForeColor = SystemColors.GrayText,
            });
            y += 28;

            Add(new Label { Text = "WSL distribution", Bounds = new Rectangle(14, y + 4, 120, 20) });
            _distributions.Bounds = new Rectangle(140, y, 380, 24);
            _distributions.DropDownStyle = ComboBoxStyle.DropDownList;
            Add(_distributions);
            Button refresh = new Button { Text = "Refresh", Bounds = new Rectangle(528, y - 1, 96, 26) };
            refresh.Click += (s, e) => LoadDistributions();
            Add(refresh);
            y += 36;

            Add(new Label { Text = "OmoBench checkout", Bounds = new Rectangle(14, y + 4, 120, 20) });
            _checkout.Bounds = new Rectangle(140, y, 380, 24);
            Add(_checkout);
            Button browse = new Button { Text = "Browse…", Bounds = new Rectangle(528, y - 1, 96, 26) };
            browse.Click += (s, e) => BrowseForCheckout();
            Add(browse);
            y += 30;
            Add(new Label
            {
                Text = "A Windows folder on a local drive (WSL reads it as /mnt/…).",
                Bounds = new Rectangle(140, y, 480, 18),
                ForeColor = SystemColors.GrayText,
            });
            y += 26;

            Add(new Label { Text = "Python interpreter", Bounds = new Rectangle(14, y + 4, 120, 20) });
            _python.Bounds = new Rectangle(140, y, 484, 24);
            Add(_python);
            y += 30;
            Add(new Label
            {
                Text = "Absolute path inside WSL, for example /home/you/.venvs/omobench-runner/bin/python",
                Bounds = new Rectangle(140, y, 484, 18),
                ForeColor = SystemColors.GrayText,
            });
            y += 28;

            Add(new Label { Text = "Panel port", Bounds = new Rectangle(14, y + 4, 120, 20) });
            _port.Bounds = new Rectangle(140, y, 90, 24);
            _port.Minimum = 1;
            _port.Maximum = 65535;
            Add(_port);
            Add(new Label
            {
                Text = "The companion moves to the next free port if this one is busy.",
                Bounds = new Rectangle(240, y + 4, 384, 18),
                ForeColor = SystemColors.GrayText,
            });
            y += 36;

            _verify.Text = "Check this environment";
            _verify.Bounds = new Rectangle(140, y, 180, 28);
            _verify.Click += (s, e) => Verify();
            Add(_verify);
            y += 36;

            _result.Bounds = new Rectangle(14, y, 610, 150);
            _result.Multiline = true;
            _result.ReadOnly = true;
            _result.ScrollBars = ScrollBars.Vertical;
            _result.BackColor = SystemColors.Window;
            Add(_result);
            y += 158;

            _registration.Bounds = new Rectangle(14, y, 610, 18);
            _registration.ForeColor = SystemColors.GrayText;
            Add(_registration);
            y += 24;

            _save.Text = "Save and register omobench://";
            _save.Bounds = new Rectangle(14, y, 230, 30);
            _save.Click += (s, e) => SaveAndRegister();
            Add(_save);

            _unregister.Text = "Remove registration";
            _unregister.Bounds = new Rectangle(254, y, 160, 30);
            _unregister.Click += (s, e) => Unregister();
            Add(_unregister);

            Button close = new Button { Text = "Close", Bounds = new Rectangle(528, y, 96, 30) };
            close.Click += (s, e) => Close();
            Add(close);

            LoadDistributions();
            _checkout.Text = _configuration.CheckoutWindowsPath;
            _python.Text = _configuration.PythonWslPath;
            _port.Value = _configuration.Port >= 1 && _configuration.Port <= 65535 ? _configuration.Port : Configuration.DefaultPort;
            UpdateRegistrationLabel();
        }

        private void Add(Control control)
        {
            Controls.Add(control);
        }

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
                    _checkout.Text = dialog.SelectedPath;
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
                return false;
            }

            // The checkout must look like one before anything is launched.
            if (!Directory.Exists(Path.Combine(candidate.CheckoutWindowsPath, "cli", "src", "omobench")))
            {
                _result.Text = "That folder does not look like an OmoBench checkout:\r\n    "
                    + candidate.CheckoutWindowsPath
                    + "\r\n\r\nExpected to find cli\\src\\omobench inside it.";
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
