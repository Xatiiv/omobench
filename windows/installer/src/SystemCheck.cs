using System;
using System.Collections.Generic;
using System.Globalization;
using Microsoft.Win32;
using OmoBench.Companion;

namespace OmoBench.Installer
{
    public enum SupportLevel
    {
        /// <summary>Everything needed is present.</summary>
        Ready,

        /// <summary>Supported machine, but something still has to be set up.</summary>
        NeedsSetup,

        /// <summary>This machine cannot run OmoBench's benchmark path.</summary>
        Unsupported,
    }

    public sealed class SystemReport
    {
        public SupportLevel Level;
        public string Headline;
        public string Explanation;
        public string WindowsName;
        public int WindowsBuild;
        public bool WslPresent;
        public readonly List<WslDistribution> Distributions = new List<WslDistribution>();

        public bool HasWsl2Distribution
        {
            get
            {
                foreach (WslDistribution distribution in Distributions)
                {
                    if (distribution.Version == 2) { return true; }
                }
                return false;
            }
        }
    }

    /// <summary>
    /// Whether this machine can run OmoBench, answered before anything is
    /// installed.
    ///
    /// The supported configuration is stated rather than implied: 64-bit
    /// Windows 10 version 2004 (build 19041) or later, or Windows 11, with
    /// WSL2. That is the configuration in which `wsl --install` exists and
    /// WSL2 is available, and the benchmark runner needs WSL2 because its
    /// dependency set includes uvloop, which has no native Windows build.
    ///
    /// An unsupported machine is told so plainly and early. Half-installing
    /// and failing later would waste the person's time and leave files behind.
    /// </summary>
    public static class SystemCheck
    {
        /// <summary>Windows 10 2004. Below this, `wsl --install` does not exist.</summary>
        public const int MinimumWindowsBuild = 19041;

        /// <summary>Replaced by tests only.</summary>
        internal static Func<int> WindowsBuildProvider = ReadWindowsBuild;
        internal static Func<string> WindowsNameProvider = ReadWindowsName;
        internal static Func<bool> Is64BitProvider = () => Environment.Is64BitOperatingSystem;
        internal static Func<bool> WslPresentProvider = () => Wsl.IsAvailable;
        internal static Func<List<WslDistribution>> DistributionProvider = Wsl.ListDistributions;

        public static SystemReport Inspect()
        {
            SystemReport report = new SystemReport();
            report.WindowsBuild = Safe(WindowsBuildProvider, 0);
            report.WindowsName = Safe(WindowsNameProvider, "Windows");

            if (!Safe(Is64BitProvider, true))
            {
                report.Level = SupportLevel.Unsupported;
                report.Headline = "This is a 32-bit version of Windows.";
                report.Explanation =
                    "WSL2 requires 64-bit Windows, and the benchmark runner only exists for 64-bit "
                    + "Linux. OmoBench cannot run benchmarks on this machine.\r\n\r\n"
                    + "You can still browse published results on the OmoBench website.";
                return report;
            }

            if (report.WindowsBuild > 0 && report.WindowsBuild < MinimumWindowsBuild)
            {
                report.Level = SupportLevel.Unsupported;
                report.Headline = report.WindowsName + " is older than OmoBench supports.";
                report.Explanation =
                    "OmoBench needs Windows 10 version 2004 (build "
                    + MinimumWindowsBuild.ToString(CultureInfo.InvariantCulture)
                    + ") or later, or Windows 11. This machine reports build "
                    + report.WindowsBuild.ToString(CultureInfo.InvariantCulture) + ".\r\n\r\n"
                    + "Earlier versions cannot install WSL2, which the benchmark runner requires. "
                    + "Updating Windows through Settings > Windows Update is the way forward; "
                    + "nothing OmoBench installs can work around it.";
                return report;
            }

            report.WslPresent = Safe(WslPresentProvider, false);
            if (!report.WslPresent)
            {
                report.Level = SupportLevel.NeedsSetup;
                report.Headline = "WSL is not installed yet.";
                report.Explanation = WslInstallGuidance();
                return report;
            }

            List<WslDistribution> distributions = Safe(DistributionProvider, new List<WslDistribution>());
            report.Distributions.AddRange(distributions);

            if (distributions.Count == 0)
            {
                report.Level = SupportLevel.NeedsSetup;
                report.Headline = "WSL is installed, but has no Linux distribution.";
                report.Explanation = DistributionGuidance();
                return report;
            }

            if (!report.HasWsl2Distribution)
            {
                report.Level = SupportLevel.NeedsSetup;
                report.Headline = "The installed Linux distributions are WSL version 1.";
                report.Explanation =
                    "OmoBench needs WSL2. Convert an existing distribution from a Windows terminal:\r\n\r\n"
                    + "    wsl --set-version <name> 2\r\n\r\n"
                    + "The conversion can take several minutes and needs free disk space roughly equal "
                    + "to the size of the distribution. Then come back here and press Check again.";
                return report;
            }

            report.Level = SupportLevel.Ready;
            report.Headline = "This machine is ready for OmoBench.";
            report.Explanation = report.WindowsName + ", WSL2 available with "
                + distributions.Count.ToString(CultureInfo.InvariantCulture) + " distribution(s).";
            return report;
        }

        /// <summary>
        /// What to do when WSL is missing.
        ///
        /// The installer does not run `wsl --install` itself. That command
        /// needs administrator rights, enables Windows features, and usually
        /// requires a restart before it finishes; driving it from a per-user
        /// installer would mean asking for elevation the rest of this program
        /// deliberately never needs, and would still not survive the reboot.
        /// So it explains the supported Microsoft procedure and then re-checks
        /// the real state of the machine afterwards.
        /// </summary>
        public static string WslInstallGuidance()
        {
            return
                "OmoBench runs its benchmarks inside WSL2 (Windows Subsystem for Linux). Installing "
                + "WSL is a Windows operation that needs administrator rights and a restart, so it is "
                + "done once, by you, outside this installer.\r\n\r\n"
                + "1. Open Terminal, Command Prompt or PowerShell as administrator.\r\n"
                + "   (Right-click the Start button, choose the \"(Admin)\" entry.)\r\n"
                + "2. Run:\r\n"
                + "       wsl --install\r\n"
                + "3. Restart Windows when it asks. This step is required; WSL is not usable until the "
                + "restart finishes.\r\n"
                + "4. After the restart, Ubuntu opens by itself and asks you to create a Linux username "
                + "and password. These are new credentials for the Linux environment -- they are not your "
                + "Windows account, and they can be different.\r\n"
                + "5. Come back here and press \"Check again\".\r\n\r\n"
                + "This installer re-checks the real state of the machine; it does not assume the steps "
                + "worked. Microsoft's instructions: https://learn.microsoft.com/windows/wsl/install";
        }

        public static string DistributionGuidance()
        {
            return
                "WSL itself is present, but there is no Linux distribution for OmoBench to use.\r\n\r\n"
                + "1. Open Terminal, Command Prompt or PowerShell.\r\n"
                + "2. Run:\r\n"
                + "       wsl --install -d Ubuntu\r\n"
                + "3. When it finishes, Ubuntu opens and asks you to create a Linux username and "
                + "password. These are new credentials for the Linux environment, separate from your "
                + "Windows account.\r\n"
                + "4. Come back here and press \"Check again\".\r\n\r\n"
                + "Administrator rights are usually not needed for this step if WSL is already installed.";
        }

        private static T Safe<T>(Func<T> provider, T fallback)
        {
            try { return provider(); }
            catch (Exception) { return fallback; }
        }

        private static int ReadWindowsBuild()
        {
            using (RegistryKey key = Registry.LocalMachine.OpenSubKey(
                @"SOFTWARE\Microsoft\Windows NT\CurrentVersion"))
            {
                if (key == null) { return 0; }
                string build = key.GetValue("CurrentBuildNumber") as string;
                int value;
                return int.TryParse(build, NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
                    ? value : 0;
            }
        }

        private static string ReadWindowsName()
        {
            using (RegistryKey key = Registry.LocalMachine.OpenSubKey(
                @"SOFTWARE\Microsoft\Windows NT\CurrentVersion"))
            {
                if (key == null) { return "Windows"; }
                string product = key.GetValue("ProductName") as string;
                string display = key.GetValue("DisplayVersion") as string;
                int build = ReadWindowsBuild();
                // Windows 11 still reports "Windows 10" in ProductName.
                if (build >= 22000 && product != null && product.Contains("Windows 10"))
                {
                    product = product.Replace("Windows 10", "Windows 11");
                }
                return string.IsNullOrEmpty(display) ? (product ?? "Windows") : product + " " + display;
            }
        }
    }
}
