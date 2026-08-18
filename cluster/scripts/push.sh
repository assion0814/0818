#!/usr/bin/env bash
# 推送脚本：把本地提交推送到 yjh051108/dsh-routing-suite
# 注意：本机 SSH 账号(assion0814)对该仓库无写权限，请用有权限的账号执行本脚本。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ "$(git config user.name 2>/dev/null)" == "" ]]; then
  echo "请先配置 git 身份："
  echo '  git config user.name "你的名字"'
  echo '  git config user.email "你的邮箱"'
  exit 1
fi

echo "==> 检查未提交变更"
git status --short

echo "==> 推送 main 到 GitHub (SSH)"
git push git@github.com:yjh051108/dsh-routing-suite.git main

echo "✅ 推送完成"
