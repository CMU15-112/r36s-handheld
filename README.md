# cmu_graphics on the R36S

Prepares an R36S SD card running dArkOS to run CMU 15-112 `cmu_graphics` games.

**Students:** see [STUDENT_GUIDE.md](STUDENT_GUIDE.md).

## Setting up a card

On a Linux PC, plug in the SD card and run:

```bash
./setup.sh --eject
```

The script uses `sudo` to write to ROOTFS, so it may ask for your password. It's safe to
re-run on a card that's already set up.

| Partition | Installed |
|---|---|
| ROOTFS | pygame 2.6.1, pycairo 1.27.0, cmu_graphics 1.1.44 with speed patches, Pillow 13.0.0, numpy 2.5.5, pymunk 7.3.1, perlin-noise 1.14, pathfinding 1.0.23 and pytmx 3.32, in `/usr/local/lib/python3.13/dist-packages` |
| EASYROMS | The shared launcher in `ports/cmu_libs`, plus the demo games in `ports` |

To keep the menus simple, the script also removes:

* The PICO-8 carts (`EASYROMS/pico-8`), the ScummVM menu entry (`scummvm/menu.scummvm`) and
  every `Scan_for_new_games.*` entry
* Every empty folder on EASYROMS, such as unused ROM folders, except inside `PortMaster`
* The Kodi and RetroArch menus, by deleting their entries from
  `/etc/emulationstation/es_systems.cfg` on ROOTFS. The first run saves the original as
  `es_systems.cfg.r36s-cmu.bak`. RetroArch itself stays installed, because the emulators
  use it.

Downloads are pinned, checksum-verified and cached in `~/.cache/r36s-cmu`. After
installing, the script remounts both partitions and compares every file against what it
wrote.

Shut the console down fully before removing or inserting the card. If the card is swapped
while the console is asleep, the console can later write a stale folder listing back to
the card, and newly copied files disappear.

## How games launch

A game is a folder and a menu entry with the same name:

```
EASYROMS/ports/My Game/main.py
EASYROMS/ports/My Game.sh
```

Every menu entry is a copy of [payload/game.sh](payload/game.sh), which runs
`ports/cmu_libs/launch.sh`. The launcher finds the game folder from the entry's filename,
turns on the screen scaling and gamepad translation, pins the clocks at their maximum, and
sends the game's output to `log.txt` in its folder.

The first launch on a new card compiles the libraries, because games can't write to ROOTFS
at runtime.

## Files

```
setup.sh              prepares the card
STUDENT_GUIDE.md      how students get a game running on the console
payload/fastpath.py   speed patches, screen scaling and gamepad translation for cmu_graphics
payload/launch.sh     shared launcher, installed to ports/cmu_libs
payload/game.sh       menu entry for one game
demo_games/           Joystick Tester, CMU Swarm and CMU FPS Demo, installed on every card
```
