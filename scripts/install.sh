#!/usr/bin/env sh
# Install cws globally via uv tool.
# Usage: curl -fsSL https://raw.githubusercontent.com/luyao618/code-while-shit/main/scripts/install.sh | sh
set -e

# 1. Install uv if missing
if ! command -v uv >/dev/null 2>&1; then
  echo "→ uv not found, installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Source uv's env so this script can use it immediately
  . "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Install cws as a uv tool from this repo
echo "→ Installing cws via uv tool..."
uv tool install --force git+https://github.com/luyao618/code-while-shit.git

# 3. Update shell PATH for future sessions
uv tool update-shell || true

# 4. Verify cws is reachable in current/future shells
INSTALL_BIN="$HOME/.local/bin"
echo ""
if command -v cws >/dev/null 2>&1; then
  echo "✅ cws installed: $(command -v cws)"
else
  case ":$PATH:" in
    *":$INSTALL_BIN:"*)
      echo "⚠️  cws is on PATH but not yet found by this shell — open a new shell."
      ;;
    *)
      echo "⚠️  cws was installed to $INSTALL_BIN/cws but that directory is NOT on your PATH."
      echo "    Add this line to your shell rc (~/.zshrc or ~/.bashrc) and restart the shell:"
      echo "        export PATH=\"\$HOME/.local/bin:\$PATH\""
      ;;
  esac
fi

echo ""
echo "Next steps:"
echo "  cws init                                       # creates ~/.config/cws/config.toml"
echo "  cws config set feishu.app_id YOUR_APP_ID"
echo "  cws config set feishu.app_secret YOUR_APP_SECRET"
echo "  cd /path/to/your/project && cws serve          # workspace = current directory"
