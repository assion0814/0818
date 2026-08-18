#!/usr/bin/env bash
# 安装 aikube 到 ~/.local/bin（软链，跨平台 Python 3.9+）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${HOME}/.local/bin"
mkdir -p "$BIN"

cat > "$BIN/aikube" <<EOF
#!/usr/bin/env bash
# aikube launcher —— 指向 dsh-routing-suite/cluster
export PYTHONPATH="$ROOT:\${PYTHONPATH:-}"
exec python3 -m aikube "\$@"
EOF
chmod +x "$BIN/aikube"

echo "✅ aikube 已安装: $BIN/aikube"
echo "   用法: aikube cluster init --name demo"
echo "   如 PATH 未包含 $BIN，请执行: export PATH=\"\$HOME/.local/bin:\$PATH\""
