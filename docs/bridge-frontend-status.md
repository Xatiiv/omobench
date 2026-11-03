# Experimental bridge HTTP frontend

This is a tested component, **not an available Windows–WSL bridge**. Nothing in
the panel, companion, installer preparation, CLI execution or runner imports or
starts it. No endpoint restriction has been relaxed. Install `cli[bridge]` only
to develop/test this component; CI installs that extra for its unit suite.

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
destination and be cancellation-safe. Tests use Linux TCP, not Windows stdio.

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

These tests establish frontend behaviour only. They do not establish the framed
Windows stdio transport, NAT/mirrored networking behaviour, Windows lifecycle,
runner header injection, token redaction in evidence, overhead, installation or
community eligibility.

## Remaining integration gates

1. Implement the bounded, multiplexed Windows/WSL transport and cancellation.
2. Integrate token delivery with both runner execution paths without putting it
   in argv, environment snapshots, diagnostics or evidence. GuideLLM's ordinary
   API-key setting is **not** this dedicated header and must not be repurposed.
3. Record transport version, HTTP restrictions and distinct serving/runner host
   provenance in local reports. Keep bridged runs out of public submission until
   its schema and validation represent those facts.
4. Measure overhead with concurrency 1 and 16 on real Windows, test disconnects,
   NAT and mirrored networking, then expose the explicit panel action.

No model inference, benchmark profile change, evidence rewrite or system package
installation is part of this component's verification.
