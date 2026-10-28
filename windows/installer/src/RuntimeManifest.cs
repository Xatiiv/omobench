using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;

namespace OmoBench.Installer
{
    /// <summary>
    /// What a usable OmoBench payload has to contain, and the check that the
    /// one just unpacked actually does.
    ///
    /// The archive's checksum only proves it is the file this build produced.
    /// It says nothing about whether that file was built correctly -- an
    /// allowlist copy that silently matched nothing still produces a valid,
    /// correctly checksummed archive of almost nothing, and that has happened
    /// here before. A file count is no better: twenty empty directories and a
    /// handful of profiles would pass it while the validator and the panel's
    /// web assets were missing.
    ///
    /// So the contents are checked by name: the paths the installed program
    /// reads at run time, and a manifest of every file with its own checksum.
    /// The required list lives here, compiled into the installer, rather than
    /// travelling inside the payload -- a payload cannot be its own authority
    /// on what it was supposed to contain.
    ///
    /// The list below is also read by windows/installer/build.ps1, which
    /// checks the archive it has just produced before that archive is embedded
    /// into anything. Keep one quoted path per line between the markers.
    /// </summary>
    public static class RuntimeManifest
    {
        /// <summary>The manifest the build writes into the payload root.</summary>
        public const string FileName = "MANIFEST.sha256";

        // required-begin
        private static readonly string[] Required =
        {
            "LICENSE",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
            "cli/LICENSE",
            "cli/NOTICE",
            "cli/pyproject.toml",
            "cli/src/omobench/__init__.py",
            "cli/src/omobench/cli.py",
            "cli/src/omobench/launcher.py",
            "cli/src/omobench/managed_setup.py",
            "cli/src/omobench/telemetry.py",
            "cli/src/omobench/ui.py",
            "cli/src/omobench/ui_managed.py",
            "cli/src/omobench/ui_submission.py",
            "cli/src/omobench/github_auth.py",
            "cli/src/omobench/community_submission.py",
            "cli/src/omobench/web/index.html",
            "cli/src/omobench/web/app.js",
            "cli/src/omobench/web/community.js",
            "cli/src/omobench/web/style.css",
            "cli/src/omobench/web/guided.html",
            "cli/src/omobench/web/guided.js",
            "cli/src/omobench/web/guided.css",
            "benchmarks/profiles/chat-small-v1.yaml",
            "benchmarks/profiles/chat-long-v1.yaml",
            "benchmarks/profiles/throughput-v1.yaml",
            "catalog/models/seed-v1.yaml",
            "catalog/downloads/cpu-v1.json",
            "schema/benchmark-run.schema.json",
            "tools/validate_results.py",
        };
        // required-end

        public static string[] RequiredPaths
        {
            get { return (string[])Required.Clone(); }
        }

        /// <summary>
        /// Which required paths are absent from the given set. The comparison
        /// is by name, so it works equally on a tar listing and on a directory
        /// walk of the extracted copy.
        /// </summary>
        public static List<string> MissingFrom(IEnumerable<string> paths)
        {
            Dictionary<string, bool> present = new Dictionary<string, bool>(
                StringComparer.OrdinalIgnoreCase);
            if (paths != null)
            {
                foreach (string path in paths)
                {
                    string normalised = Normalise(path);
                    if (normalised.Length > 0) { present[normalised] = true; }
                }
            }

            List<string> missing = new List<string>();
            foreach (string required in Required)
            {
                if (!present.ContainsKey(required)) { missing.Add(required); }
            }
            return missing;
        }

        /// <summary>
        /// Strip a leading archive root and normalise separators, so
        /// "omobench/cli/pyproject.toml", ".\cli\pyproject.toml" and
        /// "cli/pyproject.toml" are one path.
        /// </summary>
        public static string Normalise(string path)
        {
            string text = (path ?? "").Replace('\\', '/').Trim();
            while (text.StartsWith("./", StringComparison.Ordinal)) { text = text.Substring(2); }
            if (text.StartsWith("omobench/", StringComparison.OrdinalIgnoreCase))
            {
                text = text.Substring("omobench/".Length);
            }
            return text.TrimStart('/');
        }

        public sealed class Entry
        {
            public string Sha256;
            public string Path;
        }

        /// <summary>
        /// Read a manifest. Lines are "&lt;sha256&gt;  &lt;relative path&gt;",
        /// the format sha256sum writes, so the same file can be checked by
        /// hand on either side of the install.
        /// </summary>
        public static List<Entry> Parse(string text, out string failure)
        {
            failure = null;
            List<Entry> entries = new List<Entry>();
            foreach (string rawLine in (text ?? "").Replace("\r", "").Split('\n'))
            {
                string line = rawLine.Trim();
                if (line.Length == 0 || line[0] == '#') { continue; }

                int split = line.IndexOf(' ');
                if (split <= 0)
                {
                    failure = "The manifest has a line that is not \"checksum path\": " + line;
                    return null;
                }
                string hash = line.Substring(0, split).Trim();
                string path = Normalise(line.Substring(split).TrimStart(' ', '*'));
                if (hash.Length != 64 || path.Length == 0)
                {
                    failure = "The manifest has a line that is not \"checksum path\": " + line;
                    return null;
                }
                // A manifest is data from a file; it must not be able to point
                // the check, or anything later, outside the unpacked copy.
                if (path.Contains("../") || path.StartsWith("/", StringComparison.Ordinal)
                    || path.IndexOf(':') >= 0)
                {
                    failure = "The manifest names a path outside the unpacked source: " + path;
                    return null;
                }
                entries.Add(new Entry { Sha256 = hash.ToLowerInvariant(), Path = path });
            }

            if (entries.Count == 0)
            {
                failure = "The manifest is empty.";
                return null;
            }
            return entries;
        }

        /// <summary>
        /// Check an unpacked copy against its manifest and against what the
        /// program actually needs.
        ///
        /// This runs before the Python environment is created, so an
        /// incomplete payload is reported as an incomplete payload rather than
        /// as a confusing pip or import failure several minutes later.
        /// </summary>
        public static bool Validate(string extractedRoot, out string failure)
        {
            failure = null;
            string manifestPath = Path.Combine(extractedRoot, FileName);
            string text;
            try
            {
                if (!File.Exists(manifestPath))
                {
                    failure = "The unpacked source has no " + FileName + ", so its contents cannot be "
                        + "checked. This installer's payload was not produced by a complete build.";
                    return false;
                }
                text = File.ReadAllText(manifestPath);
            }
            catch (Exception error)
            {
                failure = "The unpacked source's " + FileName + " could not be read: " + error.Message;
                return false;
            }

            List<Entry> entries = Parse(text, out failure);
            if (entries == null) { return false; }

            List<string> listed = new List<string>();
            foreach (Entry entry in entries) { listed.Add(entry.Path); }

            List<string> missingRequired = MissingFrom(listed);
            if (missingRequired.Count > 0)
            {
                failure = "The bundled OmoBench source is missing "
                    + Count(missingRequired.Count, "file") + " the program needs:\r\n    "
                    + string.Join("\r\n    ", missingRequired.ToArray())
                    + "\r\n\r\nNothing was installed. Use a released installer, or rebuild this one.";
                return false;
            }

            List<string> absent = new List<string>();
            List<string> wrong = new List<string>();
            foreach (Entry entry in entries)
            {
                string file = Path.Combine(extractedRoot, entry.Path.Replace('/', Path.DirectorySeparatorChar));
                try
                {
                    if (!File.Exists(file)) { absent.Add(entry.Path); continue; }
                    string actual = RuntimePayload.Sha256(File.ReadAllBytes(file));
                    if (!string.Equals(actual, entry.Sha256, StringComparison.OrdinalIgnoreCase))
                    {
                        wrong.Add(entry.Path);
                    }
                }
                catch (Exception error)
                {
                    failure = "The unpacked file " + entry.Path + " could not be read: " + error.Message;
                    return false;
                }
                if (absent.Count + wrong.Count >= 10) { break; }
            }

            if (absent.Count > 0)
            {
                failure = "The unpacked source is missing " + Count(absent.Count, "file")
                    + " its own manifest lists:\r\n    "
                    + string.Join("\r\n    ", absent.ToArray());
                return false;
            }
            if (wrong.Count > 0)
            {
                failure = "The unpacked source does not match its own manifest:\r\n    "
                    + string.Join("\r\n    ", wrong.ToArray())
                    + "\r\n\r\nThe files on disk differ from the ones this installer was built with.";
                return false;
            }
            return true;
        }

        private static string Count(int number, string noun)
        {
            return number.ToString(CultureInfo.InvariantCulture) + " " + noun + (number == 1 ? "" : "s");
        }
    }
}
