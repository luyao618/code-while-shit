#!/usr/bin/env sh
# Install cws globally via uv tool.
# Usage: curl -fsSL https://raw.githubusercontent.com/luyao618/code-while-shit/main/scripts/install.sh | sh
set -e
# 1. Install uv if missing
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Source uv's env so this script can use it immediately
  . "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
fi
# 2. Install cws as a uv tool from this repo
uv tool install --force git+https://github.com/luyao618/code-while-shit.git
# 3. Ensure ~/.local/bin is in PATH (uv tool puts binaries there)
uv tool update-shell || true
echo ""
echo "✅ cws installed. Open a new shell or run: source ~/.zshrc (or ~/.bashrc)"
echo "Next: cws init  # creates ~/.config/cws/config.toml"
echo "      cws config set feishu.app_id YOUR_APP_ID"
echo "      cws config set feishu.app_secret YOUR_APP_SECRET"
echo "      cd /path/to/your/project && cws serve  # workspace = cwd"
