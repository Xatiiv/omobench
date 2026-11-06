"use strict";

// The one workflow view, shared by the guided page and the advanced
// "connect your own server" panel. Six stages, three gates, six completion
// states and the evidence summary, rendered once here and mounted by both
// pages -- the stages themselves are computed server-side in
// omobench/workflow.py, so neither the markup nor the rules exist twice.
//
// Every element is optional. A page that has no gate lists still gets its
// stepper; a page that has no evidence block still gets its completion
// states. That is what lets two different layouts share one implementation
// instead of growing a second one.
globalThis.WorkflowView = class WorkflowView {
  static GATES = ["run", "package", "submit"];

  // A check is a pass, a failure, or neither -- and "neither" is two different
  // things that need different words: nobody has supplied it yet, and nobody
  // has checked. Never a pass.
  static CHECK_CLASS = { ok: "ok", blocked: "fail", attention: "pending", unknown: "pending" };
  static STATE_WORD = {
    ok: "ready", blocked: "needs attention", attention: "worth checking",
    unknown: "not established",
  };
  static COMPLETION_CLASS = { done: "ok", failed: "fail", blocked: "fail", pending: "pending" };

  constructor(options = {}) {
    // A page that navigates between screens marks the open one itself, and
    // "the step you are looking at" is not the same claim as "the stage the
    // work has reached" -- so only a page without screens lets the stages
    // write aria-current.
    this.ariaCurrent = options.ariaCurrent !== false;
    this.document = options.document || globalThis.document;
  }

  get(id) {
    return this.document.getElementById(id);
  }

  static element(tag, className, text) {
    const node = globalThis.document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // What was checked, what was seen, where that came from, and one specific
  // thing to do about it.
  //
  // Severity is a second axis and is rendered as one: a check can be severe
  // without being a failure, which is exactly what an environment that has
  // already run out of memory is. It is marked, not re-coloured as a fault,
  // because the gate it blocks has not changed.
  static check(item) {
    const classes = [`status-item ${WorkflowView.CHECK_CLASS[item.state] || "pending"}`];
    if (item.severity === "severe") classes.push("severe");
    const li = WorkflowView.element("li", classes.join(" "));
    if (item.severity === "severe") {
      li.append(WorkflowView.element("span", "severity-tag", "Severe"));
    }
    li.append(WorkflowView.element("strong", null, item.label));
    li.append(WorkflowView.element("span", "sub-check", item.observed
      ? `Seen: ${item.observed} — from ${item.source}`
      : `${WorkflowView.STATE_WORD[item.state] || item.state} — ${item.source}`));
    if (item.detail) li.append(WorkflowView.element("span", null, item.detail));
    if (item.recovery) li.append(WorkflowView.element("span", "recovery", item.recovery));
    return li;
  }

  renderGates(report) {
    const local = this.get("preflight-local");
    if (local) local.textContent = report.local_report;
    for (const gate of WorkflowView.GATES) {
      const list = this.get(`gate-${gate}`);
      if (!list) continue;
      const items = report.checks.filter((item) => item.blocks === gate);
      list.replaceChildren(...items.map(WorkflowView.check));
      const note = this.get(`gate-note-${gate}`);
      if (note) note.textContent = report.gates[gate];
      const count = this.get(`gate-count-${gate}`);
      if (!count) continue;
      const waiting = report.blocking[gate].length;
      count.textContent = waiting ? `${waiting} outstanding` : items.length ? "nothing outstanding" : "";
      count.className = `gate-count ${waiting ? "waiting" : "clear"}`;
    }
    // Checks that gate nothing get their own place. Listing them under a gate
    // would suggest they stand in its way, which is the opposite of what they say.
    const ungated = report.checks.filter((item) => item.blocks === null);
    const block = this.get("gate-none-block"), list = this.get("gate-none");
    if (block) block.hidden = !ungated.length;
    if (list) list.replaceChildren(...ungated.map(WorkflowView.check));
  }

  // The stage class is set without touching anything else on the tab: a page
  // that also marks which screen is open must keep that mark, and a stepper
  // that fought its own page over one className would flicker between them.
  static STAGE_CLASSES = ["stage-done", "stage-current", "stage-todo", "stage-blocked"];

  renderStages(report) {
    report.stages.forEach((stage) => {
      const tab = this.document.querySelector(`.step-tab[data-stage="${stage.id}"]`);
      if (!tab) return;
      tab.classList.remove(...WorkflowView.STAGE_CLASSES);
      tab.classList.add(`stage-${stage.state}`);
      if (this.ariaCurrent) {
        tab.setAttribute("aria-current", stage.state === "current" ? "step" : "false");
      }
      const label = tab.querySelector(".step-state");
      if (label) label.textContent = stage.state === "done" ? "done" : stage.state === "current" ? "now" : "";
    });
  }

  renderCompletion(steps) {
    const block = this.get("completion"), list = this.get("completion-steps");
    if (!block || !list) return;
    block.hidden = !steps || !steps.length;
    if (!steps || !steps.length) return;
    list.replaceChildren(...steps.map((step) => {
      const li = WorkflowView.element("li", `status-item ${WorkflowView.COMPLETION_CLASS[step.state] || "pending"}`);
      li.append(WorkflowView.element("strong", null, step.label),
                WorkflowView.element("span", null, step.detail));
      return li;
    }));
  }

  // A fact nobody recorded reads as "Not recorded" rather than being left out:
  // a missing row is a gap a reader fills with an assumption.
  renderEvidence(summary) {
    const block = this.get("evidence-summary");
    if (!block) return;
    block.hidden = !summary;
    if (!summary) return;
    this.get("evidence-qualifications").textContent = summary.qualifications.join(" ");
    this.get("evidence-sections").replaceChildren(...summary.sections.map((section) => {
      const node = WorkflowView.element("section");
      const list = WorkflowView.element("dl", "kv");
      section.rows.forEach((row) => {
        const dt = WorkflowView.element("dt", null, row.label);
        const dd = WorkflowView.element("dd", null, row.value);
        if (!row.recorded) dd.classList.add("unrecorded");
        if (row.source) dd.append(WorkflowView.element("span", "sub-check", row.source));
        if (row.note) dd.append(WorkflowView.element("span", "sub-check", row.note));
        list.append(dt, dd);
      });
      node.append(WorkflowView.element("h4", null, section.title), list);
      return node;
    }));
  }

  render(report) {
    this.renderGates(report);
    this.renderStages(report);
    this.renderCompletion(report.completion);
    return report;
  }
};
