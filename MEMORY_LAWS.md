# Memory — Laws

These are the laws `MEMORY_SPECIFICATION.md` is built to obey. Like Core's own laws, they are not aspirations — where a law has a specific enforcement mechanism, that mechanism is named here and implemented in code. Each law also cites the `MEMORY_ADVERSARIAL_MATRIX.md` sections that pressure-test it, so the audit at architectural closure has a direct trail from rule to evidence rather than needing to re-derive it.

1. Memory never rewrites or deletes a Core fact.
2. Retrieval establishes candidacy, not truth.
3. Similarity is not identity.
4. Recency alone never establishes epistemic precedence.
5. Accessibility is not existence — forgetting reduces default visibility; it never destroys.
6. Attention (`WorkingSet`) is bounded, and overflow is always reported, never silently dropped.
7. Ordering belongs to the construction that owns it.
8. Conflict is surfaced, never adjudicated.
9. A resolved `Contradiction` is not the same as a structured adjudication.
10. Belief projection is keyed by subject *and* predicate, and is granted only where Core's own `Context`/time structure make the answer mechanical.
11. Memory performs no temporal carry-forward.
12. Persistence eligibility is not equivalent to satisfying Core's `Entity` protocol, and persistence never manufactures semantic identity.
13. Unsupported durable values fail explicitly; nothing is silently stringified, pickled, or coerced across the persistence boundary.
14. Lexical indexing never invents searchable text from an arbitrary value.
15. Storage backends do not own semantic policy.
16. Failure to retrieve is not evidence of absence, deletion, or falsehood.

## Notes on the harder-to-enforce laws

**Law 1 — Memory never rewrites or deletes a Core fact.** Enforced structurally: Memory has no delete operation anywhere in its surface, and `RetentionLog` — the only mechanism that changes how an item is treated — is itself append-only, exactly like Core's `ContradictionLog`. Archiving an item changes retrieval's default behavior, never the item's storage.
*Adversarial evidence*: RT-01–RT-12, RR-01–RR-12, X-04.

**Law 2 — Retrieval establishes candidacy, not truth.** `RecallCandidate` carries structured `relevance` evidence but no truth claim; `BeliefProjection` is computed from stored `Claim`/`Contradiction`/`Resolution` state, never from what a retrieval operation happened to surface. A false, lexically-perfect match and a true, poorly-ranked one are handled by entirely separate mechanisms.
*Adversarial evidence*: RC-01–RC-10, MT-01–MT-10, X-03.

**Law 3 — Similarity is not identity.** `RecallCandidate.relevance: tuple[Kind, ...]` names *what kind* of match occurred (identity / lexical / contextual) rather than exposing a bare, backend-specific score that could be mistaken for an identity signal.
*Adversarial evidence*: RC-01–RC-03, RC-05–RC-06.

**Law 4 — Recency alone never establishes epistemic precedence.** `belief_state()` filters by `(subject, predicate)` and `Context` compatibility only; `Claim.at` is never read as a tiebreaker. This is the law the birth-date/account-balance distinction exists to enforce.
*Adversarial evidence*: BP-08, BP-09, CT-01–CT-09, CF-13, X-01, X-02.

**Law 5 — Accessibility is not existence.** `RetentionLog.current()` changes only what default retrieval surfaces; an `ARCHIVED` item remains fully persisted and is retrievable through an explicit archive-inclusive query.
*Adversarial evidence*: RT-10, RR-02–RR-04, X-04.

**Law 8 — Conflict is surfaced, never adjudicated.** `BeliefProjection` never reads `Resolution.rationale` as structured data, under any circumstance — including a rationale that contains machine-looking text naming a winner. `RESOLVED_OPAQUE_CONFLICT` exists specifically to name the state Core's `Resolution` shape cannot resolve mechanically, rather than let Memory guess.
*Adversarial evidence*: CF-01–CF-15, X-02, X-09.

**Law 9 — A resolved `Contradiction` is not the same as a structured adjudication.** Distinguished from an unresolved conflict *and* from a mechanically-determined belief by its own status value (`RESOLVED_OPAQUE_CONFLICT`), rather than being collapsed into either.
*Adversarial evidence*: CF-02, CF-06, CF-12.

**Law 11 — Memory performs no temporal carry-forward.** A direct, verified consequence of Core's own `Context.merge()`: `as_of: WallInstant` has no default and is never `None`, so any two differing `as_of` values fall into `merge()`'s conflict branch rather than its fill-in branch. A `Claim` whose context doesn't match a query's `as_of` is incompatible, full stop — never treated as still applying.
*Adversarial evidence*: CT-01, CT-06–CT-09, X-01.

**Law 12 — Persistence eligibility is not equivalent to Core `Entity`, and persistence never manufactures semantic identity.** `Resolution` and `RetentionMark` are persisted despite carrying no Core `Id`; they receive a storage-local sequence key that is never promoted into an `Id` and is never `Ref`-targetable. No API exists to construct a `Ref` from a storage-local key.
*Adversarial evidence*: PA-01–PA-11, ID-01–ID-06, RF-01–RF-08.

**Law 13 — Unsupported durable values fail explicitly.** `as_persisted_value()` validates against the closed `PersistedValue` domain and raises, naming the exact offending path, on anything outside it — never `repr()`, never `pickle`, never a string fallback.
*Adversarial evidence*: CD-01–CD-24, FL-01–FL-07.

**Law 14 — Lexical indexing never invents searchable text.** Only fields already `str`-typed in Core's own schema, or `object`-typed fields whose `PersistedValue` encoding already happens to be `str`, are indexed — never an implicit `str()`/`repr()` of an arbitrary payload.
*Adversarial evidence*: FT-01–FT-14.

**Law 15 — Storage backends do not own semantic policy.** `belief_state()` and `admit()` are pure functions over data already fetched; a backend's only job is locating candidates. The in-memory reference store (`InMemoryStore`, from Pass 2) and the SQLite backend (Pass 3) are required to produce semantically identical results for the same inputs.
*Adversarial evidence*: BE-01–BE-08, CL-01–CL-11, MS-01–MS-06.

**Law 16 — Failure to retrieve is not evidence of absence, deletion, or falsehood.** A retrieval operation returning no candidates says only that this query didn't surface a match — it is never conflated with "forgotten," "archived," "nonexistent," or "false."
*Adversarial evidence*: RR-10, MT-07, MT-08, X-10.
