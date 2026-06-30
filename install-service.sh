#!/usr/bin/env bash
#
# Install the Budget app as a systemd service on a Raspberry Pi (Zero 2 W or
# any Debian/Raspberry Pi OS based system).
#
# Usage (from the app directory):
#     sudo ./install-service.sh
#
# It creates a virtual environment, installs dependencies, writes
# /etc/systemd/system/budget.service tailored to this machine, then enables
# and starts the service so it survives reboots.

set -e

SERVICE_NAME="budget"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

# Figure out which non-root user should own and run the service.
RUN_USER="${SUDO_USER:-$(id -un)}"
if [ "$RUN_USER" = "root" ]; then
    echo "Refusing to run the service as root. Run this script with sudo from your normal user account."
    exit 1
fi

echo "App directory : $APP_DIR"
echo "Service user  : $RUN_USER"

# The repo is sometimes cloned/owned by root, which prevents the service user
# from creating the venv or writing budget.db. Make sure it owns the app dir.
OWNER="$(stat -c '%U' "$APP_DIR")"
if [ "$OWNER" != "$RUN_USER" ]; then
    echo "Fixing ownership: $APP_DIR -> $RUN_USER"
    chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
fi

# --- 1. Python virtual environment + dependencies ---------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required. Install it with: sudo apt install -y python3 python3-venv"
    exit 1
fi

if [ ! -d "$APP_DIR/.venv" ]; then
    echo "Creating virtual environment..."
    sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/.venv"
fi

echo "Installing dependencies..."
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# --- 2. systemd unit --------------------------------------------------------
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
echo "Writing $UNIT_PATH ..."
sed -e "s|__USER__|$RUN_USER|g" -e "s|__DIR__|$APP_DIR|g" \
    "$APP_DIR/budget.service" | sudo tee "$UNIT_PATH" >/dev/null

# --- 3. enable + start ------------------------------------------------------
echo "Enabling and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo
echo "Done. The Budget app is now running and will start on boot."
echo "  Status : sudo systemctl status $SERVICE_NAME"
echo "  Logs   : journalctl -u $SERVICE_NAME -f"
echo "  Open   : http://$(hostname -I | awk '{print $1}'):5000"
