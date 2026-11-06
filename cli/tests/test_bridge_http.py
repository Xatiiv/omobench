"""Real TCP fixtures for the disabled bridge frontend; no Windows/model claims."""
import asyncio
import unittest

try:
    from omobench.bridge_http import BridgeFrontend, Destination, Limits, TOKEN_HEADER
except ModuleNotFoundError as exc:
    if exc.name != "h11":
        raise
    BridgeFrontend = None


@unittest.skipUnless(BridgeFrontend, "Install cli[bridge] to test the experimental frontend")
class DestinationTests(unittest.TestCase):
    def test_only_explicit_loopback_destinations(self):
        for url in ("http://127.0.0.1:1234", "http://localhost:1234/", "http://[::1]:1234"):
            with self.subTest(url=url):
                self.assertEqual(Destination.parse(url).port, 1234)
        for url in ("https://localhost:1234", "http://localhost", "http://localhost:0",
                    "http://10.0.0.1:1234", "http://127.1:1234", "http://example.com:1234",
                    "http://user:SECRET@localhost:1234", "http://localhost:1234/path",
                    "http://localhost:1234?", "http://localhost:1234#", "http://local\nhost:1234"):
            with self.subTest(url=url), self.assertRaises(ValueError) as caught:
                Destination.parse(url)
            self.assertNotIn("SECRET", str(caught.exception))

    def test_destination_is_frozen_and_direct_construction_validated(self):
        with self.assertRaises(ValueError):
            Destination("0.0.0.0", 1234)
        with self.assertRaises(AttributeError):
            Destination("127.0.0.1", 1234).port = 80
        for value in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                Limits(idle_seconds=value)


@unittest.skipUnless(BridgeFrontend, "Install cli[bridge] to test the experimental frontend")
class FrontendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.arrivals = []
        self.connections = 0
        self.writers = set()
        self.fixture_tasks = set()
        self.loop_errors = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: self.loop_errors.append(context))
        self.entered = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.flooding = asyncio.Event()
        self.response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
        self.server = await asyncio.start_server(self.fixture, "127.0.0.1", 0)
        self.server_port = self.server.sockets[0].getsockname()[1]
        self.bridge = await BridgeFrontend(
            Destination("127.0.0.1", self.server_port), self.open_stream,
            limits=Limits(header_seconds=.5, idle_seconds=2, drain_seconds=.5)).start()

    async def asyncTearDown(self):
        await self.bridge.close()
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.writers):
            writer.close()
        for task in list(self.fixture_tasks):
            task.cancel()
        await asyncio.gather(*self.fixture_tasks, return_exceptions=True)
        self.assertEqual(self.loop_errors, [], "Unhandled fixture or bridge task failure")

    async def open_stream(self):
        self.connections += 1
        return await asyncio.open_connection("127.0.0.1", self.server_port)

    async def fixture(self, reader, writer):
        task = asyncio.current_task()
        self.fixture_tasks.add(task)
        self.writers.add(writer)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            if b"transfer-encoding: chunked" in headers.lower():
                body = b""
                while True:
                    line = await reader.readline()
                    if not line:
                        return  # Bridge rejected/cancelled the incomplete body.
                    size = int(line.strip(), 16)
                    if not size:
                        await reader.readline()
                        break
                    body += await reader.readexactly(size)
                    await reader.readexactly(2)
            else:
                length = next((int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                               if line.lower().startswith(b"content-length:")), 0)
                body = await reader.readexactly(length)
            self.arrivals.append((headers, body))
            if b" /flood " in headers:
                writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                self.flooding.set()
                for _ in range(1024):
                    writer.write(b"10000\r\n" + b"a" * 65536 + b"\r\n")
                    await writer.drain()
                return
            if b" /wait " in headers:
                self.entered.set()
                await reader.read()
                self.disconnected.set()
                return
            for chunk in self.response.split(b"|SPLIT|"):
                writer.write(chunk)
                await writer.drain()
                await asyncio.sleep(0)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.writers.discard(writer)
            self.fixture_tasks.discard(task)

    def request(self, *, token=True, extra=b"", path=b"/v1/models", method=b"GET", body=b""):
        auth = TOKEN_HEADER + b": " + self.bridge.token + b"\r\n" if token else b""
        return (method + b" " + path + b" HTTP/1.1\r\nHost: ignored.example\r\n" + auth
                + extra + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)

    async def exchange(self, request, *, fragmented=False):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.bridge.port)
        try:
            if fragmented:
                for byte in request:
                    writer.write(bytes([byte]))
                    await writer.drain()
                    await asyncio.sleep(0)
            else:
                writer.write(request)
                await writer.drain()
            return await asyncio.wait_for(reader.read(), 4)
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_authentication_refuses_before_opening_transport(self):
        other = BridgeFrontend(Destination("127.0.0.1", 1), self.open_stream)
        variants = [self.request(token=False),
                    self.request(token=False, extra=TOKEN_HEADER + b": wrong\r\n"),
                    self.request(extra=TOKEN_HEADER + b": " + self.bridge.token + b"\r\n"),
                    self.request(token=False, extra=TOKEN_HEADER + b": " + other.token + b"\r\n")]
        for request in variants:
            self.assertIn(b"403", await self.exchange(request))
        self.assertEqual(self.connections, 0)
        self.assertEqual(self.arrivals, [])

    async def test_fragmented_post_strips_secret_preserves_body_and_server_auth(self):
        body = b'{"messages":[{"content":"synthetic fixture"}]}'
        response = await self.exchange(self.request(method=b"POST", body=body,
            extra=b"Authorization: Bearer contributor-fixture\r\n"), fragmented=True)
        self.assertIn(b"200", response)
        headers, forwarded = self.arrivals[0]
        self.assertEqual(forwarded, body)
        self.assertNotIn(self.bridge.token, headers)
        self.assertNotIn(TOKEN_HEADER, headers.lower())
        self.assertIn(b"Authorization: Bearer contributor-fixture".lower(), headers.lower())
        self.assertIn(b"host: 127.0.0.1:" + str(self.server_port).encode(), headers.lower())
        self.assertNotIn(b"ignored.example", headers)
        self.assertNotIn(self.bridge.token.decode(), repr(self.bridge))

    async def test_protocols_and_framing_refused_before_transport(self):
        variants = [self.request(method=b"CONNECT"), self.request(path=b"http://evil.test/a"),
                    self.request(path=b"//evil.test/a"), self.request(extra=b"Upgrade: websocket\r\n"),
                    self.request(extra=b"Expect: 100-continue\r\n"),
                    self.request(extra=b"Transfer-Encoding: chunked\r\n"),
                    self.request(extra=b"Content-Length: 100\r\n"),
                    self.request(extra=b"Connection: content-length\r\n"),
                    self.request().replace(b"HTTP/1.1", b"HTTP/1.0"),
                    b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"]
        for raw in variants:
            with self.subTest(raw=raw.split(b"\r\n")[0]):
                response = await self.exchange(raw)
                self.assertNotIn(b"200", response)
        self.assertEqual(self.connections, 0)

    async def test_pipelining_never_forwards_second_request(self):
        await self.exchange(self.request(path=b"/first") + self.request(path=b"/second", token=False))
        self.assertEqual(len(self.arrivals), 1)
        self.assertIn(b" /first ", self.arrivals[0][0])

    async def test_chunked_sse_preserves_payload_bytes(self):
        payload = b'data: {"token":"hello"}\n\ndata: [DONE]\n\n'
        self.response = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: text/event-stream\r\n\r\n"
                         + f"{len(payload):x}\r\n".encode() + payload[:10] + b"|SPLIT|"
                         + payload[10:] + b"\r\n0\r\n\r\n")
        import h11
        parser = h11.Connection(h11.CLIENT)
        parser.send(h11.Request(method="GET", target="/", headers=[("Host", "localhost")]))
        parser.send(h11.EndOfMessage())
        parser.receive_data(await self.exchange(self.request()))
        data = b""
        while True:
            event = parser.next_event()
            if isinstance(event, h11.EndOfMessage):
                break
            if isinstance(event, h11.Data):
                data += event.data
        self.assertEqual(data, payload)

    async def test_sixteen_streams_independent_of_waiting_stream(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.bridge.port)
        writer.write(self.request(path=b"/wait"))
        await writer.drain()
        await asyncio.wait_for(self.entered.wait(), 2)
        responses = await asyncio.gather(*(self.exchange(self.request(path=f"/{i}".encode())) for i in range(16)))
        self.assertTrue(all(b"200" in r for r in responses))
        self.assertEqual(len(self.arrivals), 17)
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(self.disconnected.wait(), 2)
        await reader.read()

    async def test_stop_closes_owned_streams_and_keeps_server_alive(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.bridge.port)
        writer.write(self.request(path=b"/wait"))
        await writer.drain()
        await asyncio.wait_for(self.entered.wait(), 2)
        old_token, old_port = self.bridge.token, self.bridge.port
        await self.bridge.close()
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
        await asyncio.wait_for(self.disconnected.wait(), 2)
        self.assertEqual(self.bridge.token, b"")
        self.assertFalse(self.bridge._tasks)
        with self.assertRaises(OSError):
            await asyncio.open_connection("127.0.0.1", old_port)
        writer.close()
        await writer.wait_closed()
        self.bridge = await BridgeFrontend(Destination("127.0.0.1", self.server_port), self.open_stream).start()
        self.assertIn(b"403", await self.exchange(self.request(token=False, extra=TOKEN_HEADER + b": " + old_token + b"\r\n")))
        self.assertIn(b"200", await self.exchange(self.request()))

    async def test_redirect_is_returned_without_opening_another_destination(self):
        self.response = b"HTTP/1.1 307 Temporary Redirect\r\nLocation: http://unselected.invalid/\r\nContent-Length: 0\r\n\r\n"
        self.assertIn(b"307", await self.exchange(self.request()))
        self.assertEqual(self.connections, 1)

    async def test_upstream_error_does_not_echo_secrets(self):
        async def fail():
            raise OSError("SECRET connection diagnostic")
        self.bridge._open_stream = fail
        response = await self.exchange(self.request())
        self.assertIn(b"502", response)
        self.assertNotIn(b"SECRET", response)

    async def test_header_and_body_limits(self):
        self.bridge.limits = Limits(headers=256, body=4)
        self.assertIn(b"431", await self.exchange(self.request(extra=b"Long: " + b"a" * 300 + b"\r\n")))
        self.assertIn(b"413", await self.exchange(self.request(method=b"POST", body=b"12345")))
        self.assertEqual(self.connections, 0)

    async def test_listener_is_ipv4_loopback_only(self):
        self.assertEqual(self.bridge._server.sockets[0].getsockname()[0], "127.0.0.1")

    async def test_slow_consumer_does_not_block_other_streams(self):
        # A real socket consumer that stops reading, not a sleeping upstream.
        reader, writer = await asyncio.open_connection("127.0.0.1", self.bridge.port, limit=1024)
        writer.write(self.request(path=b"/flood"))
        await writer.drain()
        await asyncio.wait_for(self.flooding.wait(), 2)
        replies = await asyncio.gather(*(self.exchange(self.request()) for _ in range(16)))
        self.assertTrue(all(b"200" in reply for reply in replies))
        self.assertEqual(len(self.arrivals), 17)
        writer.transport.abort()
        await asyncio.wait_for(self.bridge.close(), 2)
        self.assertFalse(self.bridge._tasks)

    async def test_stop_cancels_pending_transport_open(self):
        opening, cancelled = asyncio.Event(), asyncio.Event()
        async def blocked():
            opening.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        self.bridge._open_stream = blocked
        reader, writer = await asyncio.open_connection("127.0.0.1", self.bridge.port)
        writer.write(self.request())
        await writer.drain()
        await asyncio.wait_for(opening.wait(), 2)
        await asyncio.wait_for(self.bridge.close(), 2)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(await reader.read(), b"")
        writer.close()
        await writer.wait_closed()

    async def test_client_limit_refuses_without_opening_transport(self):
        self.bridge.limits = Limits(clients=1)
        reader, writer = await asyncio.open_connection("127.0.0.1", self.bridge.port)
        writer.write(self.request(path=b"/wait"))
        await writer.drain()
        await asyncio.wait_for(self.entered.wait(), 2)
        second_reader, second_writer = await asyncio.open_connection("127.0.0.1", self.bridge.port)
        self.assertEqual(await asyncio.wait_for(second_reader.read(), 2), b"")
        self.assertEqual(self.connections, 1)
        second_writer.close()
        await second_writer.wait_closed()
        writer.close()
        await writer.wait_closed()
        await reader.read()

    async def test_partial_headers_have_a_total_deadline(self):
        self.bridge.limits = Limits(header_seconds=.05)
        reader, writer = await asyncio.open_connection("127.0.0.1", self.bridge.port)
        writer.write(b"GET / HTTP/1.1\r\n")
        await writer.drain()
        self.assertIn(b"502", await asyncio.wait_for(reader.read(), 1))
        self.assertEqual(self.connections, 0)
        writer.close()
        await writer.wait_closed()

    async def test_chunked_request_and_forbidden_trailers(self):
        header = self.request(method=b"POST").replace(b"Content-Length: 0", b"Transfer-Encoding: chunked")
        self.assertIn(b"200", await self.exchange(header + b"4\r\ndata\r\n0\r\n\r\n"))
        self.assertEqual(self.arrivals[0][1], b"data")
        response = await self.exchange(header + b"4\r\ndata\r\n0\r\n" + TOKEN_HEADER + b": secret\r\n\r\n")
        self.assertIn(b"400", response)
        # The fixture cannot complete the rejected request: no end marker or
        # trailer was sent. Previously completed evidence is unchanged.
        self.assertEqual(len(self.arrivals), 1)


if __name__ == "__main__":
    unittest.main()
