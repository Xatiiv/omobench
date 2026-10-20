using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace OmoBench.Companion.Tests
{
    /// <summary>
    /// Focused tests for the companion's decision logic, run as a plain
    /// console program so the suite needs no test framework, no NuGet restore
    /// and no network -- the same constraints as the build itself.
    ///
    /// They link the shipped source files directly (see build.ps1), so what is
    /// tested here is the code that ships, not a copy of it. Nothing here
    /// starts WSL, a panel, or a benchmark.
    /// </summary>
    internal static class TestProgram
    {
        private static int _passed;
        private static readonly List<string> Failures = new List<string>();

        private static int Main()
        {
            ProtocolTests();
            CommandLineTests();
            WslPathTests();
            ConfigurationTests();
            StatusDocumentTests();
            RedactionTests();
            RuntimeDirectoryPrivacyTests();
            DistributionParsingTests();
            EnvironmentCheckTests();
            PanelProgramTests();
            ShutdownTests();
            RegistrationTests();

            Console.WriteLine();
            Console.WriteLine(_passed + " passed, " + Failures.Count + " failed");
            foreach (string failure in Failures)
            {
                Console.WriteLine("  FAILED: " + failure);
            }
            return Failures.Count == 0 ? 0 : 1;
        }

        // -- the one action this companion accepts ---------------------------

        private static void ProtocolTests()
        {
            Section("Protocol input (untrusted)");

            foreach (string accepted in new[]
            {
                "omobench://open", "omobench://open/", "OMOBENCH://OPEN", "omobench://Open",
                "  omobench://open  ", "omobench:open",
            })
            {
                Protocol.Result result = Protocol.Parse(accepted);
                Check("accepts " + Show(accepted), result.Accepted && result.Action == Protocol.ActionOpen);
            }

            // The local maintenance switches are matched as exact literals,
            // and no URI can ever spell one: every URI carries its scheme, so
            // a link cannot turn into "--register".
            foreach (string flag in new[] { "--register", "--unregister", "--check" })
            {
                Check("maintenance switch " + flag + " is not a URI",
                    !Protocol.Parse(flag).Accepted);
                Check("no omobench link can spell " + flag,
                    !("omobench://" + flag.TrimStart('-')).Equals(flag, StringComparison.Ordinal));
            }

            // Everything else is refused, including things that look close
            // enough to be dangerous if they were not.
            foreach (string rejected in new[]
            {
                null, "", "   ",
                "omobench://", "omobench://open/extra", "omobench://open?run=chat-small-v1",
                "omobench://open#fragment", "omobench://run", "omobench://exec",
                "omobench://open:8765", "omobench://user:pass@open",
                "http://127.0.0.1:8765/", "https://example.test/omobench://open",
                "file:///C:/Windows/System32/cmd.exe", "javascript:alert(1)",
                "omobench://open\" & calc.exe", "omobench://open; calc.exe",
                "omobench://open\r\nopen", "not a uri at all",
            })
            {
                Protocol.Result result = Protocol.Parse(rejected);
                Check("rejects " + Show(rejected), !result.Accepted && !string.IsNullOrEmpty(result.Rejection));
            }
        }

        // -- argument handling, including paths with spaces -------------------

        private static void CommandLineTests()
        {
            Section("Command-line quoting");

            Check("plain argument is not quoted", CommandLine.Quote("python") == "python");
            Check("path with spaces is quoted",
                CommandLine.Quote(@"C:\Users\Sam Smith\Code\omobench") == "\"C:\\Users\\Sam Smith\\Code\\omobench\"");
            Check("empty argument becomes an empty quoted string", CommandLine.Quote("") == "\"\"");
            Check("embedded quote is escaped", CommandLine.Quote("a\"b") == "\"a\\\"b\"");
            Check("trailing backslash is doubled before the closing quote",
                CommandLine.Quote(@"C:\path with space\") == "\"C:\\path with space\\\\\"");
            Check("interior backslashes are preserved",
                CommandLine.Quote(@"/home/sam/.venvs/omo bench/bin/python")
                    == "\"/home/sam/.venvs/omo bench/bin/python\"");

            string joined = CommandLine.Join(new[] { "wsl.exe", "--distribution", "My Distro", "--", "/usr/bin/python 3" });
            Check("join quotes only what needs it",
                joined == "wsl.exe --distribution \"My Distro\" -- \"/usr/bin/python 3\"");

            // A value that would be dangerous in a shell is just one argument
            // here: nothing is ever handed to cmd.exe or /bin/sh.
            Check("shell metacharacters stay inside one argument",
                CommandLine.Quote("a b & calc.exe") == "\"a b & calc.exe\"");
        }

        private static void WslPathTests()
        {
            Section("Windows -> WSL path translation");

            string path;
            Check("drive path translates",
                CommandLine.TryToWslPath(@"C:\Users\sam\Code\omobench", out path) && path == "/mnt/c/Users/sam/Code/omobench");
            Check("spaces survive translation",
                CommandLine.TryToWslPath(@"D:\My Code\omo bench", out path) && path == "/mnt/d/My Code/omo bench");
            Check("forward slashes are accepted",
                CommandLine.TryToWslPath("C:/Users/sam", out path) && path == "/mnt/c/Users/sam");
            Check("drive root translates",
                CommandLine.TryToWslPath(@"E:\", out path) && path == "/mnt/e");
            Check("trailing separator is trimmed",
                CommandLine.TryToWslPath(@"C:\Code\omobench\", out path) && path == "/mnt/c/Code/omobench");

            Check("UNC path is refused rather than guessed", !CommandLine.TryToWslPath(@"\\server\share\omobench", out path));
            Check("relative path is refused", !CommandLine.TryToWslPath(@"omobench\cli", out path));
            Check("empty path is refused", !CommandLine.TryToWslPath("", out path));
            Check("null path is refused", !CommandLine.TryToWslPath(null, out path));
        }

        // -- configuration and prerequisite failures --------------------------

        private static void ConfigurationTests()
        {
            Section("Configuration validation");

            Configuration empty = new Configuration();
            Check("a fresh configuration is incomplete", !empty.IsComplete);
            Check("an empty configuration explains the first missing piece",
                empty.Validate() != null && empty.Validate().Contains("distribution"));

            string existingDirectory = Path.GetTempPath().TrimEnd('\\');
            Configuration configuration = new Configuration
            {
                Distribution = "Ubuntu",
                CheckoutWindowsPath = existingDirectory,
                PythonWslPath = "/home/sam/.venvs/omobench-runner/bin/python",
                Port = 8765,
            };
            Check("a complete configuration validates", configuration.Validate() == null);
            Check("a complete configuration is complete", configuration.IsComplete);

            Configuration missingCheckout = Clone(configuration);
            missingCheckout.CheckoutWindowsPath = @"C:\definitely\not\here\omobench";
            Check("a missing checkout is reported",
                (missingCheckout.Validate() ?? "").Contains("no longer exists"));

            Configuration relativePython = Clone(configuration);
            relativePython.PythonWslPath = "python";
            Check("a non-absolute interpreter is refused",
                (relativePython.Validate() ?? "").Contains("absolute Linux path"));

            Configuration badPort = Clone(configuration);
            badPort.Port = 0;
            Check("an invalid port is refused", (badPort.Validate() ?? "").Contains("port"));

            Configuration uncCheckout = Clone(configuration);
            uncCheckout.CheckoutWindowsPath = @"\\server\share\omobench";
            Check("a checkout WSL cannot reach is refused",
                (uncCheckout.Validate() ?? "").Contains("/mnt/"));

            Check("the config path is per user and outside the checkout",
                Configuration.FilePath.IndexOf("OmoBench", StringComparison.OrdinalIgnoreCase) >= 0
                && Configuration.FilePath.IndexOf(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    StringComparison.OrdinalIgnoreCase) == 0);
        }

        // -- the panel's startup document -------------------------------------

        private static void StatusDocumentTests()
        {
            Section("Panel status document");

            string error;
            PanelStatus ready = PanelStatus.Parse(
                "{\"schema\": 1, \"state\": \"ready\", \"url\": \"http://127.0.0.1:8765/#token=abc123\", " +
                "\"origin\": \"http://127.0.0.1:8765\", \"port\": 8765, \"token\": \"abc123\", \"pid\": 42}", out error);
            Check("a ready document parses", ready != null && ready.IsReady && error == null);
            Check("the URL is carried through", ready != null && ready.Url == "http://127.0.0.1:8765/#token=abc123");
            Check("the token is carried through", ready != null && ready.Token == "abc123");
            Check("the port is carried through", ready != null && ready.Port == 8765);

            PanelStatus failed = PanelStatus.Parse(
                "{\"schema\": 1, \"state\": \"failed\", \"reason\": \"already_running\", " +
                "\"message\": \"A control panel is already running for this checkout.\"}", out error);
            Check("a failure document parses", failed != null && failed.IsFailed);
            Check("the machine-readable reason survives", failed != null && failed.Reason == "already_running");

            PanelStatus futureSchema = PanelStatus.Parse("{\"schema\": 99, \"state\": \"ready\"}", out error);
            Check("an unknown schema is refused, not guessed at",
                futureSchema == null && (error ?? "").Contains("schema"));

            PanelStatus empty = PanelStatus.Parse("", out error);
            Check("an empty document is refused", empty == null && error != null);

            PanelStatus offHost = PanelStatus.Parse(
                "{\"schema\": 1, \"state\": \"ready\", \"url\": \"http://evil.test/#token=abc\", " +
                "\"origin\": \"http://evil.test\", \"port\": 80, \"token\": \"abc\"}", out error);
            Check("a non-loopback URL is refused so the token is never sent off-host",
                offHost == null && (error ?? "").Contains("loopback"));

            PanelStatus tokenless = PanelStatus.Parse(
                "{\"schema\": 1, \"state\": \"ready\", \"url\": \"http://127.0.0.1:8765/\", " +
                "\"origin\": \"http://127.0.0.1:8765\", \"port\": 8765}", out error);
            Check("a ready document without a token is refused", tokenless == null);

            Check("loopback check accepts 127.0.0.1", PanelStatus.IsLoopbackHttpUrl("http://127.0.0.1:8765/#token=x"));
            Check("loopback check rejects https elsewhere", !PanelStatus.IsLoopbackHttpUrl("https://example.test/"));
            Check("loopback check rejects credentials",
                !PanelStatus.IsLoopbackHttpUrl("http://user:pw@127.0.0.1:8765/"));
            Check("loopback check rejects a look-alike host",
                !PanelStatus.IsLoopbackHttpUrl("http://127.0.0.1.evil.test/"));
        }

        // -- tokens must not reach logs ---------------------------------------

        private static void RedactionTests()
        {
            Section("Token redaction in diagnostics");

            string banner = "OmoBench local control panel\n\nhttp://127.0.0.1:8765/#token=VerySecretValue123\n";
            string redacted = Diagnostics.Redact(banner);
            Check("the panel banner's token is removed", !redacted.Contains("VerySecretValue123"));
            Check("the rest of the banner survives", redacted.Contains("http://127.0.0.1:8765/"));

            Check("header form is redacted",
                !Diagnostics.Redact("X-OmoBench-Token: abcdef").Contains("abcdef"));
            Check("assignment form is redacted",
                !Diagnostics.Redact("token=abcdef").Contains("abcdef"));
            Check("api key form is redacted",
                !Diagnostics.Redact("api_key=supersecret").Contains("supersecret"));
            Check("ordinary text is untouched",
                Diagnostics.Redact("Starting the panel on port 8765.") == "Starting the panel on port 8765.");
            Check("null is handled", Diagnostics.Redact(null) == null);

            Check("the log lives outside the checkout, under the user's local app data",
                Diagnostics.LogPath.IndexOf(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    StringComparison.OrdinalIgnoreCase) == 0);
        }

        // -- the directory that holds the token document ----------------------

        private static void RuntimeDirectoryPrivacyTests()
        {
            Section("Runtime directory privacy");

            // The panel writes the status document from inside WSL through
            // /mnt/c, where DrvFs discards the 0600 mode it asks for, so the
            // directory's own access list is what actually protects the token.
            string path = Configuration.EnsureRuntimeDirectory();
            Check("the runtime directory exists", Directory.Exists(path));
            Check("it is under this user's local app data, not the checkout",
                path.IndexOf(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    StringComparison.OrdinalIgnoreCase) == 0);

            string[] identities = Configuration.RuntimeDirectoryAccessIdentities();
            string me = System.Security.Principal.WindowsIdentity.GetCurrent().Name;
            bool mentionsMe = false;
            bool mentionsOnlyExpected = true;
            foreach (string identity in identities)
            {
                if (identity.Equals(me, StringComparison.OrdinalIgnoreCase)) { mentionsMe = true; }
                bool expected = identity.Equals(me, StringComparison.OrdinalIgnoreCase)
                    || identity.EndsWith("SYSTEM", StringComparison.OrdinalIgnoreCase);
                if (!expected) { mentionsOnlyExpected = false; }
            }
            Check("this user can use it", mentionsMe);
            Check("nobody else is granted access (inheritance is switched off): "
                + string.Join(", ", identities), mentionsOnlyExpected);
        }

        // -- WSL discovery -----------------------------------------------------

        private static void DistributionParsingTests()
        {
            Section("WSL distribution list parsing");

            string listing = string.Join("\n", new[]
            {
                "  NAME                   STATE           VERSION",
                "* Ubuntu                 Running         2",
                "  docker-desktop         Stopped         2",
                "  Legacy Distro Name     Stopped         1",
            });

            List<WslDistribution> distributions = Wsl.ParseDistributionList(listing);
            Check("three distributions are found", distributions.Count == 3);
            Check("the default is marked", distributions[0].IsDefault && distributions[0].Name == "Ubuntu");
            Check("the version is read", distributions[0].Version == 2);
            Check("a name with spaces survives", distributions[2].Name == "Legacy Distro Name");
            Check("a WSL1 distribution keeps its version", distributions[2].Version == 1);
            Check("the header row is ignored", distributions.TrueForAll(d => d.Name != "NAME"));

            Check("empty output yields nothing", Wsl.ParseDistributionList("").Count == 0);
            Check("null output yields nothing", Wsl.ParseDistributionList(null).Count == 0);

            // WSL2 is required; a WSL1 selection is explained, not silently used.
            Check("WSL2 selection passes", EnvironmentCheck.DescribeDistribution(distributions, "Ubuntu") == null);
            Check("WSL1 selection is explained",
                (EnvironmentCheck.DescribeDistribution(distributions, "Legacy Distro Name") ?? "").Contains("WSL2"));
            Check("an unknown distribution is explained",
                (EnvironmentCheck.DescribeDistribution(distributions, "Nope") ?? "").Contains("not an installed"));
        }

        // -- "is this really the right environment?" ---------------------------

        private static void EnvironmentCheckTests()
        {
            Section("Environment check");

            Check("the probe is a single argument with no double quotes to mangle",
                !EnvironmentCheck.ProbeScript.Contains("\""));

            List<string> program = EnvironmentCheck.BuildProbeProgram("/home/sam/.venvs/omo/bin/python");
            Check("the probe runs the configured interpreter directly",
                program.Count == 3 && program[0] == "/home/sam/.venvs/omo/bin/python" && program[1] == "-c");

            string checkout = "/mnt/c/Users/sam/Code/omobench";
            string good = "OMOBENCH_PROBE {\"module\": \"" + checkout + "/cli/src/omobench/cli.py\", " +
                "\"pinned\": \"0.7.3\", \"runner\": \"0.7.3\", \"executable\": \"/home/sam/.venvs/omo/bin/python\"}";

            EnvironmentCheckResult ok = EnvironmentCheck.Interpret(0, good, checkout, false);
            Check("a matching environment passes", ok.Ok);
            Check("the pinned runner version is reported", ok.InstalledRunnerVersion == "0.7.3");

            EnvironmentCheckResult wrongCheckout = EnvironmentCheck.Interpret(
                0, good.Replace(checkout, "/home/sam/other-omobench"), checkout, false);
            Check("OmoBench imported from elsewhere is refused",
                !wrongCheckout.Ok && wrongCheckout.Summary.Contains("different checkout"));

            EnvironmentCheckResult noRunner = EnvironmentCheck.Interpret(
                0, good.Replace("\"runner\": \"0.7.3\"", "\"runner\": null"), checkout, false);
            Check("a missing pinned runner is refused",
                !noRunner.Ok && noRunner.Summary.Contains("GuideLLM runner is not installed"));

            EnvironmentCheckResult wrongRunner = EnvironmentCheck.Interpret(
                0, good.Replace("\"runner\": \"0.7.3\"", "\"runner\": \"0.8.0\""), checkout, false);
            Check("a different runner version is refused",
                !wrongRunner.Ok && wrongRunner.Summary.Contains("0.8.0"));

            EnvironmentCheckResult cannotImport = EnvironmentCheck.Interpret(
                1, "ModuleNotFoundError: No module named 'omobench'", checkout, false);
            Check("an interpreter without OmoBench is refused",
                !cannotImport.Ok && cannotImport.Summary.Contains("cannot import OmoBench"));

            EnvironmentCheckResult timedOut = EnvironmentCheck.Interpret(0, "", checkout, true);
            Check("a timeout is reported as a timeout", !timedOut.Ok && timedOut.Summary.Contains("timed out"));

            // A sibling directory sharing a prefix is not the checkout.
            Check("a prefix-sharing sibling is not accepted as the checkout",
                !EnvironmentCheck.ImportsFromCheckout("/mnt/c/Code/omobench-copy/cli/x.py", "/mnt/c/Code/omobench"));
            Check("a real child path is accepted",
                EnvironmentCheck.ImportsFromCheckout("/mnt/c/Code/omobench/cli/x.py", "/mnt/c/Code/omobench"));
        }

        // -- how the panel is actually started ---------------------------------

        private static void PanelProgramTests()
        {
            Section("Panel start-up command");

            Configuration configuration = new Configuration
            {
                Distribution = "Ubuntu",
                CheckoutWindowsPath = @"C:\Users\Sam Smith\Code\omobench",
                PythonWslPath = "/home/sam/.venvs/omobench runner/bin/python",
                Port = 8765,
            };

            List<string> program = PanelSession.BuildPanelProgram(
                configuration, 8770, "/mnt/c/Users/Sam Smith/AppData/Local/OmoBench/companion/panel-1.json");

            Check("the configured interpreter is invoked directly, not through a shell or PATH",
                program[0] == "/home/sam/.venvs/omobench runner/bin/python");
            Check("the CLI module is used", program[1] == "-m" && program[2] == "omobench.cli" && program[3] == "ui");
            Check("the existing --no-browser behaviour is used", program.Contains("--no-browser"));
            Check("the chosen port is passed", program.Contains("8770"));
            Check("the private status channel is passed", program.Contains("--status-file"));
            Check("no benchmark or download is requested",
                !program.Contains("run") && !program.Contains("prepare") && !program.Contains("launch"));

            string arguments = Wsl.BuildRunArguments(
                configuration.Distribution, configuration.CheckoutWslPath, program);
            Check("the distribution is selected explicitly", arguments.Contains("--distribution Ubuntu"));
            Check("the working directory is the checkout, quoted",
                arguments.Contains("--cd \"/mnt/c/Users/Sam Smith/Code/omobench\""));
            Check("the program follows -- so wsl.exe stops parsing options", arguments.Contains(" -- "));
            Check("the interpreter path with spaces is quoted",
                arguments.Contains("\"/home/sam/.venvs/omobench runner/bin/python\""));
            Check("no shell is involved", !arguments.Contains("bash") && !arguments.Contains("cmd.exe")
                && !arguments.Contains("source "));

            Check("a free port is found from the preferred one", PanelSession.FindAvailablePort(49200) >= 49200);
        }

        // -- stopping: graceful, and only what we own -------------------------

        private static void ShutdownTests()
        {
            Section("Panel shutdown");

            List<string> shutdown = PanelSession.BuildShutdownProgram(4242);
            Check("the panel is asked to stop, not killed",
                shutdown.Count == 3 && shutdown[0] == "kill" && shutdown[1] == "-TERM" && shutdown[2] == "4242");
            Check("SIGKILL is never used for the panel itself", !shutdown.Contains("-KILL") && !shutdown.Contains("-9"));

            // A stale or malformed document must not be able to aim the signal
            // at init, at the whole process group, or at an arbitrary process.
            foreach (int refused in new[] { 0, 1, -1, -4242 })
            {
                bool rejected = false;
                try { PanelSession.BuildShutdownProgram(refused); }
                catch (ArgumentOutOfRangeException) { rejected = true; }
                Check("a pid of " + refused + " is refused", rejected);
            }

            string arguments = Wsl.BuildRunArguments("Ubuntu", null, PanelSession.BuildShutdownProgram(4242));
            Check("the signal is scoped to the configured distribution",
                arguments.Contains("--distribution Ubuntu") && arguments.Contains(" -- kill -TERM 4242"));
            Check("no global WSL shutdown is ever issued",
                !arguments.Contains("--shutdown") && !arguments.Contains("--terminate"));

            // The pid only ever comes from a status document that parsed as
            // ready; a failure document carries none.
            string error;
            PanelStatus failed = PanelStatus.Parse(
                "{\"schema\": 1, \"state\": \"failed\", \"reason\": \"already_running\", \"pid\": 9999}", out error);
            Check("a failed document still parses", failed != null);
            Check("nothing is stopped from a failure document, because it is never ready",
                failed != null && !failed.IsReady);

            PanelStatus ready = PanelStatus.Parse(
                "{\"schema\": 1, \"state\": \"ready\", \"url\": \"http://127.0.0.1:8765/#token=t\", " +
                "\"origin\": \"http://127.0.0.1:8765\", \"port\": 8765, \"token\": \"t\", \"pid\": 1417}", out error);
            Check("a ready document carries the panel's in-WSL pid",
                ready != null && ready.PanelProcessId == 1417);
        }

        // -- protocol registration ---------------------------------------------

        private static void RegistrationTests()
        {
            Section("Protocol registration");

            string command = ProtocolRegistration.BuildCommand(@"C:\Program Files\OmoBench\OmoBenchCompanion.exe");
            Check("the executable path is quoted",
                command.StartsWith("\"C:\\Program Files\\OmoBench\\OmoBenchCompanion.exe\"", StringComparison.Ordinal));
            Check("the URI is passed as a single argument placeholder", command.EndsWith("\"%1\"", StringComparison.Ordinal));
            Check("the registration is per user, under Software\\Classes",
                ProtocolRegistration.KeyPath == @"Software\Classes\omobench");
        }

        // -- harness -----------------------------------------------------------

        private static Configuration Clone(Configuration source)
        {
            return new Configuration
            {
                Distribution = source.Distribution,
                CheckoutWindowsPath = source.CheckoutWindowsPath,
                PythonWslPath = source.PythonWslPath,
                Port = source.Port,
            };
        }

        private static string Show(string value)
        {
            if (value == null) return "<null>";
            if (value.Length == 0) return "<empty>";
            return "'" + value.Replace("\r", "\\r").Replace("\n", "\\n") + "'";
        }

        private static void Section(string title)
        {
            Console.WriteLine();
            Console.WriteLine("== " + title + " ==");
        }

        private static void Check(string description, bool condition)
        {
            if (condition)
            {
                _passed++;
                Console.WriteLine("  ok    " + description);
            }
            else
            {
                Failures.Add(description);
                Console.WriteLine("  FAIL  " + description);
            }
        }
    }
}
