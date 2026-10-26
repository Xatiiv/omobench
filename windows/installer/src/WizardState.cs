using System;
using OmoBench.Companion;

namespace OmoBench.Installer
{
    /// <summary>What a completion arriving from background work may do.</summary>
    public enum Completion
    {
        /// <summary>A replaced operation, or one already finished. Change nothing.</summary>
        Stale,

        /// <summary>This operation, but it was called off. Finish stopping; announce nothing.</summary>
        Cancelled,

        /// <summary>This operation, wanted. Apply it.</summary>
        Accepted,
    }

    /// <summary>
    /// Whether a result arriving from the installer's background work still
    /// describes what the window is doing.
    ///
    /// This is the installer's own logic, not the companion's. The two windows
    /// look similar and were previously tested through the companion's
    /// <see cref="SetupState"/>, which the installer does not use -- so the
    /// code that actually ran had no test at all.
    ///
    /// The distinction this class exists to keep is between *cancellation
    /// requested* and *work finished*. Treating Stop as the end of the
    /// operation was wrong twice over: the window disabled Stop, threw the
    /// eventual completion away as stale, and so never ran the code that puts
    /// the buttons back -- leaving a wizard with nothing enabled; and it
    /// cleared the working flag while the worker was still running, so closing
    /// the window skipped the wait that exists to let that worker stop at a
    /// checkpoint of its own choosing.
    ///
    /// So a cancelled operation is still the current operation, and still
    /// owes the window a completion. What cancellation changes is what that
    /// completion is allowed to do: finish stopping and restore the controls,
    /// never save a configuration or announce success.
    /// </summary>
    public sealed class WizardState
    {
        /// <summary>Incremented only when a new operation starts.</summary>
        public int Generation { get; private set; }

        /// <summary>
        /// True from the moment work starts until its completion is handled.
        /// Cancelling does not clear it: the worker is still running.
        /// </summary>
        public bool Working { get; private set; }

        /// <summary>True once Stop has been pressed and before it has finished.</summary>
        public bool CancellationRequested { get; private set; }

        private DiscoveryCancel _cancel;

        /// <summary>Start an operation, returning the generation to carry with it.</summary>
        public int Begin(DiscoveryCancel cancel)
        {
            Generation++;
            Working = true;
            CancellationRequested = false;
            _cancel = cancel;
            return Generation;
        }

        /// <summary>
        /// Ask the current operation to stop. The token is cancelled so the
        /// work itself winds up; the operation stays current and stays
        /// working until it reports back.
        /// </summary>
        public void Cancel()
        {
            if (!Working) { return; }
            CancellationRequested = true;
            DiscoveryCancel cancel = _cancel;
            if (cancel != null) { cancel.Cancel(); }
        }

        /// <summary>
        /// Whether a progress update carrying this generation still describes
        /// what the window is doing. Queued updates outlive the work that
        /// produced them, and one from a replaced operation would otherwise
        /// redraw a newer operation's stages.
        /// </summary>
        public bool IsCurrent(int generation)
        {
            return Working && generation == Generation;
        }

        /// <summary>
        /// Hand in a completion, and learn what it may do.
        ///
        /// Nothing is changed for a stale one -- not the working flag, which
        /// by then belongs to a newer operation. The current operation ends
        /// here whether or not its result is wanted, because the work really
        /// has finished; a window that goes on believing otherwise waits for a
        /// worker that will never report again.
        /// </summary>
        public Completion Complete(int generation)
        {
            if (!Working || generation != Generation)
            {
                return Completion.Stale;
            }

            Working = false;
            bool cancelled = CancellationRequested
                || (_cancel != null && _cancel.IsCancelled);
            CancellationRequested = false;
            return cancelled ? Completion.Cancelled : Completion.Accepted;
        }
    }
}
