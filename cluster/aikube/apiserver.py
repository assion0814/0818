"""ai-apiserver — API 服务（类比 kube-apiserver）+ ai-cni 路由表（类比 Calico）。

职责：
- 节点注册 / 心跳 / 状态（kubelet 上报）
- 任务 CRUD：提交任务 → 拆分为 N 个 Pod（replicas）→ 进入 Pending
- Pod 生命周期：bind / 状态上报 / 驱逐
- ai-cni：任务→节点 的路由表与节点发现（模拟 Calico 网络插件）
- Bearer token 鉴权（类比 kubeadm 的 cluster token）
"""
from __future__ import annotations

import argparse
import re
import json
import secrets
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .util import http, log, free_port, etcd_path, api_url, APIError
from .tools import default_node_tools

DEFAULT_PORT = 16443
ETCD_PORT = 12379

# 任务模式（对齐套装 router-standard 的四行为带）
MODES = ("auto", "spec", "react", "mixed", "weak")
PHASES = ("Pending", "Scheduled", "Running", "Succeeded", "Failed", "Evicted")

NODE_ROLES = {"master": "master", "worker": "worker"}

# 控制面 API 面：只保留集群管理端点（无任何任务执行/任意代码端点）
CONTROL_PLANE_API = [
    "GET  /healthz",
    "POST /api/v1/nodes                    节点注册（join token 校验）",
    "POST /api/v1/nodes/<name>/heartbeat   心跳上报",
    "POST /api/v1/nodes/<name>/cordon      cordon/uncordon",
    "GET  /api/v1/nodes[ /<name>]          节点查询",
    "POST /api/v1/tasks                    任务提交（拆分为 Pod）",
    "GET  /api/v1/tasks[ /<name>]          任务查询",
    "GET  /api/v1/pods[?node&phase]        Pod 查询",
    "POST /api/v1/pods/<name>/bind         调度器绑定",
    "POST /api/v1/pods/<name>/status       kubelet 状态上报",
    "POST /api/v1/pods/<name>/tools        工具调用留痕",
    "POST /api/v1/pods/<name>/evict        驱逐",
    "POST /api/v1/pods/<name>/requeue      重调度回队",
    "PUT/DELETE /api/v1/{nodes,pods,tasks}/<name>  组件内状态同步",
]
# 显式禁止面：控制面绝不提供任务执行类端点（/exec、/run、/shell）
FORBIDDEN_API = ("/exec", "/shell", "/run")


class Cni:
    """ai-cni — 节点发现 + 任务→节点路由表（模拟 Calico / 集群网络层）。"""

    def __init__(self, etcd: dict):
        self.etcd = etcd  # {"url": ..., "token": ...}

    def nodes(self) -> dict[str, dict]:
        return self.etcd_get("/nodes/")

    def etcd_get(self, prefix: str) -> dict:
        return http("GET", self.etcd["url"] + "/v2/kv" + prefix,
                    token=self.etcd.get("token"))

    def etcd_put(self, key: str, value: dict) -> None:
        http("PUT", self.etcd["url"] + "/v2/kv/" + key, value,
             token=self.etcd.get("token"))


class Api:
    """集群状态读写：etcd 之上的领域模型。

    remote=False（apiserver 进程内）：直连 ai-etcd（/v2/kv）。
    remote=True （scheduler/controller 等组件）：走 apiserver REST（/api/v1）。
    """

    def __init__(self, etcd: dict, remote: bool = False):
        self.etcd = etcd
        self.remote = remote
        self.cni = Cni(etcd)

    # ------------------------------------------------------- 底层存取
    _KINDS = {"nodes/": "nodes", "pods/": "pods", "tasks/": "tasks"}

    def _split(self, key: str) -> tuple[str, str]:
        """/nodes/k8s-node1 -> ("nodes", "k8s-node1")"""
        prefix, _, name = key[1:].partition("/")
        return prefix, name

    def _get(self, key: str) -> dict | None:
        if self.remote:
            kind, name = self._split(key)
            r = http("GET", f"{self.etcd['url']}/api/v1/{kind}/{name}",
                     token=self.etcd.get("token"))
            items = r.get("items") or []
            return items[0] if items else None
        r = http("GET", self.etcd["url"] + "/v2/kv" + key,
                 token=self.etcd.get("token"))
        return r.get("value")

    def _put(self, key: str, value: dict) -> None:
        if self.remote:
            kind, name = self._split(key)
            http("PUT", f"{self.etcd['url']}/api/v1/{kind}/{name}", value,
                 token=self.etcd.get("token"))
            return
        http("PUT", self.etcd["url"] + "/v2/kv" + key, value,
             token=self.etcd.get("token"))

    def _delete(self, key: str) -> None:
        if self.remote:
            kind, name = self._split(key)
            http("DELETE", f"{self.etcd['url']}/api/v1/{kind}/{name}",
                 token=self.etcd.get("token"))
            return
        http("DELETE", self.etcd["url"] + "/v2/kv" + key,
             token=self.etcd.get("token"))

    def _prefix(self, prefix: str) -> dict:
        if self.remote:
            kind = self._KINDS.get(prefix.lstrip("/"))
            if not kind:
                raise APIError(404, f"unknown prefix {prefix}")
            r = http("GET", f"{self.etcd['url']}/api/v1/{kind}",
                     token=self.etcd.get("token"))
            items = r.get("items", {})
            if isinstance(items, list):  # 兼容性：列表转 name->obj
                items = {p["name"]: p for p in items}
            return {"items": {k.split("/", 2)[-1]: v for k, v in items.items()}}
        r = http("GET", self.etcd["url"] + "/v2/kv" + prefix,
                 token=self.etcd.get("token"))
        items = r.get("items", {})
        return {"items": {k.split("/", 2)[-1]: v for k, v in items.items()}}

    # ------------------------------------------------------------- 节点
    def nodes(self) -> dict[str, dict]:
        return self._prefix("/nodes/")["items"]

    def node(self, name: str) -> dict | None:
        return self._get(f"/nodes/{name}")

    def register_node(self, name: str, profile: dict, token: str) -> dict:
        caps = profile.get("capabilities", ["execute"])
        node = {
            "name": name,
            "role": profile.get("role", "worker"),
            "model": profile.get("model", "mock-flash"),
            "capabilities": caps,
            # 工具最小权限：显式白名单优先，否则由能力推导最小集
            "tools": sorted(profile.get("tools") or default_node_tools(caps)),
            "slots": profile.get("slots", 2),
            "labels": profile.get("labels", {}),
            "runtime": profile.get("runtime", "mock"),
            "ready": True,
            "schedulable": True,
            "heartbeat": time.time(),
            "queue": 0,
            "latency_ms": 0.0,
            "last_seen": time.time(),
            "token": token,
            "pods": [],
        }
        self._put(f"/nodes/{name}", node)
        return node

    def node_heartbeat(self, name: str, status: dict) -> dict | None:
        node = self._get(f"/nodes/{name}")
        if not node:
            return None
        node["heartbeat"] = time.time()
        node["ready"] = bool(status.get("ready", node.get("ready", True)))
        node["queue"] = status.get("queue", node.get("queue", 0))
        node["latency_ms"] = status.get("latency_ms", node.get("latency_ms", 0.0))
        node["last_seen"] = time.time()
        self._put(f"/nodes/{name}", node)
        return node

    def set_node_schedulable(self, name: str, schedulable: bool) -> dict | None:
        node = self._get(f"/nodes/{name}")
        if not node:
            return None
        node["schedulable"] = schedulable
        self.set_node_event(name, "cordon" if not schedulable else "uncordon")
        self._put(f"/nodes/{name}", node)
        return node

    def set_node_event(self, name: str, what: str) -> None:
        node = self._get(f"/nodes/{name}")
        if not node:
            return
        events = node.setdefault("events", [])
        events.append({"t": time.time(), "what": what})
        self._put(f"/nodes/{name}", node)

    def mark_node_ready(self, name: str, ready: bool) -> dict | None:
        """仅翻转 ready 标志，不触碰 heartbeat（heartbeat 只由 kubelet 上报）。"""
        node = self._get(f"/nodes/{name}")
        if not node:
            return None
        node["ready"] = ready
        self.set_node_event(name, "Ready(心跳恢复)" if ready else "NotReady: 心跳超时")
        self._put(f"/nodes/{name}", node)
        return node

    def create_replica_pod(self, task: dict, idx: int) -> dict:
        pod = {
            "name": f"{task['name']}-{idx:02d}",
            "task": task["name"],
            "mode": task.get("mode", "auto"),
            "size": task.get("size", "small"),
            "affinity": task.get("affinity", {}),
            "text": task.get("text", ""),
            "tools_requested": task.get("tools_requested", []),
            "tools_allowed": None,
            "phase": "Pending",
            "node": None,
            "created": time.time(),
            "started": None,
            "finished": None,
            "attempts": 0,
            "retries_left": 3,
            "reschedule_count": 0,
            "decision": None,
            "classification": None,
            "events": [{"t": time.time(), "what": "created by replicaset 控制器"}],
        }
        self._put(f"/pods/{pod['name']}", pod)
        return pod

    # ------------------------------------------------------------- 任务/Pod
    def create_task(self, spec: dict, token: str) -> dict:
        task_id = spec.get("name") or "task-" + uuid.uuid4().hex[:8]
        requested = sorted(set(spec.get("tools") or []))
        task = {
            "name": task_id,
            "text": spec["text"],
            "mode": spec.get("mode", "auto"),
            "replicas": int(spec.get("replicas", 1)),
            "affinity": spec.get("affinity", {}),
            "size": spec.get("size", "small"),
            "tools_requested": requested,
            "created": time.time(),
            "owner": token[:8],
        }
        self._put(f"/tasks/{task_id}", task)
        for i in range(1, task["replicas"] + 1):
            pod = {
                "name": f"{task_id}-{i:02d}",
                "task": task_id,
                "mode": task["mode"],
                "size": task["size"],
                "affinity": task["affinity"],
                "text": task["text"],
                "tools_requested": requested,
                "tools_allowed": None,   # 调度器绑定后填充
                "phase": "Pending",
                "node": None,
                "created": time.time(),
                "started": None,
                "finished": None,
                "attempts": 0,
                "retries_left": 3,
                "reschedule_count": 0,
                "decision": None,      # 调度器打分明细
                "classification": None,  # AI 分类结果
                "events": [],
            }
            self._put(f"/pods/{pod['name']}", pod)
        return task

    def pods(self, node: str | None = None, phase: str | None = None) -> list[dict]:
        items = self._prefix("/pods/")["items"]
        out = list(items.values())
        if node:
            out = [p for p in out if p.get("node") == node]
        if phase:
            out = [p for p in out if p.get("phase") == phase]
        return sorted(out, key=lambda p: p["name"])

    def tasks(self) -> dict[str, dict]:
        return self._prefix("/tasks/")["items"]

    def pod(self, name: str) -> dict | None:
        return self._get(f"/pods/{name}")

    def set_pod_phase(self, name: str, phase: str, **extra) -> dict | None:
        pod = self._get(f"/pods/{name}")
        if not pod:
            return None
        pod["phase"] = phase
        pod.update(extra)
        self._put(f"/pods/{name}", pod)
        return pod

    def bind_pod(self, name: str, node: str, decision: dict) -> dict | None:
        pod = self._get(f"/pods/{name}")
        if not pod or pod["phase"] != "Pending":
            return None
        pod["phase"] = "Scheduled"
        pod["node"] = node
        # 工具最小权限：只批准节点白名单 ∩ 任务所需工具。
        # 任务所需 = 显式请求 ∪ 调度器按 AI 分类推导的模式默认集（decision.tools_required）
        node_obj = self.node(node) or {}
        whitelist = set(node_obj.get("tools", []))
        requested = set(pod.get("tools_requested", []))
        if not requested:
            requested = set((decision or {}).get("tools_required", []))
        allowed = sorted(requested & whitelist)
        pod["tools_allowed"] = allowed
        pod["decision"] = decision
        pod["events"].append({"t": time.time(),
                              "what": f"bind -> {node}, tools={allowed or 'none'}"})
        self._put(f"/pods/{name}", pod)
        return pod

    def pod_tool_event(self, name: str, tool_log: list[dict]) -> dict | None:
        """节点侧工具调用留痕：记录每次工具调用的放行/拒绝。"""
        pod = self._get(f"/pods/{name}")
        if not pod:
            return None
        pod["tool_log"] = tool_log
        for entry in tool_log:
            status = "放行" if entry.get("allowed") else "拒绝"
            pod["events"].append({"t": time.time(),
                                  "what": f"tool.{entry['tool']} {status}: "
                                          f"{entry.get('result','')[:60]}"})
        self._put(f"/pods/{name}", pod)
        return pod

    def evict_pod(self, name: str, reason: str) -> dict | None:
        pod = self._get(f"/pods/{name}")
        if not pod or pod["phase"] in ("Succeeded", "Failed", "Evicted"):
            return None
        pod["phase"] = "Evicted"
        pod["reschedule_count"] = pod.get("reschedule_count", 0) + 1
        pod["node"] = None
        pod["events"].append({"t": time.time(), "what": f"evict: {reason}"})
        self._put(f"/pods/{name}", pod)
        return pod

    def requeue_pod(self, name: str) -> dict | None:
        """驱逐后的 Pod 回到 Pending 等待重调度（kube-scheduler 重绑定类比）。"""
        pod = self._get(f"/pods/{name}")
        if not pod or pod["phase"] != "Evicted":
            return None
        pod["phase"] = "Pending"
        pod["events"].append({"t": time.time(), "what": "requeue -> Pending"})
        self._put(f"/pods/{name}", pod)
        return pod


class Handler(BaseHTTPRequestHandler):
    api: Api
    token: str

    def log_message(self, *a):
        pass

    def _reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

    def _authed(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {self.token}"

    def do_GET(self):
        if self.path == "/healthz":
            return self._reply(200, {"ok": True, "component": "ai-apiserver"})
        if not self._authed():
            return self._reply(401, {"error": "unauthorized"})
        if any(f in self.path for f in FORBIDDEN_API):
            return self._reply(404, {"error": f"forbidden: {self.path}"})
        u = urlparse(self.path)
        p = u.path
        if p == "/api/v1/nodes":
            return self._reply(200, {"items": self.api.nodes()})
        if p.startswith("/api/v1/nodes/"):
            node = self.api.node(p[len("/api/v1/nodes/"):])
            return self._reply(200, {"items": [node]} if node else {"items": []})
        if p == "/api/v1/tasks":
            return self._reply(200, {"items": self.api.tasks()})
        if p.startswith("/api/v1/tasks/"):
            task = self.api._get("/tasks/" + p[len("/api/v1/tasks/"):])
            return self._reply(200, {"items": [task]} if task else {"items": []})
        if p == "/api/v1/pods":
            from urllib.parse import parse_qs
            q = parse_qs(u.query)
            pods = self.api.pods(q.get("node", [None])[0], q.get("phase", [None])[0])
            return self._reply(200, {"items": pods})
        if p.startswith("/api/v1/pods/"):
            pod = self.api.pod(p[len("/api/v1/pods/"):])
            return self._reply(200, {"items": [pod]} if pod else {"items": []})
        return self._reply(404, {"error": f"not found: {p}"})

    def do_POST(self):
        if not self._authed():
            return self._reply(401, {"error": "unauthorized"})
        u = urlparse(self.path)
        p = u.path
        if any(f in p for f in FORBIDDEN_API):
            return self._reply(404, {"error": f"forbidden: {p}"})
        try:
            body = self._body()
        except Exception:
            return self._reply(400, {"error": "bad json body"})
        # --- 节点注册（kubeadm join 的 apiserver 侧，校验 join token）
        if p == "/api/v1/nodes":
            if body.get("token", "") != self.token:
                return self._reply(403, {"error": "join token 无效（kubeadm 类比）"})
            node = self.api.register_node(body["name"], body["profile"],
                                          body.get("token", ""))
            return self._reply(201, {"node": node})
        # --- 心跳
        if p.startswith("/api/v1/nodes/") and p.endswith("/heartbeat"):
            name = p[len("/api/v1/nodes/"):-len("/heartbeat")]
            node = self.api.node_heartbeat(name, body)
            return self._reply(200, {"node": node} if node else {"error": "no node"})
        # --- cordon / uncordon（节点维护，博客"节点维护"操作类比）
        if p.startswith("/api/v1/nodes/") and p.endswith("/cordon"):
            name = p[len("/api/v1/nodes/"):-len("/cordon")]
            node = self.api.set_node_schedulable(name, bool(body.get("schedulable", False)))
            return self._reply(200, {"node": node} if node else {"error": "no node"})
        # --- 任务提交（kubectl run 的 apiserver 侧）
        if p == "/api/v1/tasks":
            task = self.api.create_task(body, self.token)
            return self._reply(201, {"task": task})
        # --- Pod 绑定（kube-scheduler 写回）
        if p.startswith("/api/v1/pods/") and p.endswith("/bind"):
            name = p[len("/api/v1/pods/"):-len("/bind")]
            pod = self.api.bind_pod(name, body["node"], body.get("decision", {}))
            return self._reply(200, {"pod": pod} if pod else {"error": "bind failed"})
        # --- Pod 状态上报（kubelet）
        if p.startswith("/api/v1/pods/") and p.endswith("/status"):
            name = p[len("/api/v1/pods/"):-len("/status")]
            pod = self.api.set_pod_phase(name, body["phase"], **body.get("extra", {}))
            return self._reply(200, {"pod": pod} if pod else {"error": "no pod"})
        # --- 工具调用留痕（kubelet 上报 ToolSandbox 日志）
        if p.startswith("/api/v1/pods/") and p.endswith("/tools"):
            name = p[len("/api/v1/pods/"):-len("/tools")]
            pod = self.api.pod_tool_event(name, body.get("tool_log", []))
            return self._reply(200, {"pod": pod} if pod else {"error": "no pod"})
        # --- 驱逐（controller-manager）
        if p.startswith("/api/v1/pods/") and p.endswith("/evict"):
            name = p[len("/api/v1/pods/"):-len("/evict")]
            pod = self.api.evict_pod(name, body.get("reason", "manual"))
            return self._reply(200, {"pod": pod} if pod else {"error": "evict failed"})
        if p.startswith("/api/v1/pods/") and p.endswith("/requeue"):
            name = p[len("/api/v1/pods/"):-len("/requeue")]
            pod = self.api.requeue_pod(name)
            return self._reply(200, {"pod": pod} if pod else {"error": "requeue failed"})
        # --- 组件通用读写（scheduler/controller 的 remote 模式；
        #     仅匹配 /api/v1/<kind>/<name> 单段资源，杜绝 /exec 等落入）
        m = re.fullmatch(r"/api/v1/(nodes|pods|tasks)/([^/]+)", p)
        if m:
            kind, name = m.group(1), m.group(2)
            self.api._put(f"/{kind}/{name}", body)
            return self._reply(200, {kind[:-1]: self.api._get(f"/{kind}/{name}")})
        return self._reply(404, {"error": f"not found: {p}"})

    def do_PUT(self):
        if not self._authed():
            return self._reply(401, {"error": "unauthorized"})
        u = urlparse(self.path)
        p = u.path
        try:
            body = self._body()
        except Exception:
            return self._reply(400, {"error": "bad json body"})
        m = re.fullmatch(r"/api/v1/(nodes|pods|tasks)/([^/]+)", p)
        if m:
            kind, name = m.group(1), m.group(2)
            self.api._put(f"/{kind}/{name}", body)
            return self._reply(200, {kind[:-1]: body})
        return self._reply(404, {"error": f"not found: {p}"})

    def do_DELETE(self):
        if not self._authed():
            return self._reply(401, {"error": "unauthorized"})
        u = urlparse(self.path)
        p = u.path
        m = re.fullmatch(r"/api/v1/(nodes|pods|tasks)/([^/]+)", p)
        if m:
            kind, name = m.group(1), m.group(2)
            self.api._delete(f"/{kind}/{name}")
            return self._reply(200, {"deleted": True})
        return self._reply(404, {"error": f"not found: {p}"})


def serve(port: int, etcd: dict, token: str) -> None:
    api = Api(etcd)
    Handler.api = api
    Handler.token = token
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    log("ai-apiserver", f"监听 127.0.0.1:{port}，etcd={etcd['url']}")
    srv.serve_forever()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="aikube apiserver")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--etcd-port", type=int, default=ETCD_PORT)
    ap.add_argument("--token", default="")
    args = ap.parse_args(argv)
    token = args.token or secrets.token_hex(16)
    etcd = {"url": f"http://127.0.0.1:{args.etcd_port}", "token": ""}
    serve(args.port, etcd, token)


if __name__ == "__main__":
    main()
