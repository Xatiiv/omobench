"""HTTP frontend for the explicitly selected experimental Windows bridge.

One authenticated request per connection. The injected transport opens only the
destination selected by the owner; HTTP input cannot select a transport target.
This changes HTTP framing, but preserves streamed body bytes. It is not an
opaque TCP relay and does not implement the Windows/WSL transport.
"""

import asyncio
from dataclasses import dataclass
import secrets
from urllib.parse import urlsplit

import h11


TOKEN_HEADER = b"x-omobench-bridge-token"
_HOP = {b"connection", b"keep-alive", b"proxy-authenticate",
        b"proxy-authorization", b"te", b"trailer", b"upgrade"}


@dataclass(frozen=True)
class Destination:
    host: str
    port: int

    def __post_init__(self):
        if self.host not in ("127.0.0.1", "::1"):
            raise ValueError("Select an exact Windows loopback destination.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("A destination port from 1 to 65535 is required.")

    @classmethod
    def parse(cls, url):
        try:
            if not isinstance(url, str) or any(ord(c) <= 32 for c in url):
                raise ValueError
            parsed = urlsplit(url)
            if (parsed.scheme != "http" or parsed.username is not None
                    or parsed.password is not None or parsed.query or parsed.fragment
                    or "?" in url or "#" in url or parsed.path not in ("", "/")):
                raise ValueError
            host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
            return cls(host, parsed.port)
        except (ValueError, TypeError):
            # Never echo a supplied URL, which might contain credentials.
            raise ValueError("Select HTTP loopback with an explicit port and no credentials.") from None

    @property
    def authority(self):
        host = "[::1]" if self.host == "::1" else self.host
        return f"{host}:{self.port}".encode("ascii")


@dataclass(frozen=True)
class Limits:
    headers: int = 65536
    body: int = 8 * 1024 * 1024
    clients: int = 32
    header_seconds: float = 5
    connect_seconds: float = 5
    idle_seconds: float = 310
    drain_seconds: float = 5

    def __post_init__(self):
        import math
        for value in vars(self).values():
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Bridge limits must be finite and positive.")


class _Refused(Exception):
    def __init__(self, status):
        self.status = status


def _headers(headers):
    """Remove hop credentials and connection-nominated headers on every message."""
    nominated = set()
    for name, value in headers:
        if name == b"connection":
            nominated.update(part.strip().lower() for part in value.split(b","))
    # Framing fields must not be removed based on a Connection option.
    if nominated & {b"content-length", b"transfer-encoding", b"host"}:
        raise _Refused(400)
    return [(n, v) for n, v in headers if n not in _HOP | nominated | {TOKEN_HEADER}]


class BridgeFrontend:
    """Owner-scoped listener. open_stream() must use a frozen upstream target.

    No diagnostic logging or persistence; lifecycle belongs to BridgeSession. Token access
    is deliberately explicit; repr does not expose it. close() owns only these
    streams, never an upstream model server process.
    """

    def __init__(self, destination, open_stream, *, limits=None):
        if not isinstance(destination, Destination):
            raise TypeError("A validated Destination is required.")
        self.destination = destination
        self._open_stream = open_stream
        self.limits = limits or Limits()
        self._token = secrets.token_urlsafe(32).encode("ascii")
        self._server = None
        self._tasks = set()
        self._closed = False

    @property
    def token(self):
        return self._token

    @property
    def port(self):
        if self._server is None:
            raise RuntimeError("Bridge is not listening.")
        return self._server.sockets[0].getsockname()[1]

    async def start(self):
        if self._closed or self._server is not None:
            raise RuntimeError("Create a new bridge instance for each start.")
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0,
                                                 limit=self.limits.headers)
        return self

    async def close(self):
        self._closed = True
        self._token = b""
        server, self._server = self._server, None
        if server is not None:
            server.close()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Python 3.12 waits for active transports here. Cancel/close our clients
        # first, otherwise stopping can wait for the full upstream idle timeout.
        if server is not None:
            await server.wait_closed()

    def _accept(self, reader, writer):
        # Synchronous admission, before the coroutine can yield. Refused peers
        # allocate no task or upstream connection.
        if self._closed or len(self._tasks) >= self.limits.clients:
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _write(self, writer, data):
        if data:
            writer.write(data)
            await asyncio.wait_for(writer.drain(), self.limits.drain_seconds)

    async def _event(self, conn, reader):
        while True:
            event = conn.next_event()
            if event is not h11.NEED_DATA:
                return event
            data = await asyncio.wait_for(reader.read(65536), self.limits.idle_seconds)
            conn.receive_data(data)

    async def _serve(self, reader, writer):
        upstream = None
        disconnect = None
        response_started = False
        try:
            # Read exactly the first header block. StreamReader's limit plus
            # this explicit length check bounds even fragmented/slow headers.
            block = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),
                                           self.limits.header_seconds)
            if len(block) > self.limits.headers:
                raise _Refused(431)
            client = h11.Connection(h11.SERVER, max_incomplete_event_size=self.limits.headers)
            client.receive_data(block)
            request = client.next_event()
            if not isinstance(request, h11.Request):
                raise _Refused(400)
            tokens = [v for n, v in request.headers if n == TOKEN_HEADER]
            if (len(tokens) != 1 or not self._token
                    or not secrets.compare_digest(tokens[0], self._token)):
                raise _Refused(403)
            if (request.http_version != b"1.1" or request.method not in (b"GET", b"POST")
                    or not request.target.startswith(b"/") or request.target.startswith(b"//")
                    or b"#" in request.target or b"\\" in request.target
                    or any(n == b"upgrade" for n, _ in request.headers)):
                raise _Refused(400)
            if any(n == b"expect" for n, _ in request.headers):
                raise _Refused(417)
            names = {n for n, _ in request.headers}
            if {b"content-length", b"transfer-encoding"} <= names:
                raise _Refused(400)
            if any(n == b"content-length" and int(v) > self.limits.body
                   for n, v in request.headers):
                raise _Refused(413)
            headers = [(n, v) for n, v in _headers(request.headers) if n != b"host"]
            headers += [(b"host", self.destination.authority), (b"connection", b"close")]
            remote = h11.Connection(h11.CLIENT, max_incomplete_event_size=self.limits.headers)
            out_request = remote.send(h11.Request(method=request.method,
                                                  target=request.target, headers=headers))
            upstream_reader, upstream = await asyncio.wait_for(
                self._open_stream(), self.limits.connect_seconds)
            await self._write(upstream, out_request)
            body_size = 0
            while True:
                event = await self._event(client, reader)
                if isinstance(event, h11.EndOfMessage):
                    if event.headers:
                        raise _Refused(400)  # No credentials or headers in trailers.
                    await self._write(upstream, remote.send(h11.EndOfMessage()))
                    break
                if not isinstance(event, h11.Data):
                    raise _Refused(400)
                body_size += len(event.data)
                if body_size > self.limits.body:
                    raise _Refused(413)
                await self._write(upstream, remote.send(h11.Data(data=event.data)))
            # Never enter another h11 cycle. Queued or later pipelined requests
            # cannot reuse the authenticated connection.
            if client.trailing_data[0]:
                raise _Refused(400)
            owner = asyncio.current_task()

            async def watch_disconnect():
                # A second request, EOF, or half-close ends this stream. No
                # additional request bytes can reach the selected server.
                try:
                    await reader.read(1)
                except asyncio.CancelledError:
                    return
                except Exception:
                    pass
                owner.cancel()

            disconnect = asyncio.create_task(watch_disconnect())
            while True:
                event = await self._event(remote, upstream_reader)
                if isinstance(event, h11.InformationalResponse):
                    if event.status_code == 101:
                        raise _Refused(502)
                    continue
                if isinstance(event, h11.Response):
                    if sum(len(n) + len(v) + 4 for n, v in event.headers) > self.limits.headers:
                        raise _Refused(502)
                    headers = _headers(event.headers) + [(b"connection", b"close")]
                    data = client.send(h11.Response(status_code=event.status_code, headers=headers))
                    response_started = True
                elif isinstance(event, h11.Data):
                    data = client.send(h11.Data(data=event.data))
                elif isinstance(event, h11.EndOfMessage):
                    # Do not forward response trailers, including auth headers.
                    await self._write(writer, client.send(h11.EndOfMessage()))
                    break
                else:
                    raise _Refused(502)
                await self._write(writer, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = exc.status if isinstance(exc, _Refused) else 502
            if isinstance(exc, (h11.RemoteProtocolError, asyncio.IncompleteReadError)):
                status = 400 if upstream is None else 502
            if isinstance(exc, asyncio.LimitOverrunError):
                status = 431
            if not response_started:
                try:
                    await self._write(writer, f"HTTP/1.1 {status} Bridge request refused\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii"))
                except Exception:
                    pass
        finally:
            if disconnect is not None:
                disconnect.cancel()
                await asyncio.gather(disconnect, return_exceptions=True)
            for stream in (upstream, writer):
                if stream is not None:
                    stream.close()
            for stream in (upstream, writer):
                if stream is not None:
                    try:
                        await asyncio.wait_for(stream.wait_closed(), self.limits.drain_seconds)
                    except (Exception, asyncio.CancelledError):
                        # abort is scoped to this connection and ensures a slow
                        # peer cannot keep shutdown waiting indefinitely.
                        stream.transport.abort()
