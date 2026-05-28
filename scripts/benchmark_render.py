#!/usr/bin/env python3
"""Benchmark TinyProgrammer display performance on a target device.

The script intentionally does not change application behavior. It reports the
display capabilities it can discover, then times the shared render paths used by
physical framebuffer displays and the dashboard stream.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return

    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        return
    except Exception:
        pass

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def list_paths(path: Path, pattern: str) -> list[str]:
    try:
        return sorted(item.name for item in path.glob(pattern))
    except OSError:
        return []


def discover_framebuffer(device: str) -> dict[str, Any]:
    fb_name = Path(device).name
    sys_fb = Path("/sys/class/graphics") / fb_name
    info: dict[str, Any] = {
        "device": device,
        "exists": Path(device).exists(),
        "sysfs": str(sys_fb),
        "sysfs_exists": sys_fb.exists(),
    }

    for key in (
        "name",
        "virtual_size",
        "bits_per_pixel",
        "stride",
        "mode",
        "modes",
        "rotate",
    ):
        value = read_text(sys_fb / key)
        if value is not None:
            info[key] = value

    return info


def discover_drm() -> dict[str, Any]:
    dev_dri = Path("/dev/dri")
    sys_drm = Path("/sys/class/drm")
    connectors: list[dict[str, str]] = []

    if sys_drm.exists():
        for connector in sorted(sys_drm.glob("card*-*")):
            entry: dict[str, str] = {"name": connector.name}
            for key in ("status", "enabled", "modes"):
                value = read_text(connector / key)
                if value is not None:
                    entry[key] = value
            connectors.append(entry)

    return {
        "dev_dri_exists": dev_dri.exists(),
        "cards": list_paths(dev_dri, "card*"),
        "render_nodes": list_paths(dev_dri, "renderD*"),
        "connectors": connectors,
    }


def probe_sdl_drivers(drivers: list[str], timeout: float) -> dict[str, str]:
    results: dict[str, str] = {}
    probe = (
        "import os, pygame; "
        "pygame.display.init(); "
        "pygame.display.set_mode((16, 16)); "
        "print(pygame.display.get_driver()); "
        "pygame.display.quit()"
    )

    for driver in drivers:
        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = driver
        try:
            completed = subprocess.run(
                [sys.executable, "-c", probe],
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            results[driver] = "timeout"
            continue

        if completed.returncode == 0:
            results[driver] = (completed.stdout.strip().splitlines() or ["ok"])[-1]
        else:
            message = completed.stderr.strip().splitlines()
            results[driver] = message[-1] if message else f"exit {completed.returncode}"

    return results


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def summarize(values: list[float]) -> dict[str, float]:
    avg = statistics.mean(values)
    return {
        "n": len(values),
        "avg_ms": avg,
        "min_ms": min(values),
        "max_ms": max(values),
        "fps": 1000.0 / avg if avg else 0.0,
    }


def bench(name: str, count: int, fn: Callable[[], object], warmup: int = 1) -> dict[str, Any]:
    for _ in range(warmup):
        fn()

    times: list[float] = []
    for _ in range(count):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000.0)

    result = {"name": name, **summarize(times)}
    print_bench(result)
    return result


def print_bench(result: dict[str, Any]) -> None:
    print(
        f"{result['name']}: n={result['n']} "
        f"avg={result['avg_ms']:.1f}ms min={result['min_ms']:.1f}ms "
        f"max={result['max_ms']:.1f}ms fps={result['fps']:.1f}"
    )


def make_surface(pygame: Any, width: int, height: int, lines: int):
    surface = pygame.Surface((width, height))
    surface.fill((255, 255, 255))
    rng = random.Random(7)
    for _ in range(lines):
        pygame.draw.line(
            surface,
            (100, 50, 0),
            (rng.randrange(width), rng.randrange(height)),
            (rng.randrange(width), rng.randrange(height)),
        )
    return surface


def encode_stream_frame(surface, scale: float = 1.0, quality: int = 70) -> bytes:
    import io

    import pygame
    from PIL import Image

    if scale < 1.0:
        width, height = surface.get_size()
        surface = pygame.transform.scale(
            surface,
            (max(1, int(width * scale)), max(1, int(height * scale))),
        )

    arr = pygame.surfarray.array3d(surface).transpose(1, 0, 2)
    image = Image.fromarray(arr.astype("uint8"), "RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def current_sdl_driver(pygame: Any) -> str | None:
    if not pygame.display.get_init():
        return None
    try:
        return pygame.display.get_driver()
    except pygame.error as exc:
        return f"unavailable: {exc}"


def write_framebuffer_frame(writer: Any, surface: Any) -> None:
    if not writer.write(surface):
        raise RuntimeError(f"framebuffer write failed for {writer.device}")


def benchmark_typing(terminal, text: str, count: int) -> dict[str, Any]:
    from programmer.code_typing import CodeTypingRenderer

    times: list[float] = []
    for _ in range(count):
        terminal.clear()
        start = time.perf_counter()
        typing = CodeTypingRenderer(terminal, skip_indent=False, delay_range=None)
        typing.type_text(text)
        typing.finish()
        times.append((time.perf_counter() - start) * 1000.0)

    result = {
        "name": "typing_render_chars",
        "chars": len(text),
        "chars_per_sec": (len(text) * count) / (sum(times) / 1000.0),
        **summarize(times),
    }
    print(
        f"{result['name']}: n={result['n']} chars={result['chars']} "
        f"avg={result['avg_ms']:.1f}ms cps={result['chars_per_sec']:.1f}"
    )
    return result


def benchmark_canvas_commands(terminal, commands: list[str], count: int) -> dict[str, Any]:
    times: list[float] = []
    for _ in range(count):
        terminal.canvas_surface.fill((255, 255, 255))
        start = time.perf_counter()
        for command in commands:
            terminal.process_draw_command(command)
        times.append((time.perf_counter() - start) * 1000.0)

    result = {
        "name": "canvas_command_processing",
        "commands": len(commands),
        "commands_per_sec": (len(commands) * count) / (sum(times) / 1000.0),
        **summarize(times),
    }
    print(
        f"{result['name']}: n={result['n']} commands={result['commands']} "
        f"avg={result['avg_ms']:.1f}ms cps={result['commands_per_sec']:.1f}"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=positive_int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--lines", type=int, default=1000)
    parser.add_argument("--canvas-commands", type=int, default=1000)
    parser.add_argument("--typing-lines", type=int, default=4)
    parser.add_argument(
        "--typing-samples",
        type=positive_int,
        default=1,
        help="typing uses the real tick path, so keep this low on slow displays",
    )
    parser.add_argument("--stream-scale", type=float, default=1.0)
    parser.add_argument("--stream-quality", type=int, default=70)
    parser.add_argument(
        "--probe-sdl",
        action="store_true",
        help="probe SDL drivers in subprocesses; skipped by default",
    )
    parser.add_argument("--sdl-timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="print final JSON report")
    args = parser.parse_args()

    load_env()

    from web.config_manager import ConfigManager

    ConfigManager()

    import config
    import pygame
    from display import frame_stream
    from display.framebuffer import FB_DEVICE, FramebufferWriter, rgb888_to_rgb565
    from display.terminal import Terminal

    capabilities: dict[str, Any] = {
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "display": {
            "profile": getattr(config, "DISPLAY_PROFILE", None),
            "width": getattr(config, "DISPLAY_WIDTH", None),
            "height": getattr(config, "DISPLAY_HEIGHT", None),
            "target_fps": getattr(config, "TARGET_FPS", None),
            "chrome_backend": getattr(config, "DISPLAY_CHROME_BACKEND", None),
            "web_stream_enabled": getattr(config, "WEB_STREAM_ENABLED", None),
        },
        "framebuffer": discover_framebuffer(FB_DEVICE),
        "drm": discover_drm(),
        "sdl": {
            "driver_before_terminal": current_sdl_driver(pygame),
            "current_driver": None,
            "probe": "skipped; pass --probe-sdl to test candidate drivers",
        },
    }
    if args.probe_sdl:
        capabilities["sdl"]["probe"] = probe_sdl_drivers(
            ["kmsdrm", "fbcon", "directfb", "x11", "wayland", "dummy"],
            args.sdl_timeout,
        )

    terminal = Terminal(
        width=config.DISPLAY_WIDTH,
        height=config.DISPLAY_HEIGHT,
        color_bg=config.COLOR_BG,
        color_fg=config.COLOR_FG,
        font_name=config.FONT_NAME,
        font_size=config.FONT_SIZE,
        status_bar_height=config.STATUS_BAR_HEIGHT,
    )
    terminal._min_flip_interval = 0
    capabilities["sdl"]["current_driver"] = current_sdl_driver(pygame)

    surface = make_surface(pygame, config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT, args.lines)
    writer = FramebufferWriter(config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)
    original_stream_enabled = getattr(config, "WEB_STREAM_ENABLED", False)
    results: list[dict[str, Any]] = []

    print("== Capabilities ==")
    print(json.dumps(capabilities, indent=2, sort_keys=True))
    print("== Benchmarks ==")

    try:
        results.append(
            bench(
                "rgb888_to_rgb565",
                args.samples,
                lambda: rgb888_to_rgb565(surface),
                args.warmup,
            )
        )

        framebuffer_write_available = False
        if writer.enabled:
            try:
                write_framebuffer_frame(writer, surface)
                framebuffer_write_available = True
            except RuntimeError as exc:
                print(f"fb_write_full: skipped ({exc})")

        if framebuffer_write_available:
            results.append(
                bench(
                    "fb_write_full",
                    args.samples,
                    lambda: write_framebuffer_frame(writer, surface),
                    args.warmup,
                )
            )
        elif not writer.enabled:
            print("fb_write_full: skipped (framebuffer unavailable)")

        results.append(
            bench(
                "jpeg_encode_equivalent",
                args.samples,
                lambda: encode_stream_frame(
                    surface,
                    scale=args.stream_scale,
                    quality=args.stream_quality,
                ),
                args.warmup,
            )
        )

        def put_stream_frame() -> None:
            if hasattr(frame_stream, "_last_encode_time"):
                frame_stream._last_encode_time = 0.0
            frame_stream.put_frame(surface)

        config.WEB_STREAM_ENABLED = True
        if hasattr(frame_stream, "register_client"):
            frame_stream.register_client()
        try:
            results.append(
                bench(
                    "frame_stream_put_frame",
                    args.samples,
                    put_stream_frame,
                    args.warmup,
                )
            )
        finally:
            if hasattr(frame_stream, "unregister_client"):
                frame_stream.unregister_client()

        terminal.show_canvas()
        canvas_w, canvas_h = terminal.canvas_size
        commands = [
            (
                f"CMD:LINE,{i % canvas_w},{(i * 3) % canvas_h},"
                f"{(i * 7) % canvas_w},{(i * 11) % canvas_h},100,50,0\n"
            )
            for i in range(args.canvas_commands)
        ]
        results.append(benchmark_canvas_commands(terminal, commands, args.samples))

        real_flip = terminal._flip

        def render_composite_only() -> None:
            terminal._flip = lambda force=False: None
            try:
                terminal._render()
            finally:
                terminal._flip = real_flip

        results.append(
            bench(
                "terminal_render_composite_only",
                args.samples,
                render_composite_only,
                args.warmup,
            )
        )

        config.WEB_STREAM_ENABLED = False
        if terminal.fb_writer and not framebuffer_write_available:
            print("terminal_render_display_no_stream: skipped (framebuffer write failed)")
        else:
            results.append(
                bench(
                    "terminal_render_display_no_stream",
                    args.samples,
                    terminal._render,
                    args.warmup,
                )
            )

        typing_text = "".join(
            f"for i in range({line}): print(i)  # line {line}\n"
            for line in range(args.typing_lines)
        )
        if terminal.fb_writer and not framebuffer_write_available:
            print("typing_render_chars: skipped (framebuffer write failed)")
        else:
            results.append(benchmark_typing(terminal, typing_text, args.typing_samples))
    finally:
        config.WEB_STREAM_ENABLED = original_stream_enabled
        if hasattr(writer, "close"):
            writer.close()
        if terminal.fb_writer and hasattr(terminal.fb_writer, "close"):
            terminal.fb_writer.close()
        pygame.quit()

    if args.json:
        print("== JSON ==")
        print(json.dumps({"capabilities": capabilities, "benchmarks": results}, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
