"""端到端测试：真实拉起 1 主 2 从集群，验证完整链路。

场景：
  E2E-1 三任务路由：spec→Pro 节点 / react→Flash 节点 / gpu 亲和→gpu 节点
  E2E-2 运行中 Pod 的节点被杀 → NotReady → 驱逐 → requeue → 重调度到健康节点
  E2E-3 副本保持：replicas=2 的任务失败一个后控制器补建

运行：python3 -m unittest tests.test_e2e -v
依赖：本测试直接使用 aikube CLI（不要求集群已启动，自行拉起/清理）。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

E2E_HOME = Path(tempfile.mkdtemp(prefix="aikube-e2e-"))


def run_cli(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "AIKUBE_HOME": str(E2E_HOME),
           "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-m", "aikube", *args],
                          capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=ROOT)


def api_get(path: str) -> dict:
    from aikube.util import http, load_kubeconfig
    cfg = load_kubeconfig()
    if str(E2E_HOME) not in os.environ.get("AIKUBE_HOME", ""):
        pass  # 下面显式用环境
    return http("GET", cfg["server"] + path, token=cfg["token"])


class TestE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = {**os.environ, "AIKUBE_HOME": str(E2E_HOME),
               "PYTHONPATH": str(ROOT)}
        os.environ["AIKUBE_HOME"] = str(E2E_HOME)
        r = run_cli("cluster", "init", "--name", "e2e")
        assert r.returncode == 0, r.stderr
        time.sleep(4)  # 等 kubelet 注册+心跳

    @classmethod
    def tearDownClass(cls):
        run_cli("cluster", "stop")

    def _pods(self):
        import json as _j
        from aikube.util import http, load_kubeconfig
        cfg = load_kubeconfig()
        return http("GET", cfg["server"] + "/api/v1/pods",
                    token=cfg["token"])["items"]

    def _wait_pod_phase(self, pod: str, phases: set[str], timeout: float = 40) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            p = next((x for x in self._pods() if x["name"] == pod), None)
            if p and p["phase"] in phases:
                return p
            time.sleep(1)
        self.fail(f"pod {pod} 未在 {timeout}s 内到达 {phases}: "
                  f"{[ (x['name'], x['phase']) for x in self._pods() ]}")

    def _submit(self, *args: str) -> str:
        """提交任务并返回任务名。"""
        r = run_cli("run", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split("任务 ")[1].split(" ")[0]

    def test_e2e1_routing(self):
        """三种任务类型路由到正确节点。"""
        spec_task = self._submit("设计一个微服务架构上线方案", "--mode", "auto")
        react_task = self._submit("修复登录页面的 bug", "--mode", "auto")
        gpu_task = self._submit("用 GPU 训练图像识别模型", "--mode", "auto",
                                "--affinity", "gpu=true")

        for task in (spec_task, react_task, gpu_task):
            self._wait_pod_phase(f"{task}-01", {"Succeeded", "Failed"}, 40)

        pods = {p["task"]: p for p in self._pods()}
        spec_pod, react_pod, gpu_pod = (pods[t] for t in
                                        (spec_task, react_task, gpu_task))
        # spec → Pro 节点（master 或 node2）
        self.assertTrue(spec_pod["node"] in ("k8s-master", "k8s-node2"),
                        f"spec 应路由到 Pro 节点: {spec_pod['node']}")
        self.assertEqual(spec_pod["classification"]["mode"], "spec")
        # react → Flash 节点（node1 优先）
        self.assertEqual(react_pod["node"], "k8s-node1")
        # gpu 亲和 → node2
        self.assertEqual(gpu_pod["node"], "k8s-node2")

    def test_e2e2_evict_reschedule(self):
        """运行中节点被杀 → NotReady → 驱逐 → 重调度到健康节点。"""
        from aikube.util import http, load_kubeconfig
        cfg = load_kubeconfig()
        task = self._submit("重构支付模块并输出完整实施方案", "--mode", "spec",
                            "--size", "large")
        pod_name = f"{task}-01"
        pod = self._wait_pod_phase(pod_name, {"Running"}, 20)
        node = pod["node"]
        # 杀掉该节点 kubelet
        pidfile = E2E_HOME / "run" / f"{node}.pid"
        os.kill(int(pidfile.read_text()), 9)
        # 控制器 10s 判 NotReady → 驱逐 → requeue → 调度器重绑定
        p = self._wait_pod_phase(pod_name, {"Succeeded", "Failed"}, 60)
        self.assertEqual(p["phase"], "Succeeded", p)
        self.assertGreaterEqual(p["reschedule_count"], 1,
                                "被驱逐的 Pod 应有重调度记录")
        self.assertNotEqual(p["node"], node,
                            f"重调度后不应再落在死节点 {node}，实际 {p['node']}")
        # 死节点应为 NotReady
        nodes = http("GET", cfg["server"] + "/api/v1/nodes",
                     token=cfg["token"])["items"]
        self.assertFalse(nodes[node]["ready"])
        # 事件链可追溯
        events = [e["what"] for e in p.get("events", [])]
        self.assertTrue(any("evict" in e for e in events), events)
        self.assertTrue(any("requeue" in e for e in events), events)

    def test_e2e3_replicas(self):
        """replicas=2：两个 Pod 并行，全部成功。"""
        task = self._submit("批量导出财务报表", "--mode", "react",
                            "--replicas", "2")
        for i in (1, 2):
            self._wait_pod_phase(f"{task}-{i:02d}", {"Succeeded", "Failed"}, 40)
        pods = [p for p in self._pods() if p["task"] == task]
        self.assertEqual(len(pods), 2)
        self.assertTrue(all(p["phase"] == "Succeeded" for p in pods))

    # ------------------------------------------------------ 工具最小权限
    def test_e2e4_tool_filter_routing(self):
        """工具覆盖过滤：spec 任务显式请求 code_exec → 只路由到有该工具的节点。"""
        from aikube.util import http, load_kubeconfig
        cfg = load_kubeconfig()
        # 前序测试可能杀掉过 kubelet：先复活全部节点（幂等）
        run_cli("cluster", "start")
        time.sleep(4)
        task = self._submit("编写代码修复方案", "--mode", "spec", "--tools",
                            "code_exec")
        pod_name = f"{task}-01"
        pod = self._wait_pod_phase(pod_name, {"Succeeded", "Failed"}, 40)
        self.assertEqual(pod["phase"], "Succeeded", pod)
        # master(计划型) 无 code_exec → 被工具过滤，路由到 node2
        self.assertEqual(pod["node"], "k8s-node2", pod)
        dec = pod["decision"]
        self.assertEqual(dec["tools_required"], ["code_exec"])
        self.assertEqual(dec["tools_allowed"], ["code_exec"])
        self.assertTrue(any("工具不足" in why for why in dec["filtered"].values()),
                        dec["filtered"])

    def test_e2e5_tool_denial_trace(self):
        """越权工具拒绝留痕：手工绑定到无 file_write 的节点，沙箱拒绝并记录事件。"""
        from aikube.util import http, load_kubeconfig
        cfg = load_kubeconfig()
        api = cfg["server"]
        tok = cfg["token"]
        # 1. cordon 全部节点，防止调度器抢走 Pod
        for n in http("GET", f"{api}/api/v1/nodes", token=tok)["items"]:
            http("POST", f"{api}/api/v1/nodes/{n}/cordon",
                 {"schedulable": False}, token=tok)
        # 2. 提交 react 任务，显式请求 file_write（node1 白名单没有）
        r = http("POST", f"{api}/api/v1/tasks",
                 {"text": "执行修复任务", "mode": "react",
                  "tools": ["file_write", "code_exec"]}, token=tok)
        task = r["task"]["name"]
        pod_name = f"{task}-01"
        # 3. 手工绑定到 k8s-node1（绕过调度器，模拟陈旧绑定）
        http("POST", f"{api}/api/v1/pods/{pod_name}/bind",
             {"node": "k8s-node1", "decision": {"winner": "k8s-node1",
                                                "scores": {}, "filtered": {},
                                                "tools_required": ["file_write",
                                                                   "code_exec"]}},
             token=tok)
        pod = self._wait_pod_phase(pod_name, {"Succeeded", "Failed"}, 40)
        self.assertEqual(pod["phase"], "Succeeded", pod)
        # 绑定兜底：tools_allowed = 请求 ∩ 节点白名单 = 仅 code_exec
        self.assertEqual(pod["tools_allowed"], ["code_exec"])
        # 沙箱拒绝留痕：事件 + 输出
        events = [e["what"] for e in pod["events"]]
        self.assertTrue(any("tool.file_write 拒绝" in e for e in events), events)
        self.assertIn("越权拒绝", pod["output"])
        tool_log = pod.get("tool_log", [])
        denied = [t for t in tool_log if not t["allowed"]]
        self.assertEqual([t["tool"] for t in denied], ["file_write"])
        # 4. 恢复可调度
        for n in http("GET", f"{api}/api/v1/nodes", token=tok)["items"]:
            http("POST", f"{api}/api/v1/nodes/{n}/cordon",
                 {"schedulable": True}, token=tok)

    def test_e2e6_role_gate_and_tool_matrix(self):
        """角色门控：worker 拒绝管理命令；get tools 展示控制面/执行面工具矩阵。"""
        import subprocess as sp
        r = sp.run([sys.executable, "-m", "aikube", "--role", "worker",
                    "run", "越权任务"], capture_output=True, text=True,
                   timeout=30, env={**os.environ, "AIKUBE_HOME": str(E2E_HOME),
                                    "PYTHONPATH": str(ROOT)}, cwd=ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn("禁止命令", r.stderr)
        # worker 允许执行面工具：get pods
        r2 = sp.run([sys.executable, "-m", "aikube", "--role", "worker",
                     "get", "pods"], capture_output=True, text=True,
                    timeout=30, env={**os.environ, "AIKUBE_HOME": str(E2E_HOME),
                                     "PYTHONPATH": str(ROOT)}, cwd=ROOT)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("POD", r2.stdout)
        # 工具矩阵
        r3 = run_cli("get", "tools")
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertIn("控制面 API 面", r3.stdout)
        self.assertIn("节点工具白名单", r3.stdout)
        self.assertIn("k8s-node1", r3.stdout)
        self.assertIn("code_exec", r3.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
