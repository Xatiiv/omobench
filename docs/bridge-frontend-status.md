# Experimental bridge HTTP frontend

The experimental bridge is now connected to the advanced panel and both runner
execution paths. It is explicit and off by default. It is not a published
installer update, and native helper tests do not establish real Windows–WSL
interop or overhead. Bridged measurements are publishable under schema 1.1, which
records the Windows serving host and the WSL runner separately and marks the
bridge's latency as included in every number; see [provenance.md](provenance.md).

## Use in an updated installation

Start the panel through the newly built Windows companion. In **Connect & run**,
select **My model server runs on Windows**, enter the exact Windows IPv4 loopback
address and port (for example `http://127.0.0.1:1234`), and use **Check this server**.
The companion supplies its own executable path to WSL; the browser cannot select
an executable. Keep LM Studio listening on loopback. No firewall or LAN binding
change is required. A missing/old companion or disabled WSL interop is an error,
never a fallback to the WSL server on the same port.

The checkbox is a deliberate experimental opt-in. Windows IPv6, HTTPS, and servers
requiring an additional API-key configuration are not exposed by this first UI.
A separately configured same-port Linux server is never selected by discovery.
Mirrored networking does not change the explicitly chosen Windows destination;
there is no heuristic that switches it to a possibly unrelated Linux listener.

## Protocol decision

`cli/src/omobench/bridge_http.py` implements an HTTP-aware Linux frontend with
h11 0.16.0. It accepts one GET or POST per HTTP/1.1 connection. It parses framing,
strips `X-OmoBench-Bridge-Token` before forwarding, rewrites Host to the frozen
destination, and preserves contributor Authorization and body/SSE payload bytes.
It does **not** preserve HTTP wire framing, connection reuse, or timing. Those
differences require measurement and disclosure before benchmark use.

The frontend generates a fresh in-memory secret per instance. Exactly one valid
token header is required before opening the transport. Tokens from another or
stopped instance fail. There is no token persistence, logging or CLI argument.
This is scoped access control for the local listener, not isolation from an
attacker who already controls the same OS account or can read process memory.

The listener binds only IPv4 `127.0.0.1` on a kernel-selected port. The validated
destination is an explicit HTTP port on Windows `127.0.0.1` or `::1` (literal
`localhost` is deterministically mapped to IPv4 loopback). An injected, trusted
`open_stream()` implementation supplies each upstream stream; it receives no
request-derived address. That callback must be constructed for the same frozen
destination and be cancellation-safe. Production uses the Windows helper over WSL executable interop stdio. Linux tests use a synthetic peer; native Windows CI selects the compiled companion executable.

Unsupported: HTTPS, HTTP/2, CONNECT, WebSocket/Upgrade, absolute-form targets,
Expect/100-continue, request trailers, keepalive, pipelining and half-closed
clients. They are refused or disconnected; no automatic fallback occurs.
Only the first request can reach the transport. An incomplete request may have
forwarded part of its body before cancellation or a framing/size error; it is
never replayed. Request upload finishes before response reading begins.
Response trailers are discarded. Redirect responses are returned without
opening their destination; the runner must continue to set
`follow_redirects=false`, since a redirect-following client could still escape.

## Resource and lifecycle boundaries

- 32 admitted clients, 64 KiB header limit, 8 MiB request-body limit.
- 64 KiB read chunks with awaited writer drainage, no application stream queue.
- 5-second total request-header deadline, 5-second transport-open deadline,
  310-second per-read idle deadline and 5-second drainage deadline.
- An excess connection is closed without a task or upstream connection.
- Disconnect cancels that connection; stopping closes the listener, cancels
  admitted tasks, closes their streams, then waits for listener cleanup.
- The upstream server process is never owned or stopped. Failures return static
  text or close an already-started response, never exception strings or secrets.

## Verification boundaries

`test_bridge_http.py` drives real isolated TCP sockets: missing/duplicate/wrong
authentication, secret stripping, contributor credentials, fragmented POST,
chunked request and SSE bodies, forbidden framing/protocols, pipelining, header
and body limits, slow headers, admission limit, 16 parallel responses while a
separate stream waits or its consumer stops reading, cancellation during connect
and response, stopping/restarting with token rotation, fixed destination and
redirection, and upstream errors. It also fails on unhandled event-loop errors.

The shutdown test exposed a real Python 3.12 ordering issue: waiting for the
server to close before cancelling clients waited for upstream timeout. Clients
are now cancelled and closed before awaiting server shutdown.

## Transport and runner integration

`bridge_transport.py` multiplexes up to 32 streams over one private pipe pair.
The companion's `--bridge-stdio-v1 <port>` mode never opens a listener or reads
configuration. It connects only to literal Windows IPv4 loopback at the selected
port. The mode is not reachable through an `omobench://` URI. Protocol version 1
starts with `OMB1`, then 9-byte network-order frames (kind, stream id, length),
maximum 16 KiB payload, and 64 KiB credit per stream in each direction. IDs are
monotonic and never reused. Invalid framing/credit closes the owned transport.
The Windows inbound queue is a fixed 64 KiB byte ring per stream.

One session belongs to one explicit server check or run. A run creates its own
session, so restarting or changing port cannot reuse an earlier secret. EOF
closes the helper's connections; explicit cleanup waits five seconds before
terminating only that owned helper if it has not exited. No model-server process
is signalled. All helper errors are static, not raw exception text.

Both CLI execution routes use the same pinned GuideLLM backend subclass. It
adds the bridge header only for the exact frontend origin, disables ambient
proxies, HTTP/2, keepalive and redirects. The ordinary backend arguments and
serialized info contain no secret. Telemetry keeps credentials in private
runtime state; the plain subprocess receives them through stdin. Worker
serialization transfers private state to the owned workers. No token is put in
argv, environment variables, persistent files, reports or reproduction commands.

`environment.json`, the saved job and local report record the transport version,
Windows destination port, runner host (WSL2), serving host (Windows, hardware
unverified), protocol and included overhead. The dashboard labels these runs.
The panel, CLI packager and registry validator refuse their public submission.
This does not change any profile, measured window or acceptance threshold.

## Tests and remaining platform checks

- Frontend tests exercise authenticated HTTP framing and bounded streams.
- Transport integration tests exercise actual subprocess stdio, 16 simultaneous
  streams, helper failure, port refusal and cleanup. On Linux the peer is a
  clearly synthetic Python fixture. On Windows CI it is the compiled production
  companion executable. Neither test opens a WSL distribution.
- Runner fixtures use the real pinned runner, generated tokenizer and local SSE
  server through both plain and telemetry routes, retaining the production v2
  profile and asserting 60 requests / 50 measured. They scan every generated
  artifact and captured log for the bridge token. No model is downloaded or run.
- Publication tests retain local export and refuse packaging/submission.

Before claiming general Windows support, test the new companion with real WSL
interop under NAT and mirrored modes, test panel/companion termination, and
measure added latency and throughput at concurrency 1 and 16 on Windows–WSL.
The bridge does not subtract its overhead or claim timing equivalence to a direct
connection. Those observations cannot be supplied by Linux or Windows-only CI.
A clean-machine installer test and a genuine community submission remain separate
work, not outcomes of this bridge implementation.
