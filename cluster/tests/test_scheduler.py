"""ai-scheduler 单元测试：分类器规则、Filter、Score、Bind。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aikube.scheduler import Classifier, Scheduler, MODE_CAPABILITY  # noqa: E402


class TestClassifier(unittest.TestCase):
    def setUp(self):
        self.cls = Classifier(enabled=False)  # 仅规则

    def test_plan_keyword(self):
        r = self.cls.classify("请设计一个微服务架构方案")
        self.assertEqual(r["mode"], "spec")
        self.assertEqual(r["source"], "rules")

    def test_action_keyword(self):
        r = self.cls.classify("修复登录页面的 bug")
        self.assertEqual(r["mode"], "react")

    def test_short_question_is_weak(self):
        r = self.cls.classify("这个报错是什么意思？")
        self.assertEqual(r["mode"], "weak")

    def test_long_task_is_mixed(self):
        r = self.cls.classify("我们需要完成这件事并交付最终结果。" * 30)
        self.assertEqual(r["mode"], "mixed")


class FakeApi:
    """内存版 Api 桩：记录调度结果，供调度器单测。"""

    def __init__(self, nodes: dict, pods: list[dict]):
        self._nodes = nodes
        self._pods = pods
        self.bound = []

    def nodes(self):
        return self._nodes

    def pods(self, node=None, phase=None):
        out = [dict(p) for p in self._pods]
        if node:
            out = [p for p in out if p.get("node") == node]
        if phase:
            out = [p for p in out if p.get("phase") == phase]
        return out

    def set_pod_phase(self, name, phase, **extra):
        for p in self._pods:
            if p["name"] == name:
                p.update(extra)
                p["phase"] = phase

    def bind_pod(self, name, node, decision):
        self.bound.append((name, node, decision))


def node(name, model, caps, slots=2, labels=None, ready=True, schedulable=True,
         queue=0, latency=0.0):
    return {"name": name, "model": model, "capabilities": caps, "slots": slots,
            "labels": labels or {}, "ready": ready, "schedulable": schedulable,
            "queue": queue, "latency_ms": latency}


def pod(name, mode="react", affinity=None, text="执行任务"):
    return {"name": name, "mode": mode, "affinity": affinity or {},
            "text": text, "phase": "Pending", "node": None}


class TestScheduler(unittest.TestCase):
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

    def test_filter_notready(self):
        self.api._nodes["k8s-node1"]["ready"] = False
        self.sched._schedule_pod(self.api.pods()[0])
        name, node, dec = self.api.bound[0]
        self.assertNotEqual(node, "k8s-node1")

    def test_filter_cordon(self):
        self.api._nodes["k8s-node2"]["schedulable"] = False
        self.sched._schedule_pod(self.api.pods()[0])
        name, node, dec = self.api.bound[0]
        self.assertNotEqual(node, "k8s-node2")

    def test_filter_capability(self):
        # react 任务需要 execute/react；master 无此能力 → 被过滤，
        # 最终路由到具备执行能力的节点（flash 偏好 → node1）
        self.sched._schedule_pod(self.api.pods()[0])
        name, node, dec = self.api.bound[0]
        self.assertIn("k8s-master", dec["filtered"])  # 能力不足
        self.assertEqual(node, "k8s-node1")

    def test_affinity_pins_gpu_node(self):
        self.api._pods = [pod("t-01", mode="react",
                              affinity={"gpu": "true"}, text="训练任务")]
        self.sched._schedule_pod(self.api.pods()[0])
        name, node, dec = self.api.bound[0]
        self.assertEqual(node, "k8s-node2")

    def test_spec_prefers_pro_nodes(self):
        self.api._pods = [pod("t-01", mode="spec", text="设计架构方案")]
        self.sched._schedule_pod(self.api.pods()[0])
        name, node, dec = self.api.bound[0]
        self.assertTrue(node in ("k8s-master", "k8s-node2"),
                        f"spec 应路由到 Pro 节点，实际 {node}")

    def test_load_balance(self):
        # node1 满载 → 应绕开
        self.api._nodes["k8s-node1"]["queue"] = 2  # slots=2 已满
        self.api._pods = [pod("t-01", mode="react", text="执行任务")]
        self.sched._schedule_pod(self.api.pods()[0])
        name, node, dec = self.api.bound[0]
        self.assertEqual(node, "k8s-node2")

    def test_auto_classify_then_route(self):
        self.api._pods = [pod("t-01", mode="auto", text="设计一个迁移方案")]
        self.sched._schedule_pod(self.api.pods()[0])
        name, node, dec = self.api.bound[0]
        self.assertEqual(dec["classification"]["mode"], "spec")
        self.assertIn(node, ("k8s-master", "k8s-node2"))

    def test_no_candidate_returns_false(self):
        for n in self.api._nodes.values():
            n["ready"] = False
        self.assertFalse(self.sched._schedule_pod(self.api.pods()[0]))
        self.assertEqual(self.api.bound, [])

    def test_score_weights(self):
        n = node("n1", "mock-flash", ["execute", "react"], queue=0, latency=100)
        p = pod("t-01", mode="react")
        s = self.sched._score(n, p)
        self.assertAlmostEqual(s["total"],
                               sum(self.sched._score(n, p)[k] * w for k, w in
                                   [("load", .4), ("capability", .3),
                                    ("affinity", .2), ("latency", .1)]))


if __name__ == "__main__":
    unittest.main()
