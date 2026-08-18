# 实验记录（P1-P3，2026-08-18 实测）

环境：单机多进程模拟集群，`k8s-master`(mock-pro/plan,spec,classify) +
`k8s-node1`(mock-flash/execute,react,classify) + `k8s-node2`(mock-pro/plan,spec,execute, gpu=true)。
全部实验通过 `python3 -m unittest tests.test_e2e -v` 复现。

## P1 任务感知路由（E2E-1）✅

提交三个 `--mode auto` 任务，观察 AI 分类与路由：

| 任务文本 | 分类(置信) | 路由节点 | 判定 |
|---|---|---|---|
| 设计一个微服务架构上线方案 | spec (0.85, rules) | k8s-master (Pro) | ✅ 计划任务→深度思考节点 |
| 修复登录页面的 bug | react (0.80, rules) | k8s-node1 (Flash) | ✅ 执行任务→快速执行节点 |
| 用 GPU 训练图像识别模型 | react (0.80, rules) | k8s-node2 (gpu=true) | ✅ 标签亲和命中 |

打分样例（spec 任务，describe 输出）：
```
k8s-master  total=0.94 (load=1.00 cap=1.00 aff=0.70 lat=1.00) ← 选中
k8s-node2   total=0.94 (load=1.00 cap=1.00 aff=0.70 lat=1.00)
被过滤: k8s-node1 能力不足(需['plan','spec'])
```
结论：规则分类四类全准；Pro 节点在能力匹配上正确胜出；亲和性硬过滤生效。
缺陷记录：首版 `startswith("pro")` 对 "mock-pro" 判 False 导致 Pro 加分失效——改为
子串匹配后修复（见 P4 回归）。

## P2 故障自愈闭环（E2E-2）✅

场景：spec/large（4s 执行）任务 Running 在 k8s-master 上，`kill -9` master 的 kubelet。

| 时刻 | 事件 |
|---|---|
| T+0s | kubelet 被杀，心跳停止 |
| T+10s | 控制器判 NotReady（事件: NotReady: 心跳超时） |
| T+10s | 驱逐 Running Pod（事件: evict: 节点 NotReady, reschedule_count=1） |
| T+14s | requeue → Pending（事件: requeue -> Pending） |
| T+15s | 调度器重绑定：死节点被 Filter 淘汰，落到 k8s-node2 |
| T+19s | 重新执行完成 → Succeeded（reschedule_count=1, node 变更） |

断言全过：`reschedule_count ≥ 1`、`node ≠ 死节点`、死节点 NotReady、事件链含 evict/requeue。
缺陷记录：首版控制器在标记 NotReady 时刷新了 heartbeat，导致节点永不超时且立即"恢复"——
重构为 heartbeat 只由 kubelet 写入后修复。

## P3 副本保持（E2E-3）✅

`--replicas 2` 的 react 任务：两个 Pod（task-xxx-01/-02）并行调度到不同/同节点执行，
最终全部 Succeeded；控制器未误补建第三个 Pod（首版把 Pending 当缺失导致 -03 误建，
修复为 Pending 计入存活后稳定）。

## P4 单元回归（18/18 通过）✅

- 规则分类 4 例（spec/react/weak/mixed）
- Filter：NotReady / cordon / 能力不足 / 亲和性 / 槽位满载
- Score：Pro/Flash 偏好、权重和恒等、同分决胜
- ai-etcd：PUT/GET/DELETE + WAL 重放恢复
- Api 领域模型：节点注册/心跳、任务拆 Pod、bind 幂等、驱逐→requeue
- HTTP 层：join token 不匹配 403

## 复现

```bash
cd cluster
python3 -m unittest discover -s tests -v   # P4
python3 -m unittest tests.test_e2e -v      # P1-P3（约 40s）
```
