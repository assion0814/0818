"""ai-etcd / apiserver 领域模型 单元测试。"""
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aikube.state import Store  # noqa: E402
from aikube.apiserver import Api  # noqa: E402
from aikube.util import http  # noqa: E402


class TestStore(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "etcd.jsonl"

    def test_put_get_delete(self):
        s = Store(self.path)
        s.put("/nodes/a", {"name": "a", "ready": True})
        self.assertEqual(s.get("/nodes/a")["ready"], True)
        self.assertIn("/nodes/a", s.prefix("/nodes/"))
        s.delete("/nodes/a")
        self.assertIsNone(s.get("/nodes/a"))

    def test_wal_replay(self):
        s1 = Store(self.path)
        s1.put("/nodes/x", {"v": 1})
        s1.put("/nodes/x", {"v": 2})
        s1.put("/tasks/t", {"v": 3})
        s2 = Store(self.path)  # 重放
        self.assertEqual(s2.get("/nodes/x")["v"], 2)
        self.assertEqual(s2.get("/tasks/t")["v"], 3)


class TestApiModel(unittest.TestCase):
    """用内存 Store 直测 Api 领域模型（不启 HTTP）。"""

    class FakeEtcd:
        def __init__(self, store: Store):
            self.store = store
            self.url = "memory"

        def _kv(self, key: str) -> dict | None:
            if key.startswith("/v2/kv/"):
                k = key[len("/v2/kv/"):]
                return {"value": self.store.get(k)}
            if key.startswith("/v2/kv"):
                prefix = key[len("/v2/kv"):].lstrip("/")
                return {"items": self.store.prefix(prefix)}
            return {}

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.store = Store(Path(self.tmp) / "etcd.jsonl")
        import aikube.apiserver as ap
        self.api = Api({"url": "memory", "token": ""})
        self.api._get = self._get
        self.api._put = self._put
        self.api._delete = self._delete
        self.api._prefix = self._fake_prefix

    def _get(self, key):
        return self.store.get(key)

    def _put(self, key, value):
        self.store.put(key, value)

    def _delete(self, key):
        self.store.delete(key)

    def _fake_prefix(self, prefix):
        raw = self.store.prefix(prefix)
        return {"items": {k.split("/", 2)[-1]: v for k, v in raw.items()}}

    def test_node_register_heartbeat(self):
        n = self.api.register_node("k8s-node1",
                                   {"model": "mock-flash",
                                    "capabilities": ["execute"],
                                    "slots": 2, "labels": {}}, "tok")
        self.assertTrue(n["ready"])
        self.api.node_heartbeat("k8s-node1", {"ready": True, "queue": 1})
        self.assertEqual(self.api.node("k8s-node1")["queue"], 1)
        # 心跳会刷新 heartbeat 时间
        self.assertGreater(self.api.node("k8s-node1")["heartbeat"], n["heartbeat"] - 1)

    def test_task_pods_lifecycle(self):
        self.api.register_node("k8s-node1", {"model": "mock-flash",
                                             "capabilities": ["execute"],
                                             "slots": 2, "labels": {}}, "tok")
        task = self.api.create_task({"text": "修复登录 bug", "mode": "react",
                                     "replicas": 2}, "tok")
        self.assertEqual(task["name"].startswith("task-"), True)
        pods = self.api.pods()
        self.assertEqual(len(pods), 2)
        self.assertTrue(all(p["phase"] == "Pending" for p in pods))
        # bind
        pod = self.api.bind_pod(pods[0]["name"], "k8s-node1",
                                {"winner": "k8s-node1", "scores": {}})
        self.assertEqual(pod["phase"], "Scheduled")
        self.assertEqual(pod["node"], "k8s-node1")
        # 已绑定 Pod 不能重复 bind
        self.assertIsNone(self.api.bind_pod(pods[0]["name"], "k8s-node1", {}))
        # 驱逐 → requeue
        ev = self.api.evict_pod(pods[0]["name"], "测试驱逐")
        self.assertEqual(ev["phase"], "Evicted")
        self.assertEqual(ev["reschedule_count"], 1)
        rq = self.api.requeue_pod(pods[0]["name"])
        self.assertEqual(rq["phase"], "Pending")

    def test_join_token_validation_via_http(self):
        """HTTP 层：join token 不匹配必须 403（kubeadm 类比）。"""
        import tempfile
        import secrets
        import threading
        import time
        from aikube import state as state_mod
        from aikube import apiserver as ap
        tmp = Path(tempfile.mkdtemp())
        port = 19800
        token = secrets.token_hex(8)
        threading.Thread(target=state_mod.serve,
                         args=(port, tmp / "etcd.jsonl"), daemon=True).start()
        threading.Thread(target=ap.serve,
                         args=(19801, {"url": f"http://127.0.0.1:{port}",
                                       "token": ""}, token), daemon=True).start()
        time.sleep(0.8)
        try:
            from aikube.util import APIError
            with self.assertRaises(APIError) as ctx:
                http("POST", "http://127.0.0.1:19801/api/v1/nodes",
                     {"name": "x", "profile": {}, "token": "bad"}, token=token)
            self.assertEqual(ctx.exception.status, 403)
            ok = http("POST", "http://127.0.0.1:19801/api/v1/nodes",
                      {"name": "x", "profile": {"model": "mock-flash",
                                                "capabilities": ["execute"],
                                                "slots": 1, "labels": {}},
                       "token": token}, token=token)
            self.assertEqual(ok["node"]["name"], "x")
        finally:
            pass  # server 均为 daemon 线程，进程退出即清理


if __name__ == "__main__":
    unittest.main()
