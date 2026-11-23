#!/usr/bin/env bash
# setup.sh: prepare an R36S SD card (dArkOS) for cmu_graphics games, from a Linux PC.
#
#   ROOTFS partition:    cmu_graphics, cmu_graphics_helpers, certifi, pygame (for joystick.py) and the WHEELS packages, in /usr/local/lib/python3.13/dist-packages
#                        the cage Wayland kiosk and its libraries (Debian trixie arm64), in /usr/local/lib/r36s-cage
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

# launch.sh needs PortMaster's control.txt. These are the places launch.sh looks, as seen
# from the PC (on dArkOS, /opt/system/Tools is EASYROMS/tools).
PM_FOUND=0
for pm in "$CARD/tools/PortMaster" "$ROOTFS/opt/system/Tools/PortMaster" "$ROOTFS/opt/tools/PortMaster" \
          "$ROOTFS/home/ark/.local/share/PortMaster" "$CARD/ports/PortMaster"; do
  if [ -f "$pm/control.txt" ]; then PM_FOUND=1; fi
done
[ "$PM_FOUND" = 1 ] || warn "PortMaster isn't installed, so games won't launch (they'll go straight back to the menu, with no log.txt).
         Install it on the console: Start → Options → Tools → Install PortMaster."

# The console's Python must match the cp313 wheels below (CPython 3.13, aarch64).
[ -x "$ROOTFS/usr/bin/python3.13" ] || die "console Python isn't 3.13 ($ROOTFS/usr/bin/python3.13 missing): these builds won't load"
# cmu_graphics_helpers links against these directly...
for lib in libasound.so.2 libfontconfig.so.1 libfreetype.so.6 libstdc++.so.6; do
  # -L too: these are symlinks to absolute paths that only resolve on the console
  [ -e "$ROOTFS/usr/lib/aarch64-linux-gnu/$lib" ] || [ -L "$ROOTFS/usr/lib/aarch64-linux-gnu/$lib" ] || warn "console is missing $lib; cmu_graphics_helpers won't load"
done
# Its window needs a Wayland compositor: the launcher runs each game inside cage, installed below.
# joystick.py reads the gamepad through pygame, which uses the console's SDL2.
for lib in libSDL2-2.0.so.0 libSDL2_ttf-2.0.so.0 libSDL2_image-2.0.so.0 libSDL2_mixer-2.0.so.0; do
  [ -e "$ROOTFS/usr/lib/aarch64-linux-gnu/$lib" ] || [ -L "$ROOTFS/usr/lib/aarch64-linux-gnu/$lib" ] || warn "console is missing $lib; pygame may not load"
done

# ---------------------------------------------------------------- downloads (pinned)
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/r36s-cmu"
mkdir -p "$CACHE"
# cmu_graphics and cmu_graphics_helpers ship prebuilt in wheels/ instead of coming from PyPI:
# cmu_graphics_helpers has no aarch64 wheel there, and cmu_graphics is a locally built 3.0.2.post1.
LOCAL_WHEELS=(
  "cmu_graphics-3.0.2.post1-py3-none-any.whl 1afe3d15d6580c059349bc2b71b48843401ee9f6fdeee444b3b8a9c5feb693ea"
  "cmu_graphics_helpers-0.1.11-cp38-abi3-linux_aarch64.whl a7c805d28947727f2285964dc419e94c479c20f357c812347941627bb8b0720b"
)
WHEEL_FILES=()
for wheel in "${LOCAL_WHEELS[@]}"; do
  whl=${wheel% *}
  [ -f "$HERE/wheels/$whl" ] || die "wheels/$whl is missing"
  echo "${wheel##* }  $HERE/wheels/$whl" | sha256sum -c --quiet - || die "wheels/$whl doesn't match its checksum"
  cp "$HERE/wheels/$whl" "$CACHE/$whl"
  WHEEL_FILES+=("$whl")
done

# pygame, which joystick.py uses to read the gamepad (cmu_graphics itself no longer needs it)
PYGAME_DEB=python3-pygame_2.6.1-1+b2_arm64.deb
say "downloading pygame 2.6.1 (Debian arm64)"
fetch "https://deb.debian.org/debian/pool/main/p/pygame/$PYGAME_DEB" \
  753af87964217b82c3d185b5712096c623140ac8957c47fc9910d669a9f7fb3e "$CACHE/$PYGAME_DEB" \
  https://snapshot.debian.org/file/0bb92d71b4806a618104db6de8e25c8f10ae30d4

# Packages students can import. PyPI's manylinux wheels bundle their own native libraries.
PYPI=https://files.pythonhosted.org/packages
WHEELS=(
  "$PYPI/32/d1/a3652fe2c19134d0d15922177821d863cbd7e842d53a21bbe7e8e3a1b5ac/certifi-2026.10.23-py3-none-any.whl e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1"
  "$PYPI/fb/3c/cd0770e96d20bfd990db024bcbd43897ff3e88da1f4782cf90ea231b1d4f/pillow-13.0.0-cp313-cp313-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl 19b66ca0a4d25cc0200b381ce824c7ea090db61427315e73cfb05dbf1b35c369"
  "$PYPI/57/06/de83b859944409bb9058bc6110581a3d7fc1f54d6df1222744911d7f6b19/numpy-2.5.5-cp313-cp313-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl ae2b8285d0b4eed24739c3ca9ec0fa424a0247489c01e1d5fe2815a599e11d33"
  "$PYPI/cc/d6/c1a1b3b721d434a1dff5c4c4f44f08365aba671961a8a68e9ff7262f763b/pymunk-7.3.1-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl 9f7c920eae0b8686c57060292fe0181419bbe396cc8d7e9036205dac207ce271"
  "$PYPI/37/6f/3b5ce4c3b2192d250f04908f2bfd91ef34552ec8f7716a5d4abdb8d67bb2/cffi-2.1.1-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.whl f16c709686a78c727bbbf059f92b0bf41c6fc60deec706d2dc19f529175a6125"
  "$PYPI/b6/ae/315bbeee126a769cbde43cf4908acb4b40408d8bf8bbf334dd7368d8dadf/pycparser-3.12-py3-none-any.whl a076f3f489b39b43ed13563a1dafe42447445bec2e294154ec5c57fcdda4c85e"
  "$PYPI/6d/35/8747bfef07ac48450f78d167e5c298ca31306c43699841b0b1e805f2fdd4/perlin_noise-1.14-py3-none-any.whl 3ecfae6437860c3e51d5f4bac0a7ceea1b9193a4a83c6d775edd5d47a918bc62"
  "$PYPI/9c/95/2964da9ffb3dc5ed0fad8a6e9ae93d8c344ce633efe6ef8de61e4fd8777a/pathfinding-1.0.23-py3-none-any.whl 06259dc0831c5e3b77b394c4f0073d79ae67542d89ad92a88881984a824df408"
  "$PYPI/35/e0/fd0a2d2b93599dec876158fe7fccb8c3e8096844a5a4110dd5bc67377128/PyTMX-3.32-py3-none-any.whl 4da4c01133dfcb2746cb4e7f46ea1aef21d56119ab044f8f77b9906dea5fbccb"
)
say "downloading certifi, Pillow, numpy, pymunk, perlin-noise, pathfinding and pytmx"
for wheel in "${WHEELS[@]}"; do
  url=${wheel% *}
  WHEEL_FILES+=("$(basename "$url")")
  fetch "$url" "${wheel##* }" "$CACHE/$(basename "$url")"
done

# cage, the Wayland kiosk that games run inside, and every library it loads apart from glibc,
# libgcc and libstdc++, found from the binaries' NEEDED entries. xkb-data gives it keymaps.
# All of it goes in a private folder, so nothing here can shadow the console's own libraries.
# Each entry: pool path, sha256, sha1 (for the snapshot.debian.org fallback).
CAGE_DEBS=(
  "pool/main/c/cage/cage_0.2.0-2_arm64.deb 842e8f2fd06c2e73b36804507eadf2faeab9e019a061c60825de6e3a88b3e279 b607a5b8b85c8c416381316a16c243b14a2f460e"
  "pool/main/w/wayland/libwayland-server0_1.23.1-3_arm64.deb 062702d57cf07b42ab7f0fa1aac8bc3f605eb47f2f0b9ecb1614674140d79f1b e6fda30118e1f4d1161a6f0e2317d800b7d86662"
  "pool/main/w/wlroots/libwlroots-0.18_0.18.2-3_arm64.deb 86384acb38bf7667639abe21756eced004c7bf23d132eb1d0033e84b7514c91f 08a4dcf9d9df1a50a0e0890a7872e4161b2576c1"
  "pool/main/libx/libxkbcommon/libxkbcommon0_1.7.0-2_arm64.deb 866888d3cfeb32388dd88a2615c4f78ebf8dd7b272821a83494cba260d8fa7d1 0898c5f0777a752ff8b32e906e81bb5a578245fb"
  "pool/main/libd/libdrm/libdrm2_2.4.124-2_arm64.deb b535506630bb6a9a616bc07caf3b29915f980d8c3437852fb96ea64fb1a8a3f8 2c296be5aea149cff0b8c1c13e3bf2e450e7b12f"
  "pool/main/p/pixman/libpixman-1-0_0.44.0-3_arm64.deb b502cd8b1ece222f42b44c42c33a300d84152c20089660167b9422ad2ebb6132 27ea3a1ea6b1e8a07714747bb0a984f1bf72e478"
  "pool/main/libg/libglvnd/libegl1_1.7.0-1+b2_arm64.deb b663ad1777df7ed8e320f897bbf678c2baef67f5458fe4a43195ac9b444c7afb 801011d23b7d320e987ad420a36a3110ef6d246b"
  "pool/main/m/mesa/libgbm1_25.0.7-2+deb13u1_arm64.deb 2beb8d20ca715a01b7b2352c1167c0d7097c9d8a486704a9a966f95d4077cdfc c43463a42f22adb74f6305bc6cb3de5dd3bbe3c8"
  "pool/main/libg/libglvnd/libgles2_1.7.0-1+b2_arm64.deb c727fa1746ed3e343d1a2917932363d71e961eacca0443a1e2296587e03c47fb 21642e1540296cd3c73416697b9b9363bbd54a76"
  "pool/main/v/vulkan-loader/libvulkan1_1.4.309.0-1_arm64.deb f5f576d8b9e5703702a9f3254c8c5d130bef17da9db4aecd46ba86cec7a08930 f0ea3605f43135591d4084fc5961541c3105f027"
  "pool/main/l/lcms2/liblcms2-2_2.16-2+deb13u2_arm64.deb 6636997fc488327a8d0951a038df6e5178a52b3f5e5cc2386d42655621f5672d 9f15dfc3e0cff9948bb6598f793cc42931095a46"
  "pool/main/s/systemd/libudev1_257.13-1~deb13u1_arm64.deb 459b1c9c17cc3c586e90d13d2a991715d498c6eed6c046cabf321021167bc380 8cd4ebb59c350a4214d6e45398cbe3b558940ab2"
  "pool/main/s/seatd/libseat1_0.9.1-1_arm64.deb 1e079a0d7f31220014abb24717d29e54f829d3c057a34647ca0ee9368922c63b 472f961994c6032027b3070e236146ab795176eb"
  "pool/main/libd/libdisplay-info/libdisplay-info2_0.2.0-2_arm64.deb 9719d01a5eaef520f41ee576af111c8f4c59246a8861178504d066b86d2ba7c9 8d95410fddef27eca62872ebb6841f403d217cfd"
  "pool/main/libl/libliftoff/libliftoff0_0.5.0-1.1_arm64.deb 80009cc94d8deacf419c23d53d6b6764d4a9ad118639bbfad8b695879dc50249 2f690d0626b9f8fa8ff58dc11a86bfcdcfe25803"
  "pool/main/libi/libinput/libinput10_1.28.1-1+deb13u1_arm64.deb a2acd04f3b1e9e91d22a26b63650087a179b6b3dbaddadc92d89edfbb2075ae1 8b01dc2e2ca115872068c5eb43d70811e9bba923"
  "pool/main/libx/libxcb/libxcb1_1.17.0-2+b1_arm64.deb d0178198e80ed4cacdececabe2c112ec88c7a9258cc11a55b8e267ab14a90d82 e966957e4699677442fcae8914b375b653164f02"
  "pool/main/libx/libxcb/libxcb-dri3-0_1.17.0-2+b1_arm64.deb 247bf758dd78d6c285fdd47942ddacf4a3189fd50de4f0891c9bd9aea12da3a1 99a8dbdaf1fb431de77368553e09352fda96dd72"
  "pool/main/libx/libxcb/libxcb-present0_1.17.0-2+b1_arm64.deb 73521d715554a2230901d1d480465d6358b3083a5ce8801639595ffde6f31441 08bccc8b89f3c419fa58f639999e4b9fbe88b3bb"
  "pool/main/libx/libxcb/libxcb-render0_1.17.0-2+b1_arm64.deb 7d6faf0afad0b27a047bbf320bf80999e94b7e6a0f73e42069e8b08958910466 676542e987e4de116e1088ea6472a26a3a5e5717"
  "pool/main/x/xcb-util-renderutil/libxcb-render-util0_0.3.10-1_arm64.deb f4a33bcc7780f404623a608823b48b91d9ac776a1396d56e9916cbfee49b182c 5dbd080c01abe920397342ebb3102ad2674f57fc"
  "pool/main/libx/libxcb/libxcb-shm0_1.17.0-2+b1_arm64.deb e9e871dc5d2f9265eb5d3028f0d0545cb6f8b748a68997b4c43f5a9416c7ead7 e1b950f41398e3d562a13ff456b19065c2581589"
  "pool/main/libx/libxcb/libxcb-xfixes0_1.17.0-2+b1_arm64.deb 19cdb8502827bc010993e69eaba09c670024976f7edd35b2b504bd646b141b0a baf74e4d3452b1dd4d0736160c01b9a5702ae2d5"
  "pool/main/libx/libxcb/libxcb-xinput0_1.17.0-2+b1_arm64.deb ebc39f2f1940a03963b0c1db916b19178e8078132adb669a27cfa8dd38c5d3c3 f0de1de30b9fdf954b8f2116d3ec14be656ec1da"
  "pool/main/w/wayland/libwayland-client0_1.23.1-3_arm64.deb 8abdc649e46470548c545f827e16f52e1bc16d05d4a3794e6dedb2d74ce58c9a a6f134475b0bbc6f028465bb80ec3cf0bdb20115"
  "pool/main/libx/libxcb/libxcb-composite0_1.17.0-2+b1_arm64.deb d37523c77b7d7786286dd466f876d6310c6c35e9eb12fcb36c70c4732118eb0e fc5a06a7a88cb4abf493a183fe8db7a1c46771f7"
  "pool/main/x/xcb-util-wm/libxcb-ewmh2_0.4.2-1_arm64.deb ccd6969a3bee33c4cfdf2302123936a8d9176e205e3b9badad386ef914e61a10 7e7fb1d55867687f1ce88cd0196009ec982a9606"
  "pool/main/x/xcb-util-wm/libxcb-icccm4_0.4.2-1_arm64.deb 4f9b048b98cd9cd7b0a553c8eff05bcd25cfc4fbfddbe4f83e9d6a9c82e2d876 89844aa6f81a5863d729f6bdebe4458151b9271c"
  "pool/main/libx/libxcb/libxcb-res0_1.17.0-2+b1_arm64.deb 941c1aff2e7ec05168746f80028952854198bfceb1ba69c404da1043b70b8ede 177da29b7a49f59305c89d1835d1baa15c2e8b68"
  "pool/main/x/xcb-util-errors/libxcb-errors0_1.0.1-4_arm64.deb 8b2830241364a6ad18f05e0f32cb0c3942b08cd666f2d5961d323a97237f67f9 7f25b67743b90a45a9adc1c8c8802742b3937f64"
  "pool/main/libx/libxau/libxau6_1.0.11-1_arm64.deb ac1061728670f4626adaa1288953a0e6fb801c9cae72ee1c3231e63e2609d23a 51c8766644614fccde2eca41745aa0a6ca1272c7"
  "pool/main/libx/libxdmcp/libxdmcp6_1.1.5-1_arm64.deb e10bbb0802181992ecf091e9425171850eee16068729c109214ba4924f81fb52 c9ad9b7de45a31b874b5d81f43a1788090a1371e"
  "pool/main/libf/libffi/libffi8_3.4.8-2_arm64.deb d84a783b818f2386627604e64b793eb4ac5bb9ea1ba321a194a1c9c82fe09a01 4c9a0dae1e87367e6794583e71d9bac024e71ac7"
  "pool/main/m/mtdev/libmtdev1t64_1.1.7-1_arm64.deb 3433f77a9f96d0dc537e870db734a4dbb50528836782c86709845736e8897b47 5aac1150cd3e30d7919e2faa780e621571dccb7b"
  "pool/main/libe/libevdev/libevdev2_1.13.4+dfsg-1_arm64.deb abdb9ca2f0a27d88ec16b480f346d1d6c183c59e3d071734ba8d8b9794aacfc6 0b706dec03c137f392a5f0c2d2f309dfe0f4e84d"
  "pool/main/libw/libwacom/libwacom9_2.14.0-1_arm64.deb 8fb8419c43fcc166f996c30cc262fcc86457bdd6f82656dad8619c88dc00b013 0b4567132635257493b0b1b7c44bffc4a9f6f045"
  "pool/main/libg/libgudev/libgudev-1.0-0_238-6_arm64.deb f0fec02f1bbc0803c9d044413609d3cd0fde24bfe6a5141a38aabfa86c292b21 b3e4a18d500d101b965f83f5dc7198e34e002e40"
  "pool/main/g/glib2.0/libglib2.0-0t64_2.84.4-3~deb13u5_arm64.deb d446f0ea85fb9dc411219ce861e59e5eabe1981c796918771a7d2aa97cc72481 f03f8c72728bd9bb77e96e90d7171e695ea079f1"
  "pool/main/g/gcc-14/libatomic1_14.2.0-19_arm64.deb 7ccff90234484bf21ae5ecb79d25b6a84b2f4038f65dd139085b2e3a937cd8dc 1c253948664b91d1351b0fe793a8528d75369561"
  "pool/main/p/pcre2/libpcre2-8-0_10.46-1~deb13u2_arm64.deb e7d2c997dac145c16457be0fed3d084c98cd030bec7633eec7e5bde6dbb97712 4c9e1f42f6c956a02e5e07ed7a3c9c4be25d8dbc"
  "pool/main/libc/libcap2/libcap2_2.75-10+deb13u1+b3_arm64.deb 863aa5b9cdf6f80010b48982d4639126b09a89eeef2c0c5c7be226297c811581 2f11f716354a7ab401a65d0779de2579965dca5d"
  "pool/main/s/systemd/libsystemd0_257.13-1~deb13u1_arm64.deb d2f63c408549c29eaefb6f4c948393edce99f45ee8e43c991391bef63fb7d54b 12182690ab7a5cafb5bfe829891b8e145e85910c"
  "pool/main/libg/libglvnd/libglvnd0_1.7.0-1+b2_arm64.deb 29ec7b8cf1c9a47055de643eb0a1921854e762b4789a7f3bc3b0dea498fd4da9 df7527527aafb6caec1274e5c50736a1a6cf2cde"
  "pool/main/e/expat/libexpat1_2.8.3-1~deb13u1_arm64.deb c3248c4beb721c8290e6e34527ae56b1fc965aa6a14982ca602df4458ef0854e 91f866fbf91fb293a12b0e97805c06aa1c4a1274"
  "pool/main/x/xkeyboard-config/xkb-data_2.42-1_all.deb 196ff18533382f64e057ea49df2bb486bd4275a4cc0917361edb560b8756dada 479c272cd4ccdd68bda778cc0bf2d3b45efc14d7"
)
say "downloading cage 0.2.0 and wlroots 0.18 (Debian trixie arm64)"
DEB_FILES=()
for deb in "${CAGE_DEBS[@]}"; do
  read -r path sha sha1 <<< "$deb"
  DEB_FILES+=("$(basename "$path")")
  fetch "https://deb.debian.org/debian/$path" "$sha" "$CACHE/$(basename "$path")" \
    "https://snapshot.debian.org/file/$sha1"
done

# ---------------------------------------------------------------- assemble the Python packages
STAGE="$CACHE/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/site" "$STAGE/cage" "$STAGE/ports/cmu_libs"
say "assembling packages"
python3 -I - "$CACHE" "$STAGE/site" "${WHEEL_FILES[@]}" <<'EOF'
import os, sys, zipfile
cache, out, *wheels = sys.argv[1:]

for wheel in wheels:
    with zipfile.ZipFile(os.path.join(cache, wheel)) as z:
        for n in z.namelist():
            top = n.split('/')[0]
            isNumpyTest = top == 'numpy' and '/tests/' in n
            if not n.endswith('/') and not top.endswith('.data') and not isNumpyTest:
                z.extract(n, out)
EOF

python3 -I - "$CACHE" "$STAGE/site" "$STAGE/cage" "$PYGAME_DEB" "${DEB_FILES[@]}" <<'EOF'
import io, os, sys, tarfile
cache, site, out, pygameDeb, *debs = sys.argv[1:]

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

# pygame/ from Debian's dist-packages
prefix = './usr/lib/python3/dist-packages/'
tf = debData(os.path.join(cache, pygameDeb))
for m in tf.getmembers():
    if m.name.startswith(prefix + 'pygame/') and m.isfile():  # skips symlinks into packages the console lacks
        dest = os.path.join(site, m.name[len(prefix):])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as fh:
            fh.write(tf.extractfile(m).read())

# bin/cage, lib/ (the libraries and their soname symlinks, all relative), xkb/ (keymaps)
wanted = {'./usr/bin/': 'bin/', './usr/lib/aarch64-linux-gnu/': 'lib/', './usr/share/X11/xkb/': 'xkb/'}
for deb in debs:
    tf = debData(os.path.join(cache, deb))
    for m in tf.getmembers():
        for src, dst in wanted.items():
            rel = m.name[len(src):]
            # lib/ takes only the top level: skips plugins like gbm/ and glib-2.0/
            if m.name.startswith(src) and rel and not (dst == 'lib/' and '/' in rel):
                if m.isdir() or not (m.isfile() or m.issym()):
                    continue
                if m.issym() and (m.linkname.startswith('/') or (dst == 'lib/' and '/' in m.linkname)):
                    continue
                m.name = dst + rel
                tf.extract(m, out, filter='data')

# The game itself only needs these two from the folder; the rest is for cage alone.
os.makedirs(os.path.join(out, 'client'))
for lib in ('libwayland-client.so.0', 'libxkbcommon.so.0'):
    assert os.path.exists(os.path.join(out, 'lib', lib)), lib
    os.symlink('../lib/' + lib, os.path.join(out, 'client', lib))
EOF

# pygame's default font is a symlink into a package the console lacks: ship a real file.
FONT="$ROOTFS/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
[ -f "$FONT" ] || FONT=$(fc-match -f '%{file}' 'DejaVu Sans:bold' 2>/dev/null || true)
if [ -f "$FONT" ]; then cp "$FONT" "$STAGE/site/pygame/freesansbold.ttf"
else warn "no DejaVu Sans Bold found: pygame.font.Font(None) won't work (joystick.py doesn't need it)"; fi

# ---------------------------------------------------------------- assemble the ports folder
cp "$HERE/payload/launch.sh" "$STAGE/ports/cmu_libs/"
echo "r36s-cmu $(date +%Y-%m-%d): cmu_graphics 3.0.2.post1, cmu_graphics_helpers 0.1.11, pygame 2.6.1, cage 0.2.0" > "$STAGE/ports/cmu_libs/VERSION"

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
CAGE=usr/local/lib/r36s-cage           # replaced whole

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
NEED_MB=$(( $(du -sm "$STAGE/site" | cut -f1) * 3 / 2 + $(du -sm "$STAGE/cage" | cut -f1) ))
FREE_MB=$(df -Pm "$ROOTFS" | awk 'NR == 2 {print $4}')
if [ -d "$ROOTFS/$CAGE" ]; then REPLACED+=("$ROOTFS/$CAGE"); fi
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

say "installing cage to ROOTFS: /$CAGE"
sudo mkdir -p "$ROOTFS/$CAGE"
sudo cp -a "$STAGE/cage/." "$ROOTFS/$CAGE/"
sudo chown -R 0:0 "$ROOTFS/$CAGE"
sudo chmod -R u=rwX,go=rX "$ROOTFS/$CAGE"

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
manifest "$STAGE/cage" > "$CACHE/cage.md5"
sync
CARD=$(remount "$CARD")
ROOTFS=$(remount "$ROOTFS")
(cd "$ROOTFS/$SITE" && md5sum -c --quiet "$CACHE/rootfs.md5") || die "files on ROOTFS don't match what was written"
(cd "$ROOTFS/$CAGE" && md5sum -c --quiet "$CACHE/cage.md5") || die "files on ROOTFS don't match what was written"
(cd "$CARD/ports" && md5sum -c --quiet "$CACHE/ports.md5") || die "files on EASYROMS don't match what was written"
say "verified $(cat "$CACHE/rootfs.md5" "$CACHE/cage.md5" "$CACHE/ports.md5" | wc -l) files on the card"

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
