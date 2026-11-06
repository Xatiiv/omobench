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
has always meant *obtainable by anybody who accepts the publisher's terms*, not
*downloadable without a click-through*. That reading is kept, because narrowing
it now would retroactively disqualify a model the project has always listed.
What it excludes is weights nobody else can get at all: a private fine-tune, an
internal checkpoint, a hosted API with no weights behind it.

## The requirements

Identical for a curated model and a custom one. A submission is **ready for
review** when it meets them; review, merge and a site build still follow, and
none of them is automatic.

| | Requirement | Where it is checked |
| --- | --- | --- |
| 1 | **Licence.** A published licence that permits use and publication of benchmark results, named, with a URL a reviewer can open. | `model.eligibility.licence`, `licence_url` |
| 2 | **Obtainable weights.** Anybody can download them, accepting the publisher's terms if there are any. | `model.eligibility.weights_publicly_downloadable` |
| 3 | **Text generation.** Not image, audio, video or embedding models. | `model.eligibility.modality` |
| 4 | **Identity.** An immutable revision, or a content hash of the actual weight files. Never `main`, `master` or `latest`. | `model.revision`, `model.weights` |
| 5 | **Workload.** A versioned profile, run unmodified, with every required measurement. | `benchmark.profile_id`, `profile_sha256` |
| 6 | **Acceptance.** The profile's own minimum successful requests and maximum error rate. | `results` against the profile |
| 7 | **Artifacts and privacy.** Every evidence file present, hashed correctly, and carrying no credential, private path or loopback address. | `artifacts`, `omobench scan-evidence` |
| 8 | **Serving provenance.** What served the model, on what hardware, over what transport, and how each fact was established. | `environment` (schema 1.1+) |
| 9 | **Execution.** Bounded, and not interrupted — see [bounded-execution.md](bounded-execution.md). | `execution` (schema 1.1+) |

A model that fails one of these is refused **with the reason named**, not with
"not in the catalog". A private fine-tune fails requirement 2 and nothing else;
a vision model fails requirement 3. Both are perfectly good things to benchmark
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
- **Other modalities.** Requirement 3 is unchanged. `google/gemma-3-4b-it` is a
  worked example: its card states "Gemma 3 models are multimodal, handling text
  and image input", and its pipeline tag is `image-text-to-text`, so it is out
  of scope for a text-generation profile however good it is.
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
