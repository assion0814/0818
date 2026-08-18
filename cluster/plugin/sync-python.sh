#!/usr/bin/env bash
# 同步 aikube Python 引擎到插件包（npm pack 只打包包内文件）
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rm -rf "$ROOT/plugin/python"
mkdir -p "$ROOT/plugin/python"
cp -r "$ROOT/aikube" "$ROOT/plugin/python/aikube"
rm -rf "$ROOT/plugin/python/aikube/__pycache__"
echo "✅ plugin/python/aikube 已同步（$(find "$ROOT/plugin/python" -name '*.py' | wc -l) 个 py 文件）"
