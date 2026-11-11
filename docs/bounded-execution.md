# Bounded execution, and runs that were interrupted

A benchmark that cannot be stopped is not bounded, and a benchmark that was
not running is not a measurement. Both of those turned out to be true of
OmoBench, and both were found by a real run rather than by reading the code.

This page is the contract that came out of it: what each limit bounds, who
enforces it, what is recorded, and what is refused.

## What went wrong

A ninety-minute CPU profile on a laptop produced six successful measured
requests against a minimum of forty, and took two and a half hours to do it.

Its first eight requests took **58 to 78 seconds each** — the hardware
working exactly as it should; sixty of those is about an hour, comfortably
inside the ceiling. Then request nine took 1,505 seconds, request ten 1,279,
and request eleven **5,793**.

Nothing was wrong with the model, the processor, or the memory the
distribution had. The Windows host it ran inside repeatedly entered **Modern
Standby**, and a WSL distribution is suspended along with the machine. A
benchmark cannot tell a paused virtual machine from slow inference: it
records the wait as the model's latency, and the result was a p95 time to
first token of 1,495 seconds for a model that generates at about eight tokens
a second.

The panel's own one-minute poll loop, which does nothing but sleep, returned
13, 16, 41 and 72 minutes late. Those gaps line up to the minute with the
host's sleep and wake events.

## The four limits

They were being conflated, which is how a run with a ninety-minute ceiling
ran for two and a half hours. They are different things, they bound different
quantities, and two of them are not the runner's to enforce.

| Limit | Bounds | Enforced by | Where it comes from |
| --- | --- | --- | --- |
| **Inactivity timeout** | The gap between two chunks of one response | The runner's HTTP client | `execution.request_timeout_seconds` in the profile |
| **Request deadline** | One request end to end, however lively its stream | OmoBench's supervisor | `execution.request_deadline_seconds`, defaulting to the ceiling |
| **Measurement ceiling** | When the run stops starting new work | The runner's constraint, with the supervisor as backstop | `execution.maximum_duration_seconds` |
| **Shutdown grace** | How long a stop may take before the process group is killed | OmoBench's supervisor | 60 seconds |

### The inactivity timeout is not a request deadline

`request_timeout_seconds: 300` becomes the runner's backend `timeout`, which
it hands to httpx as `Timeout(read=300, connect=…)`. **httpx's read timeout
is the maximum gap between chunks**, not a total. A stream delivering a token
every few seconds never trips it, however long the whole request runs — which
is exactly how one request ran for 5,793 seconds under a 300-second timeout.

### The measurement ceiling was not a timer

The pinned runner's `MaxDurationConstraint` does ask for everything to stop.
It returns `request_queuing="stop"` **and** `request_processing="stop_local"`,
and the worker cancels the request it is processing when that arrives. In the
run that overran, once the constraint fired the scheduler exited **0.1
seconds later** and the one request that had been queued but never started
was recorded `cancelled`. Nothing drains.

The defect is *when* it fires. The constraint is a function the scheduler
evaluates while handling a request update — there is no timer. With a
concurrency of one, a single request that streams for ninety-six minutes
offers no update to evaluate it on, and a suspended virtual machine evaluates
nothing at all. The constraint's own recorded `elapsed_time` when it finally
fired was **9,101 seconds against a 5,400-second ceiling**.

The `stop_time` of 5,400 s in such a report is not an observation. It is
`start_time + max_duration`, written by the constraint after the fact. Read
`end_queuing_time` and `end_processing_time` for when anything happened.

## The supervisor

An independent timer, deliberately outside the control flow it watches —
because the thing it guards against is that control flow never reaching the
check.

- It asks for a graceful stop first. That is what writes the evidence: the
  runner has to unwind its scheduler, compile its report and flush it.
- It gives that stop **60 seconds**, then escalates. A runner that ignores
  the polite signal cannot hold a profile open.
- The measurement-window backstop allows the whole ceiling **plus** that
  grace before it acts, so it can never shorten a profile that is behaving.
- The request deadline is measured from the last completed request, read from
  the telemetry snapshot the run already writes rather than from inside the
  runner.

### What the window is counted from

Not this process starting. The runner's own ceiling begins at its first
request, so loading weights, building the dataset and reading the tokenizer
belong to neither the ceiling nor the grace — counting from process start
would charge every profile for its setup. The supervisor anchors to the
measurement start the run itself reports (`anchor: "measurement-start"`), and
falls back to process start only when the run never reports a first request,
which is what still bounds a setup that wedges (`anchor: "run-start"`). The
anchor is recorded alongside the deadline, because a deadline without the
moment it was measured from says nothing.

Three spans, kept apart:

```
 ├── startup ──┤├──────── measurement ceiling ────────┤├── grace ──┤
 load, dataset  warmup → measured → cooldown requests   a stop may
 (outside both)        (all inside the ceiling)          take this long
```

**Nothing inside the run can push that anchor later.** The earliest implied
measurement start wins and never moves, so a telemetry file that stops being
written cannot buy more deadline as the run does less; and a request that
begins just before the ceiling and streams for ever resets only its *own*
deadline, never the window's.

### What it cannot promise

**A wall-clock guarantee while the machine is suspended.** A supervisor
inside a paused virtual machine does not execute either. It fires on resume,
having measured the time it was not running, and the run is marked
interrupted rather than pretended about. Anything that claims otherwise is
claiming a thread ran while its CPU was switched off.

Escalation on the in-process path costs the files the run would have written
afterwards, so a run that reaches it ends incomplete and says so. That is the
trade: a run nobody can stop is worse than a run that ends incomplete.

### Contributor cancellation

Unchanged and still bounded: `SIGTERM` to the whole process group, three
seconds, then `SIGKILL`. Tested against a runner that installs `SIG_IGN` for
`SIGTERM`.

## Keeping the machine awake

For a Windows host with a WSL runner — the managed path and the bridge — the
panel asks Windows for a power request through the companion, which is the
only part of OmoBench that is a Windows program.

It asks for **`PowerRequestExecutionRequired`**, not
`PowerRequestSystemRequired`: on a Modern Standby machine the system can
still transition to its low-power idle state while the latter is held. Where
Windows refuses the stronger kind, the weaker one is taken and **reported as
the weaker one**, because it does not survive the transition that caused all
this.

`PowerRequestDisplayRequired` is never asked for. A benchmark needs the
machine awake, not the screen lit.

The request lives exactly as long as the companion process's standard input
stays open. There is no timeout to get wrong and no signal to miss: the panel
closes the pipe when measuring ends, and a panel that is killed outright
closes it by dying. Success, failure, cancellation and termination all
release it.

**This is a request, not a lock.** Closing the lid, a critical battery, an
administrator's policy, a forced sleep from the power menu and a hibernation
timer can all still suspend the machine. The panel says whether the request
was granted, in those terms, and never claims the machine cannot sleep.

When there is nobody to ask — the panel was not opened through the companion,
or the companion predates this — the check says so prominently before the
run, and says what to do instead: turn off sleep yourself for the length of
the run, stay on power, do not close the lid. It never blocks a run.
Somebody who has already disabled sleep needs no help from this.

## Detecting an interruption

Wall time and monotonic time advance together on a machine that is running.
They diverge when it is not: wall time is corrected from the host on resume,
while monotonic time does not count what was never executed. OmoBench samples
both (and `CLOCK_BOOTTIME`, where the kernel keeps its own suspend record)
once a second while measuring.

| State | What it means | Threshold |
| --- | --- | --- |
| `none` | Nothing observed | — |
| `gap` | Both clocks agreed on a jump nobody can account for: something did not get to run | 30 s beyond the sampling interval |
| `clock-jump` | The wall clock disagreed with the monotonic clock while the kernel recorded no suspend — **unconfirmed** | 5 s |
| `suspended` | `CLOCK_BOOTTIME` outran `CLOCK_MONOTONIC`: the kernel's own record of having slept — **confirmed** | 5 s |

### Why `clock-jump` is not promoted to `suspended`

Measured on this project's own WSL2 distribution (kernel
`5.15.167.4-microsoft-standard-WSL2`), after it had been up for 2 h 23 m
through several host sleep cycles:

```
CLOCK_BOOTTIME − CLOCK_MONOTONIC : 0.000 s
worst |wall − monotonic| drift   : 0.002 ms per second
timedatectl                      : System clock synchronized: yes, NTP service: active
```

Two things follow. **A Hyper-V pause is invisible to the guest kernel** — it
records no suspend, so `CLOCK_BOOTTIME` cannot detect the exact failure that
started all of this. And **NTP is active**, so the wall clock can be stepped.
A wall-against-monotonic divergence therefore has two possible causes, and
from inside the machine nothing distinguishes them. Calling it a confirmed
suspension would be claiming to know which.

The thresholds are defensible because the drift is four orders of magnitude
below them: seconds of divergence are an event, not noise.

Both states keep a result out of the registry, with different sentences —
one says the machine slept, the other says it cannot be established that the
machine was running. A `clock-jump` from a genuine NTP step will refuse a
good run; that is the deliberate trade, because publishing a measurement
that might contain a pause is worse, and the bundle stays fully available
for diagnosis either way.

Three rules about this:

- **Nothing is inferred from a slow model response.** A slow response is a
  measurement. Only a clock disagreeing with another clock is evidence about
  the machine.
- **Nothing is subtracted.** No suspended time is ever taken off a measured
  latency to publish a tidier number. The record reports; it does not
  correct.
- **A gap is a question, not a verdict.** It is recorded and it is not
  refused. Treating a busy machine's scheduling delay as a suspension would
  quietly throw away good results from loaded hardware.

## What is recorded, and where

Schema **1.1 only**, under a new top-level `execution` object. 1.0 is frozen
and the schema forbids it there, so a stored 1.0 bundle never acquires a fact
nobody collected.

```json
"execution": {
  "contract": "bounded-execution-v1",
  "limits": {
    "inactivity_timeout_seconds": 300,
    "request_deadline_seconds": 5400,
    "measurement_ceiling_seconds": 5400,
    "shutdown_grace_seconds": 60
  },
  "supervisor": { "fired": false, "reason": null, "escalated": false,
                  "at_seconds": null, "anchor": "measurement-start", "detail": "" },
  "interruption": { "state": "none", "observed_seconds": 0,
                    "longest_seconds": 0, "events": [], "method": "…" },
  "sleep_prevention": { "requested": true, "held": true,
                        "mechanism": "windows-power-request-v1",
                        "request": "execution-required", "detail": "…" }
}
```

It is written into the run's own `environment.json` while the run happens,
and copied from there at packaging time. Never recomputed: a clock read today
says nothing about whether a benchmark executed last week. A run with no
record has nothing to say, and **nothing is inferred for it** — "nobody
looked" and "nothing happened" are different claims.

### Does this change what a result measures?

No, and that is deliberate.

- No shipped profile declares `request_deadline_seconds`, so the request
  deadline defaults to the measurement ceiling — which is not a new limit at
  all, since a request cannot outlive the window it runs in.
- The supervisor's window backstop only acts after the ceiling *and* its
  grace, so a profile that behaves is never shortened.
- A profile that does declare a tighter deadline would be a different
  execution contract and gets its own version. A test guards that none does
  today.

## Eligibility

Three separate verdicts, and none of them stands in for another:

| Verdict | What it answers | What it blocks |
| --- | --- | --- |
| Schema validity | Is this a well-formed bundle with intact evidence? | Packaging |
| Privacy | Does it name the contributor or carry a credential? | Submission |
| Acceptance | Did the run measure enough, cleanly enough? | Submission |
| Interruption | Was the machine running while it measured? | Submission |

A **confirmed suspension** and an **ambiguous clock jump** are both refused by
the registry validator, and the panel's submit gate says so before you try.
Each gets its own sentence: falling short of a threshold means the run did not
measure enough; a suspension means what it did measure is not about the
model; a clock jump means it is not established that the machine was running
throughout. Three verdicts, three sentences, and none of them borrows another's
words.

The same rule is enforced in three places and tested in each: packaging reads
the record from the run's own evidence, the registry validator refuses the
bundle, and the website refuses to rank it.

The website's comparison never ranks such a result under any question, as a
second line of defence for bundles read from a file, a fork or a branch that
never passed the registry.

**None of this blocks diagnosis.** An interrupted run stays packageable where
its evidence meets the schema, its bundle stays downloadable, its preview
opens, and its run-details page renders. Acceptance criteria block public
submission, not looking at your own evidence.
