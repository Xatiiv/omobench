# Community submissions from an installed panel

The goal is download, benchmark, review, submit, then compare after acceptance.
Benchmark execution and acceptance rules are unchanged. A completed run is not
necessarily eligible for publication. No result is uploaded during sign-in,
packaging, validation, or preview.

## Contributor flow

1. Finish a benchmark and open **Share results** (or **Review & submit** in the
   advanced panel). Package and validate the evidence.
2. Leave **OmoBench community registry** selected. Choose **Sign in with GitHub**,
   then enter the displayed one-time code at `https://github.com/login/device`.
   Use only a code requested in your own local panel.
3. **Review submission files** shows the account, public upload repository,
   upstream PR destination, all five file sizes and SHA-256 hashes, normalized
   result and PR description. Download the ZIP to inspect raw evidence and logs.
4. Confirm the evidence describes real inference on the declared device and has
   no secrets. **Create pull request** creates or reuses your public OmoBench
   fork, uploads only those five files to `submit/<run-id>`, and opens a PR in
   `Xatiiv/omobench`. If you own the upstream repository, it uses a new branch
   there because GitHub does not let you fork your own repository.
5. Follow CI and maintainer review on GitHub. Uploaded evidence is public before
   merge. Registry inclusion and community comparison follow review, merge and a
   site build. You can use **Download result.json for comparison** privately in
   the website's Compare page before submission, without uploading it.

The legacy **Checkout origin** option still uses existing `gh` authentication
and opens its PR in the displayed origin repository. It needs a Git checkout
and write access. The complete ZIP is available without either sign-in route.

## Permissions and credential lifetime

GitHub OAuth device flow supports the local WSL/Linux panel without a client
secret or a callback server. The requested `public_repo` scope is broader than
one fork: it allows writing to public repositories on that account. It does not
request private-repository access. The app uses it only for the reviewed
OmoBench fork and submission. Unexpected or broader granted scopes are refused.

The access token and device code stay in the panel process's memory. They are
not returned to browser JavaScript, saved in job/config files, put on command
lines, or included in evidence or logs. API requests use fixed GitHub HTTPS
hosts and refuse redirects. The existing local panel token, Host, Origin and
JSON checks protect sign-in and submission endpoints as well.

Signing out or stopping the panel forgets the local credential. Tokens are
forgotten after eight hours at most, sooner if GitHub supplies a shorter expiry.
This does not revoke the OAuth authorization at GitHub; **Review or revoke
access on GitHub** opens that app's settings. A revoked token is reported and
requires signing in again. An account change invalidates existing previews.

## Review, forks and retries

Reviews expire after 15 minutes and are bound to the job, account, sign-in
session, repository IDs, base revision, and exact five-file digest. They are
memory-only. Refresh or restart requires a fresh review, not a fresh benchmark.
Sign-in cannot change during a running packaging or submission operation.

Preparation reads GitHub; it never creates a fork or uploads files. Existing
forks are checked by owner and fork-network IDs, not by name alone. Renamed
fork discovery examines at most 500 upstream forks, then stops honestly if it
cannot establish a destination. An unrelated repository with the same name is
refused. The base commit/tree comes from upstream, so an old fork does not need
its default branch rewritten. No existing ref is updated or force-pushed.

A fork may take time to become ready. The panel says to wait and retry. It saves
submission progress locally, including account, fork IDs and commit SHA, so a
lost response after creating a ref or PR can be reconciled without a duplicate.
Closed or merged PRs are reported as such. Branch collisions are refused.
Repackaging is blocked once community submission begins; the same evidence can
still be downloaded. Return to the original account and route for retries.
Local files and benchmark evidence are never deleted on upload failure.

## Maintainer setup before enabling sign-in

This change does not itself register or authorize a GitHub application.
`github_auth.OAUTH_CLIENT_ID` is empty until an OmoBench OAuth app is registered.
The UI reports that state and keeps manual bundle download available.

Register a project-owned OAuth app with a recognizable name, homepage and
permission explanation, and enable device flow. No client secret is needed or
used. Record only its public client ID in `cli/src/omobench/github_auth.py`.
`OMOBENCH_GITHUB_CLIENT_ID` can override that value for local development or a
downstream build; it is not a credential and never changes API hosts. Rebuild
the installer from the tested committed revision so the runtime payload includes
the configured ID and new modules. Do not claim the released installer supports
sign-in until that release has actually shipped.

Use an account that does not own upstream to verify real sign-in and fork
submission, with genuine reviewed evidence and explicit consent to publish it.
Do not upload synthetic fixtures to the community registry. Verify sign-out,
revocation, restart/review, a private local comparison and eventual accepted
community comparison. Automated tests use isolated API and browser DOM fixtures;
those tests do not establish that a real OAuth app is registered or authorized.

Official references:

- [GitHub device flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow)
- [OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
- [Fork API and asynchronous creation](https://docs.github.com/en/rest/repos/forks#create-a-fork)

## Pilot completion criteria

After the real sign-in test, collect genuine submissions from three to five
volunteer setups, with consent. Record install/setup blockers, time to first
valid run, whether they needed a terminal, submission success and comparison
compatibility. Keep CPU/GPU execution and different profile hashes distinct.
Acceptance thresholds stay fixed; a rejected run is useful pilot feedback,
not a reason to relabel evidence. This pilot has not been run by the code tests.
