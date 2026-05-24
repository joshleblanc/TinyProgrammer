"""
Color Adjustment Layer for TinyProgrammer Display

Applies Photoshop-style color adjustments to the entire rendered frame
before writing to framebuffer. Uses precomputed per-channel uint8 lookup
tables so the runtime cost is a single vectorized index per channel —
fast enough to keep up on the Pi Zero 2 W where float-based blending was
the framebuffer bottleneck.
"""

import numpy as np

# Preset color schemes
# Each scheme has: mode, color (RGB), intensity (0.0-1.0)
COLOR_SCHEMES = {
    "none": None,
    "amber": {"mode": "multiply", "color": (255, 176, 0), "intensity": 0.6},
    "green": {"mode": "multiply", "color": (0, 255, 80), "intensity": 0.55},
    "blue": {"mode": "multiply", "color": (80, 140, 255), "intensity": 0.5},
    "sepia": {"mode": "multiply", "color": (255, 200, 150), "intensity": 0.6},
    "cool": {"mode": "multiply", "color": (180, 210, 255), "intensity": 0.4},
    "warm": {"mode": "multiply", "color": (255, 220, 180), "intensity": 0.45},
    "night": {"mode": "multiply", "color": (255, 50, 50), "intensity": 0.6},
    "inverted": {"mode": "invert", "color": None, "intensity": 1.0},
    # Monochrome schemes: desaturate to luminance first, then tint. Unlike
    # the "multiply" schemes above these throw away the canvas's color info,
    # producing a classic one-color CRT/e-ink look.
    "mono": {"mode": "desaturate", "color": (255, 255, 255), "intensity": 1.0},
    "mono_amber": {"mode": "desaturate", "color": (255, 176, 0), "intensity": 1.0},
    "mono_green": {"mode": "desaturate", "color": (0, 255, 80), "intensity": 1.0},
    "mono_blue": {"mode": "desaturate", "color": (80, 140, 255), "intensity": 1.0},
}

# Module-level LUT cache. One entry per scheme name; built lazily on first use.
_LUT_CACHE: dict = {}


def apply_color_adjustment(r, g, b, scheme_name):
    """
    Apply color adjustment to RGB uint8 numpy arrays via precomputed LUTs.

    Args:
        r, g, b: numpy arrays of uint8 for each color channel
        scheme_name: name of the color scheme to apply

    Returns:
        Tuple of (r, g, b) adjusted uint8 numpy arrays
    """
    if scheme_name == "none" or scheme_name not in COLOR_SCHEMES:
        return r, g, b
    if COLOR_SCHEMES[scheme_name] is None:
        return r, g, b

    luts = _get_luts(scheme_name)
    r_lut, g_lut, b_lut = luts["r_lut"], luts["g_lut"], luts["b_lut"]

    if luts["mode"] == "desaturate":
        # Rec. 601 luminance: 0.299*R + 0.587*G + 0.114*B. Integer approximation
        # 77/256, 150/256, 29/256 (sums to 256, so >>8 normalizes). uint16
        # intermediates avoid overflow from the channel sums.
        r16 = r.astype(np.uint16)
        g16 = g.astype(np.uint16)
        b16 = b.astype(np.uint16)
        lum = ((77 * r16 + 150 * g16 + 29 * b16) >> 8).astype(np.uint8)
        return r_lut[lum], g_lut[lum], b_lut[lum]

    return r_lut[r], g_lut[g], b_lut[b]


def _get_luts(scheme_name: str) -> dict:
    cached = _LUT_CACHE.get(scheme_name)
    if cached is not None:
        return cached

    luts = _build_luts(scheme_name)
    _LUT_CACHE[scheme_name] = luts
    return luts


def _build_luts(scheme_name: str) -> dict:
    """Build per-channel uint8 LUTs for the given scheme."""
    scheme = COLOR_SCHEMES[scheme_name]
    mode = scheme["mode"]
    color = scheme.get("color") or (255, 255, 255)
    intensity = float(scheme["intensity"])
    cr, cg, cb = color

    idx = np.arange(256, dtype=np.float32)
    keep = 1.0 - intensity

    if mode == "multiply":
        rl = idx * keep + (idx * cr / 255.0) * intensity
        gl = idx * keep + (idx * cg / 255.0) * intensity
        bl = idx * keep + (idx * cb / 255.0) * intensity
    elif mode == "screen":
        rl = idx * keep + (255.0 - (255.0 - idx) * (255.0 - cr) / 255.0) * intensity
        gl = idx * keep + (255.0 - (255.0 - idx) * (255.0 - cg) / 255.0) * intensity
        bl = idx * keep + (255.0 - (255.0 - idx) * (255.0 - cb) / 255.0) * intensity
    elif mode == "overlay":
        rl = idx * keep + _overlay_curve(idx, cr) * intensity
        gl = idx * keep + _overlay_curve(idx, cg) * intensity
        bl = idx * keep + _overlay_curve(idx, cb) * intensity
    elif mode == "invert":
        curve = idx * keep + (255.0 - idx) * intensity
        rl = gl = bl = curve
    elif mode == "desaturate":
        # LUT input is luminance, not the original channel. At intensity=1.0
        # (all current mono schemes) this is exact. At intensity<1.0 we blend
        # the tint against luminance rather than the original channel, which
        # is a slight deviation from the prior float implementation but
        # preserves the "pull toward this tint" feel.
        rl = idx * keep + (idx * cr / 255.0) * intensity
        gl = idx * keep + (idx * cg / 255.0) * intensity
        bl = idx * keep + (idx * cb / 255.0) * intensity
    else:
        rl = gl = bl = idx

    return {
        "mode": mode,
        "r_lut": np.clip(rl, 0, 255).astype(np.uint8),
        "g_lut": np.clip(gl, 0, 255).astype(np.uint8),
        "b_lut": np.clip(bl, 0, 255).astype(np.uint8),
    }


def _overlay_curve(idx, c):
    low = 2.0 * idx * c / 255.0
    high = 255.0 - 2.0 * (255.0 - idx) * (255.0 - c) / 255.0
    return np.where(idx < 128, low, high)


def get_available_schemes():
    """Return list of available color scheme names."""
    return list(COLOR_SCHEMES.keys())
