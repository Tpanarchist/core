# Core

A personal foundational Python library, built from an explicit ontology rather than from utility code.

Read in this order:

## Core foundation

1. [`SPECIFICATION.md`](SPECIFICATION.md) — the 16 concepts, the 3 conceptual levels, and what each concept means.
2. [`LAWS.md`](LAWS.md) — the 20 laws those concepts obey.
3. [`ARCHITECTURE.md`](ARCHITECTURE.md) — the Python package derived from the specification: module layout, dependency graph, and the runtime contracts that realize the semantics.

## Memory layer

Derived constructions over Core; a consumer of the closed substrate, not a continuation of Core's own ontology.

1. [`MEMORY_SPECIFICATION.md`](MEMORY_SPECIFICATION.md) — what memory means in this system: five new derived constructions, reused Core vocabulary, and what Memory explicitly does not do.
2. [`MEMORY_LAWS.md`](MEMORY_LAWS.md) — the durable rules Memory obeys, cross-referenced to their adversarial evidence.
3. [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md) — the Python package derived from the specification, including the persistence/codec boundary and the SQLite backend's role.
4. [`MEMORY_ADVERSARIAL_MATRIX.md`](MEMORY_ADVERSARIAL_MATRIX.md) — the failure cases the theory and its implementation must survive.

Status: Core specification and architecture frozen; implementation complete (see `ARCHITECTURE.md`). Memory specification, laws, architecture, and adversarial matrix frozen; implementation proceeding in four checkpointed passes (see `MEMORY_ARCHITECTURE.md`). Pass 1 (semantic constructions) closed. Pass 2 (persistence boundary + in-memory reference store) preregistered (`docs/memory-passes/02-persistence-boundary.md`), implementation starting.
