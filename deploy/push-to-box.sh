#!/usr/bin/env bash
#
# push-to-box.sh — run this FROM TERMUX (or any machine with ssh/scp).
#
#   Usage:   ./push-to-box.sh user@your-vps-ip [ssh-port]
#
# Copies this deploy folder (installer + all app dependency wheels) to the
# box over plain scp, then sshes in and runs the offline installer. The box
# needs no internet beyond your SSH session — everything it needs is in
# this folder.
#
# Termux one-time prep:   pkg install -y git openssh

set -euo pipefail

DEST="${1:?usage: ./push-to-box.sh user@host [ssh-port]}"
PORT="${2:-22}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> pushing $DIR -> $DEST:~/hermes-deploy (port $PORT)"
ssh -p "$PORT" "$DEST" 'rm -rf ~/hermes-deploy'
scp -P "$PORT" -r "$DIR" "$DEST":'~/hermes-deploy'

echo "==> installing on the box"
ssh -p "$PORT" "$DEST" 'cd ~/hermes-deploy && ./install-hermes.sh'

echo
echo "==> done. ssh in and run:  hermes"
