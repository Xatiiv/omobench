using System;
using System.IO;
using Microsoft.Win32;

namespace OmoBench.Companion
{
    /// <summary>
    /// Registers <c>omobench://</c> for the current user only.
    ///
    /// Everything is written under HKEY_CURRENT_USER\Software\Classes, which
    /// needs no administrator rights and affects nobody else on the machine.
    /// Unregistering removes exactly the key this companion created, and only
    /// when it still points at this companion -- so a different OmoBench
    /// build's registration is never deleted out from under it.
    /// </summary>
    public static class ProtocolRegistration
    {
        public const string KeyPath = @"Software\Classes\" + Protocol.Scheme;
        private const string CommandSubKey = @"shell\open\command";
        private const string OwnerValueName = "OmoBenchCompanion";

        public static string RegisteredCommand()
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(KeyPath + @"\" + CommandSubKey))
            {
                return key == null ? null : key.GetValue(null) as string;
            }
        }

        public static bool IsRegisteredForThisExecutable(string executablePath)
        {
            string command = RegisteredCommand();
            if (string.IsNullOrEmpty(command))
            {
                return false;
            }
            return command.IndexOf(executablePath, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        /// <summary>
        /// The command Windows runs for an omobench:// link. "%1" is the URI;
        /// it is passed as a single quoted argument and is only ever validated
        /// by <see cref="Protocol.Parse"/>, never executed or interpolated.
        /// </summary>
        public static string BuildCommand(string executablePath)
        {
            return CommandLine.Quote(executablePath) + " \"%1\"";
        }

        public static void Register(string executablePath)
        {
            if (string.IsNullOrWhiteSpace(executablePath) || !File.Exists(executablePath))
            {
                throw new InvalidOperationException("Cannot register a protocol handler for a missing executable.");
            }

            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(KeyPath))
            {
                if (key == null)
                {
                    throw new InvalidOperationException("Could not create " + KeyPath + " for the current user.");
                }
                key.SetValue(null, "URL:OmoBench Companion");
                key.SetValue("URL Protocol", string.Empty);
                // Ownership marker, so uninstall can tell this registration
                // apart from one written by something else.
                key.SetValue(OwnerValueName, executablePath);

                using (RegistryKey iconKey = key.CreateSubKey("DefaultIcon"))
                {
                    if (iconKey != null)
                    {
                        iconKey.SetValue(null, executablePath + ",0");
                    }
                }
                using (RegistryKey commandKey = key.CreateSubKey(CommandSubKey))
                {
                    if (commandKey == null)
                    {
                        throw new InvalidOperationException("Could not create the handler command key.");
                    }
                    commandKey.SetValue(null, BuildCommand(executablePath));
                }
            }
            Diagnostics.Write("Registered omobench:// for the current user -> " + executablePath);
        }

        /// <summary>
        /// Remove the registration, but only if this companion owns it.
        /// Returns a human-readable description of what happened.
        /// </summary>
        public static string Unregister(string executablePath)
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(KeyPath))
            {
                if (key == null)
                {
                    return "No omobench:// registration exists for your user account.";
                }
                string owner = key.GetValue(OwnerValueName) as string;
                string command = null;
                using (RegistryKey commandKey = key.OpenSubKey(CommandSubKey))
                {
                    if (commandKey != null)
                    {
                        command = commandKey.GetValue(null) as string;
                    }
                }

                bool ownedByUs =
                    (!string.IsNullOrEmpty(owner) && string.Equals(owner, executablePath, StringComparison.OrdinalIgnoreCase))
                    || (!string.IsNullOrEmpty(command) && command.IndexOf(executablePath, StringComparison.OrdinalIgnoreCase) >= 0);

                if (!ownedByUs)
                {
                    return "The omobench:// registration points at another program:\n    "
                        + (command ?? owner ?? "(unknown)")
                        + "\n\nIt was left untouched.";
                }
            }

            Registry.CurrentUser.DeleteSubKeyTree(KeyPath, false);
            Diagnostics.Write("Removed the omobench:// registration for the current user.");
            return "Removed the omobench:// registration for your user account.";
        }
    }
}
