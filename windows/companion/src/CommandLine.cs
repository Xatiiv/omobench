using System;
using System.Collections.Generic;
using System.Text;

namespace OmoBench.Companion
{
    /// <summary>
    /// Builds Windows command lines for the processes this companion starts.
    ///
    /// .NET Framework's ProcessStartInfo exposes only a single Arguments
    /// string, so the quoting has to be done here, to the same rules
    /// CommandLineToArgvW applies when the receiving program parses it back.
    /// This matters for ordinary setups: checkout and virtual-environment
    /// paths routinely contain spaces.
    ///
    /// Note what this is not: it is not shell escaping, because nothing here
    /// goes through a shell. wsl.exe parses its own command line and execs the
    /// Linux program directly, so there is no cmd.exe, no /bin/sh, and no
    /// metacharacter to escape. Values still come only from saved
    /// configuration, never from a URI.
    /// </summary>
    public static class CommandLine
    {
        /// <summary>
        /// Quote one argument so CommandLineToArgvW yields it back verbatim.
        /// </summary>
        public static string Quote(string argument)
        {
            if (argument == null)
            {
                throw new ArgumentNullException("argument");
            }

            // An argument with no space, tab, quote or vertical tab needs no
            // quoting at all -- but an empty one still needs "".
            if (argument.Length > 0 && argument.IndexOfAny(new[] { ' ', '\t', '"', '\n', '\v' }) < 0)
            {
                return argument;
            }

            StringBuilder quoted = new StringBuilder();
            quoted.Append('"');
            for (int index = 0; index < argument.Length; index++)
            {
                int backslashes = 0;
                while (index < argument.Length && argument[index] == '\\')
                {
                    backslashes++;
                    index++;
                }

                if (index == argument.Length)
                {
                    // Backslashes before the closing quote must be doubled so
                    // they are not read as escaping it.
                    quoted.Append('\\', backslashes * 2);
                    break;
                }

                if (argument[index] == '"')
                {
                    quoted.Append('\\', backslashes * 2 + 1).Append('"');
                }
                else
                {
                    quoted.Append('\\', backslashes).Append(argument[index]);
                }
            }

            quoted.Append('"');
            return quoted.ToString();
        }

        /// <summary>Join arguments into one correctly quoted command line.</summary>
        public static string Join(IEnumerable<string> arguments)
        {
            if (arguments == null)
            {
                throw new ArgumentNullException("arguments");
            }

            StringBuilder line = new StringBuilder();
            foreach (string argument in arguments)
            {
                if (line.Length > 0)
                {
                    line.Append(' ');
                }
                line.Append(Quote(argument));
            }
            return line.ToString();
        }

        /// <summary>
        /// Translate a Windows path to the /mnt/&lt;drive&gt;/... form WSL sees.
        /// Returns false for anything that is not a plain local drive path --
        /// a UNC share, a bare relative path, or a device path -- rather than
        /// guessing at a translation that would silently point somewhere else.
        /// </summary>
        public static bool TryToWslPath(string windowsPath, out string wslPath)
        {
            wslPath = null;
            if (string.IsNullOrWhiteSpace(windowsPath))
            {
                return false;
            }

            string path = windowsPath.Trim();
            if (path.StartsWith(@"\\", StringComparison.Ordinal))
            {
                return false; // UNC / device paths have no /mnt equivalent.
            }

            if (path.Length < 3 || path[1] != ':' || (path[2] != '\\' && path[2] != '/'))
            {
                return false;
            }

            char drive = char.ToLowerInvariant(path[0]);
            if (drive < 'a' || drive > 'z')
            {
                return false;
            }

            string remainder = path.Substring(3).Replace('\\', '/').TrimEnd('/');
            wslPath = remainder.Length == 0 ? "/mnt/" + drive : "/mnt/" + drive + "/" + remainder;
            return true;
        }
    }
}
