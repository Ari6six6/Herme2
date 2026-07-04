#!/usr/bin/env bash
#
# Hermes offline installer / updater.
#
#   Usage:   ./install-hermes.sh
#
# Installs (or updates) Hermes from the bundled wheelhouse — no git, no PyPI,
# no network. Runs as your normal user; no sudo needed. It drops Hermes into a
# private venv (~/.hermes-venv) and puts `hermes` on your PATH.
#
# This is NOT the box bootstrap. It does not touch the firewall, the WireGuard
# killswitch, Docker, or system hardening. On a brand-new box, run the full
# setup.sh once for all of that; use THIS script whenever you just want to
# install or update Hermes itself from a wheelhouse you scp'd over.

set -Eeuo pipefail

c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_cyn=$'\033[36m'; c_red=$'\033[31m'; c_rst=$'\033[0m'
say()  { printf '%s==>%s %s\n' "$c_cyn" "$c_rst" "$*"; }
ok()   { printf '%s ok %s %s\n' "$c_grn" "$c_rst" "$*"; }
warn() { printf '%swarn%s %s\n' "$c_ylw" "$c_rst" "$*" >&2; }
die()  { printf '%sERR %s %s\n' "$c_red" "$c_rst" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHEELHOUSE="${HERMES_WHEELHOUSE:-$SCRIPT_DIR/wheelhouse}"
VENV="${HERMES_VENV:-$HOME/.hermes-venv}"
BIN_DIR="$HOME/.local/bin"

# ---------------------------------------------------------------------------
say "Checking the bundle"
[[ -d "$WHEELHOUSE" ]] || die "wheelhouse not found at $WHEELHOUSE (run this from the unpacked bundle dir)"
APP_WHL="$(ls "$WHEELHOUSE"/hermes_agent-*.whl 2>/dev/null | head -1 || true)"
[[ -n "$APP_WHL" ]] || die "no hermes_agent wheel in $WHEELHOUSE"
ok "wheelhouse at $WHEELHOUSE ($(ls -1 "$WHEELHOUSE"/*.whl | wc -l) wheels)"
ok "app: $(basename "$APP_WHL")"

# ---------------------------------------------------------------------------
say "Locating Python"
PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || die "python3 not found — on a fresh box run setup.sh first (or: sudo apt install python3 python3-venv)"
if ! "$PY" -m venv --help >/dev/null 2>&1; then
  die "python3 venv module missing — install it:  sudo apt install python3-venv"
fi
ok "using $("$PY" --version 2>&1) at $PY"

# ---------------------------------------------------------------------------
say "Preparing the venv at $VENV"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PY" -m venv "$VENV"
  ok "created venv"
else
  ok "reusing existing venv"
fi

# ---------------------------------------------------------------------------
say "Installing Hermes (offline, from the wheelhouse)"
# --no-index + --find-links => never touches the network (safe under the
# killswitch). --force-reinstall guarantees the app is replaced even when the
# version number is unchanged.
"$VENV/bin/pip" install \
  --no-index --find-links "$WHEELHOUSE" \
  --force-reinstall --upgrade \
  hermes-agent
INSTALLED_VER="$("$VENV/bin/pip" show hermes-agent 2>/dev/null | awk '/^Version:/{print $2}')"
ok "installed hermes-agent $INSTALLED_VER"

# ---------------------------------------------------------------------------
say "Putting hermes on your PATH"
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/hermes" "$BIN_DIR/hermes"
ok "symlinked $BIN_DIR/hermes -> $VENV/bin/hermes"

case ":$PATH:" in
  *":$BIN_DIR:"*) : ;;
  *)
    if ! grep -q '.local/bin' "$HOME/.bashrc" 2>/dev/null; then
      echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
      warn "added ~/.local/bin to PATH in ~/.bashrc — run 'source ~/.bashrc' or re-login"
    fi
    ;;
esac

# ---------------------------------------------------------------------------
echo
say "Done — Hermes $INSTALLED_VER is installed."
echo
echo "  Start it:   hermes        (if 'command not found', run: source ~/.bashrc)"
echo
echo "  This release ships the full-power configuration as the default — every"
echo "  evolved feature (directives, compaction, skills, delegation, prefix-cache"
echo "  ordering, verify-before-done) is now ON out of the box. Anything you set"
echo "  yourself in ~/.hermes/config.json still wins. To dial one back:"
echo "      hermes  ->  config set <flag> false"
echo
ok "Enjoy."
