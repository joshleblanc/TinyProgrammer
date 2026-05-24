"""
Direct Framebuffer Writer for SPI TFT Displays

Bypasses SDL's broken fbcon driver on Bookworm/Trixie by:
1. Rendering to an in-memory pygame surface
2. Converting to RGB565 and writing directly to /dev/fb0

Works with fbtft-based displays (ILI9486, ILI9341, etc.)
Also works with HDMI displays that report portrait framebuffer (480x800)
"""

import os
import numpy as np

from .color_adjustment import apply_color_adjustment

# Module-level color scheme setting (can be changed at runtime)
_color_scheme = "none"


def set_color_scheme(scheme_name: str):
    """Set the active color scheme for framebuffer output."""
    global _color_scheme
    _color_scheme = scheme_name
    print(f"[FB] Color scheme set to: {scheme_name}")


def get_color_scheme() -> str:
    """Get the current color scheme name."""
    return _color_scheme


# Check if we're on a system with a framebuffer
FB_DEVICE = os.environ.get("FB_DEVICE", "/dev/fb0")
IS_FRAMEBUFFER_AVAILABLE = os.path.exists(FB_DEVICE)

# Rotation setting: 0=none, 1=90°CW, 2=180°, 3=270°CW (90°CCW)
# Set via environment variable or auto-detect based on framebuffer dimensions
FB_ROTATION = int(os.environ.get("FB_ROTATION", "-1"))  # -1 = auto-detect


def _read_text(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    value = _read_text(path)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def rgb888_to_rgb565(surface) -> np.ndarray:
    """
    Convert a pygame surface (RGB888) to RGB565 numpy array.
    Applies color adjustment layer if set.

    RGB565 format: RRRRR GGGGGG BBBBB (16 bits)
    """
    import pygame
    # pixels3d is a view, avoiding the full RGB888 copy made by array3d().
    arr = pygame.surfarray.pixels3d(surface)  # Shape: (width, height, 3)

    # Extract RGB channels as uint8 (views; LUT path consumes uint8 directly).
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]

    # Apply color adjustment layer if active. Returns uint8 arrays.
    if _color_scheme != "none":
        r, g, b = apply_color_adjustment(r, g, b, _color_scheme)

    # Convert to RGB565. Widening to uint16 happens after color adjust so the
    # heavy intermediates stay uint8.
    r16 = r.astype(np.uint16)
    g16 = g.astype(np.uint16)
    b16 = b.astype(np.uint16)
    rgb565 = ((r16 & 0xF8) << 8) | ((g16 & 0xFC) << 3) | (b16 >> 3)

    # Transpose because pygame uses (x, y) but framebuffer uses (y, x)
    return np.ascontiguousarray(rgb565.T)


def rgb888_to_xrgb8888(surface) -> np.ndarray:
    """Convert a pygame surface to a 32bpp XRGB/ARGB framebuffer array."""
    import pygame
    arr = pygame.surfarray.pixels3d(surface)  # Shape: (width, height, 3)

    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]
    if _color_scheme != "none":
        r, g, b = apply_color_adjustment(r, g, b, _color_scheme)

    r32 = r.astype(np.uint32)
    g32 = g.astype(np.uint32)
    b32 = b.astype(np.uint32)
    xrgb = 0xFF000000 | (r32 << 16) | (g32 << 8) | b32
    return np.ascontiguousarray(xrgb.T)


class FramebufferWriter:
    """
    Writes pygame surfaces directly to a Linux framebuffer device.
    Supports rotation for displays with portrait-mode framebuffers.
    """

    def __init__(self, width: int = 480, height: int = 320, device: str = None):
        self.render_width = width   # What we render at (landscape)
        self.render_height = height
        self.device = device or FB_DEVICE
        self.enabled = IS_FRAMEBUFFER_AVAILABLE
        self.rotation = FB_ROTATION
        self.fb_width = width
        self.fb_height = height
        self.fb_bpp = 16
        self.fb_stride = width * 2
        self.fb_name = ""
        self._fb = None

        if self.enabled:
            # Get actual framebuffer dimensions
            try:
                self._load_capabilities()

                # Auto-detect rotation if framebuffer is portrait but we render landscape
                if self.rotation == -1:
                    if self.fb_width < self.fb_height and width > height:
                        # Framebuffer is portrait, we render landscape - need 270° CW (90° CCW) rotation
                        self.rotation = 3
                        print(f"[FB] Auto-detected rotation: 270° CW (portrait FB {self.fb_width}x{self.fb_height} -> landscape {width}x{height})")
                    else:
                        self.rotation = 0

                if self.rotation == 0 and (self.fb_width != width or self.fb_height != height):
                    print(f"[FB] Warning: Expected {width}x{height}, got {self.fb_width}x{self.fb_height}")

                if self.fb_bpp not in (16, 32):
                    print(f"[FB] Warning: optimized writer supports 16bpp RGB565 and 32bpp XRGB8888; framebuffer reports {self.fb_bpp}bpp")
            except Exception as e:
                print(f"[FB] Could not verify dimensions: {e}")
                self.rotation = 0 if self.rotation == -1 else self.rotation

    def _load_capabilities(self):
        fb_base = f"/sys/class/graphics/{os.path.basename(self.device)}"

        size_text = _read_text(os.path.join(fb_base, "virtual_size"))
        if size_text:
            self.fb_width, self.fb_height = map(int, size_text.split(','))

        self.fb_bpp = _read_int(os.path.join(fb_base, "bits_per_pixel")) or self.fb_bpp
        self.fb_stride = _read_int(os.path.join(fb_base, "stride")) or (
            self.fb_width * max(1, self.fb_bpp // 8)
        )
        self.fb_name = _read_text(os.path.join(fb_base, "name")) or ""

    def write(self, surface) -> bool:
        """
        Write a pygame surface to the framebuffer.
        Handles rotation if framebuffer orientation differs from render orientation.
        Returns True on success, False on failure.
        """
        if not self.enabled:
            return False

        try:
            if self.fb_bpp == 16:
                frame = rgb888_to_rgb565(surface)
            elif self.fb_bpp == 32:
                frame = rgb888_to_xrgb8888(surface)
            else:
                print(f"[FB] Write skipped: unsupported framebuffer depth {self.fb_bpp}bpp")
                return False

            # Apply rotation if needed
            if self.rotation == 1:  # 90° CW
                frame = np.rot90(frame, k=-1)  # k=-1 is 90° CW
            elif self.rotation == 2:  # 180°
                frame = np.rot90(frame, k=2)
            elif self.rotation == 3:  # 270° CW (90° CCW)
                frame = np.rot90(frame, k=1)  # k=1 is 90° CCW

            # Ensure contiguous array for writing
            frame = np.ascontiguousarray(frame)

            fb = self._open_fb()
            fb.seek(0)
            self._write_array(fb, frame)

            return True
        except Exception as e:
            print(f"[FB] Write error: {e}")
            return False

    def clear(self, r: int = 0, g: int = 0, b: int = 0) -> bool:
        """
        Clear the framebuffer with a solid color.
        """
        if not self.enabled:
            return False

        try:
            if self.fb_bpp == 16:
                color = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                dtype = np.uint16
            elif self.fb_bpp == 32:
                color = 0xFF000000 | (r << 16) | (g << 8) | b
                dtype = np.uint32
            else:
                print(f"[FB] Clear skipped: unsupported framebuffer depth {self.fb_bpp}bpp")
                return False

            data = np.full((self.fb_height, self.fb_width), color, dtype=dtype)

            fb = self._open_fb()
            fb.seek(0)
            self._write_array(fb, data)

            return True
        except Exception as e:
            print(f"[FB] Clear error: {e}")
            return False

    def close(self):
        """Close the framebuffer file descriptor if it is open."""
        if self._fb:
            self._fb.close()
            self._fb = None

    def _open_fb(self):
        if self._fb is None or self._fb.closed:
            self._fb = open(self.device, 'r+b', buffering=0)
        return self._fb

    def _write_array(self, fb, data: np.ndarray):
        row_bytes = data.shape[1] * data.dtype.itemsize
        if self.fb_stride == row_bytes:
            fb.write(memoryview(data))
            return

        padded = np.zeros((data.shape[0], self.fb_stride), dtype=np.uint8)
        padded[:, :row_bytes] = data.view(np.uint8).reshape(data.shape[0], row_bytes)
        fb.write(memoryview(padded))


# Singleton instance for easy access
_writer = None

def get_writer(width: int = 480, height: int = 320) -> FramebufferWriter:
    """Get or create the global framebuffer writer."""
    global _writer
    if _writer is None:
        _writer = FramebufferWriter(width, height)
    return _writer
