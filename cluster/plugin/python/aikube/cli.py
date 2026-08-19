"""aikube — AI K8s 集群调度网络 CLI（kubectl + kubeadm 风格合体）。

命令族（对齐博客《K8s搭建命令速查手册》的分组）：
  集群管理 : aikube cluster init|start|stop|status   （kubeadm init/systemctl 类比）
  节点加入 : aikube node join <token>                 （kubeadm join 类比）
  节点维护 : aikube node cordon|uncordon|drain <node> （kubectl cordon/drain）
  令牌     : aikube token create                      （kubeadm token create）
  资源查看 : aikube get nodes|pods|tasks              （kubectl get）
  任务提交 : aikube run <任务文本> [--mode|--replicas|--affinity|--watch]
  详情/日志: aikube describe <pod> | aikube logs <pod>
  删除     : aikube delete pod|task <name>
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .util import (api_url, die, free_port, home_dir, is_alive, load_cluster_conf,
                   load_kubeconfig, log, logs_dir, read_pid, rm_pid, run_dir,
                   save_cluster_conf, save_kubeconfig, write_pid, http)
from .apiserver import CONTROL_PLANE_API

PKG_DIR = Path(__file__).parent  # aikube 包目录
CLUSTER_ROOT = PKG_DIR.parent    # 包父目录（PYTHONPATH 锚点）

DEFAULT_MASTER = "k8s-master"
DEFAULT_NODES = ["k8s-node1", "k8s-node2"]
DEFAULT_PORTS = {"etcd": 12379, "apiserver": 16443}

# 博客拓扑的默认节点画像（1 主 2 从）
DEFAULT_PROFILES = {
    "k8s-master": {"role": "master", "model": "mock-pro",
                   "capabilities": ["plan", "spec", "classify"],
                   "slots": 2, "labels": {"role": "master"}, "runtime": "mock"},
    "k8s-node1": {"role": "worker", "model": "mock-flash",
                  "capabilities": ["execute", "react", "classify"],
                  "slots": 2, "labels": {"role": "worker", "gpu": "false"},
                  "runtime": "mock"},
    "k8s-node2": {"role": "worker", "model": "mock-pro",
                  "capabilities": ["plan", "spec", "execute"],
                  "slots": 2, "labels": {"role": "worker", "gpu": "true"},
                  "runtime": "mock"},
}

# ------------------------------------------------------------ 进程编排
def _spawn(name: str, args: list[str], wait_port: int | None = None,
           wait_health: str | None = None) -> None:
    """以独立进程启动一个集群组件（单机多进程模拟）。"""
    pid = read_pid(name)
    if is_alive(pid):
        log("aikube", f"{name} 已在运行 (pid={pid})")
        return
    logf = open(logs_dir() / f"{name}.log", "a", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(CLUSTER_ROOT)}
    proc = subprocess.Popen([sys.executable, "-m", "aikube", *args],
                            cwd=CLUSTER_ROOT, stdout=logf, stderr=logf,
                            start_new_session=True, env=env)
    write_pid(name, proc.pid)
    log("aikube", f"{name} 启动 (pid={proc.pid}, 日志: logs/{name}.log)")
    if wait_port:
        _wait_port(wait_port)
    if wait_health:
        _wait_health(wait_health, 30)


def _wait_port(port: int, timeout: float = 15) -> None:
    import socket
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    die(f"端口 {port} 等待超时")


def _wait_health(url: str, timeout: float = 30) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            http("GET", url, timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    die(f"{url} 健康检查超时")


def _stop(name: str) -> None:
    pid = read_pid(name)
    if is_alive(pid):
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass
        log("aikube", f"{name} 已停止 (pid={pid})")
    rm_pid(name)


def _proc_args_for(name: str, conf: dict) -> list[str]:
    tok = conf["token"]
    api = f"http://127.0.0.1:{conf['apiserver']['port']}"
    if name == "etcd":
        return ["etcd", "--port", str(conf["etcd"]["port"])]
    if name == "apiserver":
        return ["apiserver", "--port", str(conf["apiserver"]["port"]),
                "--etcd-port", str(conf["etcd"]["port"]), "--token", tok]
    if name == "scheduler":
        args = ["scheduler", "--api", api, "--token", tok]
        if conf.get("llm_url"):
            args += ["--llm-url", conf["llm_url"], "--llm-model",
                     conf.get("llm_model", "qwen2.5:7b")]
        return args
    if name == "controller":
        return ["controller", "--api", api, "--token", tok]
    if name in conf["nodes"]:
        prof = conf["nodes"][name]
        caps = ",".join(prof.get("capabilities", ["execute"]))
        labels = ",".join(f"{k}={v}" for k, v in prof.get("labels", {}).items())
        args = ["kubelet", "--node", name, "--api", api, "--token", tok,
                "--role", prof.get("role", "worker"),
                "--model", prof.get("model", "mock-flash"),
                "--capabilities", caps, "--slots", str(prof.get("slots", 2)),
                "--labels", labels, "--runtime", prof.get("runtime", "mock"),
                "--join-token", tok]
        if conf.get("llm_url") and prof.get("runtime") == "openai":
            args += ["--llm-url", conf["llm_url"]]
        return args
    die(f"未知组件: {name}")


# ------------------------------------------------------------ cluster 命令
def cmd_cluster_init(args: argparse.Namespace) -> None:
    if load_cluster_conf():
        die("集群已初始化（~/.aikube/cluster.json 存在）；如需重建请先 "
            "aikube cluster stop && 删除 ~/.aikube/cluster.json")
    token = secrets.token_hex(16)
    nodes = {}
    for n in [args.master, *args.nodes]:
        prof = dict(DEFAULT_PROFILES.get(n, {
            "role": "worker", "model": "mock-flash",
            "capabilities": ["execute", "react"],
            "slots": 2, "labels": {}, "runtime": "mock"}))
        prof["model"] = args.model_of.get(n, prof.get("model", "mock-flash"))
        nodes[n] = prof
    # 端口互斥解析：etcd 与 apiserver 回退到同一端口段时绝不取同一端口
    etcd_port = free_port(DEFAULT_PORTS["etcd"], base=args.port_base)
    apiserver_port = free_port(DEFAULT_PORTS["apiserver"], base=args.port_base,
                               exclude={etcd_port})
    conf = {
        "name": args.name,
        "created": time.time(),
        "token": token,
        "etcd": {"port": etcd_port},
        "apiserver": {"port": apiserver_port},
        "llm_url": args.llm_url,
        "llm_model": args.llm_model,
        "nodes": nodes,
    }
    save_cluster_conf(conf)
    save_kubeconfig({"server": f"http://127.0.0.1:{conf['apiserver']['port']}",
                     "token": token, "cluster": conf["name"]})
    log("aikube", f"集群「{conf['name']}」初始化完成，token={token[:8]}…")
    print(f"  kubeconfig: {home_dir() / 'config'}")
    print(f"  集群配置  : {home_dir() / 'cluster.json'}")
    print("  启动全部组件…")
    cmd_cluster_start(args)


def cmd_cluster_start(args: argparse.Namespace) -> None:
    conf = load_cluster_conf()
    if not conf:
        die("集群未初始化：先运行 aikube cluster init")
    api = f"http://127.0.0.1:{conf['apiserver']['port']}"
    _spawn("etcd", _proc_args_for("etcd", conf), wait_port=conf["etcd"]["port"])
    _spawn("apiserver", _proc_args_for("apiserver", conf),
           wait_port=conf["apiserver"]["port"], wait_health=api + "/healthz")
    _spawn("scheduler", _proc_args_for("scheduler", conf))
    _spawn("controller", _proc_args_for("controller", conf))
    for n in conf["nodes"]:
        _spawn(n, _proc_args_for(n, conf))
    time.sleep(1.5)
    print("集群已启动。节点状态：")
    cmd_get(argparse.Namespace(kind="nodes"))  # get nodes


def cmd_cluster_stop(args: argparse.Namespace) -> None:
    conf = load_cluster_conf()
    names = ["controller", "scheduler", "apiserver", "etcd"]
    if conf:
        names += list(conf["nodes"])
    for n in names:
        _stop(n)
    print("集群已停止")


def cmd_cluster_status(args: argparse.Namespace) -> None:
    conf = load_cluster_conf()
    if not conf:
        die("集群未初始化")
    print(f"集群: {conf['name']}  token: {conf['token'][:8]}…")
    for n in ["etcd", "apiserver", "scheduler", "controller", *conf["nodes"]]:
        pid = read_pid(n)
        state = "running" if is_alive(pid) else "stopped"
        print(f"  {n:<12} {state:<8} pid={pid or '-'}")


def cmd_node_join(args: argparse.Namespace) -> None:
    """aikube node join <token> —— kubeadm join 类比：校验 token 并加入节点。"""
    conf = load_cluster_conf()
    if not conf:
        die("集群未初始化")
    if args.token != conf["token"]:
        die("join token 无效（与集群 token 不匹配），如忘记可用 aikube token create")
    if args.node in conf["nodes"]:
        die(f"节点 {args.node} 已在集群中")
    prof = {"role": "worker", "model": args.model,
            "capabilities": [c.strip() for c in args.capabilities.split(",") if c.strip()],
            "slots": args.slots,
            "labels": dict(kv.split("=", 1) for kv in args.labels.split(",") if "=" in kv),
            "runtime": args.runtime}
    if args.tools:
        prof["tools"] = [t.strip() for t in args.tools.split(",") if t.strip()]
    conf["nodes"][args.node] = prof
    save_cluster_conf(conf)
    log("aikube", f"节点 {args.node} 加入集群（{args.model}）")
    _spawn(args.node, _proc_args_for(args.node, conf))
    time.sleep(1)
    cmd_get(args)


def cmd_token_create(args: argparse.Namespace) -> None:
    conf = load_cluster_conf()
    if not conf:
        die("集群未初始化")
    new = secrets.token_hex(16)
    conf["token"] = new
    save_cluster_conf(conf)
    save_kubeconfig({**load_kubeconfig(), "token": new})
    print(f"新 token: {new}")
    print("提示：token 变更后需重启 apiserver/scheduler/controller/kubelet 生效")


def cmd_node_cordon(args: argparse.Namespace) -> None:
    _node_mutate(args.node, "cordon", {"schedulable": not args.uncordon})


def cmd_node_drain(args: argparse.Namespace) -> None:
    conf = load_cluster_conf()
    api = api_url()
    tok = load_kubeconfig().get("token", "")
    http("POST", f"{api}/api/v1/nodes/{args.node}/cordon", {"schedulable": False},
         token=tok)
    # 驱逐该节点上所有进行中的 Pod
    for pod in http("GET", f"{api}/api/v1/pods?node={args.node}", token=tok)["items"]:
        if pod["phase"] in ("Running", "Scheduled"):
            http("POST", f"{api}/api/v1/pods/{pod['name']}/evict",
                 {"reason": "drain"}, token=tok)
            print(f"  驱逐 {pod['name']}")
    print(f"节点 {args.node} 已 drain（cordon + 驱逐，Pod 将被重调度）")


def _node_mutate(node: str, what: str, payload: dict) -> None:
    api = api_url()
    tok = load_kubeconfig().get("token", "")
    http("POST", f"{api}/api/v1/nodes/{node}/{what}", payload, token=tok)
    print(f"节点 {node} {what} 完成")


# ------------------------------------------------------------ get 命令
# 角色工具门控：执行面（worker）只保留与任务执行相关的命令，
# 控制面（control-plane）才有全部管理工具（控制面本身无执行类命令）。
WORKER_ALLOWED = {
    "get": {"pods"},          # 查看本集群 Pod（执行面可观测自己执行的任务）
    "logs": None,             # 任务输出
}

def cmd_get(args: argparse.Namespace) -> None:
    api = api_url()
    tok = load_kubeconfig().get("token", "")
    kind = args.kind
    if kind == "tools":
        return cmd_get_tools()
    if kind == "nodes":
        items = http("GET", f"{api}/api/v1/nodes", token=tok)["items"]
        print(f"{'NAME':<12}{'ROLE':<8}{'MODEL':<12}{'READY':<8}"
              f"{'SCHED':<8}{'LOAD':<6}{'LAT(ms)':<9}{'HEARTBEAT'}")
        for n in sorted(items.values(), key=lambda x: x["name"]):
            age = max(0, time.time() - n.get("heartbeat", 0))
            print(f"{n['name']:<12}{n.get('role',''):<8}{n.get('model',''):<12}"
                  f"{'Ready' if n.get('ready') else 'NotReady':<8}"
                  f"{'Yes' if n.get('schedulable', True) else 'No(cordon)':<8}"
                  f"{n.get('queue',0):<6}{n.get('latency_ms',0.0):<9.1f}"
                  f"{age:.0f}s")
    elif kind in ("pods", "tasks"):
        if kind == "pods":
            items = http("GET", f"{api}/api/v1/pods", token=tok)["items"]
            print(f"{'POD':<24}{'TASK':<20}{'MODE':<7}{'NODE':<12}"
                  f"{'PHASE':<10}{'TOOLS':<28}{'COST(s)'}")
            for p in sorted(items, key=lambda x: x["name"]):
                cost = p.get("cost_s", "-")
                tools = ",".join(p.get("tools_allowed") or []) or "-"
                print(f"{p['name']:<24}{p.get('task',''):<20}{p.get('mode',''):<7}"
                      f"{str(p.get('node') or '-'):<12}{p.get('phase',''):<10}"
                      f"{tools:<28}{cost}")
        else:
            items = http("GET", f"{api}/api/v1/tasks", token=tok)["items"]
            print(f"{'TASK':<24}{'MODE':<7}{'REPLICAS':<9}{'TEXT'}")
            for t in sorted(items.values(), key=lambda x: x["name"]):
                print(f"{t['name']:<24}{t.get('mode',''):<7}{t.get('replicas',1):<9}"
                      f"{t.get('text','')[:50]}")
    else:
        die(f"未知资源类型: {kind}（nodes|pods|tasks）")


def cmd_get_tools() -> None:
    """工具矩阵：控制面 API 面（最小集）+ 各节点工具白名单（最小权限）。"""
    print("=== 控制面 API 面（仅管理端点，无执行端点）===")
    for line in CONTROL_PLANE_API:
        print("  " + line)
    api = api_url()
    tok = load_kubeconfig().get("token", "")
    items = http("GET", f"{api}/api/v1/nodes", token=tok)["items"]
    print("\n=== 节点工具白名单（执行面最小权限）===")
    for n in sorted(items.values(), key=lambda x: x["name"]):
        tools = ", ".join(n.get("tools", [])) or "(无)"
        print(f"  {n['name']:<12} [{n.get('model',''):<10}] {tools}")
    print("\n=== 模式 → 任务所需工具（AI 分类推导）===")
    from .tools import MODE_TOOLS
    for mode, tools in MODE_TOOLS.items():
        print(f"  {mode:<7} {', '.join(tools)}")


# ------------------------------------------------------------ run 命令
def cmd_run(args: argparse.Namespace) -> None:
    api = api_url()
    tok = load_kubeconfig().get("token", "")
    text = " ".join(args.text)
    spec = {"text": text, "mode": args.mode, "replicas": args.replicas,
            "size": args.size}
    if args.tools:
        spec["tools"] = [t.strip() for t in args.tools.split(",") if t.strip()]
    if args.affinity:
        spec["affinity"] = dict(kv.split("=", 1) for kv in args.affinity.split(",")
                                if "=" in kv)
    r = http("POST", f"{api}/api/v1/tasks", spec, token=tok)
    task = r["task"]
    print(f"任务 {task['name']} 已提交（mode={task['mode']}，replicas={task['replicas']}）")
    print("  " + text[:80] + ("…" if len(text) > 80 else ""))
    if args.watch:
        while True:
            pods = http("GET", f"{api}/api/v1/pods", token=tok)["items"]
            mine = [p for p in pods if p["task"] == task["name"]]
            states = {p["name"]: p["phase"] for p in mine}
            print("  " + "  ".join(f"{k}={v}" for k, v in sorted(states.items())))
            if all(p["phase"] in ("Succeeded", "Failed") for p in mine):
                break
            time.sleep(1)


# ------------------------------------------------------------ describe/logs
def cmd_describe(args: argparse.Namespace) -> None:
    api = api_url()
    tok = load_kubeconfig().get("token", "")
    name = args.name
    pods = http("GET", f"{api}/api/v1/pods", token=tok)["items"]
    pod = next((p for p in pods if p["name"] == name), None)
    if not pod:
        die(f"Pod 不存在: {name}（可用 aikube get pods 查看）")
    print(f"Pod: {pod['name']}   任务: {pod['task']}")
    print(f"  模式: {pod['mode']}  阶段: {pod['phase']}  节点: {pod.get('node') or '-'}"
          f"  重调度: {pod.get('reschedule_count', 0)}")
    print(f"  工具: 请求={pod.get('tools_requested') or '-'}  "
          f"授权={pod.get('tools_allowed') or '-'}")
    cls = pod.get("classification")
    if cls:
        print(f"  AI 分类: {cls.get('mode')} (置信 {cls.get('confidence')}, "
              f"来源 {cls.get('source')})")
    dec = pod.get("decision")
    if dec:
        print(f"  调度策略: {dec.get('strategy')}")
        print(f"  调度时刻: {time.strftime('%H:%M:%S', time.localtime(dec.get('time', 0)))}")
        print(f"  工具需求: {dec.get('tools_required')}  授权: {dec.get('tools_allowed')}")
        for n, s in sorted(dec.get("scores", {}).items(), key=lambda x: -x[1]["total"]):
            mark = "← 选中" if n == dec.get("winner") else ""
            print(f"    {n:<12} total={s['total']:.2f} "
                  f"(load={s['load']:.2f} cap={s['capability']:.2f} "
                  f"aff={s['affinity']:.2f} lat={s['latency']:.2f}) {mark}")
        flt = dec.get("filtered") or {}
        if flt:
            print("  被过滤节点:")
            for n, why in flt.items():
                print(f"    {n:<12} {why}")
    print("  事件:")
    for e in pod.get("events", [])[-8:]:
        print(f"    {time.strftime('%H:%M:%S', time.localtime(e['t']))} {e['what']}")
    if pod.get("output"):
        print(f"  输出: {pod['output'][:200]}")
    if pod.get("error"):
        print(f"  错误: {pod['error']}")


def cmd_logs(args: argparse.Namespace) -> None:
    api = api_url()
    tok = load_kubeconfig().get("token", "")
    name = args.name
    pods = http("GET", f"{api}/api/v1/pods", token=tok)["items"]
    pod = next((p for p in pods if p["name"] == name), None)
    if not pod:
        die(f"Pod 不存在: {name}")
    out = pod.get("output") or pod.get("error") or "(无输出)"
    print(out)


def cmd_delete(args: argparse.Namespace) -> None:
    api = api_url()
    tok = load_kubeconfig().get("token", "")
    if args.kind == "pod":
        http("POST", f"{api}/api/v1/pods/{args.name}/evict", {"reason": "manual"},
             token=tok)
        print(f"Pod {args.name} 已驱逐（控制器将按 replicas 补建或重调度）")
    elif args.kind == "task":
        pods = http("GET", f"{api}/api/v1/pods", token=tok)["items"]
        for p in pods:
            if p["task"] == args.name:
                http("POST", f"{api}/api/v1/pods/{p['name']}/evict",
                     {"reason": "task deleted"}, token=tok)
        print(f"任务 {args.name} 的所有 Pod 已驱逐（任务本体保留在 etcd）")
    else:
        die("aikube delete pod|task <name>")


# ------------------------------------------------------------ 入口
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="aikube",
        description="AI K8s 集群调度网络 — dsh-routing-suite/cluster 组件")
    ap.add_argument("--version", action="version", version=f"aikube {__version__}")
    ap.add_argument("--role", choices=["control-plane", "worker"],
                    default=os.environ.get("AIKUBE_ROLE", "control-plane"),
                    help="工具门控角色：worker 只保留任务执行相关命令")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # cluster
    p = sub.add_parser("cluster", help="集群生命周期（kubeadm init 类比）")
    cs = p.add_subparsers(dest="sub", required=True)
    pi = cs.add_parser("init", help="初始化并启动 1 主 N 从 集群")
    pi.add_argument("--name", default="demo")
    pi.add_argument("--master", default=DEFAULT_MASTER)
    pi.add_argument("--nodes", nargs="*", default=list(DEFAULT_NODES))
    pi.add_argument("--port-base", type=int, default=16000)
    pi.add_argument("--llm-url", default=os.environ.get("AIKUBE_LLM_URL"))
    pi.add_argument("--llm-model", default="qwen2.5:7b")
    pi.add_argument("--model", action="append", default=[],
                    help="覆盖节点模型，格式 node=model，如 k8s-node1=mock-pro")
    pi.set_defaults(func=cmd_cluster_init)
    p2 = cs.add_parser("start", help="按已有配置启动全部组件")
    p2.set_defaults(func=cmd_cluster_start, kind="nodes")
    p3 = cs.add_parser("stop", help="停止全部组件")
    p3.set_defaults(func=cmd_cluster_stop)
    p4 = cs.add_parser("status", help="查看组件进程状态")
    p4.set_defaults(func=cmd_cluster_status)

    # node
    p = sub.add_parser("node", help="节点操作（kubeadm join / kubectl cordon 类比）")
    ns = p.add_subparsers(dest="sub", required=True)
    pj = ns.add_parser("join", help="节点加入集群")
    pj.add_argument("token")
    pj.add_argument("--node", required=True)
    pj.add_argument("--model", default="mock-flash")
    pj.add_argument("--capabilities", default="execute,react")
    pj.add_argument("--slots", type=int, default=2)
    pj.add_argument("--labels", default="")
    pj.add_argument("--runtime", default="mock")
    pj.add_argument("--tools", default="",
                    help="节点工具白名单覆盖，如 file_read,code_exec（默认按能力推导最小集）")
    pj.set_defaults(func=cmd_node_join, kind="nodes")
    for sub_name, uncordon in (("cordon", False), ("uncordon", True)):
        pc = ns.add_parser(sub_name, help=f"{'解除' if uncordon else ''}节点不可调度")
        pc.add_argument("node")
        pc.set_defaults(func=cmd_node_cordon, uncordon=uncordon)
    pd = ns.add_parser("drain", help="节点维护：cordon + 驱逐 Pod")
    pd.add_argument("node")
    pd.set_defaults(func=cmd_node_drain)

    # token
    p = sub.add_parser("token", help="集群令牌")
    ts = p.add_subparsers(dest="sub", required=True)
    tc = ts.add_parser("create", help="创建新 join token（kubeadm token create）")
    tc.set_defaults(func=cmd_token_create)

    # get
    pg = sub.add_parser("get", help="查看资源（kubectl get）")
    pg.add_argument("kind", choices=["nodes", "pods", "tasks", "tools"])
    pg.set_defaults(func=cmd_get)

    # run
    pr = sub.add_parser("run", help="提交任务（kubectl run）")
    pr.add_argument("text", nargs="+")
    pr.add_argument("--mode", choices=["auto", "spec", "react", "mixed", "weak"],
                    default="auto")
    pr.add_argument("--replicas", type=int, default=1)
    pr.add_argument("--size", choices=["small", "medium", "large"], default="small")
    pr.add_argument("--affinity", default="", help="节点标签亲和，如 gpu=true")
    pr.add_argument("--tools", default="",
                    help="任务显式请求的工具，如 file_read,code_exec（默认按模式推导）")
    pr.add_argument("--watch", action="store_true")
    pr.set_defaults(func=cmd_run)

    # describe / logs / delete
    p = sub.add_parser("describe", help="查看 Pod 调度详情（kubectl describe）")
    p.add_argument("name")
    p.set_defaults(func=cmd_describe)
    p = sub.add_parser("logs", help="查看 Pod 输出（kubectl logs）")
    p.add_argument("name")
    p.set_defaults(func=cmd_logs)
    p = sub.add_parser("delete", help="删除资源")
    p.add_argument("kind", choices=["pod", "task"])
    p.add_argument("name")
    p.set_defaults(func=cmd_delete)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # --model node=model 覆盖（cluster init）
    args.model_of = {}
    for m in getattr(args, "model", []) or []:
        if "=" in m:
            k, v = m.split("=", 1)
            args.model_of[k] = v
    # 角色工具门控：执行面节点只保留与任务执行相关的工具
    role = getattr(args, "role", None) or os.environ.get("AIKUBE_ROLE", "control-plane")
    if role not in ("control-plane", "worker"):
        die(f"未知角色: {role}（control-plane|worker）")
    if role == "worker":
        allowed_kinds = WORKER_ALLOWED.get(args.cmd)
        if args.cmd == "get" and args.kind in allowed_kinds:
            pass  # 执行面允许：get pods
        elif args.cmd == "logs":
            pass
        else:
            die(f"执行面角色(worker)禁止命令: {args.cmd} {getattr(args, 'kind', '')} —— "
                f"仅保留 get pods / logs（与任务执行无关的工具一律不保留）")
    args.func(args)


if __name__ == "__main__":
    main()
