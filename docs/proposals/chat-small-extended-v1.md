# Proposal: `chat-small-extended-v1` — a longer-duration chat-small profile

Status: **implemented**. `benchmarks/profiles/chat-small-extended-v1.yaml` and its
`cli.py` `PROFILE_SPECS` runner mapping exist; it is selectable via the CLI and
both UIs. `chat-small-v1`, `chat-long-v1`, and `throughput-v1` are unchanged.
This revision corrects request-accounting issues found after the first draft,
and again after a second review — see the changelog at the bottom — before
implementation; the corrected design is what shipped.

## 1. Purpose

`chat-small-v1`'s `maximum_duration_seconds: 900` (15 minutes) is enough time for
fast, typically GPU-backed servers to comfortably complete its `measured_requests:
50` target. It is not enough for genuinely slow hardware — see §3 for measured
data from a real CPU run that hit the 900 s ceiling at only 13 measured
requests, far short of `acceptance.minimum_successful_requests: 40`.

`chat-small-extended-v1` is the **same workload** with a longer duration
ceiling, so slower hardware gets enough wall-clock time to collect a full
sample judged against the *same* acceptance bar. It is not a CPU-only
profile: GPU execution is equally eligible, and — per §3 — a fast server's
expected runtime is unaffected by raising the ceiling, since the scheduler
stops as soon as its request budget completes, however little wall-clock
time that took.

## 2. Implemented profile

`benchmarks/profiles/chat-small-extended-v1.yaml`:

```yaml
id: chat-small-extended-v1
profile_format_version: 1
status: active
title: Interactive chat latency (extended duration)
description: >-
  The same chat-small-v1 workload — synthetic assistant-sized prompt, single
  streaming completion — run against a longer duration ceiling so hardware
  too slow to finish chat-small-v1's 50 measured requests within 15 minutes,
  chiefly CPU-only execution, still collects a full sample. Eligible for both
  CPU and GPU execution. On hardware fast enough to finish chat-small-v1
  today, this profile takes no longer: the run still stops as soon as its
  request budget completes, well before the duration ceiling.
transport:
  protocol: openai-chat-completions
  streaming: true
workload:
  prompt_source: synthetic-deterministic
  prompt_seed: 20260828
  prompt_uniqueness: per-request
  target_input_tokens: 1024
  input_token_tolerance_percent: 2
  requested_output_tokens: 256
execution:
  concurrency: 1
  measured_requests: 50
  warmup_requests: 5
  cooldown_requests: 5
  request_timeout_seconds: 300
  maximum_duration_seconds: 5400
acceptance:
  minimum_successful_requests: 40
  maximum_error_rate_percent: 1
report:
  exclude_warmup_and_cooldown: true
  required_metrics:
    - ttft_p50_ms
    - ttft_p95_ms
    - inter_token_latency_p50_ms
    - inter_token_latency_p95_ms
    - output_tokens_per_second
    - total_tokens_per_second
    - requests_per_second
    - error_rate_percent
```

Every field is identical to `chat-small-v1.yaml` **except** `title`,
`description`, and `execution.maximum_duration_seconds` (900 → 5,400).
`workload.*`, `execution.concurrency`/`measured_requests`/`warmup_requests`/
`cooldown_requests`, `acceptance.*`, and `report.*` are unchanged — this is
deliberately the same benchmark, not a new one, and the runner mapping below
is built to make the runner's *actual* behavior match `cooldown_requests: 5`
exactly, not to redefine it.

### Implemented runner configuration

`cli.py`'s `PROFILE_SPECS` dict is what `build_guidellm_command()` actually
reads; the YAML above is not parsed for execution (see the discrepancy note
in §3). The shipped entry:

```python
"chat-small-extended-v1": {
    "input_tokens": 1024,
    "output_tokens": 256,
    "profile": "kind=synchronous,warmup.value=5,warmup.mode=requests,cooldown.value=4,cooldown.mode=requests",
    "max_requests": 60,
    "max_duration_seconds": 5400,
},
```

Two changes from `chat-small-v1`, both explained and verified in §3:

1. **Explicit request-count phases**, not `warmup=0.1,cooldown=0.1` (percent,
   `prefer_duration` mode). `chat-small-v1`'s percent scheme ties warmup and
   cooldown to a fraction of the *duration ceiling*, not to the declared
   `warmup_requests`/`cooldown_requests` counts — irrelevant for a fixed
   15-minute profile, but wrong for a profile whose whole point is a large,
   flexible ceiling: at 5,400 s it would reserve a fixed 540 s (9 min) for
   warmup and 540 s for cooldown regardless of how many requests that
   actually is. `mode=requests` ties each phase to an explicit request count
   instead, independent of how long each request takes.
2. **`cooldown.value: 4`, not 5 — a documented compensation, not the declared
   count.** GuideLLM 0.7.3's own request-count cooldown boundary excludes one
   *more* trailing request than the value it is given (verified end to end
   in §3: asking for `cooldown.value=5` actually excludes 6). Asking for
   `cooldown.value=4` is the smallest correction that makes the runner's
   *actual* excluded count equal to the declared 5 — not a change to what
   "cooldown" means, and not a change to `cooldown_requests: 5` in the YAML,
   which stays the accurate description of what actually happens.
   `max_requests: 60` (5 + 50 + 5, the declared sum) follows directly once
   the *actual* cooldown is corrected to 5 — no inflation of the request
   budget is needed.

`managed_setup.validate_recipe` and the `--profile` argparse `choices` on
`run`/`package` both derive from `PROFILE_SPECS`, so adding this entry alone
makes the profile selectable everywhere those are consulted — no separate
code paths needed updating.

## 3. Duration and request accounting

### Verified through the complete pinned GuideLLM 0.7.3 path, not one function in isolation

An earlier revision of this profile verified warmup/cooldown boundaries by
calling `TransientPhaseConfig.compute_transition_time()` directly with
hand-built stand-in objects. That is not sufficient on its own: the real
counting path also depends on `GenerativeBenchmarkAccumulator.update_estimate()`
(which drives `compute_transition_time()` from real scheduler-yielded events
and decides which sub-accumulator — completed/incomplete/errored — a request
lands in) and `GenerativeMetrics.compile()`'s own `get_within_range()` overlap
filter (which independently decides, from the timestamps
`compute_transition_time()` produced, which specific requests are "measured").
A narrower test could not have caught a mismatch between those two layers.

This revision instead drives the **real, complete path** with a fast,
deterministic, in-process test (`ChatSmallExtendedBoundaryTests` in
`cli/tests/test_cli.py`, run — not skipped — against the pinned GuideLLM 0.7.3
package in this environment): real `guidellm.schemas.info.RequestInfo` and
`guidellm.scheduler.schemas.SchedulerState`/`SchedulerProgress` objects,
matching the real field semantics confirmed by reading
`guidellm/scheduler/constraints/request.py` (`remaining_requests = min(max(0,
max_num - state.processed_requests), max_num)` — `processed_requests` only
increments on *terminal* completion, and `remaining_requests` is recomputed
from it on every event, so both quantities mean exactly what a real run's
constraint bookkeeping means, not an approximation of it), for a synthetic,
sequential (concurrency = 1) request timeline where request *i* occupies the
inclusive-start/exclusive-end window `[i − 1, i)`. Each event is fed through
the real `GenerativeBenchmarkAccumulator.update_estimate()`, and the final
answer is read from the real `GenerativeMetrics.compile(accumulator)` —
i.e., scheduler events, accumulator updates, and final compiled metrics, the
complete path, not one link in it.

Findings, all from that test, verified rather than assumed:

- **The CLI string syntax for explicit request-count phases exists and is
  supported.** `guidellm.utils.arg_string.loads()` (the parser behind every
  `--profile`/`--data`/etc. value) supports dot notation for nested fields:
  `warmup.value=5,warmup.mode=requests` parses to
  `{"warmup": {"value": 5, "mode": "requests"}}`, which
  `SynchronousProfileArgs.model_validate()` accepts directly as
  `TransientPhaseConfig(value=5, mode="requests")`.
- With `mode="requests"`, `compute_limits()` skips the duration branch
  entirely — no `prefer_duration` fallback can override it, unlike a bare
  percent or absolute value (which defaults to `mode="prefer_duration"` and
  would silently reinterpret a bare `warmup=5` as "5 **seconds**", not 5
  requests, then discard even that if a duration constraint is present).
  This is the trap explicit `mode=requests` avoids.
- **Warmup is exact.** A `warmup.value=5` run, driven through the complete
  path, excludes precisely requests 1–5 regardless of the configured total
  (confirmed at totals of 59, 60, and 61 in earlier exploratory runs, and at
  60 in the committed test).
- **Cooldown is not exact — verified, not assumed, through the complete
  path.** A `cooldown.value=5` run at a configured total of 60 yields
  `request_totals.total = 49`, not 50: the boundary excludes the *last six*
  requests, one more than asked. A `cooldown.value=4` run at the same total
  of 60 yields exactly `request_totals = {successful: 50, incomplete: 0,
  errored: 0, total: 50}` — the compensation exactly cancels the off-by-one,
  and 60 (5 declared warmup + 50 declared measured + 5 *actual* cooldown) is
  reached with no inflation of the request budget.
- `build_guidellm_command()` passes **two independent stop constraints**:
  `kind=max_requests,count=60` and `kind=max_duration,seconds=5400`.
  GuideLLM stops on whichever fires first — this is what makes 5,400 s a
  ceiling rather than a mandatory runtime (§1, §5). **Reaching the request
  budget does not by itself guarantee 50 measured requests occur** — it
  guarantees that *if* all 60 complete, exactly 50 are measured; if the
  duration ceiling cuts the run off first, the actual measured count is
  whatever fell between warmup's end and the cutoff — §4 covers this case
  with verified partial-completion numbers, also produced by the complete
  path, not a shortcut.
- "Measured" request counts and metrics are computed by
  `GenerativeMetrics.compile()` via
  `accumulator.completed/incomplete/errored.get_within_range(measure_start_time,
  measure_end_time)` — an **overlap** filter, exposed as
  `benchmark.metrics.request_totals`. This is the field OmoBench's
  normalizer reads (fixed in a prior session);
  `scheduler_metrics.requests_made` spans the full warmup+measured+cooldown
  lifecycle and must not be used for reporting.

### Discrepancy flagged, not silently worked around

`build_guidellm_command()` hardcodes `--constraint kind=max_error_rate,rate=0.01`
for **every** profile, rather than reading it from that profile's own
`acceptance.maximum_error_rate_percent`. For `chat-small-v1` and this profile
(both 1%) the values happen to match, so this introduces no live bug. But the
runner-level stop condition and the profile's declared acceptance threshold
remain two independently-maintained numbers, not one derived from the other —
a future profile wanting a different `maximum_error_rate_percent` would
silently get the wrong GuideLLM-level stop behavior unless this hardcode is
also parameterized. Left as a flagged follow-up, not changed here.

### Measured data used for the comparison

From the existing run (`.omobench-runs/run-32dd26a849e5/`, `Intel(R)
Core(TM) Ultra 7 155H`, single-stream CPU llama.cpp, unchanged):
`benchmark.metrics.request_latency.successful.mean` = **58.46 s/request**.
Real, current-format evidence — not re-run for this proposal.

Because the request budget is fixed (60 total, capped by `max_requests`
regardless of ceiling), a longer ceiling does **not** change how many
requests get measured when the ceiling isn't hit — it changes only *how much
of that fixed 60-request run a given ceiling can actually fit*. The right
comparison is the fraction of the ceiling the measured host's full run would
consume — its safety margin — not a hypothetical "how many requests fit in
this much wall-clock time" (that number describes theoretical time-budget
capacity only, useful for nothing here, since the request-count constraint
stops the run at 60 regardless of remaining budget).

| D (ceiling) | time for the full 60-request budget @ 58.46 s/req | margin before the ceiling binds | breakeven rate (ceiling ÷ 60) |
|---|---|---|---|
| 3,600 s (60 min) | 3,508 s (58.5 min) | **92 s — 2.6%** | 60.0 s/req |
| 5,400 s (90 min) | 3,508 s (58.5 min) | 1,892 s — **35.0%** | 90.0 s/req |

**60 minutes is not a safe choice.** The *already-measured* reference host
sits at only 2.6% margin before the 60-minute ceiling would start truncating
its run — any ordinary run-to-run variance (a slower request, thermal
throttling, OS scheduling) could push it over and cut the run short before
all 50 measured requests land. Since the purpose is headroom for hardware
slower than what has already been measured, a ceiling the reference host
itself barely clears defeats the purpose.

**Recommendation: 90 minutes (5,400 s).** It gives the reference host 35%
margin before the ceiling would bind at all, and raises the breakeven rate to
exactly 90.0 s/request — any host up to ~54% slower than the one already
measured still completes the full 60-request budget without being cut off.
It is also a small, easily-communicated number for contributors, unlike an
open-ended alternative.

### Expected runtime vs. maximum permitted runtime

Different things, reported as such (§5):

- **Maximum permitted runtime** is the ceiling: 90 minutes, never exceeded,
  for any hardware.
- **Expected runtime on fast/GPU hardware**: the `max_requests` constraint
  (60) fires first, exactly as `chat-small-v1`'s does today; typical GPU
  decode throughput completes 60 requests in well under a minute per
  request, so the run ends in a couple of minutes, nowhere near 90.
- **Expected runtime on the measured CPU host**: completing the full
  60-request budget takes ≈58.5 minutes (58.46 s × 60) — under the 90-minute
  ceiling, so this host is expected to finish on its own, at its own pace,
  landing on the full 50-request measured sample instead of the 13 it
  reached under the 900 s cap. It does **not** run for the full 90 minutes;
  the ceiling is headroom it does not need, reserved for hardware slower
  than this reference.

## 4. Error-rate findings

### The four unsuccessful requests: what they were, and whether extending duration addresses them

Summary of the completed investigation: the 4 "incomplete" requests recorded
by GuideLLM's scheduler-level `requests_made` for the real 900 s CPU run
were **not** service or inference errors — `errored` was 0 throughout. They
were requests still queued or in flight when the `max_duration` constraint
fired (`duration_exceeded: true` in `scheduler_state`) and got cancelled; the
one such request with retained per-request detail shows `status:
"cancelled"`, occurring after the cooldown boundary, not during measurement.
`metrics.request_totals` (the correctly measured-window-scoped field) shows
**0 incomplete and 0 errored** among the 13 requests that actually overlapped
the measured window.

**Does extending the duration address the cause? Only partly — stated
explicitly rather than implied as fully resolved.**

- What it *does* fix: with explicit request-count phases and the verified
  cooldown compensation (§3), a duration-limited run no longer applies a
  fixed 10%-of-ceiling cooldown window regardless of progress — cooldown's
  request-count trigger only activates once the configured total (60) is
  actually reached. Verified through the complete path: a run cut off after
  only 54 of 60 requests (a duration cutoff at ~100 s/request) never fires
  the cooldown boundary at all, so every completed request past warmup — 49
  of them — is retained as measured, not discarded to a phantom cooldown
  carve-out the old percent-based scheme would have applied unconditionally.
  A much slower cutoff (27 of 60 requests complete, ~200 s/request) still
  correctly retains 22 measured requests — legitimately below the
  40-minimum, and correctly reported as such, not silently inflated or
  truncated further.
- What it does **not** fix, and remains true regardless of the chosen
  ceiling: **any fixed-duration run can still cut off whatever request is
  in flight or queued when the wall-clock budget expires.** This is
  inherent to a duration-capped, single-stream synchronous benchmark, not a
  defect in this profile or in GuideLLM. A longer ceiling and explicit
  request-count phases both make this event less consequential (partial
  evidence is retained cleanly instead of contaminating a fixed cooldown
  window), but neither prevents it from happening. Documented as expected
  behavior, not chased further as an unresolved defect.

### The "49/50 passes" acceptance check, verified

Two cases, both against `acceptance.minimum_successful_requests: 40` and
`acceptance.maximum_error_rate_percent: 1` (unchanged from `chat-small-v1`,
not loosened):

| Case | attempted | successful | incomplete | errored | error rate | Outcome |
|---|---|---|---|---|---|---|
| Duration-limited, still passes | 49 | 49 | 0 | 0 | 0.0% | **Meets acceptance** (49 ≥ 40, 0% ≤ 1%) |
| One failure among a full sample | 50 | 49 | — | 1 | 2.0% | **Fails acceptance** (2% > 1%), *even though* successful count (49) alone would pass |

The second case is deliberately not "discarded because the duration limit
was reached" and not conflated with the first: `attempted` includes the one
failed request (`50 = 49 successful + 1 failed`); OmoBench's normalizer
computes `failed = incomplete + errored` and
`error_rate_percent = 100 * failed / attempted` uniformly — an attempted
request that failed is counted in the denominator whether it failed by
timing out, being cancelled, or erroring, and is never dropped merely
because a duration constraint was involved. `test_slower_run_that_fits_the_
ceiling_reports_the_full_intended_sample` in `cli/tests/test_cli.py` asserts
this exact 50/49/1/2.0% shape; `test_duration_limited_run_retains_partial_
evidence_and_still_meets_acceptance` asserts the 49/49/0/0.0%-passes shape
separately, so the two are not confused with each other. Both were already
correct; this section documents and names them rather than changing
behavior that was already right.

## 5. User experience

Execution and all counting stay in the existing CLI/GuideLLM path
(`build_guidellm_command`, `normalize_guidellm_report`,
`tools/validate_results.py`'s acceptance check) — nothing here adds a second
place that runs or scores a benchmark; this section only proposes wording for
surfaces that already exist.

**CLI** (`omobench run --execute` / `omobench package`, extending the
existing printed summary):

```
Profile: chat-small-extended-v1 (extended duration)
Maximum duration: up to 90 min — stops earlier once 50 measured requests complete
Measured requests: 47 / 50 target (40 minimum to submit)
Benchmark finished. Meets submission requirements.
```

or, for an under-sample run:

```
Measured requests: 22 / 50 target (40 minimum to submit)
Benchmark finished. Does not meet submission requirements
(22 successful requests is below minimum_successful_requests (40)).
```

The second line already exists almost verbatim as the acceptance-check
message in `tools/validate_results.py`; proposed CLI wording just echoes the
same "finished vs. meets requirements" distinction immediately, instead of
only at the later `validate_bundle` step.

**Local panel** (`index.html`/`app.js`): `profileDetail()` already renders
`... up to ${spec.max_requests} measured requests / ${spec.max_duration_seconds
/ 60} min` directly from `PROFILE_SPECS` — with the entry in §2 present, it
shows "up to 60 measured requests / 90 min" with no further UI code change
(a future refinement could special-case the label to read "up to 90 min,
stops at 50 measured" for clarity, but is not required for correctness).
Proposed addition: a short progress line under the run panel while an active
job is on an extended profile, e.g. `Measured so far: 31 / 50 (40 minimum)`,
sourced from the same live job detail already polled every 1.5 s. The
existing `validation-result` text (`job.validation.errors.join("\n")`)
already surfaces the acceptance message verbatim once "Package & validate"
runs, satisfying the "meets submission requirements" half with no further
work.

**Guided panel**: the recipe card already renders `profile
${recipe.benchmark_profile}` generically (`guided.js`); the second recipe
(`qwen3-4b-cpu-extended-v1`, reusing the identical pinned model/runtime
artifacts) shows this profile id automatically. Recipe-selection UI already
supports more than one recipe (it only auto-selects when exactly one exists);
adding a second recipe surfaces a genuine picker with no JS changes.

## 6. Compatibility

- `chat-small-v1`, `chat-long-v1`, and `throughput-v1` are unchanged — no
  file under `benchmarks/profiles/` was edited for them, and their
  `PROFILE_SPECS` entries, runner behavior, and acceptance thresholds are
  untouched.
- No existing run or result is relabeled. A result packaged against
  `chat-small-v1` keeps `benchmark.profile_id: chat-small-v1` and its own
  `profile_sha256` forever; `chat-small-extended-v1` is a wholly new,
  additive profile ID with its own file and hash. The existing CPU run
  (`.omobench-runs/run-32dd26a849e5/`) was inspected read-only throughout
  this work and never repackaged or relabeled.
- `configuration_fingerprint` already includes `benchmark.profile_id`/
  `profile_sha256`, so a `chat-small-extended-v1` run can never collide
  with, or be silently treated as a duplicate of, a `chat-small-v1` run.
- The extended-duration managed recipe (`qwen3-4b-cpu-extended-v1`) pins the
  identical model and runtime artifacts (same URLs, revisions, SHA-256s) as
  the existing `qwen3-4b-cpu-v1` recipe; installing it never triggers a
  second download when the files are already verified on disk.
- Comparisons — on the website and anywhere else — should filter and group
  by exact `profile_id`, exactly as the existing profile filter already
  does; `chat-small-v1` and `chat-small-extended-v1` results should be shown
  as results from two distinct, separately-versioned profiles, never merged
  into one row or aggregate, even though their workload parameters match.

## Changelog

- **First correction, before implementation**: warmup/cooldown changed from
  `warmup=0.1,cooldown=0.1` (percent, `prefer_duration`) to explicit
  `mode=requests` phases; the duration-comparison analysis in §3 was rebuilt
  around margin-before-the-ceiling-binds rather than a hypothetical request
  count a fixed duration budget could theoretically hold (the retracted "73
  requests in 90 minutes" claim, which did not account for the request-count
  constraint stopping the run at its budget regardless of remaining time).
- **Second correction, after review**: the first correction compensated for
  GuideLLM's cooldown off-by-one by inflating `max_requests` to 61 (5 warmup
  + 50 measured + 6 *actual* cooldown), leaving `cooldown_requests: 5` in the
  YAML contradicted by an actual cooldown of 6 — a contract mismatch. Also,
  the verification behind that number checked
  `TransientPhaseConfig.compute_transition_time()` directly, not the
  complete accumulator-and-compile path. Both are corrected here: the runner
  mapping now sets `cooldown.value=4` (not 5) so the *actual* cooldown is 5,
  matching the declared contract exactly at `max_requests: 60` (not 61); and
  every number in §3–§4 is now verified through
  `GenerativeBenchmarkAccumulator.update_estimate()` and
  `GenerativeMetrics.compile()` end to end (`ChatSmallExtendedBoundaryTests`
  in `cli/tests/test_cli.py`, run — not skipped — against the pinned
  GuideLLM 0.7.3 package), not through the boundary function alone.
