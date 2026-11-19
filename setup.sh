#!/usr/bin/env bash
# setup.sh: prepare an R36S SD card (dArkOS) for cmu_graphics games, from a Linux PC.
#
#   ROOTFS partition:    cmu_graphics, pygame, pycairo and the WHEELS packages, in /usr/local/lib/python3.13/dist-packages
#   EASYROMS partition:  ports/cmu_libs (the shared launcher) and the demo games; empty folders are removed
#   Also removed, to keep the menus simple: Kodi and RetroArch, PICO-8 carts, ScummVM and "Scan for new games" entries
#
# Safe to re-run. Writing to ROOTFS uses sudo, so it may ask for your password.
#
#   ./setup.sh [--card /path/to/EASYROMS] [--rootfs /path/to/ROOTFS] [--eject]
#
#   --card, --rootfs   where the partitions are mounted (default: found by label)
#   --eject            unmount and power off the card at the end
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

CARD="" ROOTFS="" EJECT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --card) CARD=$2; shift 2 ;;
    --rootfs) ROOTFS=$2; shift 2 ;;
    --eject) EJECT=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done

for c in curl python3 sha256sum md5sum findmnt lsblk sudo; do
  command -v "$c" >/dev/null 2>&1 || die "'$c' is required but not installed"
done

# mount_by_label LABEL -> where that partition is mounted, mounting it if needed
mount_by_label() {
  local dev target
  dev=$(lsblk -rpno NAME,LABEL 2>/dev/null | awk -v l="$1" '$2 == l {print $1; exit}')
  [ -n "$dev" ] || return 0
  target=$(findmnt -rn -o TARGET -S "$dev" 2>/dev/null | head -1 || true)
  if [ -z "$target" ] && command -v udisksctl >/dev/null; then
    udisksctl mount -b "$dev" >/dev/null 2>&1 || true
    target=$(findmnt -rn -o TARGET -S "$dev" 2>/dev/null | head -1 || true)
  fi
  echo "$target"
}

# remount DIR -> DIR's new path, so verifying reads the card and not the page cache
remount() {
  local dev
  if ! mountpoint -q "$1" || ! command -v udisksctl >/dev/null; then
    warn "can't remount $1: verifying from the page cache, not the card itself"
    echo "$1"; return 0
  fi
  dev=$(findmnt -n -o SOURCE --target "$1")
  udisksctl unmount -b "$dev" >/dev/null && udisksctl mount -b "$dev" >/dev/null || die "could not remount $dev"
  findmnt -rn -o TARGET -S "$dev" | head -1
}

# fetch URL SHA256 OUT [FALLBACK_URL...]  (cached; checksum always verified)
fetch() {
  local url=$1 sha=$2 out=$3; shift 3
  if [ -f "$out" ] && echo "$sha  $out" | sha256sum -c --quiet - 2>/dev/null; then return 0; fi
  for u in "$url" "$@"; do
    if curl -fsSL --retry 3 -o "$out.part" "$u" && echo "$sha  $out.part" | sha256sum -c --quiet - 2>/dev/null; then
      mv "$out.part" "$out"; return 0
    fi
    warn "download failed or checksum mismatch: $u"
  done
  rm -f "$out.part"
  die "could not download $(basename "$out")"
}

# manifest DIR -> md5 list of the files under DIR, with relative paths
manifest() {
  (cd "$1" && find . -type f -print0 | sort -z | xargs -0 md5sum)
}

# ---------------------------------------------------------------- find the card
[ -n "$CARD" ] || CARD=$(mount_by_label EASYROMS)
[ -n "$ROOTFS" ] || ROOTFS=$(mount_by_label ROOTFS)
[ -n "$CARD" ] || die "EASYROMS partition not found. Plug in the card or pass --card /path/to/EASYROMS"
[ -n "$ROOTFS" ] || die "ROOTFS partition not found. Plug in the card or pass --rootfs /path/to/ROOTFS"
[ -d "$CARD/ports" ] || die "$CARD has no ports/ folder: is this the R36S EASYROMS partition?"
say "EASYROMS: $CARD"
say "ROOTFS:   $ROOTFS"

# The console's Python must match the pygame/pycairo builds (CPython 3.13, aarch64).
[ -x "$ROOTFS/usr/bin/python3.13" ] || die "console Python isn't 3.13 ($ROOTFS/usr/bin/python3.13 missing): these builds won't load"
for lib in libSDL2-2.0.so.0 libcairo.so.2 libSDL2_ttf-2.0.so.0 libSDL2_image-2.0.so.0 libSDL2_mixer-2.0.so.0; do
  # -L too: these are symlinks to absolute paths that only resolve on the console
  [ -e "$ROOTFS/usr/lib/aarch64-linux-gnu/$lib" ] || [ -L "$ROOTFS/usr/lib/aarch64-linux-gnu/$lib" ] || warn "console is missing $lib; pygame may not load"
done

# ---------------------------------------------------------------- downloads (pinned)
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/r36s-cmu"
mkdir -p "$CACHE"
PYGAME_DEB=python3-pygame_2.6.1-1+b2_arm64.deb
CAIRO_DEB=python3-cairo_1.27.0-2_arm64.deb
CMU_WHL=cmu_graphics-1.1.44-py3-none-any.whl
say "downloading pygame 2.6.1, pycairo 1.27.0 (Debian arm64) and cmu_graphics 1.1.44"
fetch "https://deb.debian.org/debian/pool/main/p/pygame/$PYGAME_DEB" \
  753af87964217b82c3d185b5712096c623140ac8957c47fc9910d669a9f7fb3e "$CACHE/$PYGAME_DEB" \
  https://snapshot.debian.org/file/0bb92d71b4806a618104db6de8e25c8f10ae30d4
fetch "https://deb.debian.org/debian/pool/main/p/pycairo/$CAIRO_DEB" \
  bbcec3d07448914ea81df1d2c74b25a54e3707f0452a3326f93a47a0ed7e05a5 "$CACHE/$CAIRO_DEB" \
  https://snapshot.debian.org/file/3b6258ba50f6d0a7465c739fd62c326146f53c88
fetch "https://files.pythonhosted.org/packages/63/bc/843f49fa7325b49e2f16fc73a59fbd3035b5cf16dc3b122ad98ef1803584/$CMU_WHL" \
  53e089a1a3a6da6b9a6cff7eab3ef3cb418ce6379644eae9f9ae1f8e39c22879 "$CACHE/$CMU_WHL"

# Packages students can import. PyPI's manylinux wheels bundle their own native libraries.
PYPI=https://files.pythonhosted.org/packages
WHEELS=(
  "$PYPI/fb/3c/cd0770e96d20bfd990db024bcbd43897ff3e88da1f4782cf90ea231b1d4f/pillow-13.0.0-cp313-cp313-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl 19b66ca0a4d25cc0200b381ce824c7ea090db61427315e73cfb05dbf1b35c369"
  "$PYPI/57/06/de83b859944409bb9058bc6110581a3d7fc1f54d6df1222744911d7f6b19/numpy-2.5.5-cp313-cp313-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl ae2b8285d0b4eed24739c3ca9ec0fa424a0247489c01e1d5fe2815a599e11d33"
  "$PYPI/cc/d6/c1a1b3b721d434a1dff5c4c4f44f08365aba671961a8a68e9ff7262f763b/pymunk-7.3.1-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl 9f7c920eae0b8686c57060292fe0181419bbe396cc8d7e9036205dac207ce271"
  "$PYPI/37/6f/3b5ce4c3b2192d250f04908f2bfd91ef34552ec8f7716a5d4abdb8d67bb2/cffi-2.1.1-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.whl f16c709686a78c727bbbf059f92b0bf41c6fc60deec706d2dc19f529175a6125"
  "$PYPI/b6/ae/315bbeee126a769cbde43cf4908acb4b40408d8bf8bbf334dd7368d8dadf/pycparser-3.12-py3-none-any.whl a076f3f489b39b43ed13563a1dafe42447445bec2e294154ec5c57fcdda4c85e"
  "$PYPI/6d/35/8747bfef07ac48450f78d167e5c298ca31306c43699841b0b1e805f2fdd4/perlin_noise-1.14-py3-none-any.whl 3ecfae6437860c3e51d5f4bac0a7ceea1b9193a4a83c6d775edd5d47a918bc62"
  "$PYPI/9c/95/2964da9ffb3dc5ed0fad8a6e9ae93d8c344ce633efe6ef8de61e4fd8777a/pathfinding-1.0.23-py3-none-any.whl 06259dc0831c5e3b77b394c4f0073d79ae67542d89ad92a88881984a824df408"
  "$PYPI/35/e0/fd0a2d2b93599dec876158fe7fccb8c3e8096844a5a4110dd5bc67377128/PyTMX-3.32-py3-none-any.whl 4da4c01133dfcb2746cb4e7f46ea1aef21d56119ab044f8f77b9906dea5fbccb"
)
say "downloading Pillow, numpy, pymunk, perlin-noise, pathfinding and pytmx"
WHEEL_FILES=()
for wheel in "${WHEELS[@]}"; do
  url=${wheel% *}
  WHEEL_FILES+=("$(basename "$url")")
  fetch "$url" "${wheel##* }" "$CACHE/$(basename "$url")"
done

# ---------------------------------------------------------------- assemble the Python packages
STAGE="$CACHE/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/site" "$STAGE/ports/cmu_libs"
say "assembling packages"
python3 -I - "$CACHE" "$STAGE/site" "$PYGAME_DEB" "$CAIRO_DEB" "$CMU_WHL" "${WHEEL_FILES[@]}" <<'EOF'
import io, os, sys, tarfile, zipfile
cache, out, pygameDeb, cairoDeb, cmuWhl, *wheels = sys.argv[1:]

def debData(path):
    """data.tar.* member of a .deb (an ar archive)."""
    with open(path, 'rb') as f:
        assert f.read(8) == b'!<arch>\n', path
        while True:
            hdr = f.read(60)
            if len(hdr) < 60:
                raise SystemExit(f'no data.tar in {path}')
            name = hdr[:16].decode().strip().rstrip('/')
            size = int(hdr[48:58])
            body = f.read(size)
            if size % 2:
                f.read(1)
            if name.startswith('data.tar'):
                return tarfile.open(fileobj=io.BytesIO(body))

prefix = './usr/lib/python3/dist-packages/'
for deb, pkg in ((pygameDeb, 'pygame'), (cairoDeb, 'cairo')):
    tf = debData(os.path.join(cache, deb))
    for m in tf.getmembers():
        if not m.name.startswith(prefix + pkg + '/'):
            continue
        rel = m.name[len(prefix):]
        dest = os.path.join(out, rel)
        if m.isdir():
            os.makedirs(dest, exist_ok=True)
        elif m.isfile():  # skips symlinks, which point at packages the console lacks
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, 'wb') as fh:
                fh.write(tf.extractfile(m).read())

with zipfile.ZipFile(os.path.join(cache, cmuWhl)) as z:
    for n in z.namelist():
        if n.startswith('cmu_graphics/') and not n.endswith('/'):
            z.extract(n, out)

for wheel in wheels:
    with zipfile.ZipFile(os.path.join(cache, wheel)) as z:
        for n in z.namelist():
            top = n.split('/')[0]
            isNumpyTest = top == 'numpy' and '/tests/' in n
            if not n.endswith('/') and not top.endswith('.data') and not isNumpyTest:
                z.extract(n, out)
EOF

# pygame's default font is a symlink into a package the console lacks: ship a real file.
FONT="$ROOTFS/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
[ -f "$FONT" ] || FONT=$(fc-match -f '%{file}' 'DejaVu Sans:bold' 2>/dev/null || true)
if [ -f "$FONT" ]; then cp "$FONT" "$STAGE/site/pygame/freesansbold.ttf"
else warn "no DejaVu Sans Bold found: pygame.font.Font(None) won't work (cmu_graphics doesn't need it)"; fi

cp "$HERE/payload/fastpath.py" "$STAGE/site/cmu_graphics/fastpath.py"
cat >> "$STAGE/site/cmu_graphics/__init__.py" <<'EOF'

# Speed patches for slow handhelds (see fastpath.py). CMU_FAST=0 disables them.
if os.environ.get('CMU_FAST', '1') != '0':
    from cmu_graphics import fastpath as _fastpath

    _fastpath.install(globals())
EOF

# ---------------------------------------------------------------- assemble the ports folder
cp "$HERE/payload/launch.sh" "$STAGE/ports/cmu_libs/"
echo "r36s-cmu $(date +%Y-%m-%d): cmu_graphics 1.1.44 + fastpath, pygame 2.6.1, pycairo 1.27.0" > "$STAGE/ports/cmu_libs/VERSION"

# A game is a folder plus a menu entry with the same name.
DEMOS=("joystick_tester:Joystick Tester" "cmu_swarm:CMU Swarm" "cmu_fps_demo:CMU FPS Demo")
for demo in "${DEMOS[@]}"; do
  cp -r "$HERE/demo_games/${demo%%:*}" "$STAGE/ports/${demo#*:}"
  cp "$HERE/payload/game.sh" "$STAGE/ports/${demo#*:}.sh"
done
find "$STAGE/ports" -name __pycache__ -type d -prune -exec rm -rf {} +

# ---------------------------------------------------------------- install
SITE=usr/local/lib/python3.13/dist-packages
DEST="${ROOTFS:?}/$SITE"
RECORD="$DEST/.r36s-cmu-installed"  # what the last run installed, so only that gets replaced

mapfile -t NEW < <(ls -A "$STAGE/site")
OLD=()
if [ -f "$RECORD" ]; then
  while IFS= read -r name; do
    case "$name" in ""|.|..|*/*) ;; *) OLD+=("$name") ;; esac
  done < "$RECORD"
fi
REPLACED=()
for name in "${OLD[@]}" "${NEW[@]}"; do
  if [ -e "$DEST/$name" ]; then REPLACED+=("$DEST/$name"); fi
done

# Leave room for the compiled files the first launch adds.
NEED_MB=$(( $(du -sm "$STAGE/site" | cut -f1) * 3 / 2 ))
FREE_MB=$(df -Pm "$ROOTFS" | awk 'NR == 2 {print $4}')
if [ "${#REPLACED[@]}" -gt 0 ]; then
  FREE_MB=$(( FREE_MB + $(du -smc "${REPLACED[@]}" | tail -1 | cut -f1) ))
fi
[ "$FREE_MB" -ge "$NEED_MB" ] || die "ROOTFS needs ${NEED_MB} MB free but has ${FREE_MB} MB: nothing was changed"

say "installing packages to ROOTFS: /$SITE"
sudo mkdir -p "$DEST"
if [ "${#REPLACED[@]}" -gt 0 ]; then sudo rm -rf "${REPLACED[@]}"; fi
printf '%s\n' "${NEW[@]}" | sudo tee "$RECORD" >/dev/null
sudo chmod 644 "$RECORD"
sudo cp -r "$STAGE/site/." "$DEST/"
for name in "${NEW[@]}"; do
  sudo chown -R 0:0 "$DEST/$name"
  sudo chmod -R u=rwX,go=rX "$DEST/$name"
done

# Hide the Kodi and RetroArch menus. RetroArch itself stays: the emulators use it.
ESCFG="$ROOTFS/etc/emulationstation/es_systems.cfg"
if [ -f "$ESCFG" ]; then
  DROPPED=$(python3 -I - "$ESCFG" "$STAGE/es_systems.cfg" kodi retroarch <<'EOF'
import re, sys, xml.etree.ElementTree as ET
src, out, *drop = sys.argv[1:]
with open(src, encoding='utf-8', newline='') as f:
    text = f.read()

def keep(m):
    name = re.search(r'<name>\s*(.*?)\s*</name>', m.group(0), re.S)
    return '' if name and name.group(1) in drop else m.group(0)

new = re.sub(r'[ \t]*<system>.*?</system>[ \t]*\r?\n?', keep, text, flags=re.S)
try:
    ET.fromstring(text.encode())
except ET.ParseError:
    pass  # not strict XML to begin with: nothing to compare against
else:
    ET.fromstring(new.encode())  # the edit must not break a valid file
with open(out, 'w', encoding='utf-8', newline='') as f:
    f.write(new)
print(text.count('<system>') - new.count('<system>'))
EOF
  )
  if [ "$DROPPED" -gt 0 ]; then
    [ -e "$ESCFG.r36s-cmu.bak" ] || sudo cp -p "$ESCFG" "$ESCFG.r36s-cmu.bak"
    sudo tee "$ESCFG" < "$STAGE/es_systems.cfg" >/dev/null  # tee keeps the file's owner and mode
    say "removed Kodi and RetroArch from the main menu (original saved as es_systems.cfg.r36s-cmu.bak)"
  fi
else
  warn "no es_systems.cfg on ROOTFS: Kodi and RetroArch menus left as they are"
fi

say "installing launcher and demo games to EASYROMS: ports/"
# cmu_libs is replaced whole; the other names are left over from older versions of this setup
rm -rf "$CARD/ports/cmu_libs" "$CARD/ports/cmu_swarm" "$CARD/ports/cmu_fps_demo" \
       "$CARD/ports/CMU Graphics Test.sh" "$CARD/ports/cmutest"
for demo in "${DEMOS[@]}"; do rm -rf "$CARD/ports/${demo#*:}"; done
cp -r "$STAGE/ports/." "$CARD/ports/"

# Bundled PICO-8 carts, the ScummVM menu entry and "Scan for new games" entries just confuse students.
rm -rf "$CARD/pico-8" "$CARD/scummvm/menu.scummvm"
SCANS=$(find "$CARD" -mindepth 2 -maxdepth 2 -type f -iname 'Scan_for_new_*' -print -delete | wc -l)
say "removed the PICO-8 carts, the ScummVM menu entry and $SCANS \"Scan for new games\" entries"

# Unused ROM folders just confuse students. PortMaster's own folders are left alone.
REMOVED=$(find "$CARD" -mindepth 1 -type d -empty -not -path '*/PortMaster/*' -print -delete | wc -l) \
  || warn "could not remove every empty folder from EASYROMS"
say "removed ${REMOVED:-0} empty folders from EASYROMS"

# ---------------------------------------------------------------- verify
say "verifying the copy"
manifest "$STAGE/site" > "$CACHE/rootfs.md5"
manifest "$STAGE/ports" > "$CACHE/ports.md5"
sync
CARD=$(remount "$CARD")
ROOTFS=$(remount "$ROOTFS")
(cd "$ROOTFS/$SITE" && md5sum -c --quiet "$CACHE/rootfs.md5") || die "files on ROOTFS don't match what was written"
(cd "$CARD/ports" && md5sum -c --quiet "$CACHE/ports.md5") || die "files on EASYROMS don't match what was written"
say "verified $(cat "$CACHE/rootfs.md5" "$CACHE/ports.md5" | wc -l) files on the card"

say "done: $(cat "$CARD/ports/cmu_libs/VERSION")"
if [ "$EJECT" = 1 ]; then
  sync
  dev=$(findmnt -n -o SOURCE --target "$CARD")
  disk=/dev/$(lsblk -no PKNAME "$dev")
  for p in $(lsblk -lnpo NAME "$disk" | tail -n +2); do
    if findmnt -rn -S "$p" >/dev/null; then udisksctl unmount -b "$p" >/dev/null; fi
  done
  udisksctl power-off -b "$disk" >/dev/null 2>&1 || true
  say "card ejected: safe to remove. Insert it into the R36S while the console is OFF."
else
  say "card left mounted: eject it from your file manager, or re-run with --eject"
fi
