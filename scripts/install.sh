#!/usr/bin/env bash
#
# Install garmin-mcp-bridge and register it with Claude Desktop.
# macOS and Linux. For Windows use scripts/install.ps1.
#
#   ./scripts/install.sh
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT_NAME="Claude Desktop"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
die()  { printf '  \033[31merror\033[0m %s\n' "$1" >&2; exit 1; }

case "$(uname -s)" in
    Darwin) CONFIG_PATH="$HOME/Library/Application Support/Claude/claude_desktop_config.json" ;;
    Linux)  CONFIG_PATH="$HOME/.config/Claude/claude_desktop_config.json" ;;
    *)      die "Unsupported OS: $(uname -s). Use scripts/install.ps1 on Windows." ;;
esac

bold "garmin-mcp-bridge installer"
echo

# --- 1. uv -------------------------------------------------------------------

bold "1/5  Checking for uv"
if ! command -v uv >/dev/null 2>&1; then
    warn "uv not found"
    read -r -p "  Install uv now via the official installer? [y/N] " reply
    if [[ "$reply" =~ ^[Yy]$ ]]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # The installer drops uv in one of these; pick up whichever exists.
        for candidate in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
            [[ -x "$candidate/uv" ]] && export PATH="$candidate:$PATH"
        done
        command -v uv >/dev/null 2>&1 || die "uv installed but not on PATH. Open a new shell and re-run."
    else
        die "uv is required. See https://docs.astral.sh/uv/getting-started/installation/"
    fi
fi
ok "uv $(uv --version | awk '{print $2}')"

# --- 2. environment ----------------------------------------------------------

bold "2/5  Building the environment"
(cd "$REPO_DIR" && uv sync --quiet)
PYTHON_BIN="$REPO_DIR/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || die "Expected interpreter missing at $PYTHON_BIN"
ok "$PYTHON_BIN"

# --- 3. API key --------------------------------------------------------------

bold "3/5  Intervals.icu API key"
echo "  Get it at https://intervals.icu/settings — bottom of the page, 'Developer Settings'."
API_KEY="${INTERVALS_API_KEY:-}"
if [[ -z "$API_KEY" ]]; then
    read -r -s -p "  Paste your API key: " API_KEY
    echo
fi
[[ -n "$API_KEY" ]] || die "No API key given."

# --- 4. verify ---------------------------------------------------------------

bold "4/5  Verifying against the live API"
if ! INTERVALS_API_KEY="$API_KEY" "$PYTHON_BIN" "$REPO_DIR/selftest.py"; then
    echo
    warn "Selftest reported failures."
    warn "If activities are missing, run the Garmin backfill: Intervals.icu >"
    warn "Settings > Connections > Garmin > 'Download old data'."
    read -r -p "  Register with $CLIENT_NAME anyway? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || die "Aborted."
fi

# --- 5. register -------------------------------------------------------------

bold "5/5  Registering with $CLIENT_NAME"
mkdir -p "$(dirname "$CONFIG_PATH")"
[[ -f "$CONFIG_PATH" ]] || echo '{}' > "$CONFIG_PATH"

CONFIG_PATH="$CONFIG_PATH" PYTHON_BIN="$PYTHON_BIN" API_KEY="$API_KEY" \
"$PYTHON_BIN" - <<'PY'
import json, os, shutil, sys

path = os.environ["CONFIG_PATH"]
try:
    with open(path, encoding="utf-8") as fh:
        config = json.load(fh)
except json.JSONDecodeError as exc:
    sys.exit(f"  error {path} is not valid JSON ({exc}). Fix or remove it, then re-run.")

if not isinstance(config, dict):
    sys.exit(f"  error {path} does not contain a JSON object.")

backup = path + ".bak"
shutil.copy(path, backup)

config.setdefault("mcpServers", {})["garmin"] = {
    "command": os.environ["PYTHON_BIN"],
    "args": ["-m", "garmin_mcp_bridge.server"],
    "env": {"INTERVALS_API_KEY": os.environ["API_KEY"]},
}

with open(path, "w", encoding="utf-8") as fh:
    json.dump(config, fh, indent=2, ensure_ascii=False)

print(f"  ok    servers now configured: {', '.join(config['mcpServers'])}")
print(f"  ok    backup written to {backup}")
PY

echo
bold "Done."
echo "  Quit $CLIENT_NAME completely (Cmd+Q, not just the window) and start it again."
echo "  Then ask it: \"Call check_connection.\""
echo
echo "  Your API key is stored in plain text in:"
echo "    $CONFIG_PATH"
echo "  See the README section 'Keeping the key out of the config' for an alternative."
