using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;

namespace OmoBench.Installer
{
    internal static class LicenseFiles
    {
        internal static readonly string[] Names =
            { "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md" };

        // Read the complete set before installation writes anything. A broken
        // build must not install a binary without its accompanying terms.
        internal static Dictionary<string, byte[]> ReadAll()
        {
            Dictionary<string, byte[]> files = new Dictionary<string, byte[]>();
            Assembly assembly = Assembly.GetExecutingAssembly();
            foreach (string name in Names)
            {
                using (Stream source = assembly.GetManifestResourceStream("OmoBench.Legal." + name))
                {
                    if (source == null) { throw new InvalidDataException("Missing embedded legal file: " + name); }
                    using (MemoryStream buffer = new MemoryStream())
                    {
                        source.CopyTo(buffer);
                        byte[] bytes = buffer.ToArray();
                        if (bytes.Length == 0) { throw new InvalidDataException("Empty embedded legal file: " + name); }
                        files.Add(name, bytes);
                    }
                }
            }
            return files;
        }
    }
}
