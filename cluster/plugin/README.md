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
| `index.js` | 注册 service `aikubeCluster` + tool `aikube` |
| `python/` | 打包的 aikube Python 引擎（`sync-python.sh` 从 `../aikube` 同步） |
| `dsh-testkit.yaml` | Testkit 场景：quick suite，exercise 调用 `aikube {action: smoke}` |

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

本机实测（2026-08-18，run `20260818161052-5d9eb334`）：**verdict passed**，
11 个生命周期阶段全绿（resolve/install-dsh/package/install-plugin/assemble/boot/
register/exercise/uninstall/reboot/cleanup），完整证据见 [evidence/](evidence/)。

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
