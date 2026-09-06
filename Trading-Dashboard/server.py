"""
Trading Dashboard backend.

Zero third-party dependencies on purpose (stdlib http.server only), so it
runs anywhere Python 3 runs. See README.md for how to start it and how to
swap the simulated data engine for a real market-data / SEC EDGAR feed.
"""
import json
import os
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from data_engine import get_market, reset_market_cache
from providers import YahooFinanceProvider, SecEdgarProvider, CACHE_DIR

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "TradingDashboard/1.0"

    def log_message(self, fmt, *args):
        pass  # keep stdout quiet; flip on for debugging

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, status=200):
        ext = os.path.splitext(path)[1]
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, url_path):
        if url_path == "/":
            url_path = "/index.html"
        safe_path = os.path.normpath(url_path).lstrip("/")
        full_path = os.path.join(STATIC_DIR, safe_path)
        if not full_path.startswith(STATIC_DIR) or not os.path.isfile(full_path):
            self._send_json({"error": "not found"}, 404)
            return
        self._send_file(full_path)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/meta":
                market = get_market()
                sources = market.source_summary()
                self._send_json({
                    "as_of": market.end_date.strftime("%Y-%m-%d"),
                    "universe_size": len(market.meta),
                    "sectors": sorted(set(m["sector"] for m in market.meta.values())),
                    "sources": sources,
                    "disclaimer": (
                        "Priser: Yahoo Finance når muligt, ellers simuleret. "
                        "13F: SEC EDGAR når muligt, ellers simuleret. "
                        "Se badge og kilde-indikator for hver række."
                    ),
                })
            elif path == "/api/refresh":
                query = parse_qs(parsed.query)
                if query.get("force", ["0"])[0] == "1":
                    shutil.rmtree(CACHE_DIR, ignore_errors=True)
                YahooFinanceProvider._available = None
                SecEdgarProvider._available = None
                reset_market_cache()
                market = get_market()
                self._send_json({"status": "ok", **market.source_summary()})
            elif path == "/api/universe":
                self._send_json({"rows": get_market().universe_rows()})
            elif path == "/api/sectors":
                self._send_json({"sectors": get_market().sector_summary()})
            elif path.startswith("/api/ohlc/"):
                ticker = path.rsplit("/", 1)[-1]
                data = get_market().ohlc_weekly(ticker)
                if data is None:
                    self._send_json({"error": f"unknown ticker {ticker}"}, 404)
                else:
                    self._send_json(data)
            elif path == "/api/13f":
                self._send_json(get_market().thirteen_f())
            elif path.startswith("/api/13f/"):
                ticker = path.rsplit("/", 1)[-1]
                self._send_json({"ticker": ticker.upper(), "holdings": get_market().thirteen_f_for_ticker(ticker)})
            elif path == "/api/insights":
                self._send_json({"insights": get_market().insights()})
            elif path.startswith("/api/"):
                self._send_json({"error": "unknown endpoint"}, 404)
            else:
                self._serve_static(path)
        except Exception as exc:  # keep the server alive; surface the error to the client
            self._send_json({"error": str(exc)}, 500)


def main():
    port = int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Trading Dashboard running at http://localhost:{port}")
    get_market()  # warm the cache before first request
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
