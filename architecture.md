# Typefree — Architecture

This document explains *how Typefree works*, *why each piece exists*, and *how
the pieces fit together*. For a quick handoff/status, see `memory.md`. For user
instructions, see `README.md`.

---

## 1. The problem

Linux has no built-in "press a key, dictate text anywhere" feature like Windows'
`Win+H`. Building one needs three capabilities, and on **Wayland** each is
deliberately restricted for security:

1. **Hear a global hotkey** even when our app isn't focused.
2. **Inject text** into whatever app currently has focus.
3. **Record the mic** and **turn speech into text**.

On X11, apps can grab global keys and synthesize input freely (`pynput`,
`xdotool`). On **Wayland**, a normal app **cannot** grab global hotkeys or send
keystrokes to other apps. So we go *below* the display server, to the Linux
input subsystem, which works the same on X11 and Wayland.

## 2. The approach in one picture

```
   ┌──────────────┐    Alt+Z (key events)     ┌──────────────────┐
   │   keyboard   │ ───────────────────────▶  │  typefree daemon │
   │ /dev/input/* │        (evdev read)        │   (typefree.py)  │
   └──────────────┘                            └────────┬─────────┘
                                                         │ start/stop
                                                         ▼
                                              ┌────────────────────┐
                                              │  sounddevice (mic)  │
                                              │  PortAudio → Pulse  │
                                              └─────────┬──────────┘
                                                        │ WAV (16 kHz mono)
                                                        ▼
                                              ┌────────────────────┐
                                              │   OpenAI Whisper    │
                                              │   (local, offline)  │
                                              └─────────┬──────────┘
                                                        │ text
                              ┌─────────────────────────┴───────────────┐
                              ▼                                          ▼
                   ┌────────────────────┐                    ┌────────────────────┐
                   │ ydotool → ydotoold │                    │  wl-copy / xclip   │
                   │   → /dev/uinput    │                    │   (clipboard)      │
                   │ (types at cursor)  │                    └────────────────────┘
                   └────────────────────┘
```

## 3. Components and why each was chosen

### 3.1 Hotkey: `evdev` reading `/dev/input/event*`
- `evdev` is the Linux kernel input layer. Reading the device nodes gives **raw
  key up/down events** for every keyboard, regardless of display server or which
  window is focused — exactly what a global hotkey needs.
- **Why not pynput / KDE global shortcuts?** pynput can't grab global keys on
  Wayland. KDE shortcuts fire on *press* only, which doesn't model hold-to-talk
  well. evdev gives us clean press/release for both `hold` and `toggle` modes.
- **Permission:** `/dev/input/event*` is `root:input`. The user must be in the
  **`input`** group (set up by the installer; needs one relogin to take effect).
- `find_keyboards()` enumerates devices and keeps those advertising both
  `KEY_Z` and `KEY_LEFTALT` (a reliable "this is a real keyboard" heuristic).
- The daemon `select()`s over all keyboard fds and tracks modifier state itself.

### 3.2 Typing at the cursor: `ydotool` + `ydotoold` via `/dev/uinput`
- `/dev/uinput` lets a process **create a virtual input device** and emit events
  the compositor treats like real hardware — so injected text lands in whatever
  app is focused, on Wayland or X11.
- `ydotool` is the CLI; `ydotoold` is its daemon that **owns a persistent**
  virtual device.
- **Why build 1.x from source?** Ubuntu's `ydotool` **0.1.8 has no daemon**: it
  re-creates the uinput device on every invocation, and the compositor needs a
  few milliseconds to notice the new device — so the **first characters are
  dropped**. With `ydotoold` (1.x) the device stays warm, so **every character
  lands**. We verified this with an evdev capture test (full round-trip, 0 drops).
- **Permission:** a udev rule (`99-typefree-uinput.rules`) sets `/dev/uinput` to
  `root:input 0660`, so `input`-group members (the user, hence ydotoold) can open
  it. The ydotoold socket is created at `$XDG_RUNTIME_DIR/.ydotool_socket`,
  perm 0660, owned by the user; `typefree.py` points `YDOTOOL_SOCKET` there.

### 3.3 Clipboard: `wl-copy` (Wayland) / `xclip` (X11)
- Output goes to **both** the cursor (typed) and the clipboard, per the user's
  request. `wl-copy` is the Wayland-native clipboard tool; `xclip` is the X11
  fallback. The daemon prefers `wl-copy` and falls back to `xclip` if absent.

### 3.4 Audio capture: `sounddevice` (PortAudio)
- Records mono **16 kHz float32** (Whisper's native rate) into memory via a
  callback while the hotkey is held, then converts to int16 WAV.
- Runs in the **user** session so it reaches PipeWire/Pulse normally.

### 3.5 Speech → text: OpenAI **Whisper** (local)
- Fully offline; the model downloads once (~150 MB for `base`) to `~/.cache`.
- `base` is the default (good speed/accuracy). `small`/`medium`/`large` are more
  accurate but slower and larger. Configurable.
- Language is fixed (`en` by default) or `"auto"` to detect.

## 4. Process & service model

Everything runs as **user-level systemd services** (`systemctl --user`), not
root, because audio and the Wayland session live in the user session.

- **`ydotoold.service`** — starts `ydotoold` with the user-owned socket.
- **`typefree.service`** — runs `typefree.py`; `Wants=`/`After=ydotoold.service`
  so the typing backend is up first. Carries `TYPEFREE_*` env defaults and
  `YDOTOOL_SOCKET`.
- Both are `enabled` (WantedBy `default.target`) → auto-start on login/boot.

```
login session (user "hero", in group "input")
   └─ systemd --user
        ├─ ydotoold.service ──▶ /usr/local/bin/ydotoold  (owns /dev/uinput)
        └─ typefree.service ──▶ python3 typefree.py
                                   ├─ reads /dev/input (evdev)
                                   ├─ records mic (PortAudio)
                                   ├─ Whisper transcribe
                                   ├─ ydotool type  (via ydotoold socket)
                                   └─ wl-copy / xclip
```

### Why a relogin is required once
The installer adds the user to `input`, but group membership is captured at
**login**. The `systemd --user` manager inherited the *old* groups, so its
services can't open `/dev/input` or `/dev/uinput` until the next login. After
relogin, both services start cleanly and auto-run thereafter.

## 5. Configuration

`~/.config/typefree/config.json` (env vars `TYPEFREE_*` override it):

| Key          | Values                                  | Meaning                         |
|--------------|------------------------------------------|---------------------------------|
| `hotkey`     | `a`–`z`, `space`, `f1`–`f12`             | main key                        |
| `modifier`   | `alt`/`ctrl`/`shift`/`super`/`none`     | held with the key               |
| `mode`       | `hold` / `toggle`                        | push-to-talk vs press-on/off    |
| `model`      | `tiny`/`base`/`small`/`medium`/`large`  | Whisper size                    |
| `language`   | e.g. `en`, or `auto`                     | transcription language          |
| `type_out`   | `true`/`false`                           | type at cursor                  |
| `clipboard`  | `true`/`false`                           | copy to clipboard               |
| `sample_rate`| int (default 16000)                      | capture rate                    |

Apply changes: `systemctl --user restart typefree.service`.

## 6. Control flow in `typefree.py`

1. `load_config()` merges defaults + JSON + env.
2. `find_keyboards()` opens all keyboard devices.
3. Main loop `select()`s over keyboard fds; `_on_key()` tracks the modifier and
   the hotkey:
   - **hold:** key-down (with modifier) → `_start()`; key-up → `_stop_and_transcribe()`.
   - **toggle:** key-down toggles between start and stop.
4. `_start()` opens a `sounddevice.InputStream` and buffers frames.
5. `_stop_and_transcribe()` (background thread) builds a WAV, runs
   `whisper.transcribe`, then `_clip_text()` (wl-copy/xclip) and `_type_text()`
   (ydotool) on the result.

## 7. Security & privacy

- **Offline:** audio never leaves the machine; Whisper runs locally.
- **Privilege:** no root daemon. The only elevated setup is installing packages
  and a udev rule, plus adding the user to `input`. The `input` group grants read
  access to input devices — a deliberate, scoped trade-off for global hotkeys.
- **Secrets:** the installer can take the sudo password via `STT_PASSWORD` for
  automation; it is never written to disk or committed.

## 8. Failure modes & how they surface

| Symptom                          | Likely cause                                  | Fix                                   |
|----------------------------------|-----------------------------------------------|---------------------------------------|
| Alt+Z does nothing               | not in active `input` group                   | log out/in; `bash status.sh`          |
| services loop / won't start      | pre-relogin (no group) or uinput perms        | relogin; check `99-typefree-uinput.rules` |
| text copied but not typed        | ydotoold down / `/dev/uinput` perms           | `systemctl --user status ydotoold`    |
| first chars missing when typing  | old ydotool 0.1.8 (no daemon)                 | ensure 1.x from source is installed   |
| no/garbled transcription         | wrong mic / model too small                   | check `arecord -l`; bigger model      |

## 9. Repository layout

```
typefree/
├── typefree.py               # the daemon
├── requirements.txt          # python deps
├── install.sh                # full installer (builds ydotool 1.x)
├── uninstall.sh              # removal + manual-cleanup hints
├── status.sh / logs.sh       # helpers
├── typefree.service          # user service: the daemon
├── ydotoold.service          # user service: ydotool daemon
├── 99-typefree-uinput.rules  # udev: /dev/uinput -> group input 0660
├── config.example.json       # default config
├── README.md                 # user guide
├── architecture.md           # this file
├── memory.md                 # handoff / status log
└── LICENSE                   # MIT
```
