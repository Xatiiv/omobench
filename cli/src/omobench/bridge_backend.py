"""GuideLLM 0.7.3 adapter; only private runtime state carries authentication."""
from urllib.parse import urlsplit

from guidellm.backends.openai.http import OpenAIHTTPBackend


class BridgeBackend(OpenAIHTTPBackend):
    def __init__(self, arguments):
        super().__init__(arguments)
        from .bridge_runner import _credentials
        if (_credentials is None or arguments.target.rstrip("/") != _credentials["endpoint"]
                or arguments.http2 or arguments.follow_redirects):
            raise ValueError("Bridge runner destination does not match its private session.")
        self._bridge_endpoint = _credentials["endpoint"]
        self._bridge_token = _credentials["token"]

    async def process_startup(self):
        await super().process_startup()
        # Replace the parent's default client so environment proxy settings
        # cannot route local bridge credentials through a proxy. Args/info
        # contain no credential, including in raw reports and console output.
        import httpx
        await self._async_client.aclose()
        self._async_client = httpx.AsyncClient(
            http2=False, follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(300, read=self._args.timeout, connect=self._args.timeout_connect),
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=0),
            event_hooks={"request": [self._authenticate]})

    async def _authenticate(self, request):
        target = urlsplit(self._bridge_endpoint)
        if (request.url.scheme != "http" or request.url.host != target.hostname
                or request.url.port != target.port):
            raise ValueError("Bridge request attempted to leave its selected destination.")
        request.headers["X-OmoBench-Bridge-Token"] = self._bridge_token
