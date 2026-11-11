"use strict";

// Managed preparation, mounted inside the one panel.
//
// This used to be a page of its own, with its own model chooser, its own
// stepper and its own dashboard. Two pages meant two places to look for the
// same stage, and the split cut across the real question rather than along
// it: what somebody is doing is measuring their hardware, whether the model
// server is one OmoBench prepared or one they already run.
//
// So what survives is the part that was actually unique -- verifying,
// downloading, cancelling, resuming and recovering a pinned model and runtime
// -- and the parts that were duplicates are gone. It owns no selection: the
// workload comes from the panel's one selector. It owns no progress display:
// the panel's shared dashboard renders that, as it already does for a run
// against somebody's own server. It owns no stepper.
//
// What it does own is the download, which is the one thing here that is slow,
// resumable, cancellable and capable of leaving a half-written file behind.
globalThis.ManagedSetup = class ManagedSetup {
  static SETUP_ACTIVE = new Set(["preparing", "cancelling"]);
  static RUN_ACTIVE = new Set(["starting_server", "waiting_ready", "running_benchmark",
                               "cancelling"]);

  // `api` and `document` are injected so the component can be driven in a
  // harness without a browser or a server, which is how its download,
  // cancellation and recovery paths are tested without fetching gigabytes.
  constructor({ api, document: doc, formatBytes, onStage, onFinished } = {}) {
    this.api = api;
    this.document = doc || globalThis.document;
    this.formatBytes = formatBytes || ((value) => `${value} bytes`);
    this.onStage = onStage || (() => {});
    this.onFinished = onFinished || (() => {});
    this.selection = null;
    this.storageDir = "";
    this.review = null;
    this.activeKind = null;
    this.activeJobId = null;
    this.benchmarkJobId = null;
    this.timers = [];
    this.stopped = false;
  }

  get(id) {
    return this.document.getElementById(id);
  }

  get busy() {
    return this.activeJobId !== null;
  }

  clearTimers() {
    this.timers.forEach((timer) => clearTimeout(timer));
    this.timers = [];
  }

  // Stop watching, without cancelling anything on the server. The panel calls
  // this when it closes or moves on; an in-flight poll that resolves after it
  // must not quietly schedule another.
  stop() {
    this.stopped = true;
    this.clearTimers();
  }

  // -- selection ------------------------------------------------------------

  // The workload comes from the panel. Nothing here chooses one, and there is
  // no second list for the two to disagree about.
  async select(selection) {
    this.selection = selection;
    const block = this.get("managed-setup");
    if (block) block.hidden = !selection || !selection.managed_recipe;
    if (!selection || !selection.managed_recipe) {
      this.review = null;
      return null;
    }
    const field = this.get("managed-storage-dir");
    if (field && !field.value) field.value = selection.suggested_storage_dir || "";
    return this.refresh();
  }

  // -- review ---------------------------------------------------------------

  // Read-only, and says so: exact files, sizes, licences, hashes and free
  // space, with nothing downloaded and nothing written.
  async refresh() {
    if (!this.selection || !this.selection.managed_recipe) return null;
    const field = this.get("managed-storage-dir");
    this.storageDir = field ? field.value.trim() : this.storageDir;
    const error = this.get("managed-error");
    if (error) error.hidden = true;
    if (!this.storageDir) {
      this.fail("Enter a storage folder.");
      return null;
    }
    try {
      this.review = await this.api("/api/managed/review", {
        recipe: this.selection.managed_recipe,
        storage_dir: this.storageDir,
      });
    } catch (problem) {
      this.review = null;
      this.fail(problem.message);
      return null;
    }
    this.render();
    return this.review;
  }

  fail(message) {
    const error = this.get("managed-error");
    if (error) {
      error.hidden = false;
      error.textContent = message;
    }
    const start = this.get("managed-start");
    if (start) start.disabled = true;
  }

  render() {
    const report = this.review;
    const files = this.get("managed-files");
    if (files) {
      files.replaceChildren(...(report.files || []).map((file) => {
        const row = this.document.createElement("div");
        row.className = "file-card";
        row.textContent = `${String(file.kind || "").toUpperCase()} · ${file.filename} · `
          + `${this.formatBytes(file.size_bytes)} · licence ${file.licence}`;
        const where = this.document.createElement("div");
        where.className = "path";
        where.textContent = `${file.repository} @ ${file.revision}  sha256 ${file.sha256}`;
        row.append(where);
        return row;
      }));
    }

    const platform = this.get("managed-platform");
    if (platform) {
      const installed = report.already_installed || {};
      platform.textContent = (report.platform && report.platform.message) || "";
      if (installed.installed && !installed.valid) {
        platform.textContent += ` Previously installed files failed re-verification: ${installed.error}`;
      }
    }

    // Three states, not two: known and trustworthy, known but unverifiable
    // (WSL's own virtual disk, whose reported capacity can exceed what the
    // Windows drive behind it will actually give), and unknown. Only the
    // first ever says "enough space".
    const space = this.get("managed-space");
    if (space) {
      if (report.free_space_verified === false) {
        space.textContent = `Virtual filesystem free space: ${this.formatBytes(report.free_space_bytes)} `
          + `(${this.formatBytes(report.required_bytes)} required). `
          + (report.space_note || "This has not been verified against the actual backing drive.");
      } else if (report.free_space_bytes === null || report.free_space_bytes === undefined) {
        space.textContent = "Could not determine free space for this folder.";
      } else if (report.has_enough_space === false) {
        space.textContent = `Not enough free space: ${this.formatBytes(report.free_space_bytes)} free, `
          + `${this.formatBytes(report.required_bytes)} required.`;
      } else {
        space.textContent = `Free space: ${this.formatBytes(report.free_space_bytes)} `
          + `(${this.formatBytes(report.required_bytes)} required), enough space.`;
      }
    }

    const installed = report.already_installed || {};
    const ready = installed.installed && installed.valid;
    const start = this.get("managed-start");
    if (start) {
      start.disabled = !(report.platform && report.platform.ok) || report.has_enough_space === false;
      start.dataset.alreadyInstalled = ready ? "1" : "0";
      start.textContent = ready
        ? "Start benchmark"
        : `Download and verify (${this.formatBytes(report.bytes_to_download ?? report.required_bytes)})`;
    }
  }

  // -- preparation and run --------------------------------------------------

  // Verified files are never fetched again: a recipe already installed and
  // still hashing correctly goes straight to the run.
  async start() {
    if (!this.selection || !this.review) return null;
    const start = this.get("managed-start");
    const alreadyInstalled = start && start.dataset.alreadyInstalled === "1";
    const body = { recipe: this.selection.managed_recipe, storage_dir: this.storageDir };
    if (alreadyInstalled) {
      this.activeKind = "run";
      this.activeJobId = (await this.api("/api/managed/runs", { ...body, mode: "execute" })).id;
    } else {
      this.activeKind = "setup";
      this.activeJobId = (await this.api("/api/managed/setup-jobs",
                                         { ...body, confirmed: true })).id;
    }
    this.poll();
    return this.activeJobId;
  }

  // Cancelling a download is the same request as cancelling a run, because
  // from here they are the same thing: work this panel started and must be
  // able to stop.
  async cancel() {
    if (!this.activeJobId) return;
    const path = this.activeKind === "setup"
      ? `/api/managed/setup-jobs/${this.activeJobId}/cancel`
      : `/api/managed/runs/${this.activeJobId}/cancel`;
    await this.api(path, {});
  }

  async poll() {
    this.clearTimers();
    if (!this.activeJobId || this.stopped) return;
    let job = null;
    try {
      job = this.activeKind === "setup"
        ? await this.api(`/api/managed/setup-jobs/${this.activeJobId}`)
        : await this.api(`/api/managed/runs/${this.activeJobId}`);
    } catch (problem) {
      // A job the panel no longer knows about is a restart, not a crash.
      this.finish(false, problem.message);
      return;
    }

    const active = this.activeKind === "setup"
      ? ManagedSetup.SETUP_ACTIVE.has(job.status)
      : ManagedSetup.RUN_ACTIVE.has(job.status);
    this.onStage({ kind: this.activeKind, job, active });

    if (active) {
      if (!this.stopped) this.timers.push(setTimeout(() => this.poll(), 1000));
      return;
    }
    if (this.activeKind === "setup") {
      if (job.status === "completed") {
        // Downloaded and verified. The run is a separate job so a failure
        // here never looks like a benchmark that went wrong.
        this.activeKind = "run";
        this.activeJobId = (await this.api("/api/managed/runs", {
          recipe: this.selection.managed_recipe,
          storage_dir: this.storageDir,
          mode: "execute",
        })).id;
        this.poll();
        return;
      }
      this.finish(false, job.error || "Preparation did not complete.");
      return;
    }
    this.benchmarkJobId = job.benchmark_job_id || null;
    this.finish(job.status === "completed", job.error, job);
  }

  finish(success, message, job) {
    this.clearTimers();
    const kind = this.activeKind;
    this.activeKind = null;
    this.activeJobId = null;
    this.onFinished({ success, message, job, kind, benchmarkJobId: this.benchmarkJobId });
  }

  // -- recovery -------------------------------------------------------------

  // The panel can be closed, or crash, while a download is half done. On
  // reopening, any job still running is adopted rather than abandoned, and a
  // partly written file is resumed rather than restarted.
  async recover() {
    let setups = [];
    let runs = [];
    try {
      setups = await this.api("/api/managed/setup-jobs");
      runs = await this.api("/api/managed/runs");
    } catch {
      return null;
    }
    const run = (runs || []).find((job) => ManagedSetup.RUN_ACTIVE.has(job.status));
    const setup = run ? null : (setups || []).find(
      (job) => ManagedSetup.SETUP_ACTIVE.has(job.status));
    if (!run && !setup) return null;
    this.activeKind = run ? "run" : "setup";
    this.activeJobId = (run || setup).id;
    this.poll();
    return { kind: this.activeKind, id: this.activeJobId };
  }
};
