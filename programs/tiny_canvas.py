import os
import json
import sys
import time

class Canvas:
    """
    A simple interface for drawing on the Tiny Programmer canvas.
    Outputs commands to stdout that the main process interprets.
    Canvas dimensions come from TINY_CANVAS_W/H env vars set by the
    runtime, falling back to the 480x320 reference size.
    """

    def __init__(self, w=None, h=None):
        self.width = w if w is not None else int(os.environ.get("TINY_CANVAS_W", 416))
        self.height = h if h is not None else int(os.environ.get("TINY_CANVAS_H", 218))
        self._batch_enabled = os.environ.get("TINY_CANVAS_BATCH", "1").lower() not in ("0", "false", "no")
        self._commands = []
        # Flush immediately so animation is smooth
        sys.stdout.reconfigure(line_buffering=True)

    def update(self):
        """Flush and render the current frame."""
        self.show()

    def move(self, *args):
        """Dummy method for compatibility."""
        pass

    def _emit(self, command, *args):
        if self._batch_enabled:
            self._commands.append([command, *args])
            return
        print("CMD:" + ",".join(str(part) for part in (command, *args)))

    def _flush(self):
        if not self._commands:
            return
        print("CMDS:" + json.dumps(self._commands, separators=(",", ":")))
        self._commands = []

    def clear(self, r=0, g=0, b=0):
        """Clear screen with color."""
        self._emit("CLEAR", r, g, b)

    def pixel(self, x, y, r=255, g=255, b=255):
        """Draw a single pixel."""
        self._emit("PIXEL", int(x), int(y), r, g, b)

    def line(self, x1, y1, x2, y2, r=255, g=255, b=255):
        """Draw a line."""
        self._emit("LINE", int(x1), int(y1), int(x2), int(y2), r, g, b)

    def rect(self, x, y, w, h, r=255, g=255, b=255):
        """Draw a rectangle outline."""
        self._emit("RECT", int(x), int(y), int(w), int(h), r, g, b)

    def fill_rect(self, x, y, w, h, r=255, g=255, b=255):
        """Draw a filled rectangle."""
        self._emit("FILLRECT", int(x), int(y), int(w), int(h), r, g, b)

    def circle(self, x, y, radius, r=255, g=255, b=255):
        """Draw a circle outline."""
        self._emit("CIRCLE", int(x), int(y), int(radius), r, g, b)

    def fill_circle(self, x, y, radius, r=255, g=255, b=255):
        """Draw a filled circle."""
        self._emit("FILLCIRCLE", int(x), int(y), int(radius), r, g, b)

    def show(self):
        """Mark the end of a frame so the host can render it cleanly."""
        self._flush()
        print("CMD:FLIP")

    def sleep(self, seconds):
        """Sleep for seconds."""
        self._flush()
        time.sleep(seconds)
