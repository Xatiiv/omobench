using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;

namespace OmoBench.Companion
{
    public sealed class WslDistribution
    {
        public string Name { get; set; }
        public int Version { get; set; }
        public bool IsDefault { get; set; }

        public override string ToString()
        {
            string suffix = Version == 2 ? "WSL2" : "WSL" + Version.ToString(CultureInfo.InvariantCulture);
            return Name + "  (" + suffix + (IsDefault ? ", default" : "") + ")";
        }
    }

    public sealed class ProcessResult
    {
        public int ExitCode { get; set; }
        public string StandardOutput { get; set; }
        public string StandardError { get; set; }
        public bool TimedOut { get; set; }

        public string CombinedOutput
        {
            get { return ((StandardOutput ?? "") + "\n" + (StandardError ?? "")).Trim(); }
        }
    }

    /// <summary>
    /// Everything this companion knows how to ask of WSL. All of it runs
    /// wsl.exe directly with an argument list built by <see cref="CommandLine"/>
    /// -- never through cmd.exe or a Linux shell, and never with any value
    /// derived from a URI.
    /// </summary>
    public static class Wsl
    {
        public static string ExecutablePath
        {
            get
            {
                // Resolve explicitly out of System32 rather than trusting PATH.
                string system = Environment.GetFolderPath(Environment.SpecialFolder.System);
                string path = Path.Combine(system, "wsl.exe");
                return File.Exists(path) ? path : "wsl.exe";
            }
        }

        public static bool IsAvailable
        {
            get { return File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "wsl.exe")); }
        }

        /// <summary>Run a program and capture its output, without a console window.</summary>
        public static ProcessResult Run(string fileName, string arguments, int timeoutMilliseconds, Encoding outputEncoding = null)
        {
            ProcessStartInfo startInfo = new ProcessStartInfo
            {
                FileName = fileName,
                Arguments = arguments,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            if (outputEncoding != null)
            {
                startInfo.StandardOutputEncoding = outputEncoding;
                startInfo.StandardErrorEncoding = outputEncoding;
            }

            using (Process process = new Process())
            {
                process.StartInfo = startInfo;
                StringBuilder output = new StringBuilder();
                StringBuilder error = new StringBuilder();
                process.OutputDataReceived += (sender, e) => { if (e.Data != null) output.AppendLine(e.Data); };
                process.ErrorDataReceived += (sender, e) => { if (e.Data != null) error.AppendLine(e.Data); };
                process.Start();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                if (!process.WaitForExit(timeoutMilliseconds))
                {
                    try { process.Kill(); } catch (Exception) { }
                    return new ProcessResult { ExitCode = -1, TimedOut = true, StandardOutput = output.ToString(), StandardError = error.ToString() };
                }
                process.WaitForExit();
                return new ProcessResult
                {
                    ExitCode = process.ExitCode,
                    StandardOutput = output.ToString(),
                    StandardError = error.ToString(),
                };
            }
        }

        /// <summary>
        /// Parse `wsl.exe --list --verbose` output. wsl.exe emits UTF-16 and
        /// localises its headers, so this reads the shape of the table rather
        /// than its words: optional "*" default marker, name, state, version.
        /// </summary>
        public static List<WslDistribution> ParseDistributionList(string listOutput)
        {
            List<WslDistribution> distributions = new List<WslDistribution>();
            if (string.IsNullOrWhiteSpace(listOutput))
            {
                return distributions;
            }

            foreach (string rawLine in listOutput.Replace("\0", "").Split('\n'))
            {
                string line = rawLine.Replace("\r", "").Trim();
                if (line.Length == 0)
                {
                    continue;
                }

                bool isDefault = line.StartsWith("*", StringComparison.Ordinal);
                if (isDefault)
                {
                    line = line.Substring(1).Trim();
                }

                string[] columns = line.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries);
                if (columns.Length < 3)
                {
                    continue;
                }

                string versionText = columns[columns.Length - 1];
                int version;
                if (!int.TryParse(versionText, NumberStyles.Integer, CultureInfo.InvariantCulture, out version))
                {
                    continue; // header row, or a line this version of wsl.exe shapes differently
                }

                // Name may contain spaces; state is the column before version.
                string name = string.Join(" ", columns, 0, columns.Length - 2);
                if (name.Length == 0)
                {
                    continue;
                }

                distributions.Add(new WslDistribution { Name = name, Version = version, IsDefault = isDefault });
            }

            return distributions;
        }

        public static List<WslDistribution> ListDistributions()
        {
            if (!IsAvailable)
            {
                return new List<WslDistribution>();
            }
            // wsl.exe writes UTF-16LE for this command.
            ProcessResult result = Run(ExecutablePath, "--list --verbose", 20000, Encoding.Unicode);
            return ParseDistributionList(result.CombinedOutput);
        }

        /// <summary>
        /// Build the argument string that runs one program inside a
        /// distribution, with a working directory, and nothing else: no login
        /// shell, no profile, no PATH lookup.
        /// </summary>
        public static string BuildRunArguments(string distribution, string workingDirectoryWslPath, IEnumerable<string> program)
        {
            List<string> arguments = new List<string> { "--distribution", distribution };
            if (!string.IsNullOrEmpty(workingDirectoryWslPath))
            {
                arguments.Add("--cd");
                arguments.Add(workingDirectoryWslPath);
            }
            // --exec, not --. With "--", wsl.exe hands the command to the
            // distribution's login shell, which then expands it: a path
            // containing $, a backtick, a glob or a semicolon stops meaning
            // itself. --exec runs the program directly through execvpe, so
            // every argument arrives exactly as written and no shell is
            // involved at any point.
            arguments.Add("--exec");
            arguments.AddRange(program);
            return CommandLine.Join(arguments);
        }
    }
}
