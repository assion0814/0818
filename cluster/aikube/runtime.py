"""ai-runtime — 容器运行时类比（博客中的 containerd）。

kubelet 在节点上执行 Pod 时调用 runtime：
- mock    : 内置模拟执行（默认，离线可跑通全链路）
- openai  : 调用 OpenAI 兼容接口（本地 ollama http://127.0.0.1:11434/v1 亦可），
            执行真正的大模型推理，作为"真实容器镜像"。
"""
from __future__ import annotations

import json
import time
from urllib import request, error

MOCK_OUTPUTS = {
    "spec": "【计划完成】已产出结构化方案（步骤/风险/验收），等待执行确认。",
    "react": "【执行完成】已直接产出结果并附关键依据，无冗余过程。",
    "mixed": "【混合完成】先快速试错收敛，再给出带计划的最终结论。",
    "weak": "【分类完成】任务模糊，已按默认分类并给出保守答复。",
}

MODEL_MOCK_NAMES = {"pro": "mock-pro", "flash": "mock-flash"}

# 每个工具的默认调用参数（确定性 exercise）
DEFAULT_TOOL_ARGS = {
    "file_read": {"path": "notes.txt"},
    "file_write": {"path": "notes.txt", "content": "AI 集群任务笔记"},
    "math_calc": {"expr": "2+3*4"},
    "classify_text": {},
    "summarize_text": {},
    "web_fetch": {"url": "https://example.com/docs"},
    "code_exec": {"code": "print('ok')"},
}


class Runtime:
    """执行器：kubelet 调用 run() 执行一个 Pod。

    sandbox 非空时，Pod 的工具调用在 ToolSandbox 内执行（最小权限：
    只放行 tools_allowed，越权工具返回拒绝并留痕）。
    """

    def __init__(self, kind: str = "mock", llm_url: str | None = None,
                 model: str | None = None, llm_api_key: str | None = None,
                 base_ms: int = 800, jitter_ms: int = 400):
        self.kind = kind
        self.llm_url = llm_url or "http://127.0.0.1:11434/v1"
        self.model = model or "mock-pro"
        self.llm_api_key = llm_api_key
        self.base_ms = base_ms
        self.jitter_ms = jitter_ms

    # ------------------------------------------------------------- 模拟执行
    def _mock_run(self, pod: dict, sandbox=None) -> tuple[str, float, list]:
        text = pod.get("text", "")          # pod 自带任务文本
        mode = pod.get("mode", "weak")
        size = pod.get("size", "small")
        delay = {"small": 1, "medium": 2, "large": 4}[size] * (self.base_ms / 800)
        t0 = time.time()
        time.sleep(max(0.1, delay))
        cost = round(time.time() - t0, 2)
        summary = text[:60] + ("…" if len(text) > 60 else "")
        out = f"[mock-{self.model}] {MOCK_OUTPUTS.get(mode, MOCK_OUTPUTS['weak'])} 任务: {summary}"
        tool_log: list = []
        if sandbox is not None:
            # 尝试执行：显式请求优先，否则尝试调度器批准的模式默认工具集
            requested = sorted(set(pod.get("tools_requested") or
                                   pod.get("tools_allowed") or []))
            for tool in requested:
                args = dict(DEFAULT_TOOL_ARGS.get(tool, {}))
                if tool in ("classify_text", "summarize_text"):
                    args["text"] = text
                tool_log.append(sandbox.exec(tool, args))
            allowed = sum(1 for e in tool_log if e["allowed"])
            denied = [e["tool"] for e in tool_log if not e["allowed"]]
            out += f" 工具调用: 放行 {allowed}/{len(tool_log)}"
            if denied:
                out += f"，越权拒绝 {denied}"
        return out, cost, tool_log

    # ------------------------------------------------------- OpenAI 兼容执行
    def _openai_run(self, pod: dict) -> tuple[str, float]:
        task = pod.get("task", {})
        text = task.get("text", "")
        mode = pod.get("mode", "weak")
        prompt = (
            f"你是 AI 集群节点 {self.model} 上的执行器。"
            f"任务分类: {mode}。请执行任务并简洁输出结果。\n任务: {text}"
        )
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 512,
        }).encode()
        req = request.Request(self.llm_url.rstrip("/") + "/chat/completions",
                              data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.llm_api_key:
            req.add_header("Authorization", f"Bearer {self.llm_api_key}")
        t0 = time.time()
        try:
            with request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
            cost = round(time.time() - t0, 2)
            out = data["choices"][0]["message"]["content"].strip()
            return out, cost
        except (error.URLError, KeyError, IndexError, json.JSONDecodeError) as e:
            raise RuntimeError(f"LLM 执行失败: {e}") from None

    def run(self, pod: dict, sandbox=None) -> tuple[str, float, list]:
        if self.kind == "openai":
            out, cost = self._openai_run(pod)
            return out, cost, []
        return self._mock_run(pod, sandbox)
