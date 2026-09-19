"""Harness run in a fresh subprocess by test_import_side_effects.py.

Installs guards against every law-20-prohibited action, then imports the
Core module named on the command line. Exits 0 if the import completed
without triggering a guard. A guard raises, so a nonzero exit (with a
traceback on stderr) means the import performed a prohibited side effect.

Process isolation is deliberate: reload()-based approaches were tried
earlier (see 5140cd3) and rejected — reloading a module in-process while
its dependents still hold pre-reload class references corrupts isinstance
and dataclass-equality semantics. A fresh interpreter has no such state.
"""

from __future__ import annotations

import sys


def _forbid(name: str):
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(f"import-time call to {name} is prohibited (law 20)")

    return _raise


def _install_guards() -> None:
    import os
    import random
    import time
    import uuid

    # Ordinary stdlib imports above happen before the audit hook is armed
    # below, so the harness's own setup is never mistaken for a Core side
    # effect. Guard installation itself performs no filesystem/network/
    # randomness/clock operation.

    # --- identity allocation ---
    uuid.uuid1 = _forbid("uuid.uuid1")
    uuid.uuid4 = _forbid("uuid.uuid4")

    # --- wall clock ---
    time.time = _forbid("time.time")
    time.time_ns = _forbid("time.time_ns")

    # --- monotonic / performance clocks ---
    time.monotonic = _forbid("time.monotonic")
    time.monotonic_ns = _forbid("time.monotonic_ns")
    time.perf_counter = _forbid("time.perf_counter")
    time.perf_counter_ns = _forbid("time.perf_counter_ns")

    # --- randomness ---
    random.random = _forbid("random.random")
    random.randrange = _forbid("random.randrange")
    random.randint = _forbid("random.randint")
    random.choice = _forbid("random.choice")
    random.choices = _forbid("random.choices")
    random.getrandbits = _forbid("random.getrandbits")
    os.urandom = _forbid("os.urandom")

    # --- datetime.datetime.now()/.utcnow() ---
    # datetime.datetime is an immutable C type; its classmethods can't be
    # patched via setattr (see the Pass 2/3 notes on this same limitation).
    # Substitute the module-level *name* with a guarded subclass instead —
    # `from datetime import datetime` inside a freshly-imported Core module
    # then receives this guarded class.
    import datetime as datetime_module

    class GuardedDateTime(datetime_module.datetime):
        @classmethod
        def now(cls, tz: object = None) -> GuardedDateTime:  # noqa: ARG003
            raise AssertionError("import-time call to datetime.datetime.now is prohibited (law 20)")

        @classmethod
        def utcnow(cls) -> GuardedDateTime:
            raise AssertionError(
                "import-time call to datetime.datetime.utcnow is prohibited (law 20)"
            )

    datetime_module.datetime = GuardedDateTime  # type: ignore[misc]

    # --- subprocess / sockets / filesystem mutation / env mutation / writes ---
    _write_mode_chars = frozenset("wax+")
    _write_flag_bits = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC | os.O_EXCL
    _rejected_events = frozenset(
        {
            "subprocess.Popen",
            "socket.__new__",
            "socket.connect",
            "socket.bind",
            "os.mkdir",
            "os.rmdir",
            "os.rename",
            "os.replace",
            "os.remove",
            "os.unlink",
            "os.putenv",
            "os.unsetenv",
        }
    )

    def _audit_hook(event: str, args: tuple[object, ...]) -> None:
        if event == "open":
            path = args[0] if len(args) > 0 else None
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else None
            if isinstance(mode, str) and mode:
                if any(ch in _write_mode_chars for ch in mode):
                    raise AssertionError(
                        f"import-time write-mode open() is prohibited (law 20): "
                        f"{path!r} mode={mode!r}"
                    )
                return  # a read-only mode string is fine
            if isinstance(flags, int) and (flags & _write_flag_bits):
                raise AssertionError(
                    f"import-time write-flag open() is prohibited (law 20): "
                    f"{path!r} flags={flags!r}"
                )
            return
        if event in _rejected_events:
            raise AssertionError(f"import-time {event} is prohibited (law 20): args={args!r}")

    sys.addaudithook(_audit_hook)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: _side_effect_harness.py <module.dotted.path>", file=sys.stderr)
        return 2
    target = sys.argv[1]
    _install_guards()
    __import__(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
