"""Append-only intent journal (plan §5.2).

The journal is the record of *intent*: every command the system sent, its
envelope parameters, the broker acknowledgment, and fills. It is never a
source of truth about positions or orders — that is the broker's job (§5.1).
Its two uses are forensics and TCA: `replay()` reconstructs any session from
the durable record.

Writes go through an explicit write-behind buffer: `append()` only stages an
entry; `flush()` is what makes it durable on disk. That is what makes the
§5.6 chaos scenario real — a crash with a non-empty buffer loses exactly the
buffered entries, and the resync protocol then has to explain the gap between
the durable journal and the broker's truth.

Format: one JSON object per line (JSONL), fields
  {seq, ts, kind, ...payload}
with `seq` a strictly increasing integer that survives restarts (the next
journal instance resumes from the durable maximum). A torn final line (a
crash mid-flush) is tolerated on read: it is dropped, never parsed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IntentJournal:
    def __init__(self, path, clock=utc_now_iso):
        self._path = Path(path)
        self._clock = clock
        self._durable = []          # entries known to be on disk
        self._pending = []          # staged, lost if the process dies now
        self.torn_tail_dropped = False
        if self._path.exists():
            self._load()
        self._next_seq = (self._durable[-1]["seq"] + 1) if self._durable else 1

    def _load(self):
        lines = [ln for ln in self._path.read_text().splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            try:
                self._durable.append(json.loads(line))
            except json.JSONDecodeError:
                # only a trailing partial write is survivable; anything else
                # means the file was edited by hand, which must stay loud
                if i != len(lines) - 1:
                    raise
                self.torn_tail_dropped = True

    # --- writing ------------------------------------------------------------

    def append(self, kind, **payload):
        """Stage an entry. Returns the entry (with its assigned seq). Nothing
        is durable until flush()."""
        entry = {"seq": self._next_seq, "ts": self._clock(), "kind": kind}
        entry.update(payload)
        self._next_seq += 1
        self._pending.append(entry)
        return entry

    def flush(self):
        """Make every staged entry durable (write + fsync)."""
        if not self._pending:
            return 0
        n = len(self._pending)
        with open(self._path, "a") as fh:
            for entry in self._pending:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._durable.extend(self._pending)
        self._pending = []
        return n

    # --- reading --------------------------------------------------------------

    @property
    def durable_seq(self):
        """Seq of the last entry known to survive a crash (0 when empty)."""
        return self._durable[-1]["seq"] if self._durable else 0

    def durable_entries(self):
        return list(self._durable)

    def pending_entries(self):
        return list(self._pending)

    def replay(self):
        """The forensic/TCA view (§5.2): the durable record, in order."""
        return self.durable_entries()
