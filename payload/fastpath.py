"""Speed patches for cmu_graphics on slow handhelds (R36S, RK3326).

Usage: put this file next to your game and import it:

    from cmu_graphics import *
    import fastpath

(Importing it before or after cmu_graphics both work.) CMU_FAST=0 in the
environment turns the patches off without editing the game.

Ported to cmu_graphics 3.x. 3.x no longer uses pygame: wyvern (cmu_graphics_helpers,
built on winit + softbuffer + tiny-skia + rodio) owns the window, the event loop,
input and sound, and calls App.on_event(event, surface) with 'step', 'redraw',
mouse/key and 'resize' events. Differences from 2.x this file works around:
  - colours are plain RGBA (2.x swapped them to BGRA);
  - there is no pixel read-back (no surface.data): the frame and rendered text are
    read back through Canvas.save_png (to /dev/shm when available), and only when
    it pays off (static layer captures; labels drawn many times);
  - the window surface keeps its pixels between 'redraw' events, so a frame where
    nothing changed is simply not redrawn;
  - the surface may be HiDPI: device pixels per app unit = surface.width / window
    width (from the 'resize' events);
  - no gamepad support at all (no pygame events), so CMU_R36S_JOY is gone;
  - sound is decoded on the fly by rodio, so the 2.x sound patch is gone.

Every patch keeps the
original behaviour; anything the fast paths don't handle falls back to the
original code, so error messages for bad arguments stay the same.

1. redrawAll runs once per rendered frame instead of after every event handler
   (stock code reruns it after onStep, onKeyHold, onKeyPress... and throws all but
   the last result away). Frames where nothing changed are skipped.
2. drawRect/Oval/Circle/Line/Polygon/RegularPolygon/Star/Arc/Label/Image with
   ordinary arguments (including rotateAngle, dashes, opacity, align) skip the
   validated shape-object machinery and draw straight to the canvas.
3. Translation lookups, colours, rgb() objects, text measurements, rendered text
   (once drawn often enough) and resized images are cached. So is each built shape,
   by its exact arguments: a call identical to a recent one (tile map, star field,
   HUD) skips argument checks and geometry. Fonts are selected only when they change.
4. Shapes are drawn the moment drawX() is called (no shape list), and an unchanged
   start of the frame (background, tile map...) is pasted from a cached bitmap
   instead of being redrawn (StaticPrefix; CMU_STATIC_LAYER=0 disables it). The
   layer times itself and stops while pasting is slower than just drawing.

Opt-in via environment:
  CMU_FIT=1   open fullscreen and scale the app to fill the screen keeping its aspect
              ratio (800x600 arcade games -> 640x480), rendered at screen resolution.
              The app keeps its own width/height; mouse positions are mapped back.
  CMU_MVC_CHECK=0|N   the MVC checker (which hashes all of `app` before and after every
              redrawAll; ~20% of frame time in a small app, more in big ones) runs on
              every frame by default. 0 turns it off; N checks one frame in N, so a
              redrawAll that changes `app` is still reported, just a little later (or
              missed if it only happens on unchecked frames). A game that sets
              app.disableMvcChecker = True itself is never checked.
              Caveat with the checker off: fastpath runs redrawAll once per *rendered*
              frame (not after every event like stock, and not at all for unchanged
              frames), so a redrawAll that changes `app` misbehaves differently than in
              stock.
  CMU_APPROX=1  speedups whose output is very close to, but not pixel-identical with,
              the stock drawing:
                - labels whose text keeps changing are drawn from cached images of
                  their characters (tiny-skia anti-aliases a lone glyph slightly
                  differently from the same glyph in a string; up to ~10/255);
                - rotated images are drawn from copies pre-rotated to the nearest 2
                  degrees, placed on whole pixels (rotating while drawing costs ~10x
                  a plain copy).

Measuring: fastbench.py renders a game's frames off-screen (no window, so it runs
over ssh on the handheld) and reports ms per frame; --save/--diff check that a change
keeps the output pixel-identical. On the handheld itself, also check the CPU
governor (cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor): `performance`
keeps the clock up for a game loop that ondemand/schedutil may underclock.
"""

import gc
import math
import os
import struct
import sys
import tempfile
import time
import zlib

import cmu_graphics as _pkg
from cmu_graphics import shape_logic as sl
from cmu_graphics import cmu_graphics as cg
from cmu_graphics.deps import wyvern

# CMU_APPROX=1: speedups whose output is close to, but not exactly, the stock drawing.
APPROX = os.environ.get('CMU_APPROX') == '1'

TWO_PI = 2 * math.pi
DEG = math.pi / 180
FAIL = object()  # "can't fast-path this call, use the original"


# ---------------------------------------------------------------- caches

_origToEnglish = sl.toEnglish
_teCache = {}


def toEnglish(key, context, returnLanguage=False):
    # type(key) keeps False/0 and True/1 apart (equal hashes)
    k = (type(key), key, context, returnLanguage, sl.cmuGraphicsLanguage)
    try:
        return _teCache[k]
    except KeyError:
        pass
    except TypeError:  # unhashable key
        return _origToEnglish(key, context, returnLanguage)
    if len(_teCache) > 20000:
        _teCache.clear()
    r = _teCache[k] = _origToEnglish(key, context, returnLanguage)
    return r


_origT = sl.t
_tCache = {}


def t(key, variables=None, language=None):
    if variables is not None or language is not None:
        return _origT(key, variables, language)
    k = (type(key), key, sl.cmuGraphicsLanguage)
    try:
        return _tCache[k]
    except KeyError:
        pass
    except TypeError:
        return _origT(key)
    r = _tCache[k] = _origT(key)
    return r


_rgbCache = {}


def rgb(r, g, b):
    """rgb() returning shared objects (RGB is immutable and compares by value)."""
    k = (type(r), r, type(g), g, type(b), b)
    try:
        c = _rgbCache.get(k)
    except TypeError:
        return sl.RGB(r, g, b)
    if c is None:
        if len(_rgbCache) > 50000:
            _rgbCache.clear()
        c = _rgbCache[k] = sl.RGB(r, g, b)
    return c


_colorCache = {}


def color(c, alpha):
    """Canvas source tuple (r, g, b, a) for a colour, None for no colour, FAIL to fall back."""
    if c is None:
        return None
    tc = type(c)
    if tc is str:
        rgb3 = _colorCache.get(c)
        if rgb3 is None:
            v = sl.CSS3_COLORS_TO_RGB.get(toEnglish(c, 'color').lower())
            if v is None:
                return FAIL
            d = v.__dict__
            rgb3 = _colorCache[c] = (d['_red'] / 255, d['_green'] / 255, d['_blue'] / 255)
        return rgb3 + (alpha,)
    if tc is sl.RGB:
        d = c.__dict__
        r, g, b = d['_red'], d['_green'], d['_blue']
        if not (_num(r) and _num(g) and _num(b)):
            return FAIL
        return (r / 255, g / 255, b / 255, alpha)
    return FAIL  # gradients etc.


_extentsCache = {}
_glyphCache = {}
_measureCtx = {}  # face -> canvas with that font selected (select_font_face is slow)


def measureCtx(face):
    mc = _measureCtx.get(face)
    if mc is None:
        mc = _measureCtx[face] = wyvern.ImageSurface(1, 1).canvas
        mc.select_font_face(*face)
    return mc


def glyphExtents(face, size, ch):
    """(x bearing, y bearing, width, height, x advance) of one character."""
    k = (face, size, ch)
    e = _glyphCache.get(k)
    if e is None:
        mc = measureCtx(face)
        if len(_glyphCache) > 20000:
            _glyphCache.clear()
        mc.set_font_size(size)
        xb, yb, w, h, xa, ya = mc.text_extents(ch)
        e = _glyphCache[k] = (xb, yb, w, h, xa)
    return e


def textExtents(face, size, text):
    k = (face, size, text)
    e = _extentsCache.get(k)
    if e is None:
        if len(_extentsCache) > 5000:
            _extentsCache.clear()
        mc = measureCtx(face)
        mc.set_font_size(size)
        xb, yb, w, h, xa, ya = mc.text_extents(text)
        e = _extentsCache[k] = (xb, yb, w, h, xa)
    return e


_fontCache = {}


def fontFace(font, bold, italic):
    k = (font, bold, italic)
    f = _fontCache.get(k)
    if f is None:
        f = _fontCache[k] = sl.getFont(font, bold, italic)
    return f


# ---------------------------------------------------------------- pixel read-back

_SHM = '/dev/shm'
_SNAP_DIR = _SHM if os.path.isdir(_SHM) and os.access(_SHM, os.W_OK) else tempfile.gettempdir()
_SNAP_PATH = os.path.join(_SNAP_DIR, 'cmu_fastpath_%d.png' % os.getpid())


def snapshot(ctx):
    """The canvas' pixels as a WyvernImage (device pixels), via a PNG round trip."""
    ctx.save_png(_SNAP_PATH)
    try:
        return wyvern.load_image_from_path(_SNAP_PATH)
    finally:
        _unlink()


def _unlink():
    try:
        os.remove(_SNAP_PATH)
    except OSError:
        pass


_PIL_BPP = {'L': 1, 'LA': 2, 'RGB': 3, 'RGBA': 4}


def readPixels(ctx):
    """(width, height, bytes per pixel, pixel bytes) of the canvas."""
    ctx.save_png(_SNAP_PATH)
    try:
        try:
            from PIL import Image  # much faster than decodePng, when it's installed
        except ImportError:
            Image = None
        if Image is not None:
            with Image.open(_SNAP_PATH) as im:
                if im.mode in _PIL_BPP:
                    im.load()
                    return im.width, im.height, _PIL_BPP[im.mode], im.tobytes()
        with open(_SNAP_PATH, 'rb') as f:
            return decodePng(f.read())
    finally:
        _unlink()


def decodePng(data):
    """Minimal decoder for the 8-bit, non-interlaced PNGs save_png writes."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    pos, idat, w, h, bpp = 8, [], 0, 0, 4
    while pos < len(data):
        n, typ = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if typ == b'IHDR':
            w, h, depth, ctype, _, _, interlace = struct.unpack('>IIBBBBB', body)
            if depth != 8 or interlace or ctype not in (0, 2, 4, 6):
                raise ValueError('unsupported PNG')
            bpp = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
        elif typ == b'IDAT':
            idat.append(body)
        elif typ == b'IEND':
            break
    raw = zlib.decompress(b''.join(idat))
    stride = w * bpp
    out = bytearray(h * stride)
    prev = bytes(stride)
    for y in range(h):
        start = y * (stride + 1)
        f = raw[start]
        line = bytearray(raw[start + 1:start + 1 + stride])
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, bpp, out


# ---------------------------------------------------------------- argument checks


def _num(v):
    tv = type(v)
    return tv is int or tv is float


_ALIGNS = None


def alignAttrs(align):
    global _ALIGNS
    if _ALIGNS is None:
        _ALIGNS = set(sl.TRANSLATED_ALIGNS['keys'])
    if type(align) is not str:
        return FAIL
    a = toEnglish(align, 'align')
    if a not in _ALIGNS:
        return FAIL
    return sl.getAlignAttrs(a)


def dashPattern(d):
    if d is False:
        return None
    if d is True:
        return (5, 5)
    if type(d) in (list, tuple) and d and all(_num(v) and v >= 0 for v in d) and any(d):
        return tuple(d)
    return FAIL


_SHAPE_KW = frozenset(['fill', 'border', 'borderWidth', 'opacity', 'rotateAngle', 'dashes', 'align', 'visible', 'db'])
_POLY_KW = _SHAPE_KW - {'align'}
_STAR_KW = _SHAPE_KW | {'roundness'}
_LINE_KW = frozenset(['fill', 'opacity', 'rotateAngle', 'dashes', 'visible', 'db', 'lineWidth', 'arrowStart', 'arrowEnd'])
_LABEL_KW = frozenset(['fill', 'border', 'borderWidth', 'opacity', 'rotateAngle', 'align', 'visible', 'db', 'bold', 'italic', 'size', 'font'])
_IMAGE_KW = _SHAPE_KW | {'width', 'height'}


_styleCache = {}


def style(kw, allowed, defaultAlign):
    """Common keyword handling, memoized on the exact keywords, value types and values.
    Returns (fill, border, borderWidth, alignAttrs, dash, rotateAngle) or FAIL."""
    rot = kw.get('rotateAngle', 0)
    if rot and type(rot) is not bool:
        # Usually animated: keep it out of the cache key, or every call would miss.
        if not _num(rot):
            return FAIL
        kw = dict(kw)
        del kw['rotateAngle']
        s = style(kw, allowed, defaultAlign)
        if s is FAIL or 'rotateAngle' not in allowed:
            return FAIL
        return s[:5] + (rot,)
    try:
        k = (allowed, defaultAlign, tuple(kw.items()), tuple(map(type, kw.values())))
        s = _styleCache.get(k)
    except TypeError:  # unhashable value, e.g. a dashes list
        return parseStyle(kw, allowed, defaultAlign)
    if s is None:
        if len(_styleCache) > 20000:
            _styleCache.clear()
        s = _styleCache[k] = parseStyle(kw, allowed, defaultAlign)
    return s


def parseStyle(kw, allowed, defaultAlign):
    if not kw.keys() <= allowed:
        return FAIL
    get = kw.get
    if get('visible', True) is not True or get('db', '') != '':
        return FAIL
    if get('arrowStart', False) is not False or get('arrowEnd', False) is not False:
        return FAIL
    rot = get('rotateAngle', 0)
    if not _num(rot):
        return FAIL
    dash = dashPattern(get('dashes', False))
    if dash is FAIL:
        return FAIL
    opacity = get('opacity', 100)
    if not _num(opacity) or not 0 <= opacity <= 100:
        return FAIL
    alpha = opacity / 100
    fill = color(get('fill', 'black'), alpha)
    border = color(get('border', None), alpha)
    if fill is FAIL or border is FAIL:
        return FAIL
    bw = get('borderWidth', 2)
    if not _num(bw) or bw < 0:
        return FAIL
    if defaultAlign:
        aa = alignAttrs(get('align', defaultAlign))
        if aa is FAIL:
            return FAIL
    else:
        aa = None
    return fill, border, bw, aa, dash, rot


def alignLeftTop(aa, x, y, w, h):
    xa, ya = aa
    left = x if xa == 'left' else (x - w if xa == 'right' else x - w / 2)
    top = y if ya == 'top' else (y - h if ya == 'bottom' else y - h / 2)
    return left, top


# ---------------------------------------------------------------- drawing


class FastShape(object):
    """A fast-path draw call: a function and its pre-computed arguments."""

    __slots__ = ('fn', 'args')

    def __init__(self, fn, args):
        self.fn = fn
        self.args = args


def toDevice(ctx):
    """Switch ctx from app units to device pixels (S.dev maps app -> device)."""
    d, ox, oy = S.dev
    if d != 1 or ox or oy:
        ctx.transform(1 / d, 0, 0, 1 / d, -ox / d, -oy / d)


_bezCache = {}


def unitBezier(start, sweep):
    """shape_logic's own Bezier approximation of a unit circle arc (90-degree
    fragments, counter-clockwise from east), so edges match the stock drawing."""
    k = (start, sweep)
    bp = _bezCache.get(k)
    if bp is None:
        if len(_bezCache) > 5000:
            _bezCache.clear()
        bp = _bezCache[k] = [tuple(p) for p in sl.Oval.getBezierPoints(start, sweep, True)]
    return bp


def _curves(ctx, bp, cx, cy, rx, ry):
    for i in range(0, len(bp), 4):
        (x1, y1), (x2, y2), (x3, y3) = bp[i + 1], bp[i + 2], bp[i + 3]
        ctx.curve_to(cx + rx * x1, cy + ry * y1, cx + rx * x2, cy + ry * y2, cx + rx * x3, cy + ry * y3)


def _ellipsePath(ctx, cx, cy, rx, ry):
    # wyvern applies the transform at fill time, so build the ellipse directly.
    bp = unitBezier(0, 360)
    ctx.new_path()
    ctx.move_to(cx + rx * bp[0][0], cy + ry * bp[0][1])
    _curves(ctx, bp, cx, cy, rx, ry)
    ctx.close_path()


def rotateAbout(ctx, deg, ax, ay):
    ctx.translate(ax, ay)
    ctx.rotate(deg * DEG)
    ctx.translate(-ax, -ay)


def paintPath(ctx, fill, border, bw, dash, roundJoin=False):
    """Fill the current path, then draw an inner border (2x width clipped to the
    shape), the way shape_logic does for polygons and ovals."""
    if fill is not None:
        ctx.set_source_rgba(*fill)
        ctx.fill_preserve()
    if border is not None and bw:
        ctx.clip_preserve()
        ctx.set_source_rgba(*border)
        if dash:
            ctx.set_dash(dash)
        if roundJoin:
            ctx.set_line_join(wyvern.LineJoin.ROUND)
        ctx.set_line_width(bw * 2)
        ctx.stroke()
    ctx.new_path()


def strokeInset(ctx, border, bw, dash):
    """Stroke the current (already inset by bw/2) path at bw: the same pixels as
    shape_logic's 2*bw stroke clipped to the shape, without a costly clip mask."""
    ctx.set_source_rgba(*border)
    if dash:
        ctx.set_dash(dash)
    ctx.set_line_width(bw)
    ctx.stroke()


def drawRectFast(ctx, x, y, w, h, fill, border, bw, dash, rot):
    if not rot:
        if border is None or not bw:
            if fill is not None:
                ctx.new_path()
                ctx.rectangle(x, y, w, h)
                ctx.set_source_rgba(*fill)
                ctx.fill()
            return
        if w > bw and h > bw and not dash:  # dashed: clip path keeps the dash phase
            ctx.save()
            ctx.new_path()
            if fill is not None:
                ctx.rectangle(x, y, w, h)
                ctx.set_source_rgba(*fill)
                ctx.fill()
            ctx.rectangle(x + bw / 2, y + bw / 2, w - bw, h - bw)
            strokeInset(ctx, border, bw, dash)
            ctx.restore()
            return
    ctx.save()
    if rot:
        rotateAbout(ctx, rot, x + w / 2, y + h / 2)
    ctx.new_path()
    ctx.rectangle(x, y, w, h)
    paintPath(ctx, fill, border, bw, dash)
    ctx.restore()


def drawOvalFast(ctx, cx, cy, rx, ry, fill, border, bw, dash, rot):
    if rx == ry:
        if border is None or not bw:
            if fill is not None:
                _ellipsePath(ctx, cx, cy, rx, rx)
                ctx.set_source_rgba(*fill)
                ctx.fill()
            return
        if rx > bw / 2 and not dash:
            ctx.save()
            if fill is not None:
                _ellipsePath(ctx, cx, cy, rx, rx)
                ctx.set_source_rgba(*fill)
                ctx.fill()
            _ellipsePath(ctx, cx, cy, rx - bw / 2, rx - bw / 2)
            strokeInset(ctx, border, bw, dash)
            ctx.restore()
            return
    ctx.save()
    if rot:
        rotateAbout(ctx, rot, cx, cy)
    _ellipsePath(ctx, cx, cy, rx, ry)
    paintPath(ctx, fill, border, bw, dash)
    ctx.restore()


def _polyPath(ctx, pts):
    # Same path as utils.makePolygonPath: start at the last point.
    ctx.move_to(pts[-2], pts[-1])
    for i in range(0, len(pts), 2):
        ctx.line_to(pts[i], pts[i + 1])
    ctx.close_path()


def drawPolyFast(ctx, pts, fill, border, bw, dash):
    ctx.save()
    ctx.new_path()
    _polyPath(ctx, pts)
    paintPath(ctx, fill if len(pts) > 4 else None, border, bw, dash)
    ctx.restore()


def drawInsetPolyFast(ctx, pts, inner, fill, border, bw, dash):
    ctx.save()
    ctx.new_path()
    if fill is not None:
        _polyPath(ctx, pts)
        ctx.set_source_rgba(*fill)
        ctx.fill()
    _polyPath(ctx, inner)
    strokeInset(ctx, border, bw, dash)
    ctx.restore()


def drawArcFast(ctx, cx, cy, rx, ry, start, sweep, fill, border, bw, dash, rot):
    ctx.save()
    if rot:
        rotateAbout(ctx, rot, cx, cy)
    ctx.new_path()
    # wyvern's arc() won't join to the centre, so build the pie from shape_logic's curves
    bp = unitBezier(start, sweep)
    ctx.move_to(cx, cy)
    ctx.line_to(cx + rx * bp[0][0], cy + ry * bp[0][1])
    _curves(ctx, bp, cx, cy, rx, ry)
    ctx.close_path()
    paintPath(ctx, fill, border, bw, dash, roundJoin=True)
    ctx.restore()


def drawLineFast(ctx, x1, y1, x2, y2, fill, lw, dash, rot):
    ctx.save()
    if rot:
        rotateAbout(ctx, rot, (x1 + x2) / 2, (y1 + y2) / 2)
    ctx.new_path()
    ctx.set_source_rgba(*fill)
    if dash:
        ctx.set_dash(dash)
    ctx.set_line_width(lw)
    ctx.move_to(x1, y1)
    ctx.line_to(x2, y2)
    ctx.stroke()
    ctx.restore()


def _quarter(v):
    i = math.floor(v)
    f = round((v - i) * 4) / 4
    if f == 1:
        return i + 1, 0.0
    return i, f


# Reading text back costs ~25 drawings of it (a PNG round trip), so a label is only
# turned into a cached image once it has been drawn this many times unchanged.
TEXT_CACHE_AFTER = 30
_textUses = {}
_maskCache = {}
_INVERT = bytes(255 - i for i in range(256))


def textMask(face, size, text, fx, fy):
    """Coverage of the text (one byte per pixel), rendered once at this size and
    sub-pixel offset. wyvern surfaces start opaque white and have no operators, so
    the text is drawn black on white and the coverage is 255 - the red channel."""
    k = (face, size, text, fx, fy)
    m = _maskCache.get(k)
    if m is None:
        if len(_maskCache) > 3000:
            _maskCache.clear()
        xb, yb, w, h, xa = textExtents(face, size, text)
        pad = 2
        ox = pad - math.floor(xb)
        oy = pad - math.floor(yb)
        mw = int(math.ceil(w)) + 2 * pad + 2
        mh = int(math.ceil(h)) + 2 * pad + 2
        c = wyvern.ImageSurface(mw, mh).canvas
        c.select_font_face(*face)
        c.set_font_size(size)
        c.set_source_rgba(0, 0, 0, 1)
        c.new_path()
        c.move_to(ox + fx, oy + fy)
        c.text_path(text)  # same outlines as shape_logic's labels
        c.fill()
        pw, ph, bpp, px = readPixels(c)
        if (pw, ph) != (mw, mh):
            raise ValueError('unexpected read-back size')
        m = _maskCache[k] = (bytes(px[0::bpp]).translate(_INVERT), mw, mh, ox, oy)
    return m


_alphaTables = {}
_textCache = {}


def _scaleTable(c):
    """Byte table v -> v * c for a channel value c in 0..1 (premultiplying)."""
    ic = int(round(c * 255))
    tbl = _alphaTables.get(ic)
    if tbl is None:
        tbl = _alphaTables[ic] = bytes((v * ic + 127) // 255 for v in range(256))
    return tbl


def textImage(face, size, text, fx, fy, fill):
    """The text as a WyvernImage in its fill colour, opacity baked into the alpha.
    WyvernImage pixels are premultiplied RGBA, so each colour channel is
    coverage * colour."""
    k = (face, size, text, fx, fy, fill)
    hit = _textCache.get(k)
    if hit is not None:
        return hit
    if len(_textCache) > 3000:
        _textCache.clear()
    cov, mw, mh, ox, oy = textMask(face, size, text, fx, fy)
    r, g, b, a = fill
    alpha = cov.translate(_scaleTable(a)) if a < 1 else cov
    buf = bytearray(mw * mh * 4)
    buf[0::4] = alpha.translate(_scaleTable(r))
    buf[1::4] = alpha.translate(_scaleTable(g))
    buf[2::4] = alpha.translate(_scaleTable(b))
    buf[3::4] = alpha
    hit = _textCache[k] = (wyvern.WyvernImage(buf, mw, mh, mw * 4), ox, oy)
    return hit


def textBox(face, size, text):
    """(ink width, x adjustment) as shape_logic measures a label."""
    xb, yb, w, h, xa = textExtents(face, size, text)
    if len(text) > 0 and (text[0] == ' ' or text[-1] == ' '):
        return max(w, xa), 0
    return w, xb


def stampText(ctx, text, x, y, face, size, fill, ax, xattr):
    """Draw the label from a cached image in device pixels (no resampling when the
    app is scaled). Returns False if the text isn't worth caching (yet)."""
    d, ox, oy = S.dev
    dsize = size * d
    if d != 1:
        # Glyphs are hinted at the device size, so the text's width isn't exactly
        # d times its app-size width: align it at the device size.
        dw, dadj = textBox(face, dsize, text)
        anchor = ax * d + ox
        left = anchor if xattr == 'left' else (anchor - dw if xattr == 'right' else anchor - dw / 2)
        ix, fx = _quarter(left - dadj)
    else:
        ix, fx = _quarter(x + ox)
    iy, fy = _quarter(y * d + oy)
    k = (face, dsize, text, fx, fy, fill)
    if k not in _textCache:
        n = _textUses.get(k, 0) + 1
        if n < TEXT_CACHE_AFTER:
            if len(_textUses) > 20000:
                _textUses.clear()
            _textUses[k] = n
            return APPROX and stampGlyphs(ctx, text, ix, fx, iy, fy, face, dsize, fill)
        _textUses.pop(k, None)
        try:
            textImage(face, dsize, text, fx, fy, fill)
        except Exception:
            _textCache[k] = None  # read-back failed: always draw this one as a path
            return False
    hit = _textCache[k]
    if hit is None:
        return False
    img, mox, moy = hit
    ctx.save()
    ctx.new_path()
    toDevice(ctx)
    ctx.draw_image(img, ix - mox, iy - moy, 1.0)
    ctx.restore()
    return True


# CMU_APPROX: labels whose text keeps changing (scores, timers) are drawn from cached
# images of their characters. Not exact: tiny-skia anti-aliases a glyph filled on its
# own slightly differently (up to ~10/255) from the same glyph in a whole string.
# One glyph image costs a small read-back, so a glyph is cached once it has been
# drawn this many times.
GLYPH_CACHE_AFTER = 3
_glyphUses = {}
_glyphPlan = {}


def glyphPlan(face, dsize, text, fx):
    """[(x offset, glyph)] for the inked characters of text drawn at sub-pixel phase
    fx, or None when two characters would share a pixel column (overlapping
    anti-aliased edges composite differently from one fill of the whole string)."""
    k = (face, dsize, text, fx)
    plan = _glyphPlan.get(k, FAIL)
    if plan is not FAIL:
        return plan
    if len(_glyphPlan) > 5000:
        _glyphPlan.clear()
    plan = []
    pos = 0
    lastRight = None
    for ch in text:
        xb, yb, w, h, xa = glyphExtents(face, dsize, ch)
        if xa != int(xa):
            plan = None
            break
        if w > 0 or h > 0:
            left = math.floor(pos + fx + xb)
            if lastRight is not None and left < lastRight:
                plan = None
                break
            lastRight = math.ceil(pos + fx + xb + w)
            plan.append((int(pos), ch))
        pos += xa
    _glyphPlan[k] = plan
    return plan


def stampGlyphs(ctx, text, ix, fx, iy, fy, face, dsize, fill):
    """Draw the label as one cached image per character. False if it can't (yet)."""
    plan = glyphPlan(face, dsize, text, fx)
    if not plan:
        return False
    ready = True
    for _, ch in plan:
        k = (face, dsize, ch, fx, fy, fill)
        if k not in _textCache:
            n = _glyphUses.get(k, 0) + 1
            if n < GLYPH_CACHE_AFTER:
                if len(_glyphUses) > 20000:
                    _glyphUses.clear()
                _glyphUses[k] = n
                ready = False
                continue
            _glyphUses.pop(k, None)
            try:
                textImage(face, dsize, ch, fx, fy, fill)
            except Exception:
                _textCache[k] = None
        if _textCache[k] is None:
            return False  # read-back failed for this glyph
    if not ready:
        return False
    ctx.save()
    ctx.new_path()
    toDevice(ctx)
    draw = ctx.draw_image
    for dx, ch in plan:
        img, mox, moy = _textCache[(face, dsize, ch, fx, fy, fill)]
        draw(img, ix + dx - mox, iy - moy, 1.0)
    ctx.restore()
    return True


def drawLabelFast(ctx, text, x, y, face, size, fill, border, bw, rot, cx, cy, ax, xattr):
    if (border is None or not bw) and not rot:
        if stampText(ctx, text, x, y, face, size, fill, ax, xattr):
            return
    if S.face != face:
        # Selected outside save/restore so it stays for the next label (it's slow);
        # restore() would undo it. rebuild() resets S.face each frame.
        ctx.select_font_face(*face)
        S.face = face
    ctx.save()
    ctx.new_path()
    if rot:
        rotateAbout(ctx, rot, cx, cy)
    ctx.set_font_size(size)
    ctx.move_to(x, y)
    ctx.text_path(text)
    ctx.set_source_rgba(*fill)
    ctx.fill_preserve()
    if border is not None and bw:
        ctx.set_source_rgba(*border)
        ctx.set_line_width(bw)
        ctx.stroke()
    ctx.new_path()
    ctx.restore()


class PixelLRU(object):
    """Image cache bounded by total pixels; evicts the least recently used."""

    def __init__(self, budget):
        import collections
        self.budget = budget
        self.pixels = 0
        self.items = collections.OrderedDict()

    def get(self, k):
        v = self.items.get(k)
        if v is not None:
            self.items.move_to_end(k)
        return v

    def put(self, k, v, pixels):
        while self.items and self.pixels + pixels > self.budget:
            _, (_, old) = self.items.popitem(last=False)
            self.pixels -= old
        self.items[k] = (v, pixels)
        self.pixels += pixels


_scaledCache = PixelLRU(24_000_000)  # ~96 MB of resized images


def scaledImage(key, img, w, h):
    """The image resampled once to w x h pixels, so each frame is a plain copy."""
    w = max(1, int(round(w)))
    h = max(1, int(round(h)))
    if w == img.width and h == img.height:
        return img
    k = (key, w, h)
    hit = _scaledCache.get(k)
    if hit is not None:
        return hit[0]
    s = img.scaled(w, h)
    _scaledCache.put(k, s, w * h)
    return s


# CMU_APPROX: a rotated image is drawn from a copy rotated to the nearest ROT_STEP
# degrees and placed on whole pixels (a plain copy instead of a resampling). The
# copies are made with PIL from one read-back of the unrotated image; without PIL,
# images are rotated while drawing as usual.
ROT_STEP = 2
_rotBase = {}  # (key, width, height) -> the image as a PIL 'RGBa' image, or None


def _readOver(img, black):
    """img's pixels drawn over an opaque black or white background."""
    c = wyvern.ImageSurface(img.width, img.height).canvas  # starts opaque white
    if black:
        c.new_path()
        c.rectangle(0, 0, img.width, img.height)
        c.set_source_rgba(0, 0, 0, 1)
        c.fill()
    c.draw_image(img, 0, 0, 1.0)
    w, h, bpp, px = readPixels(c)
    if (w, h) != (img.width, img.height) or bpp < 3:
        raise ValueError('unexpected read-back')
    return bpp, px


def rotationBase(key, img):
    k = (key, img.width, img.height)
    if k in _rotBase:
        return _rotBase[k]
    base = None
    try:
        from PIL import Image, ImageChops
        size = (img.width, img.height)
        # Over black each channel is colour * alpha (premultiplied); over white it's
        # that + (1 - alpha), so alpha = 1 - (white - black).
        bpp, onBlack = _readOver(img, True)
        _, onWhite = _readOver(img, False)

        def chan(px, c):
            return Image.frombytes('L', size, bytes(px[c::bpp]))

        a = ImageChops.invert(ImageChops.subtract(chan(onWhite, 1), chan(onBlack, 1)))
        # A premultiplied channel can't exceed alpha.
        rgb = [ImageChops.darker(chan(onBlack, c), a) for c in range(3)]
        base = Image.merge('RGBa', rgb + [a])
    except Exception:
        pass  # no PIL or no read-back: rotate while drawing
    if len(_rotBase) > 200:
        _rotBase.clear()
    _rotBase[k] = base
    return base


def rotatedImage(key, img, deg):
    """img rotated by deg (rounded to ROT_STEP) about its centre, on a transparent
    image with the same centre, or None."""
    q = round(deg / ROT_STEP) * ROT_STEP % 360
    k = ('rot', key, img.width, img.height, q)
    hit = _scaledCache.get(k)
    if hit is not None:
        return hit[0]
    base = rotationBase(key, img)
    if base is None:
        return None
    from PIL import Image
    # Canvas rotation is clockwise on screen, PIL's counter-clockwise. Bilinear like
    # wyvern's own filtering (bicubic could overshoot alpha).
    im = base.rotate(-q, resample=Image.BILINEAR, expand=True)
    r = wyvern.WyvernImage(bytearray(im.tobytes()), im.width, im.height, im.width * 4)
    _scaledCache.put(k, r, im.width * im.height)
    return r


def drawImageFast(ctx, key, src, left, top, w, h, alpha, rot):
    # Work in device pixels with an image pre-scaled to its on-screen size.
    d, ox, oy = S.dev
    img = scaledImage(key, src, w * d, h * d)
    if rot and APPROX:
        r = rotatedImage(key, img, rot)
        if r:
            ctx.save()
            ctx.new_path()
            toDevice(ctx)
            cx = (left + w / 2) * d + ox
            cy = (top + h / 2) * d + oy
            ctx.draw_image(r, round(cx - r.width / 2), round(cy - r.height / 2), alpha)
            ctx.restore()
            return
    ctx.save()
    ctx.new_path()
    if rot:
        rotateAbout(ctx, rot, left + w / 2, top + h / 2)
        ctx.translate(left, top)
        if d != 1:
            ctx.transform(1 / d, 0, 0, 1 / d, 0, 0)
        ctx.draw_image(img, 0, 0, alpha)
    else:
        toDevice(ctx)
        # Whole-pixel placement: no filtering, a plain copy.
        ctx.draw_image(img, round(left * d + ox), round(top * d + oy), alpha)
    ctx.restore()


# ---------------------------------------------------------------- builders


def buildRect(args, kw):
    if len(args) != 4:
        return FAIL
    x, y, w, h = args
    if not (_num(x) and _num(y) and _num(w) and _num(h)) or w <= 0 or h <= 0:
        return FAIL
    s = style(kw, _SHAPE_KW, 'left-top') if kw else _DEFAULT_RECT
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    left, top = alignLeftTop(aa, x, y, w, h)
    return FastShape(drawRectFast, (left, top, w, h, fill, border, bw, dash, rot))


def buildOval(args, kw):
    if len(args) != 4:
        return FAIL
    x, y, w, h = args
    if not (_num(x) and _num(y) and _num(w) and _num(h)) or w <= 0 or h <= 0:
        return FAIL
    s = style(kw, _SHAPE_KW, 'center')
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    left, top = alignLeftTop(aa, x, y, w, h)
    return FastShape(drawOvalFast, (left + w / 2, top + h / 2, w / 2, h / 2, fill, border, bw, dash, rot))


def buildCircle(args, kw):
    if len(args) != 3:
        return FAIL
    x, y, r = args
    if not (_num(x) and _num(y) and _num(r)) or r <= 0:
        return FAIL
    s = style(kw, _SHAPE_KW, 'center')
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    left, top = alignLeftTop(aa, x, y, 2 * r, 2 * r)
    return FastShape(drawOvalFast, (left + r, top + r, r, r, fill, border, bw, dash, rot))


def _rotatePts(pts, deg, ax, ay):
    c, s = math.cos(deg * DEG), math.sin(deg * DEG)
    out = []
    for i in range(0, len(pts), 2):
        dx, dy = pts[i] - ax, pts[i + 1] - ay
        out.append(ax + dx * c - dy * s)
        out.append(ay + dx * s + dy * c)
    return out


def buildPolygon(args, kw):
    n = len(args)
    if n < 6 or n % 2:
        return FAIL
    for v in args:
        if not _num(v):
            return FAIL
    s = style(kw, _POLY_KW, None)
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    pts = args
    if rot:
        cx, cy = sl.utils.getPolygonCentroid([[args[i], args[i + 1]] for i in range(0, n, 2)])
        pts = _rotatePts(args, rot, cx, cy)
    return FastShape(drawPolyFast, (pts, fill, border, bw, dash))


_unitCache = {}


def unitPoints(kind, points, roundness):
    """Regular polygon / star vertices for radius 1 as (sin, -cos) of the angle
    from north, clockwise (same as utils.getRegularPolygonPoints/getStarPoints)."""
    k = (kind, points, roundness)
    u = _unitCache.get(k)
    if u is None:
        dt = 360 / points
        angles = []
        for i in range(points):
            angles.append((i * dt, 1))
            if kind == 'star':
                angles.append((i * dt + dt / 2, roundness / 100))
        u = _unitCache[k] = [(a * DEG, r) for a, r in angles]
    return u


def _inCircle(kind, args, kw, allowed):
    if len(args) != 4:
        return FAIL
    cx, cy, r, points = args
    if not (_num(cx) and _num(cy) and _num(r)) or r <= 0:
        return FAIL
    if type(points) is not int or points < 3:
        return FAIL
    if kw.get('align', 'center') != 'center':
        return FAIL
    roundness = None
    if kind == 'star':
        roundness = kw.get('roundness', 'default')
        if roundness == 'default':
            roundness = sl.utils.getDefaultRoundness(points)
        elif not _num(roundness) or not 0 <= roundness <= 100:
            return FAIL
        roundness = max(roundness, 5)
    s = style(kw, allowed, None)
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    unit = unitPoints(kind, points, roundness)
    rr = rot * DEG
    pts = []
    for a, k in unit:
        pts.append(cx + r * k * math.sin(a + rr))
        pts.append(cy - r * k * math.cos(a + rr))
    if kind == 'poly' and border is not None and bw and not dash:
        # Convex: insetting by bw/2 keeps it regular (apothem - bw/2).
        ri = r - (bw / 2) / math.cos(math.pi / points)
        if ri > 0:
            inner = []
            for a, k in unit:
                inner.append(cx + ri * math.sin(a + rr))
                inner.append(cy - ri * math.cos(a + rr))
            return FastShape(drawInsetPolyFast, (pts, inner, fill, border, bw, dash))
    return FastShape(drawPolyFast, (pts, fill, border, bw, dash))


def buildRegularPolygon(args, kw):
    return _inCircle('poly', args, kw, _SHAPE_KW)


def buildStar(args, kw):
    return _inCircle('star', args, kw, _STAR_KW)


def buildArc(args, kw):
    if len(args) != 6:
        return FAIL
    cx, cy, w, h, start, sweep = args
    if not (_num(cx) and _num(cy) and _num(w) and _num(h) and _num(start) and _num(sweep)):
        return FAIL
    if w <= 0 or h <= 0 or not 0 < sweep < 360:
        return FAIL
    if kw.get('align', 'center') != 'center':
        return FAIL
    s = style(kw, _SHAPE_KW, None)
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    return FastShape(drawArcFast, (cx, cy, w / 2, h / 2, start, sweep, fill, border, bw, dash, rot))


def _noop(ctx):
    pass


def buildLine(args, kw):
    if len(args) != 4:
        return FAIL
    x1, y1, x2, y2 = args
    if not (_num(x1) and _num(y1) and _num(x2) and _num(y2)):
        return FAIL
    s = style(kw, _LINE_KW, None)
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    lw = kw.get('lineWidth', 2)
    if not _num(lw) or lw <= 0:
        return FAIL
    if fill is None:
        return FastShape(_noop, ())
    return FastShape(drawLineFast, (x1, y1, x2, y2, fill, lw, dash, rot))


def buildLabel(args, kw):
    if len(args) != 3:
        return FAIL
    value, x, y = args
    if not (_num(x) and _num(y)) or callable(value):
        return FAIL
    s = style(kw, _LABEL_KW, 'center')
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    get = kw.get
    size, font = get('size', 12), get('font', 'arial')
    bold, italic = get('bold', False), get('italic', False)
    if not _num(size) or size < 0 or type(font) is not str:
        return FAIL
    if type(bold) is not bool or type(italic) is not bool:
        return FAIL
    if 'font' in kw:
        sl.maybe_show_font_warning(font)
    if fill is None:
        fill = (0, 0, 0, 1)  # shape_logic draws fill=None labels black
    text = str(value)
    face = fontFace(font, bold, italic)
    h = -textExtents(face, size, text)[1]
    w, xAdjust = textBox(face, size, text)
    left, top = alignLeftTop(aa, x, y, w, h)
    return FastShape(
        drawLabelFast,
        (text, left - xAdjust, top + h, face, size, fill, border, bw, rot, left + w / 2, top + h / 2, x, aa[0]),
    )


def buildImage(args, kw):
    if len(args) != 3:
        return FAIL
    url, x, y = args
    if not (_num(x) and _num(y)):
        return FAIL
    if not (type(url) is str or isinstance(url, sl.PILWrapper)):
        return FAIL
    if kw.get('fill') is not None or kw.get('border') is not None or kw.get('dashes', False):
        return FAIL
    rest = {k: v for k, v in kw.items() if k != 'width' and k != 'height' and k != 'fill'}
    if not kw.keys() <= _IMAGE_KW:
        return FAIL
    s = style(rest, _SHAPE_KW, 'left-top')
    if s is FAIL:
        return FAIL
    try:
        sl.loadImage(url)  # cached by shape_logic after the first call
    except Exception:
        return FAIL  # the original call raises the proper error
    key = sl.hashReference(url)
    src = sl.activeDrawing.images[key]
    w, h = kw.get('width', src.width), kw.get('height', src.height)
    if not (_num(w) and _num(h)) or w <= 0 or h <= 0:
        return FAIL
    left, top = alignLeftTop(s[3], x, y, w, h)
    alpha = kw.get('opacity', 100) / 100
    return FastShape(drawImageFast, (key, src, left, top, w, h, alpha, s[5]))


_DEFAULT_RECT = None


def flushSlowShapes(ctx):
    """Draw (in order) and drop shapes the original code added to the group."""
    tlg = sl.activeDrawing.tlg
    g = getattr(tlg, '_shape', tlg)
    if g._shapes:
        shapes = g._shapes
        g._shapes = []
        ctx.save()
        # Start from the clean source state the stock drawing has (the fast paths
        # set colours without save/restore).
        ctx.set_source_rgba(0, 0, 0, 1)
        try:
            for shape in shapes:
                shape.draw(ctx)
        finally:
            ctx.restore()


# Built shapes, keyed by the call's signature (function, args, kwargs), so a call
# identical to a recent one (a tile map, a star field, a HUD box) skips argument
# checking and geometry. Two generations: when the new one fills up it becomes the
# old one, and old entries that are used again move back to the new one.
MEMO_SIZE = 4096
_memoNew = {}
_memoOld = {}


def memoMiss(key, builder, args, kwargs):
    global _memoNew, _memoOld
    item = _memoOld.get(key)
    if item is None:
        item = builder(args, kwargs)
    if len(_memoNew) >= MEMO_SIZE:
        _memoOld = _memoNew
        _memoNew = {}
    _memoNew[key] = item
    return item


def clearMemo():
    _memoNew.clear()
    _memoOld.clear()


def drawNow(ctx, key, builder, slowFn, args, kwargs):
    if key is None:
        item = builder(args, kwargs)  # unhashable arguments
    else:
        item = _memoNew.get(key)
        if item is None:
            item = memoMiss(key, builder, args, kwargs)
    if item is not FAIL:
        item.fn(ctx, *item.args)  # immediate mode: straight into the frame
        return None
    result = slowFn(*args, **kwargs)
    flushSlowShapes(ctx)
    return result


class StaticPrefix(object):
    """Skips re-drawing the unchanged start of a frame.

    Each draw call gets a signature (function, args, kwargs). While this frame's
    calls equal last frame's, they aren't drawn; once the matching run reaches the
    cached length, a bitmap of that prefix is pasted instead. A mismatch inside the
    run replays the skipped calls first, so output is always exact. The bitmap is
    re-captured whenever the static run gets shorter or longer; captures are a PNG
    round trip, so if they keep getting replaced quickly, capturing backs off.

    Pasting a full frame isn't free (a 2-3x larger cost than filling the screen), so
    the time from the start of the frame to the end of the paste is compared with
    the time the same calls took to draw. If pasting keeps losing, the layer stops
    capturing for a while (a cheap tile grid is faster to just draw)."""

    SKIP, EXTEND, NORMAL = 0, 1, 2
    MIN_CALLS = 16  # shorter static runs aren't worth a full-frame copy
    SHORT_LIFE = 30  # frames: a snapshot replaced sooner than this was wasted
    MAX_BACKOFF = 1800
    SLOW_PASTES = 3  # this many pastes in a row slower than PASTE_RATIO x drawing...
    PASTE_RATIO = 0.8
    SLOW_BLOCK = 600  # ...and capturing stops for this many frames

    def __init__(self):
        self.on = os.environ.get('CMU_STATIC_LAYER', '1') != '0'
        self.frame = 0
        self.backoff = 0
        self.blockedUntil = 0
        self.slowPastes = 0
        self.drawCost = 0.0
        self.t0 = 0.0
        self.active = False  # tracking this frame's calls (see begin)
        self.reset()

    def reset(self):
        self.prev = []
        self.length = 0
        self.snap = None
        self.snapFrame = None
        self.key = None
        self.active = False

    def begin(self, ctx, key, paintBg):
        """Returns True if the caller must paint the background now."""
        self.frame += 1
        if key != self.key:
            self.reset()
            self.key = key
        self.ctx = ctx
        if self.snap is None and self.frame < self.blockedUntil:
            # Capturing is blocked, so there's nothing to skip: don't even track
            # the calls (step() costs a little per call). Tracking restarts after.
            self.active = False
            self.prev = []
            return True
        self.active = True
        self.t0 = time.perf_counter()
        self.cur = []
        self.pending = []
        self.paintBg = paintBg
        self.mode = self.SKIP if self.snap is not None else self.EXTEND
        return self.mode != self.SKIP  # the pasted bitmap already holds the background

    def capture(self, n):
        cost = time.perf_counter() - self.t0  # drawing calls 0..n-1 this frame
        f = self.frame
        if self.snapFrame is not None:
            if f - self.snapFrame < self.SHORT_LIFE:
                self.backoff = min(max(self.backoff * 2, self.SHORT_LIFE), self.MAX_BACKOFF)
                self.blockedUntil = f + self.backoff
            else:
                self.backoff = 0
        self.snap, self.length, self.snapFrame = None, 0, None
        if n < self.MIN_CALLS or f < self.blockedUntil:
            return
        try:
            self.snap = snapshot(self.ctx)  # the frame so far is exactly calls 0..n-1
        except Exception:
            self.on = False  # no read-back on this system: just draw everything
            return
        self.length = n
        self.snapFrame = f
        self.drawCost = cost
        self.slowPastes = 0

    def paste(self):
        ctx = self.ctx
        ctx.save()
        toDevice(ctx)
        ctx.draw_image(self.snap, 0, 0, 1.0)
        ctx.restore()
        if time.perf_counter() - self.t0 < self.PASTE_RATIO * self.drawCost:
            self.slowPastes = 0
            return
        self.slowPastes += 1
        if self.slowPastes >= self.SLOW_PASTES:
            # Drawing is cheaper: drop the snapshot (this frame is already complete
            # up to here) and don't capture again for a while.
            self.slowPastes = 0
            self.snap, self.length, self.snapFrame = None, 0, None
            self.blockedUntil = self.frame + self.SLOW_BLOCK

    def replayPending(self):
        self.paintBg()
        pending, self.pending = self.pending, []
        for call in pending:
            drawNow(self.ctx, *call)

    def step(self, sig, builder, slowFn, args, kwargs):
        """True if the call was absorbed (skipped), False if the caller must draw it.
        sig is None for a call with unhashable arguments (it never matches)."""
        i = len(self.cur)
        prev = self.prev
        # sig is hashable, so an equal one holds no mutable values.
        same = sig is not None and i < len(prev) and sig == prev[i]
        self.cur.append(sig)
        if self.mode == self.SKIP:
            if same and i < self.length:
                self.pending.append((sig, builder, slowFn, args, kwargs))
                if i + 1 == self.length:
                    self.pending = []
                    self.paste()
                    self.mode = self.EXTEND
                return True
            self.replayPending()  # mismatch inside the cached run
            self.capture(i)
            self.mode = self.NORMAL
            return False
        if self.mode == self.EXTEND:
            if same:
                return False
            if i != self.length:
                self.capture(i)
            self.mode = self.NORMAL
        return False

    def end(self):
        if not self.active:
            self.ctx = None
            return
        self.active = False
        n = len(self.cur)
        if self.mode == self.SKIP:  # frame ended inside the cached run
            self.replayPending()
            self.capture(n)
        elif self.mode == self.EXTEND and n != self.length:  # whole frame unchanged
            self.capture(n)
        self.prev = self.cur
        self.ctx = None


def makeFastDrawFn(builder, slowFn):
    isLabel = builder is buildLabel

    def drawFn(*args, **kwargs):
        ctx = S.ctx  # set only while the user's redrawAll runs in CPCS mode
        if ctx is None:
            return slowFn(*args, **kwargs)
        # The signature of the call. A label's text is in it too: 1 == 1.0 == True,
        # but they're drawn as '1', '1.0' and 'True'.
        if kwargs:
            key = (builder, args, tuple(kwargs.items()))
        else:
            key = (builder, args)
        if isLabel and args:
            key = key + (str(args[0]),)
        try:
            item = _memoNew.get(key)
        except TypeError:  # unhashable (e.g. a list argument): no memo, never static
            key = item = None
        layer = S.layer
        if layer.active and layer.step(key, builder, slowFn, args, kwargs):
            return None
        if item is None:
            if key is None:
                item = builder(args, kwargs)
            else:
                item = memoMiss(key, builder, args, kwargs)
        if item is not FAIL:
            item.fn(ctx, *item.args)  # immediate mode: straight into the frame
            return None
        result = slowFn(*args, **kwargs)
        flushSlowShapes(ctx)
        return result

    drawFn.__name__ = slowFn.__name__
    drawFn.__doc__ = slowFn.__doc__
    return drawFn


# ---------------------------------------------------------------- frame loop


class State(object):
    dirty = True
    lastRender = 0.0
    stepped = False  # a step event came since the last rendered frame
    surface = None  # wyvern surface of the latest event
    win = None  # window size (logical units) from the latest 'resize' event
    fit = False
    fitView = None  # (scale, x offset, y offset) of the app in the window (CMU_FIT)
    clearWindow = True  # CMU_FIT: the letterbox needs painting
    ctx = None  # wyvern canvas while the user's redrawAll is drawing
    face = None  # font selected on ctx at the redrawAll level (see drawLabelFast)
    language = None  # cmu_graphics language the memoized shapes were built in
    dev = (1.0, 0.0, 0.0)  # app units -> device pixels: (scale, x offset, y offset)
    layer = None
    inspectorShown = False
    mvcEvery = 1  # CMU_MVC_CHECK: run the MVC checker on 1 frame in N (0 = never)
    mvcFrame = 0
    stopSnap = None  # the frame (with the error screen) once the app has stopped


S = State()
_origRedrawAllWrapper = cg.App.redrawAllWrapper
_origRedrawAll = cg.App.redrawAll
_origOnEvent = cg.App.on_event


def redrawAllWrapper(self):
    # Defer: the user's redrawAll runs once, right before the frame is rendered.
    if self._isMvc:
        S.dirty = True
    else:
        _origRedrawAllWrapper(self)


def paintBackground(self, ctx):
    bg = self.background or 'white'
    c = color(bg, 1.0)
    if c is FAIL or c is None:
        shape = sl.Rect(
            {'noGroup': True, 'top': 0, 'left': 0, 'width': self.width, 'height': self.height, 'fill': bg}
        )
        shape.draw(ctx)
    else:
        ctx.new_path()
        ctx.rectangle(0, 0, self.width, self.height)
        ctx.set_source_rgba(*c)
        ctx.fill()


def setupView(self, ctx):
    """Work out S.dev and, with CMU_FIT, put the app's transform on ctx."""
    surface = S.surface
    win = S.win
    if win is None or not win[0]:
        win = (self.width, self.height)
    # HiDPI: the surface is in device pixels, the window size in logical units.
    sf = surface.width / win[0] if surface is not None and win[0] else 1.0
    if not S.fit:
        S.dev = (sf, 0.0, 0.0)
        return
    s = min(win[0] / self.width, win[1] / self.height)
    ox = (win[0] - self.width * s) / 2
    oy = (win[1] - self.height * s) / 2
    if S.clearWindow:
        S.clearWindow = False
        ctx.new_path()
        ctx.rectangle(-1, -1, win[0] + 2, win[1] + 2)
        ctx.set_source_rgba(0, 0, 0, 1)
        ctx.fill()
    S.fitView = (s, ox, oy)
    ctx.transform(s, 0, 0, s, ox, oy)
    S.dev = (sf * s, sf * ox, sf * oy)


def renderFailed(self, ctx):
    """What _safeMethod does, without ending the event loop: report, stop, and draw
    the error screen over what was drawn so far."""
    sys.excepthook(*sys.exc_info())
    self.stop()
    if self._running:
        self.drawErrorScreen()
    flushSlowShapes(ctx)


def rebuild(self, ctx):
    """Run the user's redrawAll, drawing each shape into ctx as it is called."""
    S.dirty = False
    layer = S.layer
    if layer.on:
        bg = self.background
        key = (id(S.surface), S.surface and S.surface.width, S.surface and S.surface.height, S.dev,
               bg if bg is None or type(bg) is str else id(bg))
        if layer.begin(ctx, key, lambda: paintBackground(self, ctx)):
            paintBackground(self, ctx)
    else:
        paintBackground(self, ctx)
    ctx.save()
    if sl.cmuGraphicsLanguage != S.language:  # colour and align names are translated
        S.language = sl.cmuGraphicsLanguage
        clearMemo()
    S.ctx = ctx
    S.face = None
    every = S.mvcEvery
    skipCheck = False
    if every != 1 and not self._disableMvcChecker:
        S.mvcFrame += 1
        skipCheck = every == 0 or S.mvcFrame % every != 0
    try:
        if skipCheck:
            # Only for this frame: the game's own disableMvcChecker setting is untouched.
            self._disableMvcChecker = True
            try:
                _origRedrawAllWrapper(self)
            finally:
                self._disableMvcChecker = False
        else:
            _origRedrawAllWrapper(self)
        flushSlowShapes(ctx)
        if layer.on:
            layer.end()
    except Exception:
        S.ctx = None
        self.inRedrawAll = False
        layer.reset()
        renderFailed(self, ctx)
    finally:
        S.ctx = None
        S.face = None
        ctx.restore()
        ctx.new_path()


def drawInspector(self, ctx):
    ctx.save()
    try:
        self.inspector.draw(ctx)
    finally:
        ctx.restore()


def drawStopped(self, ctx, inspector):
    """After an error: keep the last frame with the error screen over it."""
    if S.stopSnap is None:
        flushSlowShapes(ctx)  # the error screen's shapes
        try:
            ctx.save()
            ctx.new_path()
            # snapshot takes the whole surface in device pixels; nothing to undo here
            S.stopSnap = snapshot(ctx)
        except Exception:
            S.stopSnap = False
        finally:
            ctx.restore()
    elif S.stopSnap and (inspector or S.inspectorShown):
        ctx.save()
        toDevice(ctx)
        ctx.draw_image(S.stopSnap, 0, 0, 1.0)
        ctx.restore()
    if inspector:
        drawInspector(self, ctx)


def redrawAll(self, ctx):
    if not self._isMvc:
        # Shape-object mode (cmu_graphics.run()): the stock drawing, scaled if CMU_FIT.
        ctx.save()
        try:
            setupView(self, ctx)
            _origRedrawAll(self, ctx)
        finally:
            ctx.restore()
        return
    inspector = self.shouldDrawInspector()
    ctx.save()
    try:
        setupView(self, ctx)
        if self.stopped:
            drawStopped(self, ctx, inspector)
            S.inspectorShown = inspector
            return
        if inspector or S.inspectorShown:
            S.dirty = True  # no display list to redraw the inspector over: rebuild
        if not S.dirty:
            return  # nothing changed: the window keeps the last frame
        now = time.perf_counter()
        sps = self.stepsPerSecond
        if not S.stepped and not self.paused and sps >= 10 and now - S.lastRender < 0.5 / sps:
            return  # event-only redraw right after a frame: the next step draws it
        S.lastRender = now
        S.stepped = False
        rebuild(self, ctx)
        if inspector:
            drawInspector(self, ctx)
        S.inspectorShown = inspector
    finally:
        ctx.restore()
        self.frameworkRedrew = True


class _Mouse(object):
    __slots__ = ('x', 'y', 'button', 'buttons')


class _MouseEvent(object):
    __slots__ = ('event_type', 'mouse')


def _fitMouse(self, event):
    """Window coordinates -> app coordinates when the app is scaled to fit."""
    s, ox, oy = S.fitView
    m = event.mouse
    mm = _Mouse()
    mm.x = int(min(max((m.x - ox) / s, 0), self.width - 1))
    mm.y = int(min(max((m.y - oy) / s, 0), self.height - 1))
    mm.button = getattr(m, 'button', None)
    mm.buttons = getattr(m, 'buttons', None)
    e = _MouseEvent()
    e.event_type = event.event_type
    e.mouse = mm
    return e


_MOUSE_EVENTS = frozenset(['mouse_move', 'mouse_drag', 'mouse_press', 'mouse_release'])


def onEvent(self, event, surface):
    S.surface = surface
    et = event.event_type
    if et == 'step':
        S.stepped = True  # the redraw that follows a step is never put off
    elif et == 'resize':
        S.win = (event.resize.width, event.resize.height)
        S.clearWindow = True
        S.dirty = True
        if S.fit:
            return  # the app keeps its size; the next redraw rescales it
    elif S.fit and et in _MOUSE_EVENTS and S.fitView is not None:
        event = _fitMouse(self, event)
    _origOnEvent(self, event, surface)


# ---------------------------------------------------------------- install

_BUILDERS = {
    'drawRect': buildRect,
    'drawOval': buildOval,
    'drawCircle': buildCircle,
    'drawLine': buildLine,
    'drawPolygon': buildPolygon,
    'drawRegularPolygon': buildRegularPolygon,
    'drawStar': buildStar,
    'drawArc': buildArc,
    'drawLabel': buildLabel,
    'drawImage': buildImage,
}


def _replaceEverywhere(swaps):
    """Swap original functions for the fast ones wherever they were imported:
    cmu_graphics' own modules, the package (including its translated aliases,
    e.g. the Spanish names), and the game itself if it already did
    `from cmu_graphics import *`."""
    main = sys.modules.get('__main__')
    spaces = [cg.__dict__, sl.__dict__, vars(_pkg)]
    if main is not None:
        spaces.append(main.__dict__)
    for g in spaces:
        for name, value in list(g.items()):
            new = swaps.get(id(value))
            if new is not None and new[0] is value:
                g[name] = new[1]


def install():
    global _DEFAULT_RECT
    # Fewer, cheaper GC passes: every frame allocates many short-lived objects,
    # and the big module-level tables never need rescanning.
    gc.freeze()
    gc.set_threshold(20000, 20, 20)

    swaps = {}  # id(original) -> (original, replacement)

    def swap(orig, new):
        swaps[id(orig)] = (orig, new)

    swap(_origToEnglish, toEnglish)
    swap(_origT, t)
    swap(cg.__dict__['rgb'], rgb)
    _DEFAULT_RECT = style({}, _SHAPE_KW, 'left-top')
    # Same hash as RGB.__hash__, without three trips through the translating __getattr__.
    sl.RGB.__hash__ = lambda c: hash((c.__dict__['_red'], c.__dict__['_green'], c.__dict__['_blue']))

    cg.App.redrawAllWrapper = redrawAllWrapper
    cg.App.redrawAll = redrawAll
    cg.App.on_event = onEvent
    S.layer = StaticPrefix()
    S.fit = os.environ.get('CMU_FIT') == '1'
    try:
        S.mvcEvery = max(0, int(os.environ.get('CMU_MVC_CHECK', '1')))
    except ValueError:
        S.mvcEvery = 1
    if S.fit:
        object.__getattribute__(cg.app, '_app')._fullscreen = True

    for name, builder in _BUILDERS.items():
        orig = cg.__dict__[name]
        swap(orig, makeFastDrawFn(builder, orig))
    _replaceEverywhere(swaps)


if os.environ.get('CMU_FAST', '1') != '0' and not getattr(cg, '_fastpathInstalled', False):
    cg._fastpathInstalled = True
    print('Enabling fast path')
    install()
