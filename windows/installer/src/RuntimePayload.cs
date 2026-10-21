using System;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;

namespace OmoBench.Installer
{
    /// <summary>
    /// The OmoBench runtime source, carried inside the installer.
    ///
    /// It used to be downloaded from GitHub at install time, which made the
    /// whole managed path depend on the repository being publicly readable by
    /// an anonymous request -- and on the network being there at exactly the
    /// wrong moment. Carrying it removes both dependencies and makes the
    /// installer one self-describing artifact: the companion inside it and
    /// this archive come from the same build, so they cannot disagree about
    /// which revision they are.
    ///
    /// It is still not an offline installer. Python dependencies, including
    /// the pinned benchmark runner, are installed from PyPI and need a
    /// network connection.
    /// </summary>
    public static class RuntimePayload
    {
        public const string ResourceName = "OmoBenchRuntimeSource.tar.gz";

        /// <summary>Opens the embedded archive. Replaced by tests only.</summary>
        internal static Func<Stream> Opener = OpenEmbedded;

        /// <summary>The checksum the build recorded. Replaced by tests only.</summary>
        internal static string ExpectedSha256 = BuildStamp.RuntimeSourceSha256;

        /// <summary>The length the build recorded. Replaced by tests only.</summary>
        internal static long ExpectedBytes = BuildStamp.RuntimeSourceBytes;

        private static Stream OpenEmbedded()
        {
            Assembly assembly = Assembly.GetExecutingAssembly();
            foreach (string name in assembly.GetManifestResourceNames())
            {
                if (name.EndsWith(ResourceName, StringComparison.OrdinalIgnoreCase))
                {
                    return assembly.GetManifestResourceStream(name);
                }
            }
            return null;
        }

        /// <summary>
        /// Write the archive to <paramref name="destinationFile"/>, but only
        /// if it is exactly what this build produced.
        ///
        /// The checksum is checked before the file is handed to anything that
        /// unpacks it. An archive that has been truncated, substituted or
        /// corrupted on its way here is refused rather than extracted over a
        /// directory and installed; "it looked like a tar.gz" is not a
        /// standard worth meeting for code about to be run.
        /// </summary>
        public static bool TryWrite(string destinationFile, out string failure)
        {
            failure = null;
            byte[] payload;

            try
            {
                using (Stream stream = Opener())
                {
                    if (stream == null)
                    {
                        failure = "This installer does not carry the OmoBench runtime source. "
                            + "The build is incomplete; use a released installer.";
                        return false;
                    }
                    using (MemoryStream buffer = new MemoryStream())
                    {
                        byte[] chunk = new byte[64 * 1024];
                        int read;
                        while ((read = stream.Read(chunk, 0, chunk.Length)) > 0)
                        {
                            buffer.Write(chunk, 0, read);
                        }
                        payload = buffer.ToArray();
                    }
                }
            }
            catch (Exception error)
            {
                failure = "The embedded runtime source could not be read: " + error.Message;
                return false;
            }

            if (payload.Length == 0)
            {
                failure = "The embedded runtime source is empty.";
                return false;
            }

            if (ExpectedBytes > 0 && payload.Length != ExpectedBytes)
            {
                failure = "The embedded runtime source is "
                    + payload.Length.ToString(CultureInfo.InvariantCulture)
                    + " bytes, but this build recorded "
                    + ExpectedBytes.ToString(CultureInfo.InvariantCulture) + ".";
                return false;
            }

            string actual = Sha256(payload);
            if (string.IsNullOrEmpty(ExpectedSha256))
            {
                failure = "This build recorded no checksum for the runtime source, so it cannot be "
                    + "verified before use.";
                return false;
            }
            if (!string.Equals(actual, ExpectedSha256, StringComparison.OrdinalIgnoreCase))
            {
                failure = "The embedded runtime source does not match the checksum recorded when this "
                    + "installer was built.\r\n    expected " + ExpectedSha256 + "\r\n    found    " + actual;
                return false;
            }

            try
            {
                string directory = Path.GetDirectoryName(destinationFile);
                if (!string.IsNullOrEmpty(directory)) { Directory.CreateDirectory(directory); }
                File.WriteAllBytes(destinationFile, payload);
                return true;
            }
            catch (Exception error)
            {
                failure = "The runtime source could not be written to " + destinationFile
                    + ": " + error.Message;
                return false;
            }
        }

        public static string Sha256(byte[] payload)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                byte[] digest = algorithm.ComputeHash(payload);
                System.Text.StringBuilder text = new System.Text.StringBuilder(digest.Length * 2);
                foreach (byte value in digest)
                {
                    text.Append(value.ToString("x2", CultureInfo.InvariantCulture));
                }
                return text.ToString();
            }
        }
    }
}
