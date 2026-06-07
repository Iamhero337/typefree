# Typefree — Project Memory / Handoff Log

> Purpose of this file: a precise, offline record of **what we built, why, what
> works, what's left, and what we want next** — so that a future session (me, the
> user, or another AI) can resume *without re-deriving context*. Read this first.

Last updated: 2026-06-07 (post-reboot: live end-to-end CONFIRMED working)

---

## 1. What the user wants (the goal, in their words)

- A mic-to-text tool on **Linux** that works like Windows' **`Win+H`**.
- Must work **everywhere** — wherever the text cursor is blinking (browser,
  terminal/CLI, editor, chat). Output to **both** the cursor *and* the clipboard.
- **Always on** (background daemon, auto-start on boot).
- **Free** and prefer **offline**.
- Hotkey: **`Alt+Z`**, and **customizable** to anything.
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

Repo: `/home/hero/Documents/Gits/typefree` (git initialized, 2 commits).

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
  `message-new-instant` (start) · `complete` (done) · `dialog-warning` (empty/err).
  Files live in `/usr/share/sounds/freedesktop/stereo/`.

Helpers `_notify()` / `_play()` in `typefree.py` are best-effort & non-blocking
(`subprocess.Popen`, swallow errors) so feedback never breaks dictation.
`libnotify-bin` added to `install.sh` apt deps. Service env already has
`DBUS_SESSION_BUS_ADDRESS` + `WAYLAND_DISPLAY`, so toasts reach the screen from
the `systemd --user` unit. **User confirmed: "it works perfectly, I got what I
wanted."**

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
- Possible: a `typefree` CLI to change hotkey/model without editing JSON.
- Possible: auto-paste fallback (`ydotool key ctrl+v`) for apps where typing is
  slow; note terminals need ctrl+shift+v.
- Possible: VAD / silence trim, and a "type incrementally as you speak" mode.
- Audio in the user service: confirm PortAudio reaches Pulse/PipeWire under
  systemd `--user` (worked in a normal shell; re-check inside the service).
- Consider pushing the repo to GitHub (not done; no remote configured).

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
