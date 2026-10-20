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
            ActivityCheckTests();
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

            // Confirming a stop, and escalating to one, use the same pid rules.
            List<string> probe = PanelSession.BuildAliveProbeProgram(4242);
            Check("liveness is checked with a signal that does nothing",
                probe.Count == 3 && probe[0] == "kill" && probe[1] == "-0" && probe[2] == "4242");

            List<string> exists = PanelSession.BuildProcessExistsProgram(4242);
            Check("the process listing can be asked about one known process",
                exists[0] == "ps" && exists.Contains("-p") && exists[exists.Count - 1] == "4242");

            List<string> children = PanelSession.BuildChildListProgram(4242);
            Check("children are listed by asking ps for one parent's children",
                children[0] == "ps" && children.Contains("--ppid") && children[children.Count - 1] == "4242");
            Check("the process listing asks for pids only, with no shell",
                children.Contains("-o") && children.Contains("pid="));

            List<string> force = PanelSession.BuildForceKillProgram(new[] { 4242, 4243 });
            Check("a forced stop names every process explicitly",
                force.Count == 4 && force[0] == "kill" && force[1] == "-KILL"
                && force[2] == "4242" && force[3] == "4243");
            Check("a forced stop never takes a name, a pattern, or a process group",
                !force.Contains("-f") && !force.Contains("pkill") && !force.Contains("-1"));

            foreach (int refused in new[] { 0, 1, -1, -4242 })
            {
                bool probeRejected = false, listRejected = false, killRejected = false;
                try { PanelSession.BuildAliveProbeProgram(refused); }
                catch (ArgumentOutOfRangeException) { probeRejected = true; }
                try { PanelSession.BuildChildListProgram(refused); }
                catch (ArgumentOutOfRangeException) { listRejected = true; }
                try { PanelSession.BuildForceKillProgram(new[] { refused }); }
                catch (ArgumentOutOfRangeException) { killRejected = true; }
                Check("a pid of " + refused + " is refused everywhere, not just by SIGTERM",
                    probeRejected && listRejected && killRejected);
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
            }

            public bool IsAlive(int processId) { return Living.Contains(processId); }

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

                if (arguments.Contains(" -- kill -0 "))
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

                if (arguments.Contains(" -- kill -TERM "))
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

                if (arguments.Contains(" -- kill -KILL "))
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

                if (arguments.Contains(" -- ps "))
                {
                    if (!ProcessListingWorks)
                    {
                        // A ps usage error exits 1 too -- indistinguishable from
                        // "nothing matched" by exit code alone.
                        return Exit(1, "", "error: unknown gnu long option");
                    }
                    if (arguments.Contains(" --ppid "))
                    {
                        if (ChildListFails.Contains(last)) { return Exit(127, "", "ps: cannot answer"); }
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
                        return Living.Contains(last) ? Exit(0, last + "\n") : Exit(1);
                    }
                    if (arguments.EndsWith(" -e", StringComparison.Ordinal))
                    {
                        return Exit(0, Lines(Living));
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
                Check("it was asked, not killed", polite.Ran(" -- kill -TERM 1417") && !polite.Ran("kill -KILL"));
                Check("its departure was actually verified", polite.Ran(" -- kill -0 1417"));

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
                Check("SIGTERM was tried before SIGKILL", stubborn.Ran(" -- kill -TERM 1417"));
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
                    denied.Ran(" -- ps -o pid= --no-headers -p 1417"));

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

                // 5. The capability probe succeeds, then a child listing fails.
                //    "ps works" is not "every later ps answer is trustworthy".
                FakeDistribution flaky = new FakeDistribution(PanelPid, 2000, 2001);
                flaky.IgnoresTerm.Add(PanelPid);
                flaky.Children[PanelPid] = new[] { 2000 };
                flaky.Children[2000] = new[] { 2001 };
                flaky.ChildListFails.Add(2000);
                PanelSession.WslRunner = flaky.Run;
                ShutdownOutcome partial = new PanelSession().ShutdownLinuxSide(
                    "Ubuntu", PanelPid, "http://127.0.0.1:8765", "t", out report);
                Check("a child listing that fails mid-walk is refused, not read as childless",
                    partial == ShutdownOutcome.NotConfirmed);
                Check("no partial tree was force-stopped", !flaky.Ran("kill -KILL"));
                Check("the grandchild behind the failed listing is still alive",
                    flaky.IsAlive(2001));

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
                    survivingPanel.Ran(" -- kill -TERM 1417"));

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
