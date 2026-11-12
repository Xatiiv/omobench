using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Management;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using Microsoft.Win32;

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
    /// Deliberately narrow. It reads five CIM classes and, read-only, one
    /// registry value per display adapter, prints one document and exits. It
    /// opens no listener, starts no process, writes no file, reads no
    /// configuration, writes no registry key and reaches no network. It cannot
    /// be reached from a URI: Windows hands a protocol activation the whole URI
    /// as one argument, and every URI begins with its scheme, so no link can
    /// spell "--hardware-probe-v1".
    ///
    /// Video memory is read from where the driver itself records it: the
    /// 64-bit HardwareInformation.qwMemorySize value under the adapter's display
    /// class key, which is the card's dedicated memory. AdapterRAM is never
    /// used -- it is a 32-bit field, capped and wrong under modern drivers, and
    /// reports 4 GB for a 16 GB RX 9070. Shared system memory a GPU may borrow
    /// is not dedicated memory and is never reported as it. Where the value is
    /// absent the memory is null: unknown, which is not zero.
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
            int? cores, threads;
            ProcessorCounts(out cores, out threads);
            document["cpu_cores"] = cores;
            document["cpu_threads"] = threads;
            document["ram_gb"] = PhysicalMemoryGb();
            document["ram_installed_gb"] = InstalledMemoryGb();
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
        /// Physical cores and logical threads, summed over every socket. Null
        /// when the class does not say, rather than a guess from the name.
        /// </summary>
        private static void ProcessorCounts(out int? cores, out int? threads)
        {
            int coreTotal = 0, threadTotal = 0;
            bool any = false;
            foreach (ManagementObject entry in Query(
                "SELECT NumberOfCores, NumberOfLogicalProcessors FROM Win32_Processor"))
            {
                uint value;
                if (UInt32.TryParse(Convert.ToString(entry["NumberOfCores"], CultureInfo.InvariantCulture), out value))
                {
                    coreTotal += (int)value; any = true;
                }
                if (UInt32.TryParse(Convert.ToString(entry["NumberOfLogicalProcessors"], CultureInfo.InvariantCulture), out value))
                {
                    threadTotal += (int)value;
                }
            }
            cores = any && coreTotal > 0 ? (int?)coreTotal : null;
            threads = threadTotal > 0 ? (int?)threadTotal : null;
        }

        /// <summary>
        /// The memory modules installed, summed -- 32 GB for two 16 GB sticks --
        /// which is what a person calls their RAM. TotalPhysicalMemory, above,
        /// is what Windows can use after reservations, and is kept separately.
        /// </summary>
        private static object InstalledMemoryGb()
        {
            ulong total = 0;
            foreach (ManagementObject entry in Query("SELECT Capacity FROM Win32_PhysicalMemory"))
            {
                ulong bytes;
                if (ulong.TryParse(Convert.ToString(entry["Capacity"], CultureInfo.InvariantCulture), out bytes))
                {
                    total += bytes;
                }
            }
            if (total == 0) { return null; }
            return Math.Round(total / 1073741824.0, 2);
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
            Dictionary<string, Queue<double?>> dedicated = DedicatedMemoryByAdapter();
            foreach (ManagementObject entry in Query("SELECT Name, DriverVersion FROM Win32_VideoController"))
            {
                string name = Text(entry["Name"]);
                if (name == null || IsNonPhysical(name)) { continue; }
                Dictionary<string, object> device = new Dictionary<string, object>();
                device["vendor"] = Vendor(name);
                device["model"] = name;
                // Never AdapterRAM: see the note on this class. Two identical
                // cards each have their own key, taken in order.
                Queue<double?> values;
                device["memory_gb"] = dedicated.TryGetValue(name, out values) && values.Count > 0
                    ? (object)values.Dequeue() : null;
                device["count"] = 1;
                device["driver_version"] = Text(entry["DriverVersion"]);
                found.Add(device);
            }
            return found;
        }

        private const string DisplayClass =
            @"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}";

        /// <summary>
        /// Dedicated video memory per adapter name, from the driver's own
        /// record. Read-only; a key this account cannot open is skipped.
        /// </summary>
        private static Dictionary<string, Queue<double?>> DedicatedMemoryByAdapter()
        {
            Dictionary<string, Queue<double?>> found = new Dictionary<string, Queue<double?>>(StringComparer.OrdinalIgnoreCase);
            try
            {
                using (RegistryKey root = Registry.LocalMachine.OpenSubKey(DisplayClass, false))
                {
                    if (root == null) { return found; }
                    foreach (string child in root.GetSubKeyNames())
                    {
                        if (!Regex.IsMatch(child, @"^\d{4}$")) { continue; }
                        try
                        {
                            using (RegistryKey adapter = root.OpenSubKey(child, false))
                            {
                                if (adapter == null) { continue; }
                                string name = Text(adapter.GetValue("DriverDesc"));
                                if (name == null) { continue; }
                                double? gigabytes = DedicatedGigabytes(adapter.GetValue("HardwareInformation.qwMemorySize"));
                                Queue<double?> queue;
                                if (!found.TryGetValue(name, out queue)) { queue = new Queue<double?>(); found[name] = queue; }
                                queue.Enqueue(gigabytes);
                            }
                        }
                        catch (System.Security.SecurityException) { }
                        catch (UnauthorizedAccessException) { }
                    }
                }
            }
            catch (System.Security.SecurityException) { }
            catch (UnauthorizedAccessException) { }
            return found;
        }

        /// <summary>
        /// A qwMemorySize value as gigabytes, or null. The value is a REG_QWORD
        /// (a long) on current drivers and REG_BINARY on some older ones; zero
        /// or anything else unreadable is unknown, not zero memory.
        /// </summary>
        internal static double? DedicatedGigabytes(object value)
        {
            ulong bytes = 0;
            if (value is long) { bytes = unchecked((ulong)(long)value); }
            else if (value is int) { bytes = unchecked((uint)(int)value); }
            else
            {
                byte[] binary = value as byte[];
                if (binary != null && binary.Length >= 8) { bytes = BitConverter.ToUInt64(binary, 0); }
                else if (binary != null && binary.Length >= 4) { bytes = BitConverter.ToUInt32(binary, 0); }
            }
            if (bytes == 0) { return null; }
            return Math.Round(bytes / 1073741824.0, 2);
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
