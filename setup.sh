#!/usr/bin/env bash
#
# callisto-jupiter one-command installer (Linux + macOS).
#
# Usage:
#   sudo ./setup.sh "https://ingest.callistosignal.com/servers/<id>" "<token>"
#   sudo ./setup.sh --uninstall [--keep-config]
#
# The DSN and token can also come from the CALLISTO_DSN / CALLISTO_TOKEN
# environment variables, or you'll be prompted for them. The token is sent in the
# X-Callisto-Jupiter-Token header, kept separate from the DSN. Installs a
# dedicated venv at /opt/callisto-jupiter, writes the config to the
# OS-conventional path, installs + starts the service, and runs one verification
# cycle. Re-running is safe (venv reused, existing config backed up).
#
# --uninstall removes everything this script creates: the service, the venv, the
# state directory, the service account and the config (the config holds the push
# token, so it goes by default; --keep-config leaves it behind).
#
set -euo pipefail

# Paths are overridable so a non-standard layout (or a test harness) can point the
# script at a different tree; the defaults are what every real install uses.
VENV="${VENV:-/opt/callisto-jupiter}"
BIN="$VENV/bin/callisto-jupiter"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✅ %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m❌ %s\033[0m\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

# --- 1. flags --------------------------------------------------------------------
UNINSTALL=false
KEEP_CONFIG=false
for arg in "$@"; do
    case "$arg" in
        --uninstall)   UNINSTALL=true ;;
        --keep-config) KEEP_CONFIG=true ;;
    esac
done

# --- 2. privilege: re-exec under sudo if not root --------------------------------
if [ "$(id -u)" -ne 0 ]; then
    say "Elevating with sudo..."
    exec sudo -E DSN_FROM_PARENT="${1:-${CALLISTO_DSN:-}}" \
        TOKEN_FROM_PARENT="${2:-${CALLISTO_TOKEN:-}}" bash "$0" "$@"
fi

# --- 3. resolve OS ---------------------------------------------------------------
case "$(uname -s)" in
    Linux)  OS=linux ;  CONFIG_DIR="${CONFIG_DIR:-/etc/callisto-jupiter}" ;;
    Darwin) OS=macos ;  CONFIG_DIR="${CONFIG_DIR:-/Library/Application Support/callisto-jupiter}" ;;
    *)      die "Unsupported OS '$(uname -s)'. Use setup.ps1 on Windows." ;;
esac
CONFIG="$CONFIG_DIR/config.toml"
UNIT="${UNIT:-/etc/systemd/system/callisto-jupiter.service}"
PLIST="${PLIST:-/Library/LaunchDaemons/com.callistosignal.jupiter.plist}"
# The systemd unit runs as this dedicated account (launchd on macOS runs as root).
SERVICE_USER=callisto-jupiter
STATE_DIR="${STATE_DIR:-/var/lib/callisto-jupiter}"

# --- 4. uninstall: tear down what this script created, then exit ------------------
if [ "$UNINSTALL" = true ]; then
    say "Uninstalling callisto-jupiter..."
    if [ "$OS" = linux ]; then
        if command -v systemctl >/dev/null 2>&1; then
            systemctl disable --now callisto-jupiter >/dev/null 2>&1 || true
            systemctl reset-failed callisto-jupiter >/dev/null 2>&1 || true
        fi
        rm -f "$UNIT"
        if command -v systemctl >/dev/null 2>&1; then
            systemctl daemon-reload >/dev/null 2>&1 || true
        fi
        rm -rf "$STATE_DIR"
        # Installs made by the old DynamicUser=yes unit kept their state in
        # /var/lib/private/callisto-jupiter, with $STATE_DIR a symlink to it.
        rm -rf "$(dirname "$STATE_DIR")/private/$(basename "$STATE_DIR")"
        if getent passwd "$SERVICE_USER" >/dev/null 2>&1; then
            userdel "$SERVICE_USER" >/dev/null 2>&1 \
                || err "Could not remove the $SERVICE_USER account — remove it by hand."
        fi
    else
        launchctl bootout system/com.callistosignal.jupiter >/dev/null 2>&1 || true
        rm -f "$PLIST"
        # macOS keeps the store-and-forward buffer next to the config, not in /var/lib.
        rm -f "$CONFIG_DIR/buffer.json"
    fi
    rm -rf "$VENV"
    if [ "$KEEP_CONFIG" = true ]; then
        say "Keeping $CONFIG — it still holds the push token, so delete it (and rotate"
        say "the token in Callisto) once this server is decommissioned."
    else
        # Only the files this script wrote: an operator-added env file or anything
        # else in the directory is left alone, and the directory itself only goes
        # if that emptied it.
        rm -f "$CONFIG" "$CONFIG.bak"
        rmdir "$CONFIG_DIR" 2>/dev/null || true
    fi
    ok "callisto-jupiter removed."
    if [ "$OS" = macos ]; then
        echo "   Logs left in place: /var/log/callisto-jupiter.log (and .err.log)"
    fi
    exit 0
fi

# --- 5. resolve DSN + token: arg → env (incl. sudo-forwarded) → prompt ------------
DSN="${1:-${CALLISTO_DSN:-${DSN_FROM_PARENT:-}}}"
if [ -z "$DSN" ]; then
    printf 'Paste the server DSN (from the server page in Callisto): '
    read -r DSN
fi
case "$DSN" in
    https://*) : ;;
    *) die "DSN looks wrong. Expected: https://ingest.callistosignal.com/servers/<id>" ;;
esac

TOKEN="${2:-${CALLISTO_TOKEN:-${TOKEN_FROM_PARENT:-}}}"
if [ -z "$TOKEN" ]; then
    printf 'Paste the server token (from the server page in Callisto): '
    read -r TOKEN
fi
[ -n "$TOKEN" ] || die "Token is required (sent in the X-Callisto-Jupiter-Token header)."

# --- 6. require python3 >= 3.9 ---------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 not found. Install Python 3.9+ first."
python3 - <<'PY' || die "Python 3.9+ required (found $(python3 -V 2>&1))."
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY

# --- 7. service account (Linux) ---------------------------------------------------
# Everything below is written root-owned but group-readable by this account, so
# the unprivileged service can actually read its config and state.
if [ "$OS" = linux ] && ! getent passwd "$SERVICE_USER" >/dev/null 2>&1; then
    say "Creating system user $SERVICE_USER ..."
    NOLOGIN=/usr/sbin/nologin
    [ -x "$NOLOGIN" ] || NOLOGIN=/sbin/nologin
    [ -x "$NOLOGIN" ] || NOLOGIN=/bin/false
    useradd --system --no-create-home --home-dir "$STATE_DIR" \
        --shell "$NOLOGIN" "$SERVICE_USER" \
        || die "Could not create the $SERVICE_USER system user."
fi

# --- 8. venv + install (GPU autodetect) ------------------------------------------
say "Creating virtualenv at $VENV ..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip

if command -v nvidia-smi >/dev/null 2>&1; then
    say "NVIDIA GPU detected — installing with GPU support (nvidia-ml-py)..."
    # Drop the deprecated `pynvml` distribution if an older install left it behind:
    # it owns the same pynvml.py that nvidia-ml-py ships, and it warns on every import.
    "$VENV/bin/pip" uninstall --quiet --yes pynvml >/dev/null 2>&1 || true
    "$VENV/bin/pip" install --quiet "$SCRIPT_DIR[gpu]"
else
    say "No NVIDIA GPU detected — installing core (CPU/RAM/DISK)..."
    "$VENV/bin/pip" install --quiet "$SCRIPT_DIR"
fi

# --- 9. write config (back up any existing one) ----------------------------------
DISK_PATH="/"
mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG" ]; then
    say "Existing config found — backing up to $CONFIG.bak"
    cp "$CONFIG" "$CONFIG.bak"
fi
cat > "$CONFIG" <<EOF
# callisto-jupiter configuration — generated by setup.sh
# The token is the secret push credential (sent in the X-Callisto-Jupiter-Token
# header); keep this file private.
dsn = "$DSN"
token = "$TOKEN"
interval_seconds = 60
disk_path = "$DISK_PATH"
timeout_seconds = 10
EOF
# root-owned, group-readable by the service account only: the token stays secret
# from other users but the unprivileged service can read it.
if [ "$OS" = linux ]; then
    chown "root:$SERVICE_USER" "$CONFIG"
    chmod 640 "$CONFIG"
    chmod 755 "$CONFIG_DIR"
    if [ -f "$CONFIG.bak" ]; then chmod 600 "$CONFIG.bak"; fi
else
    chmod 600 "$CONFIG"
fi
ok "Config written to $CONFIG"

# --- 10. state directory (Linux) ---------------------------------------------------
# systemd's StateDirectory= only fixes ownership of a directory it creates, so
# repair installs where a root run left a root-owned buffer the service can't read.
if [ "$OS" = linux ]; then
    mkdir -p "$STATE_DIR"
    chown -R "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR"
    chmod 700 "$STATE_DIR"
fi

# --- 11. install + start the service ----------------------------------------------
if [ "$OS" = linux ]; then
    say "Installing systemd service..."
    cp "$SCRIPT_DIR/deploy/callisto-jupiter.service" "$UNIT"
    systemctl daemon-reload
    systemctl enable --now callisto-jupiter
    LOG_HINT="journalctl -u callisto-jupiter -f"
else
    say "Installing launchd daemon..."
    cp "$SCRIPT_DIR/deploy/com.callistosignal.jupiter.plist" "$PLIST"
    chown root:wheel "$PLIST"
    launchctl bootout system/com.callistosignal.jupiter 2>/dev/null || true
    launchctl bootstrap system "$PLIST"
    launchctl kickstart -k system/com.callistosignal.jupiter
    LOG_HINT="tail -f /var/log/callisto-jupiter.log"
fi

# --- 12. verify with one collect+push cycle --------------------------------------
# Run as the service account on Linux, not root: it proves the account can read
# the config, and stops the verification run from leaving a root-owned buffer.json
# in the state directory that the service would then fail to read.
say "Verifying with one collect + push cycle..."
if [ "$OS" = linux ]; then
    if command -v runuser >/dev/null 2>&1; then
        VERIFY=(runuser -u "$SERVICE_USER" -- "$BIN" --once)
    else
        VERIFY=(sudo -u "$SERVICE_USER" -- "$BIN" --once)
    fi
else
    VERIFY=("$BIN" --once)
fi
if "${VERIFY[@]}"; then
    ok "callisto-jupiter installed and running."
    echo "   Follow logs: $LOG_HINT"
else
    die "Service installed but the test cycle failed — check the DSN and network, then: $LOG_HINT"
fi
