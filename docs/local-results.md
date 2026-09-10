# Show a packaged GPU result on the local website

The comparison website reads normalized `result.json` files under `results/`.
It does not read `.omobench-runs/`, UI job history, or local CPU summaries.
Opening `omobench ui` and running a benchmark does not automatically add a
website registry row.

## Preview a validated bundle

First complete a real GPU run and package/validate it using the
[browser panel](browser-control.md#gpu-packaging-and-pull-requests) or
[CLI workflow](submission-bundle.md#create-the-local-normalized-summary).

- For a panel ZIP, extract **at the checkout root**; it already includes the
  `results/<model-slug>/<date>-<run-id>/` directory layout.
- For a CLI bundle, copy the five standard files to a new directory under
  `results/`, following the [manual workflow](submission-bundle.md#manual-pull-request-workflow).
- Do not replace an existing bundle, copy UI job metadata, or rename a CPU
  summary to `result.json`.

The destination must contain:

```text
result.json
guidellm-benchmarks.json.gz
guidellm.stdout.log
environment.json
reproduce.md
```

From the activated Python environment in the checkout:

```bash
python -m pip install -e './cli[ui]'
python tools/validate_results.py
```

Then, in a terminal with Node.js/npm installed, install website dependencies if
needed and start (or restart) the development server:

```bash
npm ci
npm run dev
```

Open the printed URL. The registry section shows the packaged metrics;
**View details** shows model/runtime/hardware metadata, profile, configuration
fingerprint, and evidence hashes. Illustrative comparison rows remain separate.

## From local preview to the hosted registry

Copying files locally does not upload them. Submit through a
[GitHub pull request](submission-bundle.md#manual-pull-request-workflow) when ready.
The hosted website includes the result after review, merge, and a build from
a commit that contains the bundle. PR branches can expose their evidence and
preview deployments before acceptance, depending on repository/hosting settings.

If you submitted directly through the panel, the upload did not alter your local
checkout. After that PR is merged, stop active runs, update the checkout, and
restart the website to include it locally. See [updating](quickstart.md#update-an-existing-checkout).

Do not submit the same bundle twice via both routes. If it was already copied
to the local registry for preview, the panel's duplicate check may reject a
second submission of the staging copy; continue with the manual PR for the
already-previewed files.
