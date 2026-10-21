using System;
using System.Collections.Generic;
using System.Diagnostics;
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
            StatusDocumentStrictnessTests();
            RedactionTests();
            RuntimeDirectoryPrivacyTests();
            RuntimeDirectoryFailureTests();
            DistributionParsingTests();
            EnvironmentCheckTests();
            PanelProgramTests();
            ShutdownTests();
            ShutdownOutcomeTests();
            ShutdownRobustnessTests();
            RecoveryCorrectnessTests();
            ActivityCheckTests();
            ArgumentDeliveryTests();
            CheckoutDiscoveryTests();
            InterpreterDiscoveryTests();
            DiscoveryOutcomeTests();
            SetupStateTests();
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

        // -- the document decides where a token goes, so it is read strictly ---

        /// <summary>Build a ready document, so each test can vary one field.</summary>
        private static string ReadyDocument(
            string url, string origin, string port, string token, string pid)
        {
            return "{\"schema\": 1, \"state\": \"ready\", \"url\": \"" + url + "\", "
                + "\"origin\": \"" + origin + "\", \"port\": " + port + ", "
                + "\"token\": \"" + token + "\", \"pid\": " + pid + "}";
        }

        private static void StatusDocumentStrictnessTests()
        {
            Section("Panel status document: malformed and inconsistent input");

            string error;

            // Malformed input must fail as a parse failure, not be mined for
            // whatever fields happen to be findable in it.
            foreach (string malformed in new[]
            {
                "{\"schema\": 1, \"state\": \"ready\"",       // truncated
                "[{\"schema\": 1, \"state\": \"ready\"}]",    // an array, not an object
                "\"just a string\"",
                "42",
                "not json at all",
                "{\"schema\": \"1\", \"state\": \"ready\"}",  // schema as text
                "{\"state\": \"ready\"}",                     // no schema at all
                "{\"schema\": 1, \"state\": \"ready\", \"token\": 12345}",   // token not text
                "{\"schema\": 1, \"state\": \"ready\", \"port\": \"8765\"}", // port as text
            })
            {
                PanelStatus refused = PanelStatus.Parse(malformed, out error);
                Check("refuses " + Show(malformed), refused == null && !string.IsNullOrEmpty(error));
            }

            // Each of the following carries a plausible-looking loopback URL,
            // so a reader that validated the URL alone would accept them. The
            // fields have to agree with each other.
            PanelStatus portMismatch = PanelStatus.Parse(
                ReadyDocument("http://127.0.0.1:8765/#token=t", "http://127.0.0.1:9999", "8765", "t", "42"),
                out error);
            Check("an origin whose port contradicts the reported port is refused",
                portMismatch == null && (error ?? "").Contains("origin"));

            PanelStatus originElsewhere = PanelStatus.Parse(
                ReadyDocument("http://127.0.0.1:8765/#token=t", "http://evil.test:8765", "8765", "t", "42"),
                out error);
            Check("an off-host origin is refused even when the url is loopback",
                originElsewhere == null);

            PanelStatus originWithPath = PanelStatus.Parse(
                ReadyDocument("http://127.0.0.1:8765/#token=t", "http://127.0.0.1:8765/api", "8765", "t", "42"),
                out error);
            Check("an origin carrying a path is refused", originWithPath == null);

            PanelStatus tokenMismatch = PanelStatus.Parse(
                ReadyDocument("http://127.0.0.1:8765/#token=somethingelse", "http://127.0.0.1:8765", "8765", "t", "42"),
                out error);
            Check("a url whose token is not the reported token is refused",
                tokenMismatch == null && (error ?? "").Contains("same session"));

            foreach (string pid in new[] { "0", "1", "-5" })
            {
                PanelStatus badPid = PanelStatus.Parse(
                    ReadyDocument("http://127.0.0.1:8765/#token=t", "http://127.0.0.1:8765", "8765", "t", pid),
                    out error);
                Check("a ready document with pid " + pid + " is refused, because that pid gets signalled",
                    badPid == null);
            }

            PanelStatus noPid = PanelStatus.Parse(
                "{\"schema\": 1, \"state\": \"ready\", \"url\": \"http://127.0.0.1:8765/#token=t\", "
                + "\"origin\": \"http://127.0.0.1:8765\", \"port\": 8765, \"token\": \"t\"}", out error);
            Check("a ready document with no pid at all is refused", noPid == null);

            foreach (string port in new[] { "0", "70000" })
            {
                PanelStatus badPort = PanelStatus.Parse(
                    ReadyDocument("http://127.0.0.1:" + port + "/#token=t",
                        "http://127.0.0.1:" + port, port, "t", "42"), out error);
                Check("a ready document with port " + port + " is refused", badPort == null);
            }

            // A consistent document still parses, so none of the above is
            // strictness that refuses the real thing.
            PanelStatus good = PanelStatus.Parse(
                ReadyDocument("http://127.0.0.1:8766/#token=abc", "http://127.0.0.1:8766", "8766", "abc", "4242"),
                out error);
            Check("a consistent ready document is still accepted",
                good != null && good.IsReady && good.Port == 8766 && good.PanelProcessId == 4242);

            Check("origin check accepts the matching loopback origin",
                PanelStatus.IsLoopbackOrigin("http://127.0.0.1:8765", 8765));
            Check("origin check rejects a mismatched port",
                !PanelStatus.IsLoopbackOrigin("http://127.0.0.1:8765", 8766));
            Check("origin check rejects credentials",
                !PanelStatus.IsLoopbackOrigin("http://user:pw@127.0.0.1:8765", 8765));
            Check("origin check rejects a look-alike host",
                !PanelStatus.IsLoopbackOrigin("http://127.0.0.1.evil.test:8765", 8765));
            Check("origin check rejects https", !PanelStatus.IsLoopbackOrigin("https://127.0.0.1:8765", 8765));
            Check("origin check rejects a fragment",
                !PanelStatus.IsLoopbackOrigin("http://127.0.0.1:8765/#token=t", 8765));
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
            string failure;
            bool enforced = Configuration.TryEnsureRuntimeDirectory(out failure);
            string path = Configuration.RuntimeDirectoryPath;
            Check("privacy is enforced on the real runtime directory", enforced && failure == null);
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

        /// <summary>
        /// What happens when the access list simply cannot be set.
        ///
        /// This used to be unsurvivable rather than merely unfortunate:
        /// enforcement reported its failure through the diagnostic log, the log
        /// lives inside the directory being secured, and writing to it ran
        /// enforcement again -- so a persistent permission failure recursed
        /// until the stack ran out, taking the process with it. The failure is
        /// injected here against a throwaway directory; the real runtime
        /// directory's permissions are never touched.
        /// </summary>
        private static void RuntimeDirectoryFailureTests()
        {
            Section("Runtime directory privacy failure");

            string sandbox = Path.Combine(
                Path.GetTempPath(), "omobench-companion-test-" + Guid.NewGuid().ToString("N"));
            string savedOverride = Configuration.RuntimeDirectoryOverride;
            Action<string> savedApplier = Configuration.AccessControlApplier;
            Func<string, int, ProcessResult> savedRunner = PanelSession.WslRunner;

            try
            {
                int applierCalls = 0;
                Configuration.RuntimeDirectoryOverride = sandbox;
                Configuration.AccessControlApplier = path =>
                {
                    applierCalls++;
                    throw new UnauthorizedAccessException("injected permission failure");
                };

                Diagnostics.WriteCount = 0;
                string failure;
                bool enforced = Configuration.TryEnsureRuntimeDirectory(out failure);
                Check("a permission failure is reported rather than swallowed",
                    !enforced && (failure ?? "").Contains("injected permission failure"));
                Check("enforcement is attempted once, and then gives up",
                    applierCalls == 1);
                Check("enforcement does not log, so it cannot log its own failure",
                    Diagnostics.WriteCount == 0);

                // The recursion proof: logging must not re-enter enforcement,
                // and one call must produce exactly one write.
                applierCalls = 0;
                Diagnostics.WriteCount = 0;
                Diagnostics.Write("a message written while enforcement is failing");
                Check("logging never triggers access-list enforcement", applierCalls == 0);
                Check("one log call writes exactly once, with no recursion",
                    Diagnostics.WriteCount == 1);

                // And the consequence that matters: nothing is launched and no
                // token document is published when privacy cannot be set.
                PanelSession.WslRunner = (arguments, timeout) =>
                {
                    throw new InvalidOperationException("no WSL command should run in this test");
                };
                int launchesBefore = PanelSession.ProcessLaunchCount;
                PanelSession session = new PanelSession();
                Configuration configuration = new Configuration
                {
                    Distribution = "Ubuntu",
                    CheckoutWindowsPath = Path.GetTempPath().TrimEnd('\\'),
                    PythonWslPath = "/home/sam/.venvs/omobench-runner/bin/python",
                    Port = 8765,
                };
                session.Start(configuration);

                Check("the panel is not started when its token cannot be kept private",
                    session.State == PanelState.Failed && !session.IsRunning);
                Check("no backend process was launched",
                    PanelSession.ProcessLaunchCount == launchesBefore);
                Check("the failure explains itself in terms a person can act on",
                    (session.Message ?? "").IndexOf("private", StringComparison.OrdinalIgnoreCase) >= 0);
                Check("no status document was published",
                    !Directory.Exists(sandbox)
                    || Directory.GetFiles(sandbox, "panel-*.json").Length == 0);
            }
            finally
            {
                Configuration.RuntimeDirectoryOverride = savedOverride;
                Configuration.AccessControlApplier = savedApplier;
                PanelSession.WslRunner = savedRunner;
                try { Directory.Delete(sandbox, true); } catch (Exception) { }
            }
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
            Check("the program is executed directly, with no shell to expand it", arguments.Contains(" --exec "));
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
                arguments.Contains("--distribution Ubuntu") && arguments.Contains(" --exec kill -TERM 4242"));
            Check("no global WSL shutdown is ever issued",
                !arguments.Contains("--shutdown") && !arguments.Contains("--terminate"));

            // Confirming a stop, and escalating to one, use the same pid rules.
            List<string> probe = PanelSession.BuildAliveProbeProgram(4242);
            Check("liveness is checked with a signal that does nothing",
                probe.Count == 3 && probe[0] == "kill" && probe[1] == "-0" && probe[2] == "4242");

            // One query answers liveness, parentage and identity, and it takes
            // no process id -- so there is no per-process query whose failure
            // could be mistaken for an answer about that process.
            List<string> table = PanelSession.BuildProcessTableProgram();
            Check("the process table is read in one query, for every process",
                table[0] == "ps" && table.Contains("-e"));
            Check("it carries parentage and start time, not just pids",
                table.Contains("pid=,ppid=,lstart="));
            Check("reading the table needs no process id to be trusted first",
                !table.Contains("-p") && !table.Contains("--ppid"));

            List<PanelSession.ProcessRecord> parsed = PanelSession.ParseProcessTable(
                "  1417     1 Mon Oct 20 13:28:57 2026\n"
                + "  2000  1417 Mon Oct 20 13:29:01 2026\n"
                + "\n"
                + "  bogus row\n");
            Check("the table parses pid, parent and start time", parsed.Count == 2);
            Check("parentage is read", parsed[1].Pid == 2000 && parsed[1].Parent == 1417);
            Check("the start time is kept whole, spaces and all",
                parsed[0].StartedAt == "Mon Oct 20 13:28:57 2026");
            Check("unparseable rows are dropped, not guessed at",
                parsed[0].Pid == 1417 && parsed.Count == 2);

            List<string> force = PanelSession.BuildForceKillProgram(new[] { 4242, 4243 });
            Check("a forced stop names every process explicitly",
                force.Count == 4 && force[0] == "kill" && force[1] == "-KILL"
                && force[2] == "4242" && force[3] == "4243");
            Check("a forced stop never takes a name, a pattern, or a process group",
                !force.Contains("-f") && !force.Contains("pkill") && !force.Contains("-1"));

            foreach (int refused in new[] { 0, 1, -1, -4242 })
            {
                bool probeRejected = false, termRejected = false, killRejected = false;
                try { PanelSession.BuildAliveProbeProgram(refused); }
                catch (ArgumentOutOfRangeException) { probeRejected = true; }
                try { PanelSession.BuildShutdownProgram(refused); }
                catch (ArgumentOutOfRangeException) { termRejected = true; }
                try { PanelSession.BuildForceKillProgram(new[] { refused }); }
                catch (ArgumentOutOfRangeException) { killRejected = true; }
                Check("a pid of " + refused + " is refused everywhere, not just by SIGTERM",
                    probeRejected && termRejected && killRejected);
            }

            bool emptyRejected = false;
            try { PanelSession.BuildForceKillProgram(new int[0]); }
            catch (ArgumentException) { emptyRejected = true; }
            Check("a forced stop with no targets is refused rather than run bare", emptyRejected);

            Check("process ids are parsed from ps output, ignoring noise",
                PanelSession.ParseProcessIds(" 2000 \n2001\n\n").Count == 2);
            Check("init is never parsed out of ps output",
                PanelSession.ParseProcessIds("1\n0\n").Count == 0);

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

        /// <summary>
        /// A stand-in for one distribution, so the decisions taken while
        /// stopping a panel can be tested exactly -- including the ones that
        /// only happen when the panel refuses to go -- without starting WSL,
        /// a panel, or a benchmark. It records every command line it is given,
        /// so a test can assert on what was *not* run as well as what was.
        /// </summary>
        private sealed class FakeDistribution
        {
            public readonly List<string> Commands = new List<string>();
            public readonly Dictionary<int, int[]> Children = new Dictionary<int, int[]>();

            /// <summary>
            /// Which processes exist, tracked individually: a single "is the
            /// panel alive" flag cannot express a parent that died while its
            /// runner survived, which is one of the cases that matters most.
            /// </summary>
            public readonly List<int> Living = new List<int>();

            public readonly List<int> IgnoresTerm = new List<int>();
            public readonly List<int> IgnoresKill = new List<int>();

            /// <summary>Alive, but `kill -0` answers EPERM instead of success.</summary>
            public readonly List<int> NotSignallable = new List<int>();

            /// <summary>Parents whose child listing cannot be answered.</summary>
            public readonly List<int> ChildListFails = new List<int>();

            public bool ProcessListingWorks = true;
            public bool SignalCommandWorks = true;
            public bool Responsive = true;

            /// <summary>
            /// Makes a query about one specific process or parent fail with
            /// exit 1 while the general listing keeps working. That is the
            /// shape a reader cannot tell from "nothing matched", and the one
            /// the 127-returning ChildListFails knob does not produce.
            /// </summary>
            public bool TargetedQueriesLie;

            /// <summary>
            /// When each process started, as the process table reports it.
            /// Identity is the pid together with this: a pid on its own is
            /// reused by the kernel and stops meaning the same process.
            /// </summary>
            public readonly Dictionary<int, string> StartedAt = new Dictionary<int, string>();

            /// <summary>Retire a process and let an unrelated one take its pid.</summary>
            public void ReusePid(int processId)
            {
                StartedAt[processId] = "Tue Oct 21 09:00:00 2026";
            }

            /// <summary>
            /// A panel that honours SIGTERM also stops the runner it started,
            /// which is why the graceful path does not need to enumerate.
            /// </summary>
            public bool TermReapsDescendants = true;

            /// <summary>
            /// Every scenario keeps one unrelated process alive. It stands in
            /// for the rest of the distribution, so `ps -e` has something to
            /// report, and it must still be there at the end of every test.
            /// </summary>
            public const int UnrelatedPid = 9999;

            public FakeDistribution(params int[] living)
            {
                Living.Add(UnrelatedPid);
                Living.AddRange(living);
                foreach (int pid in Living) { StartedAt[pid] = "Mon Oct 20 13:28:57 2026"; }
            }

            public bool IsAlive(int processId) { return Living.Contains(processId); }

            private string StartOf(int processId)
            {
                string started;
                return StartedAt.TryGetValue(processId, out started)
                    ? started : "Mon Oct 20 13:28:57 2026";
            }

            private int ParentOf(int processId)
            {
                foreach (KeyValuePair<int, int[]> entry in Children)
                {
                    foreach (int child in entry.Value)
                    {
                        if (child == processId && Living.Contains(entry.Key)) { return entry.Key; }
                    }
                }
                return 1;   // reparented to init once its parent has gone
            }

            public ProcessResult Run(string arguments, int timeoutMilliseconds)
            {
                Commands.Add(arguments);
                if (!Responsive)
                {
                    return new ProcessResult { ExitCode = -1, TimedOut = true };
                }

                string[] parts = arguments.Split(' ');
                int last;
                int.TryParse(parts[parts.Length - 1], out last);

                if (arguments.Contains(" kill -0 "))
                {
                    if (!SignalCommandWorks) { return Exit(127, "", "kill: command not found"); }
                    if (NotSignallable.Contains(last))
                    {
                        // The real kill(1) reports this with the same exit code
                        // as "no such process": the conflation under test.
                        return Exit(1, "", "kill: (" + last + ") - Operation not permitted");
                    }
                    return Living.Contains(last)
                        ? Exit(0)
                        : Exit(1, "", "kill: (" + last + ") - No such process");
                }

                if (arguments.Contains(" kill -TERM "))
                {
                    if (!SignalCommandWorks) { return Exit(127, "", "kill: command not found"); }
                    if (!Living.Contains(last)) { return Exit(1); }
                    if (!IgnoresTerm.Contains(last))
                    {
                        Living.Remove(last);
                        if (TermReapsDescendants) { Reap(last); }
                    }
                    return Exit(0);
                }

                if (arguments.Contains(" kill -KILL "))
                {
                    if (!SignalCommandWorks) { return Exit(127, "", "kill: command not found"); }
                    bool afterFlag = false;
                    foreach (string part in parts)
                    {
                        if (part == "-KILL") { afterFlag = true; continue; }
                        int target;
                        if (afterFlag && int.TryParse(part, out target) && !IgnoresKill.Contains(target))
                        {
                            Living.Remove(target);
                        }
                    }
                    return Exit(0);
                }

                if (arguments.Contains(" ps "))
                {
                    if (!ProcessListingWorks)
                    {
                        // A ps usage error exits 1 too -- indistinguishable from
                        // "nothing matched" by exit code alone.
                        return Exit(1, "", "error: unknown gnu long option");
                    }

                    // The whole table: pid, parent, and when each started.
                    if (arguments.Contains("pid=,ppid=,lstart="))
                    {
                        StringBuilder table = new StringBuilder();
                        foreach (int pid in Living)
                        {
                            table.Append("  ").Append(pid).Append("  ")
                                .Append(ParentOf(pid)).Append(" ")
                                .Append(StartOf(pid)).Append("\n");
                        }
                        return Exit(0, table.ToString());
                    }

                    if (arguments.Contains(" --ppid "))
                    {
                        if (ChildListFails.Contains(last)) { return Exit(127, "", "ps: cannot answer"); }
                        if (TargetedQueriesLie) { return Exit(1); }
                        int[] children;
                        if (!Children.TryGetValue(last, out children)) { return Exit(1); }
                        List<int> alive = new List<int>();
                        foreach (int child in children)
                        {
                            if (Living.Contains(child)) { alive.Add(child); }
                        }
                        return alive.Count == 0 ? Exit(1) : Exit(0, Lines(alive));
                    }
                    if (arguments.Contains(" -p "))
                    {
                        if (TargetedQueriesLie) { return Exit(1); }
                        return Living.Contains(last) ? Exit(0, last + "\n") : Exit(1);
                    }
                    if (arguments.EndsWith(" -e", StringComparison.Ordinal))
                    {
                        return Exit(0, Lines(Living));   // the plain "is ps working" listing
                    }
                }
                return Exit(127);
            }

            private void Reap(int processId)
            {
                int[] children;
                if (!Children.TryGetValue(processId, out children)) { return; }
                foreach (int child in children)
                {
                    Living.Remove(child);
                    Reap(child);
                }
            }

            private static string Lines(List<int> values)
            {
                StringBuilder builder = new StringBuilder();
                foreach (int value in values) { builder.Append("   ").Append(value).Append("\n"); }
                return builder.ToString();
            }

            private static ProcessResult Exit(int code, string output = "", string error = "")
            {
                return new ProcessResult { ExitCode = code, StandardOutput = output, StandardError = error };
            }

            public bool Ran(string fragment)
            {
                return Commands.Exists(c => c.Contains(fragment));
            }

            public string All { get { return string.Join(" || ", Commands.ToArray()); } }
        }

        private static void ShutdownOutcomeTests()
        {
            Section("Panel shutdown outcomes");

            Func<string, int, ProcessResult> savedRunner = PanelSession.WslRunner;
            Func<string, string, bool> savedProbe = PanelSession.OwnershipProbe;
            int savedGrace = PanelSession.GracePeriodMilliseconds;
            int savedForce = PanelSession.ForcePeriodMilliseconds;
            int savedPoll = PanelSession.LivenessPollMilliseconds;

            try
            {
                PanelSession.GracePeriodMilliseconds = 20;
                PanelSession.ForcePeriodMilliseconds = 20;
                PanelSession.LivenessPollMilliseconds = 1;

                const int PanelPid = 1417;
                const int UnrelatedPid = FakeDistribution.UnrelatedPid;
                string report;

                // 1. The ordinary case: the panel honours SIGTERM.
                FakeDistribution polite = new FakeDistribution(PanelPid);
                PanelSession.WslRunner = polite.Run;
                PanelSession.OwnershipProbe = (origin, token) => true;
                ShutdownOutcome graceful = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a panel that honours SIGTERM is confirmed stopped",
                    graceful == ShutdownOutcome.Stopped && report == null);
                Check("it was asked, not killed", polite.Ran(" --exec kill -TERM 1417") && !polite.Ran("kill -KILL"));
                Check("its departure was actually verified", polite.Ran(" --exec kill -0 1417"));

                // 2. An unresponsive panel: escalation stays inside the tree
                //    rooted at the process proved to be ours.
                FakeDistribution stubborn = new FakeDistribution(PanelPid, 2000, 2001);
                stubborn.IgnoresTerm.Add(PanelPid);
                stubborn.Children[PanelPid] = new[] { 2000 };
                stubborn.Children[2000] = new[] { 2001 };
                PanelSession.WslRunner = stubborn.Run;
                ShutdownOutcome escalated = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("an unresponsive panel is escalated to and confirmed stopped",
                    escalated == ShutdownOutcome.Stopped);
                Check("SIGTERM was tried before SIGKILL", stubborn.Ran(" --exec kill -TERM 1417"));
                Check("the panel and its own descendants are force-stopped together",
                    stubborn.Ran("kill -KILL 1417 2000 2001"));
                Check("an unrelated process is never named: " + stubborn.All,
                    !stubborn.All.Contains(UnrelatedPid.ToString()));

                // 3. Force-stop that does not work is reported honestly.
                FakeDistribution immortal = new FakeDistribution(PanelPid);
                immortal.IgnoresTerm.Add(PanelPid);
                immortal.IgnoresKill.Add(PanelPid);
                immortal.Children[PanelPid] = new int[0];
                PanelSession.WslRunner = immortal.Run;
                ShutdownOutcome unconfirmed = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a panel that survives everything is not reported as stopped",
                    unconfirmed == ShutdownOutcome.NotConfirmed);
                Check("the report names what is still there",
                    (report ?? "").Contains("could not be confirmed stopped")
                    && (report ?? "").Contains("1417 (the panel)"));

                // 4. If the process list cannot be read, nothing is killed on
                //    a guess.
                FakeDistribution blind = new FakeDistribution(PanelPid) { ProcessListingWorks = false };
                blind.IgnoresTerm.Add(PanelPid);
                PanelSession.WslRunner = blind.Run;
                ShutdownOutcome cannotEnumerate = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("an unlistable process tree is reported, not guessed at",
                    cannotEnumerate == ShutdownOutcome.NotConfirmed);
                Check("nothing was force-stopped without knowing what it is",
                    !blind.Ran("kill -KILL"));

                // 5. Ownership unproven, panel alive: strictly hands off.
                FakeDistribution unproven = new FakeDistribution(PanelPid);
                PanelSession.WslRunner = unproven.Run;
                PanelSession.OwnershipProbe = (origin, token) => false;
                ShutdownOutcome handsOff = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a panel that cannot be proved ours is left alone",
                    handsOff == ShutdownOutcome.NotConfirmed);
                Check("no signal was sent to it",
                    !unproven.Ran("kill -TERM") && !unproven.Ran("kill -KILL"));
                Check("and the report says why", (report ?? "").Contains("did not confirm"));

                // 6. Ownership unproven because it already exited: that is a
                //    stop, and it is confirmed by asking.
                FakeDistribution departed = new FakeDistribution();   // the panel pid is not alive
                PanelSession.WslRunner = departed.Run;
                ShutdownOutcome alreadyGone = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a panel that has already exited counts as stopped",
                    alreadyGone == ShutdownOutcome.Stopped);

                // 7. Retry: the same session can be told to stop again, and the
                //    second attempt succeeds once the panel responds.
                FakeDistribution retried = new FakeDistribution(PanelPid);
                retried.IgnoresTerm.Add(PanelPid);
                retried.IgnoresKill.Add(PanelPid);
                retried.Children[PanelPid] = new int[0];
                PanelSession.WslRunner = retried.Run;
                PanelSession.OwnershipProbe = (origin, token) => true;
                PanelSession session = new PanelSession();
                ShutdownOutcome first = session.ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                retried.IgnoresTerm.Clear();
                retried.IgnoresKill.Clear();
                ShutdownOutcome second = session.ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a failed stop can be retried on the same session",
                    first == ShutdownOutcome.NotConfirmed && second == ShutdownOutcome.Stopped);

                // 8. Across every one of those scenarios, the distribution
                //    itself is never touched.
                foreach (FakeDistribution distribution in new[]
                    { polite, stubborn, immortal, blind, unproven, departed, retried })
                {
                    Check("no global WSL shutdown or terminate was ever issued",
                        !distribution.All.Contains("--shutdown") && !distribution.All.Contains("--terminate"));
                    Check("the unrelated process survived",
                        distribution.IsAlive(UnrelatedPid));
                }

                // A missing pid is a refusal, not a broader signal.
                ShutdownOutcome noPid = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", 0, "http://127.0.0.1:8765", "t", out report);
                Check("without an in-distribution pid, nothing is stopped",
                    noPid == ShutdownOutcome.NotConfirmed);
            }
            finally
            {
                PanelSession.WslRunner = savedRunner;
                PanelSession.OwnershipProbe = savedProbe;
                PanelSession.GracePeriodMilliseconds = savedGrace;
                PanelSession.ForcePeriodMilliseconds = savedForce;
                PanelSession.LivenessPollMilliseconds = savedPoll;
            }
        }

        /// <summary>
        /// The difference between "confirmed gone" and "could not tell", and
        /// between a fully walked process tree and a partly walked one.
        ///
        /// Every case here was a wrong answer before the fix: a process that
        /// exists but cannot be signalled, a probe that could not run at all,
        /// a tree deeper than the walk, a child listing that failed after the
        /// capability probe succeeded, and a parent that died while its child
        /// lived on. Each produced "stopped".
        /// </summary>
        private static void ShutdownRobustnessTests()
        {
            Section("Shutdown: confirmed absence vs failed observation");

            Func<string, int, ProcessResult> savedRunner = PanelSession.WslRunner;
            Func<string, string, bool> savedProbe = PanelSession.OwnershipProbe;
            int savedGrace = PanelSession.GracePeriodMilliseconds;
            int savedForce = PanelSession.ForcePeriodMilliseconds;
            int savedPoll = PanelSession.LivenessPollMilliseconds;

            try
            {
                PanelSession.GracePeriodMilliseconds = 20;
                PanelSession.ForcePeriodMilliseconds = 20;
                PanelSession.LivenessPollMilliseconds = 1;

                const int PanelPid = 1417;
                const int UnrelatedPid = FakeDistribution.UnrelatedPid;
                string report;

                // 1. Alive, but not signallable by us. The real kill(1) reports
                //    EPERM with the same exit code as ESRCH, so an exit code
                //    alone must never be read as "the process is gone".
                FakeDistribution denied = new FakeDistribution(PanelPid);
                denied.NotSignallable.Add(PanelPid);
                PanelSession.WslRunner = denied.Run;
                PanelSession.OwnershipProbe = (origin, token) => false;
                ShutdownOutcome permissionDenied = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a process that exists but cannot be signalled is not reported stopped",
                    permissionDenied == ShutdownOutcome.NotConfirmed);
                Check("the process table was consulted, not just the signal",
                    denied.Ran("pid=,ppid=,lstart="));

                // 2. The liveness question could not be asked at all.
                FakeDistribution mute = new FakeDistribution(PanelPid)
                {
                    SignalCommandWorks = false,
                    ProcessListingWorks = false,
                };
                PanelSession.WslRunner = mute.Run;
                ShutdownOutcome unanswerable = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("an unanswerable liveness check is not reported stopped",
                    unanswerable == ShutdownOutcome.NotConfirmed);

                // 3. Everything times out: still not a stop.
                FakeDistribution frozen = new FakeDistribution(PanelPid) { Responsive = false };
                PanelSession.WslRunner = frozen.Run;
                ShutdownOutcome timedOut = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a timed-out distribution is not reported stopped",
                    timedOut == ShutdownOutcome.NotConfirmed);

                // 4. A tree deeper than the walk must not be reported complete.
                PanelSession.OwnershipProbe = (origin, token) => true;
                FakeDistribution deep = new FakeDistribution(PanelPid, 2000, 2001, 2002, 2003, 2004);
                deep.IgnoresTerm.Add(PanelPid);
                deep.Children[PanelPid] = new[] { 2000 };
                deep.Children[2000] = new[] { 2001 };
                deep.Children[2001] = new[] { 2002 };
                deep.Children[2002] = new[] { 2003 };
                deep.Children[2003] = new[] { 2004 };
                PanelSession.WslRunner = deep.Run;
                ShutdownOutcome tooDeep = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a tree deeper than the walk is refused, not half-killed",
                    tooDeep == ShutdownOutcome.NotConfirmed);
                Check("nothing was force-stopped from an incomplete tree",
                    !deep.Ran("kill -KILL"));
                Check("the deepest descendant is still alive, having never been named",
                    deep.IsAlive(2004));

                // 5. A per-parent child listing that fails partway can no
                //    longer influence anything, because the tree comes from one
                //    validated reading of the whole table rather than a query
                //    per parent. The grandchild is found and stopped regardless.
                FakeDistribution flaky = new FakeDistribution(PanelPid, 2000, 2001);
                flaky.IgnoresTerm.Add(PanelPid);
                flaky.Children[PanelPid] = new[] { 2000 };
                flaky.Children[2000] = new[] { 2001 };
                flaky.ChildListFails.Add(2000);
                flaky.TargetedQueriesLie = true;
                PanelSession.WslRunner = flaky.Run;
                ShutdownOutcome partial = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a failing per-parent listing no longer decides anything",
                    partial == ShutdownOutcome.Stopped);
                Check("the whole tree was found from the table and stopped",
                    flaky.Ran("kill -KILL 1417 2000 2001"));
                Check("the grandchild was not left behind",
                    !flaky.IsAlive(2001));
                Check("no per-parent child query was relied on",
                    !flaky.Ran("--ppid"));

                // 6. The parent dies but a known child survives the force-stop.
                FakeDistribution orphaning = new FakeDistribution(PanelPid, 2000);
                orphaning.IgnoresTerm.Add(PanelPid);
                orphaning.IgnoresKill.Add(2000);
                orphaning.Children[PanelPid] = new[] { 2000 };
                PanelSession.WslRunner = orphaning.Run;
                PanelSession survivorSession = new PanelSession();
                ShutdownOutcome childLived = survivorSession.ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a surviving child means the stop is not confirmed",
                    childLived == ShutdownOutcome.NotConfirmed);
                Check("the parent did go", !orphaning.IsAlive(PanelPid));
                Check("the surviving child is named in the report: " + report,
                    (report ?? "").Contains("2000"));

                // ...and the retry still knows about it, even though the panel
                // it descended from is now gone and cannot prove ownership.
                orphaning.IgnoresKill.Clear();
                ShutdownOutcome retryAfterOrphan = survivorSession.ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a retry finishes the descendant the first attempt could not",
                    retryAfterOrphan == ShutdownOutcome.Stopped && !orphaning.IsAlive(2000));

                // 7. The public path: the Windows wrapper has exited, but the
                //    panel is still alive inside the distribution. Stop must not
                //    call that stopped, must keep the status document, and must
                //    leave a usable retry control.
                string statusFile = Path.Combine(
                    Path.GetTempPath(), "omobench-status-test-" + Guid.NewGuid().ToString("N") + ".json");
                File.WriteAllText(statusFile, "{}");
                Process exited = Process.Start(new ProcessStartInfo
                {
                    FileName = "cmd.exe",
                    Arguments = "/c exit 0",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                });
                exited.WaitForExit();

                FakeDistribution survivingPanel = new FakeDistribution(PanelPid);
                survivingPanel.IgnoresTerm.Add(PanelPid);
                survivingPanel.IgnoresKill.Add(PanelPid);
                survivingPanel.Children[PanelPid] = new int[0];
                PanelSession.WslRunner = survivingPanel.Run;

                PanelSession wrapperGone = new PanelSession();
                wrapperGone.AdoptForTest(exited, statusFile, "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t");
                Check("the wrapper really has exited", !wrapperGone.IsRunning);

                ShutdownOutcome wrapperOutcome = wrapperGone.Stop();
                Check("an exited wsl.exe is not taken as proof the panel stopped",
                    wrapperOutcome == ShutdownOutcome.NotConfirmed);
                Check("the window says so rather than claiming a stop",
                    wrapperGone.State == PanelState.StopFailed);
                Check("the status document is kept, because the panel it describes is still there",
                    File.Exists(statusFile));
                Check("Stop stays available even though the wrapper is gone",
                    wrapperGone.OwnsUnconfirmedPanel);
                Check("the panel was actually signalled, not just inspected",
                    survivingPanel.Ran(" --exec kill -TERM 1417"));

                // And once it does go, the same session confirms and tidies up.
                survivingPanel.IgnoresTerm.Clear();
                survivingPanel.IgnoresKill.Clear();
                ShutdownOutcome eventually = wrapperGone.Stop();
                Check("a later Stop on the same session confirms the panel gone",
                    eventually == ShutdownOutcome.Stopped && wrapperGone.State == PanelState.Stopped);
                Check("only then is the token document removed", !File.Exists(statusFile));
                Check("and ownership is released", !wrapperGone.OwnsUnconfirmedPanel);
                try { File.Delete(statusFile); } catch (Exception) { }

                foreach (FakeDistribution distribution in new[]
                    { denied, mute, frozen, deep, flaky, orphaning, survivingPanel })
                {
                    Check("the unrelated process survived, and was never named",
                        distribution.IsAlive(UnrelatedPid)
                        && !distribution.All.Contains(UnrelatedPid.ToString()));
                    Check("the distribution itself was never shut down",
                        !distribution.All.Contains("--shutdown")
                        && !distribution.All.Contains("--terminate"));
                }
            }
            finally
            {
                PanelSession.WslRunner = savedRunner;
                PanelSession.OwnershipProbe = savedProbe;
                PanelSession.GracePeriodMilliseconds = savedGrace;
                PanelSession.ForcePeriodMilliseconds = savedForce;
                PanelSession.LivenessPollMilliseconds = savedPoll;
            }
        }

        /// <summary>
        /// Three ways the recovery path could still reach a wrong answer: a
        /// targeted query that fails while the general listing works, a
        /// remembered pid that no longer means the same process, and a new
        /// launch quietly replacing a session whose fate is still unresolved.
        /// </summary>
        private static void RecoveryCorrectnessTests()
        {
            Section("Recovery: validated observation, identity, and unresolved sessions");

            Func<string, int, ProcessResult> savedRunner = PanelSession.WslRunner;
            Func<string, string, bool> savedProbe = PanelSession.OwnershipProbe;
            int savedGrace = PanelSession.GracePeriodMilliseconds;
            int savedForce = PanelSession.ForcePeriodMilliseconds;
            int savedPoll = PanelSession.LivenessPollMilliseconds;

            try
            {
                PanelSession.GracePeriodMilliseconds = 20;
                PanelSession.ForcePeriodMilliseconds = 20;
                PanelSession.LivenessPollMilliseconds = 1;

                const int PanelPid = 1417;
                const int ChildPid = 2000;
                const int UnrelatedPid = FakeDistribution.UnrelatedPid;
                string report;

                // -- 1. A targeted query failing is not an answer --------------

                // Liveness: the panel is alive, but `ps -p` fails with exit 1
                // while `ps -e` works. A reader that settles exit 1 by asking
                // the general listing concludes the panel is gone.
                FakeDistribution lyingAboutPanel = new FakeDistribution(PanelPid)
                {
                    TargetedQueriesLie = true,
                };
                lyingAboutPanel.NotSignallable.Add(PanelPid);   // force the listing path
                PanelSession.WslRunner = lyingAboutPanel.Run;
                PanelSession.OwnershipProbe = (origin, token) => false;
                ShutdownOutcome lyingLiveness = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a failed targeted liveness query is not read as absence",
                    lyingLiveness == ShutdownOutcome.NotConfirmed);
                Check("the panel that was never confirmed gone is still alive",
                    lyingAboutPanel.IsAlive(PanelPid));

                // Child enumeration: same failure shape, with a real child.
                FakeDistribution lyingAboutChildren = new FakeDistribution(PanelPid, ChildPid)
                {
                    TargetedQueriesLie = true,
                };
                lyingAboutChildren.IgnoresTerm.Add(PanelPid);
                lyingAboutChildren.Children[PanelPid] = new[] { ChildPid };
                PanelSession.WslRunner = lyingAboutChildren.Run;
                PanelSession.OwnershipProbe = (origin, token) => true;
                ShutdownOutcome lyingChildren = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a failed child query does not make a child-bearing panel childless",
                    lyingChildren == ShutdownOutcome.Stopped || !lyingAboutChildren.IsAlive(ChildPid));
                Check("the child was not left running while the parent was stopped",
                    !lyingAboutChildren.IsAlive(ChildPid));

                // -- 2. A remembered pid is not an identity --------------------

                // The child outlives the first force-stop, so it is remembered.
                FakeDistribution reuse = new FakeDistribution(PanelPid, ChildPid);
                reuse.IgnoresTerm.Add(PanelPid);
                reuse.IgnoresKill.Add(ChildPid);
                reuse.Children[PanelPid] = new[] { ChildPid };
                PanelSession.WslRunner = reuse.Run;
                PanelSession reuseSession = new PanelSession();
                ShutdownOutcome firstPass = reuseSession.ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("the surviving child is remembered for a retry",
                    firstPass == ShutdownOutcome.NotConfirmed);

                // Between attempts the real child exits and something unrelated
                // is given its pid. The retry must not shoot the newcomer.
                reuse.IgnoresKill.Clear();
                reuse.ReusePid(ChildPid);
                ShutdownOutcome afterReuse = reuseSession.ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("the process now holding the reused pid survives the retry",
                    reuse.IsAlive(ChildPid));
                Check("and the retry does not claim to have stopped it",
                    afterReuse == ShutdownOutcome.Stopped || afterReuse == ShutdownOutcome.NotConfirmed);
                Check("no SIGKILL was aimed at the reused pid after it changed hands",
                    CountOccurrences(reuse.All, "kill -KILL " + ChildPid) <= 1);

                // -- 3. An unresolved session is not replaceable ---------------

                string statusFile = Path.Combine(
                    Path.GetTempPath(), "omobench-unresolved-" + Guid.NewGuid().ToString("N") + ".json");
                File.WriteAllText(statusFile, "{}");
                Process exited = Process.Start(new ProcessStartInfo
                {
                    FileName = "cmd.exe",
                    Arguments = "/c exit 0",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                });
                exited.WaitForExit();

                FakeDistribution unresolved = new FakeDistribution(PanelPid);
                unresolved.IgnoresTerm.Add(PanelPid);
                unresolved.IgnoresKill.Add(PanelPid);
                PanelSession.WslRunner = unresolved.Run;

                PanelSession stranded = new PanelSession();
                stranded.AdoptForTest(exited, statusFile, "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t");
                ShutdownOutcome strandedOutcome = stranded.Stop();
                Check("the stop is unresolved to begin with",
                    strandedOutcome == ShutdownOutcome.NotConfirmed && stranded.OwnsUnconfirmedPanel);

                // Open panel now, with Setup pointing somewhere else entirely.
                int launchesBefore = PanelSession.ProcessLaunchCount;
                Configuration elsewhere = new Configuration
                {
                    Distribution = "AnotherDistro",
                    CheckoutWindowsPath = Path.GetTempPath().TrimEnd('\\'),
                    PythonWslPath = "/home/sam/.venvs/other/bin/python",
                    Port = 9100,
                };
                stranded.Start(elsewhere);

                Check("no second panel is launched over an unresolved one",
                    PanelSession.ProcessLaunchCount == launchesBefore);
                Check("the original ownership is intact, not overwritten by the new Setup",
                    stranded.OwnsUnconfirmedPanel && stranded.OwnedDistributionForTest == "Ubuntu"
                    && stranded.OwnedPanelProcessIdForTest == PanelPid);
                Check("the original status document is still the one being tracked",
                    File.Exists(statusFile));
                Check("and the recovery control is still usable",
                    stranded.OwnsUnconfirmedPanel);

                // Once the original is finally confirmed gone, starting works.
                unresolved.IgnoresTerm.Clear();
                unresolved.IgnoresKill.Clear();
                Check("the original can still be stopped afterwards",
                    stranded.Stop() == ShutdownOutcome.Stopped);
                Check("and ownership is then released for a fresh start",
                    !stranded.OwnsUnconfirmedPanel);
                try { File.Delete(statusFile); } catch (Exception) { }

                foreach (FakeDistribution distribution in new[]
                    { lyingAboutPanel, lyingAboutChildren, reuse, unresolved })
                {
                    Check("the unrelated process survived",
                        distribution.IsAlive(UnrelatedPid)
                        && !distribution.All.Contains(UnrelatedPid.ToString()));
                    Check("the distribution itself was never shut down",
                        !distribution.All.Contains("--shutdown")
                        && !distribution.All.Contains("--terminate"));
                }
            }
            finally
            {
                PanelSession.WslRunner = savedRunner;
                PanelSession.OwnershipProbe = savedProbe;
                PanelSession.GracePeriodMilliseconds = savedGrace;
                PanelSession.ForcePeriodMilliseconds = savedForce;
                PanelSession.LivenessPollMilliseconds = savedPoll;
            }
        }

        private static int CountOccurrences(string haystack, string needle)
        {
            int count = 0, index = 0;
            while ((index = haystack.IndexOf(needle, index, StringComparison.Ordinal)) >= 0)
            {
                count++;
                index += needle.Length;
            }
            return count;
        }

        // -- "is a benchmark running?" is a three-way answer --------------------

        private static void ActivityCheckTests()
        {
            Section("Active-run detection");

            bool active;
            Check("a running job is found in the listing",
                PanelSession.TryFindActiveStatus(
                    "[{\"id\": \"job-1\", \"status\": \"running\"}]", out active) && active);
            Check("a submission step counts as active",
                PanelSession.TryFindActiveStatus(
                    "[{\"id\": \"job-1\", \"status\": \"submitting\"}]", out active) && active);
            Check("a nested job object is still found",
                PanelSession.TryFindActiveStatus(
                    "{\"runs\": [{\"status\": \"packaging\"}]}", out active) && active);
            Check("a finished job is not active",
                PanelSession.TryFindActiveStatus(
                    "[{\"id\": \"job-1\", \"status\": \"completed\"}]", out active) && !active);
            Check("an empty listing is not active",
                PanelSession.TryFindActiveStatus("[]", out active) && !active);

            // Unreadable is not the same as idle: the caller turns a false
            // return into Unknown, which still warns before stopping.
            Check("an unreadable answer is not reported as idle",
                !PanelSession.TryFindActiveStatus("{\"runs\": [", out active));
            Check("a non-JSON answer is not reported as idle",
                !PanelSession.TryFindActiveStatus("<html>login</html>", out active));

            // With no connection details there is nothing to ask, and that is
            // Unknown rather than "nothing is running".
            string description;
            PanelActivity unknown = new PanelSession().CheckActivity(out description);
            Check("a session with no panel reports Unknown, not Idle",
                unknown == PanelActivity.Unknown && !string.IsNullOrEmpty(description));
        }

        /// <summary>
        /// What arguments actually arrive inside the distribution.
        ///
        /// This is the one thing no amount of string-checking on the Windows
        /// side can establish, so it is measured: each argument is sent
        /// through the real boundary and printed back by the receiving
        /// program, one per line, and compared with what was sent.
        ///
        /// It runs `printf` and nothing else -- no panel, no benchmark, no
        /// checkout -- and skips entirely when WSL is not available.
        /// </summary>
        private static void ArgumentDeliveryTests()
        {
            Section("Windows -> WSL argument delivery (real boundary)");

            if (!Wsl.IsAvailable)
            {
                Console.WriteLine("  skipped: wsl.exe is not available on this machine");
                return;
            }
            List<WslDistribution> distributions = Wsl.ListDistributions();
            string distribution = null;
            foreach (WslDistribution candidate in distributions)
            {
                if (candidate.Version == 2) { distribution = candidate.Name; break; }
            }
            if (distribution == null)
            {
                Console.WriteLine("  skipped: no WSL2 distribution to test against");
                return;
            }

            // Every one of these is mangled, expanded or split by a shell.
            string[] awkward =
            {
                "plain",
                "with spaces",
                "$HOME",
                "`whoami`",
                "$(whoami)",
                "a;b",
                "a&b",
                "a|b",
                "*",
                "quote\"inside",
                "single'quote",
                "back\\slash",
                "new\tline",
                "/mnt/c/Users/Sam Smith/Code/omo$bench/cli",
            };

            List<string> program = new List<string> { "/usr/bin/printf", "%s\\n" };
            program.AddRange(awkward);
            string arguments = Wsl.BuildRunArguments(distribution, null, program);

            Check("the invocation executes directly, with no shell in the chain",
                arguments.Contains(" --exec ") && !arguments.Contains(" -- "));

            ProcessResult result = Wsl.Run(Wsl.ExecutablePath, arguments, 30000);
            if (result.TimedOut)
            {
                Check("printf answered", false);
                return;
            }

            string[] received = (result.StandardOutput ?? "").Replace("\r", "").Split('\n');
            int matched = 0;
            for (int index = 0; index < awkward.Length; index++)
            {
                string expected = awkward[index];
                bool ok = index < received.Length && received[index] == expected;
                if (ok) { matched++; }
                Check("arrives unchanged: " + Show(expected), ok);
            }
            Check("every argument survived the boundary intact",
                matched == awkward.Length);
        }

        // -- finding the checkout ----------------------------------------------

        /// <summary>Build a directory that really is an OmoBench checkout.</summary>
        private static string MakeCheckout(string path)
        {
            Directory.CreateDirectory(Path.Combine(path, "cli", "src", "omobench"));
            Directory.CreateDirectory(Path.Combine(path, "benchmarks", "profiles"));
            File.WriteAllText(Path.Combine(path, "cli", "src", "omobench", "cli.py"), "# fixture\n");
            File.WriteAllText(Path.Combine(path, "benchmarks", "profiles", "chat-small-v1.yaml"), "name: fixture\n");
            return path;
        }

        private static string Sandbox()
        {
            string path = Path.Combine(Path.GetTempPath(), "omobench-discovery-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(path);
            return path;
        }

        private static void Discard(string path)
        {
            try { Directory.Delete(path, true); } catch (Exception) { }
        }

        private static void CheckoutDiscoveryTests()
        {
            Section("Discovery: finding the checkout");

            string sandbox = Sandbox();
            try
            {
                // A checkout is recognised by its files, not by its name.
                string real = MakeCheckout(Path.Combine(sandbox, "some-folder-name"));
                Check("a directory with the OmoBench files is a checkout",
                    Discovery.LooksLikeCheckout(real));

                string impostor = Path.Combine(sandbox, "omobench");
                Directory.CreateDirectory(impostor);
                File.WriteAllText(Path.Combine(impostor, "omobench-local-starter-v2.tar.gz"), "not a checkout");
                Check("a folder merely named omobench is not a checkout",
                    !Discovery.LooksLikeCheckout(impostor));

                string halfway = Path.Combine(sandbox, "halfway");
                Directory.CreateDirectory(Path.Combine(halfway, "cli", "src", "omobench"));
                File.WriteAllText(Path.Combine(halfway, "cli", "src", "omobench", "cli.py"), "#");
                Check("a checkout without benchmark profiles is not accepted",
                    !Discovery.LooksLikeCheckout(halfway));

                Check("a path that does not exist is not a checkout",
                    !Discovery.LooksLikeCheckout(Path.Combine(sandbox, "nowhere")));
                Check("null is handled", !Discovery.LooksLikeCheckout(null));

                // The common case: the companion is built inside the checkout,
                // so walking up from the executable finds it with no search --
                // including when the path contains spaces and metacharacters.
                string awkward = MakeCheckout(Path.Combine(sandbox, "My Projects", "omo$bench & co"));
                string exe = Path.Combine(awkward, "windows", "companion", "build", "OmoBenchCompanion.exe");
                Directory.CreateDirectory(Path.GetDirectoryName(exe));
                List<CheckoutCandidate> fromExe = Discovery.CheckoutsFromExecutable(exe);
                Check("the checkout is found from the executable's ancestors",
                    fromExe.Count == 1 && fromExe[0].WindowsPath == awkward);
                Check("a path with spaces and metacharacters survives: " + awkward,
                    fromExe.Count == 1 && fromExe[0].WindowsPath.Contains(" ")
                    && fromExe[0].WindowsPath.Contains("$") && fromExe[0].WindowsPath.Contains("&"));

                List<CheckoutCandidate> fromStray = Discovery.CheckoutsFromExecutable(
                    Path.Combine(sandbox, "elsewhere", "OmoBenchCompanion.exe"));
                Check("an executable outside any checkout finds nothing", fromStray.Count == 0);

                // Bounded search: depth, skipped directories, and unreadable
                // roots that must not abort the whole search.
                string roots = Path.Combine(sandbox, "search");
                string shallow = MakeCheckout(Path.Combine(roots, "a", "shallow-checkout"));
                string deep = MakeCheckout(Path.Combine(roots, "a", "b", "c", "d", "e", "deep-checkout"));
                string hidden = MakeCheckout(Path.Combine(roots, "a", "node_modules", "ignored-checkout"));

                DiscoveryLimits limits = new DiscoveryLimits();
                limits.MaximumDirectoryDepth = 3;
                DateTime far = DateTime.UtcNow.AddMinutes(5);
                List<CheckoutCandidate> found = Discovery.SearchForCheckouts(
                    new[] { roots, Path.Combine(sandbox, "does-not-exist") }, limits, null, far);

                bool sawShallow = false, sawDeep = false, sawHidden = false;
                foreach (CheckoutCandidate candidate in found)
                {
                    if (candidate.WindowsPath == shallow) { sawShallow = true; }
                    if (candidate.WindowsPath == deep) { sawDeep = true; }
                    if (candidate.WindowsPath == hidden) { sawHidden = true; }
                }
                Check("a checkout within the depth limit is found", sawShallow);
                Check("a checkout beyond the depth limit is not", !sawDeep);
                Check("node_modules is never descended into", !sawHidden);
                Check("an unreadable or missing root does not abort the search", sawShallow);

                limits.MaximumDirectoriesVisited = 1;
                List<CheckoutCandidate> capped = Discovery.SearchForCheckouts(
                    new[] { roots }, limits, null, far);
                Check("the number of directories examined is capped", capped.Count <= 1);

                limits = new DiscoveryLimits();
                DiscoveryCancel cancelled = new DiscoveryCancel();
                cancelled.Cancel();
                Check("a cancelled search stops immediately",
                    Discovery.SearchForCheckouts(new[] { roots }, limits, cancelled, far).Count == 0);
                Check("an elapsed deadline stops the search",
                    Discovery.SearchForCheckouts(new[] { roots }, limits, null,
                        DateTime.UtcNow.AddSeconds(-1)).Count == 0);

                // The checkout-location contract is unchanged: WSL must be able
                // to reach it as /mnt/<drive>.
                Configuration saved = new Configuration { CheckoutWindowsPath = @"\\server\share\omobench" };
                List<CheckoutCandidate> reachable = Discovery.FindCheckouts(
                    saved, exe, new DiscoveryLimits(), null, far);
                foreach (CheckoutCandidate candidate in reachable)
                {
                    Check("every candidate is reachable from WSL: " + candidate.WindowsPath,
                        !string.IsNullOrEmpty(candidate.WslPath)
                        && candidate.WslPath.StartsWith("/mnt/", StringComparison.Ordinal));
                }
            }
            finally
            {
                Discard(sandbox);
            }
        }

        // -- finding the Python environment -------------------------------------

        private static void InterpreterDiscoveryTests()
        {
            Section("Discovery: finding the Python environment");

            Check("an absolute Linux interpreter is plausible",
                Discovery.IsPlausibleLinuxInterpreter("/home/sam/.venvs/omobench-runner/bin/python"));
            Check("a relative path is refused", !Discovery.IsPlausibleLinuxInterpreter("python3"));
            Check("an empty value is refused", !Discovery.IsPlausibleLinuxInterpreter(""));

            // A Windows virtual environment reached through /mnt is not the
            // Linux runner environment, however OmoBench-looking its path.
            Check("a Windows interpreter is refused",
                !Discovery.IsPlausibleLinuxInterpreter("/mnt/c/Code/omobench/.venv/Scripts/python.exe"));
            Check("a Windows interpreter is refused even without the extension",
                !Discovery.IsPlausibleLinuxInterpreter("/mnt/c/Code/omobench/.venv/Scripts/python"));
            Check("any .exe is refused",
                !Discovery.IsPlausibleLinuxInterpreter("/mnt/c/Python312/python.exe"));

            DiscoveryLimits limits = new DiscoveryLimits();
            List<InterpreterCandidate> candidates = Discovery.BuildInterpreterCandidates(
                "/home/linuxuser/.venvs/saved-env/bin/python",
                "/home/linuxuser",
                new[]
                {
                    "/home/linuxuser/.venvs/omobench/bin/python",
                    "/home/linuxuser/.venvs/omobench-runner/bin/python",
                },
                new[] { "/mnt/c/Code/omobench" },
                "/usr/bin/python3",
                limits);

            Check("the saved interpreter is tried first",
                candidates.Count > 0 && candidates[0].WslPath == "/home/linuxuser/.venvs/saved-env/bin/python");
            Check("the conventional environment is included",
                Contains(candidates, "/home/linuxuser/.venvs/omobench-runner/bin/python"));
            Check("other environments under the home directory are included",
                Contains(candidates, "/home/linuxuser/.venvs/omobench/bin/python"));
            Check("a Linux environment inside the checkout is included",
                Contains(candidates, "/mnt/c/Code/omobench/.venv/bin/python"));
            Check("the interpreter on PATH is included",
                Contains(candidates, "/usr/bin/python3"));

            // The identity point: a virtual environment is named by its own
            // bin/python. Resolving that symlink would make every environment
            // look like /usr/bin/python3 and pick the wrong one.
            Check("virtual environments keep their own interpreter path, not the system one",
                Contains(candidates, "/home/linuxuser/.venvs/omobench/bin/python")
                && Contains(candidates, "/home/linuxuser/.venvs/omobench-runner/bin/python")
                && candidates[0].WslPath != "/usr/bin/python3");

            int duplicates = 0;
            foreach (InterpreterCandidate candidate in candidates)
            {
                if (candidate.WslPath == "/usr/bin/python3") { duplicates++; }
            }
            Check("candidates are de-duplicated", duplicates == 1);

            List<InterpreterCandidate> withWindows = Discovery.BuildInterpreterCandidates(
                "/mnt/c/Code/omobench/.venv/Scripts/python.exe", "/home/linuxuser",
                null, null, null, limits);
            Check("a saved Windows interpreter is dropped rather than offered",
                !Contains(withWindows, "/mnt/c/Code/omobench/.venv/Scripts/python.exe"));

            limits.MaximumInterpreters = 2;
            List<InterpreterCandidate> capped = Discovery.BuildInterpreterCandidates(
                "/home/linuxuser/.venvs/a/bin/python", "/home/linuxuser",
                new[] { "/home/linuxuser/.venvs/b/bin/python", "/home/linuxuser/.venvs/c/bin/python" },
                null, "/usr/bin/python3", limits);
            Check("the number of interpreters probed is capped", capped.Count == 2);

            // No shell, in any of the programs discovery runs.
            Check("the home directory is read without a shell",
                Discovery.BuildHomeProgram()[0] == "printenv");
            Check("directory listings use ls directly",
                Discovery.BuildListProgram("/home/x/.venvs")[0] == "ls");
            string arguments = Wsl.BuildRunArguments(
                "Ubuntu", null, Discovery.BuildListProgram("/home/sam/my envs"));
            Check("a listing path with spaces is quoted, not split",
                arguments.Contains("\"/home/sam/my envs\""));
            Check("discovery never builds a shell command",
                !arguments.Contains("bash") && !arguments.Contains("sh -c")
                && arguments.Contains(" --exec "));
        }

        private static bool Contains(List<InterpreterCandidate> candidates, string path)
        {
            foreach (InterpreterCandidate candidate in candidates)
            {
                if (candidate.WslPath == path) { return true; }
            }
            return false;
        }

        // -- the whole search, against a stand-in distribution -------------------

        /// <summary>
        /// A distribution that answers the handful of questions discovery
        /// asks. No WSL, no panel, no benchmark.
        /// </summary>
        private sealed class FakeWsl
        {
            public readonly List<string> Commands = new List<string>();
            public string Home = "/home/linuxuser";
            public string PathPython = "/usr/bin/python3";
            public readonly Dictionary<string, string[]> Listings = new Dictionary<string, string[]>();
            public readonly Dictionary<string, string> Probes = new Dictionary<string, string>();
            public bool HomeUnavailable;
            public bool ProbesTimeOut;

            public ProcessResult Run(string arguments, int timeout, DiscoveryCancel cancel)
            {
                Commands.Add(arguments);
                if (cancel != null && cancel.IsCancelled)
                {
                    return new ProcessResult { ExitCode = -1, TimedOut = true };
                }
                if (arguments.Contains(" printenv HOME"))
                {
                    return HomeUnavailable
                        ? new ProcessResult { ExitCode = 1, StandardError = "distribution not running" }
                        : Ok(Home);
                }
                if (arguments.Contains(" which python3"))
                {
                    return PathPython == null ? new ProcessResult { ExitCode = 1 } : Ok(PathPython);
                }
                if (arguments.Contains(" ls -1 "))
                {
                    foreach (KeyValuePair<string, string[]> entry in Listings)
                    {
                        if (arguments.Contains(entry.Key))
                        {
                            return Ok(string.Join("\n", entry.Value));
                        }
                    }
                    return new ProcessResult { ExitCode = 2, StandardError = "No such file or directory" };
                }
                if (arguments.Contains("OMOBENCH_PROBE"))
                {
                    if (ProbesTimeOut)
                    {
                        return new ProcessResult { ExitCode = -1, TimedOut = true };
                    }
                    string best = null;
                    foreach (string interpreter in Probes.Keys)
                    {
                        if (arguments.Contains(interpreter)
                            && (best == null || interpreter.Length > best.Length))
                        {
                            best = interpreter;
                        }
                    }
                    if (best == null)
                    {
                        return new ProcessResult
                        {
                            ExitCode = 1,
                            StandardError = "No such file or directory",
                        };
                    }
                    return Ok(Probes[best]);
                }
                return new ProcessResult { ExitCode = 127 };
            }

            private static ProcessResult Ok(string output)
            {
                return new ProcessResult { ExitCode = 0, StandardOutput = output + "\n" };
            }
        }

        /// <summary>
        /// The rejection for one interpreter. Looked up rather than indexed:
        /// every interpreter that is tried and fails is reported, so the list
        /// also holds uninteresting ones such as the system python.
        /// </summary>
        private static InstallationCandidate RejectionFor(DiscoveryReport report, string interpreter)
        {
            foreach (InstallationCandidate rejected in report.Rejected)
            {
                if (rejected.PythonWslPath == interpreter) { return rejected; }
            }
            return null;
        }

        private static string ProbeAnswer(string module, string pinned, string runner, string executable)
        {
            return "OMOBENCH_PROBE {\"module\": \"" + module + "\", \"pinned\": \"" + pinned
                + "\", \"runner\": " + (runner == null ? "null" : "\"" + runner + "\"")
                + ", \"executable\": \"" + executable + "\"}";
        }

        private static void DiscoveryOutcomeTests()
        {
            Section("Discovery: validated installations");

            Func<string, int, DiscoveryCancel, ProcessResult> savedRunner = Discovery.WslRunner;
            Func<List<WslDistribution>> savedLister = Discovery.DistributionLister;
            Func<bool> savedAvailable = Discovery.WslAvailable;
            Func<List<string>> savedRoots = Discovery.SearchRootProvider;
            string sandbox = Sandbox();

            try
            {
                // Search the fixture tree instead of this machine's real
                // project folders, so the test is about the rules rather than
                // about whatever happens to be installed here.
                Discovery.SearchRootProvider = () => new List<string> { sandbox };

                string checkout = MakeCheckout(Path.Combine(sandbox, "My Projects", "omobench"));
                string exe = Path.Combine(checkout, "windows", "companion", "build", "OmoBenchCompanion.exe");
                Directory.CreateDirectory(Path.GetDirectoryName(exe));
                string checkoutWsl;
                CommandLine.TryToWslPath(checkout, out checkoutWsl);

                Discovery.WslAvailable = () => true;
                Discovery.DistributionLister = () => new List<WslDistribution>
                {
                    new WslDistribution { Name = "Ubuntu", Version = 2, IsDefault = true },
                };

                // 1. Exactly one validated installation.
                FakeWsl one = new FakeWsl();
                one.Listings["/home/linuxuser/.venvs"] = new[] { "omobench-runner" };
                one.Probes["/home/linuxuser/.venvs/omobench-runner/bin/python"] =
                    ProbeAnswer(checkoutWsl + "/cli/src/omobench/cli.py", "0.7.3", "0.7.3",
                        "/home/linuxuser/.venvs/omobench-runner/bin/python");
                Discovery.WslRunner = one.Run;
                DiscoveryReport single = Discovery.Run(new Configuration(), exe, new DiscoveryLimits(), null, null);
                Check("one validated installation is found",
                    single.Ready.Count == 1);
                Check("it names the distribution, checkout and interpreter",
                    single.Ready.Count == 1
                    && single.Ready[0].Distribution == "Ubuntu"
                    && single.Ready[0].CheckoutWindowsPath == checkout
                    && single.Ready[0].PythonWslPath == "/home/linuxuser/.venvs/omobench-runner/bin/python");
                Check("the Linux home directory is asked for, not assumed from the Windows user",
                    one.Commands.Exists(c => c.Contains("printenv HOME")));

                // 2. Several validated installations, offered for selection.
                FakeWsl several = new FakeWsl();
                string second = MakeCheckout(Path.Combine(sandbox, "Code", "omobench"));
                string secondWsl;
                CommandLine.TryToWslPath(second, out secondWsl);
                several.Listings["/home/linuxuser/.venvs"] = new[] { "omobench", "omobench-runner" };
                several.Probes["/home/linuxuser/.venvs/omobench-runner/bin/python"] =
                    ProbeAnswer(checkoutWsl + "/cli/src/omobench/cli.py", "0.7.3", "0.7.3", "x");
                several.Probes["/home/linuxuser/.venvs/omobench/bin/python"] =
                    ProbeAnswer(secondWsl + "/cli/src/omobench/cli.py", "0.7.3", "0.7.3", "y");
                Discovery.WslRunner = several.Run;
                DiscoveryReport many = Discovery.Run(new Configuration(), exe, new DiscoveryLimits(), null, null);
                Check("both validated installations are offered", many.Ready.Count == 2);
                Check("each one pairs its own checkout with its own interpreter",
                    many.Ready.Count == 2
                    && many.Ready[0].CheckoutWindowsPath != many.Ready[1].CheckoutWindowsPath
                    && many.Ready[0].PythonWslPath != many.Ready[1].PythonWslPath);
                Check("a candidate reads as one line a person can choose between",
                    many.Ready[0].ToString().Contains("Ubuntu")
                    && many.Ready[0].ToString().Contains(many.Ready[0].CheckoutWindowsPath));

                // 3. An interpreter that imports a different checkout.
                FakeWsl elsewhere = new FakeWsl();
                elsewhere.Listings["/home/linuxuser/.venvs"] = new[] { "omobench-runner" };
                elsewhere.Probes["/home/linuxuser/.venvs/omobench-runner/bin/python"] =
                    ProbeAnswer("/home/linuxuser/some-older-copy/cli/src/omobench/cli.py", "0.7.3", "0.7.3", "z");
                Discovery.WslRunner = elsewhere.Run;
                DiscoveryReport other = Discovery.Run(new Configuration(), exe, new DiscoveryLimits(), null, null);
                Check("an interpreter importing another checkout is not accepted",
                    other.Ready.Count == 0 && other.Rejected.Count >= 1);
                Check("both locations are shown, so the mismatch is actionable",
                    other.Rejected.Count >= 1
                    && other.Rejected[0].Detail != null
                    && other.Rejected[0].Detail.Contains("some-older-copy")
                    && other.Rejected[0].Detail.Contains(checkoutWsl));
                Check("the explanation names what was found and what is missing",
                    other.Explain().Contains("Checkouts found")
                    && other.Explain().Contains("different checkout"));

                // 4. Missing and wrong GuideLLM.
                FakeWsl noRunner = new FakeWsl();
                noRunner.Listings["/home/linuxuser/.venvs"] = new[] { "omobench-runner" };
                noRunner.Probes["/home/linuxuser/.venvs/omobench-runner/bin/python"] =
                    ProbeAnswer(checkoutWsl + "/cli/src/omobench/cli.py", "0.7.3", null, "z");
                Discovery.WslRunner = noRunner.Run;
                DiscoveryReport missing = Discovery.Run(new Configuration(), exe, new DiscoveryLimits(), null, null);
                InstallationCandidate noRunnerEntry = RejectionFor(
                    missing, "/home/linuxuser/.venvs/omobench-runner/bin/python");
                Check("a missing GuideLLM runner is not ready",
                    missing.Ready.Count == 0 && noRunnerEntry != null);
                Check("and says the runner is not installed",
                    noRunnerEntry != null && noRunnerEntry.Summary.Contains("not installed"));

                FakeWsl wrongRunner = new FakeWsl();
                wrongRunner.Listings["/home/linuxuser/.venvs"] = new[] { "omobench-runner" };
                wrongRunner.Probes["/home/linuxuser/.venvs/omobench-runner/bin/python"] =
                    ProbeAnswer(checkoutWsl + "/cli/src/omobench/cli.py", "0.7.3", "0.8.0", "z");
                Discovery.WslRunner = wrongRunner.Run;
                DiscoveryReport wrong = Discovery.Run(new Configuration(), exe, new DiscoveryLimits(), null, null);
                InstallationCandidate wrongEntry = RejectionFor(
                    wrong, "/home/linuxuser/.venvs/omobench-runner/bin/python");
                Check("a different GuideLLM version is not ready",
                    wrong.Ready.Count == 0 && wrongEntry != null);
                Check("and both versions are named",
                    wrongEntry != null && wrongEntry.Summary.Contains("0.8.0")
                    && wrongEntry.Summary.Contains("0.7.3"));

                // 5. A stale saved checkout and a stale saved interpreter.
                FakeWsl stale = new FakeWsl();
                stale.Listings["/home/linuxuser/.venvs"] = new[] { "omobench-runner" };
                stale.Probes["/home/linuxuser/.venvs/omobench-runner/bin/python"] =
                    ProbeAnswer(checkoutWsl + "/cli/src/omobench/cli.py", "0.7.3", "0.7.3", "z");
                Discovery.WslRunner = stale.Run;
                Configuration staleSaved = new Configuration
                {
                    Distribution = "Ubuntu",
                    CheckoutWindowsPath = Path.Combine(sandbox, "moved-away"),
                    PythonWslPath = "/home/linuxuser/.venvs/deleted-env/bin/python",
                };
                DiscoveryReport recovered = Discovery.Run(staleSaved, exe, new DiscoveryLimits(), null, null);
                Check("a saved checkout that no longer exists is skipped, not offered",
                    !recovered.Checkouts.Exists(c => c.WindowsPath.EndsWith("moved-away", StringComparison.Ordinal)));
                Check("a saved interpreter that no longer exists does not stop discovery",
                    recovered.Ready.Count == 1
                    && recovered.Ready[0].PythonWslPath == "/home/linuxuser/.venvs/omobench-runner/bin/python");

                // 6. Distribution problems are distinguished from missing packages.
                Discovery.WslAvailable = () => false;
                DiscoveryReport noWsl = Discovery.Run(new Configuration(), exe, new DiscoveryLimits(), null, null);
                Check("a machine without WSL is reported as such",
                    noWsl.Ready.Count == 0 && noWsl.Explain().Contains("wsl.exe"));

                Discovery.WslAvailable = () => true;
                Discovery.DistributionLister = () => new List<WslDistribution>
                {
                    new WslDistribution { Name = "Legacy", Version = 1, IsDefault = true },
                };
                DiscoveryReport onlyWsl1 = Discovery.Run(new Configuration(), exe, new DiscoveryLimits(), null, null);
                Check("a WSL1-only machine is told to convert, not that packages are missing",
                    onlyWsl1.Ready.Count == 0 && onlyWsl1.Explain().Contains("WSL2"));

                Discovery.DistributionLister = () => new List<WslDistribution>
                {
                    new WslDistribution { Name = "Ubuntu", Version = 2, IsDefault = true },
                };
                FakeWsl stopped = new FakeWsl { HomeUnavailable = true };
                Discovery.WslRunner = stopped.Run;
                DiscoveryReport unavailable = Discovery.Run(new Configuration(), exe, new DiscoveryLimits(), null, null);
                Check("a distribution that cannot answer is distinguished from one missing OmoBench",
                    unavailable.Ready.Count == 0
                    && unavailable.Explain().Contains("home directory"));

                FakeWsl slow = new FakeWsl { ProbesTimeOut = true };
                slow.Listings["/home/linuxuser/.venvs"] = new[] { "omobench-runner" };
                Discovery.WslRunner = slow.Run;
                DiscoveryReport timedOutProbe = Discovery.Run(
                    new Configuration(), exe, new DiscoveryLimits(), null, null);
                Check("a probe that times out is not read as 'not installed'",
                    timedOutProbe.Ready.Count == 0
                    && timedOutProbe.Rejected.Count >= 1
                    && timedOutProbe.Rejected[0].Summary.Contains("timed out"));

                // 7. Overall deadline and cancellation.
                Discovery.WslRunner = one.Run;
                DiscoveryLimits instant = new DiscoveryLimits();
                instant.OverallTimeoutMilliseconds = 0;
                DiscoveryReport expired = Discovery.Run(new Configuration(), exe, instant, null, null);
                Check("an elapsed overall deadline is reported", expired.TimedOut);

                DiscoveryCancel cancel = new DiscoveryCancel();
                cancel.Cancel();
                DiscoveryReport cancelled = Discovery.Run(
                    new Configuration(), exe, new DiscoveryLimits(), cancel, null);
                Check("a cancelled search reports itself cancelled", cancelled.Cancelled);
                Check("and ran no probes", !cancelled.Explain().Contains("Why none"));

                // 8. Discovery changes nothing on the machine.
                bool configurationBefore = File.Exists(Configuration.FilePath);
                string registrationBefore = ProtocolRegistration.RegisteredCommand();
                Discovery.WslRunner = one.Run;
                Discovery.Run(new Configuration(), exe, new DiscoveryLimits(), null, null);
                Check("discovery writes no configuration file",
                    File.Exists(Configuration.FilePath) == configurationBefore);
                Check("discovery creates no protocol registration",
                    string.Equals(ProtocolRegistration.RegisteredCommand() ?? "",
                        registrationBefore ?? "", StringComparison.Ordinal));
            }
            finally
            {
                Discovery.WslRunner = savedRunner;
                Discovery.DistributionLister = savedLister;
                Discovery.WslAvailable = savedAvailable;
                Discovery.SearchRootProvider = savedRoots;
                Discard(sandbox);
            }
        }

        /// <summary>
        /// The Setup window's own state machine: which search result still
        /// counts, what a filled-in value may overwrite, and whether what is on
        /// screen is the combination that was actually validated.
        ///
        /// These are window interactions rather than discovery results, and
        /// both cases below were real defects: a cancelled search reaching in
        /// to change the controls of the search that replaced it, and a
        /// validated candidate being reported as ready for a combination the
        /// person had since edited.
        /// </summary>
        private static void SetupStateTests()
        {
            Section("Setup window state");

            // -- a stale completion must change nothing --------------------

            SetupState state = new SetupState();
            Check("a fresh window offers Find and nothing to cancel",
                state.FindEnabled && !state.CancelEnabled && !state.SearchRunning);

            int searchA = state.BeginSearch();
            Check("starting a search disables Find and offers Cancel",
                searchA != 0 && state.SearchRunning && state.CancelEnabled && !state.FindEnabled);
            Check("a second search cannot start alongside the first",
                state.BeginSearch() == 0);

            state.CancelSearch();
            int searchB = state.BeginSearch();
            Check("after cancelling, a new search starts", searchB != 0 && searchB != searchA);
            Check("the new search owns the controls",
                state.SearchRunning && state.CancelEnabled && !state.FindEnabled);

            // Search A now finishes, late, while B is still running.
            Check("the cancelled search's completion is refused",
                !state.CompleteSearch(searchA));
            Check("it did not stop the running search", state.SearchRunning);
            Check("it did not take away the running search's Cancel button", state.CancelEnabled);
            Check("it did not re-enable Find while a search is running", !state.FindEnabled);
            Check("and it did not let a third search start alongside B",
                state.BeginSearch() == 0);

            Check("the current search's completion is accepted",
                state.CompleteSearch(searchB));
            Check("which releases the controls",
                !state.SearchRunning && !state.CancelEnabled && state.FindEnabled);

            // -- an edit invalidates the question --------------------------

            InstallationCandidate found = new InstallationCandidate
            {
                Distribution = "Ubuntu",
                CheckoutWindowsPath = @"C:\Projects\omobench",
                PythonWslPath = "/home/sam/.venvs/omobench-runner/bin/python",
                Ready = true,
                Summary = "Ready: ...",
            };

            state = new SetupState();
            int search = state.BeginSearch();
            Check("editing the interpreter stops the search in flight",
                state.NoteInterpreterEdited());
            Check("and its result is then refused", !state.CompleteSearch(search));
            Check("the interpreter edit is remembered", state.UserEditedInterpreter);

            state = new SetupState();
            search = state.BeginSearch();
            Check("editing the checkout stops the search in flight", state.NoteCheckoutEdited());
            Check("and its result is then refused", !state.CompleteSearch(search));

            state = new SetupState();
            search = state.BeginSearch();
            Check("changing the distribution stops the search in flight",
                state.NoteDistributionChanged());
            Check("and its result is then refused", !state.CompleteSearch(search));

            // -- ready means this exact combination ------------------------

            state = new SetupState();
            state.NoteApplied(found, true);
            Check("the validated combination reports ready",
                state.IsReadyFor("Ubuntu", @"C:\Projects\omobench",
                    "/home/sam/.venvs/omobench-runner/bin/python"));
            Check("a trailing slash and different case on the checkout still match",
                state.IsReadyFor("Ubuntu", @"c:\projects\omobench\",
                    "/home/sam/.venvs/omobench-runner/bin/python"));
            Check("a different interpreter is not ready",
                !state.IsReadyFor("Ubuntu", @"C:\Projects\omobench",
                    "/home/sam/.venvs/something-else/bin/python"));
            Check("a different checkout is not ready",
                !state.IsReadyFor("Ubuntu", @"C:\Other\omobench",
                    "/home/sam/.venvs/omobench-runner/bin/python"));
            Check("a different distribution is not ready",
                !state.IsReadyFor("Debian", @"C:\Projects\omobench",
                    "/home/sam/.venvs/omobench-runner/bin/python"));
            Check("Linux interpreter paths are compared case-sensitively",
                !state.IsReadyFor("Ubuntu", @"C:\Projects\omobench",
                    "/home/sam/.venvs/OMOBENCH-RUNNER/bin/python"));

            state.NoteInterpreterEdited();
            Check("editing a value drops the validation entirely",
                state.Validated == null
                && !state.IsReadyFor("Ubuntu", @"C:\Projects\omobench",
                    "/home/sam/.venvs/omobench-runner/bin/python"));

            // -- a default filled in is not a choice made -------------------

            state = new SetupState();
            state.BeginSearch();
            state.NoteInterpreterEdited();     // the person types an interpreter
            Check("a default must not overwrite the typed interpreter",
                !state.MayApplyInterpreter(false));
            Check("but may still fill in the untouched checkout",
                state.MayApplyCheckout(false));

            Check("an explicit choice may replace the typed interpreter",
                state.MayApplyInterpreter(true));
            Check("an explicit choice may replace the checkout too",
                state.MayApplyCheckout(true));

            state.NoteApplied(found, true);
            Check("after an explicit choice there is no edit left to protect",
                !state.UserEditedInterpreter && !state.UserEditedCheckout);
            Check("so all three fields came from one validated candidate",
                state.MayApplyCheckout(false) && state.MayApplyInterpreter(false)
                && state.IsReadyFor(found.Distribution, found.CheckoutWindowsPath,
                    found.PythonWslPath));

            // A default applied at the end of a search records the candidate
            // but leaves the edit in place, so the window can see that what is
            // displayed is not what was validated.
            state = new SetupState();
            state.BeginSearch();
            state.NoteCheckoutEdited();
            state.NoteApplied(found, false);
            Check("a default keeps the person's edit flagged", state.UserEditedCheckout);
            Check("and the displayed combination is not reported ready",
                !state.IsReadyFor("Ubuntu", @"D:\my own choice\omobench",
                    "/home/sam/.venvs/omobench-runner/bin/python"));
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
