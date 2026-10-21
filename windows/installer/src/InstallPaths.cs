using System;
using System.IO;

namespace OmoBench.Installer
{
    /// <summary>
    /// Where an installation lives, and what it is allowed to touch.
    ///
    /// Everything here is per-user: the program files, the Start menu entry
    /// and the uninstall entry all sit under the current user's own profile,
    /// so installing needs no administrator rights and cannot disturb another
    /// account. Nothing is written to Program Files, HKLM, or any shared
    /// location.
    ///
    /// The distinction that matters most is between *program* and *data*. The
    /// program directory is replaced wholesale on upgrade and deleted on
    /// uninstall. Configuration, benchmark evidence, model caches and the WSL
    /// installation are data: they belong to the person, live elsewhere, and
    /// are never touched by either.
    /// </summary>
    public static class InstallPaths
    {
        public const string ProductName = "OmoBench";
        public const string CompanionFileName = "OmoBenchCompanion.exe";
        public const string UninstallerFileName = "OmoBenchUninstall.exe";

        /// <summary>Identifies this product's uninstall entry for this user.</summary>
        public const string UninstallKeyPath =
            @"Software\Microsoft\Windows\CurrentVersion\Uninstall\OmoBench";

        /// <summary>
        /// Overrides every location below, so a test can install into a
        /// throwaway directory instead of the user's real profile.
        /// </summary>
        internal static string RootOverride = null;

        /// <summary>
        /// %LOCALAPPDATA%\Programs\OmoBench -- the conventional per-user
        /// equivalent of Program Files. A stable path matters beyond tidiness:
        /// the omobench:// registration points at the executable inside it, so
        /// it must not be a temporary folder or wherever the download landed.
        /// </summary>
        public static string ProgramDirectory
        {
            get
            {
                if (!string.IsNullOrEmpty(RootOverride))
                {
                    return Path.Combine(RootOverride, "Programs", ProductName);
                }
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "Programs", ProductName);
            }
        }

        public static string CompanionPath
        {
            get { return Path.Combine(ProgramDirectory, CompanionFileName); }
        }

        public static string UninstallerPath
        {
            get { return Path.Combine(ProgramDirectory, UninstallerFileName); }
        }

        /// <summary>Records what was installed, for upgrades and support.</summary>
        public static string ManifestPath
        {
            get { return Path.Combine(ProgramDirectory, "install.json"); }
        }

        public static string StartMenuDirectory
        {
            get
            {
                if (!string.IsNullOrEmpty(RootOverride))
                {
                    return Path.Combine(RootOverride, "StartMenu");
                }
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.StartMenu),
                    "Programs");
            }
        }

        public static string ShortcutPath
        {
            get { return Path.Combine(StartMenuDirectory, ProductName + ".lnk"); }
        }

        /// <summary>
        /// Data that outlives the program. Listed explicitly so that the
        /// install, upgrade and uninstall paths can all be checked against the
        /// same statement of what must survive them.
        /// </summary>
        public static string[] PreservedDataLocations()
        {
            return new[]
            {
                // The companion's own saved configuration.
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    ProductName),
                // Diagnostic log and the runtime directory.
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    ProductName),
            };
        }

        /// <summary>
        /// True when the path is inside the program directory, and therefore
        /// something the installer owns. Used to make sure an uninstall never
        /// deletes outside what it put there.
        /// </summary>
        public static bool IsInsideProgramDirectory(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return false;
            }
            try
            {
                string full = Path.GetFullPath(path).TrimEnd('\\');
                string root = Path.GetFullPath(ProgramDirectory).TrimEnd('\\');
                return full.Equals(root, StringComparison.OrdinalIgnoreCase)
                    || full.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception)
            {
                return false;
            }
        }
    }
}
