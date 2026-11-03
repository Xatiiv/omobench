# Historical profile compatibility

Status: implemented for review. Existing evidence is never rewritten.

A result is judged against the exact profile bytes identified by its
`profile_id` and `profile_sha256`. The current profile remains the first choice.
When its digest differs, the validator may use an explicitly reviewed snapshot
in `benchmarks/profiles/history.json`. Unknown digests are still refused.

## The two archived snapshots

| Recorded digest | Provenance | Meaning |
| --- | --- | --- |
| `sha256:eedf8378d6c942510cae7e3ee306f9ab8fc91d55bd9b9406011c2ac63597db30` | CRLF checkout of the extended profile at `4e8bb6f` | Same parsed configuration as the current LF profile, but a distinct byte identity. |
| `sha256:3387c0cc6696ba3e7685021bb308af52665f5251b7404044d1c9f96cbfc373e9` | CRLF checkout at `c978c51` | Historical profile before the warmup boundary correction; not relabelled as the corrected workload. |

Both snapshots are reconstructed from committed Git blobs followed by the
specified LF-to-CRLF conversion. Full source revisions and explanations are in
the archive. Base64 preserves the exact historical bytes regardless of checkout
line-ending settings. Regression tests reproduce them from Git history.

The validator hashes the decoded bytes again, verifies the embedded profile id,
and reads acceptance directly from that snapshot. There is no parallel table of
hand-written thresholds. Both archived profiles require at least 40 successful
requests and at most 1% errors. Ambiguous entries, malformed data, mismatched
bytes and wrong profile ids fail closed.

## What this does and does not establish

This permits validation of an existing bundle against its declared historical
profile. It does not certify that an old runner executed the declared workload,
relax artifact checks, fix missing provenance, or prove actual CPU/GPU execution.
All other submission checks remain in force. Failed acceptance still fails.

Comparison groups continue to use the original profile id **and hash**. The
archive does not make LF and CRLF hashes equal, and does not merge the older
warmup mapping into today's comparison group. Historical snapshots are not
listed as selectable profiles for new runs.

The earlier local audit reported three affected bundles, one using the first
snapshot and two using the second. This change does not access or modify those
private files. Their next validation must establish their actual outcomes;
archive support alone is not a claim that they are publishable.

## Adding a snapshot

Adding an entry is a reviewed repository change, never a user-submitted hash
exception. Include exact bytes, full source revision, any deterministic checkout
conversion and a reason. Verify reconstruction, embedded id and digest in tests.
Do not accept a snapshot merely to make a failing result pass. A new workload
must still use a new profile version rather than modifying an existing profile.
