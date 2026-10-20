using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace OmoBench.Companion
{
    public sealed class EnvironmentCheckResult
    {
        public bool Ok { get; set; }
        public string Summary { get; set; }
        public string Detail { get; set; }
        public string ModulePath { get; set; }
        public string PinnedRunnerVersion { get; set; }
        public string InstalledRunnerVersion { get; set; }
    }

    /// <summary>
    /// Confirms, before anything is launched, that the configured interpreter
    /// is really the one that should run: that it imports OmoBench *from the
    /// selected checkout* (not a different copy installed elsewhere), and
    /// that the pinned GuideLLM runner is present in that same environment.
    ///
    /// The probe is a fixed one-line script: it is never built from user text
    /// or URI input, and it deliberately contains no double quotes so it
    /// survives Windows command-line quoting unchanged.
    /// </summary>
    public static class EnvironmentCheck
    {
        // Single-quoted Python only -- see the note above about quoting.
        internal const string ProbeScript =
            "import json,sys,importlib.metadata as md;" +
            "import omobench.cli as c;" +
            "g=next((d.version for d in md.distributions() if (d.metadata['Name'] or '').lower()=='guidellm'), None);" +
            "print('OMOBENCH_PROBE ' + json.dumps({'module':c.__file__,'pinned':c.GUIDELLM_VERSION,'runner':g,'executable':sys.executable}))";

        public static List<string> BuildProbeProgram(string pythonWslPath)
        {
            return new List<string> { pythonWslPath, "-c", ProbeScript };
        }

        /// <summary>
        /// Interpret the probe output. Kept separate from running it so the
        /// decision logic is testable without WSL.
        /// </summary>
        public static EnvironmentCheckResult Interpret(
            int exitCode, string output, string checkoutWslPath, bool timedOut)
        {
            if (timedOut)
            {
                return Fail("The environment check timed out.",
                    "WSL did not answer in time. Start the distribution and try again.");
            }

            if (exitCode != 0 || output == null || output.IndexOf("OMOBENCH_PROBE", StringComparison.Ordinal) < 0)
            {
                string detail = string.IsNullOrWhiteSpace(output)
                    ? "No output was returned."
                    : Diagnostics.Redact(output.Trim());
                return Fail("This interpreter cannot import OmoBench.",
                    "Install it into that environment first:\n"
                    + "    python -m pip install -e './cli[ui,runner]'\n\n"
                    + "Reported:\n" + detail);
            }

            Match line = Regex.Match(output, @"OMOBENCH_PROBE\s+(\{.*\})");
            string document = line.Success ? line.Groups[1].Value : "";
            string modulePath = ReadString(document, "module");
            string pinned = ReadString(document, "pinned");
            string runner = ReadString(document, "runner");
            string executable = ReadString(document, "executable");

            if (string.IsNullOrEmpty(modulePath))
            {
                return Fail("The environment check returned an unreadable answer.",
                    Diagnostics.Redact(output.Trim()));
            }

            if (!ImportsFromCheckout(modulePath, checkoutWslPath))
            {
                return Fail("That interpreter imports OmoBench from a different checkout.",
                    "Selected checkout:\n    " + checkoutWslPath
                    + "\n\nImported instead from:\n    " + modulePath
                    + "\n\nInstall OmoBench into this environment from the selected checkout, "
                    + "or pick the interpreter that already has it.");
            }

            if (string.IsNullOrEmpty(runner))
            {
                return Fail("The pinned GuideLLM runner is not installed in that environment.",
                    "The panel opens, but benchmarks cannot run. Install the runner extra:\n"
                    + "    python -m pip install -e './cli[ui,runner]'");
            }

            if (!string.IsNullOrEmpty(pinned) && !string.Equals(runner, pinned, StringComparison.Ordinal))
            {
                return Fail("That environment has GuideLLM " + runner + ", but this checkout pins " + pinned + ".",
                    "Reinstall the runner extra from this checkout so the pinned version is used.");
            }

            return new EnvironmentCheckResult
            {
                Ok = true,
                Summary = "Ready: OmoBench imports from the selected checkout, GuideLLM "
                    + runner + " is installed.",
                Detail = "Interpreter: " + executable + "\nOmoBench module: " + modulePath,
                ModulePath = modulePath,
                PinnedRunnerVersion = pinned,
                InstalledRunnerVersion = runner,
            };
        }

        /// <summary>
        /// True when the imported module really lives inside the selected
        /// checkout. Compared as normalised path segments so that a sibling
        /// directory with a shared prefix ("/home/x/omobench-copy") is not
        /// mistaken for the checkout ("/home/x/omobench").
        /// </summary>
        public static bool ImportsFromCheckout(string modulePath, string checkoutWslPath)
        {
            if (string.IsNullOrWhiteSpace(modulePath) || string.IsNullOrWhiteSpace(checkoutWslPath))
            {
                return false;
            }
            string module = Normalise(modulePath);
            string checkout = Normalise(checkoutWslPath);
            return module.Equals(checkout, StringComparison.OrdinalIgnoreCase)
                || module.StartsWith(checkout + "/", StringComparison.OrdinalIgnoreCase);
        }

        private static string Normalise(string path)
        {
            return path.Replace('\\', '/').TrimEnd('/');
        }

        private static EnvironmentCheckResult Fail(string summary, string detail)
        {
            return new EnvironmentCheckResult { Ok = false, Summary = summary, Detail = detail };
        }

        private static string ReadString(string json, string key)
        {
            Match match = Regex.Match(json, "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"");
            return match.Success ? match.Groups[1].Value.Replace("\\\\", "\\").Replace("\\\"", "\"") : null;
        }

        /// <summary>Run the probe for real against the configured environment.</summary>
        public static EnvironmentCheckResult Run(Configuration configuration)
        {
            string invalid = configuration.Validate();
            if (invalid != null)
            {
                return Fail("The configuration is incomplete.", invalid);
            }
            string checkoutWslPath = configuration.CheckoutWslPath;
            string arguments = Wsl.BuildRunArguments(
                configuration.Distribution, checkoutWslPath,
                BuildProbeProgram(configuration.PythonWslPath));
            Diagnostics.Write("Environment check: wsl.exe " + arguments);
            ProcessResult result = Wsl.Run(Wsl.ExecutablePath, arguments, 120000);
            return Interpret(result.ExitCode, result.CombinedOutput, checkoutWslPath, result.TimedOut);
        }

        public static string DescribeDistribution(IEnumerable<WslDistribution> distributions, string selected)
        {
            foreach (WslDistribution distribution in distributions)
            {
                if (string.Equals(distribution.Name, selected, StringComparison.Ordinal))
                {
                    return distribution.Version == 2
                        ? null
                        : "'" + selected + "' is WSL version "
                          + distribution.Version.ToString(CultureInfo.InvariantCulture)
                          + ". OmoBench needs WSL2. Convert it with:\n    wsl --set-version "
                          + selected + " 2";
                }
            }
            return "'" + selected + "' is not an installed WSL distribution on this machine.";
        }
    }
}
