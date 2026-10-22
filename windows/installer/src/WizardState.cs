using System;
using OmoBench.Companion;

namespace OmoBench.Installer
{
    /// <summary>
    /// Whether a result arriving from the installer's background work still
    /// describes what the window is doing.
    ///
    /// This is the installer's own logic, not the companion's. The two windows
    /// look similar and were previously tested through the companion's
    /// <see cref="SetupState"/>, which the installer does not use -- so the
    /// code that actually ran had no test at all.
    ///
    /// Two things make a queued result obsolete, and both have to be checked
    /// in the same place. A newer operation may have replaced it; or the
    /// person may have cancelled after the work finished but before the window
    /// got round to applying it. In that second case the work succeeded, and
    /// acting on it would save a configuration and announce success for
    /// something that was called off.
    /// </summary>
    public sealed class WizardState
    {
        /// <summary>Incremented for every new operation.</summary>
        public int Generation { get; private set; }

        /// <summary>True while background work is in flight.</summary>
        public bool Working { get; private set; }

        private DiscoveryCancel _cancel;

        /// <summary>Start an operation, returning the generation to carry with it.</summary>
        public int Begin(DiscoveryCancel cancel)
        {
            Generation++;
            Working = true;
            _cancel = cancel;
            return Generation;
        }

        /// <summary>
        /// Stop the current operation. Anything it later delivers is refused.
        /// </summary>
        public void Cancel()
        {
            DiscoveryCancel cancel = _cancel;
            if (cancel != null) { cancel.Cancel(); }
            Generation++;
            Working = false;
        }

        /// <summary>True when this operation was cancelled while it ran.</summary>
        public bool WasCancelled
        {
            get { return _cancel != null && _cancel.IsCancelled; }
        }

        /// <summary>
        /// May a completion carrying this generation be applied?
        ///
        /// Nothing is changed unless the answer is yes -- not the working
        /// flag, not the buttons, not the status. Clearing state before the
        /// check let a result from a replaced operation reach into a newer
        /// one and re-enable its controls.
        /// </summary>
        public bool TryAccept(int generation)
        {
            if (generation != Generation)
            {
                // Somebody else's operation is in flight. Change nothing at
                // all -- including the working flag, which now belongs to it.
                return false;
            }

            // This operation has ended, whether or not its result is wanted.
            Working = false;
            return !WasCancelled;
        }
    }
}
