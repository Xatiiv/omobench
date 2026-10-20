using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace OmoBench.Companion
{
    /// <summary>
    /// Small rolling diagnostic log for "nothing opened, why?" questions.
    ///
    /// The panel prints its connection URL on startup, and this companion
    /// reads that output, so every line is redacted before it is written
    /// anywhere: a connection token must not survive in a log file, a support
    /// paste, or a screenshot. Redaction happens here, once, so no caller has
    /// to remember to do it.
    /// </summary>
    public static class Diagnostics
    {
        private static readonly object Gate = new object();
        private const long MaximumBytes = 256 * 1024;

        private static readonly Regex TokenFragment =
            new Regex(@"#token=[^\s""']*", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        private static readonly Regex TokenAssignment =
            new Regex(@"(?i)\b(token|api[_-]?key|authorization)\b\s*[:=]\s*\S+", RegexOptions.Compiled);
        private static readonly Regex TokenHeader =
            new Regex(@"(?i)X-OmoBench-Token\s*:\s*\S+", RegexOptions.Compiled);

        public static string LogPath
        {
            get { return Path.Combine(Configuration.RuntimeDirectoryPath, "companion.log"); }
        }

        /// <summary>
        /// Remove anything token-shaped from a line of text. Applied to every
        /// line the panel prints and to every message shown or logged.
        /// </summary>
        public static string Redact(string text)
        {
            if (string.IsNullOrEmpty(text))
            {
                return text;
            }
            string redacted = TokenFragment.Replace(text, "#token=<redacted>");
            redacted = TokenHeader.Replace(redacted, "X-OmoBench-Token: <redacted>");
            redacted = TokenAssignment.Replace(redacted, match => match.Groups[1].Value + "=<redacted>");
            return redacted;
        }

        public static void Write(string message)
        {
            try
            {
                lock (Gate)
                {
                    Configuration.EnsureRuntimeDirectory();
                    string path = LogPath;
                    if (File.Exists(path) && new FileInfo(path).Length > MaximumBytes)
                    {
                        File.Delete(path);
                    }
                    string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture)
                        + "  " + Redact(message) + Environment.NewLine;
                    File.AppendAllText(path, line, new UTF8Encoding(false));
                }
            }
            catch (Exception)
            {
                // Diagnostics must never be the reason a launch fails.
            }
        }
    }
}
