"""Empty package marker — intermediate package for
``tests/memory/architecture/``'s marker below.

Required so the dotted module name for files under
``tests/memory/architecture/`` resolves as
``tests.memory.architecture.<name>`` (rooted at the repository root
via ``tests/__init__.py``) rather than stopping at
``memory.architecture.<name>``, which would make bare ``memory``
itself resolve to this test package in ``sys.modules`` and shadow the
real ``memory`` package (``src/memory``) for every other test file
doing ``from memory.<x> import ...``. Do not remove this file without
also removing ``tests/memory/architecture/__init__.py`` and
``tests/__init__.py`` — the three exist as a set. See
docs/MEMORY_V0_AUDIT.md, "Incident: pytest test-collection basename
collision", for the shadowing failure this avoids.
"""
