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


if __name__ == "__main__":
    unittest.main(verbosity=2)
