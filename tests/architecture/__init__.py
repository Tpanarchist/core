"""Empty package marker — disambiguates this directory's test module
names (``test_import_graph``, ``test_import_side_effects``) from
``tests/memory/architecture/``'s identically-named files under
pytest's prepend import mode.

This directory's own files have never collided with anything by
themselves; this marker exists to keep both sides of the
``tests/architecture/`` vs. ``tests/memory/architecture/`` collision
symmetric, so this (Core's own, frozen) side stays protected against
any *future* basename clash the same way the Memory side is, rather
than leaving one half of the pair unpackaged and silently relying on
the other half never adding a same-named file. See
docs/MEMORY_V0_AUDIT.md, "Incident: pytest test-collection basename
collision".
"""
