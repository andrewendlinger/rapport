"""JSON files in the state folder that the server and the CLI both write: each
module's own state (modules/<name>.json, for modules with a server.py), and
feedback.json, the feedback module's.

One discipline for all of them: a write holds a thread lock (the server's
request threads) and an flock on a side lock file (other processes: the CLI,
a second server), reads the file, changes it, and replaces it atomically. A
file's revision is its mtime_ns, so a change made by any process shows up.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from pathlib import Path

from .util import RapportError, atomic_write, ensure_state_dir, state_dir

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

MODULE_STATE_DIR = "modules"
# state kept where it was before modules had their own: reports, the managed .gitignore
# (commit_feedback) and its git notices all know feedback.json by that name
LEGACY = {"feedback": ("feedback.json", "feedback.lock")}
_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_HELD = threading.local()


class Corrupt(RapportError):
    """A state file that can't be read: the server's problem (HTTP 500), not the request's."""


def file_rev(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0


class StateFile:
    """A JSON object in the state folder. read() never creates anything;
    `with f.edit() as data:` changes it under the lock and saves it when the
    block ends without an exception. readonly: edit() raises (GET routes)."""

    def __init__(self, report_dir: Path, name: str, lock: str, readonly: bool = False):
        self.report_dir = Path(report_dir).resolve()
        self.path = state_dir(self.report_dir) / name
        self.lock_path = state_dir(self.report_dir) / lock
        self.readonly = readonly

    @contextlib.contextmanager
    def locked(self):
        """Thread lock + an flock on the lock file, so the server and the CLI
        (agent resolving while the reader comments) never interleave writes."""
        held = _HELD.__dict__.setdefault("paths", set())
        if self.lock_path in held:      # the same thread again would wait on itself forever
            raise RuntimeError(f"{self.path.name} is already being edited: don't nest edits")
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(self.lock_path, threading.Lock())
        with lock:
            ensure_state_dir(self.report_dir)
            self.lock_path.parent.mkdir(exist_ok=True)
            held.add(self.lock_path)
            try:
                with open(self.lock_path, "a+") as fh:
                    if fcntl:
                        fcntl.flock(fh, fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        if fcntl:
                            fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                held.discard(self.lock_path)

    def read(self, missing: dict | None = None) -> dict:
        """The object in the file; `missing` (default {}) if there is no file yet."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {} if missing is None else missing
        except ValueError as e:
            raise Corrupt(f"{self.path} is not valid JSON ({e}); fix or move it") from None
        if not isinstance(data, dict):
            raise Corrupt(f"{self.path} must hold a JSON object; fix or move it")
        return data

    def save(self, data: dict) -> None:
        atomic_write(self.path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def rev(self) -> int:
        return file_rev(self.path)

    @contextlib.contextmanager
    def edit(self):
        if self.readonly:
            raise RuntimeError("GET routes can't change state (they need no token, so any web page "
                               "could call them); register this handler with r.post, r.put, r.patch or r.delete")
        with self.locked():
            data = self.read()
            yield data
            self.save(data)


def module_store(report_dir: Path, name: str, readonly: bool = False) -> StateFile:
    """A module's own state: .rapport/modules/<name>.json (names are [a-z0-9-], safe as file names);
    feedback's is .rapport/feedback.json."""
    file, lock = LEGACY.get(name) or (f"{MODULE_STATE_DIR}/{name}.json", f"{MODULE_STATE_DIR}/{name}.lock")
    return StateFile(report_dir, file, lock, readonly)


class Journal:
    """A file outside the state folder that only grows (the writing log, in a folder beside the
    user config): each line appended under an flock, so two commands never interleave lines.
    The folder and file are made readable by the user only: they hold report text."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _folder(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def append(self, line: str) -> None:
        self._folder()
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(line.rstrip("\n") + "\n")

    def replace(self, text: str) -> None:
        """The whole file at once (a small side file, such as the last digest's date)."""
        self._folder()
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text.rstrip("\n") + "\n")
        os.replace(tmp, self.path)
