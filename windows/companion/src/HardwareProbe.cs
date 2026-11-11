using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Management;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace OmoBench.Companion
{
    /// <summary>
    /// Reads this Windows machine's serving hardware and prints it as JSON.
    ///
    /// It exists because of one specific gap. When the model server runs on
    /// Windows and the benchmark runner runs inside WSL2, the runner can see
    /// only the distribution: its processor string, its memory, and no GPU at
    /// all. None of that describes the machine that served the model, and
    /// publishing it as though it did would be the central dishonesty this
    /// whole schema exists to prevent. So the facts are read here, on the
    /// machine that owns them, and recorded as detected.
    ///
    /// Deliberately narrow. It reads four CIM classes, prints one document and
    /// exits. It opens no listener, starts no process, writes no file, reads no
    /// configuration, touches no registry key and reaches no network. It cannot
    /// be reached from a URI: Windows hands a protocol activation the whole URI
    /// as one argument, and every URI begins with its scheme, so no link can
    /// spell "--hardware-probe-v1".
    ///
    /// What it does not report is as deliberate. AdapterRAM is capped and wrong
    /// under modern drivers -- it reports 4 GB for a 16 GB RX 9070 -- so video
    /// memory is always null here and has to come from somewhere that knows.
    /// And finding a card is not evidence that inference used it, so nothing
    /// here claims a role for one.
    /// </summary>
    internal static class HardwareProbe
    {
        internal const string Kind = "omobench-serving-hardware-probe-v1";

        // Adapters Windows reports that are not a physical accelerator.
        private static readonly string[] NonPhysical = new[]
        {
            "microsoft basic display", "microsoft remote display", "remote desktop",
            "citrix", "vnc", "virtualbox", "vmware svga", "parallels display",
            "hyper-v video", "dameware", "teamviewer",
        };

        internal static int Run()
        {
            try
            {
                string json = new JavaScriptSerializer().Serialize(Collect());
                using (Stream output = Console.OpenStandardOutput())
                using (StreamWriter writer = new StreamWriter(output, new UTF8Encoding(false)))
                {
                    writer.Write(json);
                    writer.Flush();
                }
                return 0;
            }
            catch (Exception error)
            {
                Diagnostics.Write("--hardware-probe-v1 failed: " + error.Message);
                return 1;
            }
        }

        internal static Dictionary<string, object> Collect()
        {
            Dictionary<string, object> document = new Dictionary<string, object>();
            document["kind"] = Kind;
            document["os"] = OperatingSystem();
            document["cpu"] = Processor();
            document["ram_gb"] = PhysicalMemoryGb();
            document["accelerators"] = Accelerators();
            return document;
        }

        private static string OperatingSystem()
        {
            foreach (ManagementObject entry in Query("SELECT Caption, Version FROM Win32_OperatingSystem"))
            {
                string caption = Text(entry["Caption"]);
                string version = Text(entry["Version"]);
                if (caption == null) { continue; }
                return version == null ? caption : caption + " " + version;
            }
            return null;
        }

        private static string Processor()
        {
            foreach (ManagementObject entry in Query("SELECT Name FROM Win32_Processor"))
            {
                string name = Text(entry["Name"]);
                if (name != null) { return name; }
            }
            return null;
        }

        /// <summary>
        /// Installed physical memory, from a 64-bit field. Rounded to two
        /// decimals, the same way the Linux and macOS side rounds it, so the
        /// same machine does not read as two different ones.
        /// </summary>
        private static object PhysicalMemoryGb()
        {
            foreach (ManagementObject entry in Query("SELECT TotalPhysicalMemory FROM Win32_ComputerSystem"))
            {
                object raw = entry["TotalPhysicalMemory"];
                if (raw == null) { continue; }
                ulong bytes;
                if (!ulong.TryParse(Convert.ToString(raw, CultureInfo.InvariantCulture), out bytes) || bytes == 0)
                {
                    continue;
                }
                return Math.Round(bytes / 1073741824.0, 2);
            }
            return null;
        }

        private static List<Dictionary<string, object>> Accelerators()
        {
            List<Dictionary<string, object>> found = new List<Dictionary<string, object>>();
            foreach (ManagementObject entry in Query("SELECT Name, DriverVersion FROM Win32_VideoController"))
            {
                string name = Text(entry["Name"]);
                if (name == null || IsNonPhysical(name)) { continue; }
                Dictionary<string, object> device = new Dictionary<string, object>();
                device["vendor"] = Vendor(name);
                device["model"] = name;
                // Never AdapterRAM: see the note on this class.
                device["memory_gb"] = null;
                device["count"] = 1;
                device["driver_version"] = Text(entry["DriverVersion"]);
                found.Add(device);
            }
            return found;
        }

        internal static bool IsNonPhysical(string name)
        {
            string folded = name.ToLowerInvariant();
            foreach (string marker in NonPhysical)
            {
                if (folded.Contains(marker)) { return true; }
            }
            return false;
        }

        /// <summary>
        /// Vendor-neutral, and "other" rather than a guess when the name says
        /// nothing recognisable. The same four vendors the result schema has.
        /// </summary>
        internal static string Vendor(string name)
        {
            string folded = name.ToLowerInvariant();
            if (Regex.IsMatch(folded, @"\bnvidia\b|\bgeforce\b|\bquadro\b|\btesla\b|\brtx\b|\bgtx\b")) { return "nvidia"; }
            if (Regex.IsMatch(folded, @"\bamd\b|\bradeon\b|\bfirepro\b|\binstinct\b")) { return "amd"; }
            if (Regex.IsMatch(folded, @"\bintel\b|\barc\b|\biris\b")) { return "intel"; }
            return "other";
        }

        private static IEnumerable<ManagementObject> Query(string statement)
        {
            List<ManagementObject> results = new List<ManagementObject>();
            try
            {
                using (ManagementObjectSearcher searcher = new ManagementObjectSearcher(statement))
                using (ManagementObjectCollection collection = searcher.Get())
                {
                    foreach (ManagementBaseObject item in collection)
                    {
                        ManagementObject entry = item as ManagementObject;
                        if (entry != null) { results.Add(entry); }
                    }
                }
            }
            catch (ManagementException) { }
            catch (UnauthorizedAccessException) { }
            // A class this machine cannot answer leaves its fact unknown, which
            // the result records as unknown rather than filling in.
            return results;
        }

        private static string Text(object value)
        {
            string text = value as string;
            if (text == null) { return null; }
            text = text.Trim();
            return text.Length == 0 ? null : text;
        }
    }
}
