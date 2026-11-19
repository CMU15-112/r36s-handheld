# Running your cmu_graphics game on the R36S

Your R36S is already set up with `cmu_graphics`. To run your game on it, you need to
do three things:

1. Set your game's size to 640×480.
2. Add gamepad controls with `joystick.py`.
3. Copy your game onto the SD card.

## 1. Use a 640×480 canvas

The R36S screen is **640×480**, so end your main file with:

```python
runApp(width=640, height=480)
```

Other sizes still run. The console scales your game to fit and keeps its shape, so a
4:3 size like 800×600 fills the screen, and any other shape gets black bars. Your code
keeps using the size you chose.

The screen is small (3.5 inches diagonally), so keep text at **size 16 or larger** and
check that it's readable on the console.

## 2. Add gamepad controls

The console has no keyboard or mouse, so `onKeyPress`, `onMousePress` and the other
keyboard and mouse handlers never run there.

Copy [`joystick.py`](demo_games/joystick_tester/joystick.py) into your game's folder,
next to `main.py`, and import it in `main.py`:

```python
from cmu_graphics import *
import joystick

def onAppStart(app):
    app.cx = 320
    app.cy = 240
    app.color = 'crimson'

# Runs once each time a button is pressed
def onJoyPress(app, button, joystick):
    if button == '1':  # the A button
        app.color = 'gold' if app.color == 'crimson' else 'crimson'

# Runs on every step while the D-pad or left stick is pushed
def onDigitalJoyAxis(app, directions, joystick):
    if (0, -1) in directions: app.cx -= 5  # left
    if (0, 1) in directions:  app.cx += 5  # right
    if (1, -1) in directions: app.cy -= 5  # up
    if (1, 1) in directions:  app.cy += 5  # down

def redrawAll(app):
    drawCircle(app.cx, app.cy, 30, fill=app.color)

runApp(width=640, height=480)
```

Your handlers must be in `main.py`, the same file that calls `runApp`. If you use
screens, add the screen name as usual, like `game_onJoyPress`.

### Handlers

Write only the ones you need. Ignore the `joystick` parameter, which says which
controller was used. The console has only one.

| Handler | When it runs |
|---|---|
| `onJoyPress(app, button, joystick)` | Once when a button is pressed |
| `onJoyRelease(app, button, joystick)` | Once when a button is released |
| `onJoyButtonHold(app, buttons, joystick)` | Every step while any button is held. `buttons` is a list. |
| `onDigitalJoyAxis(app, directions, joystick)` | Every step while a direction is held, then once more with an empty list after release |
| `onJoyAxis(app, value, axis, joystick)` | Whenever a stick moves. `value` goes from -1 to 1. |

`onJoyAxis` doesn't run while a stick is held still. For analog movement, save `value`
in `app` and use it in `onStep`.

### Buttons

Buttons are **strings**, so compare with `'1'` and not `1`.

| R36S button | `button` |
|---|---|
| B (bottom) | `'0'` |
| A (right) | `'1'` |
| X (top) | `'2'` |
| Y (left) | `'3'` |
| L1 / R1 | `'4'` / `'5'` |
| L2 / R2 | `'6'` / `'7'` |
| Select / Start | `'8'` / `'9'` |
| L3 / R3 (press a stick in) | `'10'` / `'11'` |

### Directions

Each direction in `directions` is an `(axis, value)` tuple.

| Control | Left | Right | Up | Down |
|---|---|---|---|---|
| D-pad or left stick | `(0, -1)` | `(0, 1)` | `(1, -1)` | `(1, 1)` |
| Right stick | `(2, -1)` | `(2, 1)` | `(3, -1)` | `(3, 1)` |

Diagonals show up as two tuples at once.

**Pressing Select + Start together quits any game**, so you don't need to write a quit
button. Your game still receives whichever of the two was pressed first, so don't use
Select or Start alone for anything destructive, like erasing a save.

The **Joystick Tester** game on the console shows the name of every button you press
and the values from both sticks.

### Testing on your laptop

`joystick.py` runs fine on a laptop with no controller attached, but your gamepad code
won't get any input there. To keep testing on your laptop, leave your keyboard handlers
in place alongside the joystick ones. The console ignores them.

## 3. Copy your game to the SD card

1. Shut the console down fully with **Start → Quit → Shutdown**, then take out the SD
   card and plug it into your computer.
2. Open the **EASYROMS** drive, then its `ports` folder.
3. Copy your game folder into `ports`, and name it what you want to see in the menu.
   Your main file must be named `main.py`. Don't copy a `cmu_graphics` folder.
4. Make a copy of `Joystick Tester.sh`, also in `ports`, and rename it to match your
   folder exactly. You don't need to edit it.
5. Eject the card safely, put it back in the console **while the console is off**, and
   turn the console on.

Your game is now listed under **Ports**. For a game called `My Game`, you should have:

```
EASYROMS/ports/
├── My Game.sh
└── My Game/
    ├── main.py
    ├── joystick.py
    └── images, sounds and other files
```

The first game you launch on a new card can take a few minutes to start, because the
console is preparing `cmu_graphics` and its packages. This only happens once.

To update your game later, shut the console down and replace your folder on the card.

## Preinstalled packages

Python's standard library and these packages are ready to import on the console. To test
on your laptop, install them there with `pip`.

| Package | Import | Good for |
|---|---|---|
| Pillow | `from PIL import Image` | Loading, cropping and editing images, like sprite sheets. Wrap the result in `CMUImage` to draw it. |
| numpy | `import numpy` | Fast math on large grids and arrays |
| pymunk | `import pymunk` | 2D physics, like collisions, gravity and joints |
| perlin-noise | `from perlin_noise import PerlinNoise` | Natural-looking random terrain and maps |
| pathfinding | `import pathfinding` | Finding paths on a grid, like enemies chasing a player |
| pytmx | `import pytmx` | Loading maps made in the Tiled editor |

Importing numpy makes your game start more slowly, so only import it if you use it.

## Keeping your game fast

The console runs Python about 12 times slower than a laptop, so a game that's smooth on
your laptop may not be smooth here.

* Draw backgrounds and other things that don't change **first** in `redrawAll`, in the
  same order and with the same arguments every time. The console reuses that part of the
  frame instead of redrawing it.
* Avoid `gradient(...)` fills on many shapes.
* Avoid changing an image's `width` or `height` every frame. Each new size has to be
  resized once before it can be reused.
* Keep `onStep` light, and watch out for nested loops over many objects.
* Keep memory use well under 400 MB. The console has 1 GB in total.

## If something goes wrong

Whenever your game runs, its output and errors are saved to `log.txt` in your game's
folder on the card. **Check this file first.** Your `print` output is there too.

| Problem | What to check |
|---|---|
| The game isn't in the Ports menu | The `.sh` file must be directly in `ports`. Restart the console. |
| The screen goes back to the menu right away | Read `log.txt`. Also check that the folder and `.sh` names match exactly, and that your file is named `main.py`. |
| `No module named joystick` | Copy `joystick.py` next to `main.py`. |
| Buttons do nothing | Add `import joystick` to `main.py`, compare buttons to strings, and remove any `cmu_graphics` folder from your game folder. |
| An image or sound won't load | Filenames must match exactly, including capitals. Use paths relative to `main.py`. |
| The game is frozen | Try Select + Start. If that fails, hold the power button to restart the console. |

Some features don't work on the console:

* `app.getTextInput()` and `app.showMessage()` open desktop windows, so they can't run.
* Only the [preinstalled packages](#preinstalled-packages) are on the console. Any other
  package written entirely in Python usually works if you copy its folder next to `main.py`.
* Only one long music track (over 1 MB) can play at a time.
