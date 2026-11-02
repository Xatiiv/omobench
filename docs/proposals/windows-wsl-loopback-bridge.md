# Proposal: reaching a Windows-local model server from the WSL runner

Status: **design only.** No code in this change implements a bridge, and the
panel's loopback rule is unchanged. This document exists so the follow-up can be
reviewed before anything is built.

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
by OmoBench, running inside the distribution, with no listener on any address
but the distribution's own loopback.

### Destination selection

- The contributor types the Windows-side address explicitly:
  `http://127.0.0.1:<port>` **as Windows sees it**. There is no discovery, no
  scanning, no probing of a range, and no reading of other processes' listening
  sockets.
- Only a Windows **loopback** destination is accepted: `127.0.0.1`, `localhost`
  or `[::1]` plus a port. A LAN address, a hostname, a public address or a
  `host.docker.internal`-style alias is refused with the same explanation the
  panel already gives for its own endpoint field.
- The destination is fixed for the lifetime of the bridge. It cannot be changed
  by a request; nothing a server returns can retarget it.

### How the Windows side is reached

The bridge's Windows half must be a process OmoBench starts on Windows (the
companion is the natural owner: it already runs there, already talks to WSL, and
already owns per-user state). It connects to `127.0.0.1:<port>` **on Windows**,
where that is a genuine loopback connection, and relays bytes to the Linux half.
The two halves talk over a channel WSL already provides to the distribution, not
over the LAN:

- Preferred: the companion runs `wsl.exe -d <distribution> -- <relay>` and
  relays over that process's stdin/stdout. No socket is opened on any
  network interface on either side, so there is nothing for another host to
  connect to and nothing for a firewall rule to permit.
- The Linux half listens on `127.0.0.1:<ephemeral port>` **inside the
  distribution only**, binding explicitly to `127.0.0.1` (never `0.0.0.0`).

This is the only part of the design that must not be assumed to work before it
is measured: stdio relaying through `wsl.exe` is bounded by that pipe's
throughput and latency, and §6 treats that as a measurement problem rather than
an assumption.

### Authentication and ownership

- The Linux listener accepts a connection only when it presents a one-time
  token minted by the companion for that bridge instance and passed to the
  panel the same way the panel's own token is — never written to the checkout,
  never logged, never placed in a URL the runner records.
- Because the listener is loopback-only inside a single-user distribution, the
  token is defence in depth rather than the only boundary.
- The bridge is owned by the companion that started it and dies with it. A
  bridge is never shared between panels or reused across checkouts.

### Redirect and destination restrictions

- The relay is a byte relay, not an HTTP client: it does not follow redirects,
  because it does not parse responses. The panel's existing `NoRedirect` handler
  continues to refuse a redirecting server at the HTTP layer.
- One bridge, one destination, one port. No path-based routing, no CONNECT, no
  proxy semantics of any kind.

### Lifecycle

| Event | Behaviour |
| --- | --- |
| Start | Explicit, from the panel, after the contributor enters the Windows port. Never implicit, never on page load. |
| Port | Ephemeral on the Linux side, chosen by the kernel and reported back; the Windows-side port is the contributor's. |
| Reuse | One bridge per (distribution, Windows port). A second request for the same pair reuses the running bridge rather than starting a second. |
| Cancellation | A pending start is cancellable and leaves nothing behind, like the existing preparation stages. |
| Benchmark end | The bridge is **not** torn down by the end of a run; a contributor usually runs several. It is stopped explicitly, when the panel stops, or when the companion exits. |
| The model server | Never touched. The bridge connects to it and relays; stopping the bridge closes sockets and nothing else. The existing rule that cancellation stops only OmoBench's own process group is unchanged. |
| Cleanup | On stop, the Linux listener closes, the relay process exits, and the token is discarded. A crashed bridge leaves a dead listener at worst, which the next start detects and replaces. |

## 3. What this does to the result's provenance

This is the part that needs review most, because it changes what a result means.

Today "the runner host" and "the serving host" are the same machine, and
`hardware` describes it. With a bridge they are still the same *physical*
machine but two different operating-system environments: the runner's hardware
snapshot comes from the distribution, while the server ran on Windows with
whatever GPU driver and runtime Windows has.

A result produced through a bridge must therefore record, at minimum:

- that a bridge was used, and its destination port — never a host address;
- the runner host environment, as today;
- the serving host environment as **declared**, not detected, because OmoBench
  cannot inspect the Windows process from inside the distribution.

Until the schema can express "runner host" and "serving host" separately, a
bridged run should be **local-report only**: viewable and exportable, not
publishable. Publishing a bridged run under today's single-`hardware` schema
would assert that the distribution's view of the machine described the server,
which is exactly the sort of unverified claim the project refuses elsewhere.

## 4. Effect on measurements

A relay adds a hop to every request and every streamed chunk. For a benchmark
whose headline metrics are time to first token and inter-token latency, that is
not negligible and must not be hidden:

- Characterise it before shipping: measure round-trip latency and streaming
  throughput through the bridge against a fixture server, with and without the
  bridge, on the same machine.
- If the added latency is material at the scale of the metrics, say so in the
  result and in the panel, and keep bridged runs out of the public registry
  regardless of §3.
- Never silently subtract an estimate of the overhead from a measurement.

## 5. Compatibility with WSL networking modes

| Mode | Behaviour |
| --- | --- |
| NAT (default) | The bridge is needed and is the supported path. |
| Mirrored (`networkingMode=mirrored`) | `127.0.0.1` is already shared; the panel should detect that the destination is directly reachable and refuse to start a pointless bridge, telling the contributor to use the address directly. |
| WSL1 | Out of scope: the runner requires WSL2. |
| No WSL (Linux or macOS host) | Out of scope: there is one loopback namespace already. |

Detection must be a positive test — attempt a bounded connection to the
destination from inside the distribution — not an inference from configuration
files.

## 6. Test plan

Fixtures only; no real inference, no model, no LAN.

1. **Destination validation**: loopback accepted; LAN, public, hostname,
   credentials-in-URL and non-loopback aliases refused, each with its reason.
2. **Relay correctness**: a fixture HTTP server on the Windows side, streamed
   `text/event-stream` chunks relayed byte-for-byte, including a slow producer.
3. **Authentication**: a connection without the token is refused; a token from a
   previous bridge is refused.
4. **Listener scope**: the Linux listener is bound to `127.0.0.1` and is not
   reachable from any other interface.
5. **Lifecycle**: start, reuse for the same pair, cancellation mid-start,
   explicit stop, companion exit, and a crashed relay followed by a clean
   restart.
6. **Server preservation**: the fixture server is still serving after the bridge
   stops and after a benchmark is cancelled.
7. **Mirrored mode**: with the destination directly reachable, starting a bridge
   is refused with the "use the address directly" message.
8. **Overhead**: measured and reported, with the measurement itself a test
   artifact rather than a claim in prose.
9. **Failure messages**: nothing listening on the Windows port; the port serving
   something that is not an OpenAI-compatible API; the distribution stopped
   mid-run; the companion exiting while the panel is open.

Each failure message names what was tried, what answered, and what to do next,
in the style the server check already uses.

## 7. What is explicitly not proposed

- No LAN scanning, port probing or discovery of any kind.
- No listener on `0.0.0.0` or any non-loopback address, on either side.
- No firewall changes, and no instruction to the contributor to make any.
- No relaxation of the panel's endpoint rules: the panel would still accept only
  a loopback address, which is what the bridge's Linux half provides.
- No general-purpose proxy: one destination, fixed at start, with no routing.
- No automatic publication of bridged runs while the schema cannot distinguish
  runner host from serving host.
