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

# Ask the user how accurate they want transcription vs. how much RAM/CPU they
# can spare. The model is loaded once and stays resident the whole time the
# daemon runs, so this is a *permanent* RAM cost — be upfront about it.
# Echoes the chosen model name on stdout. Honors $TYPEFREE_MODEL and skips the
# prompt when non-interactive (no TTY), defaulting to 'base'.
choose_model() {
    if [ -n "$TYPEFREE_MODEL" ]; then
        echo "$TYPEFREE_MODEL"; return
    fi
    if [ ! -t 0 ]; then
        echo "base"; return
    fi
    # menu -> stderr so stdout stays clean for the chosen model name
    {
        echo ""
        echo "  Choose transcription quality. Bigger = more accurate, but uses"
        echo "  more RAM *permanently* (the model stays loaded) and is slower per"
        echo "  clip. Approx. resident RAM and one-time download:"
        echo ""
        echo "    1) tiny    ~0.6 GB RAM   75 MB    fastest, basic accuracy"
        echo "    2) base    ~0.9 GB RAM  140 MB    fast, decent       (default)"
        echo "    3) small   ~1.3 GB RAM  460 MB    medium, good"
        echo "    4) medium  ~2.5 GB RAM  1.5 GB    slow, very good"
        echo "    5) large   ~4.5 GB RAM  3.0 GB    slowest, best"
        echo ""
        free -h 2>/dev/null | awk 'NR==2{print "  Your machine: "$2" total RAM, "$7" available right now."}'
        echo ""
    } >&2
    local choice model
    read -rp "  Pick 1-5 [2]: " choice >&2
    case "$choice" in
        1) model=tiny ;;
        3) model=small ;;
        4) model=medium ;;
        5) model=large ;;
        *) model=base ;;
    esac
    echo "  → using '$model'" >&2
    echo "$model"
}

# ----------------------------------------------------------------------
# Let the user pick the dictation hotkey at install time. You HOLD the key,
# speak, then release. The catch: the daemon reads the keyboard passively, so a
# *letter* hotkey (e.g. Alt+Z) can leak its letter into the focused app if the
# letter registers a hair before its modifier — KDE's search field famously
# fills with "zzzz" via autorepeat. A non-character key (Right Ctrl, a function
# key, Menu) never produces text, so it's the safe choice; letter combos are
# offered for those who prefer the mnemonic and accept the trade-off. Right Ctrl
# is the default because it also needs no Fn (F-keys can on media keyboards).
# Echoes two words on stdout: "HOTKEY MODIFIER" (modifier 'none' for plain keys).
# Honors $TYPEFREE_HOTKEY/$TYPEFREE_MODIFIER; defaults to "rightctrl none" (no TTY).
choose_hotkey() {
    if [ -n "$TYPEFREE_HOTKEY" ]; then
        echo "$TYPEFREE_HOTKEY ${TYPEFREE_MODIFIER:-none}"; return
    fi
    if [ ! -t 0 ]; then
        echo "rightctrl none"; return
    fi
    {
        echo ""
        echo "  Choose your dictation hotkey — you HOLD it, speak, then release."
        echo ""
        echo "    1) Right Ctrl  no modifier  ✦ recommended — needs no Fn, types no"
        echo "                                  character (can't leak text), and the"
        echo "                                  desktop doesn't grab it on its own"
        echo "    2) F9          no modifier    leak-proof too, BUT on media-key"
        echo "                                  keyboards it may need Fn+F9 to reach"
        echo "    3) Alt + Z     letter combo   familiar, but a fast/simultaneous"
        echo "                                  press can leak 'z' into the field"
        echo "    4) Menu key    no modifier    the context-menu key; types nothing"
        echo "    5) custom      type your own, e.g.  'pause'  or  'ctrl+space'"
        echo ""
    } >&2
    local choice key mod custom
    read -rp "  Pick 1-5 [1]: " choice >&2
    case "$choice" in
        2) key=f9;   mod=none ;;
        3) key=z;    mod=alt  ;;
        4) key=menu; mod=none ;;
        5)
            read -rp "  Enter hotkey (key, or modifier+key): " custom >&2
            custom="$(printf '%s' "$custom" | tr 'A-Z' 'a-z' | tr -d ' ')"
            if [ -z "$custom" ]; then
                key=rightctrl; mod=none
            elif printf '%s' "$custom" | grep -q '+'; then
                mod="${custom%%+*}"; key="${custom##*+}"
            else
                key="$custom"; mod=none
            fi
            ;;
        *) key=rightctrl; mod=none ;;
    esac
    if [ "$mod" = none ]; then
        echo "  → using ${key^^}" >&2
    else
        echo "  → using ${mod^}+${key^^}" >&2
    fi
    echo "$key $mod"
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
cp typefree-launch.sh "$SHARE_DIR/"
chmod +x "$SHARE_DIR/typefree-launch.sh"

if [ ! -f "$CFG_DIR/config.json" ]; then
    cp config.example.json "$CFG_DIR/config.json"
    MODEL="$(choose_model)"
    read -r HOTKEY MODIFIER <<<"$(choose_hotkey)"
    python3 - "$CFG_DIR/config.json" "$MODEL" "$HOTKEY" "$MODIFIER" <<'PY'
import json, sys
path, model, hotkey, modifier = sys.argv[1:5]
with open(path) as f:
    cfg = json.load(f)
cfg["model"] = model
cfg["hotkey"] = hotkey
cfg["modifier"] = modifier
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PY
    say "   wrote config (model='$MODEL', hotkey='$HOTKEY', modifier='$MODIFIER') to $CFG_DIR/config.json"
else
    say "   keeping existing config at $CFG_DIR/config.json"
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
# Clickable app launcher (KDE/GNOME menu) so users never need the terminal.
# Clicking it starts the service; right-click offers Restart/Stop.
say "🖱️  Installing app launcher + icon..."
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$ICON_DIR" "$APP_DIR"
cp typefree.svg "$ICON_DIR/typefree.svg"
# Fill in the absolute launcher path (.desktop Exec can't use $HOME/%h).
sed "s#__LAUNCHER__#$SHARE_DIR/typefree-launch.sh#" \
    typefree.desktop > "$APP_DIR/typefree.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APP_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -qtf "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
say "   added 'Typefree' to your app menu"

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
