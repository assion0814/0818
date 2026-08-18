"""ai-kubelet — 节点 Agent（类比 kubelet + containerd 组合）。

职责：
- 注册节点（kubeadm join 的节点侧：带 token 向 apiserver 注册）
- 每 3s 心跳上报：就绪、队列深度、平均延迟（kubelet 的 NodeStatus 类比）
- 每 1s 轮询本节点上 Scheduled 的 Pod → 认领(Running) → 执行(runtime)
  → 上报 Succeeded / Failed（执行失败重试 3 次后 Failed，交给控制器补建）
"""
from __future__ import annotations

import argparse
import time
import traceback
from pathlib import Path
from tempfile import mkdtemp

from .util import http, log
from .runtime import Runtime
from .tools import ToolSandbox

HEARTBEAT_INTERVAL = 3.0
POLL_INTERVAL = 1.0
MAX_ATTEMPTS = 3


class Kubelet:
    def __init__(self, name: str, api_url: str, token: str, profile: dict,
                 runtime: Runtime):
        self.name = name
        self.api_url = api_url
        self.token = token
        self.profile = profile
        self.runtime = runtime
        self._latency: list[float] = []

    def _api(self, path: str, method: str = "GET", payload: dict | None = None) -> dict:
        return http(method, self.api_url + path, payload, token=self.token)

    # ------------------------------------------------------------- 生命周期
    def register(self) -> None:
        self._api("/api/v1/nodes", "POST",
                  {"name": self.name, "profile": self.profile,
                   "token": self.profile.get("join_token", "")})
        log("ai-kubelet", f"{self.name} 注册成功 (model={self.profile.get('model')})")

    def heartbeat(self) -> None:
        latency = (sum(self._latency) / len(self._latency)) * 1000 if self._latency else 0.0
        try:
            pods = self._api(f"/api/v1/pods?node={self.name}").get("items", [])
            running = len([p for p in pods if p["phase"] in ("Running", "Scheduled")])
        except Exception:
            running = 0
        self._api(f"/api/v1/nodes/{self.name}/heartbeat", "POST",
                  {"ready": True, "queue": running, "latency_ms": round(latency, 1)})

    # ------------------------------------------------------------- Pod 执行
    def _poll(self) -> None:
        try:
            pods = self._api(f"/api/v1/pods?node={self.name}&phase=Scheduled").get("items", [])
        except Exception as e:
            log("ai-kubelet", f"轮询失败: {e}")
            return
        for pod in pods:
            self._claim_and_run(pod)

    def _claim_and_run(self, pod: dict) -> None:
        name = pod["name"]
        self._api(f"/api/v1/pods/{name}/status", "POST",
                  {"phase": "Running", "extra": {"started": time.time(),
                                                 "attempts": pod.get("attempts", 0) + 1}})
        log("ai-kubelet", f"{self.name} 开始执行 {name} [{pod['mode']}] "
                          f"tools={pod.get('tools_allowed') or 'none'}")
        # 工具最小权限：每个 Pod 独立工作区 + 只放行 tools_allowed 的沙箱
        workspace = Path(mkdtemp(prefix=f"aikube-pod-{name}-"))
        sandbox = ToolSandbox(workspace, set(pod.get("tools_allowed") or []))
        try:
            out, cost, tool_log = self.runtime.run(pod, sandbox)
            self._latency.append(cost)
            self._latency = self._latency[-10:]
            if tool_log:
                self._api(f"/api/v1/pods/{name}/tools", "POST",
                          {"tool_log": tool_log})
            self._api(f"/api/v1/pods/{name}/status", "POST",
                      {"phase": "Succeeded",
                       "extra": {"output": out, "cost_s": cost,
                                 "finished": time.time()}})
            log("ai-kubelet", f"{self.name} 完成 {name} ({cost}s)")
        except Exception as e:
            traceback.print_exc()
            attempts = pod.get("attempts", 0) + 1
            if attempts >= MAX_ATTEMPTS:
                self._api(f"/api/v1/pods/{name}/status", "POST",
                          {"phase": "Failed", "extra": {"error": str(e),
                                                        "finished": time.time()}})
                log("ai-kubelet", f"{self.name} 执行失败 {name}: {e} (重试耗尽)")
            else:
                self._api(f"/api/v1/pods/{name}/status", "POST",
                          {"phase": "Scheduled",
                           "extra": {"attempts": attempts, "last_error": str(e)}})
                log("ai-kubelet", f"{self.name} 执行失败 {name}，第{attempts}次重试")

    def run(self) -> None:
        self.register()
        log("ai-kubelet", f"{self.name} 心跳/执行循环启动")
        last_hb = 0.0
        while True:
            try:
                if time.time() - last_hb >= HEARTBEAT_INTERVAL:
                    self.heartbeat()
                    last_hb = time.time()
                self._poll()
            except Exception as e:
                log("ai-kubelet", f"异常: {e}")
            time.sleep(POLL_INTERVAL)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="aikube kubelet")
    ap.add_argument("--node", required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--token", default="")
    ap.add_argument("--role", default="worker")
    ap.add_argument("--model", default="mock-flash")
    ap.add_argument("--capabilities", default="execute,classify")
    ap.add_argument("--slots", type=int, default=2)
    ap.add_argument("--labels", default="")
    ap.add_argument("--runtime", default="mock")
    ap.add_argument("--llm-url", default=None)
    ap.add_argument("--join-token", default="")
    args = ap.parse_args(argv)
    profile = {
        "role": args.role,
        "model": args.model,
        "capabilities": [c.strip() for c in args.capabilities.split(",") if c.strip()],
        "slots": args.slots,
        "labels": {kv.split("=")[0]: kv.split("=")[1] for kv in
                   args.labels.split(",") if "=" in kv},
        "runtime": args.runtime,
        "join_token": args.join_token,
    }
    rt = Runtime(args.runtime, llm_url=args.llm_url, model=args.model)
    Kubelet(args.node, args.api.rstrip("/"), args.token, profile, rt).run()


if __name__ == "__main__":
    main()
