# Typefree — Project Memory / Handoff Log

> Purpose of this file: a precise, offline record of **what we built, why, what
> works, what's left, and what we want next** — so that a future session (me, the
> user, or another AI) can resume *without re-deriving context*. Read this first.

Last updated: 2026-06-07 (reliable autostart + configurable hotkey; default now
**Right Ctrl**, not Alt+Z. Repo pushed to GitHub. About to test a clean reinstall.)

---

## 1. What the user wants (the goal, in their words)

- A mic-to-text tool on **Linux** that works like Windows' **`Win+H`**.
- Must work **everywhere** — wherever the text cursor is blinking (browser,
  terminal/CLI, editor, chat). Output to **both** the cursor *and* the clipboard.
- **Always on** (background daemon, auto-start on boot).
- **Free** and prefer **offline**.
- Hotkey: **customizable**, chosen at install. Default is now **Right Ctrl**
  (was Alt+Z — see §4f for why letter hotkeys leak and F-keys can need Fn).
- Recording style: **hold-to-talk** (hold to record, release to transcribe).
  Toggle (press to start / press again to stop) is also acceptable.
- The user is **not technical about internals** — wants it to "just work",
  minimal manual steps, and does **not** want to approve command after command.
- The user runs commands as **the logged-in user** (`hero`), not as root/Claude.

## 2. Environment (verified facts about the machine)

- Host: `TheBeast`. User: `hero`. Email: iamhero337@gmail.com.
- OS: KDE **neon** / Ubuntu 24.04 (noble) base. Kernel 6.17.
- **Session: Wayland** (`XDG_SESSION_TYPE=wayland`, `XDG_CURRENT_DESKTOP=KDE`).
  There's an XWayland display at `:1` but the compositor is Wayland (KWin).
- `sudo` password is known to the user; for non-interactive installs the script
  accepts `STT_PASSWORD=...` and pipes to `sudo -S`. **Do NOT commit the password.**
- Groups before install: hero was NOT in `input`. We added it (needs relogin).
- Audio: PipeWire/Pulse present; input devices include `sof-hda-dsp`, `pulse`,
  `default`. Mic capture via sounddevice/PortAudio works.

## 3. Why the design changed (the key lesson)

The **first attempt** used `pynput` (global hotkey) + `xclip` (clipboard) +
`print()` to stdout. **This cannot work on Wayland:**
- Wayland blocks apps from grabbing **global** hotkeys → pynput can't see Alt+Z
  system-wide.
- `xclip` only talks to the **X11** clipboard, not Wayland-native apps.
- A daemon's `print()` goes to the **journal/log**, NOT to the focused input —
  it does not "type where the cursor is".

So we **re-architected around kernel-level mechanisms** that bypass Wayland's
restrictions and work on **both Wayland and X11**:
- **evdev** reads the keyboard from `/dev/input/event*` (sees the hotkey globally).
- **ydotool + ydotoold** inject keystrokes via `/dev/uinput` (types at the cursor).
- **wl-copy** (Wayland) / **xclip** (X11 fallback) for the clipboard.
- **OpenAI Whisper** (local model) does the speech→text, offline.

### The ydotool gotcha (important)
Ubuntu's apt ships **ydotool 0.1.8**, which has **no daemon** — it recreates the
virtual uinput device on every call, so the **first characters get dropped**
("ydotoold backend unavailable… latency+delay issues"). We **build ydotool 1.x
from source** so `ydotoold` keeps a persistent warm device. **Verified**: a full
test string round-tripped with **zero dropped characters**.

## 4. What was built (current state — all committed to git)

Repo: `/home/hero/Documents/Gits/typefree` — on GitHub at
`git@github.com:Iamhero337/typefree.git` (`origin/main`).

Files:
- `typefree.py` — the daemon. evdev hotkey loop (hold/toggle), sounddevice
  recording, Whisper transcription, output via ydotool (type) + wl-copy/xclip.
  Config from `~/.config/typefree/config.json` + `TYPEFREE_*` env overrides.
- `requirements.txt` — openai-whisper, evdev, sounddevice, scipy, numpy.
- `install.sh` — full installer (apt deps, builds ydotool 1.x, pip install,
  adds user to `input`, installs udev rule, installs+enables user services).
  Supports `STT_PASSWORD` for non-interactive sudo.
- `uninstall.sh`, `status.sh`, `logs.sh` — helpers.
- `typefree.service` — user service running the daemon (Wants/After ydotoold).
- `ydotoold.service` — user service for ydotoold (`/usr/local/bin/ydotoold`,
  socket at `$XDG_RUNTIME_DIR/.ydotool_socket`, perm 0660, owned by user).
- `99-typefree-uinput.rules` — udev rule: `/dev/uinput` → group `input`, 0660.
- `config.example.json` — default config (copied to ~/.config on install).
- `README.md` — user-facing docs. `architecture.md` — deep technical docs.
- `LICENSE` (MIT), `.gitignore`.

### Verified working (on this machine)
- ✅ ydotool 1.x typing — exact string round-trip, no dropped chars
  (proved with an evdev capture script reading the virtual device).
- ✅ Whisper loads `base` and transcribes (tested via espeak-ng synthetic audio).
- ✅ evdev `find_keyboards()` finds both real keyboards.
- ✅ mic input devices present; sounddevice imports/queries fine.
- ✅ wl-copy/wl-paste clipboard round-trip.
- ✅ udev rule applied: `/dev/uinput` is `crw-rw---- root input`.
- ✅ Both services `enabled` for auto-start.

## 4b. User feedback (added after reboot — the "how do I know it's running?" fix)

After the reboot cleared the `input`-group blocker, both services auto-started
and a live mic test **worked end-to-end** (hold Alt+Z → speech typed at cursor +
on clipboard). The user's next concern: a silent background daemon gives **no
indication** it's recording/working — all status went only to the journal.

So we added **two-channel feedback** (both toggleable in config: `notify`, `sound`):
- **On-screen toasts** via `notify-send` (libnotify-bin). Uses
  `x-canonical-private-synchronous:typefree` hint so toasts replace in one slot
  instead of stacking. States: 🎤 ready (on boot) · 🎙️ Listening… (record start)
  · ⏳ Transcribing… (release) · 📝 Typed: <preview> (success) · 🔇 No speech /
  🤚 Too short / ⚠️ failed (problems).
- **Sound cues** via `paplay` (already present) playing freedesktop `.oga`:
  `message-new-instant` (start) · `dialog-warning` (empty/error). Files live in
  `/usr/share/sounds/freedesktop/stereo/`. **User preference (asked & confirmed):
  only the START sound — success is SILENT (the 📝 toast is enough). Do not add a
  "done" chime back.** The warn tone only fires on failure.

Helpers `_notify()` / `_play()` in `typefree.py` are best-effort & non-blocking
(`subprocess.Popen`, swallow errors) so feedback never breaks dictation.
`libnotify-bin` added to `install.sh` apt deps. Service env already has
`DBUS_SESSION_BUS_ADDRESS` + `WAYLAND_DISPLAY`, so toasts reach the screen from
the `systemd --user` unit. **User confirmed: "it works perfectly, I got what I
wanted."**

## 4c. System-tray icon (added after feedback)

Native **KDE Plasma tray icon** via **PyQt5 `QSystemTrayIcon`** (already installed;
speaks the StatusNotifierItem/SNI spec that Plasma Wayland uses — no AppIndicator
needed). Qt inits fine from the `systemd --user` env with `QT_QPA_PLATFORM`
defaulting to wayland; `QSystemTrayIcon.isSystemTrayAvailable()` → True.

Design (in `typefree.py`, method `_make_tray`):
- Qt **must own the main thread**, so when the tray is on, the evdev select loop
  moves to a background thread (`_evdev_loop`) and `app.exec_()` blocks in main.
  Tray off → evdev loop runs in main thread as before (graceful fallback if PyQt
  missing / headless; logs a warning, keeps toasts+sound).
- Icon is drawn at runtime with QPainter (a white mic glyph on a colored disc) —
  no icon files. Colors encode state: blue=ready, red=listening, amber=
  transcribing, grey=paused. Tooltip mirrors the state.
- Cross-thread updates are marshaled via a `pyqtSignal(str)` (`set_state()` is
  safe to call from the evdev/worker threads; the slot runs on the GUI thread).
- Right-click menu: header (hotkey·mode), **Pause dictation** (checkable → sets
  `daemon.paused`, which short-circuits `_on_key`), **Quit Typefree** (`os._exit(0)`;
  service is `Restart=on-failure` so a clean exit stays down).
- Config flag `tray` (default true). `python3-pyqt5` added to install.sh deps.
- Verified: SNI item registered with `org.kde.StatusNotifierWatcher`, and the
  owning DBus connection PID == the typefree MainPID.

## 4d. GOTCHA: installed copy vs repo (BIT US ONCE — now fixed)

`typefree.service` ExecStart runs **`%h/.local/share/typefree/typefree.py`**, the
**installed** copy — NOT the repo. install.sh does `cp typefree.py "$SHARE_DIR/"`.
So editing the repo + `systemctl --user restart` ran **stale code**: the first
round of toast/sound edits never actually executed in the daemon (only the manual
`notify-send`/`paplay` test commands did). **Fix applied on this machine:** the
installed file is now a **symlink** → the repo
(`~/.local/share/typefree/typefree.py -> /home/hero/Documents/Gits/typefree/typefree.py`),
so every repo edit + restart is live. (A fresh `install.sh` run would replace the
symlink with a copy again — re-symlink after installing if developing.)

## 4e. Reliable autostart (fixed a once-per-boot crash)

Fresh install + reboot: the daemon **core-dumped once on every boot**, then
systemd's `Restart` brought it back ~3s later — so `status.sh` always looked
healthy and the failure hid in the journal (`qt.qpa.xcb: could not connect to
display` → `qFatal` → SIGABRT core dump).

Root cause: the user unit was `WantedBy=default.target`, so it started at early
login **before** the KDE compositor exported `WAYLAND_DISPLAY`/`DISPLAY`. The
`After=graphical-session.target` line was inert because the unit wasn't *wanted
by* that target. Qt's tray init then hit no-display and aborted at the **C++
level** — uncatchable by the Python `try/except` (so the "run headless" fallback
never fired).

Fixes (commit `49927fc`):
- `typefree.service`: `PartOf=graphical-session.target` + `WantedBy=graphical-session.target`
  so the `After=` ordering is effective and it starts only once a display exists.
- `typefree.py`: `_wait_for_display()` guards Qt init (polls `WAYLAND_DISPLAY`/
  `DISPLAY` up to 8s) — never touches Qt without a display, so it falls back to
  the headless toasts+sound path instead of crashing.
- `typefree-launch.sh` (new) + `.desktop`: clicking the app icon is now
  **idempotent + self-healing** — `systemctl --user enable` (autostart) + `start`
  + a confirmation toast. Installer fills the launcher's absolute path into the
  `.desktop` (Exec can't expand `$HOME`). This is the manual fallback if boot
  autostart ever doesn't take (e.g. first login before the `input` group is live).
- **User CONFIRMED** clean autostart on a real reboot ("done it started").

## 4f. Configurable hotkey + safe default (Alt+Z → Right Ctrl)

Two real bugs with letter hotkeys, both because the daemon reads evdev
**passively** (it never *consumes* the keystroke, so the focused app sees it too):
1. **Leak:** pressing Alt+Z fast enough that `Z` registers before `Alt` types a
   literal `z`. The user hit this hard in KDE's app-search field: **autorepeat
   flooded `ZZZZZZ`** while held.
2. **Fn trap:** F9 (a leak-proof function key) needs **Fn+F9** on media-key
   keyboards — annoying. The user's keyboard does this.

Also checked the user's idea of **Super+Z**: it's already KDE `MinimizeAll=Meta+Z`
*and* still a letter combo (same leak) — rejected. (Scanned `kglobalshortcutsrc`;
`Meta+Space`, `Ctrl+Space`, `Meta+grave`, `Menu`, `Pause` are FREE on this box.)

Resolution:
- **Default is now `rightctrl` / `none`** — no Fn, no character (can't leak),
  and the desktop doesn't grab a lone Right Ctrl. The user is running it; the
  daemon announces `🎤 Typefree ready — Right Ctrl`.
- `typefree.py`: added non-letter keys to `KEYCODE` (`rightctrl`, `rightalt`,
  `rightshift`, `menu`, `capslock`, `insert`, `home`, `end`, `pageup`, `pagedown`,
  `pause`, `scrolllock`, `printscreen`), plus a `KEY_LABELS` map so the UI shows
  "Right Ctrl" not "RIGHTCTRL". DEFAULT_CONFIG updated.
- **`config.json` is now the single source of truth** for the hotkey: the
  service unit **no longer** sets `TYPEFREE_HOTKEY/MODIFIER/MODE` (those env vars
  would override config, defeating the picker). Env override still works if you
  set it yourself. Only `YDOTOOL_SOCKET` stays in the unit.
- `install.sh`: new **`choose_hotkey()`** picker (mirrors `choose_model()`),
  written into config.json at install. Options: 1) Right Ctrl (recommended)
  2) F9 (with Fn caveat) 3) Alt+Z 4) Menu 5) custom (`key` or `modifier+key`).
  Honors `$TYPEFREE_HOTKEY`/`$TYPEFREE_MODIFIER`; defaults `rightctrl none` non-TTY.
- **Multi-modifier (e.g. Ctrl+Alt+Z) is NOT supported yet** — daemon takes a
  single modifier only. Open if the user wants it.
- Docs (README, architecture, .desktop) updated to Right Ctrl.

## 5. The ONE remaining manual step (blocker) — RESOLVED

The user must **log out and back in** (or reboot) **once**. Reason: `hero` was
added to the `input` group, but Linux only applies new group membership on a
fresh login. Until then, the `systemctl --user` services can't open
`/dev/input` (evdev) or `/dev/uinput` (ydotoold) — both fail with permission /
exit-code errors and loop on restart. This is unavoidable from a script.

After relogin, both services auto-start. Verify with:
```bash
bash ~/Documents/Gits/typefree/status.sh
```
Then: **hold Alt+Z, speak, release** → text at cursor + on clipboard.

### How to test BEFORE relogin (for an AI/dev debugging)
Use `sg input -c '...'` to run a command in a shell that has the `input` group
immediately, e.g. the verification we used:
`sg input -c 'python3 /tmp/verify_typing.py'`.

## 6. What we want next / open ideas (not done yet)

- ✅ DONE: confirmed end-to-end after reboot (live mic test passed).
- ✅ DONE: recording-state feedback — toasts + sounds (see §4b). A persistent
  tray icon is still possible but toasts already cover the need.
- ✅ CONFIRMED: PortAudio reaches Pulse/PipeWire from the `--user` service.
- ✅ DONE: installer now asks accuracy-vs-RAM and writes the chosen Whisper model
  into config.json (`choose_model()` in install.sh; honors $TYPEFREE_MODEL, falls
  back to 'base' when non-interactive). Models & approx *resident* RAM: tiny ~0.6,
  base ~0.9, small ~1.3, medium ~2.5, large ~4.5 GB — model stays loaded (always-on
  daemon), runs on CPU not GPU.
- User's current machine runs the **`small`** model (~1.2 GB measured), chosen
  2026-06-07 for better laptop accuracy.
- ✅ DONE: hotkey is now chosen at install (`choose_hotkey()`), stored in
  config.json; default Right Ctrl. See §4f.
- ✅ DONE: reliable boot autostart + self-healing app launcher. See §4e.
- ✅ DONE: repo pushed to **GitHub** (`git@github.com:Iamhero337/typefree.git`,
  `origin/main`).
- Possible: **multi-modifier hotkeys** (Ctrl+Alt+Z) — not supported yet.
- Possible: a `typefree` CLI to change hotkey/model without editing JSON.
- Possible: auto-paste fallback (`ydotool key ctrl+v`) for apps where typing is
  slow; note terminals need ctrl+shift+v.
- Possible: VAD / silence trim, and a "type incrementally as you speak" mode.
- Audio in the user service: confirm PortAudio reaches Pulse/PipeWire under
  systemd `--user` (worked in a normal shell; re-check inside the service).

## 7. Gotchas / decisions to remember

- Keep everything **user-level** (user services + `input` group). We chose this
  over a root service because audio (Pulse) and the Wayland session live in the
  user session; a root daemon can't easily reach them.
- `pip` needs `--break-system-packages` on this Ubuntu (PEP 668).
- ydotool must be **1.x from source**; do not rely on apt's 0.1.8.
- Hotkey codes: `typefree.py` maps friendly names → evdev keycodes; modifier is
  one of alt/ctrl/shift/super/none.
- Default Whisper model is `base` (good speed/accuracy). Bigger = more accurate,
  slower, larger download.
- **Never commit the sudo password** or any secret to git.
