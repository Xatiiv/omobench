# Proposal: profiles whose declared phases are the phases that run

Status: **proposed, not active.** Nothing here changes an existing profile, an
existing runner mapping, an acceptance threshold or any published result. The
corrected definitions below are deliberately absent from `PROFILE_SPECS` and
from `benchmarks/profiles/`, so no run can select them until this is reviewed.

## 1. The confirmed semantics

Three of the four shipped profiles map warmup and cooldown to a fraction:

```
chat-small-v1       kind=synchronous,warmup=0.1,cooldown=0.1
chat-long-v1        kind=synchronous,warmup=0.1,cooldown=0.1
throughput-v1       kind=concurrent,streams=16,max_concurrency=16,warmup=0.1,cooldown=0.1
```

GuideLLM 0.7.3 turns a bare fraction into
`TransientPhaseConfig(percent=0.1, mode="prefer_duration")`
(`guidellm/benchmark/schemas/base.py`, `create_from_value`, lines 62-90: a value
below 1.0 becomes `percent`, and `mode` defaults to `prefer_duration`).

`TransientPhaseConfig.compute_limits()` (same file, lines 122-158) then computes
*both* a duration and a request count from that fraction and discards the
request count:

```python
if self.mode != "requests" and max_seconds is not None:
    duration = self.percent * max_seconds          # 0.1 * 900 = 90.0
if self.mode != "duration" and max_requests is not None:
    requests = int(self.percent * max_requests)    # int(0.1 * 50) = 5
if enforce_preference:
    if self.mode == "prefer_duration" and duration is not None:
        requests = None                            # the 5 is thrown away
```

`compute_transition_time()` (lines 160-248) uses those limits against
`state.progress.total_duration` and `state.start_time`, so for a warmup it
returns `start_time + 90` and for a cooldown `start_time + total_duration - 90`.
`GenerativeBenchmarkTimings.update_estimate()`
(`guidellm/benchmark/schemas/accumulator.py`, lines 148-190) assigns those to
`measure_start`/`measure_end` on the **first** scheduler event, before any
request has finished.

So **`warmup=0.1` on a profile with a duration budget is a 90-second warmup, not
a five-request warmup**, and the measured window is a fixed slice of the clock.
Only an explicit `mode=requests` phase counts requests
(`chat-small-extended-v1`).

Verified, not inferred: `cli/tests/test_profile_phase_semantics.py` drives the
pinned runner's own `GenerativeBenchmarkAccumulator` and
`GenerativeMetrics.compile()` with synthetic request events and asserts the
resulting `measure_start`/`measure_end` and `request_totals`.

### Correction to an earlier report

A previous session reported that `chat-small-v1`'s percentage scheme meant
"10% of 50 requests = 5 warmup and 5 cooldown requests, leaving 40 measured with
zero tolerance". **That was wrong**, in kind and not only in number: the
fraction never reaches the request count, because a duration budget exists.
`docs/proposals/chat-small-extended-v1.md` §"Two changes from chat-small-v1"
already described this correctly; the earlier report contradicted the
repository's own analysis and should not have.

## 2. Declared versus enforced, per profile

| Profile | Declared (YAML `execution`) | Runner mapping | Enforced limits | Phases as executed | Measured window | Declaration matches? |
| --- | --- | --- | --- | --- | --- | --- |
| `chat-small-v1` | 50 measured, 5 warmup, 5 cooldown | `warmup=0.1,cooldown=0.1` | 50 requests, 900 s | **duration**: 90 s / 90 s | middle **720 s** of 900 s | **no** |
| `chat-long-v1` | 30 measured, 3 warmup, 3 cooldown | `warmup=0.1,cooldown=0.1` | 30 requests, 1800 s | **duration**: 180 s / 180 s | middle **1440 s** of 1800 s | **no** |
| `throughput-v1` | 160 measured, 16 warmup, 16 cooldown | `warmup=0.1,cooldown=0.1` | 160 requests, 900 s | **duration**: 90 s / 90 s | middle **720 s** of 900 s | **no** |
| `chat-small-extended-v1` | 50 measured, 5 warmup, 5 cooldown | `warmup.value=4,mode=requests`, `cooldown.value=4,mode=requests` | 60 requests, 5400 s | **requests**: 5 / 5 actual | 50 requests | **yes** |

`cli.profile_phase_contract()` computes this table from OmoBench's own mapping
and each profile's own YAML; `DeclaredVersusActualTests` pins it.

### What the mismatch costs

For `chat-small-v1`, measured against the pinned runner with 50 sequential
requests and a 900-second limit:

| Per-request latency | Requests completed | Requests measured | Acceptance (≥ 40) |
| --- | --- | --- | --- |
| 1 s | 50 | **0** | fails |
| 5 s | 50 | 32 | fails |
| 9 s | 50 | 40 | passes |
| 12 s | 50 | 43 | passes |
| 16 s | 50 | 45 | passes |
| 18 s | 50 | 40 | passes |
| 20 s | 45 | 37 | fails |
| 45 s | 20 | 16 | fails |

The profile is satisfiable only in a latency band of roughly **9-18 seconds per
request**. A host fast enough to answer in a second finishes every request inside
the 90-second warmup and measures nothing at all, which OmoBench's normalizer
then refuses ("GuideLLM report contains no attempted requests"). A host slow
enough to be duration-bound never accumulates 40 measured requests. Neither
failure is visible in the profile's own declaration.

This is consistent with the only real `chat-small-v1` evidence in this checkout:
12 attempted, 12 succeeded, well below its 40-request floor.

## 3. Proposed correction

Keep every existing profile byte-identical. Add new, separately identified
versions whose runner mapping expresses the declaration they already make:

```python
# NOT YET REGISTERED IN PROFILE_SPECS -- proposal only.
"chat-small-v2": {
    "input_tokens": 1024,
    "output_tokens": 256,
    # Request-count phases, compensated for GuideLLM 0.7.3's documented
    # off-by-one (asking for N excludes N+1), exactly as
    # chat-small-extended-v1 already does.
    "profile": "kind=synchronous,warmup.value=4,warmup.mode=requests,cooldown.value=4,cooldown.mode=requests",
    "max_requests": 60,          # 5 warmup + 50 measured + 5 cooldown
    "max_duration_seconds": 900,  # unchanged ceiling, now a ceiling rather than the phase basis
},
```

`chat-long-v2` and `throughput-v2` follow the same shape: `warmup.value` and
`cooldown.value` set to `declared - 1`, `max_requests` raised to
`declared_warmup + declared_measured + declared_cooldown`, duration ceiling
unchanged.

Each needs its own `benchmarks/profiles/<id>.yaml` with the same workload and
the same acceptance thresholds, so the only change is that the declared phases
become the executed phases.

### Why a new version rather than an edit

`benchmark.profile_sha256` is part of every published result and part of the
duplicate-detection fingerprint. Editing `chat-small-v1.yaml` would silently
change what an existing `chat-small-v1` result means while leaving its recorded
hash pointing at bytes that no longer exist. A new id and a new hash keep the two
populations distinguishable for ever: a result carries `profile_id` and
`profile_sha256`, so `chat-small-v1` results remain readable as
"clock-windowed measurement, 9-18 s/request band" and `chat-small-v2` results as
"request-counted measurement".

### Migration

1. Land the new profiles unreferenced, with this document.
2. Point the guided recipes at `*-v2` once reviewed; leave `*-v1` selectable in
   the advanced panel so historical runs can be reproduced.
3. Do not migrate, re-package or re-validate any existing result. The comparison
   site should group by `profile_id`, which it already does.
4. Retire `*-v1` from the recipe list only after `*-v2` results exist.

### Open questions for review

- Whether `throughput-v1`'s concurrency 16 interacts with request-count phases
  differently from a synchronous profile; the off-by-one was verified only for
  `kind=synchronous`, and this proposal should not assume it transfers.
- Whether the duration ceiling should stay at 900 s for `chat-small-v2`, given
  that 60 requests at the slow end of the current band (18 s) need 1080 s.
- Whether `chat-small-extended-v1` makes `chat-small-v2` redundant for CPU hosts.

## 4. What ships now, without waiting for this

The panel no longer presents a declared target as a guarantee:

- The live dashboard states the measured window for what it is, and names the
  declared counts as not enforced when they are not
  (`measured-window-note` in `dashboard.js`).
- Guided recipe cards say "Declares N measured requests; the runner measures the
  middle X s of its Y s limit, so the number actually measured depends on how
  long each request takes."
- "of at most N" is shown only from the request limit OmoBench itself configured
  and the runner enforces; the scheduler's own reported total appears separately
  in technical details, labelled as the scheduler's figure.
