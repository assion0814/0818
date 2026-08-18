#!/usr/bin/env bash
# 一键演示：初始化 1 主 2 从 AI 集群 → 提交三类任务 → 故障自愈
# 用法: bash cluster/scripts/demo.sh [--llm-url http://127.0.0.1:11434/v1]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LLM_ARGS=()
if [[ "${1:-}" == "--llm-url" && -n "${2:-}" ]]; then
  LLM_ARGS=(--llm-url "$2")
fi

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

say "0/5 初始化集群（1 主 2 从，kubeadm init 类比）"
python3 -m aikube cluster init --name demo "${LLM_ARGS[@]}"

say "1/5 节点状态（kubectl get nodes 类比）"
sleep 2
python3 -m aikube get nodes

say "2/5 提交三类 AI 任务（--mode auto 由调度器分类路由）"
python3 -m aikube run "设计一个微服务架构上线方案"
python3 -m aikube run "修复登录页面的 bug"
python3 -m aikube run "用 GPU 训练图像识别模型" --affinity gpu=true

say "3/5 等待执行并查看调度结果"
sleep 8
python3 -m aikube get pods
python3 -m aikube get nodes

say "4/5 查看调度决策明细（kubectl describe 类比）"
FIRST_POD=$(python3 -m aikube get pods | awk 'NR==2{print $1}')
python3 -m aikube describe "$FIRST_POD"

say "5/5 故障自愈演示：提交长任务，运行中杀掉其所在节点 kubelet"
python3 -m aikube run "重构核心模块并输出完整实施方案" --mode spec --size large > /dev/null
sleep 2.5
POD=$(python3 -m aikube get pods | awk '$5=="Running"{print $1; exit}')
NODE=$(python3 -m aikube get pods | awk -v p="$POD" '$1==p{print $4}')
echo "长任务 $POD 正在 $NODE 上执行，kill 其 kubelet（模拟节点宕机）…"
kill -9 "$(cat "${AIKUBE_HOME:-$HOME/.aikube}/run/${NODE}.pid")" 2>/dev/null || true
echo "已 kill ${NODE} 的 kubelet，等待控制器驱逐 + 重调度（约 25s）…"
sleep 25
echo "--- 该任务最终状态（应已重调度到健康节点并成功）---"
python3 -m aikube get pods | awk -v p="$POD" '$1==p'
python3 -m aikube describe "$POD" | grep -E "阶段|节点:|事件|evict|requeue"

say "演示完成。清理：python3 -m aikube cluster stop"
