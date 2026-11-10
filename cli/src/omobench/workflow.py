"""Six stages, and one list of checks that says what each one blocks.

The panel used to answer three different questions with three different
vocabularies. "Check this server" reported stages. "Check publication
requirements" reported a checklist that only appeared after a run. Packaging
reported validator errors. A contributor had to work out for themselves which
of those stood between them and a measurement, which between them and a saved
bundle, and which between them and the public registry.

Those are three different gates and this module names them as three:

    blocks="run"      the benchmark cannot start, or would die having
                      measured nothing
    blocks="package"  the benchmark can run, but its evidence cannot become a
                      schema-valid bundle
    blocks="submit"   the bundle is valid and the public registry will not
                      take it

Nothing here relaxes anything. Packaging and the registry validator remain the
authority; this reports what they will say, early enough to act on. In
particular a model awaiting catalog review blocks only submission: the
benchmark runs, the evidence is real and the bundle is valid, because nothing
about a pending review changes what the runner measured.

Every check carries what was looked at, what was seen, where that came from,
what it blocks, and what to do about it. A check nobody has run yet says so
rather than passing.
"""
from __future__ import annotations

from typing import Any

STAGES = ("connect", "identify", "check", "run", "review", "submit")
STAGE_LABELS = {
    "connect": "Connect",
    "identify": "Identify",
    "check": "Check",
    "run": "Run",
    "review": "Review",
    "submit": "Submit",
}
GATES = ("run", "package", "submit")
MUTABLE_REVISIONS = {"main", "master", "latest", "head"}

# Where an observed value came from. Deliberately the contributor's vocabulary
# rather than the schema's: "you" and "this machine" are what a person can act
# on, and the result's own provenance field stays the schema's business.
SOURCE_YOU = "you"
SOURCE_SERVER = "your server"
SOURCE_MACHINE = "this machine"
SOURCE_CATALOG = "the OmoBench catalog"
SOURCE_RUN = "this run's evidence"
SOURCE_UNCHECKED = "not checked yet"


def check(
    identifier: str,
    label: str,
    *,
    state: str,
    observed: Any = None,
    source: str = SOURCE_UNCHECKED,
    blocks: str | None = None,
    recovery: str = "",
    detail: str = "",
    field: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """One check, in the shape every surface renders.

    `state` separates three ways of not being ready, because they call for
    different things. "blocked" is a value that is present and wrong -- a
    revision that moves, a server that refused. "attention" is a value that is
    simply not there yet, which before a run is an ordinary statement of what
    will be needed rather than a fault. "unknown" is a check nobody has run,
    which is never reported as a pass. All three stand in the way of their
    gate; only "blocked" is a failure.

    `severity` is a second, independent axis, and exists because "attention"
    covers both "you have not entered this yet" and "this environment has
    already run out of memory". Those deserve the same gate and very different
    prominence. It never changes what a check blocks.
    """
    if state not in {"ok", "blocked", "attention", "unknown"}:
        raise ValueError(f"Unknown check state: {state!r}")
    if blocks is not None and blocks not in GATES:
        raise ValueError(f"A check can only block {', '.join(GATES)}: {blocks!r}")
    if severity is not None and severity not in {"severe"}:
        raise ValueError(f"Unknown check severity: {severity!r}")
    return {
        "id": identifier,
        "label": label,
        "state": state,
        "observed": observed,
        "source": source if observed is not None else SOURCE_UNCHECKED,
        "blocks": blocks,
        "recovery": recovery,
        "detail": detail,
        "field": field,
        "severity": severity,
    }


def as_requirement(item: dict[str, Any]) -> dict[str, Any]:
    """One check in the panel checklist's older shape.

    The checklist predates this module and several surfaces read it. Rather
    than compute the same conclusions twice, it became a view: `ok` is true
    when the check passed, false when something is standing in the way, and
    null when nobody has established it -- which is never reported as a pass.
    """
    return {
        "field": item["field"],
        "label": item["label"],
        "ok": True if item["state"] == "ok" else None if item["state"] == "unknown" else False,
        "detail": " ".join(part for part in (item["detail"], item["recovery"]) if part),
        "blocks": item["blocks"],
    }


def blocking(checks: list[dict[str, Any]], gate: str) -> list[dict[str, Any]]:
    """Everything still standing between the contributor and one named gate.

    Not established counts. A tokenizer nobody has loaded and a submitter
    nobody has entered are as much in the way as a check that failed, and
    treating "we have not looked" as "fine" is how a run gets started that
    dies resolving its tokenizer.
    """
    return [item for item in checks if item["state"] != "ok" and item["blocks"] == gate]


def failing(checks: list[dict[str, Any]], gate: str) -> list[dict[str, Any]]:
    """The subset that is actually wrong, rather than merely unestablished.

    The difference matters for what a stage is called: something to go and do
    is not the same as something that went wrong.
    """
    return [item for item in checks if item["state"] == "blocked" and item["blocks"] == gate]


def memory_check(memory: dict[str, Any], *, bridged: bool = False) -> dict[str, Any]:
    """What the benchmark runner's own environment has left, said as such.

    Three things this deliberately does not do.

    It does not call the reading "this machine". Inside WSL the total is what
    Windows handed the distribution, and on a bridged run the model is served
    on a different computer altogether -- so an unlabelled number would be
    read as a fact about the serving host that nothing here established.

    It does not treat swap in use as proof of paging. Swap filling up says the
    kernel has had to move pages out at some point; Linux also swaps out idle
    pages under no particular pressure, and a full swap file on an otherwise
    comfortable host proves nothing about what a benchmark will do. What is
    worth saying is the pair: very little available *and* swap already spent.

    And it does not act. It changes no WSL setting and stops no process; both
    are the contributor's to decide, and both can cost them work they did not
    ask to lose. It never stops a run either -- a contributor may know exactly
    why their environment looks like this.
    """
    available, total = memory["available_gb"], memory["total_gb"]
    swap_used, swap_total = memory["swap_used_gb"], memory["swap_total_gb"]
    label = memory.get("environment_label", "the machine running this panel")
    total_means = memory.get("total_means", "on the machine running this panel")
    is_wsl = memory.get("environment") == "wsl"

    # Not a threshold anybody can defend in general: how much a benchmark needs
    # depends on the weights, the context length and the runtime. What is
    # defensible is reporting that this environment has already run out.
    strained = available < 1.0 or swap_used > 0.5
    # Severe is the pair, not either half: almost nothing left *and* swap
    # already spent is the state the previous CPU run started from, and it is
    # the one worth putting in front of somebody about to spend an hour.
    severe = available < 1.0 and swap_used > 0.5

    observed = f"{available} GB available of {total} GB {total_means}"
    if swap_total:
        observed += f"; {swap_used} GB of its {swap_total} GB swap in use"

    detail = f"Read from the kernel inside {label}."
    if bridged:
        detail += (" This is the benchmark runner. The model is served on another machine, "
                   "and nothing here describes that machine's memory.")
    elif is_wsl:
        detail += (" On this setup that environment runs the model server as well as the "
                   "runner. It is the memory Windows has given the distribution, not the "
                   "memory installed in the computer.")

    if not strained:
        recovery = ""
    else:
        choices = [
            "close what else is resident here -- other panels, editors, language servers, "
            "builds -- and read this again",
            "serve a smaller or more heavily quantized model, if one is already installed",
        ]
        if is_wsl:
            choices.append(
                "give the distribution more memory yourself: a `memory=` line in "
                "%USERPROFILE%\\.wslconfig, then `wsl --shutdown`. OmoBench does not write "
                "that file, and how much this computer can spare is yours to judge")
        if not bridged:
            choices.append(
                "serve the model on another machine and measure it across the bridge, so the "
                "server and the runner stop sharing one allocation")
        recovery = (
            ("Severe: this environment is nearly out of memory and has already spent swap. "
             if severe else "")
            + "Swap in use means the kernel has had to move pages out at some point; by itself "
              "it does not prove this run would page. What it does mean is that a long profile "
              "would start with no headroom, and a run that pages reports the paging rather "
              "than the model. Nothing here stops the run. Your choices: "
            + "; ".join(choices) + ".")

    return check(
        "memory",
        f"Memory to spare in {label}",
        state="attention" if strained else "ok",
        observed=observed,
        source=SOURCE_MACHINE,
        blocks="run",
        detail=detail,
        recovery=recovery,
        severity="severe" if severe else None,
    )


def host_blind_spot(memory: dict[str, Any]) -> dict[str, Any] | None:
    """What a reading taken inside WSL cannot see, said where it matters.

    This exists because of a run that failed for a reason the check above was
    structurally unable to report. Its first eight requests took 58 to 78
    seconds each -- comfortably inside the profile -- and three later ones
    took 1,505, 1,279 and 5,793 seconds, while `/proc/meminfo` reported over
    2 GB available and almost no swap in use the whole time. The distribution
    was fine. The computer underneath it was down to 680 MB of 15.46 GB and
    kept entering Modern Standby, which suspends the distribution along with
    it. A benchmark cannot tell a suspended virtual machine from slow
    inference: it records the wait as the model's latency.

    So this gates nothing and asserts nothing about the host. It says what
    the number above is not, which is the one thing the panel can honestly
    offer about a machine it cannot read.
    """
    if memory.get("environment") != "wsl":
        return None
    return check(
        "wsl-host",
        "What this reading cannot see",
        state="attention",
        observed="a WSL distribution; its Windows host is not readable from in here",
        source=SOURCE_MACHINE,
        blocks=None,
        detail="How much memory the computer has left, and whether it is about to sleep, "
               "are not visible from inside the distribution. The numbers above are the "
               "distribution's own.",
        recovery="A Windows host that runs short of memory, or that enters Modern Standby, "
                 "suspends this distribution with it -- and a benchmark cannot tell that "
                 "apart from slow inference, because it records the wait as the model's "
                 "latency. Before a long profile, leave the computer awake and on power, "
                 "and close what you do not need on the Windows side.",
    )


def sleep_prevention_check(availability: dict[str, Any]) -> dict[str, Any]:
    """Whether this run can ask Windows to stay awake, said before it starts.

    Worth its own line because the failure it guards against does not look
    like a failure. A machine that sleeps mid-benchmark produces a complete
    result with plausible numbers in it; the suspension arrives as the
    model's latency, and nothing on the page says otherwise unless somebody
    put it there.

    Marked severe when it cannot be asked for, because the contributor is
    about to spend an hour on a measurement that may quietly describe their
    power settings. It still never blocks a run: somebody who has already
    disabled sleep themselves needs no help from this.
    """
    available = bool(availability.get("available"))
    return check(
        "sleep-prevention",
        "Windows can be asked to stay awake",
        state="ok" if available else "attention",
        observed="available" if available else "not available from here",
        source=SOURCE_MACHINE,
        blocks="run",
        severity=None if available else "severe",
        detail="A WSL distribution is suspended along with the computer it runs in. Asking "
               "Windows for a power request is done through the companion, which is the only "
               "part of OmoBench that is a Windows program.",
        recovery="" if available else
                 "Open this panel through the Windows companion, and update it if it is "
                 "older than this checkout. Until then, turn off sleep yourself for the "
                 "length of the run, leave the computer on power, and do not close the lid. "
                 "A run this panel cannot keep awake still records whether the machine was "
                 f"suspended, and such a result is kept for diagnosis and not published. "
                 f"({availability.get('detail') or 'the companion was not reachable'})",
    )


def run_checks(
    *,
    setup_ok: bool,
    setup_message: str,
    endpoint: str | None,
    server_check: dict[str, Any] | None,
    tokenizer_report: dict[str, Any] | None,
    tokenizer_source: str | None,
    memory: dict[str, Any] | None = None,
    bridged: bool = False,
    sleep_prevention: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """What stands between this configuration and a benchmark that measures something.

    These are the only checks that can stop a run. Everything else -- catalog
    eligibility, an immutable revision, a submitter -- is about what happens to
    the measurements afterwards, and a run blocked on any of those would be a
    run refused for a reason the runner does not have.
    """
    checks = [check(
        "runner",
        "The benchmark runner can start here",
        state="ok" if setup_ok else "blocked",
        observed="ready" if setup_ok else "unavailable",
        source=SOURCE_MACHINE,
        blocks="run",
        recovery="" if setup_ok else setup_message,
        detail=setup_message,
    )]

    if memory is not None:
        checks.append(memory_check(memory, bridged=bridged))
        blind_spot = host_blind_spot(memory)
        if blind_spot is not None:
            checks.append(blind_spot)
        if sleep_prevention is not None and memory.get("environment") == "wsl":
            checks.append(sleep_prevention_check(sleep_prevention))

    if not endpoint:
        checks.append(check(
            "endpoint", "Your server answered", state="unknown", blocks="run",
            recovery="Enter the loopback address and port your server listens on, then "
                     "choose Check this server."))
    elif server_check is None:
        checks.append(check(
            "endpoint", "Your server answered", state="unknown", observed=endpoint,
            source=SOURCE_YOU, blocks="run",
            recovery="Choose Check this server. Until it runs, nothing here has contacted "
                     "that address."))
    else:
        failed = [stage for stage in server_check.get("stages", [])
                  if stage.get("state") == "failed"]
        passed = not failed
        checks.append(check(
            "endpoint",
            "Your server answered",
            state="ok" if passed else "blocked",
            observed=endpoint,
            source=SOURCE_SERVER,
            blocks="run",
            recovery="" if passed else failed[0].get("detail", ""),
            detail=(server_check.get("summary") or "")
                   if passed else "; ".join(stage.get("title", "") for stage in failed),
        ))
        served = server_check.get("served_models") or []
        if served:
            checks.append(check(
                "served-models", "The server listed the models it serves",
                state="ok", observed=", ".join(served[:6]), source=SOURCE_SERVER,
                detail="These are the identifiers its API answers to, which are not "
                       "necessarily catalog model identities."))

    if tokenizer_report is None:
        checks.append(check(
            "tokenizer", "The runner can load the tokenizer",
            state="unknown",
            observed=tokenizer_source or None,
            source=SOURCE_YOU if tokenizer_source else SOURCE_UNCHECKED,
            blocks="run",
            recovery="Choose Load tokenizer now. GuideLLM resolves the tokenizer before it "
                     "sends a single request, so one it cannot load ends the run having "
                     "measured nothing -- and a GGUF repository, which serves perfectly "
                     "well, contains no Transformers tokenizer at all."))
    else:
        loaded = bool(tokenizer_report.get("ok"))
        identity = tokenizer_report.get("identity") or {}
        checks.append(check(
            "tokenizer",
            "The runner can load the tokenizer",
            state="ok" if loaded else "blocked",
            observed=(f"{tokenizer_report.get('source')}"
                      + (f" · {identity['class']}" if identity.get("class") else "")),
            source=SOURCE_MACHINE,
            blocks="run",
            recovery="" if loaded else (
                (tokenizer_report.get("suggestion") or {}).get("source")
                and f"The publisher names {tokenizer_report['suggestion']['source']} as this "
                    "repository's base model. Check the model card, then enter it deliberately."
                or "Enter a repository that publishes a Transformers tokenizer, and load it again."),
            detail=tokenizer_report.get("message", ""),
        ))
        checks.append(check(
            "tokenizer-match", "The tokenizer matches the served weights",
            state="unknown", observed=None, blocks=None,
            detail="Not verifiable from here, and nothing available to OmoBench can establish "
                   "it. A tokenizer that loads is not thereby the one the served weights were "
                   "trained with; the wrong one changes every token count and so every "
                   "measurement.",
            recovery="Pick the repository the model card names, not merely one that loads."))
    return checks


def package_checks(
    *,
    job: dict[str, Any],
    model: str | None,
    revision: str | None,
    framework: str | None,
    framework_version: str | None,
    serving: dict[str, Any] | None,
    eligibility: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """What stands between a finished run and a schema-valid bundle."""
    summary = job.get("summary")
    checks = [check(
        "measurements",
        "Benchmark completed with measurements",
        state="ok" if summary else "attention",
        observed=(f"{summary['requests']['succeeded']} successful requests"
                  if summary and summary.get("requests") else None),
        source=SOURCE_RUN if summary else SOURCE_UNCHECKED,
        blocks="package",
        recovery="" if summary else (
            "This run produced no measurements, so there is nothing to package."
            if job.get("status") in {"failed", "cancelled"} else
            "Run the benchmark first; only a completed run can be packaged."),
    )]

    # A bridged run cannot be described any other way. A run declaring its own
    # model eligibility is schema 1.2, which is 1.1 plus that block, and 1.1
    # has always carried a serving environment -- so the same description is
    # needed, on the same machine or not. Saying so here is the difference
    # between a checklist item and a raw packager error.
    relayed = bool(job.get("transport"))
    if relayed or eligibility:
        declared = bool(serving and (serving.get("host") or {}).get("cpu"))
        checks.append(check(
            "serving-environment",
            "Serving environment is described separately from the runner",
            field="serving",
            state="ok" if declared else ("unknown" if serving is None else "attention"),
            observed=(serving.get("host") or {}).get("cpu") if declared else None,
            source=SOURCE_YOU,
            blocks="package",
            recovery="" if declared else
                     ("Describe the serving machine: its operating system, processor, memory, "
                      "accelerators and serving runtime. This panel is running in WSL, and what "
                      "WSL can see of the Windows host is not evidence about what served the "
                      "model, so none of it is filled in for you."
                      if relayed else
                      "Describe the serving machine: its operating system, processor, memory, "
                      "accelerators and serving runtime. A result that carries its own model "
                      "eligibility is recorded under schema 1.2, which records this too. "
                      "Nothing is filled in for you: this panel measured the run, it did not "
                      "serve it."),
            detail="Recorded, and marked as declared by you rather than detected here."
                   if declared else
                   "This run's model server and its benchmark runner were different "
                   "environments, so the result records them separately.",
        ))
        checks.append(check(
            "transport-overhead",
            "Bridge latency is inside the measurements",
            state="ok", observed="included, never subtracted", source=SOURCE_RUN,
            detail="Every number this run reports includes the loopback bridge's own latency. "
                   "It is recorded as included and is never subtracted, so a bridged result is "
                   "comparable with other bridged results and is labelled wherever it is shown.",
        ))

    immutable = bool(revision) and revision.strip().lower() not in MUTABLE_REVISIONS
    checks.append(check(
        "weights-revision",
        "Immutable revision of the actual weights source",
        field="model_revision",
        # Absent is a statement of what will be needed; a revision that moves
        # is a value that is present and wrong.
        state="ok" if immutable else ("attention" if not revision else "blocked"),
        observed=revision or None,
        source=SOURCE_YOU,
        blocks="package",
        recovery="" if immutable else
                 "A commit SHA or other immutable revision of the repository the weights really "
                 "came from. 'main', 'master' and 'latest' move, so they identify nothing. This "
                 "is not the file's SHA-256 checksum and not the OmoBench source commit. When "
                 "the weights come from a quantization repository, record that repository as "
                 "the weights source and pin its own revision. Weights with no public immutable "
                 "revision can be identified by their bytes instead: `omobench weights` hashes "
                 "the files you name and prints the revision to record.",
    ))
    recorded = bool(framework) and bool(framework_version)
    checks.append(check(
        "runtime-build",
        "Exact serving runtime version",
        field="framework_version",
        state="ok" if recorded else "attention",
        observed=f"{framework} {framework_version}" if recorded else None,
        source=SOURCE_YOU,
        blocks="package",
        recovery="" if recorded else
                 "The inference runtime and its exact version, for example llama.cpp build "
                 "b10621. An application version (the LM Studio release, say) is a different "
                 "thing from the llama.cpp build it embeds; record the runtime build, and do "
                 "not guess one. Both are recorded separately.",
    ))
    return checks


# The four facts that make a model publishable when no curated record covers
# it. A reviewer reads these; this list is what the panel checks is present,
# not what it verifies -- nothing here opens a licence or fetches a weight.
# See docs/model-eligibility.md.
ELIGIBILITY_FIELDS = ("licence", "licence_url", "weights_publicly_downloadable", "modality")


def eligibility_gaps(eligibility: dict[str, Any] | None) -> list[str]:
    """Which of the four required facts a declaration has not stated."""
    if not eligibility:
        return list(ELIGIBILITY_FIELDS)
    gaps = []
    if not str(eligibility.get("licence") or "").strip():
        gaps.append("licence")
    url = str(eligibility.get("licence_url") or "").strip()
    if not url.startswith("https://"):
        gaps.append("licence_url")
    if eligibility.get("weights_publicly_downloadable") is not True:
        gaps.append("weights_publicly_downloadable")
    if eligibility.get("modality") != "text-generation":
        gaps.append("modality")
    return gaps


def submit_checks(
    *,
    job: dict[str, Any],
    model: str | None,
    catalog: list[str],
    proposal: dict[str, Any] | None,
    submitter: str | None,
    eligibility: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """What stands between a valid bundle and the public registry.

    None of these can stop a benchmark or invalidate a bundle. A model awaiting
    catalog review is the clearest case: the measurements are real, the
    evidence is complete and the bundle validates, and all that is missing is a
    decision somebody else has to make.

    A model is judged by the requirements, not by membership of a list. Three
    routes satisfy the same gate: the curated catalog already records these
    facts; a proposal asks for the model to be added and is reviewed alongside
    the result; or the result carries the facts itself. The curated list is a
    recommendation, not the set of models allowed to exist.
    """
    catalogued = bool(model) and model in catalog
    missing = (proposal or {}).get("missing") or []
    complete = bool(proposal) and not missing
    gaps = eligibility_gaps(eligibility)
    declared = bool(eligibility) and not gaps
    satisfied = catalogued or complete or declared
    route = ("the curated catalog" if catalogued else
             "a proposal for review" if complete else
             "the eligibility this result declares" if declared else None)
    checks: list[dict[str, Any]] = [check(
        "catalog",
        "This model may be published here",
        field="model",
        state="ok" if satisfied else "attention",
        observed=(model or None) if satisfied or not model else model,
        source=SOURCE_CATALOG if catalogued else SOURCE_YOU,
        blocks=None if satisfied else "submit",
        recovery="" if satisfied else
                 f"{model} is not on the curated list, which is fine on its own. To publish it, "
                 "state what makes it publishable: the licence its publisher states, an https "
                 "URL where that licence can be read, that anybody can download the weights, "
                 "and that it is a text-generation model. Alternatively propose it for the "
                 f"curated list ({', '.join(catalog)}), or choose the catalogued identity you "
                 "actually ran -- never substitute a different model for the one you ran. You "
                 "can run this benchmark and export its local report either way.",
        detail=(f"This model meets the publication requirements through {route}. That is "
                "about the model; whether this result may be submitted is the rest of this "
                "list."
                + (" The result and the proposal are judged separately." if complete
                   and not catalogued else "")) if satisfied else
               "Serving a model establishes nothing about whether it may be published. The "
               "requirements are a published licence anybody can read, weights anybody can "
               "obtain, text generation, and an immutable identity.",
    )]

    # Two alternative routes to one gate. The gate above is what blocks
    # submission; a route blocks only once it has been started and left
    # unfinished, because a half-written declaration is a thing to finish
    # while an unstarted one is simply the other route not taken. Blocking on
    # both at once would tell a contributor they need a proposal *and* a
    # declaration, which is the opposite of what this change was for.
    if not catalogued and not complete:
        checks.append(check(
            "eligibility",
            "What makes this model publishable",
            state="ok" if declared else ("unknown" if not eligibility else "blocked"),
            observed="declared" if declared else (", ".join(gaps) if eligibility else None),
            source=SOURCE_YOU,
            blocks="submit" if eligibility and not declared else None,
            recovery="" if declared else
                     "State the licence, an https URL where it can be read, that the weights "
                     "are publicly downloadable, and that the modality is text generation."
                     if eligibility else
                     "Fill in the custom model's licence, licence URL and weights source, or "
                     "propose it for the curated list instead.",
            detail="These are the contributor's word, recorded and labelled as such. The "
                   "registry's validator is what decides, and a reviewer reads the licence "
                   "itself. Nothing here fetches a licence or a weight.",
        ))

    if not catalogued and not declared:
        checks.append(check(
            "proposal",
            "Model proposal for review",
            state="ok" if complete else ("unknown" if proposal is None else "blocked"),
            observed="complete" if complete else (", ".join(missing) or None),
            source=SOURCE_YOU,
            blocks="submit" if proposal is not None and not complete else None,
            recovery="" if complete else
                     "Fill in the model's family, parameter class, publisher page, licence and "
                     "why it belongs, and confirm the weights are publicly downloadable."
                     if proposal is not None else
                     "Optional. Declare this model's own eligibility instead, choose a "
                     "catalogued identity, or open Propose this model for the catalog.",
            detail="A proposal is a request for review and nothing more. Accepting it is a "
                   "separate, reviewed change to the catalog: merging this result would not "
                   "add the model, and this result is marked as proposed wherever it appears.",
        ))

    checks.append(check(
        "submitter",
        "Submitter identity",
        field="submitter",
        # Absent is not the same as empty: with no value to look at, the
        # honest answer is that nobody has entered one yet.
        state="unknown" if submitter is None else ("ok" if submitter.strip() else "attention"),
        observed=(submitter or "").strip() or None,
        source=SOURCE_YOU,
        blocks="submit",
        recovery="" if (submitter or "").strip() else
                 "Enter the GitHub username or organization to record in the bundle.",
    ))

    validation = job.get("validation")
    acceptance = [message for message in (validation or {}).get("errors", [])
                  if "acceptance:" in message]
    checks.append(check(
        "acceptance",
        "Profile acceptance",
        state="unknown" if not validation else ("blocked" if acceptance else "ok"),
        observed=None if not validation else ("not met" if acceptance else "met"),
        source=SOURCE_RUN if validation else SOURCE_UNCHECKED,
        blocks="submit",
        recovery=" ".join(acceptance) if acceptance else
                 ("" if validation else "Checked when you package this run."),
        detail="The profile sets its own minimum successful requests and maximum error rate. "
               "A benchmark that finished is not thereby a result the registry will take.",
    ))

    # Kept apart from acceptance on purpose. Falling short of a threshold means
    # the run did not measure enough; an interruption means what it did measure
    # may not be about the model, because time the machine spent not running
    # was recorded as the model's latency. Same gate, different sentence, and
    # the bundle stays downloadable and inspectable under both.
    interrupted = [message for message in (validation or {}).get("errors", [])
                   if "interruption:" in message]
    if interrupted or validation:
        confirmed = any("was suspended for" in message for message in interrupted)
        checks.append(check(
            "uninterrupted",
            "The machine ran throughout",
            state="blocked" if interrupted else "ok",
            observed=("suspended while measuring" if confirmed else
                      "clocks disagreed while measuring" if interrupted else
                      "no interruption observed"),
            source=SOURCE_RUN,
            blocks="submit",
            recovery=" ".join(interrupted),
            detail="Wall-clock time that passes while this environment is not running is "
                   "recorded as the model's latency, because nothing in a benchmark can tell "
                   "the two apart. A run that was suspended, or that cannot be shown not to "
                   "have been, is kept for diagnosis and is not published.",
        ))
    return checks


def stages(
    checks: list[dict[str, Any]],
    *,
    endpoint_checked: bool,
    identity_settled: bool,
    job: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Where the contributor actually is, derived from state rather than clicks.

    A stage is "done" because the thing it stands for happened, so going back
    to fix something does not lose the stages after it -- they simply recompute
    from what is true now.
    """
    job = job or {}
    completed = bool(job.get("summary"))
    packaged = bool((job.get("validation") or {}).get("valid"))
    submitted = bool((job.get("submission") or {}).get("pull_request_url"))
    runnable = not blocking(checks, "run")

    done = {
        "connect": endpoint_checked,
        "identify": identity_settled,
        "check": runnable and endpoint_checked,
        "run": completed,
        "review": packaged,
        "submit": submitted,
    }
    blocked = {
        "connect": bool(failing(checks, "run")) and not endpoint_checked,
        "check": bool(failing(checks, "run")),
        "review": completed and bool(failing(checks, "package")),
        "submit": packaged and bool(failing(checks, "submit")),
    }
    current = next((stage for stage in STAGES if not done[stage]), STAGES[-1])
    return [{
        "id": stage,
        "label": STAGE_LABELS[stage],
        "state": ("done" if done[stage] else
                  "blocked" if blocked.get(stage) else
                  "current" if stage == current else "todo"),
    } for stage in STAGES]


# What a contributor means by "done" changes six times between a finished
# benchmark and a published result, and the panel used to show two of those
# six. The gap is where false confidence lives: a validated bundle is not a
# submission, and an open pull request is not an accepted result.
COMPLETION_STEPS = (
    ("benchmark", "Benchmark completed"),
    ("evidence", "Evidence saved on this machine"),
    ("package", "Bundle validated"),
    ("ready", "Ready to submit"),
    ("submitted", "Submitted for review"),
    ("published", "Accepted and published"),
)


def completion(job: dict[str, Any], *, submit_blocked: bool,
               package_blocked: bool = False,
               evidence: str | None = None) -> list[dict[str, Any]]:
    """Six states, each reported only once something confirms it.

    Nothing here is inferred forwards. A pull request that exists is submitted
    and not accepted; acceptance is somebody else's decision, happens after a
    merge and a site build, and this panel never observes it -- so it is shown
    as outstanding rather than assumed, however long ago the submission went in.
    """
    validation = job.get("validation")
    submission = job.get("submission") or {}
    measured = bool(job.get("summary"))
    # Where the evidence actually is, looked up rather than taken from a field
    # the job record only carries while the panel is rendering it.
    saved = bool(evidence) and measured
    # Packaging wrote a bundle and nothing about the bundle itself is wrong.
    # Whether the registry will take it is the next state's question, not this
    # one's: a model awaiting review has a perfectly valid bundle. Only a
    # packaging check that is present and wrong refuses a bundle -- a field
    # nobody has retyped since reopening the run is not a refusal, and must not
    # turn a bundle that was built into one that "could not be built".
    packaged = validation is not None and not package_blocked
    validated = bool(validation) and validation.get("valid") is True
    state = {
        "benchmark": "done" if measured else (
            "failed" if job.get("status") in {"failed", "cancelled"} else "pending"),
        "evidence": "done" if saved else "pending",
        "package": "done" if packaged else (
            "failed" if validation is not None else "pending"),
        "ready": "done" if validated and not submit_blocked else (
            "blocked" if validation is not None else "pending"),
        "submitted": "done" if submission.get("pull_request_url") else (
            "failed" if submission.get("started") and not submission.get("pull_request_url")
            else "pending"),
        "published": "pending",
    }
    detail = {
        "benchmark": "This run produced measurements." if measured else
                     "No measurements were produced." if state["benchmark"] == "failed" else
                     "Not finished yet.",
        "evidence": f"Kept at {evidence}." if saved else
                    "The run's own files are written when it finishes.",
        "package": "A schema-valid bundle was written from this evidence." if packaged else
                   "The bundle could not be built from this evidence; its reasons are listed "
                   "above. The measurements are untouched."
                   if state["package"] == "failed" else "Not packaged yet.",
        "ready": "Nothing is standing in the way of submitting this." if state["ready"] == "done"
                 else "The bundle exists; the public registry still needs something above. "
                      "Nothing about that affects the measurements or the saved evidence."
                 if state["ready"] == "blocked" else "Package the run first.",
        "submitted": f"Pull request open: {submission.get('pull_request_url')}"
                     if submission.get("pull_request_url") else
                     "Submission started but no pull request was created. The evidence is "
                     "untouched and can be submitted again without re-running anything."
                     if state["submitted"] == "failed" else "Not submitted.",
        "published": "Acceptance follows review, a merge and a website build. This panel does "
                     "not observe it, so it is never shown as done here -- check the pull "
                     "request.",
    }
    return [{"id": key, "label": label, "state": state[key], "detail": detail[key]}
            for key, label in COMPLETION_STEPS]
