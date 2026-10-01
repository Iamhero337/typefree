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
import threading
import subprocess

import numpy as np
import sounddevice as sd
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
    "hotkey": "rightctrl",  # main key, combined with a modifier below
    "modifier": "none",     # alt | ctrl | super | shift | none
                            # default is a bare Right-Ctrl on purpose: it needs
                            # no Fn (unlike F-keys on media keyboards), produces
                            # no character (so a leaked keystroke can't type
                            # text — a letter hotkey floods "zzz" via autorepeat),
                            # and isn't grabbed by the desktop on its own.
    "mode": "hold",         # hold | toggle
    "model": "base",        # whisper: tiny|base|small|medium|large
    "language": "en",       # transcription language, or "auto"
    "type_out": True,       # type text at the cursor
    "clipboard": True,      # also copy to clipboard
    "notify": True,         # show on-screen toast (recording / result)
    "sound": True,          # play a short audio cue (start / done / empty)
    "tray": True,           # show a system-tray icon (status + menu)
    "sample_rate": 16000,
    "device": "auto",       # auto | cuda | cpu  (auto = GPU when available)
    "keep_warm_s": 300,     # keep the model loaded this long after a dictation
                            # (instant repeat dictations); 0 = free RAM at once
    "type_delay_ms": 1,     # per-key delay for ydotool (its default, 20, types
                            # only ~25 chars/s — a long dictation took 15 s+)
}

# freedesktop sound cues (played via paplay / pw-play).
# Only "start" chimes on every use; success is silent (the toast is enough).
# "empty" is a quiet warning that only fires when a dictation failed.
SOUNDS = {
    "start": "message-new-instant",
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
    # Non-character keys usable as a standalone hotkey (modifier = none).
    # Handy when the F-row needs Fn to reach (media-key keyboards) — these need
    # no Fn and still type nothing. "rightctrl" is the ergonomic default.
    "rightctrl": ecodes.KEY_RIGHTCTRL, "rightalt": ecodes.KEY_RIGHTALT,
    "rightshift": ecodes.KEY_RIGHTSHIFT, "menu": ecodes.KEY_COMPOSE,
    "capslock": ecodes.KEY_CAPSLOCK, "insert": ecodes.KEY_INSERT,
    "home": ecodes.KEY_HOME, "end": ecodes.KEY_END,
    "pageup": ecodes.KEY_PAGEUP, "pagedown": ecodes.KEY_PAGEDOWN,
    "pause": ecodes.KEY_PAUSE, "scrolllock": ecodes.KEY_SCROLLLOCK,
    "printscreen": ecodes.KEY_SYSRQ,
}

MODIFIERS = {
    "alt": {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT},
    "ctrl": {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
    "shift": {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT},
    "super": {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA},
    "none": set(),
}

# Pretty names for keys that shouldn't be shown as a bare uppercase token
# (used in the "ready" log line, toast, and tray tooltip).
KEY_LABELS = {
    "rightctrl": "Right Ctrl", "rightalt": "Right Alt",
    "rightshift": "Right Shift", "menu": "Menu", "capslock": "Caps Lock",
    "pageup": "Page Up", "pagedown": "Page Down", "scrolllock": "Scroll Lock",
    "printscreen": "Print Screen", "space": "Space",
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

# ---- transcription worker (separate process) ------------------------------
# The model lives in a child process so the daemon itself stays tiny (~100 MB):
# PyTorch/CTranslate2 never give their memory back to the OS, but a process
# exit does. For speed, the worker is started the moment you *press* the hotkey
# (the model loads while you speak), and it stays warm for `keep_warm_s`
# seconds after each dictation so back-to-back dictations are instant.

TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def _load_engine(cfg):
    """Load the fastest available Whisper engine. Returns (transcribe_fn, desc).

    Prefers faster-whisper (CTranslate2): ~4x faster than openai-whisper and
    on the GPU when there is one. Falls back to CPU if CUDA fails, and to
    openai-whisper if faster-whisper isn't installed.
    """
    lang = None if cfg["language"] == "auto" else cfg["language"]
    want = cfg.get("device", "auto")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        WhisperModel = None

    if WhisperModel is not None:
        import ctranslate2
        devices = []
        if want in ("auto", "cuda") and ctranslate2.get_cuda_device_count() > 0:
            devices.append(("cuda", "float16"))
        if want in ("auto", "cpu") or not devices:
            devices.append(("cpu", "int8"))
        threads = max(1, min(8, (os.cpu_count() or 2) // 2))
        last_err = None
        for device, compute in devices:
            try:
                kw = dict(device=device, compute_type=compute, cpu_threads=threads)
                try:  # skip the Hugging Face network round-trip when cached
                    model = WhisperModel(cfg["model"], local_files_only=True, **kw)
                except Exception:
                    log.info("downloading faster-whisper model '%s' (one-time) ...",
                             cfg["model"])
                    model = WhisperModel(cfg["model"], **kw)
                beam = 5 if device == "cuda" else 1  # beam search is ~free on GPU

                def transcribe(audio, model=model, beam=beam):
                    segments, _ = model.transcribe(
                        audio,
                        language=lang,
                        beam_size=beam,
                        # Silero VAD drops silence: faster, and it stops the
                        # "you" / "Thank you." hallucinations on empty clips.
                        vad_filter=True,
                        no_speech_threshold=0.3,
                        log_prob_threshold=-1.5,
                        compression_ratio_threshold=2.8,
                        condition_on_previous_text=True,
                        temperature=TEMPERATURES,
                    )
                    return "".join(s.text for s in segments).strip()

                # warm-up pass: first CUDA call pays kernel/cuBLAS init costs
                transcribe(np.zeros(16000, dtype=np.float32))
                return transcribe, f"faster-whisper {cfg['model']} on {device} ({compute})"
            except Exception as e:
                last_err = e
                log.warning("faster-whisper on %s failed: %s", device, e)
        log.warning("falling back to openai-whisper (%s)", last_err)

    import whisper
    import torch
    device = "cuda" if want != "cpu" and torch.cuda.is_available() else "cpu"
    model = whisper.load_model(cfg["model"], device=device)

    def transcribe(audio):
        result = model.transcribe(
            audio,
            language=lang,
            fp16=(device == "cuda"),
            no_speech_threshold=0.3,
            logprob_threshold=-1.5,
            compression_ratio_threshold=2.8,
            condition_on_previous_text=True,
            temperature=TEMPERATURES,
        )
        return result["text"].strip()

    return transcribe, f"openai-whisper {cfg['model']} on {device}"


def transcription_worker(cfg, conn):
    """Child-process main loop. Messages from the daemon:
         ("ping",)                      keep-alive (sent when recording starts)
         ("transcribe", job_id, audio)  float32 mono @ sample_rate
       Replies: ("ready", desc) once, then ("result"|"error", job_id, payload).
    """
    t0 = time.monotonic()
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)  # per-clip spam
    try:
        transcribe, desc = _load_engine(cfg)
    except Exception as e:
        log.error("could not load Whisper model: %s", e)
        conn.send(("fatal", None, str(e)))
        return
    log.info("model ready in %.1fs: %s", time.monotonic() - t0, desc)
    conn.send(("ready", None, desc))

    keep_warm = float(cfg.get("keep_warm_s", 300))
    # Before the first job the user may still be talking, so wait generously.
    timeout = max(keep_warm, 300.0)
    while True:
        try:
            if not conn.poll(timeout):
                log.info("worker idle for %.0fs; exiting to free memory", timeout)
                return
            msg = conn.recv()
        except (EOFError, OSError):
            return  # daemon went away
        if msg[0] != "transcribe":
            continue  # ping: just resets the idle timer
        _, job_id, audio = msg
        try:
            t = time.monotonic()
            text = transcribe(audio)
            log.info("transcribed %.1fs of audio in %.2fs",
                     len(audio) / cfg["sample_rate"], time.monotonic() - t)
            conn.send(("result", job_id, text))
        except Exception as e:
            log.error("transcription failed in worker: %s", e)
            try:
                conn.send(("error", job_id, str(e)))
            except OSError:
                return
        timeout = keep_warm
        if timeout <= 0:
            return


class Transcriber:
    """Daemon-side handle on the (lazily started) transcription worker."""

    def __init__(self, cfg):
        import multiprocessing as mp
        # "spawn", not fork: the daemon has Qt/PortAudio threads running, and
        # forking a threaded process can deadlock the child.
        self._ctx = mp.get_context("spawn")
        self.cfg = cfg
        self._lock = threading.Lock()
        self._proc = None
        self._conn = None
        self._ready = False
        self._job = 0

    def _alive(self):
        return self._proc is not None and self._proc.is_alive()

    def _spawn(self):
        parent, child = self._ctx.Pipe()
        proc = self._ctx.Process(
            target=transcription_worker, args=(self.cfg, child),
            name="typefree-worker", daemon=True,
        )
        proc.start()
        child.close()
        self._proc, self._conn, self._ready = proc, parent, False

    def _kill(self):
        if self._proc is not None:
            if self._proc.is_alive():
                self._proc.kill()
            self._proc.join(timeout=2)
        if self._conn is not None:
            self._conn.close()
        self._proc = self._conn = None
        self._ready = False

    def prewarm(self):
        """Start loading the model now (non-blocking), or keep a warm one alive."""
        with self._lock:
            try:
                if self._alive():
                    self._conn.send(("ping",))
                    return
                self._kill()  # reap an idle-exited worker
                self._spawn()
            except Exception as e:
                log.warning("could not start transcription worker: %s", e)
                self._kill()

    @property
    def ready(self):
        """True if the model is loaded (never blocks). Call only while no job
        is in flight — it consumes the worker's pending "ready" message."""
        with self._lock:
            if not self._alive():
                return False
            try:
                while not self._ready and self._conn.poll(0):
                    kind = self._conn.recv()[0]
                    if kind == "ready":
                        self._ready = True
                    elif kind == "fatal":
                        self._kill()  # transcribe() respawns and reports it
                        return False
            except (EOFError, OSError):
                self._kill()
                return False
            return self._ready

    def transcribe(self, audio):
        """Blocking. Returns the text; raises RuntimeError on failure."""
        for attempt in (1, 2):
            with self._lock:
                if not self._alive():
                    self._kill()
                    self._spawn()
                self._job += 1
                job, conn = self._job, self._conn
            try:
                conn.send(("transcribe", job, audio))
                return self._wait(conn, job, len(audio) / self.cfg["sample_rate"])
            except (EOFError, OSError, BrokenPipeError) as e:
                # the worker idled out just as we sent the job; retry once
                log.info("worker went away (%s); restarting", e or type(e).__name__)
                with self._lock:
                    self._kill()
                if attempt == 2:
                    raise RuntimeError("transcription worker crashed") from e

    def _wait(self, conn, job, duration_s):
        # Loading can include a one-time model download, so be patient until
        # the worker says "ready"; after that, a stuck job is a real hang.
        deadline = time.monotonic() + (60 + 2 * duration_s if self._ready else 900)
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not conn.poll(left):
                with self._lock:
                    self._kill()
                raise RuntimeError("transcription timed out")
            kind, job_id, payload = conn.recv()
            if kind == "ready":
                self._ready = True
                deadline = time.monotonic() + 60 + 2 * duration_s
            elif kind == "fatal":
                with self._lock:
                    self._kill()
                raise RuntimeError(payload)
            elif job_id == job:
                if kind == "error":
                    raise RuntimeError(payload)
                return payload

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
            self._q.put(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32", callback=cb
        )
        self._stream.start()

    def stop(self):
        """Stop and return the recorded audio as a float32 array in [-1, 1]."""
        if not self._active:
            return None
        self._active = False
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._stream = None
        # Drain all remaining frames — get_nowait avoids blocking and
        # is safe here because the stream is already stopped.
        while True:
            try:
                self._frames.append(self._q.get_nowait())
            except queue.Empty:
                break
        if not self._frames:
            return None
        audio = np.concatenate(self._frames, axis=0).flatten()
        # handed straight to Whisper: no WAV file, no ffmpeg decode
        return np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False)


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
        self.transcriber = Transcriber(cfg)
        self._have_ydotool = shutil.which("ydotool") is not None
        self._have_wlcopy = shutil.which("wl-copy") is not None
        self._have_xclip = shutil.which("xclip") is not None
        self._notify_bin = shutil.which("notify-send")
        self._play_bin = shutil.which("paplay") or shutil.which("pw-play")

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
        # -d/-H: ydotool's 20 ms defaults cost 40 ms per character. Text goes
        # in via stdin (-f -), which also turns off backslash-escape parsing.
        delay = str(max(0, int(self.cfg.get("type_delay_ms", 1))))
        timeout_s = 30 + len(text) * (2 * int(delay) + 5) / 1000
        try:
            subprocess.run(
                ["ydotool", "type", "-d", delay, "-H", delay, "-f", "-"],
                input=text.encode(), env=env, timeout=timeout_s,
            )
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
        # load the model while the user is still speaking
        self.transcriber.prewarm()
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
                log.info("recorded %.1f s of audio",
                         len(audio) / self.cfg["sample_rate"])
                if not self.transcriber.ready:
                    self._notify("⏳ Loading model…", "")
                t = time.monotonic()
                text = self.transcriber.transcribe(audio)
                log.info("transcription took %.2fs", time.monotonic() - t)
                if not text:
                    log.info("(no speech detected)")
                    self._notify("🔇 No speech detected", "")
                    self._play("empty")
                    return
                log.info("📝 %s", text)
                self._clip_text(text)
                t = time.monotonic()
                self._type_text(text)
                log.info("typed %d chars in %.2fs", len(text), time.monotonic() - t)
                preview = text if len(text) <= 80 else text[:77] + "…"
                self._notify("📝 Typed", preview)
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
        keyname = KEY_LABELS.get(self.cfg["hotkey"], self.cfg["hotkey"].upper())
        combo = keyname if modname == "none" \
            else f"{modname.capitalize()}+{keyname}"
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
    @staticmethod
    def _wait_for_display(timeout=8.0):
        """Return True once a display is reachable, else False after `timeout`.

        Qt aborts the *whole process* with qFatal()/SIGABRT if it can't connect
        to a display — that's a C++ abort, not a Python exception, so the
        try/except around Tray() cannot catch it. We must therefore confirm a
        display exists *before* importing Qt. At boot the systemd service can
        start a hair before the compositor exports WAYLAND_DISPLAY/DISPLAY, so
        we poll briefly to absorb that race rather than giving up immediately.
        """
        deadline = time.monotonic() + timeout
        while True:
            if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

    def _make_tray(self, combo):
        """Build a QSystemTrayIcon. Returns a Tray object, or None if Qt/the
        tray is unavailable (daemon then runs headless with toasts+sound)."""
        if not self._wait_for_display():
            log.warning("no display (WAYLAND_DISPLAY/DISPLAY unset); "
                        "running headless with toasts+sound")
            return None
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
