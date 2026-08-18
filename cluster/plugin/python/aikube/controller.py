"""ai-controller-manager — 控制器（类比 kube-controller-manager）。

循环职责（对齐 k8s 控制器的核心保证）：
1. 节点健康：心跳超时(默认 10s) → NotReady（博客中"节点状态 NotReady"处理）
2. 驱逐重调度：NotReady 节点上的 Running/Scheduled Pod → Evicted → 自动 requeue
   回 Pending，交给调度器重新选节点（自愈网络）
3. 副本保持：任务期望 replicas 与存活 Pod 数不符时补建（类比 Deployment 控制器）
4. 事件记录：所有动作写入 /events 供 describe 追溯
"""
from __future__ import annotations

import argparse
import time

from .util import http, log, api_url

HEARTBEAT_TIMEOUT = 10.0  # 秒，超过即 NotReady（博客避坑表第一条）
EVICT_RETRY = 2           # 驱逐后等待 requeue 的轮数


class Controller:
    def __init__(self, api: "Api", interval: float = 2.0,
                 heartbeat_timeout: float = HEARTBEAT_TIMEOUT):
        self.api = api
        self.interval = interval
        self.heartbeat_timeout = heartbeat_timeout
        self._evicted: dict[str, int] = {}

    def reconcile_once(self) -> None:
        now = time.time()
        # --- 1. 节点健康巡检（heartbeat 只由 kubelet 上报，控制器不改写）
        for name, node in self.api.nodes().items():
            stale = now - node.get("heartbeat", 0) > self.heartbeat_timeout
            if stale:
                if node.get("ready"):
                    self.api.mark_node_ready(name, False)
                    log("ai-controller", f"{name} 心跳超时 -> NotReady")
                    self._evict_node_pods(name, "节点 NotReady")
            else:
                if not node.get("ready"):
                    self.api.mark_node_ready(name, True)
                    log("ai-controller", f"{name} 心跳恢复 -> Ready")

        # --- 2. 驱逐后的 Pod 送回 Pending（下一轮调度器接管）
        for pod in self.api.pods(phase="Evicted"):
            self._evicted[pod["name"]] = self._evicted.get(pod["name"], 0) + 1
            if self._evicted[pod["name"]] >= EVICT_RETRY:
                self.api.requeue_pod(pod["name"])
                log("ai-controller", f"{pod['name']} requeue -> Pending(重调度)")
                self._evicted[pod["name"]] = 0

        # --- 3. 副本保持（Deployment 控制器类比）
        for task in self.api.tasks().values():
            desired = int(task.get("replicas", 1))
            pods = [p for p in self.api.pods() if p["task"] == task["name"]]
            # Pending 也算存活（正在调度中）；只有 Failed 需要补建，
            # Evicted 由步骤 2 requeue 重调度
            alive = [p for p in pods if p["phase"] in
                     ("Pending", "Scheduled", "Running")]
            done = [p for p in pods if p["phase"] == "Succeeded"]
            if len(alive) + len(done) < desired:
                missing = desired - len(alive) - len(done)
                for _ in range(missing):
                    idx = max((int(p["name"].rsplit("-", 1)[-1]) for p in pods),
                              default=0) + 1
                    self.api.create_replica_pod(task, idx)
                log("ai-controller",
                    f"task {task['name']} 副本保持: 期望{desired} "
                    f"存活{len(alive)}+完成{len(done)} -> 补建{missing}")

    def _evict_node_pods(self, node: str, reason: str) -> None:
        for pod in self.api.pods(node=node):
            if pod["phase"] in ("Running", "Scheduled"):
                self.api.evict_pod(pod["name"], reason)
                log("ai-controller", f"驱逐 {pod['name']} ({reason})")

    def run(self) -> None:
        log("ai-controller", f"控制器循环启动 (interval={self.interval}s)")
        while True:
            try:
                self.reconcile_once()
            except Exception as e:
                log("ai-controller", f"巡检异常: {e}")
            time.sleep(self.interval)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="aikube controller")
    ap.add_argument("--api", default=api_url())
    ap.add_argument("--token", default="")
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args(argv)
    from .apiserver import Api
    api = Api({"url": args.api, "token": args.token}, remote=True)
    Controller(api, args.interval).run()


if __name__ == "__main__":
    main()
