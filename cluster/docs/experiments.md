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

## P5 工具最小权限（单元 32/32 + e2e 6/6 通过）✅

**单元测试（test_tools.py，14 例）**：
- ToolSandbox：放行/拒绝、Pod 工作区隔离（p1 写的文件 p2 读不到）、
  路径越界拒绝且不泄露内容、safe_calc 拒绝任意代码
- 模式→工具映射：code_exec 永不属于 spec，file_write 永不属于 react
- 节点默认白名单最小集：纯执行节点无 file_write，纯计划节点无 code_exec
- 调度器工具过滤：有能力但缺工具的节点被淘汰（"工具不足(缺 …)"）；
  显式 --tools 请求强制执行；decision 记录 tools_required/tools_allowed
- CLI 角色门控：worker 拒绝 run/cluster/node/token/delete/get nodes/tasks
- 控制面 API 面：/exec /shell /run 全部 404（含通用分支防绕过）

**端到端（test_e2e.py E2E-4/5/6）**：
- E2E-4 工具过滤路由：spec 任务 --tools code_exec → master 被"工具不足"过滤
  → 路由到 node2，tools_allowed=[code_exec] ✅
- E2E-5 越权拒绝留痕：手工把 react 任务（请求 file_write）绑定到无 file_write
  的 node1 → 沙箱拒绝 → Pod 事件 `tool.file_write 拒绝` + 输出"越权拒绝" ✅
- E2E-6 角色门控：worker 角色 run 报"禁止命令"退出 1；get pods 放行；
  get tools 输出控制面 API 面 + 节点白名单矩阵 ✅

**实测拓扑（get tools 输出）**：

```
k8s-master   [mock-pro  ] classify_text, file_read, file_write, math_calc, summarize_text
k8s-node1    [mock-flash] classify_text, code_exec, file_read, math_calc, summarize_text, web_fetch
k8s-node2    [mock-pro  ] code_exec, file_read, file_write, math_calc, web_fetch
```

缺陷记录：首版 bind 只按显式 tools_requested 授权，未显式请求的任务
tools_allowed 为空（模式推导集未批准）——改为 `requested ∪ decision.tools_required`
后修复；运行时同步改为尝试已批准集，pods 表 TOOLS 列可见真实授权。

缺陷记录（P6 门禁回归发现）：`cluster init` 的 etcd/apiserver 端口各自独立
`free_port` 解析，当默认端口(12379/16443)被占用、双双回退到同一端口段时可能
解析出**相同端口**（apiserver 检查时 etcd 尚未绑定）→ apiserver bind 失败崩溃
→ 调度链路 404。修复：`free_port(preferred, base, exclude=...)` 端口互斥解析
（etcd 先解析，apiserver 显式排除该端口），并补单测 `test_port_exclusion`。

## P6 插件 10 动作 + 会话模式（门禁再次通过）✅

- dsh-aikube 工具动作面扩展为 10 个：init/start/stop（幂等集群生命周期）、
  run（提交任务）、get（nodes/pods/tasks/tools）、describe/logs、status、smoke。
- 真实调度动作操作默认集群状态目录（~/.aikube，AIKUBE_HOME 可覆盖）。
- 会话模式：`~/.dsh/.agent-presets/aikube` 预设（persona 只使用 aikube 工具，
  任务一律交集群调度），`settings.yaml → agent-presets.default: aikube`。
- DSH Testkit 门禁再次 passed（run `20260819002521-1ddbe620`）：
  11 阶段全绿，exercise 含端口互斥修复后的真实集群调度。

## 复现

```bash
cd cluster
python3 -m unittest discover -s tests -v   # P4/P5 单元（32 例）
python3 -m unittest tests.test_e2e -v      # P1-P3 + P5 端到端（6 例，约 50s）
```
