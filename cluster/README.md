# aikube — AI K8s 集群调度网络

dsh-routing-suite 的第三个组件：把博客《K8s环境搭建（保姆级教学）》里的
**1 主 2 从 K8s 集群**（kubeadm + containerd + Calico）复刻成一个 **AI 集群调度网络**——
Control-Plane 是"调度大脑"，Node 是"干活的 AI 节点"，AI 调度器把任务路由到能力匹配的节点。

纯 Python 标准库，零依赖；单机多进程模拟，`cluster init` 一条命令拉起全部组件。

## 为什么叫"AI K8s"？

| K8s（博客教程） | AI 集群（本组件） | 职责 |
|---|---|---|
| kube-apiserver | `ai-apiserver` | REST API：任务提交 / 节点注册 / Pod 绑定（[apiserver.py](aikube/apiserver.py)） |
| etcd | `ai-etcd` | JSONL-WAL 状态存储，崩溃重放恢复（[state.py](aikube/state.py)） |
| kube-scheduler | `ai-scheduler` | **AI 调度器**：任务分类 → Filter → 加权打分 → Bind（[scheduler.py](aikube/scheduler.py)） |
| kube-controller-manager | `ai-controller-manager` | 心跳巡检 / NotReady 驱逐 / 副本保持（[controller.py](aikube/controller.py)） |
| kubelet | `ai-kubelet` | 节点 Agent：注册 / 心跳 / 认领执行 Pod（[kubelet.py](aikube/kubelet.py)） |
| containerd | `ai-runtime` | LLM 执行器：mock 离线模拟 / OpenAI 兼容接口（ollama 可用）（[runtime.py](aikube/runtime.py)） |
| Calico | `ai-cni` | 节点发现 + 任务→节点路由表（内置于 apiserver） |
| kubectl | `aikube` CLI | `get / run / describe / logs / delete`（[cli.py](aikube/cli.py)） |
| kubeadm | `aikube cluster init` / `node join` | 集群初始化 / 节点加入（join token 校验） |

## 快速开始

```bash
cd cluster

# 1. 一键初始化 1 主 2 从（kubeadm init + join 的合体）
python3 -m aikube cluster init --name demo

# 2. 查看集群
python3 -m aikube get nodes

# 3. 提交 AI 任务（--mode auto 让 AI 调度器分类路由）
python3 -m aikube run "设计一个微服务架构上线方案"     # 含"方案"→ spec → Pro 节点
python3 -m aikube run "修复登录页面的 bug"             # 执行类 → react → Flash 节点
python3 -m aikube run "用 GPU 训练图像识别模型" --affinity gpu=true  # 标签亲和 → GPU 节点

# 4. 查看调度决策（每节点打分明细，kubectl describe 类比）
python3 -m aikube describe <pod名>
python3 -m aikube logs <pod名>

# 4b. 工具最小权限矩阵（控制面 API 面 + 各节点工具白名单）
python3 -m aikube get tools

# 4c. 执行面角色门控：worker 只保留任务执行相关命令（get pods / logs）
AIKUBE_ROLE=worker aikube run "越权任务"    # → 拒绝：执行面角色禁止命令
AIKUBE_ROLE=worker aikube get pods          # → 放行

# 4d. 任务显式请求工具：调度器按工具覆盖过滤节点，越权调用被沙箱拒绝并留痕
python3 -m aikube run "写代码修复" --tools code_exec   # 只会路由到有 code_exec 的节点
python3 -m aikube describe <pod名>                     # 工具: 请求/授权 + 调用留痕

# 5. 节点维护与故障自愈
python3 -m aikube node cordon k8s-node1   # 节点不可调度
python3 -m aikube node drain k8s-node1    # cordon + 驱逐，Pod 自动重调度
kill -9 $(cat ~/.aikube/run/k8s-node1.pid)  # 模拟节点宕机 → 10s 判 NotReady → 驱逐重调度

# 6. 新节点加入（kubeadm join 类比）
python3 -m aikube token create            # 如忘记 token
python3 -m aikube node join <token> --node k8s-node3 --model mock-flash

# 7. 停止 / 重启
python3 -m aikube cluster stop
python3 -m aikube cluster start
```

> 提示：`AIKUBE_HOME` 环境变量可换状态目录（默认 `~/.aikube`）；
> 接真实 LLM：`aikube cluster init --llm-url http://127.0.0.1:11434/v1`（本地 ollama）。

## AI 调度器（核心）

```
任务提交 → [AI 分类] → [Filter 过滤] → [Score 加权打分] → [Bind 绑定]
  mode=auto      LLM/规则        节点存活/可调度     负载 40%         写回 apiserver
                 四分类:         槽位/标签亲和/      能力匹配 30%      通知 kubelet 执行
                 spec/react/     能力匹配            亲和性 20%
                 mixed/weak                           延迟 10%
```

- **AI 分类**：优先调用 LLM（OpenAI 兼容，本地 ollama 即可），失败/未配置时规则兜底——
  复用套装 [router-standard](../preset/README.md) 的四行为带思想（spec=计划 / react=执行 /
  mixed=陷阱回避 / weak=模型自分类），Pro 节点吃 spec，Flash 节点吃 react。
- **打分透明**：每个 Pod 的 `describe` 输出全部候选节点的 load/capability/affinity/latency
  分项与总分、被过滤原因、AI 分类置信度——调度决策可审计。
- **工具最小权限**（k8s RBAC 类比）：控制面只暴露 14 个管理端点（无执行端点，`/exec` 类
  404），CLI 分角色（`--role worker` 只保留 get pods/logs）；执行面每个节点按能力推导
  工具白名单最小集，任务的 AI 分类推导所需工具，调度器做工具覆盖过滤，节点侧
  ToolSandbox 拒绝越权调用并写入 Pod 事件（`aikube get tools` 查看矩阵）。
- **自愈闭环**：控制器 10s 心跳超时 → NotReady → 驱逐 Pod → requeue → 调度器重绑定到
  健康节点；`replicas` 副本由控制器保持（Deployment 控制器类比）。

## 架构与设计

- 架构映射与术语表：[docs/architecture.md](docs/architecture.md)
- 调度算法论文（Filter/Score/Bind 数学定义）：[docs/paper.md](docs/paper.md)
- 实测记录（P1-P3 全绿）：[docs/experiments.md](docs/experiments.md)

## DSH Testkit 真实宿主门禁（已通过 ✅）

cluster 组件以原生 DSH 插件形态（[plugin/](plugin/)）通过
[DSH Testkit](https://github.com/iiwish/dsh-testkit)（
[deepseek-harness discussion #2038](https://github.com/deepseek-ai/deepseek-harness/discussions/2038)）
的真实宿主生命周期测试：在精确 DSH `0.1.0-rc.6` 中完成 install → boot → register
（service `aikubeCluster` + tool `aikube`）→ exercise（真实拉起 1 主 2 从集群、
spec/react 任务全部 Succeeded）→ uninstall → reboot → cleanup，**verdict passed**。
已连续验证两版：v0.1.0 基线与工具最小权限版（控制面 API 面无执行端点、节点工具
白名单、调度器工具覆盖过滤、ToolSandbox 越权拒绝留痕）。

- 门禁场景与复现：[plugin/README.md](plugin/README.md)
- 完整证据（report.json/md + junit + probe）：[plugin/evidence/](plugin/evidence/)

## 测试

```bash
cd cluster
python3 -m unittest discover -s tests -v     # 18 个单元测试
python3 -m unittest tests.test_e2e -v        # 3 个端到端（真实拉起集群）
```

## 安装（可选）

```bash
bash cluster/scripts/install.sh              # 软链 aikube 到 ~/.local/bin
bash cluster/scripts/demo.sh                 # 一键演示：起集群+跑任务+故障自愈
```

MIT License。
