"""Loopback-only QueuePilot application. No external APIs, telemetry or actions."""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import random
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

from routing import (
    QUEUES,
    clean_text,
    export_csv,
    parse_csv,
    reasons_for,
    queue_for,
    MAX_ROWS,
)
from store import Store, Conflict
from text_model import TextModel
from artifacts import strict_object

ROOT = Path(__file__).resolve().parent


class Application:
    def __init__(self, db, root=ROOT):
        self.root = Path(root)
        manifest = json.loads((self.root / "evidence/manifest.json").read_text())[
            "sha256"
        ]
        for name in (
            "models/baseline.json",
            "models/baseline.npz",
            "models/candidate.json",
            "models/candidate.npz",
            "evidence/report.json",
            "evidence/predictions.json",
        ):
            if hashlib.sha256(
                (self.root / name).read_bytes()
            ).hexdigest() != manifest.get(name):
                raise ValueError("Reference artifact changed: " + name)
        self.models = {
            kind: TextModel.load(self.root / "models", kind)
            for kind in ("baseline", "candidate")
        }
        self.labels = self.models["candidate"].labels
        self.report = json.loads((self.root / "evidence/report.json").read_text())
        self.predictions = json.loads(
            (self.root / "evidence/predictions.json").read_text()
        )
        self.threshold = self.report["models"]["candidate"]["policy"]["threshold"]
        self.token, self.lock, self.store = (
            secrets.token_urlsafe(32),
            threading.Lock(),
            Store(db),
        )
        if not self.store.state()["session"]:
            self.replay(120, 30, self.threshold)

    def replay(self, limit, budget, threshold):
        if type(limit) is not int or not 1 <= limit <= MAX_ROWS:
            raise ValueError("Replay size must be 1–1000.")
        rows = random.Random(17).sample(
            self.predictions, min(limit, len(self.predictions))
        )
        return self.store.create(
            rows,
            "BANKING77 official-test replay · deterministic sample, seed 17",
            threshold,
            budget,
        )

    def predict(self, rows):
        started = time.perf_counter()
        for kind, model in self.models.items():
            for row, prediction in zip(
                rows, model.predict([r["text"] for r in rows]), strict=True
            ):
                row[kind] = prediction
        return rows, (time.perf_counter() - started) * 1000

    def post(self, path, body):
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object.")
        if "budget" in body and (
            type(body["budget"]) is not int or not 0 <= body["budget"] <= MAX_ROWS
        ):
            raise ValueError("Review budget must be 0–1000 slots.")
        if path == "/api/replay":
            return self.replay(
                body.get("limit", 120),
                body.get("budget", 30),
                body.get("threshold", self.threshold),
            )
        if path == "/api/plan":
            return self.store.configure(
                body.get("session"), body.get("threshold"), body.get("budget")
            )
        if path == "/api/import":
            rows = parse_csv(body.get("csv"), self.labels)
            rows, elapsed = self.predict(rows)
            return {
                **self.store.create(
                    rows,
                    "User CSV · locally processed · labels optional",
                    body.get("threshold", self.threshold),
                    body.get("budget", 30),
                ),
                "inference_ms": elapsed,
            }
        if path == "/api/predict":
            text = clean_text(body.get("text"))
            rows, elapsed = self.predict([{"id": "scratchpad", "text": text}])
            row = rows[0]
            row["reasons"] = reasons_for(text, row["candidate"], self.threshold)
            row["suggested_queue"] = queue_for(row["candidate"]["label"])
            return {**row, "elapsed_ms": elapsed, "threshold": self.threshold}
        if path == "/api/review":
            return self.store.review(
                body.get("session"),
                body.get("id"),
                body.get("queue"),
                clean_text(body.get("note")),
                body.get("version"),
            )
        raise LookupError("Unknown API endpoint.")


def make_server(port, db, root=ROOT):
    app = Application(db, root)

    class Handler(BaseHTTPRequestHandler):
        server_version = "QueuePilot/2"

        def send(
            self,
            data,
            status=200,
            content_type="application/json; charset=utf-8",
            filename=None,
        ):
            if isinstance(data, (dict, list)):
                data = json.dumps(data, allow_nan=False).encode()
            elif isinstance(data, str):
                data = data.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
            if filename:
                self.send_header(
                    "Content-Disposition", 'attachment; filename="' + filename + '"'
                )
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.allowed():
                return self.send(
                    {"error": "Host must be the printed loopback address."}, 403
                )
            parsed = urlsplit(self.path)
            path = parsed.path
            try:
                if path == "/api/bootstrap":
                    return self.send(
                        {
                            "token": app.token,
                            "labels": app.labels,
                            "queues": QUEUES,
                            "report": app.report,
                            "threshold": app.threshold,
                        }
                    )
                session = parse_qs(parsed.query).get("session", [None])[0]
                if path == "/api/workspace":
                    return self.send(app.store.state(session))
                if path == "/api/export.csv":
                    return self.send(
                        export_csv(app.store.state(session)["rows"]),
                        content_type="text/csv; charset=utf-8",
                        filename="queuepilot-inbox.csv",
                    )
                if path == "/api/corrections.json":
                    return self.send(
                        json.dumps(
                            {
                                "scope": "Reviewer queue labels only; never added to model training or benchmark evaluation.",
                                "reviews": app.store.corrections(session),
                            },
                            indent=2,
                        ),
                        filename="queuepilot-corrections.json",
                    )
                static = {
                    "/": ("web/index.html", "text/html; charset=utf-8"),
                    "/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
                    "/style.css": ("web/style.css", "text/css; charset=utf-8"),
                    "/report.json": ("evidence/report.json", "application/json"),
                    "/evidence.zip": (
                        "evidence/queuepilot-evidence.zip",
                        "application/zip",
                    ),
                }
                if path not in static:
                    return self.send({"error": "Not found."}, 404)
                name, mime = static[path]
                return self.send(
                    (app.root / name).read_bytes(),
                    content_type=mime,
                    filename=(
                        "queuepilot-evidence.zip" if path == "/evidence.zip" else None
                    ),
                )
            except (ValueError, OSError) as exc:
                return self.send({"error": str(exc)}, 400)

        def do_POST(self):
            if (
                not self.allowed()
                or self.headers.get("Origin")
                != f"http://127.0.0.1:{self.server.server_port}"
                or not secrets.compare_digest(
                    self.headers.get("X-QueuePilot-Token", ""), app.token
                )
            ):
                return self.send(
                    {"error": "Local origin and session token are required."}, 403
                )
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send({"error": "JSON required."}, 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 520_000 or self.headers.get("Transfer-Encoding"):
                    return self.send(
                        {"error": "Request exceeds 520 KB or has no length."}, 413
                    )
                body = json.loads(
                    self.rfile.read(length),
                    object_pairs_hook=strict_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(
                        ValueError("Nonfinite JSON value.")
                    ),
                )
                with app.lock:
                    result = app.post(urlsplit(self.path).path, body)
                return self.send(result)
            except Conflict as exc:
                return self.send({"error": str(exc)}, 409)
            except LookupError as exc:
                return self.send({"error": str(exc)}, 404)
            except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                return self.send({"error": str(exc)}, 400)

        def log_message(self, format, *args):
            pass  # Never log message bodies or imported customer data.

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.app = app
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/inbox.sqlite3")
    args = parser.parse_args()
    server = make_server(args.port, args.db)
    print(f"QueuePilot ready: http://127.0.0.1:{server.server_port}", flush=True)
    print(
        "Local-only. Data stays in " + str(args.db) + ". Ctrl+C stops the server.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
