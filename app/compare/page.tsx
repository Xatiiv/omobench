"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { SiteHeader } from "@/components/product/site-header";
import { registryResults } from "@/app/data/registry-results";
import { comparisonExamples } from "@/app/data/comparison-examples";
import { readResultRecord } from "@/app/lib/result-record";
import { barPercent, eligibleLeaderboard, formatMetric, incompatibilities, metricDifference, metrics, rankRuns, type ComparisonEntry, type MetricKey } from "@/app/lib/comparison";
import "./comparison.css";

const published: ComparisonEntry[] = registryResults.map(result => ({ key: `registry:${result.id}`, origin: "registry", result }));
const maxImportBytes = 1024 * 1024;
function subscribeToLocation(onChange: () => void) {
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
}
const getLocationSearch = () => window.location.search;
const getServerSearch = () => "";

function initialPeers(entries: ComparisonEntry[], reference?: ComparisonEntry) {
  if (!reference) return [];
  return entries.filter(entry => entry.key !== reference.key && !incompatibilities(reference.result, entry.result).length).slice(0, 3).map(entry => entry.key);
}

function SetupIcon({ gpu = false }: { gpu?: boolean }) {
  return <svg viewBox="0 0 32 32" aria-hidden="true" fill="none"><rect x="7" y="7" width="18" height="18" rx="3" /><path d="M12 2v5m8-5v5M12 25v5m8-5v5M2 12h5m-5 8h5m18-8h5m-5 8h5" />{gpu ? <circle cx="16" cy="16" r="5" /> : <rect x="12" y="12" width="8" height="8" rx="1" />}</svg>;
}

function SetupCard({ entry, index, reference }: { entry: ComparisonEntry; index: number; reference: boolean }) {
  const r = entry.result;
  return <article className={`setup-card tone-${index}${reference ? " is-reference" : ""}`}>
    <div className="setup-card-top"><span className="comparison-kicker">{reference ? entry.origin === "local" ? "YOUR RESULT · REFERENCE" : "REFERENCE RUN" : `COMPARISON ${index}`}</span><SetupIcon gpu={r.executionDevice === "gpu"} /></div>
    <h3>{r.hardware.display}</h3>
    <div className="setup-card-meta"><span className="device-label">{r.executionDevice.toUpperCase()}</span><span>{r.framework} {r.frameworkVersion}</span></div>
    <div className="setup-card-score"><strong>{formatMetric(r.outputTokensPerSecond)}</strong><span>output tok/s</span></div>
    <dl className="setup-facts"><div><dt>Measured requests</dt><dd>{r.requests.succeeded} / {r.requests.attempted} successful</dd></div><div><dt>Error rate</dt><dd>{formatMetric(r.errorRatePercent)}%</dd></div><div><dt>System memory</dt><dd>{formatMetric(r.hardware.ramGb)} GB</dd></div></dl>
    <details><summary>Setup details</summary><dl className="setup-details"><div><dt>Host CPU</dt><dd>{r.hardware.cpu}</dd></div><div><dt>OS</dt><dd>{r.hardware.os}</dd></div>{r.hardware.gpus.map((g, i) => <div key={i}><dt>Accelerator</dt><dd>{g.count} × {g.model}, {g.memoryGb} GB each</dd></div>)}<div><dt>Runtime arguments</dt><dd>{r.runtimeArguments.join(" ") || "None recorded"}</dd></div><div><dt>Run ID</dt><dd>{r.id}</dd></div></dl></details>
    <footer><span>{entry.origin === "local" ? "Private file · not reviewed" : entry.origin === "example" ? "Fictional example" : r.submitter}</span>{entry.origin === "registry" && <Link href={`/runs/${encodeURIComponent(r.id)}#evidence`}>Evidence ↗</Link>}</footer>
  </article>;
}

function MetricChart({ entries, metric, featured = false }: { entries: ComparisonEntry[]; metric: MetricKey; featured?: boolean }) {
  const definition = metrics[metric];
  const maximum = Math.max(0, ...entries.map(entry => entry.result[metric]));
  const reference = entries[0]?.result[metric];
  return <section className={`metric-chart${featured ? " featured-chart" : ""}`} aria-label={definition.title}>
    <div className="chart-title"><div><p className="comparison-kicker">{featured ? "GENERATION SPEED" : metric.startsWith("ttft") ? "RESPONSE TIME" : "STREAMING SMOOTHNESS"}</p><h2>{definition.title}</h2></div><span className="metric-direction">{definition.higher ? "↑ Higher is better" : "↓ Lower is better"}</span></div>
    <div className="comparison-axis" aria-hidden="true"><span>0</span><span>{formatMetric(maximum)} {definition.unit}</span></div>
    <ol className="comparison-bars">{entries.map((entry, index) => {
      const value = entry.result[metric];
      const difference = metricDifference(value, reference, definition.higher);
      return <li key={entry.key} className={`tone-${index}`}>
        <div className="bar-label"><span><i aria-hidden="true" />{entry.result.hardware.display}{index === 0 && <small>Reference</small>}</span><strong>{formatMetric(value)} <span>{definition.unit}</span></strong></div>
        <div className="comparison-track" aria-hidden="true"><div className="comparison-fill" style={{ width: `${barPercent(value, maximum)}%` }} /></div>
        <p className={`bar-difference ${index > 0 && difference.favorable !== null ? difference.favorable ? "favorable" : "unfavorable" : ""}`}>{index === 0 ? "Reference measurement" : difference.text}</p>
      </li>;
    })}</ol>
    <p className="chart-footnote">{featured ? "Aggregate output throughput over the measured window, not peak single-request decode speed." : "Final measured-window latency. Each chart has its own zero-based scale."}</p>
  </section>;
}

export default function ComparePage() {
  const [mode, setMode] = useState<"registry" | "example">("registry");
  const [local, setLocal] = useState<ComparisonEntry | null>(null);
  const [referenceKey, setReferenceKey] = useState("");
  const [peerKeys, setPeerKeys] = useState<string[] | null>(null);
  const [latency, setLatency] = useState<"p50" | "p95">("p50");
  const [sortMetric, setSortMetric] = useState<MetricKey>("outputTokensPerSecond");
  const [device, setDevice] = useState("all");
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [reading, setReading] = useState(false);
  const [pastedJson, setPastedJson] = useState("");
  const importGeneration = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);

  const search = useSyncExternalStore(subscribeToLocation, getLocationSearch, getServerSearch);
  const requested = new URLSearchParams(search).get("run");
  useEffect(() => () => { importGeneration.current += 1; }, []);

  const entries = mode === "example" ? comparisonExamples : local ? [local, ...published] : published;
  const reference = entries.find(entry => entry.key === referenceKey) ?? (mode === "registry" ? published.find(entry => entry.result.id === requested) : undefined) ?? entries[0];
  const selectedPeerKeys = peerKeys ?? initialPeers(entries, reference);
  const peers = reference ? entries.filter(entry => entry.key !== reference.key && !incompatibilities(reference.result, entry.result).length) : [];
  const selected = reference ? [reference, ...peers.filter(entry => selectedPeerKeys.includes(entry.key)).slice(0, 3)] : [];
  const unmatched = reference ? entries.filter(entry => entry.key !== reference.key && incompatibilities(reference.result, entry.result).length > 0) : [];
  const leaderboard = reference ? rankRuns(eligibleLeaderboard(entries, reference.result, mode).filter(entry => device === "all" || entry.result.executionDevice === device), sortMetric) : [];
  const choices = peers.filter(entry => `${entry.result.hardware.display} ${entry.result.framework} ${entry.result.submitter}`.toLowerCase().includes(query.toLowerCase()));

  function chooseReference(key: string) {
    const next = entries.find(entry => entry.key === key);
    setReferenceKey(key); setPeerKeys(initialPeers(entries, next)); setQuery(""); setDevice("all");
  }

  function changeMode(next: "registry" | "example") {
    importGeneration.current += 1; setReading(false); setMode(next); setMessage(""); setQuery(""); setDevice("all");
    const nextEntries = next === "example" ? comparisonExamples : local ? [local, ...published] : published;
    setReferenceKey(nextEntries[0]?.key ?? ""); setPeerKeys(initialPeers(nextEntries, nextEntries[0]));
  }

  function acceptText(text: string, generation: number) {
    if (new TextEncoder().encode(text).byteLength > maxImportBytes) throw new Error("Choose a result.json smaller than 1 MiB.");
    const result = readResultRecord("selected result.json", JSON.parse(text));
    if (generation !== importGeneration.current) return;
    const entry: ComparisonEntry = { key: `local:${result.id}`, origin: "local", result };
    setLocal(entry); setMode("registry"); setReferenceKey(entry.key); setPeerKeys(initialPeers([entry, ...published], entry)); setDevice("all"); setQuery(""); setPastedJson("");
  }

  function importError(error: unknown) {
    setMessage(error instanceof SyntaxError ? "This is not valid JSON. Choose the packaged result.json, not a local summary or ZIP." : error instanceof Error ? error.message : "This file could not be read.");
  }

  async function openFile(file?: File) {
    if (!file) return;
    const generation = ++importGeneration.current;
    setReading(true); setMessage("");
    try {
      if (file.size > maxImportBytes) throw new Error("Choose a result.json smaller than 1 MiB, not the complete bundle or raw runner output.");
      const text = await file.text();
      if (generation === importGeneration.current) acceptText(text, generation);
    } catch (error) {
      if (generation === importGeneration.current) importError(error);
    } finally {
      if (generation === importGeneration.current) {
        setReading(false);
        if (fileInput.current) fileInput.current.value = "";
      }
    }
  }

  function openPasted() {
    const generation = ++importGeneration.current;
    setReading(false); setMessage("");
    try { acceptText(pastedJson, generation); } catch (error) { importError(error); }
  }

  function clearLocal() {
    importGeneration.current += 1; setReading(false); setLocal(null); setPastedJson(""); setMessage("");
    setReferenceKey(published[0]?.key ?? ""); setPeerKeys(initialPeers(published, published[0]));
  }

  return <main className="product-page comparison-page">
    <div className="comparison-container"><SiteHeader active="compare" />
      <section className="comparison-hero">
        <div><p className="eyebrow">THE SAME WORKLOAD. DIFFERENT POSSIBILITIES.</p><h1>See how your<br /><span>setup stacks up.</span></h1><p className="comparison-intro">Put real measurements side by side. Explore generation speed, response time and the hardware behind every run.</p></div>
        <div className="comparison-stat"><span className="comparison-kicker">REGISTRY RUNS</span><strong>{published.length.toLocaleString()}</strong><span>{published.length ? "Included in this website build" : "Your result could be the first"}</span><Link href="/run">Run a benchmark <span aria-hidden="true">↗</span></Link></div>
      </section>

      <div className="comparison-toolbar"><div className="mode-switch" role="group" aria-label="Comparison data"><button aria-pressed={mode === "registry"} onClick={() => changeMode("registry")}>Community results <span>{published.length}</span></button><button aria-pressed={mode === "example"} onClick={() => changeMode("example")}>Illustrative preview</button></div><button className="product-button secondary-button" onClick={() => fileInput.current?.click()} disabled={reading}>{reading ? "Reading file…" : "Open your result.json"}<span aria-hidden="true">＋</span></button><input ref={fileInput} type="file" accept=".json,application/json" aria-label="Open packaged result.json" className="comparison-file-input" onChange={event => void openFile(event.target.files?.[0])} /></div>
      <p className="comparison-privacy">Your file is read in this browser tab only. It is never uploaded or added to community rankings, and is cleared when you reload.</p>
      <details className="comparison-paste"><summary>Or paste a packaged result</summary><label htmlFor="pasted-result">Contents of result.json</label><textarea id="pasted-result" rows={6} spellCheck={false} maxLength={maxImportBytes} value={pastedJson} onChange={event => setPastedJson(event.target.value)} placeholder="Paste the normalized JSON from your submission preview" /><button className="product-button secondary-button" disabled={!pastedJson.trim()} onClick={openPasted}>Open pasted result</button></details>
      {requested && !referenceKey && !published.some(entry => entry.result.id === requested) && <p className="comparison-notice" role="status">That run is not included in this website build. You can open its result.json privately instead.</p>}
      {message && <p className="comparison-notice comparison-error" role="alert">{message}</p>}
      {mode === "example" && <aside className="comparison-notice example-notice"><strong>Illustrative preview</strong><span>All setups and measurements below are fictional. This demonstrates the interface, not actual hardware performance.</span><button onClick={() => changeMode("registry")}>Back to real results</button></aside>}
      {mode === "registry" && local && <aside className="comparison-notice local-notice"><strong>Private result loaded</strong><span>Summary checks passed. Evidence, model eligibility and acceptance thresholds have not been verified here. This run is not published.</span><button onClick={clearLocal}>Remove local result</button></aside>}

      {!reference ? <section className="comparison-empty"><div className="empty-compare-icon" aria-hidden="true"><SetupIcon /><span>↔</span><SetupIcon gpu /></div><p className="comparison-kicker">THE BOARD IS READY</p><h2>Great comparisons start<br />with real results.</h2><p>No community runs are included in this build yet. Open your packaged result to see its charts, or explore the clearly labelled example.</p><div className="hero-actions"><button className="product-button" onClick={() => fileInput.current?.click()}>Open your result.json</button><button className="product-button secondary-button" onClick={() => changeMode("example")}>Explore the example ↗</button></div><details><summary>Where do I get result.json?</summary><p>In the local panel, finish a run, choose Prepare submission, Package &amp; validate, then Review submission files. Download result.json there, or extract it from Download complete bundle. Opening it here does not submit it.</p></details></section> : <>
        <section className="comparison-selection" aria-label="Choose runs">
          <div className="reference-picker"><label htmlFor="reference-run">01 / Choose your reference</label><select id="reference-run" value={reference.key} onChange={event => chooseReference(event.target.value)}>{entries.map(entry => <option value={entry.key} key={entry.key}>{entry.origin === "local" ? "Your file · " : ""}{entry.result.hardware.display} · {entry.result.submitter} · {entry.result.id}</option>)}</select><p>All differences are measured against this run.</p></div>
          <div className="workload-contract"><span className="comparison-kicker">MATCHING WORKLOAD</span><h2>{reference.result.model}</h2><p>{reference.result.quantization ?? "Quantization not recorded"} <span>·</span> {reference.result.profile}</p><details><summary>What makes a comparison compatible?</summary><p>Model, weights source, immutable revision, quantization, profile ID and checksum, and GuideLLM version must match. Hardware, operating systems and runtime settings may differ. This compares complete setups, not the effect of one isolated component.</p><dl><div><dt>Weights source</dt><dd>{reference.result.modelSource ?? "Canonical model repository (legacy record)"}</dd></div><div><dt>Weights revision</dt><dd>{reference.result.modelRevision}</dd></div><div><dt>Profile checksum</dt><dd>{reference.result.profileSha256}</dd></div><div><dt>GuideLLM</dt><dd>{reference.result.runnerVersion}</dd></div></dl></details></div>
        </section>

        <section className="peer-section"><div className="comparison-section-heading"><div><p className="comparison-kicker">02 / BUILD YOUR COMPARISON</p><h2>{peers.length ? "Choose up to three other runs." : "Your reference is ready."}</h2></div><span>{peers.length} compatible {peers.length === 1 ? "run" : "runs"}</span></div>
          {peers.length ? <><label className="peer-search">Find a setup<input type="search" placeholder="Hardware, runtime or submitter" value={query} onChange={event => setQuery(event.target.value)} /></label><div className="peer-choices">{choices.map(entry => { const checked = selectedPeerKeys.includes(entry.key); return <label className={`peer-choice${checked ? " selected" : ""}`} key={entry.key}><input type="checkbox" checked={checked} disabled={!checked && selectedPeerKeys.length >= 3} onChange={() => setPeerKeys(keys => checked ? (keys ?? selectedPeerKeys).filter(key => key !== entry.key) : [...(keys ?? selectedPeerKeys), entry.key].slice(0, 3))} /><span><strong>{entry.result.hardware.display}</strong><small>{entry.result.framework} · {entry.result.submitter} · {entry.result.id}</small></span></label>; })}{choices.length === 0 && <p>No compatible runs match your search.</p>}</div></> : <p className="comparison-muted">{mode === "registry" ? "No other compatible runs are available in this build. Your measurements are shown below. Community comparisons appear after matching evidence is submitted, reviewed and included in a website build." : "Choose a different reference to explore the example."}</p>}
          {unmatched.length > 0 && <details className="unmatched-runs"><summary>{unmatched.length} {unmatched.length === 1 ? "run excluded" : "runs excluded"} from direct comparison</summary><ul>{unmatched.map(entry => <li key={entry.key}><strong>{entry.result.hardware.display}</strong><span>{incompatibilities(reference.result, entry.result).join(" · ")}</span></li>)}</ul></details>}
          {incompatibilities(reference.result, reference.result).length > 0 && <p className="comparison-notice">This record is missing comparison metadata. Its values remain visible, but it cannot join a ranking or percentage comparison.</p>}
        </section>

        <div className="selected-setups" style={{ "--setup-count": selected.length } as React.CSSProperties}>{selected.map((entry, index) => <SetupCard key={entry.key} entry={entry} index={index} reference={index === 0} />)}</div>
        <div className="comparison-charts"><MetricChart entries={selected} metric="outputTokensPerSecond" featured /><div className="latency-heading"><div><p className="comparison-kicker">HOW RESPONSIVE DOES IT FEEL?</p><h2>A closer look at latency.</h2></div><div className="mode-switch" role="group" aria-label="Latency statistic"><button aria-pressed={latency === "p50"} onClick={() => setLatency("p50")}>Median · p50</button><button aria-pressed={latency === "p95"} onClick={() => setLatency("p95")}>Tail · p95</button></div><p>p50 is the median. p95 describes the slower end of observed latencies.</p></div><MetricChart entries={selected} metric={latency === "p50" ? "ttftP50" : "ttftP95"} /><MetricChart entries={selected} metric={latency === "p50" ? "itlP50" : "itlP95"} /></div>

        <section className="comparison-leaderboard"><div className="comparison-section-heading"><div><p className="comparison-kicker">{mode === "example" ? "FICTIONAL EXAMPLES ONLY" : "SAME DECLARED WORKLOAD"}</p><h2>{mode === "example" ? "Example ordering" : "Community run leaderboard"}</h2></div><span>{leaderboard.length} {mode === "example" ? "examples" : "matching runs"}</span></div><p className="comparison-muted">{mode === "example" ? "This ordering uses simulated measurements and is not a hardware ranking." : "Ranks apply only to the matching runs and device filter below. Repeat runs count separately, this is not a ranking of unique machines or people. Private files are excluded."}</p><div className="leaderboard-controls"><label>Order by<select value={sortMetric} onChange={event => setSortMetric(event.target.value as MetricKey)}>{Object.entries(metrics).map(([key, metric]) => <option key={key} value={key}>{metric.title}, {metric.higher ? "higher" : "lower"} first</option>)}</select></label><label>Execution device<select value={device} onChange={event => setDevice(event.target.value)}><option value="all">All devices</option><option value="cpu">CPU</option><option value="gpu">GPU</option></select></label></div>
          {leaderboard.length ? <div className="leaderboard-scroll" tabIndex={0} role="region" aria-label="Ranked benchmark runs"><table><caption>{metrics[sortMetric].title} · {metrics[sortMetric].higher ? "Higher" : "Lower"} is better · {mode === "example" ? "Illustrative data" : reference.result.profile}</caption><thead><tr><th scope="col">Rank</th><th scope="col">Setup / submitter</th><th scope="col">{metrics[sortMetric].title}</th><th scope="col">Successful / attempted</th><th scope="col">Error rate</th><th scope="col">Record</th></tr></thead><tbody>{leaderboard.map(({ entry, rank }) => <tr className={entry.key === reference.key ? "reference-row" : ""} key={entry.key}><td className="rank-cell">{rank.toString().padStart(2, "0")}</td><th scope="row"><strong>{entry.result.hardware.display}</strong><small>{entry.result.framework} · {entry.result.submitter}</small>{entry.key === reference.key && <span className="table-reference">Reference</span>}</th><td className="rank-value">{formatMetric(entry.result[sortMetric])} <small>{metrics[sortMetric].unit}</small></td><td>{entry.result.requests.succeeded} / {entry.result.requests.attempted}</td><td>{formatMetric(entry.result.errorRatePercent)}%</td><td>{entry.origin === "registry" ? <Link href={`/runs/${encodeURIComponent(entry.result.id)}#evidence`}>Evidence ↗</Link> : <span>Illustrative</span>}</td></tr>)}</tbody></table></div> : <div className="leaderboard-empty">No community runs match this reference and device filter yet. Your private result is not added to this leaderboard.</div>}
        </section>
      </>}
      <section className="comparison-next"><div><p className="comparison-kicker">MAKE THE NEXT COMPARISON POSSIBLE</p><h2>Add evidence. Help everyone choose.</h2><p>Run a fixed profile, review your bundle and submit it through the local panel. Community results appear after review, merge and a website build.</p></div><Link className="product-button" href="/run">Run your benchmark ↗</Link></section>
      <footer className="comparison-footer">Measured performance. Inspectable evidence. No composite scores.</footer>
    </div>
  </main>;
}
