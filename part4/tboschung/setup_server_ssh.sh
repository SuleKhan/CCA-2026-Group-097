#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./setup_server_ssh.sh user1@server1 user2@server2
#
# Example:
#   ./setup_server_ssh.sh ubuntu@34.38.204.84 ubuntu@35.241.183.126

SERVER1="${1:-}"
SERVER2="${2:-}"

if [[ -z "$SERVER1" || -z "$SERVER2" ]]; then
  echo "Usage: $0 user1@server1 user2@server2"
  exit 1
fi

LOCAL_KEY_PATH="$HOME/.ssh/cloud-computing"

REMOTE_KEY_NAME="master_node_key"
REMOTE_KEY_PATH="\$HOME/.ssh/$REMOTE_KEY_NAME"
REMOTE_AUTH_KEYS="\$HOME/.ssh/authorized_keys"

echo "Setting up SSH keys on both servers..."

for SERVER in "$SERVER1" "$SERVER2"; do
  ssh -i "$LOCAL_KEY_PATH" "$SERVER" "
    set -e
    mkdir -p \"\$HOME/.ssh\"
    chmod 700 \"\$HOME/.ssh\"

    if [ ! -f \"$REMOTE_KEY_PATH\" ]; then
      ssh-keygen -t ed25519 -N '' -f \"$REMOTE_KEY_PATH\" -C '$SERVER'
    fi

    touch \"$REMOTE_AUTH_KEYS\"
    chmod 600 \"$REMOTE_AUTH_KEYS\"
  "
done

echo "Fetching public keys..."

PUBKEY1="$(ssh -i "$LOCAL_KEY_PATH" "$SERVER1" "cat \"$REMOTE_KEY_PATH.pub\"")"
PUBKEY2="$(ssh -i "$LOCAL_KEY_PATH" "$SERVER2" "cat \"$REMOTE_KEY_PATH.pub\"")"

echo "Installing each server's public key on the other..."

ssh -i "$LOCAL_KEY_PATH" "$SERVER1" "
  grep -qxF '$PUBKEY2' \"$REMOTE_AUTH_KEYS\" || echo '$PUBKEY2' >> \"$REMOTE_AUTH_KEYS\"
  chmod 600 \"$REMOTE_AUTH_KEYS\"
"

ssh -i "$LOCAL_KEY_PATH" "$SERVER2" "
  grep -qxF '$PUBKEY1' \"$REMOTE_AUTH_KEYS\" || echo '$PUBKEY1' >> \"$REMOTE_AUTH_KEYS\"
  chmod 600 \"$REMOTE_AUTH_KEYS\"
"

echo "Testing SSH from server 1 to server 2..."
ssh -i "$LOCAL_KEY_PATH" "$SERVER1" "
  ssh -i \"$REMOTE_KEY_PATH\" \
    -o StrictHostKeyChecking=accept-new \
    -o BatchMode=yes \
    '$SERVER2' \
    'echo SSH from server 1 to server 2 works'
"

echo "Testing SSH from server 2 to server 1..."
ssh -i "$LOCAL_KEY_PATH" "$SERVER2" "
  ssh -i \"$REMOTE_KEY_PATH\" \
    -o StrictHostKeyChecking=accept-new \
    -o BatchMode=yes \
    '$SERVER1' \
    'echo SSH from server 2 to server 1 works'
"

echo "Done. The two servers can now SSH into each other."