using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using OmoBench.Companion;

namespace OmoBench.Installer.Fixtures
{
    /// <summary>
    /// The real installer window, with a sandbox underneath it.
    ///
    /// One behaviour only shows on a running desktop: what the wizard does
    /// between pressing Stop and the work actually stopping. The window
    /// disables Stop immediately; the preparation keeps running until it
    /// reaches a checkpoint; and the completion it then delivers is the only
    /// thing that puts the buttons back. When that completion was discarded as
    /// stale, the wizard sat there with nothing enabled and no way forward --
    /// and closing it skipped the wait that lets the worker stop cleanly,
    /// because the window already believed it had finished.
    ///
    /// Everything this fixture touches is its own: the install location, the
    /// configuration, the runtime directory and log, the registry entries, the
    /// managed source and archive directories. Nothing runs in WSL at all --
    /// the distribution is a fake that answers instantly except for the pinned
    /// install step, which waits, watching for cancellation, exactly as the
    /// real one does over a slow connection. No model, no benchmark, no pip.
    ///
    /// Beyond those redirections the window, its state machine, its closing
    /// behaviour and the preparation it drives are the shipped code.
    /// </summary>
    internal static class WizardFixtureProgram
    {
        [STAThread]
        private static int Main(string[] arguments)
        {
            string sandbox = Path.Combine(Path.GetTempPath(),
                "omobench-wizard-fixture-" + Guid.NewGuid().ToString("N"));
            string fixtureKeys = @"Software\OmoBench-fixture\" + Guid.NewGuid().ToString("N");

            try
            {
                Directory.CreateDirectory(sandbox);

                InstallPaths.RootOverride = Path.Combine(sandbox, "install");
                InstallPaths.UninstallKeyPathOverride = fixtureKeys + @"\Uninstall\OmoBench";
                ProtocolRegistration.KeyPathOverride = fixtureKeys + @"\Classes\omobench";
                Configuration.FilePathOverride = Path.Combine(sandbox, "companion.config");
                Configuration.RuntimeDirectoryOverride = Path.Combine(sandbox, "runtime");

                ManagedEnvironment.SourceRootOverride = Path.Combine(sandbox, "source");
                ManagedEnvironment.ArchiveDirectoryOverride = Path.Combine(sandbox, "archives");
                ManagedEnvironment.EnvironmentRootOverride = "/tmp/omobench-wizard-fixture";

                BuildInfo build = new BuildInfo
                {
                    Version = "0.0.0-fixture",
                    Revision = "f1xturef1xturef1xturef1xturef1xturef1xtu",
                    BuiltAt = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"),
                    SourceClean = true,
                };

                // A payload that passes the installer's own checks without
                // being a download of anything.
                byte[] payload = Encoding.UTF8.GetBytes("fixture runtime payload");
                RuntimePayload.Opener = () => new MemoryStream(payload);
                RuntimePayload.ExpectedSha256 = RuntimePayload.Sha256(payload);
                RuntimePayload.ExpectedBytes = payload.Length;

                // A machine that is ready, without asking this one.
                SystemCheck.Is64BitProvider = () => true;
                SystemCheck.WindowsBuildProvider = () => 26100;
                SystemCheck.WindowsNameProvider = () => "Windows 11 (fixture)";
                SystemCheck.WslPresentProvider = () => true;
                SystemCheck.DistributionProvider = () => new List<WslDistribution>
                {
                    new WslDistribution { Name = "Ubuntu", Version = 2, IsDefault = true },
                };

                // A search that finds nothing, so the wizard offers to prepare
                // a managed environment rather than reusing a real checkout.
                Discovery.WslAvailable = () => true;
                Discovery.DistributionLister = SystemCheck.DistributionProvider;
                Discovery.SearchRootProvider = () => new List<string>();
                Discovery.WslRunner = (text, timeout, cancel) =>
                    new ProcessResult { ExitCode = 1, StandardError = "fixture" };

                FakeDistribution distribution = new FakeDistribution
                {
                    UnpackInto = ManagedEnvironment.SourceDirectory(build.Revision),
                };
                ManagedEnvironment.WslRunner = distribution.Run;
                ManagedEnvironment.Validator = configuration => new EnvironmentCheckResult
                {
                    Ok = true,
                    Summary = "Ready (fixture).",
                    Detail = "Nothing was installed; this is a fixture.",
                };

                Console.WriteLine("Installer wizard fixture");
                Console.WriteLine("  sandbox      : " + sandbox);
                Console.WriteLine("  install into : " + InstallPaths.ProgramDirectory);
                Console.WriteLine("  configuration: " + Configuration.FilePath);
                Console.WriteLine("  registry     : HKCU\\" + fixtureKeys);
                Console.WriteLine();
                Console.WriteLine("The window is the real installer. Nothing outside the sandbox is");
                Console.WriteLine("read or written, nothing runs in WSL, and no benchmark is run.");

                Application.EnableVisualStyles();
                using (InstallerForm form = new InstallerForm(build, Encoding.UTF8.GetBytes("companion")))
                {
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
        /// A distribution that answers everything instantly except the pinned
        /// install, which is the step a person actually waits through -- and
        /// so the step Stop has to work during.
        /// </summary>
        private sealed class FakeDistribution
        {
            public string UnpackInto;

            /// <summary>How long the install step takes if nobody stops it.</summary>
            public int InstallMilliseconds = 120000;

            /// <summary>
            /// How long it takes to wind up after being told to stop. Real
            /// steps do not vanish the instant the token is cancelled, and the
            /// window's behaviour during that gap is the whole point of this
            /// fixture.
            /// </summary>
            public int WindDownMilliseconds = 4000;

            public ProcessResult Run(string arguments, int timeout, DiscoveryCancel cancel)
            {
                if (arguments.Contains(" printenv HOME"))
                {
                    return new ProcessResult { ExitCode = 0, StandardOutput = "/home/fixture\n" };
                }
                if (arguments.Contains(" which python3"))
                {
                    return new ProcessResult { ExitCode = 0, StandardOutput = "/usr/bin/python3\n" };
                }
                if (arguments.Contains(" tar "))
                {
                    if (UnpackInto != null) { PlantPayload(UnpackInto); }
                    return new ProcessResult { ExitCode = 0 };
                }
                if (arguments.Contains("-m pip "))
                {
                    return Wait(cancel, InstallMilliseconds, WindDownMilliseconds);
                }
                return new ProcessResult { ExitCode = 0 };
            }

            /// <summary>
            /// Waits the way a long download does: in slices, watching for the
            /// cancellation it has been handed, and reporting that it stopped
            /// rather than that it succeeded.
            /// </summary>
            private static ProcessResult Wait(DiscoveryCancel cancel, int milliseconds, int windDown)
            {
                for (int waited = 0; waited < milliseconds; waited += 100)
                {
                    if (cancel != null && cancel.IsCancelled)
                    {
                        Thread.Sleep(windDown);
                        return new ProcessResult { ExitCode = -1, TimedOut = true };
                    }
                    Thread.Sleep(100);
                }
                return new ProcessResult { ExitCode = 0 };
            }
        }

        /// <summary>
        /// A complete payload, as a correct build produces one: every path the
        /// installer requires, and a manifest covering all of them.
        /// </summary>
        private static void PlantPayload(string root)
        {
            List<string> written = new List<string>();
            foreach (string required in RuntimeManifest.RequiredPaths)
            {
                string path = Path.Combine(root, required.Replace('/', Path.DirectorySeparatorChar));
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.WriteAllText(path, "fixture contents of " + required);
                written.Add(required);
            }

            StringBuilder manifest = new StringBuilder();
            foreach (string relative in written)
            {
                string path = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
                manifest.Append(RuntimePayload.Sha256(File.ReadAllBytes(path)))
                    .Append("  ").Append(relative).Append("\n");
            }
            File.WriteAllText(Path.Combine(root, RuntimeManifest.FileName), manifest.ToString());
        }
    }
}
