"""Carry replaceable model-test snapshots alongside ordinary command output."""

from __future__ import annotations

import json
import sys

LIMIT = 200_000


def write_model_test_snapshot(content: str) -> None:
    # A single JSON record keeps multiline ANSI output intact across pipe reads.
    sys.stdout.write("\x1e" + json.dumps({"vela_model_test": content}) + "\n")
    sys.stdout.flush()


class CommandOutput:
    def __init__(self) -> None:
        self.before = ""
        self.snapshot: str | None = None
        self.after = ""
        self.pending = ""

    @property
    def text(self) -> str:
        return (self.before + (self.snapshot or "") + self.after)[-LIMIT:]

    def append(self, text: str) -> None:
        if self.snapshot is None:
            self.before = (self.before + text)[-LIMIT:]
        else:
            self.after = (self.after + text)[-LIMIT:]

    def feed(self, text: str, *, final: bool = False) -> str:
        remaining = self.pending + text
        self.pending = ""
        while remaining:
            plain, marker, record = remaining.partition("\x1e")
            self.append(plain)
            if not marker:
                break
            payload, newline, remaining = record.partition("\n")
            if not newline:
                self.pending = (marker + record)[-LIMIT:]
                break
            try:
                data = json.loads(payload)
            except ValueError:
                data = None
            if isinstance(data, dict) and isinstance(data.get("vela_model_test"), str):
                self.snapshot = data["vela_model_test"][-LIMIT:]
            else:
                self.append(marker + payload + newline)
        if final:
            self.append(self.pending)
            self.pending = ""
        return self.text
