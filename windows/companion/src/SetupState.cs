using System;

namespace OmoBench.Companion
{
    /// <summary>
    /// The Setup window's decision logic, kept out of the window itself.
    ///
    /// Three questions are easy to get subtly wrong when they live among
    /// control handlers, and all three have produced real defects: whether an
    /// arriving search result still describes the question being asked,
    /// whether filling a field in is allowed to overwrite something the person
    /// typed, and whether what is on screen right now is actually the
    /// combination that was validated. Separating them makes each one testable
    /// without a window.
    /// </summary>
    public sealed class SetupState
    {
        /// <summary>
        /// Incremented whenever the question changes: a new search, a
        /// cancellation, or an edit to a value a search depends on. A result
        /// carrying an older number is answering something nobody asked.
        /// </summary>
        public int Generation { get; private set; }

        public bool SearchRunning { get; private set; }
        public bool CancelEnabled { get; private set; }
        public bool FindEnabled { get; private set; }

        public bool UserEditedCheckout { get; private set; }
        public bool UserEditedInterpreter { get; private set; }

        /// <summary>
        /// The combination proven ready, or null. Cleared the moment any of
        /// the three values it describes is changed, because a validation is
        /// about one specific distribution, checkout and interpreter together
        /// -- not about whatever happens to be on screen later.
        /// </summary>
        public InstallationCandidate Validated { get; private set; }

        public SetupState()
        {
            FindEnabled = true;
        }

        /// <summary>
        /// Start a search. Returns its generation, or 0 when one is already
        /// running -- two concurrent searches would race to fill the same
        /// fields.
        /// </summary>
        public int BeginSearch()
        {
            if (SearchRunning)
            {
                return 0;
            }
            Generation++;
            SearchRunning = true;
            CancelEnabled = true;
            FindEnabled = false;
            return Generation;
        }

        public void CancelSearch()
        {
            Generation++;
            StopSearch();
        }

        /// <summary>
        /// A value the search depends on changed. Any result still in flight
        /// now describes the wrong question, and anything previously validated
        /// no longer describes what is on screen.
        ///
        /// Returns true when a running search was stopped, so the caller can
        /// say so rather than leaving a stale progress message on screen.
        /// </summary>
        public bool SettingsChanged()
        {
            Generation++;
            Validated = null;
            bool stopped = SearchRunning;
            if (stopped)
            {
                StopSearch();
            }
            return stopped;
        }

        public bool NoteCheckoutEdited()
        {
            UserEditedCheckout = true;
            return SettingsChanged();
        }

        public bool NoteInterpreterEdited()
        {
            UserEditedInterpreter = true;
            return SettingsChanged();
        }

        public bool NoteDistributionChanged()
        {
            return SettingsChanged();
        }

        /// <summary>
        /// Accept a search completion.
        ///
        /// Returns false for a stale result, having changed *nothing at all*.
        /// The ordering matters: touching the running flag or the buttons
        /// before this check lets a cancelled search reach in and re-enable
        /// controls belonging to the search that replaced it -- and clearing
        /// the running flag there would let a second search start alongside
        /// the first.
        /// </summary>
        public bool CompleteSearch(int generation)
        {
            if (generation != Generation)
            {
                return false;
            }
            StopSearch();
            return true;
        }

        /// <summary>
        /// May this candidate's checkout replace what is in the field?
        ///
        /// An explicit choice may; a default filled in at the end of a search
        /// may not, if the person typed something while it ran.
        /// </summary>
        public bool MayApplyCheckout(bool explicitChoice)
        {
            return explicitChoice || !UserEditedCheckout;
        }

        public bool MayApplyInterpreter(bool explicitChoice)
        {
            return explicitChoice || !UserEditedInterpreter;
        }

        /// <summary>
        /// Record that the fields now hold this candidate. An explicit choice
        /// replaces all three values together, so there is no longer a manual
        /// edit to protect.
        /// </summary>
        public void NoteApplied(InstallationCandidate candidate, bool explicitChoice)
        {
            Validated = candidate;
            if (explicitChoice)
            {
                UserEditedCheckout = false;
                UserEditedInterpreter = false;
            }
        }

        /// <summary>
        /// Is the combination currently on screen the one that was validated?
        ///
        /// Asked of the displayed values rather than of the candidate, so a
        /// validated result whose interpreter the person then changed does not
        /// keep reporting itself ready for a pairing nobody checked.
        /// </summary>
        public bool IsReadyFor(string distribution, string checkout, string interpreter)
        {
            InstallationCandidate candidate = Validated;
            if (candidate == null || !candidate.Ready)
            {
                return false;
            }
            return Same(candidate.Distribution, distribution, false)
                && Same(candidate.CheckoutWindowsPath, checkout, true)
                && Same(candidate.PythonWslPath, interpreter, false);
        }

        private static bool Same(string left, string right, bool windowsPath)
        {
            string a = (left ?? "").Trim();
            string b = (right ?? "").Trim();
            if (windowsPath)
            {
                // Windows paths differ harmlessly in case and trailing slash.
                a = a.TrimEnd('\\');
                b = b.TrimEnd('\\');
                return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
            }
            // A distribution name and a Linux path are both case-sensitive.
            return string.Equals(a, b, StringComparison.Ordinal);
        }

        private void StopSearch()
        {
            SearchRunning = false;
            CancelEnabled = false;
            FindEnabled = true;
        }
    }
}
