"""ai-scheduler — 集群调度器（类比 kube-scheduler），AI 调度网络的大脑。

调度流水线（对齐 kube-scheduler 的 Filter → Score → Bind）：
1. AI 分类（可选）：Pending Pod 若 mode=auto，先用 LLM（或规则）把任务文本
   分类为 spec / react / mixed / weak，并提取所需能力 —— 复用套装 router-standard
   的"任务感知路由"思想：Pro 节点吃 spec/计划，Flash 节点吃 react/执行。
2. Filter：节点必须 Ready 且可调度，且能力/标签/槽位满足任务。
3. Score：加权打分（负载 40 + 能力匹配 30 + 亲和性 20 + 延迟 10），
   记录每节点打分明细（kubectl describe 可见，类比 kube-scheduler 日志）。
4. Bind：写回 apiserver，通知 kubelet 执行。
"""
from __future__ import annotations

import argparse
import json
import time
from urllib import request, error

from .util import http, log, free_port, api_url
from .tools import MODE_TOOLS, TOOL_REGISTRY

WEIGHTS = {"load": 0.40, "capability": 0.30, "affinity": 0.20, "latency": 0.10}

# 模式 ↔ 节点模型能力 的匹配表（与 router-standard 的 persona 选择一致）
MODE_CAPABILITY = {
    "spec": ["plan", "spec"],       # Pro 节点：计划-集体
    "react": ["execute", "react"],  # Flash 节点：执行者
    "mixed": ["execute", "plan"],   # 任何全能节点
    "weak": ["classify"],           # 模型自分类
}
# 模式 ↔ 默认偏好模型
MODE_MODEL = {"spec": "pro", "react": "flash", "mixed": "pro", "weak": "neutral"}


class Classifier:
    """AI 任务分类器：LLM 优先，规则兜底（无 LLM 也能全链路跑通）。"""

    def __init__(self, llm_url: str | None = None, llm_model: str | None = None,
                 enabled: bool = True):
        self.llm_url = llm_url
        self.llm_model = llm_model or "qwen2.5:7b"
        self.enabled = enabled

    # ------------------------------------------------------------ 规则分类
    @staticmethod
    def rules(text: str) -> dict:
        t = text.strip()
        n = len(t)
        has_q = any(ch in t for ch in "？?")
        action_words = ("写", "生成", "执行", "实现", "修复", "翻译", "总结",
                        "列出", "计算", "运行", "部署", "convert", "run", "fix",
                        "write", "generate", "implement")
        plan_words = ("方案", "计划", "设计", "规划", "步骤", "架构", "流程",
                      "plan", "design", "spec", "roadmap", "strategy")
        if any(w in t for w in plan_words):
            return {"mode": "spec", "confidence": 0.85, "source": "rules"}
        if has_q and n < 80:
            return {"mode": "weak", "confidence": 0.60, "source": "rules"}
        if any(t.startswith(w) or f" {w} " in f" {t} " for w in action_words):
            return {"mode": "react", "confidence": 0.80, "source": "rules"}
        if n > 200:
            return {"mode": "mixed", "confidence": 0.55, "source": "rules"}
        return {"mode": "react", "confidence": 0.50, "source": "rules"}

    # ------------------------------------------------------------ LLM 分类
    def llm(self, text: str) -> dict:
        prompt = (
            "你是 AI 集群调度器的任务分类器。把任务分类为四选一："
            "spec(需要计划/深度思考)、react(直接执行)、mixed(先试错再收敛)、"
            "weak(模糊不清)。只输出 JSON: {\"mode\": \"...\", "
            "\"capabilities\": [\"plan\"|\"execute\"|\"classify\"], "
            "\"confidence\": 0.0-1.0}\n任务: " + text[:500]
        )
        body = json.dumps({
            "model": self.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 64,
        }).encode()
        req = request.Request(self.llm_url.rstrip("/") + "/chat/completions",
                              data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"].strip()
        # 容错提取 JSON
        start, end = content.find("{"), content.rfind("}")
        obj = json.loads(content[start:end + 1]) if start >= 0 else {}
        mode = obj.get("mode", "weak")
        if mode not in MODE_MODEL:
            mode = "weak"
        caps = obj.get("capabilities") or MODE_CAPABILITY[mode]
        return {"mode": mode, "confidence": float(obj.get("confidence", 0.7)),
                "source": "llm", "capabilities": caps}

    def classify(self, text: str) -> dict:
        if self.enabled and self.llm_url:
            try:
                return self.llm(text)
            except Exception as e:
                log("ai-scheduler", f"LLM 分类失败({e})，回退规则分类")
        return self.rules(text)


class Scheduler:
    def __init__(self, api: "Api", classifier: Classifier,
                 interval: float = 1.0, dry_run: bool = False):
        self.api = api
        self.classifier = classifier
        self.interval = interval
        self.dry_run = dry_run

    # ------------------------------------------------------------- 调度
    def schedule_once(self) -> int:
        scheduled = 0
        for pod in self.api.pods(phase="Pending"):
            if self._schedule_pod(pod):
                scheduled += 1
        return scheduled

    def _schedule_pod(self, pod: dict) -> bool:
        # 1. AI 分类（仅 mode=auto 时；显式模式必须保留）
        if pod["mode"] == "auto":
            cls = self.classifier.classify(pod.get("text", ""))
            pod["mode"] = cls["mode"]
            pod["classification"] = cls
            self.api.set_pod_phase(pod["name"], "Pending",
                                   mode=cls["mode"], classification=cls)

        # 2. Filter：候选节点
        candidates = []
        for name, node in self.api.nodes().items():
            reason = self._filter(name, node, pod)
            if reason is None:
                candidates.append(name)
            else:
                pod.setdefault("filtered", {})[name] = reason

        if not candidates:
            log("ai-scheduler", f"{pod['name']} 无可用节点: {pod.get('filtered')}")
            return False

        # 3. Score：加权打分
        scores = {}
        for name in candidates:
            node = self.api.nodes()[name]
            scores[name] = self._score(node, pod)

        # 4. Bind（同分时：负载更低者优先 —— 确定性决策）
        best = max(scores, key=lambda n: (scores[n]["total"], -scores[n]["load"]))
        required = self._required_tools(pod)
        decision = {
            "time": time.time(),
            "filtered": pod.get("filtered", {}),
            "scores": scores,
            "winner": best,
            "weights": WEIGHTS,
            "classification": pod.get("classification"),
            "tools_required": required,
            "tools_allowed": sorted(set(required) &
                                    set(self.api.nodes()[best].get("tools", []))),
            "strategy": "AI 加权调度 (负载40/能力30/亲和20/延迟10) + 工具最小权限",
        }
        log("ai-scheduler",
            f"{pod['name']} [{pod['mode']}] -> {best} "
            f"(score={scores[best]['total']:.2f})")
        if not self.dry_run:
            self.api.bind_pod(pod["name"], best, decision)
        return True

    # --------------------------------------------------------- Filter 规则
    @staticmethod
    def _required_tools(pod: dict) -> list[str]:
        """任务所需工具：显式请求优先，否则按 AI 分类的模式默认集。"""
        requested = pod.get("tools_requested") or []
        if requested:
            return sorted(set(requested))
        return MODE_TOOLS.get(pod.get("mode", "react"), MODE_TOOLS["react"])

    def _filter(self, name: str, node: dict, pod: dict) -> str | None:
        if not node.get("ready"):
            return f"NotReady"
        if not node.get("schedulable", True):
            return "Unschedulable(被 cordon)"
        if node.get("queue", 0) >= node.get("slots", 1):
            return "槽位已满"
        # 亲和性：任务要求的标签节点必须有
        for k, v in pod.get("affinity", {}).items():
            if node.get("labels", {}).get(k) != v:
                return f"无标签 {k}={v}"
        # 能力匹配：节点能力须覆盖任务模式所需
        need = MODE_CAPABILITY.get(pod["mode"], ["execute"])
        node_caps = set(node.get("capabilities", []))
        if not node_caps.intersection(need):
            return f"能力不足(需{need})"
        # 工具最小权限：节点白名单须覆盖任务所需全部工具
        node_tools = set(node.get("tools") or [])
        required = self._required_tools(pod)
        missing = [t for t in required if t not in node_tools]
        if missing:
            return f"工具不足(缺 {', '.join(missing)})"
        return None

    # --------------------------------------------------------- Score 评分
    def _score(self, node: dict, pod: dict) -> dict:
        queue, slots = node.get("queue", 0), max(node.get("slots", 1), 1)
        load_score = max(0.0, 1.0 - queue / slots)          # 0.0~1.0
        # 能力匹配：偏好模型命中得满分，通用能力次之
        pref = MODE_MODEL.get(pod["mode"], "neutral")
        caps = set(node.get("capabilities", []))
        need = set(MODE_CAPABILITY.get(pod["mode"], ["execute"]))
        model = node.get("model", "")
        if pref == "neutral" or pref in model:
            cap_score = 1.0
        elif caps.intersection(need):
            cap_score = 0.6
        else:
            cap_score = 0.0
        # 亲和性：标签匹配度
        labels = node.get("labels", {})
        aff = pod.get("affinity", {})
        hits = sum(1 for k, v in aff.items() if labels.get(k) == v)
        aff_score = hits / max(len(aff), 1) if aff else 0.7
        # 延迟：越小越好
        lat = node.get("latency_ms", 0.0) or 0.0
        lat_score = max(0.0, 1.0 - lat / 5000.0)
        total = (WEIGHTS["load"] * load_score + WEIGHTS["capability"] * cap_score
                 + WEIGHTS["affinity"] * aff_score + WEIGHTS["latency"] * lat_score)
        return {
            "total": round(total, 4),
            "load": round(load_score, 4), "capability": round(cap_score, 4),
            "affinity": round(aff_score, 4), "latency": round(lat_score, 4),
        }

    def run(self) -> None:
        log("ai-scheduler", f"调度循环启动 (interval={self.interval}s)")
        while True:
            try:
                self.schedule_once()
            except Exception as e:
                log("ai-scheduler", f"调度异常: {e}")
            time.sleep(self.interval)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="aikube scheduler")
    ap.add_argument("--api", default=api_url())
    ap.add_argument("--token", default="")
    ap.add_argument("--llm-url", default=None)
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from .apiserver import Api
    api = Api({"url": args.api, "token": args.token}, remote=True)
    cls = Classifier(args.llm_url, args.llm_model, enabled=bool(args.llm_url))
    Scheduler(api, cls, args.interval, args.dry_run).run()


if __name__ == "__main__":
    main()
