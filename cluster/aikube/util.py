"""util — 公共工具：路径、端口、HTTP 客户端、日志。"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from urllib import request, error


def home_dir() -> Path:
    """~/.aikube —— 类比 ~/.kube，集群状态与配置的家目录。"""
    d = Path(os.environ.get("AIKUBE_HOME", Path.home() / ".aikube"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def etcd_path() -> Path:
    return home_dir() / "etcd.jsonl"


def cluster_conf_path() -> Path:
    return home_dir() / "cluster.json"


def kubeconfig_path() -> Path:
    return home_dir() / "config"


def run_dir() -> Path:
    d = home_dir() / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = home_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_port(preferred: int, base: int = 16000, span: int = 500) -> int:
    """在 preferred 被占用时，从 base 起找空闲端口。"""
    for p in [preferred, *range(base, base + span)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free port found")


def log(tag: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {tag}: {msg}", flush=True)


# ---------------------------------------------------------------- HTTP client
class APIError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def http(method: str, url: str, payload: dict | None = None,
         token: str | None = None, timeout: float = 5.0) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except error.HTTPError as e:
        raise APIError(e.code, e.read().decode()[:500]) from None
    except error.URLError as e:
        raise APIError(0, f"无法连接 {url}: {e.reason}") from None


def api_url() -> str:
    """从 kubeconfig 读取 apiserver 地址（类比 kubectl 的 --server）。"""
    cfg = load_kubeconfig()
    return cfg.get("server", "http://127.0.0.1:16443")


def load_kubeconfig() -> dict:
    p = kubeconfig_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_kubeconfig(cfg: dict) -> None:
    kubeconfig_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                 encoding="utf-8")


def load_cluster_conf() -> dict:
    p = cluster_conf_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_cluster_conf(cfg: dict) -> None:
    cluster_conf_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                   encoding="utf-8")


def read_pid(name: str) -> int | None:
    f = run_dir() / f"{name}.pid"
    if f.exists():
        try:
            return int(f.read_text().strip())
        except ValueError:
            return None
    return None


def write_pid(name: str, pid: int) -> None:
    (run_dir() / f"{name}.pid").write_text(str(pid))


def rm_pid(name: str) -> None:
    f = run_dir() / f"{name}.pid"
    if f.exists():
        f.unlink()


def is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def die(msg: str, code: int = 1) -> "NoReturn":
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(code)
