#!/usr/bin/env python3
"""eval-trajectory — AI 集群调度网络 轨迹评估门禁（方案 3：Trajectory Evaluation）。

语义对齐 LangChain `load_evaluator("trajectory")`：
对 agent 执行轨迹按 逻辑性/效率/正确性/工具选择 四维打分（LLM judge，
默认本机 ollama OpenAI 兼容端点），评估"装有 dsh-aikube 的 agent"的调度质量。

场景集（spec/react/mixed/weak × 正常/越权/故障）：
  N-spec / N-react / N-mixed / N-weak   正常调度：分类→路由→工具授权→执行
  A-react-extra-tool  越权：react 任务请求 file_write（react 模式不含）→
                       手工绑定到无该工具的节点 → 沙箱拒绝留痕
  A-spec-code-exec    越权：spec 任务显式请求 code_exec → 调度器工具覆盖过滤
                       （计划节点无执行工具 → 路由到全能节点）
  F-kill-node         故障：任务运行中 kill 节点 kubelet → 驱逐 → requeue → 重调度

用法：
  python3 cluster/scripts/eval-trajectory.py [--judge-model qwen2.5:1.5b]
      [--llm-url http://127.0.0.1:11434/v1] [--out docs/eval]
      [--home <AIKUBE_HOME>]   # 默认临时目录，跑完清理
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # cluster/
PYTHON_DIR = ROOT / "aikube"
sys.path.insert(0, str(ROOT))                       # 本进程可直接 import aikube

JUDGE_DIMENSIONS = ["logic", "efficiency", "correctness", "tool_selection"]

JUDGE_PROMPT = """你是 AI 集群调度网络的轨迹评估员（trajectory evaluator）。
评估下面这条真实任务轨迹的质量，从四个维度打分（每项 0-10）并给出理由：

1. logic（逻辑性）：分类→路由→工具授权→执行 的决策链是否合理
2. efficiency（效率）：是否以最少必要步骤/工具完成（工具最小权限是否被遵守）
3. correctness（正确性）：任务是否被正确分类、路由到能力匹配节点、成功完成
4. tool_selection（工具选择）：每一步是否用了合适的工具；越权调用是否被正确拒绝

只输出 JSON：{{"logic": 分数, "efficiency": 分数, "correctness": 分数,
"tool_selection": 分数, "overall": 平均分, "reason": "简要理由"}}

任务轨迹（JSON）：
{trajectory}
"""

# ---------------------------------------------------------------- 工具
class Cluster:
    """临时集群封装：独立 AIKUBE_HOME + 独立端口段，跑完 stop + 清理。"""

    def __init__(self, port_base: int = 26000):
        self.home = Path(tempfile.mkdtemp(prefix="aikube-eval-"))
        self.port_base = port_base
        self._api = None
        os.environ["AIKUBE_HOME"] = str(self.home)  # 本进程内所有读取指向临时集群

    def cli(self, *args: str, timeout: float = 90) -> str:
        env = {**os.environ, "PYTHONPATH": str(PYTHON_DIR),
               "AIKUBE_HOME": str(self.home)}
        r = subprocess.run([sys.executable, "-m", "aikube", *args],
                           capture_output=True, text=True, timeout=timeout,
                           env=env, cwd=str(ROOT))
        if r.returncode != 0:
            raise RuntimeError(f"aikube {' '.join(args)} 失败: {r.stderr[-500:]}")
        return r.stdout

    def start(self) -> None:
        self.cli("cluster", "init", "--name", "eval",
                 "--nodes", "k8s-node1", "k8s-node2",
                 "--port-base", str(self.port_base))

    def stop(self) -> None:
        try:
            self.cli("cluster", "stop")
        except Exception:
            pass

    def submit(self, text: str, mode: str = "auto", tools: str = "",
               size: str = "small") -> str:
        args = ["run", text, "--mode", mode, "--size", size]
        if tools:
            args += ["--tools", tools]
        return self.cli(*args).split("任务 ")[1].split(" ")[0]

    def wait_pod(self, pod: str, timeout: float = 60) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            pods = self.pods()
            p = next((x for x in pods if x["name"] == pod), None)
            if p and p["phase"] in ("Succeeded", "Failed"):
                return p
            time.sleep(1)
        raise TimeoutError(f"pod {pod} 未终态: {[ (x['name'], x['phase']) for x in self.pods() ]}")

    def pods(self) -> list[dict]:
        env = {**os.environ, "PYTHONPATH": str(PYTHON_DIR),
               "AIKUBE_HOME": str(self.home)}
        from aikube.util import http, load_kubeconfig
        cfg = load_kubeconfig()
        if not cfg.get("server"):
            self.cli("cluster", "status")
            cfg = load_kubeconfig()
        return http("GET", cfg["server"] + "/api/v1/pods",
                    token=cfg.get("token", ""))["items"]

    def bind_manual(self, pod: str, node: str, tools_required: list[str]) -> None:
        """绕过调度器手工绑定（模拟陈旧绑定，用于越权场景）。"""
        from aikube.util import http, load_kubeconfig
        cfg = load_kubeconfig()
        http("POST", cfg["server"] + f"/api/v1/pods/{pod}/bind",
             {"node": node, "decision": {"winner": node, "scores": {},
                                         "filtered": {}, "tools_required": tools_required}},
             token=cfg.get("token", ""))
        # 同侧 cordon 全部节点，防止调度器抢绑定
        for n in http("GET", cfg["server"] + "/api/v1/nodes",
                      token=cfg.get("token", ""))["items"]:
            http("POST", cfg["server"] + f"/api/v1/nodes/{n}/cordon",
                 {"schedulable": False}, token=cfg.get("token", ""))
        # 手工绑定后恢复可调度
        for n in http("GET", cfg["server"] + "/api/v1/nodes",
                      token=cfg.get("token", ""))["items"]:
            http("POST", cfg["server"] + f"/api/v1/nodes/{n}/cordon",
                 {"schedulable": True}, token=cfg.get("token", ""))

    def kill_kubelet(self, node: str) -> None:
        pidfile = self.home / "run" / f"{node}.pid"
        if pidfile.exists():
            os.kill(int(pidfile.read_text().strip()), 9)


def ask_judge(prompt: str, llm_url: str, model: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 500,
    }).encode()
    req = urllib.request.Request(llm_url.rstrip("/") + "/chat/completions",
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode())
    content = data["choices"][0]["message"]["content"].strip()
    if not content:  # reasoning 模型思考链占满 content 时重试加大 token
        return {"error": "judge 返回空 content（换非 reasoning 模型或加大 max_tokens）"}
    start, end = content.find("{"), content.rfind("}")
    if start < 0:
        return {"error": f"judge 未返回 JSON: {content[:150]}"}
    return json.loads(content[start:end + 1])


def build_trajectory(pod: dict) -> dict:
    dec = pod.get("decision") or {}
    steps = [{"step": 1, "action": "ai-classify",
              "detail": json.dumps(pod.get("classification"), ensure_ascii=False)}]
    steps.append({"step": 2, "action": "ai-schedule",
                  "detail": f"winner={dec.get('winner')}, "
                            f"tools_required={dec.get('tools_required')}, "
                            f"tools_allowed={dec.get('tools_allowed')}"})
    for i, t in enumerate(pod.get("tool_log", []), start=3):
        steps.append({"step": i, "action": f"tool.{t['tool']}",
                      "detail": f"allowed={t['allowed']}, result={str(t.get('result'))[:60]}"})
    for e in pod.get("events", []):
        what = e.get("what", "")
        if "evict" in what or "requeue" in what or "bind" in what:
            steps.append({"step": len(steps) + 1, "action": "controller",
                          "detail": what[:80]})
    return {"input": f"任务(pod={pod['name']}, mode={pod.get('mode')})",
            "trajectory": steps,
            "final_output": (pod.get("output") or pod.get("error") or "")[:150],
            "meta": {"pod": pod.get("name"), "node": pod.get("node"),
                     "mode": pod.get("mode"), "phase": pod.get("phase"),
                     "reschedule": pod.get("reschedule_count", 0)}}


# ---------------------------------------------------------------- 场景
def scenario_normal(cluster: Cluster, mode: str, text: str, size: str = "small") -> dict:
    task = cluster.submit(text, mode=mode, size=size)
    pod = cluster.wait_pod(f"{task}-01")
    return {"name": f"N-{mode}", "text": text, "pod": pod}

def scenario_react_extra_tool(cluster: Cluster) -> dict:
    """越权：react 任务显式请求 file_write（react 模式不含），
    手工绑定到无 file_write 的 kube-node1 → 沙箱拒绝并留痕。"""
    from aikube.util import http, load_kubeconfig
    cfg = load_kubeconfig()
    api, tok = cfg["server"], cfg.get("token", "")
    # cordon 全部节点防调度器抢
    for n in http("GET", f"{api}/api/v1/nodes", token=tok)["items"]:
        http("POST", f"{api}/api/v1/nodes/{n}/cordon",
             {"schedulable": False}, token=tok)
    r = http("POST", f"{api}/api/v1/tasks",
             {"text": "执行修复任务", "mode": "react",
              "tools": ["file_write", "code_exec"]}, token=tok)
    pod_name = r["task"]["name"] + "-01"
    http("POST", f"{api}/api/v1/pods/{pod_name}/bind",
         {"node": "k8s-node1", "decision": {"winner": "k8s-node1", "scores": {},
                                            "filtered": {},
                                            "tools_required": ["file_write", "code_exec"]}},
         token=tok)
    for n in http("GET", f"{api}/api/v1/nodes", token=tok)["items"]:
        http("POST", f"{api}/api/v1/nodes/{n}/cordon",
             {"schedulable": True}, token=tok)
    pod = cluster.wait_pod(pod_name)
    return {"name": "A-react-extra-tool", "text": "react 任务越权请求 file_write",
            "pod": pod}

def scenario_spec_code_exec(cluster: Cluster) -> dict:
    """越权：spec 任务显式请求 code_exec → 调度器工具覆盖过滤（计划节点无执行工具）。"""
    task = cluster.submit("编写代码修复方案", mode="spec", tools="code_exec")
    pod = cluster.wait_pod(f"{task}-01")
    return {"name": "A-spec-code-exec", "text": "spec 任务显式请求 code_exec", "pod": pod}

def scenario_kill_node(cluster: Cluster) -> dict:
    """故障：任务运行中 kill 节点 kubelet → 驱逐 → requeue → 重调度。"""
    task = cluster.submit("重构支付模块并输出完整实施方案", mode="spec", size="large")
    pod_name = f"{task}-01"
    t0 = time.time()
    pod = None
    while time.time() - t0 < 20:
        p = next((x for x in cluster.pods() if x["name"] == pod_name), None)
        if p and p["phase"] == "Running":
            pod = p
            break
        time.sleep(0.5)
    if pod is None:
        raise RuntimeError("任务未进入 Running")
    cluster.kill_kubelet(pod["node"])
    pod = cluster.wait_pod(pod_name, timeout=90)
    return {"name": "F-kill-node", "text": f"运行中 kill {pod['node']} kubelet",
            "pod": pod}


SCENARIOS = [
    ("N-spec", lambda c: scenario_normal(c, "spec", "设计一个微服务架构上线方案")),
    ("N-react", lambda c: scenario_normal(c, "react", "修复登录页面的 bug")),
    ("N-mixed", lambda c: scenario_normal(c, "mixed", "开发一个小游戏然后修复其中的 bug")),
    ("N-weak", lambda c: scenario_normal(c, "weak", "这个报错是什么意思？")),
    ("A-react-extra-tool", scenario_react_extra_tool),
    ("A-spec-code-exec", scenario_spec_code_exec),
    ("F-kill-node", scenario_kill_node),
]


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="AI 集群调度轨迹评估门禁")
    ap.add_argument("--judge-model", default=os.environ.get("AIKUBE_JUDGE_MODEL", "qwen2.5:1.5b"))
    ap.add_argument("--llm-url", default=os.environ.get("AIKUBE_LLM_URL", "http://127.0.0.1:11434/v1"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "eval"))
    ap.add_argument("--home", default=None, help="复用已有集群（不新建）")
    args = ap.parse_args()

    cluster = Cluster() if not args.home else None
    if cluster:
        cluster.start()
        time.sleep(3)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {"tool": "eval-trajectory", "judge_model": args.judge_model,
              "llm_url": args.llm_url, "scenarios": []}
    print(f"== 轨迹评估门禁（judge: {args.judge_model}）==")
    for name, fn in SCENARIOS:
        try:
            sc = fn(cluster)
            traj = build_trajectory(sc["pod"])
            score = ask_judge(JUDGE_PROMPT.format(
                trajectory=json.dumps(traj, ensure_ascii=False)),
                args.llm_url, args.judge_model)
            entry = {"name": sc["name"], "text": sc["text"],
                     "meta": traj["meta"], "score": score,
                     "trajectory": traj["trajectory"]}
            report["scenarios"].append(entry)
            overall = score.get("overall", "ERR")
            print(f"  [{name}] overall={overall} "
                  f"node={traj['meta']['node']} phase={traj['meta']['phase']} "
                  f"reschedule={traj['meta']['reschedule']}")
        except Exception as e:
            report["scenarios"].append({"name": name, "error": str(e)})
            print(f"  [{name}] 场景失败: {e}")

    if cluster:
        cluster.stop()
    # 汇总
    scored = [s for s in report["scenarios"] if "score" in s]
    ok_scores = [s["score"]["overall"] for s in scored
                 if isinstance(s["score"].get("overall"), (int, float))]
    report["summary"] = {
        "total": len(SCENARIOS), "scored": len(scored),
        "avg_overall": round(sum(ok_scores) / len(ok_scores), 2) if ok_scores else None,
        "min_overall": min(ok_scores) if ok_scores else None,
    }
    json_path = out_dir / "trajectory-eval.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# AI 集群调度轨迹评估报告",
          "", f"- judge: `{args.judge_model}` @ {args.llm_url}",
          f"- 场景: {report['summary']['total']}（评分 {report['summary']['scored']}）",
          f"- 平均 overall: {report['summary']['avg_overall']} / 10",
          f"- 最低 overall: {report['summary']['min_overall']} / 10", ""]
    for s in report["scenarios"]:
        md.append(f"## {s['name']}")
        md.append(f"- 任务: {s.get('text', '')}")
        if "error" in s:
            md.append(f"- ❌ 场景失败: {s['error']}")
            continue
        meta = s["meta"]
        md.append(f"- Pod {meta['pod']} @ {meta['node']} phase={meta['phase']} "
                  f"reschedule={meta['reschedule']}")
        sc = s["score"]
        if "error" in sc:
            md.append(f"- ⚠️ judge: {sc['error']}")
        else:
            md.append(f"- overall **{sc['overall']}**/10 — "
                      f"logic {sc['logic']} / efficiency {sc['efficiency']} / "
                      f"correctness {sc['correctness']} / tool_selection {sc['tool_selection']}")
            md.append(f"- reason: {sc.get('reason', '')}")
        md.append("- 轨迹:")
        for st in s["trajectory"]:
            md.append(f"  - {st['step']}. {st['action']}: {st['detail'][:100]}")
        md.append("")
    (out_dir / "trajectory-eval.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n报告已写入: {out_dir / 'trajectory-eval.md'} / trajectory-eval.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
