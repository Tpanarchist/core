"""Empty package marker.

Exists solely so pytest's default "prepend" import mode can assign
unique dotted module names to test files that share a basename in
different directories under ``tests/`` (currently:
``tests/architecture/test_import_graph.py`` vs.
``tests/memory/architecture/test_import_graph.py``, and their
``test_import_side_effects.py`` counterparts) instead of raising
"import file mismatch" the first time both are collected in the same
pytest session. Without this file (and the sibling markers in
``tests/architecture/``, ``tests/memory/``, and
``tests/memory/architecture/``), the two pairs collide because
pytest's prepend mode names an unpackaged test module after its bare
filename alone. See docs/memory-passes/04-architectural-closure.md,
Task 7's report, for the full incident writeup.

Deliberately NOT extended to every directory under ``tests/``:
``tests/semantics/``, ``tests/memory/semantics/``,
``tests/memory/integration/``, and ``tests/personal_finance/`` have no
basename collisions with each other or with anything above, and their
test modules rely on pytest's prepend-mode sys.path insertion to
resolve bare sibling imports (e.g. ``from _side_effects import ...``,
``from _memory_side_effects import ...``) that only work when a
directory is left unpackaged. Adding an ``__init__.py`` to any of
those four directories would break those imports. If a future test
file introduces a genuine basename collision against one of them,
add a marker to that specific pair only (as was done here) rather
than packaging every test directory uniformly.
"""
