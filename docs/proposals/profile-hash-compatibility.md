# Proposal: reconciling a result against the profile it actually ran

Status: **proposal only.** Nothing here is implemented, no validator behaviour
is changed, and no recorded hash is rewritten. This exists because the
line-ending fix made an existing, latent incompatibility visible, and the
decision about it belongs to a reviewer rather than to the change that found
it.

## 1. What the validator does today

`tools/validate_results.py:load_profile_acceptance()` reconciles a bundle
against the profile it names by **re-hashing the profile file in the current
checkout** and refusing when that hash differs from `benchmark.profile_sha256`:

> `benchmark.profile_sha256: does not match the current contents of
> <path>; the profile may have changed since this result was packaged`

That is deliberate and worth keeping. Acceptance thresholds come from the
profile, so judging an old result against an edited profile would apply
thresholds the run was never held to. The rule is "judge a result only against
the exact bytes it declares".

Its consequence is equally exact: **once a profile file's bytes change, every
bundle recorded against the previous bytes stops reconciling.** That is true
for a content edit and for a whitespace-only one, because the evidence is a
hash.

## 2. What made it visible

Before `.gitattributes`, git's `core.autocrlf` rewrote tracked text files to
CRLF on a Windows checkout while still reporting the tree as clean — it
compares normalized content, not bytes. `benchmarks/profiles/` was affected, so
the same profile had two hashes depending on where it was cloned:

| Profile | LF (Linux, and now everywhere) | CRLF (pre-rule Windows checkout) |
| --- | --- | --- |
| `chat-small-extended-v1.yaml` | `7029edaf…` | `eedf8378…` |

A bundle packaged on the Windows checkout recorded `eedf8378…`. It reconciled
there and **was already refused on Linux and in CI** — the incompatibility
predates the fix. Pinning the files to LF did not create it; it removed the
platform dependence, so the bundle is now refused consistently instead of
passing in one place and failing in another.

## 3. Exactly what is affected

Measured, not estimated. `results/` — the public registry — contains **zero**
bundles, so no published evidence is involved. Every local bundle under
`.omobench-runs/`, which AGENTS.md defines as local staging rather than public
evidence:

| Bundle | Profile | Recorded hash | Reconciles against the LF profile |
| --- | --- | --- | --- |
| `.omobench-runs/run-c2fa02411efc/result.json` | `chat-small-v1` | `b81e4891…` | yes |
| `.omobench-runs/run-32dd26a849e5/result.json` | `chat-small-v1` | `b81e4891…` | yes |
| `.omobench-runs/run-49f0510010c1/result.json` | `chat-small-v1` | `b81e4891…` | yes |
| `.omobench-runs/run-968f17c242c5/result.json` | `chat-small-v1` | `b81e4891…` | yes |
| `.omobench-runs/invalid-registry-copy-run-c2fa02411efc/result.json` | `chat-small-v1` | `b81e4891…` | yes |
| `.omobench-runs/ui/job-35a45c414a775f71/run-80dcc35b897b/result.json` | `chat-small-extended-v1` | `eedf8378…` | **no — line endings** |
| `.omobench-runs/ui/job-b97d2401f43fe2a5/run-0377a4636941/result.json` | `chat-small-extended-v1` | `3387c0cc…` | **no — profile content changed** |
| `.omobench-runs/ui/job-c21fd7c4b2735658/run-f59ec5667fd0/result.json` | `chat-small-extended-v1` | `3387c0cc…` | **no — profile content changed** |

So of eight local bundles: five unaffected, **one** attributable to line
endings, and two that stopped reconciling earlier, when the extended profile's
own content was corrected (its `warmup.value` changed from 5 to 4). Those two
are the rule working as intended.

All eight files are untouched: their last-written timestamps are between
2026-08-30 and 2026-10-26, all before this line of work began, and nothing in
it writes to `.omobench-runs/`.

## 4. The decision to make

**CRLF and LF hashes do not match, and nothing here proposes pretending they
do.** The recorded hash is correct: those bytes are what that run executed. The
question is narrower:

> Should the validator reconcile a bundle against a *historical* revision of the
> profile it names, or only against the current file?

Arguments for keeping it as it is: one file, one hash, no ambiguity; a
contributor re-packages rather than arguing about provenance; profile edits are
rare by design, because a changed workload is supposed to become a new `-v2`
id rather than an edit.

Arguments against: a result is evidence of what ran, and the project treats
`results/` as append-only. A rule that can retroactively invalidate correct
evidence because an unrelated whitespace rule changed is a rule that punishes
honesty.

## 5. Concrete mechanism, if the decision is to reconcile

A reviewed ledger of superseded profile bytes, beside the profiles themselves:

```yaml
# benchmarks/profiles/superseded.yaml
chat-small-extended-v1:
  - sha256: eedf8378d6c942510cae7e3ee306f9ab8fc91d55bd9b9406011c2ac63597db30
    reason: CRLF form of the current bytes, from a Windows checkout before
            .gitattributes pinned these files to LF
    acceptance: { minimum_successful_requests: 40, maximum_error_rate_percent: 1 }
  - sha256: 3387c0cc6696ba3e7685021bb308af52665f5251b7404044d1c9f96cbfc373e9
    reason: warmup.value 5 -> 4, correcting the documented off-by-one
    acceptance: { minimum_successful_requests: 40, maximum_error_rate_percent: 1 }
```

`load_profile_acceptance()` would try the current file first and fall back to a
matching ledger entry, judging the result against **the thresholds recorded in
that entry** rather than today's. Properties this keeps:

- no hash is rewritten, and no bundle is edited;
- a result is still judged only against bytes it declares;
- an unknown hash is still refused — the ledger is an allowlist, not a bypass;
- each entry states why those bytes existed, so the ledger is reviewable;
- the entries are reconstructible from git history, so review can verify them
  rather than trusting them.

Costs: a second place where acceptance thresholds live, and a growing file that
needs discipline not to become a dumping ground for "make validation pass".

## 6. Recommendation

**Do nothing yet.** The public registry is empty, and the only bundle affected
by line endings is local staging on one machine. Re-running it is cheaper and
more honest than shipping a compatibility layer with no users. Revisit this
before the first public bundle exists, because after that the choice is no
longer reversible without rewriting evidence.

If it is revisited, do it as its own change with its own tests, not folded into
whatever next edits a profile.
