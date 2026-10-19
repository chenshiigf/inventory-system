"""One-shot legacy ASGI read probe. No HTTP listener or ordinary service startup.

Run as the inventory user with the restored interpreter. Business connections
are SQLite mode=ro; application scratch/AI state lives only in a new temporary
data directory. The outer ASGI gate does not trust legacy maintenance middleware.
"""
import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys


READS = (
    ("/api/dashboard/summary", b""),
    ("/api/products", b"page=1&page_size=1&status=all"),
)


class ReadOnlyProbe:
    def __init__(self, legacy):
        self.legacy = legacy

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.legacy(scope, receive, send)
        if (scope["type"] != "http" or scope.get("method") != "GET" or
                (scope.get("path"), scope.get("query_string", b"")) not in READS):
            if scope["type"] != "http":
                raise RuntimeError("Only the fixed HTTP read probes are supported")
            await send({"type": "http.response.start", "status": 503, "headers": []})
            await send({"type": "http.response.body", "body": b'{"detail":"read-only probe"}'})
            return
        return await self.legacy(scope, receive, send)


async def request(app, method, path, query=b""):
    messages = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Future()

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "method": method, "path": path, "raw_path": path.encode(),
             "query_string": query, "headers": [], "http_version": "1.1",
             "scheme": "http", "server": ("127.0.0.1", 8102),
             "client": ("127.0.0.1", 1), "root_path": ""}
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    starts = [m for m in messages if m["type"] == "http.response.start"]
    if len(starts) != 1:
        raise RuntimeError("Probe returned no single HTTP response")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return starts[0]["status"], body


async def inspect(legacy):
    app = ReadOnlyProbe(legacy)
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    task = asyncio.create_task(app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}},
                                   incoming.get, outgoing.put))
    started = False
    try:
        await incoming.put({"type": "lifespan.startup"})
        if (await asyncio.wait_for(outgoing.get(), 10))["type"] != "lifespan.startup.complete":
            raise RuntimeError("Legacy application startup failed")
        started = True
        for path, query in READS:
            status, body = await request(app, "GET", path, query)
            if status != 200 or not isinstance(json.loads(body), dict):
                raise RuntimeError("Legacy read probe failed")
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            status, _ = await request(app, method, "/api/products")
            if status != 503:
                raise RuntimeError("Probe write admission is not closed")
    finally:
        try:
            if started:
                await incoming.put({"type": "lifespan.shutdown"})
                if (await asyncio.wait_for(outgoing.get(), 10))["type"] != "lifespan.shutdown.complete":
                    raise RuntimeError("Legacy application shutdown failed")
                await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    return {"format": "numbering-abort-readonly-probe-v1", "state": "passed",
            "transport": "asgi-no-listener", "read_routes": len(READS), "writes_rejected": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        api = args.api_root.resolve(strict=True)
        database = args.database.resolve(strict=True)
        scratch = args.data_root.resolve(strict=True)
        if (not api.is_dir() or not database.is_file() or not scratch.is_dir() or
                scratch.is_relative_to(database.parent) or database.is_relative_to(scratch) or
                scratch.is_relative_to(api)):
            raise RuntimeError("Probe data must be isolated from the business database and old code")
        # The production adapter starts with env -i. Set these again before any
        # legacy imports; SQLite enforces mode=ro independently of legacy code.
        os.environ["DATABASE_URL"] = "sqlite:///" + database.as_uri() + "?mode=ro&uri=true"
        os.environ["INVENTORY_DATA_DIR"] = str(scratch)
        os.environ.pop("DASHSCOPE_API_KEY", None)
        sys.path.insert(0, str(api))
        legacy = importlib.import_module("app.main").app
        print(json.dumps(asyncio.run(inspect(legacy)), sort_keys=True))
        return 0
    except Exception as error:
        # Do not echo legacy exceptions or environment values into root reports.
        print("Read-only legacy probe failed: " + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
