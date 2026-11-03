"""Linux-only synthetic peer for protocol tests. Windows CI uses the real EXE."""
import asyncio
import struct
import sys

from omobench.bridge_transport import HEADER, CHUNK, WINDOW, OPEN, READY, DATA, CREDIT, CLOSE


async def main(port):
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout.buffer)
    writer = asyncio.StreamWriter(transport, protocol, None, loop)
    writer.write(b"OMB1")
    lock, streams, tasks = asyncio.Lock(), {}, set()

    async def send(kind, identifier, payload=b""):
        async with lock:
            writer.write(HEADER.pack(kind, identifier, len(payload)) + payload)
            await writer.drain()

    async def connection(identifier):
        remote = None
        try:
            source, remote = await asyncio.open_connection("127.0.0.1", port)
            stream = [remote, WINDOW, asyncio.Event()]
            streams[identifier] = stream
            await send(READY, identifier)
            while True:
                while not stream[1]:
                    stream[2].clear()
                    await stream[2].wait()
                data = await source.read(min(CHUNK, stream[1]))
                if not data:
                    break
                stream[1] -= len(data)
                await send(DATA, identifier, data)
        except (ConnectionError, OSError):
            pass
        finally:
            streams.pop(identifier, None)
            if remote:
                remote.close()
            await send(CLOSE, identifier)

    try:
        while True:
            kind, identifier, length = HEADER.unpack(await reader.readexactly(9))
            if length > CHUNK:
                return
            payload = await reader.readexactly(length)
            if kind == OPEN:
                task = asyncio.create_task(connection(identifier))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif identifier in streams:
                stream = streams[identifier]
                if kind == DATA:
                    stream[0].write(payload)
                    await stream[0].drain()
                    await send(CREDIT, identifier, struct.pack("!I", length))
                elif kind == CREDIT:
                    stream[1] += struct.unpack("!I", payload)[0]
                    stream[2].set()
                elif kind == CLOSE:
                    stream[0].close()
    except asyncio.IncompleteReadError:
        pass
    finally:
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
