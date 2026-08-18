"""ai-tools — AI 集群工具最小权限层（Tool Least-Privilege）。

对齐 k8s RBAC 思想：
- 控制面（apiserver/scheduler/controller）只暴露集群管理工具，无任务执行工具；
- 执行面（节点）只保留执行任务所需工具：节点 profile 声明工具白名单，
  任务的 AI 分类推导所需工具集，调度器做工具覆盖过滤，
  节点运行时在 ToolSandbox 内执行，越权工具调用被拒绝并留痕。

工具均为确定性实现（stdlib），保证 Testkit 式可复现 exercise。
"""
from __future__ import annotations

import ast
import operator
import re
from pathlib import Path

# ------------------------------------------------------------- 工具注册表
# caps: 需要节点具备的能力之一（plan/spec/execute/react/classify）
TOOL_REGISTRY: dict[str, dict] = {
    "file_read": {"desc": "读取工作区文件", "caps": ["plan", "execute"]},
    "file_write": {"desc": "写入工作区文件", "caps": ["plan"]},
    "math_calc": {"desc": "安全数学计算", "caps": ["plan", "execute", "classify"]},
    "classify_text": {"desc": "文本四分类(spec/react/mixed/weak)", "caps": ["classify"]},
    "summarize_text": {"desc": "文本摘要", "caps": ["classify"]},
    "web_fetch": {"desc": "获取网页内容(模拟)", "caps": ["execute"]},
    "code_exec": {"desc": "执行代码片段(模拟)", "caps": ["execute"]},
}

# 模式 → 所需工具集（AI 分类结果决定任务工具需求，与 router-standard 对齐）
MODE_TOOLS: dict[str, list[str]] = {
    "spec": ["file_read", "file_write", "math_calc"],
    "react": ["code_exec", "web_fetch", "math_calc"],
    "mixed": ["file_read", "file_write", "code_exec", "math_calc"],
    "weak": ["classify_text", "summarize_text"],
}


def default_node_tools(capabilities: list[str]) -> list[str]:
    """节点默认工具白名单：由能力推导的最小集（能力→工具的保守映射）。"""
    caps = set(capabilities)
    tools = set()
    for name, meta in TOOL_REGISTRY.items():
        if caps.intersection(meta["caps"]):
            tools.add(name)
    # 节点仅具备执行能力时，绝不默认授予计划类工具（最小权限）
    return sorted(tools)


# ------------------------------------------------------------- 安全计算
_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_MAX_EXPR = 64


def safe_calc(expr: str) -> str:
    """只允许数字 + 四则/幂运算的表达式求值（杜绝任意代码执行）。"""
    if len(expr) > _MAX_EXPR or not re.fullmatch(r"[0-9+\-*/().% \t]+", expr):
        return "拒绝: 表达式包含非法字符"
    try:
        tree = ast.parse(expr, mode="eval")

        def _eval(node):
            if isinstance(node, ast.Expression):
                return _eval(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
                return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                v = _eval(node.operand)
                return v if isinstance(node.op, ast.UAdd) else -v
            raise ValueError("不支持的表达式")
        result = _eval(tree)
        return f"{result:.6f}".rstrip("0").rstrip(".") if isinstance(result, float) else str(result)
    except (ValueError, ZeroDivisionError, SyntaxError, RecursionError):
        return "拒绝: 表达式无法计算"


# ------------------------------------------------------------- ToolSandbox
class ToolSandbox:
    """节点侧工具沙箱：只放行白名单工具，越权调用返回拒绝并留痕。

    workspace: 每个 Pod 独立的工作区（文件类工具的作用域）。
    allowed:   本 Pod 被调度器批准的 tools_allowed。
    """

    def __init__(self, workspace: Path, allowed: set[str]):
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.allowed = set(allowed)
        self.log: list[dict] = []  # [{tool, allowed, result}]

    def _in_workspace(self, name: str) -> Path:
        p = (self.workspace / name).resolve()
        if not p.is_relative_to(self.workspace.resolve()):
            raise ValueError("路径越界")
        return p

    def exec(self, tool: str, args: dict | None = None) -> dict:
        args = args or {}
        if tool not in self.allowed:
            entry = {"tool": tool, "allowed": False, "result": "拒绝: 工具不在本任务白名单"}
            self.log.append(entry)
            return entry
        try:
            result = self._run(tool, args)
        except Exception as e:  # 工具自身错误不越权
            result = f"错误: {e}"
        entry = {"tool": tool, "allowed": True, "result": result}
        self.log.append(entry)
        return entry

    # ------------------------------------------------------- 工具实现
    def _run(self, tool: str, args: dict) -> str:
        if tool == "file_read":
            p = self._in_workspace(str(args.get("path", "notes.txt")))
            if p.exists():
                return f"读取成功: {p.name} = {p.read_text(encoding='utf-8')[:80]}"
            return f"文件不存在: {p.name}"
        if tool == "file_write":
            p = self._in_workspace(str(args.get("path", "notes.txt")))
            p.write_text(str(args.get("content", "")), encoding="utf-8")
            return f"写入成功: {p.name} ({len(args.get('content', ''))} 字符)"
        if tool == "math_calc":
            return f"计算 {args.get('expr', '')} = {safe_calc(str(args.get('expr', '')))}"
        if tool == "classify_text":
            from .scheduler import Classifier
            cls = Classifier(enabled=False).classify(str(args.get("text", "")))
            return f"分类: {cls['mode']} (置信 {cls['confidence']})"
        if tool == "summarize_text":
            t = str(args.get("text", ""))
            return f"摘要: {t[:40]}{'…' if len(t) > 40 else ''}"
        if tool == "web_fetch":
            return f"网页(模拟): {args.get('url', '')} 抓取成功, 2 个段落"
        if tool == "code_exec":
            return f"代码执行(模拟): {args.get('code', '')[:40]} → 输出 ok"
        return f"未知工具: {tool}"
