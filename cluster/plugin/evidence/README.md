# DSH Testkit 门禁证据

真实宿主生命周期测试结果（DSH `0.1.0-rc.6`，场景 `aikube-cluster` quick/v1）：

| 项 | 值 |
|---|---|
| Verdict | **passed** |
| Run | `20260818161052-5d9eb334` |
| 插件 | `dsh-aikube@0.1.0`（digest `sha256:d658ef97…`） |
| 关键证据 | [report.json](report.json) / [report.md](report.md) / [junit.xml](junit.xml) |
| 阶段证据 | [probe-boot.json](probe-boot.json)（服务/工具注册 + exercise 结果）、[probe-reboot.json](probe-reboot.json)（卸载后 absent）、[effective-config.yml](effective-config.yml)（79 行，含 `aikube` 行） |

## 生命周期阶段（全绿）

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
