# dsh-routing-suite — 注入器 × 思维模式路由 × AI 集群调度 套装

一个仓库装齐「运行时手术台 + 思维模式路由预设 + AI K8s 集群调度网络」：先装注入器
（免重启运行时管理层），再用它装配 router-standard 预设（任务感知思维模式路由，P1-P23
实测），最后可选搭建 aikube 集群（把 k8s 集群架构复刻为 AI 调度网络，P1-P3 实测）。

[中文](README.md) | [English](README.en.md)

## 安装链（三步）

```powershell
# 1. 拉套装（preset/cluster 在仓库内，injector 为 submodule）
git clone --recurse-submodules https://github.com/yjh051108/dsh-routing-suite.git
cd dsh-routing-suite

# 2. 一键安装（注入器装配 + 预设复制 + 提示重启）
.\install.ps1
```

或手动：

```powershell
# 步骤 1：装配注入器（官方装配，重启后由 bundles 接管）
dsh plugin --profile web add .\injector

# 步骤 2：安装 router-standard 预设
$target = Join-Path $env:USERPROFILE '.dsh\.agent-presets\router-standard'
Copy-Item -Recurse .\preset\preset $target

# 步骤 3：重启 DSH → 新会话选择 Router Standard (experimental)

# 步骤 4（可选）：搭建 AI K8s 集群调度网络（跨平台，需要 Python 3.9+）
bash cluster/scripts/install.sh
aikube cluster init --name demo
aikube run "设计一个微服务架构上线方案"    # 自动分类路由到 Pro 节点
```

## 组件

| 路径 | 仓库 | 版本 | 作用 |
|---|---|---|---|
| `injector/` | [dsh-super-injector](https://github.com/yjh051108/dsh-super-injector) | [v0.3.3](https://github.com/yjh051108/dsh-super-injector/releases/tag/v0.3.3) | 运行时注入器：dev_* 工具全家桶（注入/热重载/侧挂转正/卸载/路由自愈） |
| `preset/` | vendor 副本（原 [dsh-router-standard](https://github.com/yjh051108/dsh-router-standard) @ eff787e9） | v0.3.0 + 修复 | 思维模式路由预设：router-standard / router-spec / router-pro；**已含 extractText 导入修复**（见 [BUG-REPORT.md](BUG-REPORT.md)） |
| `cluster/` | 本仓库内（可独立演进） | v0.1.1 | AI K8s 集群调度网络：ai-apiserver/ai-etcd/ai-scheduler/ai-controller/ai-kubelet + aikube CLI，纯 Python 标准库 |

> `preset/` 因上游（yjh051108/dsh-router-standard）无写权限且修复急需落地，
> 由 submodule 改为**仓库内直接存放**（vendor 修复副本：eff787e9 树 + extractText 修复，
> 补丁见 [fix-extractText-import.patch](fix-extractText-import.patch)）。
> 获得上游写权限后可恢复 submodule 形态（见 BUG-REPORT 推送路径说明）。
> `injector/` 仍为 submodule（指向 dsh-super-injector main）。

injector 独立演进（submodule 指向其 main），preset 与 cluster 聚合在仓库内，
三者共用「任务感知路由」思想：preset 在会话内路由思维模式，cluster 在集群内路由任务。

## router-standard 预设能力（P1-P23 实测摘要）

- **三行为带 + weak 内路由**：spec（计划-集体）/ react（执行者）/ mixed（陷阱，回避）/ weak（模型自分类）
- **按模型选 persona**：Pro=spec 句+few-shot（区分度 +5.0）；Flash=neutral+classify（+5.7）
- **近距离引导**：每轮用户消息后注入固定引导（缓存 92-94% 命中），路由 96% + 收敛 100% + 反稀释
- **单任务三锚**（persona 静态）：回顾 + 收敛 + 反跑题 —— 开放任务完成率 0% → 100%
- **plan-mode 保留**：只替换 persona section，plan 边界不失忆
- **AI 自优化工具**：`dev_router_status` / `dev_router_mode` / `dev_mode_subagent`

## 文档

- 注入器引导（规范铁律 10 条）：`injector/README.md`
- 路由预设论文与实验：`preset/docs/paper.md` + `preset/docs/experiments.md`（P1-P23）
- AI 集群组件：`cluster/README.md` + `cluster/docs/architecture.md`（K8s 映射）
  + `cluster/docs/paper.md`（调度算法）+ `cluster/docs/experiments.md`（P1-P3）
- 一键演示：`bash cluster/scripts/demo.sh`

## 许可证

MIT。致谢：xiaobright/modeltest（V4.1b 评测）、xiaobright/dsh-anchored-standard（锚定机制）。

