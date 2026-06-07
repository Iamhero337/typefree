#!/usr/bin/env bash
# Typefree app launcher — what runs when you click the menu/desktop icon.
#
# It is intentionally idempotent and self-healing, so clicking the icon always
# leaves Typefree in the right state no matter how you got here:
#   • ensures autostart is ON  (so every future login starts it with no click)
#   • starts the daemon NOW if it isn't already running
#   • gives a quick on-screen confirmation
#
# This is the manual fallback for the rare case where boot autostart didn't take
# (e.g. first login right after install, before the 'input' group went live).
# One click fixes both "start it now" and "make it stick for next boot".
set -uo pipefail

UNIT=typefree.service

notify() {
    command -v notify-send >/dev/null 2>&1 && \
        notify-send -a Typefree -i typefree "$1" "${2:-}" 2>/dev/null || true
}

# Turn on autostart for every future login (no-op if already enabled).
systemctl --user enable "$UNIT" >/dev/null 2>&1 || true

if systemctl --user is-active --quiet "$UNIT"; then
    # Already up — the daemon won't re-announce itself, so confirm the click.
    notify "Typefree is already running" "Hold Alt+Z to dictate"
elif systemctl --user start "$UNIT" 2>/dev/null; then
    : # success — the daemon shows its own "🎤 ready" toast once the model loads
else
    notify "Typefree couldn't start" "Open a terminal and run: bash status.sh"
fi
