using System;

namespace OmoBench.Companion
{
    /// <summary>
    /// The only thing this application accepts from the outside world: an
    /// <c>omobench://</c> URI handed to it by Windows when a web page or a
    /// shortcut activates the registered handler.
    ///
    /// Anyone can put such a link on any page, so the URI is untrusted input.
    /// This parser is deliberately a whitelist of one action with no
    /// parameters: nothing in a URI can select an executable, a path, a port,
    /// or any other argument, and nothing from a URI is ever concatenated
    /// into a command line. Everything the companion runs comes from the
    /// user's own saved configuration instead.
    /// </summary>
    public static class Protocol
    {
        public const string Scheme = "omobench";

        /// <summary>The single supported action: open the local panel.</summary>
        public const string ActionOpen = "open";

        public sealed class Result
        {
            public bool Accepted { get; private set; }
            public string Action { get; private set; }
            public string Rejection { get; private set; }

            public static Result Accept(string action)
            {
                return new Result { Accepted = true, Action = action, Rejection = null };
            }

            public static Result Reject(string rejection)
            {
                return new Result { Accepted = false, Action = null, Rejection = rejection };
            }
        }

        /// <summary>
        /// Decide whether a URI is the one action this companion performs.
        /// Accepts exactly "omobench://open" (case-insensitive scheme and
        /// action, optional single trailing slash). Everything else is
        /// rejected with a reason, including unknown actions, any query
        /// string, fragment, userinfo, port, or extra path segments.
        /// </summary>
        public static Result Parse(string candidate)
        {
            if (string.IsNullOrWhiteSpace(candidate))
            {
                return Result.Reject("No link was supplied.");
            }

            // Reject control characters outright rather than letting Uri
            // normalise them away.
            foreach (char character in candidate)
            {
                if (char.IsControl(character))
                {
                    return Result.Reject("The link contains control characters.");
                }
            }

            Uri uri;
            if (!Uri.TryCreate(candidate.Trim(), UriKind.Absolute, out uri))
            {
                return Result.Reject("The link is not a valid URI.");
            }

            if (!string.Equals(uri.Scheme, Scheme, StringComparison.OrdinalIgnoreCase))
            {
                return Result.Reject("Only omobench:// links are accepted.");
            }

            if (!string.IsNullOrEmpty(uri.UserInfo))
            {
                return Result.Reject("The link must not contain credentials.");
            }

            if (!uri.IsDefaultPort)
            {
                return Result.Reject("The link must not contain a port.");
            }

            if (!string.IsNullOrEmpty(uri.Query))
            {
                return Result.Reject("The link must not contain parameters.");
            }

            if (!string.IsNullOrEmpty(uri.Fragment))
            {
                return Result.Reject("The link must not contain a fragment.");
            }

            // For "omobench://open" the action lands in Authority; for
            // "omobench:open" it lands in the path. Accept both spellings of
            // the same single action, and nothing with more than one segment.
            string authority = uri.Authority ?? string.Empty;
            string path = (uri.AbsolutePath ?? string.Empty).Trim('/');

            string action;
            if (authority.Length > 0)
            {
                if (path.Length > 0)
                {
                    return Result.Reject("The link must not contain extra path segments.");
                }
                action = authority;
            }
            else
            {
                if (path.Contains("/"))
                {
                    return Result.Reject("The link must not contain extra path segments.");
                }
                action = path;
            }

            if (action.Length == 0)
            {
                return Result.Reject("The link does not name an action.");
            }

            if (!string.Equals(action, ActionOpen, StringComparison.OrdinalIgnoreCase))
            {
                return Result.Reject("Unsupported action '" + action + "'. This companion only opens the local panel.");
            }

            return Result.Accept(ActionOpen);
        }
    }
}
