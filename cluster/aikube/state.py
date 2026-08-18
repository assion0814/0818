"""ai-etcd — 集群状态存储（类比 etcd）。

实现：append-only JSONL 落盘 + 内存 map + HTTP API（/v2/kv/*）。
写操作先落盘再进内存，崩溃后重放日志恢复 —— 与 etcd WAL 思路一致。
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

from .util import etcd_path, log, free_port

DEFAULT_PORT = 12379


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._replay()

    def _replay(self) -> None:
        """重放 WAL，恢复内存状态（etcd 启动恢复类比）。"""
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                op = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = op["key"]
            if op["op"] == "put":
                self._data[key] = op["value"]
            elif op["op"] == "delete":
                self._data.pop(key, None)

    def _append(self, op: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(op, ensure_ascii=False) + "\n")

    def put(self, key: str, value: dict) -> dict:
        with self._lock:
            self._append({"op": "put", "key": key, "value": value})
            self._data[key] = value
        return value

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._data.get(key)

    def delete(self, key: str) -> bool:
        with self._lock:
            existed = key in self._data
            if existed:
                self._append({"op": "delete", "key": key})
                self._data.pop(key, None)
            return existed

    def prefix(self, prefix: str) -> dict[str, dict]:
        with self._lock:
            return {k: v for k, v in self._data.items() if k.startswith(prefix)}


class Handler(BaseHTTPRequestHandler):
    store: Store  # 由 serve() 注入

    def log_message(self, *a):  # 静默访问日志
        pass

    def _reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/healthz":
            return self._reply(200, {"ok": True})
        if u.path.startswith("/v2/kv/"):
            key = "/" + unquote(u.path[len("/v2/kv/"):])
            exact = self.store.get(key)
            if exact is not None:
                return self._reply(200, {"value": exact})
            return self._reply(200, {"items": self.store.prefix(key)})
        if u.path == "/v2/kv":
            return self._reply(200, {"items": self.store.prefix(unquote(u.query))})
        self._reply(404, {"error": "not found"})

    def do_PUT(self):
        u = urlparse(self.path)
        if u.path.startswith("/v2/kv/"):
            key = "/" + unquote(u.path[len("/v2/kv/"):])
            try:
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            except Exception:
                return self._reply(400, {"error": "bad json"})
            if key == "/":
                return self._reply(400, {"error": "empty key"})
            self.store.put(key, value)
            return self._reply(200, {"key": key, "value": value})
        self._reply(404, {"error": "not found"})

    def do_DELETE(self):
        u = urlparse(self.path)
        if u.path.startswith("/v2/kv/"):
            key = "/" + unquote(u.path[len("/v2/kv/"):])
            existed = self.store.delete(key)
            return self._reply(200, {"key": key, "deleted": existed})
        self._reply(404, {"error": "not found"})


def serve(port: int, path: Path) -> None:
    store = Store(path)
    Handler.store = store
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    log("ai-etcd", f"监听 127.0.0.1:{port}，WAL: {path}")
    srv.serve_forever()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="aikube etcd", description="ai-etcd 状态存储")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--path", type=Path, default=None)
    args = ap.parse_args(argv)
    serve(args.port, args.path or etcd_path())


if __name__ == "__main__":
    main()
