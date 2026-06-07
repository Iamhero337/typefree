#!/usr/bin/env python3
"""
Typefree - Speech-to-Text for Linux (Wayland + X11)

Reads the keyboard at the kernel level (evdev), so the global hotkey works
on Wayland *and* X11. On hotkey, it records the mic, transcribes with
Whisper, then types the text at your cursor (ydotool) and copies it to the
clipboard (wl-copy / xclip).

Hotkey modes:
  hold   - hold the hotkey to record, release to transcribe   (default)
  toggle - press once to start, press again to stop

Configured via ~/.config/typefree/config.json or environment variables.
"""
import os
import sys
import json
import time
import queue
import shutil
import select
import logging
import tempfile
import threading
import subprocess

import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wavfile
import evdev
from evdev import ecodes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("typefree")

CONFIG_PATH = os.path.expanduser("~/.config/typefree/config.json")

DEFAULT_CONFIG = {
    "hotkey": "z",          # main key, combined with a modifier below
    "modifier": "alt",      # alt | ctrl | super | shift | none
    "mode": "hold",         # hold | toggle
    "model": "base",        # whisper: tiny|base|small|medium|large
    "language": "en",       # transcription language, or "auto"
    "type_out": True,       # type text at the cursor
    "clipboard": True,      # also copy to clipboard
    "notify": True,         # show on-screen toast (recording / result)
    "sound": True,          # play a short audio cue (start / done / empty)
    "tray": True,           # show a system-tray icon (status + menu)
    "sample_rate": 16000,
}

# freedesktop sound cues (played via paplay / pw-play)
SOUNDS = {
    "start": "message-new-instant",
    "done": "complete",
    "empty": "dialog-warning",
}
SOUND_DIR = "/usr/share/sounds/freedesktop/stereo"

# map friendly names -> evdev key codes
KEYCODE = {
    "a": ecodes.KEY_A, "b": ecodes.KEY_B, "c": ecodes.KEY_C, "d": ecodes.KEY_D,
    "e": ecodes.KEY_E, "f": ecodes.KEY_F, "g": ecodes.KEY_G, "h": ecodes.KEY_H,
    "i": ecodes.KEY_I, "j": ecodes.KEY_J, "k": ecodes.KEY_K, "l": ecodes.KEY_L,
    "m": ecodes.KEY_M, "n": ecodes.KEY_N, "o": ecodes.KEY_O, "p": ecodes.KEY_P,
    "q": ecodes.KEY_Q, "r": ecodes.KEY_R, "s": ecodes.KEY_S, "t": ecodes.KEY_T,
    "u": ecodes.KEY_U, "v": ecodes.KEY_V, "w": ecodes.KEY_W, "x": ecodes.KEY_X,
    "y": ecodes.KEY_Y, "z": ecodes.KEY_Z,
    "space": ecodes.KEY_SPACE, "f1": ecodes.KEY_F1, "f2": ecodes.KEY_F2,
    "f3": ecodes.KEY_F3, "f4": ecodes.KEY_F4, "f5": ecodes.KEY_F5,
    "f6": ecodes.KEY_F6, "f7": ecodes.KEY_F7, "f8": ecodes.KEY_F8,
    "f9": ecodes.KEY_F9, "f10": ecodes.KEY_F10, "f11": ecodes.KEY_F11,
    "f12": ecodes.KEY_F12,
}

MODIFIERS = {
    "alt": {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT},
    "ctrl": {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
    "shift": {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT},
    "super": {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA},
    "none": set(),
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg.update(json.load(f))
        except Exception as e:
            log.warning("Could not read config (%s); using defaults", e)
    # env overrides (handy for the systemd unit)
    cfg["hotkey"] = os.environ.get("TYPEFREE_HOTKEY", cfg["hotkey"]).lower()
    cfg["modifier"] = os.environ.get("TYPEFREE_MODIFIER", cfg["modifier"]).lower()
    cfg["mode"] = os.environ.get("TYPEFREE_MODE", cfg["mode"]).lower()
    cfg["model"] = os.environ.get("TYPEFREE_MODEL", cfg["model"])
    cfg["language"] = os.environ.get("TYPEFREE_LANGUAGE", cfg["language"])
    return cfg


def find_keyboards():
    """Return evdev devices that look like keyboards (have letter keys)."""
    kbds = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        caps = dev.capabilities().get(ecodes.EV_KEY, [])
        if ecodes.KEY_Z in caps and ecodes.KEY_LEFTALT in caps:
            kbds.append(dev)
    return kbds


class Recorder:
    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self._q = queue.Queue()
        self._stream = None
        self._frames = []
        self._active = False

    def start(self):
        if self._active:
            return
        self._frames = []
        self._active = True

        def cb(indata, frames, time_info, status):
            if status:
                log.debug("audio status: %s", status)
            if self._active:
                self._q.put(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32", callback=cb
        )
        self._stream.start()

    def stop(self):
        """Stop and return the recorded audio as an int16 numpy array."""
        if not self._active:
            return None
        self._active = False
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._stream = None
        while not self._q.empty():
            self._frames.append(self._q.get())
        if not self._frames:
            return None
        audio = np.concatenate(self._frames, axis=0).flatten()
        # float32 [-1,1] -> int16 wav
        audio = np.clip(audio, -1.0, 1.0)
        return (audio * 32767).astype(np.int16)


class Typefree:
    def __init__(self, cfg):
        self.cfg = cfg
        self.key = KEYCODE.get(cfg["hotkey"], ecodes.KEY_Z)
        self.mods = MODIFIERS.get(cfg["modifier"], MODIFIERS["alt"])
        self.mode = cfg["mode"]
        self.mod_down = False
        self.recording = False
        self.busy = False
        self.paused = False
        self.tray = None
        self.recorder = Recorder(cfg["sample_rate"])
        self.model = None
        self._have_ydotool = shutil.which("ydotool") is not None
        self._have_wlcopy = shutil.which("wl-copy") is not None
        self._have_xclip = shutil.which("xclip") is not None
        self._notify_bin = shutil.which("notify-send")
        self._play_bin = shutil.which("paplay") or shutil.which("pw-play")
        self._load_model()

    def _load_model(self):
        import whisper  # imported late so --help etc. stays fast
        log.info("Loading Whisper model '%s' ...", self.cfg["model"])
        self.model = whisper.load_model(self.cfg["model"])
        log.info("Model ready.")

    # ---- user feedback (toast + sound) ---------------------------------
    def _notify(self, title, body="", urgency="normal"):
        """Show/replace a desktop toast. Best-effort, never blocks."""
        if not (self.cfg["notify"] and self._notify_bin):
            return
        try:
            subprocess.Popen(
                [
                    self._notify_bin,
                    "-a", "Typefree",
                    "-i", "audio-input-microphone",
                    "-u", urgency,
                    "-t", "2500",
                    # collapse onto one slot instead of stacking
                    "-h", "string:x-canonical-private-synchronous:typefree",
                    title, body,
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.debug("notify failed: %s", e)

    def _play(self, which):
        """Play a short sound cue asynchronously. Best-effort."""
        if not (self.cfg["sound"] and self._play_bin):
            return
        name = SOUNDS.get(which)
        if not name:
            return
        path = os.path.join(SOUND_DIR, name + ".oga")
        if not os.path.exists(path):
            return
        try:
            subprocess.Popen(
                [self._play_bin, path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.debug("sound failed: %s", e)

    def _set_state(self, state):
        """Update the tray icon (thread-safe). No-op if tray is off."""
        if self.tray is not None:
            self.tray.set_state(state)

    # ---- output ---------------------------------------------------------
    def _type_text(self, text):
        if not (self.cfg["type_out"] and self._have_ydotool):
            return
        env = dict(os.environ)
        env.setdefault(
            "YDOTOOL_SOCKET",
            os.path.join(env.get("XDG_RUNTIME_DIR", "/tmp"), ".ydotool_socket"),
        )
        try:
            subprocess.run(["ydotool", "type", "--", text], env=env, timeout=30)
        except Exception as e:
            log.warning("ydotool type failed: %s", e)

    def _clip_text(self, text):
        if not self.cfg["clipboard"]:
            return
        try:
            if self._have_wlcopy:
                subprocess.run(["wl-copy"], input=text.encode(), timeout=10)
            elif self._have_xclip:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode(), timeout=10,
                )
        except Exception as e:
            log.warning("clipboard copy failed: %s", e)

    # ---- recording lifecycle -------------------------------------------
    def _start(self):
        if self.recording or self.busy:
            return
        self.recording = True
        log.info("🎙️  recording...")
        try:
            self.recorder.start()
        except Exception as e:
            log.error("could not start mic: %s", e)
            self.recording = False
            self._notify("🎤 Mic error", str(e), urgency="critical")
            self._play("empty")
            return
        self._set_state("recording")
        self._notify("🎙️ Listening…", "Speak now — release to type")
        self._play("start")

    def _stop_and_transcribe(self):
        if not self.recording:
            return
        self.recording = False
        self.busy = True
        log.info("⏹️  transcribing...")
        self._set_state("busy")
        self._notify("⏳ Transcribing…", "")

        def work():
            try:
                audio = self.recorder.stop()
                if audio is None or len(audio) < self.cfg["sample_rate"] // 4:
                    log.info("(too short / nothing recorded)")
                    self._notify("🤚 Too short", "Hold the key while you speak")
                    self._play("empty")
                    return
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    wavfile.write(f.name, self.cfg["sample_rate"], audio)
                    path = f.name
                lang = None if self.cfg["language"] == "auto" else self.cfg["language"]
                result = self.model.transcribe(path, language=lang, fp16=False)
                os.unlink(path)
                text = result["text"].strip()
                if not text:
                    log.info("(no speech detected)")
                    self._notify("🔇 No speech detected", "")
                    self._play("empty")
                    return
                log.info("📝 %s", text)
                self._clip_text(text)
                self._type_text(text)
                preview = text if len(text) <= 80 else text[:77] + "…"
                self._notify("📝 Typed", preview)
                self._play("done")
            except Exception as e:
                log.error("transcription error: %s", e)
                self._notify("⚠️ Transcription failed", str(e), urgency="critical")
                self._play("empty")
            finally:
                self.busy = False
                self._set_state("paused" if self.paused else "idle")

        threading.Thread(target=work, daemon=True).start()

    # ---- key handling ---------------------------------------------------
    def _on_key(self, code, value):
        # value: 1=down, 0=up, 2=autorepeat
        if self.paused:
            return
        if code in self.mods:
            self.mod_down = value != 0
            return
        if code != self.key:
            return

        mod_ok = self.mod_down or not self.mods  # "none" modifier
        if self.mode == "hold":
            if value == 1 and mod_ok:
                self._start()
            elif value == 0 and self.recording:
                self._stop_and_transcribe()
        else:  # toggle
            if value == 1 and mod_ok:
                if self.recording:
                    self._stop_and_transcribe()
                else:
                    self._start()

    def run(self):
        kbds = find_keyboards()
        if not kbds:
            log.error(
                "No keyboard input devices readable. Is the user in the "
                "'input' group? (logout/login after install)"
            )
            sys.exit(1)
        modname = self.cfg["modifier"]
        combo = self.cfg["hotkey"].upper() if modname == "none" \
            else f"{modname.capitalize()}+{self.cfg['hotkey'].upper()}"
        log.info("=" * 52)
        log.info("🎤 Typefree ready — %s (%s mode)", combo, self.mode)
        log.info("Listening on %d keyboard(s)", len(kbds))
        if not self._have_ydotool:
            log.warning("ydotool not found: text won't be typed at cursor")
        if self.cfg["notify"] and not self._notify_bin:
            log.warning("notify-send not found: no on-screen toasts "
                        "(install 'libnotify-bin')")
        log.info("=" * 52)
        self._notify("🎤 Typefree ready", f"Hold {combo} to dictate")

        # Optional system-tray icon. Qt must own the main thread, so when the
        # tray is up the evdev loop runs in a background thread; otherwise the
        # evdev loop just runs here (original behaviour).
        if self.cfg.get("tray", True):
            self.tray = self._make_tray(combo)
        if self.tray is not None:
            threading.Thread(
                target=self._evdev_loop, args=(kbds,), daemon=True
            ).start()
            self.tray.run()  # blocks in Qt event loop until Quit
        else:
            self._evdev_loop(kbds)

    def _evdev_loop(self, kbds):
        fds = {dev.fd: dev for dev in kbds}
        while True:
            r, _, _ = select.select(fds, [], [])
            for fd in r:
                try:
                    for event in fds[fd].read():
                        if event.type == ecodes.EV_KEY:
                            self._on_key(event.code, event.value)
                except OSError:
                    # device disappeared (unplugged); drop it
                    fds.pop(fd, None)
            if not fds:
                log.error("All keyboards disconnected; exiting.")
                os._exit(1)

    # ---- system tray ----------------------------------------------------
    def _make_tray(self, combo):
        """Build a QSystemTrayIcon. Returns a Tray object, or None if Qt/the
        tray is unavailable (daemon then runs headless with toasts+sound)."""
        try:
            from PyQt5.QtWidgets import (
                QApplication, QSystemTrayIcon, QMenu, QAction,
            )
            from PyQt5.QtGui import (
                QPixmap, QPainter, QColor, QBrush, QPen, QIcon,
            )
            from PyQt5.QtCore import Qt, QObject, pyqtSignal
        except Exception as e:
            log.warning("tray disabled (PyQt5 unavailable): %s", e)
            return None

        daemon = self
        COLORS = {
            "idle": "#3b82f6",       # blue  — ready
            "recording": "#ef4444",  # red   — listening
            "busy": "#f59e0b",       # amber — transcribing
            "paused": "#6b7280",     # grey  — paused
        }
        TIPS = {
            "idle": f"Typefree — ready ({combo})",
            "recording": "Typefree — 🎙️ listening…",
            "busy": "Typefree — ⏳ transcribing…",
            "paused": "Typefree — ⏸️ paused",
        }

        def make_icon(state):
            color = COLORS.get(state, COLORS["idle"])
            pm = QPixmap(64, 64)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(color)))
            p.drawEllipse(4, 4, 56, 56)
            # white mic glyph
            p.setBrush(QBrush(QColor("white")))
            p.drawRoundedRect(26, 16, 12, 21, 6, 6)   # capsule
            pen = QPen(QColor("white"))
            pen.setWidth(3)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(23, 24, 18, 18, 180 * 16, 180 * 16)  # cradle
            p.drawLine(32, 42, 32, 48)                      # stem
            p.drawLine(25, 48, 39, 48)                      # base
            p.end()
            return QIcon(pm)

        class Tray(QObject):
            sig = pyqtSignal(str)  # marshals state changes onto the GUI thread

            def __init__(self):
                super().__init__()
                self.app = QApplication.instance() or QApplication(sys.argv)
                self.app.setQuitOnLastWindowClosed(False)
                self.icon = QSystemTrayIcon()
                self.icon.setIcon(make_icon("idle"))
                self.icon.setToolTip(TIPS["idle"])

                menu = QMenu()
                header = QAction(f"Typefree — {combo} · {daemon.mode}", menu)
                header.setEnabled(False)
                menu.addAction(header)
                menu.addSeparator()
                self.pause_act = QAction("Pause dictation", menu, checkable=True)
                self.pause_act.toggled.connect(self._on_pause)
                menu.addAction(self.pause_act)
                menu.addSeparator()
                quit_act = QAction("Quit Typefree", menu)
                quit_act.triggered.connect(self._on_quit)
                menu.addAction(quit_act)
                self.icon.setContextMenu(menu)
                self.icon.show()
                self.sig.connect(self._apply)

            def _apply(self, state):
                self.icon.setIcon(make_icon(state))
                self.icon.setToolTip(TIPS.get(state, TIPS["idle"]))

            def set_state(self, state):
                """Thread-safe: callable from the evdev/worker threads."""
                self.sig.emit(state)

            def _on_pause(self, checked):
                daemon.paused = checked
                self.set_state("paused" if checked else "idle")
                if checked:
                    daemon._notify("⏸️ Paused", "Dictation hotkey is off")
                else:
                    daemon._notify("▶️ Resumed", f"Hold {combo} to dictate")

            def _on_quit(self):
                daemon._notify("🛑 Typefree stopped", "")
                self.icon.hide()
                os._exit(0)

            def run(self):
                self.app.exec_()

        try:
            return Tray()
        except Exception as e:
            log.warning("tray init failed (%s); running headless", e)
            return None


if __name__ == "__main__":
    cfg = load_config()
    Typefree(cfg).run()
