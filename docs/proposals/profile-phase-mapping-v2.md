# Proposal: profiles whose declared phases are the phases that run

Status: **`chat-small-v2` is implemented and active. `chat-long-v2` and
`throughput-v2` remain proposals.** No existing profile, runner mapping,
acceptance threshold or published result is changed by any of it: v2 is a new
id with its own file and its own `profile_sha256`, and `chat-small-v1` is
byte-identical to what it always was.

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

## 3. The correction

Keep every existing profile byte-identical. Add new, separately identified
versions whose runner mapping expresses the declaration they already make.

### chat-small-v2: implemented

Registered in `PROFILE_SPECS` and `benchmarks/profiles/chat-small-v2.yaml`:

```python
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

**Verified end to end**, not by replay: `cli/tests/test_chat_small_v2.py`
builds a local WordLevel tokenizer and an isolated streaming fixture server,
runs the command `cli.build_guidellm_command()` produces through
`cli.guidellm_runner_invocation()`, and asserts on the pinned runner's own
report and on `cli.normalize_guidellm_report()`.

`cli/tests/test_measured_window_identity.py` goes further and names every
request. The fixture stamps its arrival index into the response id, which
GuideLLM carries through to `benchmark.requests.*[].response_id`, so each
record in the report maps to one exact arrival at the server. Request numbers
below are *benchmark* requests: the harness sends one readiness POST before
timing anything, and it is subtracted everywhere.

| Case | Scheduled | Served | Warmup (excluded) | Measured | Cooldown | In flight | Never dispatched |
| --- | ---: | ---: | --- | --- | --- | ---: | ---: |
| complete run, 0.02 s/request | 60 | 60 | 1-5 | **6-55 (50)** | 56-60 | 0 | 0 |
| ceiling expires after measurement began | 40 | 39 | 1-5 | 6-39 (34) | none reached | 0 | 1 |
| ceiling expires during warmup | 7 | 5 | **none** | 1-4 (4) | none | 1 | 2 |
| `chat-small-v1`, shipped configuration | 50 | 50 | 1-50 | **0** | none | 0 | 0 |

Two of those rows correct earlier reporting. The v1 contrast runs with v1's own
50-request cap, not 60 — an earlier summary quoted 60, which was v2's budget.
And several "served" figures previously included the readiness probe.

### A run that never establishes its boundary has no measured window

The third row is not a small measurement. It is **not a measurement at all**,
and it is the one case where GuideLLM 0.7.3 cannot express this profile's
contract on its own.

`measure_start` is set on the single scheduler event where the warmup boundary
is recognised, and that event must satisfy *both* halves of
`TransientPhaseConfig.compute_transition_time(period="start")`:
`state.processed_requests == warmup.value + 1` **and** that request's own
`info.started_at is not None`. The equality is exact and evaluated per event,
so the counter can go past it unset. When a duration ceiling expires the
scheduler abandons everything still queued, and a request cancelled before it
reached `in_progress` has no `started_at`; a burst of those carries the counter
beyond `value + 1` with nothing left that could set the boundary. The observed
run finished at `processed_requests = 9` against a `warmup.value = 4` — well
past five — with no boundary at all.

A single such cancellation is not enough: anything that starts while the
counter still equals `value + 1` sets the boundary, one request late. Only a
burst with nothing left to start loses it. Both are pinned against the runner's
own accumulator in `cli/tests/test_measured_window_semantics.py`.

With `measure_start` unset,
`GenerativeBenchmarkTimings.finalized_measure_start` falls back to
`finalized_request_start` — the start of the run. The measured window becomes
the whole run, every warmup request falls inside it, and
`metrics.request_totals` reports **warmup requests as measured**. Observed by
identity: zero requests before the window, requests 1-4 inside it,
`request_totals.total == 4`.

#### How that is detected, and what the detection cannot rule out

`scheduler_metrics.measure_start_time` and `request_start_time` are those two
finalized values, so on the fallback path they are literally **the same
number**. The guard compares them directly rather than trusting the derived
`warmup_duration`, which it uses only when the primary fields are absent.
Non-finite values are rejected rather than compared, because `nan == 0.0` is
false and a corrupt field would otherwise read as healthy.

Equality has exactly two possible causes: the fallback, or a genuine boundary
landing within one float ULP of the run's first request start. At a 2026
timestamp that resolution is ~2.4 × 10⁻⁷ s, and the smallest genuine warmup
measured across every fixture run here is 0.0865 s — about 360,000 ULPs, and
it spans five HTTP round trips. The false positive is therefore not reachable
in practice, but it is a real limit of this evidence and is reported rather
than hidden: the result carries `basis` (which evidence was used) and `checked`
(whether any was conclusive). `scheduler_state.start_requests_time` and the
per-request records are reported as corroboration when the report carries them,
and never required.

`cli.measured_window_integrity()` tests for that, and
`normalize_guidellm_report()` refuses such a report with "this run never left
its warmup phase", in the same way it already refuses a report with no
attempted requests. Nothing is relabelled and no count is adjusted: a run that
never left warmup produces no result.

So the contract is: **a complete successful run measures exactly 50 requests,
after 5 warmup and before 5 cooldown. A run that ends after measurement began
reports what it actually measured, with the 5 warmup requests still excluded. A
run that ends before measurement began produces no result at all.** An error
inside the measured window stays inside it and counts against acceptance; an
error in warmup or cooldown does not reach the measured figures.

Error and in-flight behaviour, from the same harness at the totals level
(`cli/tests/test_chat_small_v2.py` and the phase matrix):

| Case | Measured |
| --- | --- |
| error on the last measured request | 49 successful + 1 errored = 50 |
| error on the first cooldown request | 50 successful |
| a request that hangs until the ceiling | 14, first 5 excluded |

### chat-long-v2 and throughput-v2: still proposals

They follow the same shape: `warmup.value` and `cooldown.value` set to
`declared - 1`, `max_requests` raised to
`declared_warmup + declared_measured + declared_cooldown`, duration ceiling
unchanged. Neither is registered.

`throughput-v2` must not be activated on the strength of the evidence above.
Every end-to-end case here is `kind=synchronous`, one request in flight.
`throughput-v1` runs `kind=concurrent,streams=16,max_concurrency=16`, where
several requests overlap each phase boundary, and GuideLLM's request-count
boundary is evaluated against `state.processed_requests` — which, with
concurrency, does not advance in the order requests were issued. Whether the
`+1` compensation holds there, and whether the boundary is even deterministic
under concurrency, needs its own verification at concurrency 16 before any
`throughput-v2` is registered.

### Why a new version rather than an edit

`benchmark.profile_sha256` is part of every published result and part of the
duplicate-detection fingerprint. Editing `chat-small-v1.yaml` would silently
change what an existing `chat-small-v1` result means while leaving its recorded
hash pointing at bytes that no longer exist. A new id and a new hash keep the two
populations distinguishable for ever: a result carries `profile_id` and
`profile_sha256`, so `chat-small-v1` results remain readable as
"clock-windowed measurement, 9-18 s/request band" and `chat-small-v2` results as
"request-counted measurement".

### Migration, as implemented for the short profile

1. `chat-small-v2` is a new file and a new id; `chat-small-v1.yaml` is
   untouched, so its `profile_sha256` and every result carrying it still mean
   exactly what they meant.
2. A new guided recipe, `qwen3-4b-cpu-v2`, points at it. The existing
   `qwen3-4b-cpu-v1` recipe keeps its id and its model and runtime pins, and is
   retitled "Qwen3 4B · CPU (legacy chat-small-v1)" so it stays available for
   reproducing earlier runs.
3. The guided page badges `qwen3-4b-cpu-v2` as **Recommended**, the extended
   recipe as **Recommended for slower hardware**, and the legacy recipe as
   **Legacy** with a line explaining why. The advanced panel sorts legacy
   profiles last, suffixes them "(legacy)" and never preselects one.
4. No existing result is migrated, re-packaged or re-validated. Results stay
   separated by `profile_id` and `profile_sha256`, which the local report's
   comparison context carries in its workload-definition group, so a v1 and a
   v2 result are never merged into one workload.
5. `chat-small-v1` stays selectable indefinitely; nothing retires it.

### Open questions that remain

- **Concurrency.** `throughput-v1`'s request-count behaviour at
  `max_concurrency=16` is unverified; see above. This is the blocking item for
  `throughput-v2`.
- **The ceiling.** `chat-small-v2` keeps the 900 s ceiling, so a host slower
  than about 15 s per request still cannot complete 60 requests inside it and
  will report a partial measured window. That is now reported honestly rather
  than silently, and `chat-small-extended-v1` exists for such hosts, but
  whether the short profile's ceiling should rise is a separate decision that
  would change what a `chat-small-v2` result means.
- **Acceptance.** v2 keeps v1's thresholds (40 minimum successful requests, 1%
  maximum error rate) unchanged. No incompatibility was found: a complete v2 run
  measures 50, comfortably above 40.

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
