# Packaging & distribution

How to ship Typefree to users — and, just as importantly, **which channels fit a
tool like this**. Typefree needs privileged input access (`/dev/input` read,
`/dev/uinput` write) plus host integration (a udev rule, the `input` group, user
services). That rules some channels in and others out.

| Channel | Fit | Notes |
|---|---|---|
| **GitHub release + README** | ✅ best reach/effort | Your real storefront. Tag releases, keep the demo GIF fresh. |
| **AUR** (`packaging/aur/`) | ✅ clean | Arch ships ydotool 1.x and most Python deps; a PKGBUILD is natural here. |
| **`.deb`** (`packaging/debian/`) | ⚠️ workable | apt has no ydotool 1.x and no Whisper, so the postinst bootstraps them (needs network on first install). For a turnkey Ubuntu run, `install.sh` is still smoothest. |
| **Flatpak / Snap** | ❌ wrong tool | Sandboxes exist to *block* global keyboard + uinput access. Even with `--device=all` you still can't add the user to `input` or install the udev rule from inside the sandbox. Don't bother. |

## AUR

```bash
cd packaging/aur
# after the v1.0.0 git tag exists on GitHub:
updpkgsums            # pins sha256 of the release tarball
makepkg -si          # build + install locally to test
namcap PKGBUILD      # lint (optional)
```

Publish (needs an AUR account + SSH key on https://aur.archlinux.org):

```bash
git clone ssh://aur@aur.archlinux.org/typefree.git aur-typefree
cp packaging/aur/PKGBUILD packaging/aur/typefree.install aur-typefree/
cd aur-typefree
makepkg --printsrcinfo > .SRCINFO     # required by the AUR
git add PKGBUILD typefree.install .SRCINFO
git commit -m "typefree 1.0.0"
git push
```

Bump `pkgver` + re-run `updpkgsums` + regenerate `.SRCINFO` for each release.

## .deb

```bash
packaging/debian/build-deb.sh 1.0.0          # -> ./typefree_1.0.0_all.deb
sudo apt install ./typefree_1.0.0_all.deb    # apt resolves Depends; postinst bootstraps the rest
```

The first install builds ydotool 1.x and pip-installs openai-whisper, so it needs
network and `git`/`cmake`/`gcc` present. Attach the `.deb` to the GitHub release.

## Cutting a GitHub release

```bash
git tag -a v1.0.0 -m "Typefree 1.0.0"
git push origin v1.0.0
gh release create v1.0.0 --title "Typefree 1.0.0" --notes-file <(echo "...") \
  typefree_1.0.0_all.deb            # optionally attach the .deb
```

## Where to announce

A 20-second demo video does more than any store listing. Good homes for this
audience: r/linux, r/kde, r/Ubuntu, r/opensource, Hacker News (Show HN), and
accessibility / non-native-English-speaker communities (the accuracy + offline
angles land hardest there).
