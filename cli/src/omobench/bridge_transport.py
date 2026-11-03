"""Version 1 credit-controlled streams over a private helper's stdio pipes."""
import asyncio
import struct

MAGIC = b"OMB1"
OPEN, READY, DATA, CREDIT, CLOSE = range(1, 6)
CHUNK, WINDOW, MAX_STREAMS = 16384, 65536, 32
HEADER = struct.Struct("!BII")


class Stream:
    def __init__(self, mux, identifier):
        self.mux, self.identifier = mux, identifier
        self.reader = asyncio.StreamReader(limit=WINDOW)
        self.ready = asyncio.get_running_loop().create_future()
        self.credit = WINDOW
        self.incoming = WINDOW
        self.changed = asyncio.Event()
        self.pending = bytearray()
        self.closed = False
        self.transport = self

    async def read(self, size=-1):
        data = await self.reader.read(min(size if size > 0 else CHUNK, CHUNK))
        if data and not self.closed:
            self.incoming += len(data)
            await self.mux.send(CREDIT, self.identifier, struct.pack("!I", len(data)))
        return data

    def write(self, data):
        if self.closed or len(self.pending) + len(data) > WINDOW:
            raise ConnectionError("Bridge stream closed or buffer limit exceeded.")
        self.pending.extend(data)

    async def drain(self):
        while self.pending:
            if self.closed:
                raise ConnectionError("Bridge stream closed.")
            if not self.credit:
                self.changed.clear()
                await asyncio.wait_for(self.changed.wait(), 5)
                continue
            count = min(CHUNK, self.credit, len(self.pending))
            self.credit -= count
            data = bytes(self.pending[:count])
            del self.pending[:count]
            await self.mux.send(DATA, self.identifier, data)

    def close(self):
        if not self.closed:
            self.finish()
            self.mux.queue_close(self.identifier)

    abort = close

    async def wait_closed(self):
        return

    def finish(self):
        self.closed = True
        self.pending.clear()
        self.reader.feed_eof()
        self.changed.set()
        if not self.ready.done():
            self.ready.set_result(False)
        self.mux.streams.pop(self.identifier, None)


class Multiplexer:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.streams = {}
        self.next_id = 0
        self.lock = asyncio.Lock()
        self.task = None
        self.closed = False
        self.closers = set()

    async def start(self):
        if await asyncio.wait_for(self.reader.readexactly(4), 5) != MAGIC:
            raise ConnectionError("Unsupported Windows bridge helper.")
        self.task = asyncio.create_task(self.receive())
        return self

    async def send(self, kind, identifier, data=b""):
        if self.closed or len(data) > CHUNK:
            raise ConnectionError("Windows bridge transport is unavailable.")
        async with self.lock:
            self.writer.write(HEADER.pack(kind, identifier, len(data)) + data)
            await asyncio.wait_for(self.writer.drain(), 5)

    def queue_close(self, identifier):
        async def notify():
            try:
                await self.send(CLOSE, identifier)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                pass
        task = asyncio.create_task(notify())
        self.closers.add(task)
        task.add_done_callback(self.closers.discard)

    async def open_stream(self):
        if self.closed or len(self.streams) >= MAX_STREAMS or self.next_id >= 0xffffffff:
            raise ConnectionError("Windows bridge stream limit reached.")
        self.next_id += 1
        stream = Stream(self, self.next_id)
        self.streams[stream.identifier] = stream
        try:
            await self.send(OPEN, stream.identifier)
            if not await asyncio.wait_for(stream.ready, 5):
                raise ConnectionError("The selected Windows server could not be reached.")
            return stream, stream
        except BaseException:
            stream.close()
            raise

    async def receive(self):
        try:
            while True:
                kind, identifier, length = HEADER.unpack(await self.reader.readexactly(HEADER.size))
                if length > CHUNK or kind not in (READY, DATA, CREDIT, CLOSE) or not 0 < identifier <= self.next_id:
                    raise ConnectionError("Invalid bridge frame.")
                data = await self.reader.readexactly(length)
                stream = self.streams.get(identifier)
                if stream is None:
                    continue  # In-flight frame for an already cancelled id, never reused.
                if kind == READY and length == 0 and not stream.ready.done():
                    stream.ready.set_result(True)
                elif kind == DATA and 0 < length <= stream.incoming and stream.ready.done():
                    stream.incoming -= length
                    stream.reader.feed_data(data)
                elif kind == CREDIT and length == 4:
                    amount = struct.unpack("!I", data)[0]
                    if not 0 < amount <= WINDOW - stream.credit:
                        raise ConnectionError("Invalid bridge credit.")
                    stream.credit += amount
                    stream.changed.set()
                elif kind == CLOSE and length == 0:
                    stream.finish()
                else:
                    raise ConnectionError("Invalid bridge stream state.")
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self.closed = True
            for stream in list(self.streams.values()):
                stream.finish()
            self.writer.close()

    async def close(self):
        self.closed = True
        for stream in list(self.streams.values()):
            stream.finish()
        for task in list(self.closers):
            task.cancel()
        await asyncio.gather(*self.closers, return_exceptions=True)
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.writer.close()
