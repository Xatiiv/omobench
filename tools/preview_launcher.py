"""Serve or export the read-only launcher design; never starts a benchmark."""

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
import webbrowser

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli" / "src"))
from omobench.cli import PROFILE_SPECS  # noqa: E402


def render_preview(root=ROOT):
    models = []
    for path in sorted((root / "catalog" / "models").glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        models.extend({"id": model["id"], "parameter_class": model.get("parameter_class"), "licence": model["licence"]}
                      for model in document["models"] if model.get("modality") == "text-generation")
    data = json.dumps({"models": models, "profiles": PROFILE_SPECS}).replace("<", "\\u003c")
    source = root / "design" / "launcher"
    html = (source / "index.html").read_text(encoding="utf-8")
    return (html.replace("/* PREVIEW_STYLE */", (source / "style.css").read_text(encoding="utf-8"))
            .replace("/* PREVIEW_SCRIPT */", (source / "app.js").read_text(encoding="utf-8"))
            .replace("PREVIEW_DATA", data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, help="write a standalone HTML preview instead of serving")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    html = render_preview()
    if args.export:
        args.export.write_text(html, encoding="utf-8")
        print(f"Wrote interactive design preview: {args.export}")
        return
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port between 1024 and 65535.")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            content = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        address = f"http://127.0.0.1:{args.port}/"
        print(f"OmoBench design preview: {address}\nNo downloads, benchmark execution, or submission APIs. Ctrl+C stops the preview.", flush=True)
        if not args.no_browser:
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
