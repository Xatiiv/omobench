# Compare benchmark results

Open **Compare** in the website navigation. Choose a reference run, then select
up to three compatible runs. Setup cards show hardware, runtime, measured
request counts and errors. The charts compare output throughput and final
first-token / inter-token latency, with a median (p50) or tail (p95) selector.

Higher output throughput is better. Lower latency is better. Percentage
differences describe the metric value relative to the chosen reference, not a
composite score. Each chart starts at zero and has its own scale. A zero reference
does not produce a percentage or an infinite improvement claim.

## What are you comparing?

Choose the question first. "Comparable" is a property of two runs **and a
question**: asking which of two machines is faster requires the hardware to
differ, and refusing that pair because it differs would answer a question
nobody asked. The four questions, what each one means to vary, and the three
outcomes are set out in [guided-workflow.md](guided-workflow.md).

**Why this comparison?** explains every run placed beside the reference: the
reasons behind the verdict, the information nobody recorded, and the
differences that remain.

Ranking is stricter than looking, and the leaderboard is unchanged whatever
question is chosen above it.

## What counts as compatible

Direct comparisons and the run leaderboard require exact matches for:

- Catalog model ID.
- Declared weights source and immutable revision. Legacy records without a
  source use their canonical model identity; they do not silently match a
  sourced re-export.
- Recorded quantization. Missing quantization cannot establish a match.
- Profile ID and the SHA-256 of its contents.
- GuideLLM version.
- Transport. A run measured through the Windows–WSL bridge carries that
  bridge's latency inside every number it reports, and it was never
  subtracted, so it is comparable with other bridged runs and not with direct
  ones. This holds under every question.
- Serving arrangement: serving on one machine while measuring on another is an
  arrangement rather than a setting.
- Weight placement, where recorded. Splitting the weights across CPU and GPU is
  the arrangement that would otherwise hide inside "GPU".
- Tokenizer source, where recorded. Token counts are whichever tokenizer
  produced them. Two results that both omit it agree about it; one recording it
  and one not is a difference.
- Catalog status. A model proposed for review has real measurements and an
  identity that is still a request, so it is readable but not rankable.

Hardware, execution device, operating system, runtime version and runtime
arguments may differ. They remain visible in the setup cards. These are
comparisons of complete setups, not controlled claims about the effect of one
component. Matching metadata does not independently prove that a server executed
the declared configuration. Configuration fingerprints deliberately do not gate
comparison, because they include the hardware being compared.

The leaderboard ranks **runs** within the matching workload and selected device
filter. Repeat runs count separately. Ties share a rank; it is not a percentile
or a ranking of unique people or machines. Unmatched runs are listed with reasons.

## Compare a private result

In the guided panel, complete a run, choose **Prepare submission**, then
**Package & validate** and **Review submission files**. Use **Download result.json
for comparison**, or extract `result.json` from **Download complete bundle**.

On the website, choose **Open your result.json**, or expand **Or paste a packaged
result**. The importer expects a normalized OmoBench v1 record, not the raw
GuideLLM output or the panel's separate local-summary download. The limit is
1 MiB. A local file is held only in the current browser tab, never uploaded,
stored in browser storage, put in a share URL or added to community rankings.
Remove it with **Remove local result**, or reload to clear it.

The browser checks the fields it uses, basic request arithmetic and finite
metrics. It does **not** validate the complete evidence bundle, profile acceptance
thresholds, catalog eligibility or actual device execution. An imported run is
always labelled private and unreviewed. Publication still uses the existing
validator and submission review process.

Registry detail pages offer **Compare this setup**. Their link includes only
the run ID. A run absent from the current website build produces a visible
message, not a substitute measurement.

## Empty registry and examples

With no published results, the page reports zero runs and explains how to add
evidence. **Illustrative preview** is an explicit opt-in demonstration using
fictional setup names and simulated values. A persistent notice labels that
mode. Examples never mix with imported or registry results and never enter the
community leaderboard.

Community results appear after evidence review, merge and a website build.
Running a benchmark locally or opening a private file does not publish it.

## Verification

`tests/result-comparison.test.mjs` exercises the production parser and comparison
functions, including mismatched workloads, zero denominators, lower-is-better
sorting, ties and exclusion of private/example data. Rendered-page tests cover
the empty registry and entry points. The guided-panel VM harness verifies that
the export preserves the reviewed normalized result. CI runs the website tests
after the production build.
