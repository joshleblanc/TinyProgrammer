"""Presentation helper for typing generated code into the terminal."""

import random
import time


TAB_WIDTH_COLUMNS = 4


class CodeTypingRenderer:
    """
    Type generated code exactly as received, with optional visual shortcuts.

    When skip_indent is enabled, leading spaces and tabs are buffered at the
    start of each line and inserted as one visible indent step.
    """

    def __init__(
        self,
        terminal,
        skip_indent: bool = False,
        delay_range: tuple[float, float] | None = None,
    ):
        self.terminal = terminal
        self.skip_indent = skip_indent
        self.delay_range = delay_range
        self._next_type_time = 0.0
        self._has_unrendered_text = False

        # skip_indent mode has two pieces of state:
        # - whether the current line has only seen leading whitespace
        # - the generated leading whitespace buffered for one bulk render
        self._at_line_start = True
        self._pending_leading_whitespace = ""

    def type_text(self, text: str):
        """Type text to the terminal without returning or changing it."""
        if not self.skip_indent:
            for char in text:
                self._type_char(char)
            return

        for char in text:
            self._type_with_indent_skip(char)

    def finish(self):
        """Flush pending leading whitespace before non-code text is typed."""
        if not self.skip_indent or not self._at_line_start:
            self._flush_render(force=True)
            return

        if self._pending_leading_whitespace:
            self._flush_leading_whitespace(render=False)
            self._at_line_start = False
        self._flush_render(force=True)

    def _type_with_indent_skip(self, char: str):
        """Type one character while batching only leading line whitespace."""
        if self._at_line_start and char in (" ", "\t"):
            self._pending_leading_whitespace += char
            return

        if self._at_line_start:
            self._flush_leading_whitespace(render=char != "\n")
            self._at_line_start = False

        if char == "\n":
            self._type_char(char)
            self._at_line_start = True
            self._pending_leading_whitespace = ""
            return

        self._type_char(char)

    def _flush_leading_whitespace(self, render: bool):
        actual_indent = self._indent_columns(self._pending_leading_whitespace)
        self._type_indent(actual_indent, render=render)
        self._pending_leading_whitespace = ""

    def _type_char(self, char: str, render: bool = True):
        self.terminal.type_char(char, render=False)
        self._has_unrendered_text = True
        if render:
            self._after_type(force=char == "\n")

    def _type_indent(self, columns: int, render: bool = True):
        columns = int(columns)
        if columns <= 0:
            return

        if hasattr(self.terminal, "type_indent"):
            self.terminal.type_indent(columns, render=False)
        else:
            for _ in range(columns):
                self.terminal.type_char(" ", render=False)
        self._has_unrendered_text = True
        if render:
            self._after_type(force=True)

    def _after_type(self, force: bool = False):
        if self.delay_range:
            delay = random.uniform(*self.delay_range)
            now = time.monotonic()
            if self._next_type_time <= 0:
                self._next_type_time = now
            self._next_type_time += delay

        self._flush_render(force=force)
        self._sleep_until_next_type()

    def _flush_render(self, force: bool = False):
        if not self._has_unrendered_text:
            return

        if hasattr(self.terminal, "tick"):
            self.terminal.tick()
        self._has_unrendered_text = False

    def _sleep_until_next_type(self):
        if self._next_type_time <= 0:
            return
        remaining = self._next_type_time - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def _indent_columns(self, line: str) -> int:
        columns = 0
        for char in line:
            if char == " ":
                columns += 1
            elif char == "\t":
                columns += TAB_WIDTH_COLUMNS
            else:
                break
        return columns
