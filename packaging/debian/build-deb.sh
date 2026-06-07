#!/usr/bin/env bash
# Build a Typefree .deb from the repo. Produces ./typefree_<ver>_all.deb.
#
# Why a hand-rolled .deb (dpkg-deb) instead of full debhelper: most of Typefree's
# heavy dependencies aren't cleanly in apt — Ubuntu's ydotool is the broken 0.1.8
# (no daemon, drops typed chars) and openai-whisper/torch aren't packaged. So the
# package installs the app + system integration + the apt-satisfiable deps, and
# the postinst bootstraps the rest (ydotool 1.x from source, Whisper via pip).
# That means the FIRST install needs network + a C toolchain. For a fully turnkey
# Ubuntu/KDE-neon experience the repo's install.sh is still the smoothest path.
set -euo pipefail

VER="${1:-1.0.0}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"   # repo root
STAGE="$(mktemp -d)"
PKGDIR="$STAGE/typefree_${VER}_all"
trap 'rm -rf "$STAGE"' EXIT

echo "📦 Building typefree_${VER}_all.deb from $REPO"

# ---- file tree ----
install -Dm755 "$REPO/typefree.py"        "$PKGDIR/usr/lib/typefree/typefree.py"
install -Dm755 "$REPO/typefree-launch.sh" "$PKGDIR/usr/lib/typefree/typefree-launch.sh"
install -d "$PKGDIR/usr/bin"
ln -s /usr/lib/typefree/typefree-launch.sh "$PKGDIR/usr/bin/typefree"

install -Dm644 "$REPO/99-typefree-uinput.rules" \
  "$PKGDIR/usr/lib/udev/rules.d/99-typefree-uinput.rules"

# systemd *user* units, rewritten for system install paths
sed 's#%h/.local/share/typefree/typefree.py#/usr/lib/typefree/typefree.py#' \
  "$REPO/typefree.service" > "$STAGE/typefree.service"
sed 's#/usr/local/bin/ydotoold#/usr/bin/ydotoold#' \
  "$REPO/ydotoold.service" > "$STAGE/ydotoold.service"
install -Dm644 "$STAGE/typefree.service" "$PKGDIR/usr/lib/systemd/user/typefree.service"
install -Dm644 "$STAGE/ydotoold.service" "$PKGDIR/usr/lib/systemd/user/ydotoold.service"

# desktop + icon
sed 's#^Exec=__LAUNCHER__#Exec=/usr/bin/typefree#' "$REPO/typefree.desktop" \
  > "$STAGE/typefree.desktop"
install -Dm644 "$STAGE/typefree.desktop" \
  "$PKGDIR/usr/share/applications/typefree.desktop"
install -Dm644 "$REPO/typefree.svg" \
  "$PKGDIR/usr/share/icons/hicolor/scalable/apps/typefree.svg"
install -Dm644 "$REPO/README.md" "$PKGDIR/usr/share/doc/typefree/README.md"
install -Dm644 "$REPO/LICENSE"   "$PKGDIR/usr/share/doc/typefree/copyright"

# ---- control ----
install -d "$PKGDIR/DEBIAN"
cat > "$PKGDIR/DEBIAN/control" <<EOF
Package: typefree
Version: $VER
Section: utils
Priority: optional
Architecture: all
Maintainer: Hero <iamhero337@gmail.com>
Depends: python3, python3-evdev, python3-numpy, python3-scipy, python3-sounddevice, python3-pyqt5, ffmpeg, wl-clipboard, libnotify-bin
Recommends: xclip
Homepage: https://github.com/Iamhero337/typefree
Description: Talk-to-type for Linux (Wayland+X11)
 Hold a key, speak, release — your words are transcribed by OpenAI Whisper
 (fully offline) and typed at your cursor in any app. Works on Wayland and X11
 via evdev (global hotkey) and ydotool (types into the focused window).
 .
 First install bootstraps ydotool 1.x (built from source) and openai-whisper
 (pip); this step needs network access and a C toolchain.
EOF

# ---- maintainer scripts ----
cat > "$PKGDIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

TARGET_USER="${SUDO_USER:-${PKEXEC_UID:+$(id -un "$PKEXEC_UID" 2>/dev/null)}}"

# 1) input group + udev so /dev/input & /dev/uinput are usable
if [ -n "$TARGET_USER" ] && ! id -nG "$TARGET_USER" 2>/dev/null | grep -qw input; then
    usermod -aG input "$TARGET_USER" || true
    echo "typefree: added '$TARGET_USER' to 'input' group — LOG OUT/IN once."
fi
udevadm control --reload 2>/dev/null || true
udevadm trigger /dev/uinput 2>/dev/null || true

# 2) openai-whisper (not in apt) via pip
if ! python3 -c 'import whisper' 2>/dev/null; then
    echo "typefree: installing openai-whisper via pip (needs network)…"
    pip3 install --break-system-packages -q openai-whisper || \
        echo "typefree: WARNING openai-whisper install failed — run 'pip3 install --break-system-packages openai-whisper' yourself."
fi

# 3) ydotool 1.x (apt's 0.1.8 drops typed chars) — build if ydotoold absent
if ! command -v ydotoold >/dev/null 2>&1; then
    echo "typefree: building ydotool 1.x from source (needs network + cmake/gcc)…"
    if command -v cmake >/dev/null && command -v gcc >/dev/null && command -v git >/dev/null; then
        B="$(mktemp -d)"
        if git clone --depth 1 https://github.com/ReimuNotMoe/ydotool.git "$B" >/dev/null 2>&1 \
           && cmake -S "$B" -B "$B/build" >/dev/null 2>&1 \
           && make -C "$B/build" >/dev/null 2>&1 \
           && make -C "$B/build" install >/dev/null 2>&1; then
            echo "typefree: ydotool installed to /usr/local/bin."
        else
            echo "typefree: WARNING ydotool build failed — see https://github.com/ReimuNotMoe/ydotool"
        fi
        rm -rf "$B"
    else
        echo "typefree: WARNING need git/cmake/gcc to build ydotool — 'sudo apt install git cmake gcc scdoc' then reinstall."
    fi
fi

# refresh desktop db + icon cache
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true

cat <<MSG
==> Typefree installed. Finish with (as your normal user):
      systemctl --user enable --now ydotoold.service typefree.service
    Then hold Right Ctrl, speak, release. Config: ~/.config/typefree/config.json
MSG
exit 0
EOF
chmod 755 "$PKGDIR/DEBIAN/postinst"

cat > "$PKGDIR/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
# Stop per-user services for the invoking user (best effort).
if [ -n "${SUDO_USER:-}" ]; then
    su - "$SUDO_USER" -c 'systemctl --user disable --now typefree.service ydotoold.service' 2>/dev/null || true
fi
exit 0
EOF
chmod 755 "$PKGDIR/DEBIAN/prerm"

# ---- build ----
dpkg-deb --build --root-owner-group "$PKGDIR" >/dev/null
OUT="$REPO/typefree_${VER}_all.deb"
cp "$PKGDIR.deb" "$OUT"
echo "✅ Built $OUT"
echo "   Install:  sudo apt install $OUT     (or: sudo dpkg -i $OUT)"
