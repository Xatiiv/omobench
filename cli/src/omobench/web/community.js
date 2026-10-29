"use strict";

// Shared by the guided and advanced panels. GitHub credentials never enter JS.
globalThis.CommunitySubmission = class CommunitySubmission {
  constructor(api, invalidate) {
    this.api = api;
    this.invalidate = invalidate;
    this.epoch = 0;
    this.generation = null;
    this.state = null;
    this.pending = false;
    this.timer = null;
    this.requestSerial = 0;
    this.el = (id) => document.getElementById(id);
    this.el("submission-mode").addEventListener("change", () => {
      this.changed(); this.render();
    });
    this.el("github-sign-in").addEventListener("click", () => this.act("start"));
    this.el("github-sign-out").addEventListener("click", () => this.act("sign-out"));
    this.el("github-cancel").addEventListener("click", () => this.act("cancel"));
  }
  get mode() { return this.el("submission-mode").value; }
  changed() { this.epoch += 1; this.invalidate(); }
  ticket() { return { epoch: this.epoch, mode: this.mode }; }
  current(ticket) { return ticket.epoch === this.epoch && ticket.mode === this.mode; }
  accept(state) {
    if (state.generation !== this.generation || state.status !== this.state?.status) this.changed();
    this.generation = state.generation; this.state = state;
    const submitter = this.el("submitter");
    if (state.account && submitter && !submitter.value) submitter.value = state.account.login;
    this.render();
  }
  render() {
    const s = this.state;
    this.el("github-auth").hidden = this.mode !== "community";
    const waiting = ["requesting", "pending"].includes(s?.status);
    this.el("github-sign-in").hidden = s?.status === "signed_in" || waiting;
    this.el("github-sign-in").disabled = this.pending || !s?.enabled;
    this.el("github-sign-out").hidden = s?.status !== "signed_in";
    this.el("github-sign-out").disabled = this.pending;
    this.el("github-cancel").hidden = !waiting;
    this.el("github-cancel").disabled = this.pending;
    this.el("github-device").hidden = s?.status !== "pending";
    this.el("github-code").textContent = s?.status === "pending" ? s.user_code : "";
    const link = this.el("github-verify");
    // Fixed destination, never turn a response into an arbitrary credential link.
    link.href = "https://github.com/login/device";
    const settings = this.el("github-revoke");
    const url = s?.authorization_url;
    settings.hidden = typeof url !== "string" || !/^https:\/\/github\.com\/settings\/connections\/applications\/[A-Za-z0-9_]{10,100}$/.test(url);
    if (!settings.hidden) settings.href = url;
    this.el("github-auth-status").textContent = !s ? "Checking sign-in availability…"
      : !s.enabled ? "GitHub sign-in is not configured in this build. Review and download your bundle below to submit manually."
      : s.status === "signed_in" ? `Signed in as ${s.account.login}. Review the files and destination before uploading.`
      : s.status === "pending" ? "Enter this code on GitHub. This panel will update when you finish."
      : s.status === "requesting" ? "Requesting a sign-in code…"
      : s.message || "Sign in to submit to the community registry. You can also download your bundle without signing in.";
  }
  async act(action) {
    if (this.pending) return;
    const serial = ++this.requestSerial;
    this.pending = true; this.render();
    try {
      const state = await this.api(`/api/github/auth/${action}`, {});
      if (serial === this.requestSerial) this.accept(state);
    }
    catch (error) { if (serial === this.requestSerial) this.el("github-auth-status").textContent = error.message; }
    finally {
      this.pending = false;
      this.el("github-sign-in").disabled = !this.state?.enabled;
      this.el("github-sign-out").disabled = false;
      this.el("github-cancel").disabled = false;
    }
  }
  async refresh() {
    clearTimeout(this.timer);
    const serial = this.pending ? null : ++this.requestSerial;
    try {
      if (serial !== null) {
        const state = await this.api("/api/github/auth");
        if (serial === this.requestSerial) this.accept(state);
      }
    }
    catch (error) {
      if (serial === this.requestSerial) {
        this.changed();
        this.el("github-auth-status").textContent = `Sign-in status unavailable: ${error.message}`;
      }
    }
    finally { this.timer = setTimeout(() => this.refresh(), 3000); }
  }
  destination(preview) {
    if (preview.mode === "community") {
      return preview.upload_repository
        ? `Account: ${preview.account.login} · ${preview.needs_fork ? "Create public fork" : "Upload to"}: ${preview.upload_repository} · PR to: ${preview.repository} (${preview.base_branch})`
        : `Community registry: ${preview.repository}. Sign in, then review again to confirm your upload destination.`;
    }
    return preview.repository ? `Destination: ${preview.repository} · base: ${preview.base_branch}` : "Download the validated bundle to submit manually.";
  }
  payload(preview, confirmed) {
    if (!preview?.can_submit) throw new Error("Review the files and destination before submitting.");
    return { mode: preview.mode || "origin", repository: preview.repository,
      bundle_digest: preview.bundle_digest, review_id: preview.review_id, confirmed };
  }
};
