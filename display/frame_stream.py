"""
Shared frame buffer for MJPEG web streaming.

Terminal pushes frames here after each render; the Flask /stream endpoint
reads them to serve a live browser preview.

Encoding is rate-limited and runs off the render thread so slow JPEG work
cannot throttle the physical framebuffer.
"""

import io
import time
import threading

_lock = threading.Condition()
_latest_frame: bytes = b""
_latest_sequence: int = 0
_last_encode_time: float = 0.0
_pending_surface = None
_pending_settings: tuple[float, int] = (1.0, 70)
_encoder_thread: threading.Thread | None = None
_active_clients: int = 0


def _stream_settings() -> tuple[float, float, int]:
    """Read stream settings lazily so dashboard overrides apply at runtime."""
    try:
        import config

        fps = float(getattr(config, "WEB_STREAM_FPS", 4))
        scale = float(getattr(config, "WEB_STREAM_SCALE", 0.5))
        quality = int(getattr(config, "WEB_STREAM_JPEG_QUALITY", 60))
    except Exception:
        fps, scale, quality = 4.0, 0.5, 60

    fps = max(0.1, min(30.0, fps))
    scale = max(0.1, min(1.0, scale))
    quality = max(20, min(95, quality))
    return fps, scale, quality


def register_client() -> None:
    """Track a connected MJPEG client."""
    global _active_clients
    with _lock:
        _active_clients += 1


def unregister_client() -> None:
    """Track a disconnected MJPEG client."""
    global _active_clients, _pending_surface
    with _lock:
        _active_clients = max(0, _active_clients - 1)
        if _active_clients == 0:
            _pending_surface = None


def has_clients() -> bool:
    """Return True when at least one stream client is connected."""
    with _lock:
        return _active_clients > 0


def _ensure_encoder_thread() -> None:
    global _encoder_thread
    if _encoder_thread and _encoder_thread.is_alive():
        return

    _encoder_thread = threading.Thread(
        target=_encoder_loop,
        name="TinyProgrammerFrameStream",
        daemon=True,
    )
    _encoder_thread.start()


def put_frame(surface) -> None:
    """Queue a pygame surface for asynchronous JPEG encoding.

    The render path intentionally does not encode JPEGs. It only copies one
    frame when a stream client is connected and the stream FPS limit allows it.
    """
    global _last_encode_time, _pending_surface, _pending_settings

    now = time.monotonic()
    fps, scale, quality = _stream_settings()
    interval = 1.0 / fps

    with _lock:
        if _active_clients <= 0:
            return
        if now - _last_encode_time < interval:
            return
        if _pending_surface is not None:
            return

        _pending_surface = surface.copy()
        _pending_settings = (scale, quality)
        _last_encode_time = now
        _ensure_encoder_thread()
        _lock.notify()


def get_frame() -> bytes:
    """Return the latest JPEG frame bytes (empty bytes if none yet)."""
    with _lock:
        return _latest_frame


def wait_for_frame(last_sequence: int = 0, timeout: float = 1.0) -> tuple[bytes, int]:
    """Wait for a new frame, returning the latest frame and sequence number."""
    with _lock:
        if _latest_sequence == last_sequence:
            _lock.wait(timeout)
        return _latest_frame, _latest_sequence


def _encoder_loop() -> None:
    global _latest_frame, _latest_sequence, _pending_surface

    while True:
        with _lock:
            while _pending_surface is None:
                _lock.wait()
            surface = _pending_surface
            scale, quality = _pending_settings
            _pending_surface = None

        try:
            frame = _encode_surface(surface, scale, quality)
        except Exception:
            continue

        with _lock:
            _latest_frame = frame
            _latest_sequence += 1
            _lock.notify_all()


def _encode_surface(surface, scale: float, quality: int) -> bytes:
    import pygame
    from PIL import Image

    if scale < 1.0:
        width, height = surface.get_size()
        size = (
            max(1, int(width * scale)),
            max(1, int(height * scale)),
        )
        surface = pygame.transform.scale(surface, size)

    # surfarray gives (w, h, 3); PIL wants (h, w, 3)
    arr = pygame.surfarray.array3d(surface).transpose(1, 0, 2)
    img = Image.fromarray(arr, "RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
