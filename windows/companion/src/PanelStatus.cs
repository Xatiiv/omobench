using System;
using System.Collections.Generic;
using System.Globalization;
using System.Web.Script.Serialization;

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
    ///
    /// Everything here is deliberately strict. The fields carry credentials
    /// this companion is about to hand to a browser, send in an authenticated
    /// request, and use to decide which process to signal, so the document is
    /// parsed as JSON by a real parser and then checked for internal
    /// consistency. A document that is malformed, or whose url, origin, port
    /// and token do not agree with each other, is refused outright rather than
    /// partially believed.
    /// </summary>
    public sealed class PanelStatus
    {
        public const int SupportedSchema = 1;

        /// <summary>
        /// The document the panel writes is a few hundred bytes. Anything
        /// substantially larger is not it, and is refused before parsing.
        /// </summary>
        private const int MaximumDocumentCharacters = 64 * 1024;

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
        /// Parse and validate the document. Returns null, with a reason, for
        /// anything that is not a well-formed, internally consistent status
        /// document for a schema this companion knows.
        /// </summary>
        public static PanelStatus Parse(string json, out string error)
        {
            error = null;
            if (string.IsNullOrWhiteSpace(json))
            {
                error = "The panel wrote an empty status document.";
                return null;
            }
            if (json.Length > MaximumDocumentCharacters)
            {
                error = "The panel status document is implausibly large; refusing to parse it.";
                return null;
            }

            Dictionary<string, object> fields;
            try
            {
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                serializer.MaxJsonLength = MaximumDocumentCharacters;
                serializer.RecursionLimit = 8;
                fields = serializer.DeserializeObject(json) as Dictionary<string, object>;
            }
            catch (Exception parseFailure)
            {
                error = "The panel status document is not valid JSON: " + parseFailure.Message;
                return null;
            }

            if (fields == null)
            {
                error = "The panel status document is not a JSON object.";
                return null;
            }

            PanelStatus status = new PanelStatus();
            int schema;
            if (!TryReadInt(fields, "schema", out schema, ref error))
            {
                return null;
            }
            status.Schema = schema;
            if (status.Schema != SupportedSchema)
            {
                error = "This companion understands panel status schema " + SupportedSchema
                    + ", but the panel wrote schema "
                    + status.Schema.ToString(CultureInfo.InvariantCulture)
                    + ". Update the companion to match this checkout.";
                return null;
            }

            string state, reason, message, url, origin, token;
            if (!TryReadString(fields, "state", out state, ref error)
                || !TryReadString(fields, "reason", out reason, ref error)
                || !TryReadString(fields, "message", out message, ref error)
                || !TryReadString(fields, "url", out url, ref error)
                || !TryReadString(fields, "origin", out origin, ref error)
                || !TryReadString(fields, "token", out token, ref error))
            {
                return null;
            }
            status.State = state;
            status.Reason = reason;
            status.Message = message;
            status.Url = url;
            status.Origin = origin;
            status.Token = token;

            // port and pid are absent from some failure documents, so they are
            // optional here and required below only for a ready document.
            int port, pid;
            if (!TryReadOptionalInt(fields, "port", out port, ref error)
                || !TryReadOptionalInt(fields, "pid", out pid, ref error))
            {
                return null;
            }
            status.Port = port;
            status.PanelProcessId = pid;

            bool ready = string.Equals(state, "ready", StringComparison.Ordinal);
            bool failed = string.Equals(state, "failed", StringComparison.Ordinal);
            if (!ready && !failed)
            {
                error = "The panel status document is neither ready nor failed.";
                return null;
            }

            if (ready && !ValidateReady(status, ref error))
            {
                return null;
            }

            return status;
        }

        /// <summary>
        /// A ready document is about to produce three consequences: a browser
        /// navigation, authenticated requests, and a shutdown signal aimed at
        /// a process id. Each of those needs a different field, so all of them
        /// are required, and they must describe one and the same panel.
        /// </summary>
        private static bool ValidateReady(PanelStatus status, ref string error)
        {
            if (string.IsNullOrEmpty(status.Url))
            {
                error = "The panel reported itself ready without an address.";
                return false;
            }
            if (string.IsNullOrEmpty(status.Token))
            {
                error = "The panel reported no connection token.";
                return false;
            }
            if (status.Port < 1 || status.Port > 65535)
            {
                error = "The panel reported an implausible port: "
                    + status.Port.ToString(CultureInfo.InvariantCulture) + ".";
                return false;
            }
            // The address is checked before anything else about the session,
            // because sending the token off-host is the worst thing a bad
            // document could achieve, and so it is the thing worth saying
            // first. Never hand anything but a loopback URL to the browser, no
            // matter what the document claims.
            if (!IsLoopbackHttpUrl(status.Url))
            {
                error = "The panel reported a non-loopback address; refusing to open it.";
                return false;
            }

            // The origin is what authenticated requests are sent to, so it is
            // validated in its own right rather than trusted because the URL
            // happened to look right.
            if (!IsLoopbackOrigin(status.Origin, status.Port))
            {
                error = "The panel reported an origin that is not http://127.0.0.1:"
                    + status.Port.ToString(CultureInfo.InvariantCulture) + "; refusing to use it.";
                return false;
            }

            // And the three must agree. The panel composes its URL as
            // origin + "/#token=" + token, so anything else means the document
            // was edited, truncated, or written by something that is not the
            // panel this companion understands.
            string expected = status.Origin + "/#token=" + status.Token;
            if (!string.Equals(status.Url, expected, StringComparison.Ordinal))
            {
                error = "The panel's address, origin and token do not describe the same session.";
                return false;
            }

            if (status.PanelProcessId <= 1)
            {
                // Nothing is ever signalled without this, and 0 or 1 would aim
                // a signal at init or at the whole process group.
                error = "The panel reported no usable process id.";
                return false;
            }

            return true;
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
            return IsLoopbackHost(uri.Host);
        }

        /// <summary>
        /// The exact origin authenticated requests may be sent to: loopback,
        /// http, the port the document reports, and nothing else -- no path,
        /// no query, no fragment, no credentials. Checked separately from the
        /// URL because this is the value that carries the token to the panel.
        /// </summary>
        public static bool IsLoopbackOrigin(string candidate, int expectedPort)
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
            if (!IsLoopbackHost(uri.Host))
            {
                return false;
            }
            if (uri.Port != expectedPort)
            {
                return false;
            }
            if (!string.IsNullOrEmpty(uri.Fragment) || !string.IsNullOrEmpty(uri.Query))
            {
                return false;
            }
            // Uri normalises an empty path to "/", so both forms are the bare
            // origin; anything else carries a path this must not post to.
            return uri.AbsolutePath == "/" || uri.AbsolutePath.Length == 0;
        }

        private static bool IsLoopbackHost(string host)
        {
            return string.Equals(host, "127.0.0.1", StringComparison.Ordinal)
                || string.Equals(host, "::1", StringComparison.Ordinal)
                || string.Equals(host, "[::1]", StringComparison.Ordinal)
                || string.Equals(host, "localhost", StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>
        /// Read a string field. Absent is acceptable; present but of another
        /// type is not -- a number or object where a token belongs means the
        /// document is not the one this companion knows how to read.
        /// </summary>
        private static bool TryReadString(
            IDictionary<string, object> fields, string key, out string value, ref string error)
        {
            value = null;
            object raw;
            if (!fields.TryGetValue(key, out raw) || raw == null)
            {
                return true;
            }
            string text = raw as string;
            if (text == null)
            {
                error = "The panel status document has a non-text \"" + key + "\" field.";
                return false;
            }
            value = text;
            return true;
        }

        private static bool TryReadInt(
            IDictionary<string, object> fields, string key, out int value, ref string error)
        {
            value = 0;
            object raw;
            if (!fields.TryGetValue(key, out raw) || raw == null)
            {
                error = "The panel status document has no \"" + key + "\" field.";
                return false;
            }
            return Convert(raw, key, out value, ref error);
        }

        private static bool TryReadOptionalInt(
            IDictionary<string, object> fields, string key, out int value, ref string error)
        {
            value = 0;
            object raw;
            if (!fields.TryGetValue(key, out raw) || raw == null)
            {
                return true;
            }
            return Convert(raw, key, out value, ref error);
        }

        /// <summary>
        /// Accept only a JSON integer. A quoted number is refused on purpose:
        /// these values choose a port to connect to and a process to signal,
        /// so a document that does not type them correctly is not trusted to
        /// have got their values right either.
        /// </summary>
        private static bool Convert(object raw, string key, out int value, ref string error)
        {
            value = 0;
            if (raw is int)
            {
                value = (int)raw;
                return true;
            }
            if (raw is long)
            {
                long wide = (long)raw;
                if (wide < int.MinValue || wide > int.MaxValue)
                {
                    error = "The panel status document has an out-of-range \"" + key + "\" field.";
                    return false;
                }
                value = (int)wide;
                return true;
            }
            error = "The panel status document has a non-numeric \"" + key + "\" field.";
            return false;
        }
    }
}
