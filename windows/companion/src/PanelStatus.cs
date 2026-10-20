using System;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace OmoBench.Companion
{
    /// <summary>
    /// Reader for the startup document <c>omobench ui --status-file</c>
    /// writes (see write_status_file in cli/src/omobench/ui.py).
    ///
    /// This is the private channel that replaces scraping the panel's console
    /// banner: the companion learns the URL, port and token as data, and
    /// learns *why* a start failed from a stable reason code rather than from
    /// English prose it would have to pattern-match.
    /// </summary>
    public sealed class PanelStatus
    {
        public const int SupportedSchema = 1;

        public int Schema { get; private set; }
        public string State { get; private set; }
        public string Reason { get; private set; }
        public string Message { get; private set; }
        public string Url { get; private set; }
        public string Origin { get; private set; }
        public string Token { get; private set; }
        public int Port { get; private set; }

        /// <summary>
        /// The panel's own process id *inside the distribution*. Used only to
        /// ask that process to shut down the way Ctrl+C would; never to kill
        /// anything on the Windows side, and only after the panel has proved
        /// it is ours by answering on its loopback port with our token.
        /// </summary>
        public int PanelProcessId { get; private set; }

        public bool IsReady
        {
            get { return string.Equals(State, "ready", StringComparison.Ordinal) && !string.IsNullOrEmpty(Url); }
        }

        public bool IsFailed
        {
            get { return string.Equals(State, "failed", StringComparison.Ordinal); }
        }

        /// <summary>
        /// Parse the document. Deliberately strict: an unknown schema is
        /// refused rather than guessed at, because the fields carry the
        /// credentials this companion is about to hand to a browser.
        ///
        /// The document is a flat object of string/number values written by
        /// json.dump, so this small reader avoids taking a JSON dependency
        /// for one file; anything it cannot read becomes a parse failure, not
        /// a partial result.
        /// </summary>
        public static PanelStatus Parse(string json, out string error)
        {
            error = null;
            if (string.IsNullOrWhiteSpace(json))
            {
                error = "The panel wrote an empty status document.";
                return null;
            }

            PanelStatus status = new PanelStatus
            {
                Schema = ReadInt(json, "schema", 0),
                State = ReadString(json, "state"),
                Reason = ReadString(json, "reason"),
                Message = ReadString(json, "message"),
                Url = ReadString(json, "url"),
                Origin = ReadString(json, "origin"),
                Token = ReadString(json, "token"),
                Port = ReadInt(json, "port", 0),
                PanelProcessId = ReadInt(json, "pid", 0),
            };

            if (status.Schema != SupportedSchema)
            {
                error = "This companion understands panel status schema " + SupportedSchema
                    + ", but the panel wrote schema " + status.Schema
                    + ". Update the companion to match this checkout.";
                return null;
            }

            if (!status.IsReady && !status.IsFailed)
            {
                error = "The panel status document is neither ready nor failed.";
                return null;
            }

            if (status.IsReady)
            {
                // Never hand anything but a loopback URL to the browser, no
                // matter what the document claims.
                if (!IsLoopbackHttpUrl(status.Url))
                {
                    error = "The panel reported a non-loopback address; refusing to open it.";
                    return null;
                }
                if (string.IsNullOrEmpty(status.Token))
                {
                    error = "The panel reported no connection token.";
                    return null;
                }
            }

            return status;
        }

        /// <summary>
        /// The panel binds to 127.0.0.1 only. Accept nothing else, so a
        /// malformed or tampered document cannot send the browser (with its
        /// token) somewhere else.
        /// </summary>
        public static bool IsLoopbackHttpUrl(string candidate)
        {
            if (string.IsNullOrWhiteSpace(candidate))
            {
                return false;
            }
            Uri uri;
            if (!Uri.TryCreate(candidate, UriKind.Absolute, out uri))
            {
                return false;
            }
            if (!string.Equals(uri.Scheme, "http", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            if (!string.IsNullOrEmpty(uri.UserInfo))
            {
                return false;
            }
            string host = uri.Host;
            return string.Equals(host, "127.0.0.1", StringComparison.Ordinal)
                || string.Equals(host, "::1", StringComparison.Ordinal)
                || string.Equals(host, "[::1]", StringComparison.Ordinal)
                || string.Equals(host, "localhost", StringComparison.OrdinalIgnoreCase);
        }

        private static string ReadString(string json, string key)
        {
            Match match = Regex.Match(
                json, "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"");
            if (!match.Success)
            {
                return null;
            }
            return Unescape(match.Groups[1].Value);
        }

        private static int ReadInt(string json, string key, int fallback)
        {
            Match match = Regex.Match(json, "\"" + Regex.Escape(key) + "\"\\s*:\\s*(-?\\d+)");
            if (!match.Success)
            {
                return fallback;
            }
            int value;
            return int.TryParse(match.Groups[1].Value, NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
                ? value : fallback;
        }

        private static string Unescape(string value)
        {
            StringBuilder builder = new StringBuilder(value.Length);
            for (int index = 0; index < value.Length; index++)
            {
                char character = value[index];
                if (character != '\\' || index + 1 >= value.Length)
                {
                    builder.Append(character);
                    continue;
                }
                index++;
                char escaped = value[index];
                switch (escaped)
                {
                    case 'n': builder.Append('\n'); break;
                    case 'r': builder.Append('\r'); break;
                    case 't': builder.Append('\t'); break;
                    case 'b': builder.Append('\b'); break;
                    case 'f': builder.Append('\f'); break;
                    case '/': builder.Append('/'); break;
                    case '\\': builder.Append('\\'); break;
                    case '"': builder.Append('"'); break;
                    case 'u':
                        if (index + 4 < value.Length)
                        {
                            int code;
                            if (int.TryParse(value.Substring(index + 1, 4), NumberStyles.HexNumber,
                                    CultureInfo.InvariantCulture, out code))
                            {
                                builder.Append((char)code);
                                index += 4;
                            }
                        }
                        break;
                    default:
                        builder.Append(escaped);
                        break;
                }
            }
            return builder.ToString();
        }
    }
}
