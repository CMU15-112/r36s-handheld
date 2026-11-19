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

export SDL_GAMECONTROLLERCONFIG="$sdl_controllerconfig"
export PYGAME_HIDE_SUPPORT_PROMPT=1
export CI=1            # stops cmu_graphics' stdin console, which would quit the game
export CMU_FIT=1       # scale any app size to the 640x480 screen
export CMU_R36S_JOY=1  # R36S pad -> arcade box numbering; Select+Start quits

# Pin CPU / memory / GPU at their top clocks (dArkOS already allows 1.512 GHz).
# The menu's perfnorm restores power saving after the game exits.
$ESUDO sh -c 'echo performance > /sys/devices/system/cpu/cpufreq/policy0/scaling_governor
  for g in dmc_performance performance; do echo $g > /sys/devices/platform/dmc/devfreq/dmc/governor && break; done
  for g in simple_performance performance; do echo $g > /sys/devices/platform/ff400000.gpu/devfreq/ff400000.gpu/governor && break; done' 2>/dev/null

python3 "$GAME_MAIN" < /dev/null

$ESUDO sh -c 'printf "\033c" > /dev/tty0'
