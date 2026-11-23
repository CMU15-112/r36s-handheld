#!/bin/bash
# Shared launcher for cmu_graphics games on the R36S (dArkOS + PortMaster).
# "ports/My Game.sh" sources this file to run "ports/My Game/main.py".

XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
if [ -d "/opt/system/Tools/PortMaster/" ]; then
  controlfolder="/opt/system/Tools/PortMaster"
elif [ -d "/opt/tools/PortMaster/" ]; then
  controlfolder="/opt/tools/PortMaster"
elif [ -d "$XDG_DATA_HOME/PortMaster/" ]; then
  controlfolder="$XDG_DATA_HOME/PortMaster"
else
  controlfolder="/roms/ports/PortMaster"
fi
source $controlfolder/control.txt
source $controlfolder/device_info.txt
[ -f "${controlfolder}/mod_${CFW_NAME}.txt" ] && source "${controlfolder}/mod_${CFW_NAME}.txt"
get_controls

GAME_DIR=${GAME_DIR:-$(basename "$0" .sh)}
GAME_MAIN=${GAME_MAIN:-main.py}
GAMEDIR="/$directory/ports/$GAME_DIR"
cd "$GAMEDIR" || exit 1
> "$GAMEDIR/log.txt" && exec > >(tee "$GAMEDIR/log.txt") 2>&1
echo "cmu launcher: $GAME_DIR/$GAME_MAIN, $(cat "/$directory/ports/cmu_libs/VERSION" 2>/dev/null)"

# Games can't write to ROOTFS, so compile the libraries once or every launch recompiles them.
SITE=/usr/local/lib/python3.13/dist-packages
if [ ! -d "$SITE/cmu_graphics/__pycache__" ]; then
  echo "first launch: compiling cmu_graphics and the preinstalled packages"
  $ESUDO python3 -m compileall -q "$SITE"
fi

# Pin CPU / memory / GPU at their top clocks (dArkOS already allows 1.512 GHz).
# The menu's perfnorm restores power saving after the game exits.
$ESUDO sh -c 'echo performance > /sys/devices/system/cpu/cpufreq/policy0/scaling_governor
  for g in dmc_performance performance; do echo $g > /sys/devices/platform/dmc/devfreq/dmc/governor && break; done
  for g in simple_performance performance; do echo $g > /sys/devices/platform/ff400000.gpu/devfreq/ff400000.gpu/governor && break; done' 2>/dev/null

# cmu_graphics draws in a Wayland window, so run the game inside cage, a fullscreen kiosk
# compositor. setup.sh installs cage and its libraries in a private folder:
#   lib/     everything cage loads, only on cage's own library path
#   client/  the two of those the game needs (wayland-client and xkbcommon)
#   xkb/     keymaps, in case the console has none
# cage runs as root with libseat's noop backend: there's no seatd or logind session to ask
# for the screen. pixman renders in software, since the console's GPU driver isn't needed.
# CI=1 stops cmu_graphics' stdin console, which would quit the game. CMU_R36S_JOY=1 makes
# joystick.py renumber the R36S pad like the arcade box, with Select+Start to quit.
# CMU_GRAPHICS_SKIP_FRAMES=1 lets cmu_graphics skip frames it can't draw in time.
# CMU_GRAPHICS_DISABLE_MVC_CHECKER=1 turns off the MVC checker, which hashes all of app
# twice a frame. CMU_GRAPHICS_APPROX=1 turns on near-identical drawing speedups.
# PYTHONUNBUFFERED=1 keeps the last prints: cmu_graphics exits without flushing them into
# the log's pipe.
# cage exits when the game does.
CAGE=/usr/local/lib/r36s-cage
RUNTIME=/tmp/r36s-cage-runtime
$ESUDO mkdir -p -m 700 "$RUNTIME"
$ESUDO env \
  LD_LIBRARY_PATH="$CAGE/lib" \
  XDG_RUNTIME_DIR="$RUNTIME" \
  XKB_CONFIG_ROOT="$CAGE/xkb" \
  LIBSEAT_BACKEND=noop \
  WLR_RENDERER=pixman \
  WLR_LIBINPUT_NO_DEVICES=1 \
  CI=1 \
  "$CAGE/bin/cage" -d -- \
  env LD_LIBRARY_PATH="$CAGE/client" CMU_R36S_JOY=1 CMU_GRAPHICS_SKIP_FRAMES=1 CMU_GRAPHICS_DISABLE_MVC_CHECKER=1 CMU_GRAPHICS_APPROX=1 PYTHONUNBUFFERED=1 PYGAME_HIDE_SUPPORT_PROMPT=1 \
  python3 "$GAME_MAIN" < /dev/null

$ESUDO sh -c 'printf "\033c" > /dev/tty0'
