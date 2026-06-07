#!/bin/bash
#
# Typefree installer  -  Speech-to-Text for Linux (Wayland + X11)
#
# Usage:
#   bash install.sh                 # interactive sudo
#   STT_PASSWORD=xxxx bash install.sh   # non-interactive sudo (CI/auto)
#
set -e

APP_NAME="typefree"
SHARE_DIR="$HOME/.local/share/$APP_NAME"
USER_UNIT_DIR="$HOME/.config/systemd/user"
CFG_DIR="$HOME/.config/$APP_NAME"

say() { echo -e "$1"; }
run_sudo() {
    if [ -n "$STT_PASSWORD" ]; then
        echo "$STT_PASSWORD" | sudo -S "$@"
    else
        sudo "$@"
    fi
}

say "========================================"
say "🎤 Typefree installer (Wayland + X11)"
say "========================================\n"

if [[ ! "$OSTYPE" == "linux-gnu"* ]]; then
    say "❌ Linux only."; exit 1
fi

# ----------------------------------------------------------------------
say "📦 Installing system packages (whisper deps, ydotool, clipboard)..."
run_sudo apt-get update -qq
run_sudo apt-get install -y -qq \
    python3-pip python3-dev \
    wl-clipboard xclip \
    libnotify-bin \
    python3-pyqt5 \
    libportaudio2 portaudio19-dev \
    ffmpeg alsa-utils \
    git cmake gcc scdoc 2>/dev/null || {
        say "⚠️  Some apt packages failed; continuing. Make sure wl-clipboard,"
        say "    ffmpeg, portaudio and a C toolchain (cmake/gcc) are present."
    }

# ----------------------------------------------------------------------
# ydotool: Ubuntu ships 0.1.8 (no daemon -> drops the first typed chars).
# We need 1.x with ydotoold for reliable typing, so build from source.
need_ydotool=1
if command -v ydotoold >/dev/null 2>&1; then
    need_ydotool=0
fi
if [ "$need_ydotool" -eq 1 ]; then
    say "🔧 Building ydotool 1.x from source (apt version is too old)..."
    BUILD=$(mktemp -d)
    git clone --depth 1 https://github.com/ReimuNotMoe/ydotool.git "$BUILD" >/dev/null 2>&1
    ( cd "$BUILD" && mkdir -p build && cd build \
        && cmake .. >/dev/null 2>&1 && make -j"$(nproc)" >/dev/null 2>&1 )
    run_sudo make -C "$BUILD/build" install >/dev/null 2>&1
    run_sudo ldconfig
    rm -rf "$BUILD"
    if command -v ydotoold >/dev/null 2>&1; then
        say "   ✓ ydotool $(command -v ydotoold) installed"
    else
        say "   ⚠️  ydotool build failed — typing at cursor may not work"
    fi
fi

# ----------------------------------------------------------------------
say "🐍 Installing Python packages..."
pip3 install --break-system-packages -q -r requirements.txt

# ----------------------------------------------------------------------
say "🔑 Configuring permissions (input group + uinput udev rule)..."
run_sudo cp 99-typefree-uinput.rules /etc/udev/rules.d/99-typefree-uinput.rules
run_sudo udevadm control --reload-rules
run_sudo udevadm trigger /dev/uinput 2>/dev/null || true

NEED_RELOGIN=0
if ! id -nG "$USER" | grep -qw input; then
    run_sudo usermod -aG input "$USER"
    NEED_RELOGIN=1
    say "   added $USER to 'input' group"
fi

# ----------------------------------------------------------------------
say "📁 Installing app to $SHARE_DIR ..."
mkdir -p "$SHARE_DIR" "$USER_UNIT_DIR" "$CFG_DIR"
cp typefree.py "$SHARE_DIR/"
chmod +x "$SHARE_DIR/typefree.py"

if [ ! -f "$CFG_DIR/config.json" ]; then
    cp config.example.json "$CFG_DIR/config.json"
    say "   wrote default config to $CFG_DIR/config.json"
fi

# ----------------------------------------------------------------------
say "⚙️  Installing systemd user services..."
cp ydotoold.service "$USER_UNIT_DIR/"
cp typefree.service "$USER_UNIT_DIR/"
systemctl --user daemon-reload
# enable both so they auto-start on the next login (when 'input' group is live)
systemctl --user enable ydotoold.service typefree.service 2>/dev/null || true
systemctl --user start ydotoold.service 2>/dev/null || true

# ----------------------------------------------------------------------
say ""
if [ "$NEED_RELOGIN" -eq 1 ]; then
    say "========================================"
    say "✅ Installed — but ONE more step is required"
    say "========================================"
    say ""
    say "You were just added to the 'input' group. Group membership only"
    say "takes effect after a fresh login, so the hotkey listener can't read"
    say "the keyboard until you log out and back in (or reboot)."
    say ""
    say "👉 Log out and back in, then run:"
    say "     systemctl --user start typefree.service"
    say "     bash status.sh"
    say ""
    say "After that: hold Alt+Z, speak, release. Text appears at your cursor."
else
    systemctl --user enable --now typefree.service 2>/dev/null || true
    sleep 2
    if systemctl --user is-active --quiet typefree.service; then
        say "========================================"
        say "✅ Typefree is running!"
        say "========================================"
        say ""
        say "🎙️  Hold Alt+Z, speak, release — text appears at your cursor."
        say ""
        say "Commands:  bash status.sh | bash logs.sh | bash uninstall.sh"
    else
        say "⚠️  Service installed but not active yet. Check: bash logs.sh"
    fi
fi
