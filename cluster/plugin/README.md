# dsh-aikube — DSH 插件包装（DSH Testkit 门禁对象）

把 [aikube AI K8s 集群调度网络](../README.md) 包装成原生 DSH 插件，通过
[DSH Testkit](https://github.com/iiwish/dsh-testkit)（见
[deepseek-harness discussion #2038](https://github.com/deepseek-ai/deepseek-harness/discussions/2038)）
的真实宿主生命周期门禁：

```
resolve → install-dsh → package → install-plugin → assemble → boot
→ register → exercise → uninstall → reboot → cleanup
```

## 插件内容

| 文件 | 作用 |
|---|---|
| `package.json` | npm 包 + `dsh.bundle.patch` 声明 |
| `cordis.patch.yml` | bundle 行插入：`id: aikube` |
| `index.js` | 注册 service `aikubeCluster` + tool `aikube`（10 动作） |
| `python/` | 打包的 aikube Python 引擎（`sync-python.sh` 从 `../aikube` 同步） |
| `dsh-testkit.yaml` | Testkit 场景：quick suite，exercise 调用 `aikube {action: smoke}` |

## tool `aikube` 动作面（控制面工具，共 10 个）

| 动作 | 参数 | 说明 |
|---|---|---|
| `init` | — | 幂等启动集群（未初始化则 init，已初始化则 start） |
| `start` / `stop` | — | 集群启停 |
| `run` | text, mode, tools | 提交任务（AI 分类路由 + 工具授权） |
| `get` | kind(nodes/pods/tasks/tools) | 查询（tools 显示控制面 API 面 + 节点白名单矩阵） |
| `describe` | pod | 调度决策 + 工具授权 + 越权留痕 |
| `logs` | pod | 任务执行输出 |
| `status` | — | 插件能力信息 |
| `smoke` | — | 全链路确定性测试（Testkit exercise） |

## 安装与设为会话模式

```bash
# 1. 安装插件到 DSH profile（与套装 injector 同一安装链）
dsh plugin --profile web add /home/assion/dsh-routing-suite/cluster/plugin

# 2. 安装 aikube 调度预设（会话模式）+ 设为默认
mkdir -p ~/.dsh/.agent-presets/aikube
cp preset/preset.yml preset/agent.cordis.yml ~/.dsh/.agent-presets/aikube/
#   并把 ~/.dsh/settings.yaml 的 agent-presets.default 改为 aikube

# 3. 重启 DSH web 服务 → 新会话即使用「AI 集群调度 (aikube)」模式
```

> 预设文件随插件仓库分发：`cluster/plugin/preset/`（preset.yml + agent.cordis.yml）。

> 预设说明：控制面 persona 只使用 `aikube` 工具（不保留与任务执行无关的工具），
> 任务一律交给集群调度执行；执行面工具授权由集群调度器按任务需求下发，
> 节点 ToolSandbox 拒绝越权并留痕。

## exercise 语义（确定性、无模型）

tool `aikube` 的 `smoke` 动作在真实 DSH 宿主进程内：
1. `aikube cluster init --name testkit`（1 主 2 从，kubeadm init 类比）
2. 轮询至 3 节点全 Ready（心跳注册）
3. 提交 spec + react 两个任务（`--mode auto`，AI 调度器分类路由）
4. 轮询至全部 Pod Succeeded
5. 输出 nodes/pods 表格，`cluster stop` 清理

全部走 mock 运行时，无需模型与 API key；Python 引擎随 npm 包一起打包
（Testkit 的 Docker runner 自带 python3）。

## 跑门禁

```bash
cd cluster/plugin
pnpm install
pnpm dsh-test --config dsh-testkit.yaml
# 产物：.dsh-testkit/runs/<run>/report.{json,md} + junit.xml + 阶段证据
```

本机实测（2026-08-19）：**verdict passed** 三轮——
`20260818161052-5d9eb334`（v0.1.0 基线）、`20260818165208-998d550f`（工具最小权限版）、
`20260819002902-8a866ed3`（v0.1.1：10 动作 + 端口互斥修复）。完整证据见 [evidence/](evidence/)。

实测踩坑（复现时注意）：
1. **镜像源**：本机 Docker 的 USTC mirror 已失效 → 改用官方支持的 local runner：
   `pnpm dsh-test --config dsh-testkit.yaml --runner local --unsafe-local`
   （local runner 需显式 `--unsafe-local`）。
2. **npm 加速**：国内网络对 registry.npmjs.org 极慢（install-dsh 会超时），
   先 `export NPM_CONFIG_REGISTRY=https://registry.npmmirror.com`（Testkit 会透传）。
3. **输出目录**：`--output` 必须放在插件源码目录之外，否则 Testkit 把运行根建在
   源码内 → `cp EINVAL (cannot copy ... to a subdirectory of self)`。
4. **exercise 确定性**：工具参数用空格分隔的节点名列表
   （`--nodes k8s-node1 k8s-node2`，不是逗号串），否则 init 会创建一个
   名为 `k8s-node1,k8s-node2` 的节点导致轮询超时。

Python 引擎变更后先同步：`bash cluster/plugin/sync-python.sh`

## 行/服务/工具预期（dsh-testkit.yaml）

- rows: `[aikube]`（对 `dsh --dump-config` 的 `id` 校验）
- services: `[aikubeCluster]`（Cordis 上下文内联探测）
- tools: `[aikube]`（真实 tool runtime 注册探测）
