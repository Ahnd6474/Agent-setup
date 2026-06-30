#!/usr/bin/env bash
set -euo pipefail

echo "Enabling Remote Login (sshd) so it starts on boot..."
sudo /usr/sbin/systemsetup -setremotelogin on
echo
echo "Current status:"
/usr/sbin/systemsetup -getremotelogin
echo
echo "If you want to verify the daemon directly, run:"
echo "  launchctl print system/com.openssh.sshd"

