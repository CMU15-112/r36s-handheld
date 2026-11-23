# cmu_graphics on the R36S

Prepares an R36S SD card running dArkOS to run CMU 15-112 `cmu_graphics` games.

**Students:** see [STUDENT_GUIDE.md](STUDENT_GUIDE.md).

## Setting up a card

### Before running anything from this repo

These steps follow the
[dArkOSRE-R36 firmware installation guide](https://github.com/southoz/dArkOS-G80CA-RE/wiki/Firmware-Installation).
Check it for anything that has changed since this was written.

1. **Download the image.** Get the latest release from
   [dArkOSRE-R36 releases](https://github.com/southoz/dArkOSRE-R36/releases). It comes as a
   `.7z` archive, such as `dArkOSRE_R36_trixie_03082026.7z`. Extract the `.img` with 7-Zip
   (`7z x <file>.7z`). The release page gives a SHA1 hash, so you can check the image with
   `sha1sum`.
2. **Flash the image**. Use Raspberry Pi Imager, Rufus, USB Image
   Tool or `dd`. Don't use Balena Etcher, which often produces failed flashes on this device.
3. **Install the DTB files for the console.** The console won't boot until the BOOT
   partition has the DTB files for its motherboard and panel. Our consoles use
   **R36S-V22 2024-12-18**.
   * On Linux or macOS, delete every `.dtb` file from the root of the BOOT partition. Then
     copy the files from `dtb/r36s/R36S-V22 2024-12-18/` to the root of BOOT.
   * On Windows, double-click `SELECT MODEL` on the BOOT partition, enter the number for
     `R36S-V22 2024-12-18`, and press `Y` to confirm.

   Other consoles need different files. Find the console's motherboard ID in the
   [supported systems table](https://github.com/southoz/dArkOSRE-R36#supported-systems).
4. **First boot.** Insert the
   card and power on. The first boot takes about 2 to 3 minutes and ends in
   EmulationStation.
5. **Install PortMaster.** Go to **Start → Options → Tools → Install PortMaster**.
6. **Shut the console down fully**, then take the card out.

### Running setup.sh

On a Linux PC, plug in the SD card and run:

```bash
./setup.sh --eject
```

The script uses `sudo` to write to ROOTFS, so it may ask for your password. It's safe to
re-run on a card that's already set up.

| Partition | Installed |
|---|---|
| ROOTFS | cmu_graphics 3.0.2.post1, cmu_graphics_helpers 0.1.11, certifi, pygame 2.6.1 (read by `joystick.py` for the gamepad), Pillow 13.0.0, numpy 2.5.5, pymunk 7.3.1, perlin-noise 1.14, pathfinding 1.0.23 and pytmx 3.32, in `/usr/local/lib/python3.13/dist-packages`. The cage 0.2.0 Wayland compositor with its libraries and keymaps (from Debian trixie arm64), in `/usr/local/lib/r36s-cage` |
| EASYROMS | The shared launcher in `ports/cmu_libs`, plus the demo games in `ports` |

To keep the menus simple, the script also removes:

* The PICO-8 carts (`EASYROMS/pico-8`), the ScummVM menu entry (`scummvm/menu.scummvm`) and
  every `Scan_for_new_games.*` entry
* Every empty folder on EASYROMS, such as unused ROM folders, except inside `PortMaster`
* The Kodi and RetroArch menus, by deleting their entries from
  `/etc/emulationstation/es_systems.cfg` on ROOTFS. The first run saves the original as
  `es_systems.cfg.r36s-cmu.bak`. RetroArch itself stays installed, because the emulators
  use it.

cmu_graphics (3.0.2.post1) and cmu_graphics_helpers (which has no aarch64 wheel on PyPI)
ship prebuilt in [wheels/](wheels/) and are checksum-verified. Every other download is
pinned, checksum-verified and cached in `~/.cache/r36s-cmu`. After
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
turns on frame skipping (`CMU_GRAPHICS_SKIP_FRAMES=1`) and near-identical drawing speedups
(`CMU_GRAPHICS_APPROX=1`), and turns off the MVC checker
(`CMU_GRAPHICS_DISABLE_MVC_CHECKER=1`) to save frame time, pins the clocks at their maximum, and sends the game's output to `log.txt` in its folder.

cmu_graphics 3 opens a Wayland window, and dArkOS has no desktop, so the launcher runs
each game inside [cage](https://github.com/cage-kiosk/cage), a compositor that shows one
fullscreen app and exits when it does. cage runs as root, without a login session, and
draws in software (`WLR_RENDERER=pixman`), so it doesn't depend on the console's GPU
driver. Its libraries stay in `/usr/local/lib/r36s-cage/lib` and are only on cage's own
library path, so they can't replace the console's. The game gets just the two it needs,
libwayland-client and libxkbcommon, from `r36s-cage/client`.

cmu_graphics 3 doesn't use pygame, so `joystick.py` reads the gamepad through pygame
itself, on each step, with SDL's dummy display (no window). The launcher sets
`CMU_R36S_JOY=1`, which makes it renumber the R36S buttons like the CMU arcade box and quit
on Select+Start.

The first launch on a new card compiles the libraries, because games can't write to ROOTFS
at runtime.

## Files

```
setup.sh              prepares the card
STUDENT_GUIDE.md      how students get a game running on the console
payload/launch.sh     shared launcher, installed to ports/cmu_libs
payload/game.sh       menu entry for one game
wheels/               prebuilt cmu_graphics and aarch64 cmu_graphics_helpers wheels
demo_games/           Joystick Tester, CMU Swarm and CMU FPS Demo, installed on every card
```
