"""Explicit Windows loopback sessions, owned by one check or benchmark."""
import asyncio
import os
from pathlib import Path
import threading

VERSION = "windows-stdio-http1-v1"


def _destination(host, port):
    """Imported here, not at module scope: the HTTP frontend needs h11, and
    `helper_path()` -- which only reads an environment variable and checks a
    file -- is useful in installs that never selected the bridge extra."""
    from .bridge_http import Destination

    return Destination(host, port)


def helper_path():
    path = Path(os.environ.get("OMOBENCH_BRIDGE_HELPER", ""))
    if (not path.is_absolute() or path.name != "OmoBenchCompanion.exe"
            or not path.is_file() or not Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()):
        raise ValueError("Open this panel through the updated Windows companion with WSL interop enabled.")
    return str(path)


def provenance(port):
    _destination("127.0.0.1", port)
    return {"kind": VERSION, "windows_port": port, "runner_host": "WSL2",
            "serving_host": "Windows loopback, hardware not independently verified",
            "protocol": "HTTP/1.1, one request per connection",
            "overhead": "Included in measured latency and throughput, not subtracted",
            # Publishable, but only as the two environments it was: schema 1.1
            # records the serving host separately from the runner, and nothing
            # visible from WSL can supply the serving side.
            "publication": "requires a declared serving environment"}


class BridgeSession:
    def __init__(self, port, *, _helper_command=None):
        self.destination = _destination("127.0.0.1", port)
        self.command = _helper_command or [helper_path(), "--bridge-stdio-v1", str(port)]
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.process = self.mux = self.frontend = None

    async def _start(self):
        from .bridge_http import BridgeFrontend
        from .bridge_transport import Multiplexer

        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            self.mux = await Multiplexer(self.process.stdout, self.process.stdin).start()
            self.frontend = await BridgeFrontend(self.destination, self.mux.open_stream).start()
        except BaseException:
            await self._close()
            raise

    async def _close(self):
        if self.frontend:
            await self.frontend.close()
        if self.mux:
            await self.mux.close()
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()  # EOF is the normal Windows shutdown signal.
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()  # Only this owned helper, never the model server.
                await self.process.wait()

    def __enter__(self):
        self.thread.start()
        future = asyncio.run_coroutine_threadsafe(self._start(), self.loop)
        try:
            future.result(timeout=12)
        except BaseException:
            future.cancel()
            self.__exit__(None, None, None)
            raise ValueError("Could not start the Windows bridge. Check the updated companion and WSL interop.") from None
        return self

    def __exit__(self, *exc):
        try:
            asyncio.run_coroutine_threadsafe(self._close(), self.loop).result(timeout=15)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            if not self.thread.is_alive():
                self.loop.close()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.frontend.port}"

    @property
    def credentials(self):
        return {"endpoint": self.endpoint, "token": self.frontend.token.decode("ascii")}
