# The guided workflow, the three gates, and comparison questions

Three ideas this document exists for: what the panel means by a stage, why
"can I run this?" and "can I publish this?" are different questions, and why
"are these two results comparable?" cannot be answered without knowing what
is being asked.

## Six stages, derived from state

    Connect → Identify → Check → Run → Review → Submit

A stage is done because the thing it stands for happened, not because a tab
was clicked. Connect is done once a server check has succeeded for the address
in the form. Identify is done once the model is settled — either the catalog
has it, or a complete proposal accompanies it. Check is done when nothing is
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

When nothing matches, the proposal opens. It tells you whether one is needed,
what it still lacks, what gets written beside the evidence
(`model-proposal.json`), and exactly what a reviewer would receive. A proposal
is a request for review: accepting it is a separate change to
`catalog/models/`, and merging the result would not make it. See
[provenance.md](provenance.md).

## Check: load the tokenizer before the benchmark does

GuideLLM resolves the tokenizer before it sends a single request, so one it
cannot load ends the run having measured nothing. The usual case is a GGUF
repository, which serves perfectly well and contains no Transformers
tokenizer at all.

**Load tokenizer now** loads it in the interpreter the benchmark will use.
**Check tokenizer** stays offline and only looks at the shape of the
identifier and whether its files are already here. Neither establishes that
the tokenizer matches the served weights, and neither pretends to.

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
| Bundle validated | the registry validator accepted it |
| Ready to submit | nothing is outstanding on the submit gate |
| Submitted for review | a pull request exists |
| Accepted and published | nothing this panel can observe |

The last is never shown as done. Acceptance follows review, a merge and a
website build, none of which the panel sees — so it reports the pull request
and stops there.

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
