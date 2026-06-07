"""Minimal HTTP server for Replit PR previews.

Replit autoscale deployments need an HTTP entrypoint that binds 0.0.0.0 and
listens on the port given by the PORT environment variable. The Intern is a CLI
agent with no web UI, so this server exists purely to give each PR preview a
viewable status page and a health check that lets the deployment go live.
"""

from __future__ import annotations

import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from intern_bot import __version__

HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def _build_info() -> dict[str, str]:
    """Best-effort deploy metadata surfaced by Replit / the preview workflow."""
    return {
        "version": __version__,
        "commit": os.environ.get("REPL_SLUG_COMMIT", os.environ.get("GIT_COMMIT", "unknown")),
        "branch": os.environ.get("REPL_SLUG", os.environ.get("GIT_BRANCH", "replit-preview")),
        "repl": os.environ.get("REPL_SLUG", "unknown"),
    }


def _status_page(info: dict[str, str]) -> bytes:
    rows = "\n".join(
        f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>"
        for k, v in info.items()
    )
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>The Intern - PR Preview</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      max-width: 40rem; margin: 4rem auto; padding: 0 1.5rem; line-height: 1.5;
    }}
    h1 {{ font-size: 1.6rem; margin-bottom: 0.25rem; }}
    .tag {{ display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
            background: #2da44e; color: #fff; font-size: 0.8rem; font-weight: 600; }}
    table {{ border-collapse: collapse; margin-top: 1.5rem; width: 100%; }}
    th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #8884; }}
    th {{ width: 8rem; opacity: 0.7; font-weight: 600; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  </style>
</head>
<body>
  <h1>The Intern <span class="tag">PR Preview</span></h1>
  <p>This is a shared Replit preview of a pull request build. The latest deploy wins.</p>
  <table>
    {rows}
  </table>
  <p style="margin-top:2rem;opacity:0.6;font-size:0.85rem">
    Health check: <code>/healthz</code>
  </p>
</body>
</html>
"""
    return page.encode("utf-8")


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "InternPreview"

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path.rstrip("/") in ("/healthz", "/health"):
            body = json.dumps({"status": "ok", **_build_info()}).encode("utf-8")
            self._respond(200, body, "application/json")
            return
        self._respond(200, _status_page(_build_info()), "text/html; charset=utf-8")

    do_HEAD = do_GET

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        # Quieter logs; prefix so Replit deployment logs are scannable.
        print("preview %s - %s" % (self.address_string(), fmt % args))


def main() -> None:
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    server = ThreadingHTTPServer((HOST, port), PreviewHandler)
    print(f"Intern preview server listening on {HOST}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
