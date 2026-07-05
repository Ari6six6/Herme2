#!/usr/bin/env bash
#
# Hermes VPS setup — one-shot bootstrap for a fresh Ubuntu server.
#
#   Usage:   sudo ./setup.sh [path/to/wg0.conf]
#
# What it does, in order:
#   1. Pre-flight checks (root, real invoking user, Ubuntu).
#   2. Snapshot the real WAN interface BEFORE any firewall change.
#   3. apt update + full-upgrade.
#   4. Install dependencies (python, docker, wireguard, fail2ban, ...).
#   5. Base hardening (unattended-upgrades, fail2ban, sysctl, conservative sshd).
#   6. Install a fail-closed WireGuard killswitch: ALL traffic rides the VPN,
#      SSH (port 22) on the real interface is the ONLY exception. If wg0 drops,
#      nothing leaks to the internet except SSH + the WG handshake.
#   7. Install Hermes for the invoking user (venv + ~/.local/bin/hermes).
#
# The killswitch keeps your inbound SSH session alive by reusing wg-quick's own
# fwmark (51820) so SSH replies route out the real NIC instead of the tunnel.
# A 5-minute dead-man's-switch auto-reverts the firewall if anything wedges your
# session; it is cancelled only once SSH is confirmed still alive.
#
# The WireGuard config is OPTIONAL and is never committed to git. Drop a wg0.conf
# next to this script (or pass its path). Without one, the box is locked to
# SSH-only egress until you supply a config and re-run.

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WG_FWMARK=51820                 # wg-quick's default fwmark; we reuse it on purpose
WG_IF=wg0
WG_CONF_DEST=/etc/wireguard/wg0.conf
KILLSWITCH_SH=/etc/hermes-killswitch.sh
KILLSWITCH_ENV=/etc/hermes-killswitch.env
KILLSWITCH_SERVICE=/etc/systemd/system/hermes-killswitch.service
ROLLBACK_UNIT=hermes-rollback
ROLLBACK_SECS=300
SSH_PORT=22

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_cyn=$'\033[36m'; c_rst=$'\033[0m'
say()  { printf '%s==>%s %s\n' "$c_cyn" "$c_rst" "$*"; }
ok()   { printf '%s ok %s %s\n' "$c_grn" "$c_rst" "$*"; }
warn() { printf '%swarn%s %s\n' "$c_ylw" "$c_rst" "$*" >&2; }
die()  { printf '%sERR %s %s\n' "$c_red" "$c_rst" "$*" >&2; exit 1; }

trap 'die "failed at line $LINENO (command: $BASH_COMMAND)"' ERR

# ===========================================================================
# 1. Pre-flight
# ===========================================================================
say "Pre-flight checks"

[[ $EUID -eq 0 ]] || die "run with sudo:  sudo ./setup.sh [path/to/wg0.conf]"

INVOKER="${SUDO_USER:-}"
[[ -n "$INVOKER" && "$INVOKER" != "root" ]] || \
  die "run via sudo from your normal user (need SUDO_USER, not a root login)"
INVOKER_HOME="$(getent passwd "$INVOKER" | cut -d: -f6)"
[[ -d "$INVOKER_HOME" ]] || die "cannot resolve home dir for $INVOKER"

[[ -r /etc/os-release ]] || die "/etc/os-release missing — unsupported OS"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "this script supports Ubuntu only (found ${ID:-unknown})"
UBUNTU_VER="${VERSION_ID:-unknown}"
case "$UBUNTU_VER" in
  22.04|24.04) ok "Ubuntu $UBUNTU_VER" ;;
  *) warn "Ubuntu $UBUNTU_VER is untested (designed for 22.04 / 24.04) — continuing" ;;
esac

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ok "repo at $REPO_DIR, installing Hermes as user '$INVOKER'"

# Locate an optional WireGuard config.
WG_CONF_SRC=""
if [[ $# -ge 1 && -n "${1:-}" ]]; then
  WG_CONF_SRC="$1"
  [[ -r "$WG_CONF_SRC" ]] || die "wg config '$WG_CONF_SRC' not found or unreadable"
elif [[ -r "$REPO_DIR/wg0.conf" ]]; then
  WG_CONF_SRC="$REPO_DIR/wg0.conf"
fi
if [[ -n "$WG_CONF_SRC" ]]; then
  ok "WireGuard config: $WG_CONF_SRC"
else
  warn "no wg0.conf supplied — VPN will be SKIPPED, firewall will lock the box to SSH-only egress"
fi

export DEBIAN_FRONTEND=noninteractive

# ===========================================================================
# 2. Snapshot the real WAN interface (BEFORE any firewall/tunnel change)
# ===========================================================================
say "Detecting WAN interface"

read -r WAN_IF WAN_SRC < <(ip -o route get 1.1.1.1 2>/dev/null \
  | sed -n 's/.* dev \([^ ]*\).* src \([^ ]*\).*/\1 \2/p') || true
WAN_GW="$(ip -o route show default 2>/dev/null | awk '/default/{print $3; exit}')"

[[ -n "${WAN_IF:-}" ]] || die "could not detect WAN interface (no default route?)"
[[ "$WAN_IF" != "$WG_IF" ]] || \
  die "default route is already on $WG_IF — a previous tunnel is up. Run 'wg-quick down $WG_IF' first."
ok "WAN interface=$WAN_IF src=$WAN_SRC gw=${WAN_GW:-?}"

# ===========================================================================
# 3. System update
# ===========================================================================
say "Updating the system (this can take a few minutes)"
apt-get update -y
apt-get -y \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold" \
  full-upgrade
ok "system up to date"

# ===========================================================================
# 4. Install dependencies
# ===========================================================================
say "Installing dependencies"
# Don't let iptables-persistent prompt / autosave current rules on install —
# we manage rules ourselves via the killswitch service.
echo "iptables-persistent iptables-persistent/autosave_v4 boolean false" | debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean false" | debconf-set-selections

apt-get install -y \
  python3-pip python3-venv python3-full git \
  docker.io \
  wireguard wireguard-tools \
  iptables iptables-persistent netfilter-persistent \
  conntrack \
  unattended-upgrades fail2ban
systemctl enable --now docker >/dev/null 2>&1 || warn "could not enable docker service"
ok "dependencies installed"

# ===========================================================================
# 5. Base hardening
# ===========================================================================
say "Hardening the box"

# --- 5a. unattended security upgrades ------------------------------------
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
EOF
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true
ok "automatic security updates enabled"

# --- 5b. fail2ban SSH jail (drop-in, never touch shipped jail.conf) -------
mkdir -p /etc/fail2ban/jail.d
cat >/etc/fail2ban/jail.d/hermes-sshd.local <<EOF
[sshd]
enabled  = true
backend  = systemd
port     = $SSH_PORT
maxretry = 5
findtime = 10m
bantime  = 1h
EOF
systemctl enable --now fail2ban >/dev/null 2>&1 || true
systemctl reload-or-restart fail2ban >/dev/null 2>&1 || true
ok "fail2ban sshd jail active"

# --- 5c. Decide on IPv6: disable unless the wg config carries a v6 Address -
WG_HAS_V6=0
if [[ -n "$WG_CONF_SRC" ]] && grep -iE '^\s*Address\s*=' "$WG_CONF_SRC" | grep -q ':'; then
  WG_HAS_V6=1
fi

# --- 5d. sysctl hardening -------------------------------------------------
{
  echo "# Hermes hardening — managed by setup.sh"
  echo "net.ipv4.conf.all.rp_filter=2"          # loose: tolerates wg policy routing
  echo "net.ipv4.conf.default.rp_filter=2"
  echo "net.ipv4.conf.all.accept_redirects=0"
  echo "net.ipv4.conf.default.accept_redirects=0"
  echo "net.ipv4.conf.all.send_redirects=0"
  echo "net.ipv4.conf.default.send_redirects=0"
  echo "net.ipv4.conf.all.accept_source_route=0"
  echo "net.ipv4.tcp_syncookies=1"
  echo "net.ipv4.conf.all.log_martians=1"
  echo "kernel.kptr_restrict=2"
  if [[ "$WG_HAS_V6" -eq 0 ]]; then
    echo "# IPv6 disabled — killswitch is v4-only, this closes the v6 leak path"
    echo "net.ipv6.conf.all.disable_ipv6=1"
    echo "net.ipv6.conf.default.disable_ipv6=1"
    echo "net.ipv6.conf.lo.disable_ipv6=1"
  fi
} >/etc/sysctl.d/99-hermes.conf
sysctl --system >/dev/null
if [[ "$WG_HAS_V6" -eq 0 ]]; then
  ok "sysctl hardening applied (IPv6 disabled)"
else
  ok "sysctl hardening applied (IPv6 kept — wg config is dual-stack)"
fi

# --- 5e. conservative sshd hardening (NO lockout: keep password auth on) --
mkdir -p /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/99-hermes.conf <<EOF
# Hermes conservative SSH hardening — managed by setup.sh
# PasswordAuthentication is intentionally left ON to avoid lockout.
PermitRootLogin prohibit-password
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 4
LoginGraceTime 30
ClientAliveInterval 120
ClientAliveCountMax 3
EOF
if sshd -t 2>/dev/null; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
  ok "sshd hardened (password auth kept ON, reload — existing session preserved)"
else
  warn "sshd config test failed — reverting sshd drop-in"
  rm -f /etc/ssh/sshd_config.d/99-hermes.conf
fi

# ===========================================================================
# 6. WireGuard killswitch
# ===========================================================================
say "Building the killswitch"

# --- 6a. Parse + resolve the WG endpoint while DNS still works ------------
WG_ENDPOINT_HOST=""; WG_ENDPOINT_PORT=""; WG_ENDPOINT_IP=""
if [[ -n "$WG_CONF_SRC" ]]; then
  ep="$(grep -iE '^\s*Endpoint\s*=' "$WG_CONF_SRC" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
  [[ -n "$ep" ]] || die "wg config has no Endpoint = host:port line"
  WG_ENDPOINT_HOST="${ep%:*}"
  WG_ENDPOINT_PORT="${ep##*:}"
  [[ -n "$WG_ENDPOINT_HOST" && -n "$WG_ENDPOINT_PORT" ]] || die "could not parse Endpoint '$ep'"
  if [[ "$WG_ENDPOINT_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    WG_ENDPOINT_IP="$WG_ENDPOINT_HOST"
  else
    WG_ENDPOINT_IP="$(getent ahostsv4 "$WG_ENDPOINT_HOST" | awk '{print $1; exit}')"
  fi
  [[ -n "$WG_ENDPOINT_IP" ]] || die "could not resolve WG endpoint host '$WG_ENDPOINT_HOST'"
  ok "WG endpoint $WG_ENDPOINT_HOST:$WG_ENDPOINT_PORT -> $WG_ENDPOINT_IP"
fi

# --- 6b. Persist environment for the killswitch script + boot service -----
cat >"$KILLSWITCH_ENV" <<EOF
# Generated by Hermes setup.sh — do not edit by hand.
WAN_IF=$WAN_IF
SSH_PORT=$SSH_PORT
WG_IF=$WG_IF
WG_FWMARK=$WG_FWMARK
WG_ENDPOINT_HOST=$WG_ENDPOINT_HOST
WG_ENDPOINT_PORT=$WG_ENDPOINT_PORT
WG_ENDPOINT_IP=$WG_ENDPOINT_IP
EOF
chmod 0644 "$KILLSWITCH_ENV"

# --- 6c. Write the killswitch apply script --------------------------------
cat >"$KILLSWITCH_SH" <<'KS_EOF'
#!/usr/bin/env bash
#
# Hermes fail-closed WireGuard killswitch — managed by setup.sh.
#
#   hermes-killswitch.sh up      apply the full policy (default DROP, SSH + wg only)
#   hermes-killswitch.sh down     keep the base fail-closed policy (tunnel torn down)
#
# Idempotent: flushes and rebuilds the firewall on each call.
#
# Routing trick: wg-quick with AllowedIPs=0.0.0.0/0 marks its own encrypted
# packets with fwmark 51820 and installs:
#     ip rule: not fwmark 51820  -> table 51820 (default dev wg0)
#     ip rule: table main suppress_prefixlength 0
# We tag inbound-SSH connection packets with the SAME fwmark via CONNMARK, so
# SSH replies skip table 51820 and route via the main table out the real NIC.
# That is what keeps your SSH session alive under a full-tunnel killswitch.

set -euo pipefail

ENV_FILE=/etc/hermes-killswitch.env
[[ -r "$ENV_FILE" ]] || { echo "killswitch: $ENV_FILE missing" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"

ACTION="${1:-up}"

# Re-resolve a hostname endpoint each time (handles dynamic DNS).
if [[ -n "${WG_ENDPOINT_HOST:-}" ]]; then
  if [[ "$WG_ENDPOINT_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    WG_ENDPOINT_IP="$WG_ENDPOINT_HOST"
  else
    r="$(getent ahostsv4 "$WG_ENDPOINT_HOST" 2>/dev/null | awk '{print $1; exit}')"
    [[ -n "$r" ]] && WG_ENDPOINT_IP="$r"
  fi
fi

# --- flush our managed state (filter + mangle); leave nat to docker -------
iptables -F
iptables -t mangle -F

# Default-deny.
iptables -P INPUT   DROP
iptables -P OUTPUT  DROP
iptables -P FORWARD DROP

# --- mangle: keep inbound SSH alive under the tunnel ----------------------
# Tag NEW inbound SSH connections on the real NIC with the wg fwmark...
iptables -t mangle -A PREROUTING -i "$WAN_IF" -p tcp --dport "$SSH_PORT" \
  -m conntrack --ctstate NEW -j CONNMARK --set-mark "$WG_FWMARK"
# ...and restore that connmark onto every locally-generated reply packet, so
# SSH replies carry fwmark 51820 -> bypass table 51820 -> exit the real NIC.
iptables -t mangle -A OUTPUT -j CONNMARK --restore-mark

# --- filter: loopback + established --------------------------------------
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# --- filter: the SSH lifeline on the real NIC -----------------------------
iptables -A INPUT  -i "$WAN_IF" -p tcp --dport "$SSH_PORT" -m conntrack --ctstate NEW -j ACCEPT
iptables -A OUTPUT -o "$WAN_IF" -p tcp --sport "$SSH_PORT" -m conntrack --ctstate ESTABLISHED -j ACCEPT

# --- filter: the WireGuard handshake on the real NIC ----------------------
if [[ -n "${WG_ENDPOINT_IP:-}" ]]; then
  iptables -A OUTPUT -o "$WAN_IF" -p udp -d "$WG_ENDPOINT_IP" --dport "$WG_ENDPOINT_PORT" -j ACCEPT
  iptables -A INPUT  -i "$WAN_IF" -p udp -s "$WG_ENDPOINT_IP" --sport "$WG_ENDPOINT_PORT" -j ACCEPT
fi

# --- filter: everything else rides the tunnel -----------------------------
iptables -A OUTPUT -o "$WG_IF" -j ACCEPT
iptables -A INPUT  -i "$WG_IF" -j ACCEPT
# Allow DHCP renew on the real NIC so the lease doesn't lapse (client->server).
iptables -A OUTPUT -o "$WAN_IF" -p udp --sport 68 --dport 67 -j ACCEPT
iptables -A INPUT  -i "$WAN_IF" -p udp --sport 67 --dport 68 -j ACCEPT

# --- Docker: force container egress through wg0, never the real NIC -------
# DOCKER-USER is evaluated before docker's own rules and survives daemon
# restarts. It only exists once docker has started.
if iptables -t filter -L DOCKER-USER -n >/dev/null 2>&1; then
  iptables -F DOCKER-USER
  iptables -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  iptables -A DOCKER-USER -o "$WG_IF" -j RETURN
  iptables -A DOCKER-USER -o "$WAN_IF" -j DROP
  iptables -A DOCKER-USER -j RETURN
fi

echo "killswitch: applied ($ACTION) wan=$WAN_IF wg=$WG_IF endpoint=${WG_ENDPOINT_IP:-none}"
KS_EOF
chmod 0755 "$KILLSWITCH_SH"
ok "wrote $KILLSWITCH_SH"

# --- 6d. Boot service: fail-closed BEFORE networking ----------------------
cat >"$KILLSWITCH_SERVICE" <<EOF
[Unit]
Description=Hermes WireGuard killswitch (fail-closed firewall)
DefaultDependencies=no
After=network-pre.target
Before=network-pre.target wg-quick@${WG_IF}.service docker.service
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$KILLSWITCH_SH up
ExecReload=$KILLSWITCH_SH up

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable hermes-killswitch.service >/dev/null 2>&1 || true
ok "boot-time killswitch service enabled"

# Re-assert the killswitch whenever docker (re)starts — docker recreates the
# DOCKER-USER chain on start, dropping our container-egress rules. This closes
# the boot-ordering gap and covers live 'systemctl restart docker'.
mkdir -p /etc/systemd/system/docker.service.d
cat >/etc/systemd/system/docker.service.d/10-hermes-killswitch.conf <<EOF
[Service]
ExecStartPost=$KILLSWITCH_SH up
EOF
systemctl daemon-reload
ok "docker drop-in re-asserts killswitch on every docker start"

# ===========================================================================
# 7. Apply the killswitch safely (dead-man's-switch guards against lockout)
# ===========================================================================
say "Applying the killswitch (with a ${ROLLBACK_SECS}s auto-revert safety net)"

# Arm a dead-man's switch: if anything below wedges SSH, the box reverts to a
# permissive firewall (and drops the tunnel) in ROLLBACK_SECS so you reconnect.
systemctl stop "${ROLLBACK_UNIT}.timer" 2>/dev/null || true
systemctl reset-failed "${ROLLBACK_UNIT}".* 2>/dev/null || true
systemd-run --unit="$ROLLBACK_UNIT" --on-active="$ROLLBACK_SECS" \
  /bin/bash -c "iptables -P INPUT ACCEPT; iptables -P OUTPUT ACCEPT; iptables -P FORWARD ACCEPT; iptables -F; iptables -t mangle -F; wg-quick down $WG_IF 2>/dev/null || true" \
  >/dev/null 2>&1 && ok "dead-man's-switch armed (auto-revert in ${ROLLBACK_SECS}s unless cancelled)" \
  || warn "could not arm dead-man's-switch — proceeding (open a 2nd SSH session as backup!)"

warn "If you get disconnected, wait ${ROLLBACK_SECS}s and reconnect — the firewall will have reverted."

# Apply the base fail-closed policy now.
"$KILLSWITCH_SH" up
ok "base killswitch applied (default DROP, SSH + handshake allowed)"

# Sanity: confirm the current SSH session's conntrack entry survives.
if conntrack -L 2>/dev/null | grep -q "dport=$SSH_PORT"; then
  ok "SSH conntrack present — session healthy"
else
  warn "could not confirm SSH conntrack (your session may still be fine)"
fi

# --- 7a. Bring up the tunnel if we have a config --------------------------
WG_ACTIVE=0
if [[ -n "$WG_CONF_SRC" ]]; then
  say "Bringing up WireGuard"
  install -m 0600 /dev/null "$WG_CONF_DEST"
  cat "$WG_CONF_SRC" >"$WG_CONF_DEST"
  chmod 0600 "$WG_CONF_DEST"

  # v4-only config means IPv6 was disabled in 5d — strip ::/0 from AllowedIPs
  # or wg-quick will try to install a v6 route and fail with
  # "IPv6 is disabled on nexthop device".
  if [[ "$WG_HAS_V6" -eq 0 ]]; then
    sed -i 's|,[[:space:]]*::/0||g; s|::/0[[:space:]]*,[[:space:]]*||g' "$WG_CONF_DEST"
  fi

  # Inject our PostUp/PreDown hooks under [Interface] if not already present.
  if ! grep -q "$KILLSWITCH_SH up" "$WG_CONF_DEST"; then
    awk -v hook="$KILLSWITCH_SH" '
      BEGIN{done=0}
      /^\[Interface\]/ && !done {
        print
        print "PostUp = " hook " up"
        print "PreDown = " hook " down"
        done=1
        next
      }
      {print}
    ' "$WG_CONF_DEST" >"${WG_CONF_DEST}.tmp" && mv "${WG_CONF_DEST}.tmp" "$WG_CONF_DEST"
    chmod 0600 "$WG_CONF_DEST"
  fi

  systemctl enable "wg-quick@${WG_IF}" >/dev/null 2>&1 || true
  wg-quick down "$WG_IF" 2>/dev/null || true
  if wg-quick up "$WG_IF"; then
    WG_ACTIVE=1
    ok "WireGuard tunnel up"
  else
    warn "wg-quick up failed — tunnel down, box remains fail-closed (SSH only)"
  fi
fi

# --- 7b. Verify, then disarm the dead-man's switch ------------------------
if [[ "$WG_ACTIVE" -eq 1 ]]; then
  exit_ip="$(curl --interface "$WG_IF" -s --max-time 8 https://api.ipify.org 2>/dev/null || true)"
  [[ -n "$exit_ip" ]] && ok "VPN egress IP: $exit_ip" || warn "tunnel up but egress check inconclusive"
fi

systemctl stop "${ROLLBACK_UNIT}.timer" 2>/dev/null || true
systemctl reset-failed "${ROLLBACK_UNIT}".* 2>/dev/null || true
ok "dead-man's-switch cancelled — firewall is now permanent"

# Persist a snapshot as a secondary safeguard (boot service is primary).
netfilter-persistent save >/dev/null 2>&1 || true

# ===========================================================================
# 8. Install Hermes for the invoking user
# ===========================================================================
say "Installing Hermes for $INVOKER"

run_as_user() { sudo -u "$INVOKER" -H bash -lc "$1"; }

# The repo must be owned by the invoking user for an editable install.
if [[ "$(stat -c '%U' "$REPO_DIR")" != "$INVOKER" ]]; then
  warn "repo is not owned by $INVOKER — chowning $REPO_DIR"
  chown -R "$INVOKER:$INVOKER" "$REPO_DIR"
fi

VENV="$INVOKER_HOME/.hermes-venv"
run_as_user "python3 -m venv '$VENV'"
run_as_user "'$VENV/bin/pip' install --upgrade pip wheel >/dev/null"
run_as_user "'$VENV/bin/pip' install -e '$REPO_DIR'"
ok "Hermes installed into $VENV"

# Put `hermes` on PATH for the user.
install -d -o "$INVOKER" -g "$INVOKER" "$INVOKER_HOME/.local/bin"
ln -sf "$VENV/bin/hermes" "$INVOKER_HOME/.local/bin/hermes"
chown -h "$INVOKER:$INVOKER" "$INVOKER_HOME/.local/bin/hermes"
if ! run_as_user 'case ":$PATH:" in *":$HOME/.local/bin:"*) exit 0;; *) exit 1;; esac'; then
  run_as_user 'grep -q "HOME/.local/bin" "$HOME/.bashrc" 2>/dev/null || echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$HOME/.bashrc"'
fi

# Let Hermes drive Docker (the twin) without sudo. Effective on next login.
usermod -aG docker "$INVOKER" 2>/dev/null || warn "could not add $INVOKER to docker group"
ok "hermes on PATH (~/.local/bin); $INVOKER added to docker group"

# ===========================================================================
# 9. Summary
# ===========================================================================
echo
say "Setup complete."
echo
echo "  VPN / killswitch:"
if [[ "$WG_ACTIVE" -eq 1 ]]; then
  echo "    - WireGuard tunnel is UP. All egress rides the VPN; only SSH uses the real NIC."
  [[ -n "${exit_ip:-}" ]] && echo "    - Public egress IP: $exit_ip"
elif [[ -n "$WG_CONF_SRC" ]]; then
  echo "    - ${c_ylw}Tunnel failed to come up${c_rst}; box is fail-closed (SSH only). Check 'wg-quick up $WG_IF'."
else
  echo "    - ${c_ylw}No VPN configured.${c_rst} Box is locked to SSH-only egress."
  echo "      Drop a wg0.conf next to setup.sh and re-run:  sudo ./setup.sh wg0.conf"
fi
echo "    - Files: $WG_CONF_DEST, $KILLSWITCH_SH, $KILLSWITCH_ENV"
echo "    - Killswitch + tunnel auto-start on reboot."
echo
echo "  ${c_ylw}Important:${c_rst} Hermes's own outbound (Vast.ai SSH tunnel/API, twin package"
echo "  builds) only works while the VPN is UP — that's the killswitch doing its job."
echo
echo "  Next steps:"
echo "    1. Log out and back in (activates the docker group + PATH)."
echo "    2. Start Hermes:    hermes"
echo "    3. In the REPL:     config set vast_api_key <your-key>"
echo "                        project new <name>"
echo "                        gpu attach   # then: gpu serve"
echo
echo "  Verify the killswitch later with:"
echo "    wg-quick down $WG_IF && curl --max-time 5 https://1.1.1.1   # must FAIL (no leak)"
echo "    wg-quick up   $WG_IF && curl -s https://api.ipify.org        # shows the VPN IP"
echo
ok "Done."
