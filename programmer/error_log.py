"""Lightweight rolling log of review and runtime errors.

Appends one JSON entry per error to programs/error_log.jsonl, trimmed to
the last MAX_ENTRIES. View with:
    tail -n 20 programs/error_log.jsonl
    grep "AttributeError" programs/error_log.jsonl | wc -l
"""

import json
import os
import time

ERROR_LOG_PATH = os.path.join("programs", "error_log.jsonl")
MAX_ENTRIES = 100


def log_error(program_type: str, stage: str, error_message: str) -> None:
    """Append an error entry, keeping only the last MAX_ENTRIES lines.

    stage: "review" for compile/banned-import failures, "runtime" for
    subprocess errors during WATCH.
    """
    entry = {
        "ts": time.time(),
        "program_type": program_type or "unknown",
        "stage": stage,
        "error": (error_message or "").strip(),
    }
    try:
        os.makedirs(os.path.dirname(ERROR_LOG_PATH) or ".", exist_ok=True)
        existing = []
        if os.path.exists(ERROR_LOG_PATH):
            with open(ERROR_LOG_PATH, "r", encoding="utf-8") as f:
                existing = f.readlines()
        existing.append(json.dumps(entry, ensure_ascii=False) + "\n")
        existing = existing[-MAX_ENTRIES:]
        with open(ERROR_LOG_PATH, "w", encoding="utf-8") as f:
            f.writelines(existing)
    except Exception as e:
        print(f"[ErrorLog] Failed to log: {e}")
