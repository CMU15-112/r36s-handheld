"""Joystick Tester: shows which R36S buttons and sticks are being used.

To use the joystick in your own game:
  1. Copy joystick.py into your game's folder.
  2. Add "import joystick" to your main file.
  3. Write the handlers you need, like the ones below.

Select + Start always quits, so you don't need to write code for that.
"""
from cmu_graphics import *
import joystick

# Buttons arrive as strings. This is what each string means on the R36S.
BUTTON_NAMES = {'0': 'B', '1': 'A', '2': 'X', '3': 'Y',
                '4': 'L1', '5': 'R1', '6': 'L2', '7': 'R2',
                '8': 'Select', '9': 'Start', '10': 'L3', '11': 'R3'}


def onAppStart(app):
    app.buttonsDown = set()
    app.directions = []
    app.axes = [0, 0, 0, 0]


# Called once when a button is pressed, like onKeyPress.
def onJoyPress(app, button, joystick):
    app.buttonsDown.add(button)


# Called once when a button is released, like onKeyRelease.
def onJoyRelease(app, button, joystick):
    app.buttonsDown.discard(button)


# Called every step while the D-pad or a stick is pushed, then once more with an
# empty list after it is let go. Each direction is an (axis, value) pair:
#   (0, -1) left    (0, 1) right    (1, -1) up    (1, 1) down
# The right stick works the same way, using axes 2 and 3.
def onDigitalJoyAxis(app, directions, joystick):
    app.directions = directions


# Called when a stick moves, for analog control. value goes from -1 to 1.
# The D-pad and left stick share axes 0 and 1. The right stick uses axes 2 and 3.
def onJoyAxis(app, value, axis, joystick):
    app.axes[axis] = value


def redrawAll(app):
    names = [BUTTON_NAMES[button] for button in app.buttonsDown]
    drawLabel('Joystick Tester', 320, 60, size=36, bold=True)
    drawLabel(f'Buttons: {names}', 320, 150, size=22)
    drawLabel(f'Directions: {app.directions}', 320, 210, size=22)
    drawLabel(f'Left stick: {app.axes[0]:.2f}, {app.axes[1]:.2f}', 320, 270, size=22)
    drawLabel(f'Right stick: {app.axes[2]:.2f}, {app.axes[3]:.2f}', 320, 330, size=22)
    drawLabel('Select + Start quits', 320, 430, size=18)


runApp(width=640, height=480)
