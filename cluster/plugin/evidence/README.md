# DSH Testkit 门禁证据

真实宿主生命周期测试结果（DSH `0.1.0-rc.6`，场景 `aikube-cluster` quick/v1）。

## 最新：v0.1.1（10 动作 + 端口互斥）✅

| 项 | 值 |
|---|---|
| Verdict | **passed** |
| 插件 | `dsh-aikube@0.1.1`（digest `sha256:73949dd5…`） |
| Run | `20260819002902-8a866ed3`（[report.json](run-20260819002902-8a866ed3/report.json) / [report.md](run-20260819002902-8a866ed3/report.md) / [junit.xml](run-20260819002902-8a866ed3/junit.xml)） |
| 阶段证据 | [probe-boot.json](run-20260819002902-8a866ed3/probe-boot.json)、[probe-reboot.json](run-20260819002902-8a866ed3/probe-reboot.json)、[effective-config.yml](run-20260819002902-8a866ed3/effective-config.yml) |

tool `aikube` 动作面 10 个（init/start/stop/run/get/describe/logs/status/smoke），
exercise 为 smoke 全链路（1 主 2 从 init → Ready → spec/react 调度 → Succeeded）。
本轮修复 etcd/apiserver 端口回退冲突（free_port 互斥解析），smoke 用独立端口段
（--port-base 24000）。

## 历史

- `20260818165208-998d550f`：工具最小权限版（TLP 引擎）✅
- `20260818161052-5d9eb334`：v0.1.0 基线 ✅

## 生命周期阶段（全绿，各版一致）

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
