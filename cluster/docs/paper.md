# ai-scheduler 调度算法论文（v0.1）

## 1. 问题定义

AI 集群 = 节点集合 `N`（每个节点有模型能力、槽位、标签、负载、延迟），
任务集合 `T`（文本 + 显式/隐式模式）。调度目标：把每个任务产生的 Pod 路由到
**能力匹配、负载均衡、满足约束** 的节点，且决策可审计、可自愈。

## 2. 四行为带（继承 router-standard）

任务模式（对齐 [router-standard](../../preset/docs/paper.md) 的分类思想）：

| 模式 | 含义 | 默认能力需求 | 偏好模型 | 典型任务 |
|---|---|---|---|---|
| `spec` | 计划-集体（深度思考） | plan, spec | Pro | 架构方案、上线规划 |
| `react` | 执行者（直接产出） | execute, react | Flash | 修 bug、翻译、生成 |
| `mixed` | 先试错再收敛 | execute, plan | 任一 | 长任务、探索型 |
| `weak` | 模型自分类 | classify | neutral | 模糊短问 |

`MODE_CAPABILITY[mode]` 给出能力需求，`MODE_MODEL[mode]` 给出偏好模型。

## 3. AI 分类器

`mode=auto` 的 Pod 先分类：

```
classify(text):
    if llm_url 可用:  return llm(text)          # JSON: {mode, capabilities, confidence}
    else:            return rules(text)         # 关键词/长度/问号启发式
```

规则分类优先级：计划词（方案/计划/设计/roadmap…）→ `spec`；
短问句（<80 字符含 ?）→ `weak`；动作词（写/修复/执行…）→ `react`；
长文本（>200 字符）→ `mixed`；兜底 `react`。
分类结果与置信度写入 Pod，`describe` 可见。

## 4. 调度流水线（Filter → Score → Bind）

### 4.1 Filter（硬约束，任一不满足即淘汰）

```
ready = True            # 心跳存活（控制器维护）
schedulable = True      # 未被 cordon
queue < slots           # 槽位未满
∀(k,v) ∈ affinity: labels[k] = v      # 标签亲和（如 gpu=true）
capabilities ∩ MODE_CAPABILITY[mode] ≠ ∅   # 能力匹配
```

### 4.2 Score（加权打分，权重可审计）

对每个候选节点：

```
load       = max(0, 1 − queue/slots)                   权重 0.40
capability = 1.0  if (mode 偏好模型 ∈ node.model)      权重 0.30
             0.6  elif capabilities ∩ need ≠ ∅
             0.0  else
affinity   = |匹配标签| / |要求标签|  （无要求时 0.7）   权重 0.20
latency    = max(0, 1 − latency_ms/5000)               权重 0.10

total = Σ wᵢ·scoreᵢ
```

同分决胜：`(total, −load)` 字典序取最大 → 负载更低者胜，决策确定性。

### 4.3 Bind

胜者写回 `POST /api/v1/pods/<name>/bind`，`decision` 字段持久化：
`{time, classification, filtered{node: 原因}, scores{node: 分项}, winner, weights}`。

## 5. 控制器（自愈）

```
每 2s 巡检：
  heartbeat 超时 10s  → NotReady（不改写 heartbeat 时间戳）→ 驱逐该节点 Pod
  心跳恢复            → Ready
  Evicted Pod 满 2 轮 → requeue → Pending（调度器重绑定）
  任务 replicas 期望 > 存活+完成 → 补建 Pod（Failed 的替换，Evicted 的重调度）
```

关键不变量：**heartbeat 时间戳只由 kubelet 写入**，控制器只翻转 ready 标志——
避免"控制器自己刷新心跳导致节点永不过期"的经典 bug。

## 6. 正确性讨论

- **无死锁**：Pending Pod 要么被绑定，要么因无候选节点滞留（可观测：filtered 原因落盘）。
- **幂等**：bind/evict/status 均检查当前 phase，重复调用不产生副作用。
- **可审计**：所有调度动作（分类、过滤、打分、绑定、驱逐、requeue）写入 Pod events/decision。
- **确定性**：同分决胜规则固定，相同状态输入 → 相同调度输出。
