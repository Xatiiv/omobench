using System;
using System.IO;
using System.Runtime.InteropServices;

namespace OmoBench.Companion
{
    /// <summary>
    /// Holds a Windows power request for as long as a benchmark is measuring,
    /// and releases it the moment that ends.
    ///
    /// This exists because of a real result that was destroyed by it missing.
    /// A ninety-minute CPU benchmark ran inside a WSL distribution while the
    /// Windows host repeatedly entered Modern Standby; the distribution is
    /// suspended along with the machine, so requests that had been taking
    /// sixty seconds took 1,505, 1,279 and 5,793. A benchmark cannot tell a
    /// paused virtual machine from slow inference -- it records the wait as
    /// the model's latency -- so the measurement was not slow, it was wrong.
    ///
    /// <para>
    /// The request kind matters. <c>PowerRequestSystemRequired</c> is the
    /// obvious one and is the wrong one here: on a Modern Standby machine the
    /// system can still transition to its low-power idle state while that is
    /// held. <c>PowerRequestExecutionRequired</c> is the kind documented for
    /// keeping a process running across those transitions, so that is what
    /// this asks for, falling back to the older kind only where the newer one
    /// is refused.
    /// </para>
    ///
    /// <para>
    /// What it does not do: <c>PowerRequestDisplayRequired</c> is never asked
    /// for. A benchmark needs the machine awake, not the screen lit, and
    /// keeping a laptop's display on for ninety minutes is a cost nobody
    /// asked for.
    /// </para>
    ///
    /// <para>
    /// What it cannot promise: this is a request, not a lock. Closing the lid,
    /// a critical battery level, an administrator's policy, a forced sleep
    /// from the power menu and a hibernation timer can all still suspend the
    /// machine. The panel is told whether the request was granted and says so
    /// in those terms; it never claims the machine cannot sleep.
    /// </para>
    ///
    /// Lifetime is tied to this process, which is the point: the panel starts
    /// it before measuring and closes its standard input afterwards, and the
    /// request dies with the process however that process ends -- success,
    /// failure, cancellation, or being killed.
    /// </summary>
    internal static class KeepAwake
    {
        private const string Reason = "OmoBench is measuring a benchmark";

        private enum PowerRequestType
        {
            DisplayRequired = 0,
            SystemRequired = 1,
            AwayModeRequired = 2,
            ExecutionRequired = 3,
        }

        private const int PowerRequestContextSimpleString = 0x1;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct PowerRequestContext
        {
            public uint Version;
            public uint Flags;
            [MarshalAs(UnmanagedType.LPWStr)]
            public string SimpleReasonString;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr PowerCreateRequest(ref PowerRequestContext context);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool PowerSetRequest(IntPtr request, PowerRequestType type);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool PowerClearRequest(IntPtr request, PowerRequestType type);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);

        /// <summary>
        /// Acquire the request, report on standard output what was acquired,
        /// then hold it until standard input reaches end of file.
        ///
        /// The caller learns the outcome from one JSON line and does not have
        /// to guess from an exit code, because "we are awake" and "we asked
        /// and were refused" need different words on the screen and the run
        /// proceeds either way.
        /// </summary>
        internal static int Run()
        {
            IntPtr request = IntPtr.Zero;
            PowerRequestType granted = PowerRequestType.ExecutionRequired;
            string failure = null;

            PowerRequestContext context = new PowerRequestContext
            {
                Version = 0,
                Flags = PowerRequestContextSimpleString,
                SimpleReasonString = Reason,
            };

            try
            {
                request = PowerCreateRequest(ref context);
                if (request == IntPtr.Zero || request == new IntPtr(-1))
                {
                    failure = "Windows refused to create a power request (error "
                        + Marshal.GetLastWin32Error() + ").";
                    request = IntPtr.Zero;
                }
                else if (!PowerSetRequest(request, PowerRequestType.ExecutionRequired))
                {
                    // Older or restricted systems may not take the execution
                    // kind. The system kind is weaker -- it does not survive a
                    // Modern Standby transition -- so it is reported as what
                    // it is rather than as success.
                    int executionError = Marshal.GetLastWin32Error();
                    if (PowerSetRequest(request, PowerRequestType.SystemRequired))
                    {
                        granted = PowerRequestType.SystemRequired;
                    }
                    else
                    {
                        failure = "Windows refused both power requests (errors "
                            + executionError + " and " + Marshal.GetLastWin32Error() + ").";
                        CloseHandle(request);
                        request = IntPtr.Zero;
                    }
                }
            }
            catch (EntryPointNotFoundException)
            {
                failure = "This version of Windows has no power-request API.";
                request = IntPtr.Zero;
            }
            catch (DllNotFoundException)
            {
                failure = "This version of Windows has no power-request API.";
                request = IntPtr.Zero;
            }

            bool held = request != IntPtr.Zero;
            Console.Out.WriteLine(Document(held, granted, failure));
            Console.Out.Flush();

            try
            {
                // Held for exactly as long as the caller keeps the pipe open.
                // Reading to end of file is what makes the lifetime the
                // caller's: no timeout to get wrong, no signal to miss, and a
                // caller that dies takes the request with it.
                using (Stream input = Console.OpenStandardInput())
                {
                    byte[] scratch = new byte[64];
                    while (input.Read(scratch, 0, scratch.Length) > 0)
                    {
                    }
                }
            }
            catch (IOException)
            {
                // A broken pipe is the same message as end of file: stop.
            }
            finally
            {
                if (request != IntPtr.Zero)
                {
                    PowerClearRequest(request, granted);
                    CloseHandle(request);
                }
            }

            return held ? 0 : 1;
        }

        private static string Document(bool held, PowerRequestType granted, string failure)
        {
            string kind = granted == PowerRequestType.ExecutionRequired
                ? "execution-required"
                : "system-required";
            string detail = held
                ? (granted == PowerRequestType.ExecutionRequired
                    ? "Windows granted an execution-required power request. The machine will "
                      + "not idle into sleep while this is held. Closing the lid, a critical "
                      + "battery, an administrator policy or sleeping it deliberately still can."
                    : "Windows refused the execution-required request and granted the weaker "
                      + "system-required one, which a Modern Standby machine can still "
                      + "transition past. Leave this computer awake and on power while measuring.")
                : failure;
            return "{\"kind\":\"omobench-keep-awake-v1\",\"held\":"
                + (held ? "true" : "false")
                + ",\"request\":" + (held ? "\"" + kind + "\"" : "null")
                + ",\"detail\":" + Json(detail) + "}";
        }

        private static string Json(string value)
        {
            if (value == null)
            {
                return "null";
            }
            System.Text.StringBuilder builder = new System.Text.StringBuilder("\"");
            foreach (char character in value)
            {
                if (character == '"' || character == '\\')
                {
                    builder.Append('\\').Append(character);
                }
                else if (character < ' ')
                {
                    builder.Append("\\u").Append(((int)character).ToString("x4"));
                }
                else
                {
                    builder.Append(character);
                }
            }
            return builder.Append('"').ToString();
        }
    }
}
