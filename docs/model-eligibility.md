# Which models may be submitted

Until now the answer was a list: `catalog/models/*.yaml`, and nothing else
could be published. That made the curated list do two jobs at once — "here are
good models to try" and "here is the set of models allowed to exist in the
registry" — and the second job was never what made a result trustworthy. What
makes a result trustworthy is its evidence.

So the list keeps the first job and loses the second. A model that is not on it
may be submitted, **if it meets the same requirements a curated model meets**,
stated here rather than implied by membership.

## The policy, as it already was

It was written down once, in `catalog/models/seed-v1.yaml`:

> Publicly downloadable open weights with a published licence that permits use
> and publication of benchmark results. OmoBench does not redistribute weights.

together with AGENTS.md's "open-weight, text-generation models". Four
conditions and one promise, and none of them is about being on a list.

**One point of interpretation, found by reading the catalog rather than the
policy.** `meta-llama/Llama-3.1-8B-Instruct` has been in the catalog since the
beginning, and its Hugging Face repository is gated: you must agree to share
your contact information before the files appear. So "publicly downloadable"
cannot have meant *downloadable without a click-through*, or that entry would
never have been listed. It means *obtainable by anybody willing to accept the
publisher's terms*. What it excludes is weights nobody else can get at all: a
private fine-tune, an internal checkpoint, a hosted API with no weights behind
it.

**That is a reading of the policy, not a blanket pass for gating.** One gated
repository being listed does not make gating irrelevant to the next one. A
gate whose terms are open to anybody is consistent with requirement 2; a gate
restricted to approved organisations, to one jurisdiction, or to
non-commercial use by a particular class of person is not, and neither is one
whose terms forbid publishing benchmark results — which is requirement 1. Each
model is judged on its own terms, and "another gated model is listed" is not
an argument about any of them.

## The requirements

Identical for a curated model and a custom one. A submission is **ready for
review** when it meets them; review, merge and a site build still follow, and
none of them is automatic.

| | Requirement | Where it is checked |
| --- | --- | --- |
| 1 | **Licence.** A published licence that permits use and publication of benchmark results, named, with a URL a reviewer can open. | `model.eligibility.licence`, `licence_url` |
| 2 | **Obtainable weights.** Anybody can download them, accepting the publisher's terms if there are any. | `model.eligibility.weights_publicly_downloadable` |
| 3 | **Text generation workload.** Text models and vision-language models running text-only chat qualify. | `model.eligibility.modality` |
| 4 | **Identity.** An immutable revision, or a content hash of the actual weight files. Never `main`, `master` or `latest`. | `model.revision`, `model.weights` |
| 5 | **Workload.** A versioned profile, run unmodified, with every required measurement. | `benchmark.profile_id`, `profile_sha256` |
| 6 | **Acceptance.** The profile's own minimum successful requests and maximum error rate. | `results` against the profile |
| 7 | **Artifacts and privacy.** Every evidence file present, hashed correctly, and carrying no credential, private path or loopback address. | `artifacts`, `omobench scan-evidence` |
| 8 | **Serving provenance.** What served the model, on what hardware, over what transport, and how each fact was established. | `environment` (schema 1.1+) |
| 9 | **Execution.** Bounded, and not interrupted — see [bounded-execution.md](bounded-execution.md). | `execution` (schema 1.1+) |

A model that fails one of these is refused **with the reason named**, not with
"not in the catalog". A private fine-tune fails requirement 2 and nothing else;
a run with image inputs fails requirement 3. Both are perfectly good things to benchmark
locally, and the panel will do it — they are simply not publishable here.

## What the curated list is now

A recommendation. `catalog/models/*.yaml` says "these are known to work, their
licences are understood, and somebody has checked them". The panel offers them
first because choosing a model is harder than it looks, not because the others
are forbidden.

Being on the list still means something at review time: a reviewer does not
have to re-establish the licence and the modality, because the list already
records them. That is why a proposal remains **available** — a contributor who
wants their model added can still send one — but it is no longer **required**
in order to submit a result.

## What this does not open up

- **Hosted proprietary APIs.** Requirement 2 excludes them: there are no
  weights to obtain. A benchmark of somebody's paid endpoint is not a
  measurement of a model you could run.
- **Vision-language models.** In Review & share, enter the exact model identity,
  licence and source and select **Vision-language model · text-only workload**.
  The registry checks the saved GuideLLM report for synthetic text chat and
  rejects missing input evidence or reported image, audio or video use.
  Existing text-only runs can be repackaged. The public run page labels the workload.
- **Weaker measurement.** Requirements 5 to 9 are exactly what they were. This
  change is about *which models*, not about what counts as evidence.

## Recording it

Schema **1.2** adds `model.eligibility`, carrying the facts requirements 1–3
need, and allows `model.catalog_status: "uncatalogued"`. Schema 1.0 stays
frozen and 1.1 keeps the meaning it was written with: neither may carry an
eligibility block, because neither ever collected one.

```json
"model": {
  "id": "org/Some-Model-7B",
  "revision": "9f2c1b…",
  "catalog_status": "uncatalogued",
  "eligibility": {
    "licence": "apache-2.0",
    "licence_url": "https://huggingface.co/org/Some-Model-7B/blob/main/LICENSE",
    "weights_publicly_downloadable": true,
    "modality": "text-generation",
    "publisher": "org",
    "source": "https://huggingface.co/org/Some-Model-7B"
  }
}
```

`catalog_status` now has three values and they say three different things:

- `catalogued` — on the curated list; the list's own record of its licence and
  modality applies.
- `proposed` — not on the list, and the bundle carries a proposal asking for it
  to be added. Still supported, no longer required.
- `uncatalogued` — not on the list and not asking to be. Eligibility rests on
  the facts in `model.eligibility`, which a reviewer checks against the
  requirements above.

## In the panel

Choose **Custom model** in the workload selector. The fields below it are the
eligibility block: the model's canonical ID, its publisher, an immutable
weights revision, the licence and a URL where it can be read, the weights
source, and a checkbox confirming anybody can download them. A tokenizer
source and a base model are optional and recorded only when given.

The panel reports, beside those fields and in the submission checklist,
whether what has been typed satisfies the requirements. It names the ones still
missing. It does **not** verify any of them: it does not open the licence URL,
does not fetch the weights, and does not confirm the publisher exists. Every
value is recorded and displayed as the contributor's word, here and on the
website. The registry's validator decides, and a reviewer reads the licence.

Three routes satisfy the same submission gate, and the panel treats them as
equals:

| Route | When |
| --- | --- |
| The curated catalog | The model is on the list, which already records these facts |
| An eligibility declaration | The model is not on the list and is not asking to be |
| A proposal | The model is not on the list and you are asking for it to be added |

A declaration never becomes a proposal and a proposal is never replaced by
one. If both are present the proposal wins, because it is an explicit request
for review that somebody else has to answer.

A model whose weights are **not** publicly downloadable can still be
benchmarked and kept locally. The panel records that fact rather than refusing
the run, and the submission gate — not the runner — is what stops it going
public.
