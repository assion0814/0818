# aikube 调度预设（会话模式）

把 DSH 会话模式设置为「AI 集群调度 (aikube)」：任务经 AI K8s 集群调度网络调度。

## 安装

```bash
mkdir -p ~/.dsh/.agent-presets/aikube
cp preset.yml agent.cordis.yml ~/.dsh/.agent-presets/aikube/
# 设为默认模式：
#   ~/.dsh/settings.yaml → agent-presets.default: aikube
# 重启 DSH web 服务 → 新会话生效
```

前提：dsh-aikube 插件已装入 profile（`dsh plugin --profile web add <plugin 目录>`），
集群已初始化（`aikube cluster init --name dsh --nodes k8s-node1 k8s-node2`，
或让会话内 agent 直接调用 `aikube action=init`）。
