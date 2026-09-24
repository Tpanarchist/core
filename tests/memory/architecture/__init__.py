"""Empty package marker — disambiguates ``test_import_graph`` and
``test_import_side_effects`` here from the identically-named files in
``tests/architecture/`` under pytest's prepend import mode.

These two files are deliberately named to mirror Core's own
``tests/architecture/test_import_graph.py`` /
``test_import_side_effects.py`` (same mechanism, applied one layer up
at the Memory package). Without this marker (plus
``tests/__init__.py`` and ``tests/memory/__init__.py``, which must
exist alongside it — see that file's docstring), pytest cannot
collect both pairs in the same session: "import file mismatch." See
docs/memory-passes/04-architectural-closure.md, Task 7's report.
"""
