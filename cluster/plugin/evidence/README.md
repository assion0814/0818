# DSH Testkit 门禁证据

真实宿主生命周期测试结果（DSH `0.1.0-rc.6`，场景 `aikube-cluster` quick/v1）。

## 最新：工具最小权限版（v0.1.0+TLP）✅

| 项 | 值 |
|---|---|
| Verdict | **passed** |
| Run | `20260818165208-998d550f`（[report.json](run-20260818165208/report.json) / [report.md](run-20260818165208/report.md) / [junit.xml](run-20260818165208/junit.xml)） |
| 阶段证据 | [probe-boot.json](run-20260818165208/probe-boot.json)（service/tool 注册 + exercise）、[probe-reboot.json](run-20260818165208/probe-reboot.json)（卸载后 absent）、[effective-config.yml](run-20260818165208/effective-config.yml)（79 行，含 `aikube` 行） |

本次验证的是**带工具最小权限（Tool Least-Privilege）的引擎**：控制面 API 面仅管理端点
（无执行端点），执行面节点按能力推导工具白名单，调度器工具覆盖过滤 + 节点侧
ToolSandbox 越权拒绝留痕。exercise 仍在真实 DSH 宿主内完成 1 主 2 从集群
init → Ready → spec/react 任务调度（含工具授权）→ Succeeded → 清理。

## 历史：v0.1.0 基线 ✅

- Run `20260818161052-5d9eb334`（[report.json](run-20260818161052/report.json)）：
  同一生命周期 11 阶段全绿（未含工具最小权限层）。

## 生命周期阶段（全绿，两版一致）

```
resolve ✓ → install-dsh ✓(精确 0.1.0-rc.6) → package ✓(npm pack) → install-plugin ✓
→ assemble ✓(79 rows, 含 id=aikube) → boot ✓ → register ✓(service aikubeCluster
+ tool aikube) → exercise ✓(真实拉起 1主2从集群, spec/react 任务全部 Succeeded)
→ uninstall ✓ → reboot ✓(无插件重启) → cleanup ✓(无残留, canary 无日志泄露)
```

## 复现

```bash
cd cluster/plugin
NPM_CONFIG_REGISTRY=https://registry.npmmirror.com \   # 国内网络加速，可选
  pnpm dsh-test --config dsh-testkit.yaml \
  --runner local --unsafe-local \
  --output /home/assion/dsh-routing-suite/cluster/.dsh-testkit-out
```

> 注意：`--output` 必须放在插件源码目录**之外**，否则 Testkit 把运行根目录建在源码内
> 导致 `cp EINVAL (cannot copy ... to a subdirectory of self)`。
> Docker runner 需可用的 registry mirror（本机 USTC mirror 失效，故用 local runner）。
