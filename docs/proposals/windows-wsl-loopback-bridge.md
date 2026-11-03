# Proposal: reaching a Windows-local model server from the WSL runner

Status: **integration design only.** An isolated, disabled
[HTTP frontend](../bridge-frontend-status.md) now implements parsing and token
stripping. The Windows transport, runner integration and panel action below
remain unimplemented. No usable bridge is
installed, and the panel's loopback rule is unchanged. This document exists so
the remaining integration can be reviewed before it is enabled.

One thing this document previously asserted has since been *measured*, and the
measurement contradicted it; §4 records what was actually observed and what was
changed in shipped code as a result. The rest of the design depends on that
correction, so it is stated before the bridge itself.

## 1. The problem, stated accurately

OmoBench's runner runs inside WSL2. A contributor's inference server often runs
on the Windows side (LM Studio, Ollama's Windows build, llama.cpp's Windows
server). Those are two different loopback namespaces:

- Windows forwards `127.0.0.1:<port>` **into** WSL2 for ports a Linux process
  listens on. That direction already works and is why the panel is reachable
  from a Windows browser.
- The reverse is **not** provided in WSL2's default NAT networking mode. From
  inside the distribution, `127.0.0.1` is the distribution's own loopback. On
  the machine this was written on (WSL 2.4.13.0, no `.wslconfig`, default NAT),
  the Windows host appears only as the default gateway, `172.27.64.1` — an
  address that changes between boots and is not loopback.
- Windows 11's **mirrored** networking mode (`networkingMode=mirrored` in
  `%USERPROFILE%\.wslconfig`) does make `127.0.0.1` shared in both directions.
  Where a contributor has enabled it, no bridge is needed at all.

The panel refuses non-loopback endpoints on purpose: a result records the
runner host's hardware as the hardware that served the run, and a local panel
must not become a general-purpose client for arbitrary hosts. Today the
supported workaround is a loopback forwarder the contributor runs themselves,
which the panel then sees as an ordinary local server.

## 2. What a bridge would have to be

A **user-selected, authenticated, single-destination loopback forwarder**, owned
by OmoBench, running inside the distribution, with **no listener on any address
other than the distribution's own loopback**.

### Destination selection

- The contributor types the Windows-side address explicitly:
  `http://127.0.0.1:<port>` **as Windows sees it**. There is no discovery, no
  scanning, no probing of a range, and no reading of other processes' listening
  sockets.
- Only a Windows **loopback** destination is accepted: `127.0.0.1`, `localhost`
  or `[::1]` plus a port. A LAN address, a hostname, a public address or a
  `host.docker.internal`-style alias is refused with the same explanation the
  panel already gives for its own endpoint field.
- The destination is **resolved once, at start, and frozen**. It is held as a
  literal address and port, not as a string re-parsed per request. Changing it
  means stopping the bridge and starting another one; there is no in-place
  retarget, no request that can carry a new destination, and nothing a server
  returns can move it (§4).

### How the Windows side is reached

The bridge's Windows half must be a process OmoBench starts on Windows (the
companion is the natural owner: it already runs there, already talks to WSL, and
already owns per-user state). It connects to `127.0.0.1:<port>` **on Windows**,
where that is a genuine loopback connection, and relays bytes to the Linux half
over the `wsl.exe` child process's stdin/stdout.

#### Every listener and every outbound connection

The earlier draft of this document claimed "no socket is opened on any network
interface on either side". **That was wrong**: the Linux half is a TCP listener,
and the Windows half opens a TCP connection. What the design actually
guarantees is narrower and checkable — **no non-loopback listener, on either
side** — so the complete inventory belongs here rather than a slogan.

| # | Endpoint | Side | Kind | Bound to / connects to | Who may reach it |
| --- | --- | --- | --- | --- | --- |
| 1 | Bridge front door | Linux (in the distribution) | **listener**, TCP | `127.0.0.1:<ephemeral>`, explicitly, never `0.0.0.0` and never `::` | Processes inside that distribution only. Authenticated (§"Authentication"). |
| 2 | Model server | Windows | **outbound** TCP connection | to the frozen `127.0.0.1:<contributor's port>` | n/a — the bridge is the client |
| 3 | Relay transport | both | **no socket**: `wsl.exe` child process stdin/stdout pipes | n/a | the companion process and its child |
| 4 | The panel itself | Linux | **listener**, TCP (already exists, unchanged) | `127.0.0.1:<panel port>`, forwarded into Windows by WSL | unchanged by this proposal |

Nothing else listens. In particular there is no listener on the Windows side at
all: the Windows half is purely a client, which is why no firewall rule is
needed and none is requested (§8).

The `wsl.exe` stdio transport is the one part of this that must not be assumed
to work before it is measured: it is bounded by that pipe's throughput and
latency, and §6 treats that as a measurement problem rather than an assumption.

### Authentication and transport ownership

- The Linux listener accepts a request only when it presents a per-instance
  token. The current frontend mints it locally; the final owner-to-runner
  handoff is an integration gate, not implemented. It must be passed to the
  panel the way the panel's own token already is: never written to the
  checkout, never logged, never placed in a URL the runner records, never
  stored in the companion configuration file.
- **The token never leaves the Linux hop.** The bridge strips its own
  authorization header before relaying, so the contributor's model server never
  receives OmoBench's bridge secret, and the panel's token is never forwarded
  either. Nothing derived from either token is written into a result bundle, a
  runner log, an environment snapshot or a diagnostic file; the existing
  redaction in `Diagnostics.Redact` and `ui.redact` covers the shapes these
  take, and the bridge adds its own header name to both.
- Any `Authorization` header the *contributor* configured for their own server
  is their credential, not OmoBench's: it is relayed unchanged and redacted
  everywhere OmoBench writes, exactly as it is today.
- The stdio transport is owned by the companion that spawned it. The child dies
  with the companion; a bridge is never shared between panels, never reused
  across checkouts, and never adopted from a previous process.

### Concurrency, framing, backpressure and failure

A benchmark is not one request at a time — `throughput-v1` runs 16 concurrent
streams — so a single-stream relay would be a correctness bug, not just a slow
one. The transport is one pipe pair, so it has to carry many connections:

- **Framing.** The stdio channel carries length-prefixed frames, each tagged
  with a stream id: `open(stream, …)`, `data(stream, bytes)`,
  `close(stream, reason)`. The relay never parses what is inside `data`; the
  framing exists only to demultiplex, and a frame boundary never implies an
  HTTP message boundary.
- **Concurrency.** Each accepted loopback connection becomes one stream id and
  one Windows-side TCP connection, for as long as it lives. Streams are
  independent: a slow or stuck stream must not stall the others, which is the
  property §6.5 tests rather than assumes.
- **Backpressure.** Reads are bounded and credit-based per stream. A consumer
  that stops reading stops its own stream's credit and nothing else; the relay
  never buffers an unbounded amount on behalf of a stalled stream, because an
  SSE response from a fast server to a stalled client is otherwise unbounded
  memory growth in the middle of a measurement.
- **Cancellation.** Stopping a benchmark closes the runner's connections. Each
  close propagates as a `close` frame, and the Windows half closes exactly that
  one TCP connection. Cancelling a run never stops the bridge, and never
  touches the model server (§8).
- **Disconnects.** Either endpoint vanishing closes only its own stream, with a
  reason the panel can report. A half-open connection is detected and closed
  rather than left to time out inside a measurement.
- **Process failure.** If the `wsl.exe` child dies, every stream is closed with
  a stated reason, the Linux listener is torn down, the token is discarded and
  the panel reports the bridge as stopped. A benchmark in flight fails with
  that reason rather than with a bare connection error, and **never** silently
  continues against a different destination.

### Lifecycle

| Event | Behaviour |
| --- | --- |
| Start | Explicit, from the panel, after the contributor enters the Windows port. Never implicit, never on page load. |
| Port | Ephemeral on the Linux side, chosen by the kernel and reported back; the Windows-side port is the contributor's. |
| Reuse | One bridge per (distribution, Windows port). A second request for the same pair reuses the running bridge rather than starting a second. |
| Destination change | Not an operation. A different Windows port is a different bridge: the running one is stopped, its token discarded and its listener closed, before another starts. |
| Cancellation | A pending start is cancellable and leaves nothing behind, like the existing preparation stages. |
| Benchmark end | The bridge is **not** torn down by the end of a run; a contributor usually runs several. It is stopped explicitly, when the panel stops, or when the companion exits. |
| The model server | Never touched. The bridge connects to it and relays; stopping the bridge closes sockets and nothing else. The existing rule that cancellation stops only OmoBench's own process group is unchanged. |
| Cleanup | On stop: the listener closes, every stream is closed, the relay process exits, the token is discarded, and the ephemeral port is released. A crashed bridge leaves a dead listener at worst, which the next start detects and replaces rather than reusing. |

## 3. Why a byte relay is not a redirect defence

This needs saying plainly, because the earlier draft got it backwards.

**A raw byte relay does not parse or reject HTTP redirects.** It does not read
status lines, it does not look at `Location`, and it cannot tell a `307` from a
`200`. Saying "the relay does not follow redirects" is true only in the sense
that a pipe does not follow anything — it is not a protection, and it must not
be counted as one. A relay that *did* inspect responses would be an HTTP proxy,
which §8 rules out.

Redirect handling therefore belongs entirely to the HTTP clients at each end of
the relay, and there are exactly two in OmoBench:

| Client | Where | What it does with a redirect |
| --- | --- | --- |
| `ui.probe_connection` and `ui.served_models` — the panel's connection checks | `cli/src/omobench/ui.py` | Refuses. `build_opener(ProxyHandler({}), NoRedirect())` raises on any redirect: "The model server redirected the request. Enter its direct localhost address." |
| GuideLLM's `OpenAIHTTPBackend` — the requests that produce the measurements | the pinned runner, `guidellm/backends/openai/http.py` | **Followed by default.** See §4. |

## 4. The pinned runner follows redirects — measured, and now constrained

`OpenAIHTTPBackendArgs.follow_redirects` defaults to **`True`** in GuideLLM
0.7.3. That is not an inference from the field's declaration; it was observed
end to end, against two local fixture servers:

- a **decoy** on the port OmoBench was told to measure, answering `/health` and
  `/v1/models` normally and every `/v1/chat/completions` with a `307` naming a
  second port;
- a **second server** on that other port, which nobody selected, recording what
  reached it.

| Backend string | Requests to the selected destination | Requests to the destination nobody selected |
| --- | --- | --- |
| as OmoBench built it before this change | 3 | **3 — every one of them** |
| with `follow_redirects=false` | 3 | 0 |

So before this change, a loopback server could move OmoBench's entire benchmark
— prompts included — to any host it named, while the result still recorded the
loopback endpoint as the thing that had been measured.

The panel's connection check is not a substitute for this. It refuses
redirects, so it would have caught the fixture above; but it is two requests at
one moment, and the benchmark is every request after it. A server that answers
the check normally and redirects afterwards — or redirects only the request
shape the benchmark uses — passes the check and still moves the measurement.
The guarantee has to live on the client that makes the measured requests.

This is not a bridge problem. It is a property of the shipped advanced
("connect a server you already run") route, and it made the sentence this
design needs — *a redirect cannot escape the explicitly selected destination* —
false. It has therefore been corrected in `cli.build_guidellm_command()`, which
now pins `follow_redirects=false` onto the same backend string that carries
`target=`, so the destination and the rule that protects it cannot drift apart.
`cli/tests/test_redirect_destination.py` asserts both halves of the table
above, the control case included, so a regression in either direction fails.

**With that in place**, the claim this design relies on is true and checkable:

- The runner's destination is the bridge's loopback front door, fixed at start.
- A redirect from the contributor's server is refused by the runner's own HTTP
  client, and never reaches the relay as a new destination.
- The relay cannot be retargeted by anything on the wire, because it does not
  read the wire; its destination came from the frozen value in §2.
- A redirect can therefore do exactly one thing: fail that request, visibly.

This was the condition the milestone set — *if the pinned runner follows
redirects and this cannot be constrained safely, treat that as an unresolved
implementation blocker.* It follows them, and it **can** be constrained, from
the backend string OmoBench already builds, with no patching of the pinned
runner and no change to any profile. The blocker is resolved; the evidence is
the test, not this paragraph.

### Still open

`guidellm/utils/text.py` constructs a bare `httpx.Client()` for loading text
assets. httpx's own default is `follow_redirects=False`, so it is not an
escape today, but it is not covered by OmoBench's backend string either. Before
a bridge ships, that path should be confirmed to be unreachable from a
benchmark run, or constrained the same way.

## 5. What this does to the result's provenance

Today "the runner host" and "the serving host" are the same machine, and
`hardware` describes it. With a bridge they are still the same *physical*
machine but two different operating-system environments: the runner's hardware
snapshot comes from the distribution, while the server ran on Windows with
whatever GPU driver and runtime Windows has.

A result produced through a bridge must therefore record, at minimum:

- that a bridge was used, and its destination port — never a host address;
- the runner host environment, as today, labelled as the **runner host**;
- the serving host environment as **declared**, not detected, and labelled as
  the **serving host**, because OmoBench cannot inspect the Windows process
  from inside the distribution.

Until the schema can express those two separately, a bridged run must be
**local-report only**: viewable and exportable, not publishable. Publishing a
bridged run under today's single-`hardware` schema would assert that the
distribution's view of the machine described the server, which is exactly the
sort of unverified claim the project refuses elsewhere.

## 6. Effect on measurements

A relay adds a hop to every request and every streamed chunk. For a benchmark
whose headline metrics are time to first token and inter-token latency, that is
not negligible and must not be hidden:

- Characterise it before shipping: measure round-trip latency and streaming
  throughput through the bridge against a fixture server, with and without the
  bridge, on the same machine, at concurrency 1 and at the concurrency
  `throughput-v1` uses.
- If the added latency is material at the scale of the metrics, say so in the
  result and in the panel, and keep bridged runs out of the public registry
  regardless of §5.
- Never silently subtract an estimate of the overhead from a measurement.

## 7. Compatibility with WSL networking modes

| Mode | Behaviour |
| --- | --- |
| NAT (default) | The bridge is needed and is the supported path. |
| Mirrored (`networkingMode=mirrored`) | `127.0.0.1` is already shared; the panel should detect that the destination is directly reachable and refuse to start a pointless bridge, telling the contributor to use the address directly. |
| WSL1 | Out of scope: the runner requires WSL2. |
| No WSL (Linux or macOS host) | Out of scope: there is one loopback namespace already. |

Every one of these must be decided by a **positive test**, never by inference:

- Reachability is a bounded TCP connection attempt to the chosen destination
  from inside the distribution. Not a read of `%USERPROFILE%\.wslconfig`, not
  the WSL version string, not the presence of a gateway address.
- "The bridge is listening" is an accepted, authenticated connection to the
  Linux front door, not the relay process being alive.
- "The listener is loopback-only" is a connection attempt from a non-loopback
  address that is refused, not a reading of the bind argument.
- A negative result says which test was run and what answered, in the style the
  server check already uses.

## 8. What is explicitly not proposed

- No LAN scanning, port probing, or discovery of any kind.
- No listener on `0.0.0.0`, `::`, or any non-loopback address, on either side.
  The Windows side has no listener at all.
- No firewall changes, and no instruction to the contributor to make any.
- No relaxation of the panel's endpoint rules: the panel would still accept only
  a loopback address, which is what the bridge's Linux half provides.
- No general-purpose proxy: one destination, frozen at start, no routing, no
  path dispatch, no `CONNECT`, no response inspection.
- No forwarding of OmoBench's own authentication secrets to the model server,
  and none of them in any evidence file, log or diagnostic.
- No stopping, restarting or otherwise touching the contributor's model server,
  including when a run is cancelled.
- No automatic publication of bridged runs while the schema cannot distinguish
  runner host from serving host.

## 9. Test plan

Fixtures only; no real inference, no model, no LAN. Nothing below is
implemented yet.

1. **Destination validation**: loopback accepted; LAN, public, hostname,
   credentials-in-URL and non-loopback aliases refused, each with its reason.
2. **Redirect escape**: a fixture that answers `/health` and `/v1/models`
   normally and redirects every completion to a second fixture nobody selected.
   The second fixture must record **zero** requests, through the bridge and
   without it. The control — the same run with the protection removed, where
   the second fixture *is* reached — runs too, so the test cannot quietly stop
   testing anything. (The no-bridge half of this exists already:
   `cli/tests/test_redirect_destination.py`.)
3. **Destination immutability**: a redirect, a `Location` header on a
   non-redirect status, and an absolute URL in a request body all leave the
   relay's destination unchanged; changing destination requires a stop and a
   new start, and the old token is refused afterwards.
4. **Unauthorized clients**: a connection with no token, a malformed token, a
   token from a previous bridge instance, and a token belonging to a
   concurrently running second bridge are each refused, and the refusal does
   not reveal the expected value.
5. **Concurrent streams**: 16 simultaneous streamed responses relayed
   byte-for-byte; a slow consumer on one stream does not stall the other 15;
   a stalled reader does not grow the relay's memory without bound.
6. **Relay correctness**: streamed `text/event-stream` chunks arrive
   byte-identical, including a slow producer, a chunk split across frames, and
   a response larger than one buffer.
7. **Disconnects**: the runner closing mid-stream, the model server closing
   mid-stream, the model server never answering, and a half-open connection —
   each closes exactly one stream, with a stated reason, leaving the others
   running.
8. **Process failure and cleanup**: the `wsl.exe` child killed mid-benchmark;
   the companion exiting with the panel open; a crashed bridge followed by a
   clean restart. After each: no listener remains, no relay process remains, no
   token remains, and the ephemeral port is released.
9. **Listener scope**: the Linux listener is bound to `127.0.0.1` and a
   connection attempt from any other address is refused — tested by connecting,
   not by reading the bind call.
10. **Secret containment**: the model server fixture records every header it
    receives and must never see OmoBench's bridge or panel token; a full
    evidence bundle, runner log and diagnostic log produced through a bridge
    contain neither.
11. **Server preservation**: the fixture server is still serving after the
    bridge stops, after a benchmark is cancelled, and after the companion
    exits.
12. **Mirrored mode**: with the destination directly reachable, starting a
    bridge is refused with the "use the address directly" message — decided by
    the positive reachability test of §7, not by configuration.
13. **Overhead**: measured and reported at concurrency 1 and 16, with the
    measurement itself a test artifact rather than a claim in prose.
14. **Failure messages**: nothing listening on the Windows port; the port
    serving something that is not an OpenAI-compatible API; the distribution
    stopped mid-run; the companion exiting while the panel is open.

Each failure message names what was tried, what answered, and what to do next,
in the style the server check already uses.
