# 架构：从 K8s 集群到 AI 集群调度网络

本文档把博客《K8s环境搭建（保姆级教学）：手把手从零搭建K8s集群》
（1 主 2 从，kubeadm + containerd + Calico）的每一步映射到本组件。

## 1. 博客拓扑 vs 本组件拓扑

```
博客（真实 K8s）                         本组件（AI 集群，单机多进程）
┌─────────────────────────────┐          ┌──────────────────────────────┐
│ Master 192.168.1.100        │          │ Control-Plane (进程)          │
│  ├ kube-apiserver           │          │  ├ ai-apiserver  :16443      │
│  ├ etcd                     │          │  ├ ai-etcd       :12379      │
│  ├ kube-scheduler           │          │  ├ ai-scheduler              │
│  └ kube-controller-manager  │          │  └ ai-controller-manager     │
├─────────────────────────────┤          ├──────────────────────────────┤
│ Node1 192.168.1.101         │          │ Node 进程 (每节点一个)        │
│  └ kubelet + containerd     │          │  └ ai-kubelet + ai-runtime   │
│ Node2 192.168.1.102         │          │    k8s-master / k8s-node1 /  │
│  └ kubelet + containerd     │          │    k8s-node2（沿用博客命名）  │
└─────────────────────────────┘          └──────────────────────────────┘
```

对应关系：

| 博客步骤 | K8s 组件 | AI 集群组件 | 实现 |
|---|---|---|---|
| 集群初始化（四） | `kubeadm init` | `aikube cluster init` | 生成 token/kubeconfig，按 `cluster.json` 拉起全部进程 |
| 节点加入（五） | `kubeadm join <token>` | `aikube node join <token>` | 校验 token → 写 cluster.json → 拉起 kubelet 进程 |
| 组件安装（三） | kubelet/kubeadm/kubectl | `aikube` 包 | 纯 Python 标准库，`pip` 都无需 |
| 容器运行时（二.5） | containerd | `ai-runtime` | mock 执行器 / OpenAI 兼容接口 |
| 网络插件（四.4） | Calico | `ai-cni` | 节点发现 + 任务→节点路由表（apiserver 内） |
| 命令工具（六） | `kubectl get/describe/logs` | `aikube get/describe/logs` | CLI 子命令 |
| 集群验证（六） | `kubectl create deployment nginx` | `aikube run "任务"` | 任务→Pod→调度→执行 |
| 令牌管理（七） | `kubeadm token create` | `aikube token create` | 滚动更新集群 token |
| 节点维护 | `kubectl cordon/drain` | `aikube node cordon/drain` | 置 Unschedulable + 驱逐 |
| NotReady 处理（七） | Calico 排查 | 控制器自动驱逐+重调度 | 10s 心跳超时判死 |

## 2. 数据流：一次任务的生命周期

```
aikube run "任务文本"
   │  POST /api/v1/tasks {text, mode=auto, replicas}
   ▼
ai-apiserver ──拆分为 N 个 Pod(phase=Pending)──▶ ai-etcd (JSONL-WAL)
   ▲                                                 │
   │ GET /api/v1/pods?phase=Pending                  │
ai-scheduler ◀───────────────────────────────────────┘
   │ 1) AI 分类（LLM/规则）：spec/react/mixed/weak
   │ 2) Filter：Ready? Schedulable? 槽位? 标签亲和? 能力?
   │ 3) Score：0.4·load + 0.3·capability + 0.2·affinity + 0.1·latency
   │ 4) Bind：POST /api/v1/pods/<name>/bind {node, decision}
   ▼
ai-apiserver ── pod.phase=Scheduled, node=xxx ──▶ ai-etcd
   ▲
   │ GET /api/v1/pods?node=xxx&phase=Scheduled
ai-kubelet ── 认领(Running) ──▶ ai-runtime 执行 ──▶ 上报 Succeeded/Failed
   │
   └─ 每 3s 心跳：ready/queue/latency_ms（NodeStatus 类比）
```

## 3. 自愈闭环（故障注入实验）

```
kill -9 kubelet(pod 所在节点)
   → 心跳停止
   → ai-controller 10s 后判 NotReady（事件记录）
   → 驱逐该节点上 Running/Scheduled Pod（phase=Evicted, reschedule_count+1）
   → 2 轮巡检后 requeue（phase=Pending）
   → ai-scheduler 重新 Filter/Score（死节点被过滤）
   → Bind 到健康节点 → kubelet 重新执行 → Succeeded
```

实测耗时：NotReady ≈ 10s，驱逐+requeue ≈ 4s，重调度 ≈ 1s（见 experiments.md P2）。

## 4. 目录结构

```
cluster/
├── aikube/                  # Python 包（组件全家桶）
│   ├── __main__.py          # 入口分发：CLI 子命令 / 组件进程
│   ├── cli.py               # aikube CLI + 单机进程编排（kubeadm/kubectl 合体）
│   ├── apiserver.py         # ai-apiserver（REST + cni 路由表 + 领域模型 Api）
│   ├── state.py             # ai-etcd（JSONL-WAL KV 存储 + HTTP）
│   ├── scheduler.py         # ai-scheduler（分类/Filter/Score/Bind + 工具覆盖）
│   ├── controller.py        # ai-controller-manager（健康/驱逐/副本）
│   ├── kubelet.py           # ai-kubelet（注册/心跳/沙箱执行）
│   ├── runtime.py           # ai-runtime（mock / OpenAI 兼容）
│   ├── tools.py             # 工具注册表 + 模式→工具映射 + ToolSandbox 最小权限
│   └── util.py              # 配置/端口/日志/HTTP 客户端
├── scripts/
│   ├── install.sh           # 安装到 ~/.local/bin
│   ├── demo.sh              # 一键演示
│   └── push.sh              # 推送到 GitHub（用户账号）
├── docs/                    # 本文档 + 论文 + 实验记录
└── tests/                   # 单元测试 + 端到端测试
```

## 5. 设计取舍

- **单机多进程**：每个组件是独立 OS 进程（PID 文件 + 日志分离），复用博客"多节点"心智，
  但通过 127.0.0.1 端口互联——本地即可验证全部集群语义。
- **etcd = JSONL 追加日志**：写先落盘再进内存，重启重放恢复（WAL 思路），避免引入真 etcd。
- **调度决策落盘**：每个 Pod 的 `decision` 字段完整记录分类/过滤/打分，`describe` 可审计。
- **LLM 可插拔**：分类与执行都可接 OpenAI 兼容端点（ollama 默认 11434）；无 LLM 时
  规则分类 + mock 执行，全链路离线可跑。
- **工具最小权限**：控制面 API 面固定 14 个管理端点（无执行端点，/exec 类 404）；
  执行面节点工具白名单由能力推导最小集，调度器做工具覆盖过滤，节点侧 ToolSandbox
  拒绝越权并留痕（`aikube get tools` 查看矩阵）。
