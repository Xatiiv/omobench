# The guided workflow, the three gates, and comparison questions

Three ideas this document exists for: what the panel means by a stage, why
"can I run this?" and "can I publish this?" are different questions, and why
"are these two results comparable?" cannot be answered without knowing what
is being asked.

## Two ways in, one workflow

The panel's front page asks one question first: do you want OmoBench to set a
model server up for you, or do you already run one?

- **The managed setup** downloads one reviewed model and runtime, verifies both
  against their pinned hashes, and starts the server itself. CPU only.
- **A server you already run** is the `/advanced` panel: llama.cpp, vLLM, LM
  Studio, a Windows server measured from WSL through the bridge. CPU or GPU.

They differ in who starts the server and in nothing else. The same engine runs
the same fixed profile, writes the same evidence, and is held to the same
requirements — and both pages render the *same* six stages, three gates and six
completion states, computed once in `cli/src/omobench/workflow.py` and drawn
once by `cli/src/omobench/web/workflow.js`. Neither page decides for itself
whether a run is ready.

## Six stages, derived from state

    Connect → Identify → Check → Run → Review → Submit

A stage is done because the thing it stands for happened, not because a tab
was clicked. Connect is done once a server check has succeeded for the address
in the form. Identify is done once the model is settled — the catalog
has it, the result declares its own eligibility, or a complete proposal
accompanies it. Check is done when nothing is
standing in the way of running. Run is done when a benchmark produced
measurements. Review is done when the registry validator accepted the bundle.
Submit is done when a pull request exists.

Because the stages are computed rather than remembered, going back to fix
something costs nothing. Nothing is torn down and no field is cleared: the
page recomputes from what is true now, and the stages after the one being
revisited stay done if they still are.

## Three gates

The same list of checks answers three different questions, and every check
says which one it belongs to.

| Gate | What it decides |
| --- | --- |
| `run` | Whether a benchmark can start, and whether it would measure anything |
| `package` | Whether the evidence can become a schema-valid bundle |
| `submit` | Whether the public registry will take that bundle |

Nothing here relaxes anything — packaging and `tools/validate_results.py`
remain the authority. What this does is report their verdicts early enough to
act on, and keep them apart.

**A model awaiting catalog review blocks submission and nothing else.** The
benchmark runs, the measurements are real, the evidence is complete and the
bundle validates, because none of that depends on somebody else's pending
decision. The converse holds too: a bundle that validates is not thereby a
result the registry will take, and the panel never says it is.

Only an actual runner constraint stops a run. An unloaded tokenizer and an
unreachable server are two; an uncatalogued model and a missing weights
revision are not. The panel shows what is outstanding beside the start button
and lets you start anyway.

### Every check says the same five things

What was looked at, what was seen, where that came from, which gate it blocks,
and one specific thing to do about it.

Three ways of not being ready are distinguished, because they call for
different things:

- **present and wrong** — a revision that moves, a server that refused. A
  failure.
- **not supplied yet** — before a run, an ordinary statement of what will be
  needed rather than a fault.
- **nobody checked** — never reported as a pass. A tokenizer nobody has loaded
  is as much in the way as one that failed to load.

## Identify: two names for one model

Your server answers to whatever identifier it was configured with. The catalog
has its own canonical identity. These are different strings and OmoBench keeps
them apart: `model.id` is the catalog identity, `model.served_model_id` is the
alias.

**Suggest a catalog model** offers entries the alias may mean and applies none
of them. One match is a suggestion to confirm; several make the mapping
ambiguous and you choose, because choosing for you would publish a measurement
under a model nobody ran.

When nothing matches, there are two ways on and the panel offers both.

The first is the model's own **eligibility**: the licence its publisher
states, an https URL where that licence can be read, that anybody can download
the weights, and that it is a text-generation model. Typed into the custom
model fields, those four facts go into the bundle as `model.eligibility` under
schema 1.2, and the model is recorded as `uncatalogued` — not on the list, and
not asking to be. The panel reports which of the four are still missing. It
verifies none of them: it does not open the licence, does not fetch the
weights, and labels every value as the contributor's word. See
[model-eligibility.md](model-eligibility.md).

The second is a **proposal**, which opens when it is the route left. It tells
you what it still lacks, what gets written beside the evidence
(`model-proposal.json`), and exactly what a reviewer would receive. A proposal
is a request for review: accepting it is a separate change to
`catalog/models/`, and merging the result would not make it. See
[provenance.md](provenance.md).

The two are alternatives, not steps. Opening the proposal section is not
starting a proposal, and a proposal nobody has typed into never overrides a
complete declaration. Where both are present, the proposal wins: it is an
explicit request somebody else has to answer.

## Check: load the tokenizer before the benchmark does

GuideLLM resolves the tokenizer before it sends a single request, so one it
cannot load ends the run having measured nothing. The usual case is a GGUF
repository, which serves perfectly well and contains no Transformers
tokenizer at all.

**Load tokenizer now** loads it in the interpreter the benchmark will use.
**Check tokenizer** stays offline and only looks at the shape of the
identifier and whether its files are already here. Neither establishes that
the tokenizer matches the served weights, and neither pretends to.

## Check: a profile your hardware can finish

Every profile stops on whichever comes first: its request budget, or its
duration ceiling. Which one arrives first is the single most consequential
thing about running one, and it is not obvious from either number.

When the budget finishes first, the run ends having measured what it set out to
measure. When the **ceiling** arrives first, the scheduler stops queuing,
abandons what it has not started, and cancels the request still streaming. That
cancelled request is attempted-but-not-successful inside the measured window —
so a run that ends on the clock carries an error it did not earn. One
cancellation in a thirteen-request sample is a 7.7% error rate against a
threshold of 1%.

The arithmetic worth knowing beforehand is the ceiling over the request budget:

| Profile | Budget | Ceiling | Time per request |
| --- | --- | --- | --- |
| `chat-small-v2` | 60 requests | 15 min | 15 s |
| `chat-small-extended-v1` | 60 requests | 90 min | 90 s |

The two are the *same workload* — same prompt, same seed, same 1,024 input and
256 requested output tokens, same phase counts, same acceptance thresholds of
40 successful measured requests and at most 1% errors. The extended profile is
not an easier profile. It is the same measurement with more clock, for hardware
whose requests do not fit in fifteen seconds each.

Measured on this project's own laptop CPU, a 4B model quantized to Q4_K_M took
roughly 52 seconds per request. That does not fit `chat-small-v2`, and the
consequence was not a slow result but an ineligible one.

The same machine on `chat-small-extended-v1` is worth describing, because it
failed differently and for a reason worth recognising. It produced 28
successful measured requests with **no errors at all** and still fell short of
the 40 the profile requires. Most of its requests took 43 to 77 seconds — but
five of the thirty-three took 297, 436, 509, 718 and 1,621 seconds, and
between those stalls the run went back to its usual speed. That is not a slow
processor and it is not thermal decline, both of which are gradual. The
environment it ran in had 0.26 GB of memory available with its swap entirely
full.

What that environment was is the point. The benchmark ran inside a WSL
distribution with a 7.49 GB allocation on a computer with 15.46 GB installed.
Measured on the same machine with nothing else running, the managed server
freshly started is resident at **4.30 GB, leaving 4.51 GB available with swap
untouched** — which looks like ample room for a 2.5 GB model.

It is not, and a later attempt proved why. **The server's resident set grows
with the requests it serves.** Over a 50-minute run of the same profile it
went from 4.30 GB to **6.99 GB**, consumed the distribution's 2 GB of swap on
the way, dragged generation from about 8 tokens a second down to 0.83, and
was then killed outright by the kernel:

```
Out of memory: Killed process 197464 (llama-server)
  total-vm:12792992kB, anon-rss:6990392kB
```

Thirty consecutive requests had taken 48 to 73 seconds each before that. The
four that followed took 308 to 335 seconds, and then the server stopped
answering.

So "4.30 GB resident" is a figure about a server that has just started, and
nothing should be sized from it. What a long run needs is headroom for a
footprint that keeps growing — on this hardware, more than a 7.49 GB
allocation has to give once the runner, its tokenizer and the machine's own
services are in it too.

So a host like that does not need a profile with weaker criteria; the numbers
it produced are not describing the inference stack. This is why the panel
reads out what the environment has left before the run — and why it says
*which* environment, because inside WSL that total is an allocation rather
than the computer's memory, and on a bridged run the model is served on a
machine this reading knows nothing about. Severe pressure is marked as
severe. The check never stops a run — a contributor may know exactly why
their environment looks like that — and no acceptance threshold moves because
of it.

### What a reading taken inside WSL cannot see

A later run on the same machine, with the distribution quiet and 6.5 GB of
its 7.49 GB free at the start, failed anyway — and failed for a reason
`/proc/meminfo` is structurally unable to report.

Its first eight requests took **58 to 78 seconds each**, which is the
hardware working exactly as it should: sixty of those is about an hour,
comfortably inside the ninety-minute ceiling. Then request nine took 1,505
seconds, request ten 1,279, and request eleven **5,793**. Throughout, the
distribution reported over 2 GB available and essentially no swap in use.

The computer underneath it did not. The Windows host has 15.46 GB installed
and was down to around 680 MB available, with its page file 7.5 GB in use —
and it repeatedly entered **Modern Standby**, which suspends a WSL
distribution along with the rest of the machine. The panel's own one-minute
poll loop, which does nothing but sleep, returned 13, 16, 41 and 72 minutes
late; those gaps line up to the minute with the host's sleep and wake events.
A benchmark cannot tell a suspended virtual machine from slow inference. It
records the wait as the model's latency, and the result is a p95 time to
first token of 1,494 seconds for a model that generates at about 8 tokens a
second.

So the panel says what the reading does not cover, as a note that gates
nothing: inside WSL, the host's memory and its power state are not readable,
and a long profile needs the computer awake, on power, and not short of
memory. That is the honest extent of what a panel running inside a
distribution can offer about the machine outside it.

Nor does the panel act on it. Swap filling up means the kernel has had to
move pages out at some point; Linux also swaps idle pages out on a
comfortable host, so a full swap file is not by itself proof that a run is
paging. The choices are stated and left to the contributor: close what else
is resident, serve a smaller model if one is installed, give the distribution
more memory (a `memory=` line in `%USERPROFILE%\.wslconfig` and
`wsl --shutdown` — OmoBench does not write that file), or serve the model
elsewhere and measure it across the bridge. Nothing is changed and no process
is stopped on anybody's behalf.

The panel now says all of this on the page, before the run: what the profile
executes, when it stops, how much time the budget allows per request, what
qualifying takes, and what happens if the ceiling wins. None of it is a
prediction about your machine, and the panel never estimates how long your run
will take.

A contributor-initiated cancel is bounded: it sends the whole process group a
`SIGTERM`, gives it three seconds, and then kills it, so a runner that ignores
the polite signal cannot hold a ninety-minute profile open.

**A ceiling-driven stop was not**, and the reason is worth stating exactly,
because it is not the one it first looks like.

The pinned runner's duration constraint does ask for everything to stop. It
returns `request_queuing="stop"` *and* `request_processing="stop_local"`, and
the worker cancels the request it is processing when that arrives. In the run
that overran, once the constraint fired the scheduler exited **0.1 seconds
later** and the one request that had been queued but never started was
recorded `cancelled`. Nothing drains.

The defect is *when* it fires. The constraint is a function the scheduler
evaluates while handling a request update — there is no timer. With a
concurrency of one, a single request that streams for 96 minutes produces no
update for the scheduler to evaluate it on, and a suspended virtual machine
evaluates nothing at all. In that run the constraint's own recorded
`elapsed_time` when it finally fired was **9,101 seconds against a 5,400-second
ceiling**: 3,701 seconds late. The `stop_time` of 5,400 s in the report is not
an observation — it is `start_time + max_duration`, written by the constraint
after the fact.

The profile's `request_timeout_seconds: 300` does not cap it either. It
becomes the backend's `timeout`, which the runner passes to httpx as
`Timeout(read=300, connect=…)`, and httpx's read timeout is the maximum gap
*between chunks*. A stream delivering a token every few seconds never trips
it, however long the whole request runs.

So four different limits have to be named separately, and OmoBench now does:

| Limit | What it bounds | Who enforces it |
| --- | --- | --- |
| Inactivity timeout | The gap between two chunks of one response | the runner's HTTP client |
| Request deadline | One request end to end, however lively its stream | OmoBench's supervisor |
| Measurement ceiling | When the run stops starting new work | the runner's constraint |
| Shutdown grace | How long a stop may take before the process group is killed | OmoBench's supervisor |

See [bounded-execution.md](bounded-execution.md).

## Review: what the evidence says

The evidence summary turns a result into labelled rows: the runner against the
serving environment, the accelerators and what each one did, the canonical
model and the alias, the weight identity, the engine build separately from the
application release, the tokenizer, and the transport with its own
qualification.

Three rules shape it.

A fact nobody recorded reads as **Not recorded** rather than being left out,
because a missing row is a gap a reader fills with an assumption. A schema 1.0
result therefore keeps every fact it has, gains none it never recorded, and
says which of its gaps are gaps.

**Nothing is scored.** There is no number summarising how trustworthy a result
is, because the facts differ in kind rather than degree: a detected processor
and a declared GPU memory are not two points on one scale.

**Detection is not verification.** Reading a fact on a machine establishes what
that machine reports about itself. Nothing here independently confirms that
the hardware named did the work.

The panel and the website render the same summary from the same result;
`cli/tests/test_evidence_parity.py` holds the two implementations to identical
output.

## Review: six states, none assumed

| State | Confirmed by |
| --- | --- |
| Benchmark completed | measurements exist |
| Evidence saved | the run's own files are on disk |
| Bundle validated | packaging wrote a bundle and no packaging check is failing |
| Ready to submit | the validator accepted it and nothing is outstanding on the submit gate |
| Submitted for review | a pull request exists |
| Accepted and published | nothing this panel can observe |

The last is never shown as done. Acceptance follows review, a merge and a
website build, none of which the panel sees — so it reports the pull request
and stops there.

These describe what happened, not what the form currently says. Reopening a
run clears the packaging fields, which puts every packaging check back to
outstanding; outstanding is not refused, and a bundle that was built stays
built. Only a check that is present and wrong — a revision that moves, a
runtime version that is not one — reports the bundle as refused.

A failure after the benchmark never costs the benchmark. A refused bundle
leaves every evidence file untouched and can be packaged again; a submission
that started and produced no pull request says so and can be retried without
re-running anything.

## Comparison questions

"Comparable" is not a property of two runs. It is a property of two runs **and
a question**. Asking which of two machines is faster requires the model and the
workload to match and requires the hardware to differ; calling that pair
incomparable because their hardware differs answers a question nobody asked.

| Question | Meant to differ | Held fixed |
| --- | --- | --- |
| Repeat of the same setup | nothing | everything |
| Different hardware | the machine, and how the weights sat on it | model, weights, quantization, workload, tokenizer |
| Different serving runtime | the engine and its build, and the placement | model, weights, workload |
| Different model | model, weights, quantization, tokenizer | workload, placement |

Three outcomes:

- **Directly comparable** — every dimension matches. Only these are ranked.
- **Comparable, with qualifications** — the runs differ only where the question
  meant them to, or something needed is not recorded. Look side by side; the
  qualification is stated.
- **Not comparable for this question** — they differ somewhere the question
  holds fixed.

**Ranking is stricter than looking**, and the leaderboard is unchanged: it
ranks runs of the same declared workload whatever question is selected above
it. Hardware, engine build and execution device have always been free to vary
there — that is what the leaderboard is for.

Some things never vary, under any question. A bridged run is never comparable
with a direct one, because a relay sits inside every request it made and its
cost was never subtracted. Serving on one machine while measuring on another
is likewise an arrangement rather than a setting.

Two distinctions worth knowing. *Unrecorded is not different*: two results that
both omit a tokenizer agree about it rather than disagreeing. *Quantization is
the exception* — both omitting it still blocks a ranking, because it changes
the numbers too much to set aside.

**Why this comparison?** shows, for every run placed beside the reference, the
reasons behind the verdict, the information nobody recorded, and the
differences that remain. There is no composite score and no universal
threshold: token rates across different models are not a measure of the same
work, and nothing here pretends otherwise.
