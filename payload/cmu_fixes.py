"""Fixes for bugs in stock cmu_graphics 3.0.2. setup.sh installs this as
cmu_graphics/cmu_fixes.py and imports it at the end of cmu_graphics/__init__.py,
so it applies whether or not fastpath is on.

1. CMUImage and the MVC checker. CMUImage (shape_logic.PILWrapper) converts its PIL
   image the first time it's drawn and caches the result on itself, in
   self._imageParams. The MVC checker hashes everything reachable from app, so an
   image kept in app (app.sprite = CMUImage(...)) and first drawn in redrawAll looks
   like redrawAll changed app: "You may not change the app state (the model) in
   redrawAll (the view)". Keep the converted image outside the object instead, keyed
   weakly so it goes away with the CMUImage.
"""

import weakref

from cmu_graphics import shape_logic as _sl

_imageParams = weakref.WeakKeyDictionary()  # CMUImage -> converted image


def _getParams(self):
    params = _imageParams.get(self)
    if params is None:
        params = _imageParams[self] = _sl.wyvernImageFromPilImage(self.image)
    return params


_sl.PILWrapper.params = property(_getParams, None)
