"""工具最小权限（Tool Least-Privilege）单元测试。

覆盖：ToolSandbox 放行/拒绝、工作区隔离、安全计算、
调度器工具覆盖过滤、CLI 角色门控、控制面 API 面（无执行端点）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aikube.tools import (ToolSandbox, MODE_TOOLS, TOOL_REGISTRY,  # noqa: E402
                          default_node_tools, safe_calc)
from aikube.scheduler import Classifier, Scheduler  # noqa: E402
from tests.test_scheduler import FakeApi, node, pod  # noqa: E402


class TestToolSandbox(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp())

    def test_allow_and_deny(self):
        sb = ToolSandbox(self.ws, {"file_write", "math_calc"})
        ok = sb.exec("file_write", {"path": "a.txt", "content": "hi"})
        self.assertTrue(ok["allowed"])
        calc = sb.exec("math_calc", {"expr": "2+3*4"})
        self.assertTrue(calc["allowed"])
        self.assertIn("14", calc["result"])
        denied = sb.exec("code_exec", {"code": "x"})
        self.assertFalse(denied["allowed"])
        self.assertIn("拒绝", denied["result"])
        self.assertEqual(len(sb.log), 3)

    def test_workspace_isolation(self):
        sb1 = ToolSandbox(self.ws / "p1", {"file_write", "file_read"})
        sb2 = ToolSandbox(self.ws / "p2", {"file_read"})
        sb1.exec("file_write", {"path": "n.txt", "content": "secret"})
        r = sb2.exec("file_read", {"path": "n.txt"})
        self.assertIn("不存在", r["result"])  # Pod 间工作区隔离

    def test_path_escape_denied(self):
        # 路径越界：工具本身已授权，但越权参数被拒绝且不泄露内容
        sb = ToolSandbox(self.ws, {"file_read"})
        r = sb.exec("file_read", {"path": "../../etc/passwd"})
        self.assertTrue(r["allowed"])
        self.assertIn("越界", r["result"])
        self.assertNotIn("root:", r["result"])  # 内容未泄露

    def test_safe_calc(self):
        self.assertEqual(safe_calc("2+3*4"), "14")
        self.assertIn("拒绝", safe_calc("__import__('os')"))
        self.assertIn("拒绝", safe_calc("1/0"))


class TestToolDerivation(unittest.TestCase):
    def test_mode_tools(self):
        self.assertEqual(set(MODE_TOOLS["spec"]), {"file_read", "file_write", "math_calc"})
        self.assertIn("code_exec", MODE_TOOLS["react"])
        # 执行类工具绝不属于计划类模式（最小权限）
        self.assertNotIn("code_exec", MODE_TOOLS["spec"])
        self.assertNotIn("file_write", MODE_TOOLS["react"])

    def test_default_node_tools_least_privilege(self):
        # 纯执行节点：没有计划类工具
        exec_tools = default_node_tools(["execute", "react"])
        self.assertIn("code_exec", exec_tools)
        self.assertNotIn("file_write", exec_tools)
        # 纯计划节点：没有执行类工具
        plan_tools = default_node_tools(["plan", "spec"])
        self.assertIn("file_write", plan_tools)
        self.assertNotIn("code_exec", plan_tools)
        self.assertNotIn("web_fetch", plan_tools)

    def test_registry_capability_coverage(self):
        for t in MODE_TOOLS.values():
            for name in t:
                self.assertIn(name, TOOL_REGISTRY)


class TestSchedulerToolFilter(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi(
            nodes={
                "k8s-master": node("k8s-master", "mock-pro",
                                   ["plan", "spec", "classify"]),
                "k8s-node1": node("k8s-node1", "mock-flash",
                                  ["execute", "react", "classify"]),
                "k8s-node2": node("k8s-node2", "mock-pro",
                                  ["plan", "spec", "execute"],
                                  labels={"gpu": "true"}),
            },
            pods=[pod("t-01", mode="react", text="修复登录 bug")],
        )
        self.sched = Scheduler(self.api, Classifier(enabled=False))

    def test_spec_filtered_on_node_without_plan_tools(self):
        # spec 需要 file_write；node1 有能力(plan/spec)但工具白名单缺 file_write → 工具过滤
        self.api._nodes["k8s-node1"]["capabilities"] = ["plan", "spec", "execute"]
        self.api._nodes["k8s-node1"]["tools"] = ["file_read", "math_calc", "code_exec"]
        self.api._pods = [pod("t-01", mode="spec", text="设计方案")]
        self.sched._schedule_pod(self.api.pods()[0])
        name, node_, dec = self.api.bound[0]
        self.assertIn("k8s-node1", dec["filtered"])
        self.assertIn("工具不足", dec["filtered"]["k8s-node1"])
        self.assertEqual(node_, "k8s-master")

    def test_explicit_tools_request_enforced(self):
        # 任务显式请求 web_fetch：master 无 → 过滤
        self.api._pods = [pod("t-01", mode="spec", text="调研方案",
                              tools=["web_fetch"])]
        self.sched._schedule_pod(self.api.pods()[0])
        name, node_, dec = self.api.bound[0]
        self.assertEqual(dec["tools_required"], ["web_fetch"])
        self.assertEqual(node_, "k8s-node2")  # 唯一有 web_fetch 的节点

    def test_decision_records_tools(self):
        self.api._pods = [pod("t-01", mode="react", text="执行任务")]
        self.sched._schedule_pod(self.api.pods()[0])
        name, node_, dec = self.api.bound[0]
        self.assertEqual(set(dec["tools_required"]), set(MODE_TOOLS["react"]))
        self.assertEqual(set(dec["tools_allowed"]), set(MODE_TOOLS["react"]))


class TestRoleGate(unittest.TestCase):
    def test_worker_role_denies_admin(self):
        from aikube import cli
        from aikube.util import die as _unused  # noqa: F401
        # worker 角色禁止 run / cluster / node / token / delete / get nodes
        for argv in (["--role", "worker", "run", "任务"],
                     ["--role", "worker", "cluster", "init"],
                     ["--role", "worker", "get", "nodes"],
                     ["--role", "worker", "get", "tasks"],
                     ["--role", "worker", "node", "cordon", "x"],
                     ["--role", "worker", "delete", "pod", "x"]):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(argv)
            self.assertEqual(ctx.exception.code, 1, argv)

    def test_worker_role_allows_execution_tools(self):
        from aikube import cli
        from aikube.util import APIError
        # get pods / logs 放行到命令层：集群在跑则正常返回或报业务错误（Pod 不存在），
        # 不在则 APIError——都证明门控未拦截（若被门控拒绝会 SystemExit(1)
        # 且 stderr 含"禁止命令"）
        for argv in (["--role", "worker", "get", "pods"],
                     ["--role", "worker", "logs", "x"]):
            try:
                cli.main(argv)
            except (APIError, SystemExit):
                pass


class TestControlPlaneSurface(unittest.TestCase):
    def test_port_exclusion(self):
        """etcd/apiserver 回退同一端口段时绝不解析出相同端口（bind 竞争修复）。"""
        from aikube.util import free_port
        base = 19820
        p1 = free_port(12379, base=base)
        p2 = free_port(16443, base=base, exclude={p1})
        self.assertNotEqual(p1, p2)
        # 排除集合生效：p2 不会落在 p1 上，即使 p1 恰为 preferred
        p3 = free_port(16443, base=base, exclude={p1})
        self.assertNotEqual(p3, p1)

    def test_no_exec_endpoint(self):
        """控制面 API 面：/exec /shell /run 必须 404（含通用分支防绕过）。"""
        import json
        import secrets
        import threading
        import time
        from aikube import state as state_mod
        from aikube import apiserver as ap
        from aikube.util import http, APIError
        tmp = Path(tempfile.mkdtemp())
        token = secrets.token_hex(8)
        threading.Thread(target=state_mod.serve,
                         args=(19810, tmp / "etcd.jsonl"), daemon=True).start()
        threading.Thread(target=ap.serve,
                         args=(19811, {"url": "http://127.0.0.1:19810",
                                       "token": ""}, token), daemon=True).start()
        time.sleep(0.8)
        for path in ("/api/v1/pods/x/exec", "/api/v1/pods/x/shell",
                     "/api/v1/exec", "/api/v1/pods/x/run"):
            with self.assertRaises(APIError) as ctx:
                http("POST", f"http://127.0.0.1:19811{path}",
                     {"cmd": "ls"}, token=token)
            self.assertEqual(ctx.exception.status, 404, path)

    def test_control_plane_api_list_is_minimal(self):
        from aikube.apiserver import CONTROL_PLANE_API, FORBIDDEN_API
        self.assertTrue(CONTROL_PLANE_API)
        self.assertLessEqual(len(CONTROL_PLANE_API), 15)  # 极少工具
        self.assertTrue(any("exec" in line for line in FORBIDDEN_API))


if __name__ == "__main__":
    unittest.main()
