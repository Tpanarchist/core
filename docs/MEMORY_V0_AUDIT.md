# Memory v0 — Closure Audit

Mirrors Core's own `docs/V0_AUDIT.md` at the Memory layer. See
`docs/memory-passes/04-architectural-closure.md` for what this pass
covers and why.

As of this audit: **462 tests** under `tests/memory/` (architecture +
semantics + integration), `ruff check src/memory tests/memory` clean,
`pyright src/memory tests/memory` (strict) clean. Every "Test evidence"
citation below was independently confirmed against the real repository
(`grep` for the method name plus a live `uv run pytest <node-id> -v`
run) rather than transcribed from the pass's own pre-researched draft;
a handful of citations in that draft turned out to be missing a class
qualifier, or to cite a matrix ID with no test of that literal name —
those are corrected here and called out in the notes under each table.

## Table A — the five derived constructions

| Construction | Built from | Code home | Enforced invariant | Test evidence | Status |
|---|---|---|---|---|---|
| `Episode` | `Id` + `subject: Id \| Ref` + `Context` + ordered `Ref` items + `WallInstant` open/close | `episode.py` | Caller-supplied `Id` required (never self-allocated); append-only until closed; item order is append-call order, never sorted by wall time (law 7) | `test_episode.py`; SQLite round-trip specifically: `test_sqlite_store.py::TestEpisodeSqliteTransitions::test_es04_append_call_order_survives_reversed_member_timestamps` | Closed |
| `RecallCandidate` | `Ref` + `Context` + `relevance: tuple[Kind, ...]` + `WallInstant` | `recall.py` | `relevance` non-empty, no duplicate Kinds; not `Entity`-bearing (RF-02) | `test_recall.py::TestRecallCandidate` | Closed |
| `WorkingSet` | `capacity: int` + `admitted: tuple[RecallCandidate, ...]` | `recall.py` (`admit()`) | `admitted` never exceeds `capacity`; overflow returned as `excluded`, never silently dropped (law 6); no reranking/scoring — caller order preserved (law 7); not `Entity`-bearing (RF-03) | `test_recall.py::TestWorkingSetAdmission`, `TestWorkingSetConstruction` (incl. `test_rf_03_working_set_not_entity_bearing`, Task 3) | Closed |
| `RetentionMark` / `RetentionLog` | `Ref` + `accessibility: Kind` + `WallInstant` + optional `rationale`, append-only sequence with last-mark-wins projection | `retention.py` | Append-only; `current()` is a projection over append order, never sorted by `at` and never a stored flag (law 5); not `Entity`-bearing (RF-04) | `test_retention.py` (incl. `TestRefClosure::test_rf_04_retention_mark_not_entity_bearing`, Task 3) | Closed |
| `BeliefProjection` | `Id \| Ref` + `Kind` (predicate) + `Context` (query) + `Kind` (status) + `tuple[Claim, ...]` + `tuple[Contradiction \| Resolution, ...]` | `belief.py` (`belief_state()`) | Mechanical-only: never reads `Claim.at` (law 4), never parses `Resolution.rationale` (law 8); keyed by subject *and* predicate (law 10); not `Entity`-bearing (RF-05) | `test_belief.py` (incl. `TestBeliefProjectionConstructorInvariants::test_rf_05_belief_projection_not_entity_bearing`, Task 3) | Closed |

### Supporting machinery (not counted among the five)

| Item | Category | Code home | Test evidence | Status |
|---|---|---|---|---|
| `PersistedValue` | semantic value-domain refinement (same category as Core's own `Kind`/`Maybe`) | `codec.py` | `test_codec.py` | Closed |
| `MemoryStore` (protocol) | Capability-like protocol (same category as Core's own `EffectSink`) | `store.py` | `test_store.py::TestMemoryStoreProtocol` (`isinstance` checks), `test_sqlite_store.py::TestProtocolConformance` | Closed |
| `InMemoryStore` | concrete v0 implementer of `MemoryStore` | `store.py` | `test_store.py` (whole file) | Closed |
| `SqliteMemoryStore` | concrete, durable v0 implementer of `MemoryStore` | `sqlite_store.py` | `test_sqlite_store.py` (whole file) | Closed |

**Correction vs. the pass's draft**: the draft cited the `MemoryStore` protocol row as `test_store.py::TestProtocolConformance`-equivalent coverage. No class named `TestProtocolConformance` exists in `test_store.py` — the real `isinstance(InMemoryStore(), MemoryStore)` check lives in `test_store.py::TestMemoryStoreProtocol::test_in_memory_store_satisfies_protocol` (that class also has a negative case, `test_incomplete_fake_does_not_satisfy_protocol`). `test_sqlite_store.py::TestProtocolConformance` is real and unchanged. Both verified passing.

---

## Table B — the 16 Memory laws

| # | Law | Code enforcement | Test evidence | Status |
|---|---|---|---|---|
| 1 | Memory never rewrites or deletes a Core fact | No delete operation anywhere in Memory's surface; `RetentionLog` is itself append-only | `test_retention.py`, `test_store.py::TestRetrievalRetention` | Closed |
| 2 | Retrieval establishes candidacy, not truth | `RecallCandidate` carries no truth claim; `BeliefProjection` computed independently from stored state | `test_recall.py`, `test_belief.py`; X-03 (`tests/memory/integration/test_cross_module_scenarios.py`, Task 5) | Closed |
| 3 | Similarity is not identity | `RecallCandidate.relevance: tuple[Kind, ...]` names match *kind*, never a bare score | `test_recall.py::TestRecallCandidate` | Closed |
| 4 | Recency alone never establishes epistemic precedence | `belief_state()` filters by subject/predicate/Context only; `Claim.at` never read as a tiebreaker | `test_belief.py::TestContextAndTime` (incl. `test_belief_state_never_inspects_claim_at`); X-01 | Closed |
| 5 | Accessibility is not existence | `RetentionLog.current()` changes only default retrieval visibility; `ARCHIVED` item remains fully persisted | `test_store.py::TestRetrievalRetention::test_rr_02_rr_03_archived_excluded_by_default_included_when_requested`; X-04 | Closed |
| 6 | Attention (`WorkingSet`) is bounded, overflow always reported, never silently dropped | `admit()` returns `(WorkingSet, excluded)` — the excluded tuple IS the report | `test_recall.py::TestWorkingSetAdmission::test_ws_01_capacity_smaller_than_candidates_splits_exactly` | Closed |
| 7 | Ordering belongs to the construction that owns it | `Episode` preserves append-call order, never sorted by wall time; `admit()` performs no reranking | X-07 (`test_sqlite_store.py::TestEpisodeSqliteTransitions::test_es04_append_call_order_survives_reversed_member_timestamps`); `admit()`'s own docstring/tests in `test_recall.py::TestWorkingSetAdmission::test_ws_06_admit_preserves_caller_order_regardless_of_relevance` | Closed |
| 8 | Conflict is surfaced, never adjudicated | `BeliefProjection` never reads `Resolution.rationale` as structured data, ever | `test_belief.py::TestContradiction` (incl. `test_cf_14_differing_authority_values_never_compared_to_each_other`); X-02, X-09 | Closed |
| 9 | A resolved `Contradiction` is not the same as a structured adjudication | `RESOLVED_OPAQUE_CONFLICT` distinguishes from both unresolved conflict and mechanical determination | `test_belief.py::TestContradiction::test_cf_02_cf_03_cf_04_resolution_never_selects_a_winner` | Closed |
| 10 | Belief projection is keyed by subject *and* predicate, granted only where Core's `Context`/time make the answer mechanical | `belief_state()`'s required `subject`/`predicate` params + `Context.merge()`-based compatibility check | `test_belief.py::TestOrdinaryProjection::test_bp_04_different_predicate_ignored`, `test_bp_05_different_subject_ignored`; `TestContextAndTime::test_ct_01_differing_as_of_is_a_context_conflict` | Closed |
| 11 | Memory performs no temporal carry-forward | Direct consequence of `Context.merge()`: differing `as_of` is always a conflict, never a fill-in | `test_belief.py::TestContextAndTime::test_ct_01_differing_as_of_is_a_context_conflict`; X-01 | Closed |
| 12 | Persistence eligibility ≠ Core `Entity`; persistence never manufactures identity | `Resolution`/`RetentionMark` persisted with a storage-local sequence key, never promoted to `Id`/`Ref` | `test_store.py` `PA`/`ID`-series (`TestPersistBasicEntities`, `TestIdentityCollision`, `TestNoGenericEnumerationOrDelete`); full citation-by-case in the Ref-closure audit below (RF-01..08, Task 3) | Closed |
| 13 | Unsupported durable values fail explicitly | `as_persisted_value()` validates against the closed `PersistedValue` domain, raises with exact path | `test_codec.py` `CD`/`FL`-series (25 `CD-*` tests, 7 `FL-*` tests), incl. `TestRejections::test_ip_06_offending_value_itself_is_retained_not_just_its_path` (Task 4) | Closed |
| 14 | Lexical indexing never invents searchable text | Only already-`str`-typed fields (or `object` fields whose encoding happens to be `str`) are indexed | `test_sqlite_store.py::TestFtsIndexing` (14 tests, 6 `FT-*`-named) | Closed |
| 15 | Storage backends do not own semantic policy | `belief_state()`/`admit()` are pure functions over already-fetched data; `InMemoryStore`/`SqliteMemoryStore` required semantically identical | `test_sqlite_store.py::TestBackendEquivalence` (18 tests, incl. `test_unsupported_value_fails_identically_on_both_backends`; its own docstring at line 1437 names this as proving matrix sections O and N/`CL-11` too), `test_store.py::TestClaimsFor`/`TestConflictsFor` (`CL-01..10`); the pure semantic modules' independence from any storage backend (`MS-01`/`MS-02`) is a structural property proved by `test_import_graph.py::ALLOWED_IMPORTS` (`episode`/`recall`/`retention`/`belief`/`codec` have no edge to `memory.store` or `memory.sqlite_store`) | Closed |
| 16 | Failure to retrieve is not evidence of absence, deletion, or falsehood | A no-candidate retrieval result is never conflated with forgotten/archived/nonexistent/false | X-10 (`tests/memory/integration/test_cross_module_scenarios.py::TestX10ForgettingVsFailureToRecall`, Task 5) | Closed |

**Corrections vs. the pass's draft**:
- **Law 7**: the draft's `X-07 (\`test_es04_append_call_order_survives_reversed_member_timestamps\`)` cited a bare method name with no file/class. Filled in as `test_sqlite_store.py::TestEpisodeSqliteTransitions::test_es04_...`, and the `admit()` citation was narrowed from "its own docstring/tests" to the specific test that proves order-preservation (`test_ws_06_admit_preserves_caller_order_regardless_of_relevance`).
- **Law 15**: the draft cited `test_store.py` `(MS-01..06)`. No test in the repository is named after any `MS-*` matrix ID, and `MS-03..06` (storage-swap/corruption/ranking-change scenarios) have no single dedicated test — they are conceptual/architectural claims in `MEMORY_ADVERSARIAL_MATRIX.md`'s section S, not individually testable assertions. What *is* concretely true and verified: `MS-01`/`MS-02` ("pure semantic modules work without SQLite / remain importable without it") are structurally proven by the import graph — `test_import_graph.py::ALLOWED_IMPORTS` shows `episode`, `recall`, `retention`, `belief`, `codec` never depend on `store` or `sqlite_store`. Replaced the overclaim with this real evidence and the real `CL`/`BE` test locations.
- **Law 16**: the draft cited `test_store.py::TestRetrievalRetention (RR-10)`. `TestRetrievalRetention` is real (6 tests, all passing) but none is named or scoped to `RR-10`'s specific scenario ("zero candidates ≠ proof nothing matches"); grepping the whole `tests/memory` tree for `rr_10`, `mt_07`, `mt_08` (the three adversarial IDs `MEMORY_LAWS.md`'s own notes cite for this law) returns no hits — none of those three IDs has a test of that literal name anywhere in the suite. The one test that squarely and explicitly exercises this law is Task 5's new `X-10` test, which persists a claim, marks it `ACTIVE`, issues a query that lexically fails to match, and asserts the empty `()` retrieval result coexists with an unchanged, still-`ACTIVE` `retention_for()` reading — i.e., a failed retrieval is verified not to imply forgetting. Replaced the citation with `X-10` alone rather than a `test_store.py` reference that doesn't actually target this scenario.

---

## Ref-closure audit (matrix section V, RF-01..08)

| Case | Scenario | Result | Evidence |
|---|---|---|---|
| RF-01 | Episode Ref | Valid — Episode carries `Id` | `test_episode.py::TestConstruction` |
| RF-02 | RecallCandidate Ref attempted | Impossible — not `Entity`-bearing | `test_recall.py::TestRecallCandidate::test_rc_not_entity_bearing_in_v0` |
| RF-03 | WorkingSet Ref attempted | Impossible — not `Entity`-bearing | `test_recall.py::TestWorkingSetConstruction::test_rf_03_working_set_not_entity_bearing` (Task 3) |
| RF-04 | RetentionMark Ref attempted | Impossible — not `Entity`-bearing | `test_retention.py::TestRefClosure::test_rf_04_retention_mark_not_entity_bearing` (Task 3) |
| RF-05 | BeliefProjection Ref attempted | Impossible — not `Entity`-bearing | `test_belief.py::TestBeliefProjectionConstructorInvariants::test_rf_05_belief_projection_not_entity_bearing` (Task 3) |
| RF-06 | Resolution persisted | Persistence does not make it Ref-targetable — no `id` field on Core's own `Resolution` | `test_store.py::TestNoGenericEnumerationOrDelete::test_pa_09_10_11_public_surface_matches_protocol_exactly` |
| RF-07 | SQLite local sequence key exists | Does not satisfy Entity — plain `int`, never wrapped | `test_sqlite_store.py::TestRefClosure::test_rf_07_sqlite_local_sequence_key_is_a_plain_int_never_entity` (Task 3) |
| RF-08 | Application wants to reference a non-Entity storage row | Must reference a real Entity — storage position never promoted; confirmed no other public API path exists on either backend | `test_store.py::TestNoGenericEnumerationOrDelete::test_pa_09_10_11_public_surface_matches_protocol_exactly`, `test_sqlite_store.py::TestRefClosure::test_rf_08_sqlite_memory_store_public_surface_matches_protocol_plus_close` (Task 3) |

**Correction vs. the pass's draft**: RF-06 and RF-08 both cited `test_store.py::test_pa_09_10_11_public_surface_matches_protocol_exactly` as a bare (class-less) node id. That method is defined inside `class TestNoGenericEnumerationOrDelete`, and pytest refuses to collect the bare form (`ERROR: not found`) — confirmed by running it directly. Both entries now carry the class-qualified, actually-invokable node id (`... -v` run and passed).

---

## Information-preservation audit (matrix section W, IP-01..10)

Core's own "failure preserves information" principle, applied at the
Memory layer — nothing Memory does silently discards information a caller
might need.

| Case | Scenario | Result | Evidence |
|---|---|---|---|
| IP-01 | WorkingSet over capacity | Excluded returned, never dropped | `test_recall.py::TestWorkingSetAdmission` |
| IP-02 | Contradiction resolved | Original Contradiction preserved | `test_belief.py::TestContradiction` |
| IP-03 | Retention changes | Prior marks preserved (append-only) | `test_retention.py::TestDefaultAndBasicTransitions` |
| IP-04 | Episode closed | Items preserved | `test_store.py::TestEpisodeStoreSurface` |
| IP-05 | Retrieval cannot resolve a referenced Claim | Missing Ref stays explicit (`None`, never fabricated) | `test_store.py::TestPersistBasicEntities::test_resolve_missing_id_returns_none`, `test_sqlite_store.py::TestQueryDelegation::test_resolve_missing_returns_none` |
| IP-06 | Codec encounters unsupported path | Path + offending value retained in the error, not just the path | `test_codec.py::TestRejections::test_ip_06_offending_value_itself_is_retained_not_just_its_path` (Task 4) |
| IP-07 | Identity collision | Existing/attempted identity exposed structurally | `test_store.py::TestExceptions::test_identity_collision_carries_structured_fields_not_stringified_payload` |
| IP-08 | FTS cannot index non-string payload | Payload persists; merely not lexically indexed | `test_sqlite_store.py::TestFtsIndexing::test_ft08_event_bytes_payload_not_indexed`, `TestRecordCodecRoundTrip::test_event_with_bytes_payload_and_no_context_round_trips` |
| IP-09 | Claim incompatible with query Context | Exclusion mechanical; Claim itself unchanged (immutable) | `test_belief.py::TestContextAndTime::test_ct_04_incompatible_non_none_context_field_excludes_claim` |
| IP-10 | Belief conflict opaque after Resolution | Conflict history surfaced, no fabricated winner | `test_belief.py::TestContradiction::test_cf_02_cf_03_cf_04_resolution_never_selects_a_winner` |

**Corrections vs. the pass's draft**: IP-05's `test_store.py` citation and IP-06's `test_codec.py` citation were both bare (class-less) node ids in the draft (`test_resolve_missing_id_returns_none`, `test_ip_06_offending_value_itself_is_retained_not_just_its_path`); both are real methods but nested inside `TestPersistBasicEntities` and `TestRejections` respectively. Qualified with their real classes and re-run to confirm they pass as written.

---

## Cross-module integration scenarios (matrix section X, X-01..10)

| Case | Scenario | Evidence |
|---|---|---|
| X-01 | Changing balance without contradiction | `test_belief.py::TestContextAndTime::test_x_01_changing_balance_without_contradiction` |
| X-02 | Conflicting birth date (AMBIGUOUS → UNRESOLVED_CONFLICT → RESOLVED_OPAQUE_CONFLICT) | `test_belief.py::TestOrdinaryProjection::test_bp_03_two_compatible_claims_no_contradiction_is_ambiguous`, `TestContradiction::test_cf_01_relevant_unresolved_contradiction`, `TestContradiction::test_cf_02_cf_03_cf_04_resolution_never_selects_a_winner` |
| X-03 | Retrieval is not belief | `tests/memory/integration/test_cross_module_scenarios.py::TestX03RetrievalIsNotBelief` (Task 5) |
| X-04 | Archive is not deletion | `test_store.py::TestRetrievalRetention::test_rr_02_rr_03_archived_excluded_by_default_included_when_requested`, `test_sqlite_store.py::TestFtsIndexing::test_archived_item_remains_physically_indexed`, `TestBackendEquivalence::test_archive_then_reactivate_retrieval_equivalent` |
| X-05 | Identity vs namespace | `test_belief.py::TestOrdinaryProjection::test_bp_06_bp_07_subject_matches_across_id_and_namespaced_ref`, `test_retention.py::TestIdentityAcrossNamespaces` |
| X-06 | Unsupported persistence payload | `test_sqlite_store.py::TestBackendEquivalence::test_unsupported_value_fails_identically_on_both_backends` |
| X-07 | Episode ordering defeats wall time | `test_sqlite_store.py::TestEpisodeSqliteTransitions::test_es04_append_call_order_survives_reversed_member_timestamps` |
| X-08 | Cross-predicate conflict | `test_belief.py::TestContradiction::test_x_08_cross_predicate_conflict_still_relevant`, `test_store.py::TestConflictsFor::test_cl_04_cross_predicate_contradiction_still_relevant` |
| X-09 | Resolved and unresolved conflict coexist | `test_belief.py::TestContradiction::test_x_09_resolved_and_unresolved_conflicts_coexist` |
| X-10 | Forgetting vs failure-to-recall | `tests/memory/integration/test_cross_module_scenarios.py::TestX10ForgettingVsFailureToRecall` (Task 5) |

**Correction vs. the pass's draft**: X-02, X-05, and X-08 each cited one or more bare (class-less) method names in `test_belief.py`/`test_store.py`. All are real, passing tests, now qualified with their actual classes (`TestOrdinaryProjection`, `TestContradiction`, `TestConflictsFor`) so each node id is directly `pytest`-invokable as written.

---

## Import/dependency audit (matrix section T, IM-01..10)

`MEMORY_ADVERSARIAL_MATRIX.md` section T's ten import/dependency cases,
checked against `tests/memory/architecture/test_import_graph.py` and
`tests/memory/architecture/test_import_side_effects.py`. Every node id
below was independently collected and run (`uv run pytest <node-id>
-v`, or `--collect-only -q` first for the parametrized cases) rather
than transcribed from the pass's draft.

| Case | Scenario | Required behavior | Evidence |
|---|---|---|---|
| IM-01 | A tier-0 Memory module imports a `memory.*` sibling it doesn't need | Fails | `test_import_graph.py::TestImportEdgesAreAllowed::test_every_import_edge_is_allowed` |
| IM-02 | Any `core.*` module imports `memory.*` | Fails | `test_import_graph.py::TestCoreNeverImportsMemory::test_no_core_module_imports_memory` |
| IM-03 | `belief` imports `sqlite3` (or `sqlite_store`) | Fails | `test_every_import_edge_is_allowed` (`belief`'s `ALLOWED_IMPORTS` entry has no `sqlite_store`/stdlib-`sqlite3` edge) plus the explicit negative seam `TestCriticalNegativeSeams::test_negative_seam[belief-memory.sqlite_store]` |
| IM-04 | `recall` imports `store` | Fails | `TestCriticalNegativeSeams::test_negative_seam[recall-memory.store]` |
| IM-05 | `codec` imports `sqlite3` | Fails | `test_every_import_edge_is_allowed` (`codec`'s `ALLOWED_IMPORTS` entry — `{core.value, core.identity, core.time, core.context}` — has no stdlib/sqlite entry at all) |
| IM-06 | `store` imports `sqlite_store` (the inversion — tier 1 depending on tier 2) | Fails | **No dedicated negative-seam case exists for this exact edge.** `TestCriticalNegativeSeams`'s 14 parametrized cases cover `sqlite_store`'s own forbidden imports and each tier-0 module's forbidden imports of `store`/`sqlite_store`, but none names `store → memory.sqlite_store` specifically. The only coverage is indirect: `store`'s `ALLOWED_IMPORTS` entry does not include `memory.sqlite_store`, so `test_every_import_edge_is_allowed` would fail if `store.py` ever imported it — confirmed by reading both the entry and `store.py`'s actual imports. This is a real, honestly-recorded gap: a genuine dedicated regression case for this specific inversion is missing, not merely under-cited. |
| IM-07 | `sqlite_store` imports only what `MEMORY_ARCHITECTURE.md`'s row for it explicitly lists | Pass | `test_every_import_edge_is_allowed` (`sqlite_store`'s row in `ALLOWED_IMPORTS`) |
| IM-08 | A module exists under `src/memory/` with no entry in the allowed-imports map | Module-inventory test fails | `TestModuleInventory::test_discovered_modules_exactly_match_the_architecture_map` |
| IM-09 | Import-time SQLite connection/file creation | Side-effect test fails | `tests/memory/architecture/test_import_side_effects.py::test_importing_module_has_no_side_effects` (all 8 parametrized modules: `memory`, `memory.belief`, `memory.codec`, `memory.episode`, `memory.recall`, `memory.retention`, `memory.sqlite_store`, `memory.store`) |
| IM-10 | Module import reads clock/UUID/random | Side-effect test fails | Same as IM-09 — one harness (`tests/architecture/_side_effect_harness.py`, Core's own generic script, reused unmodified) guards both nondeterminism sources and filesystem/connection side effects in the same fresh-process run |

All 3 non-parametrized node ids above and all 22 parametrized cases
(14 negative-seam + 8 side-effect) were run directly and pass. IM-06 is
the one case in this section without a dedicated test — noted above
rather than papered over with a citation that doesn't actually name a
test of that scenario.

---

## Import graph manual cross-check

`MEMORY_ARCHITECTURE.md`'s dependency table (the `| Tier | Module | Owns |
Depends on |` table, lines 45-53) was read by eye against
`tests/memory/architecture/test_import_graph.py`'s `ALLOWED_IMPORTS` map
(Task 1). They agree entry-for-entry:

- `episode` → `core.identity`, `core.time`, `core.context` — matches.
- `recall` → `core.identity`, `core.time`, `core.context`, `core.value` — matches.
- `retention` → `core.identity`, `core.time`, `core.value` — matches.
- `belief` → `core.identity`, `core.context`, `core.value`, `core.epistemic`, `core.result` — matches.
- `codec` → `core.value`, `core.identity`, `core.time`, `core.context` — matches.
- `store` → `memory.episode`, `memory.recall`, `memory.retention`, `memory.codec`, plus ten `core.*` modules (`identity`, `time`, `context`, `value`, `epistemic`, `observation`, `event`, `effect`, `provenance`, `error`) — matches.
- `sqlite_store` → `memory.store`, `memory.codec`, `memory.recall`, `memory.retention`, plus the same ten-module `core.*` set (stdlib `sqlite3`/`hashlib`/`hmac`/`os`/`struct` are outside what the AST-based import test tracks, since it only walks `core.*`/`memory.*` targets) — matches. No `memory.episode` edge on either `store` or `sqlite_store`, confirming the stale-entry correction noted below has held.

No drift found. `test_import_graph.py::TestImportEdgesAreAllowed::test_every_import_edge_is_allowed` (confirmed passing) is the mechanical proof this holds for every row simultaneously, not just the ones spot-checked above.

`tests/memory/architecture/test_import_graph.py`'s AST-parsing helper
functions (`_module_key_to_dotted`, `_resolve_from_import`,
`_dynamic_import_targets`, `_add_target`, `_dependency_edges`,
`_root_package_name_imports`, `_wildcard_imports`, etc.) duplicate,
rather than import or share, the equivalent helpers in Core's own
`tests/architecture/test_import_graph.py` (confirmed: both files
define their own near-identical `_module_key_to_dotted`/
`_resolve_from_import`/`_dynamic_import_targets`/`_add_target`
functions independently). This is deliberate, not an oversight: the
preregistration explicitly calls for mirroring Core's own test
structure at the Memory layer, and factoring out a shared helper
module would mean editing Core's already-frozen test tree — out of
scope for this pass.

## Incident: pytest test-collection basename collision

`tests/memory/architecture/test_import_graph.py` and
`tests/memory/architecture/test_import_side_effects.py` share a
basename with their Core-side namesakes,
`tests/architecture/test_import_graph.py` and
`tests/architecture/test_import_side_effects.py`. Before this pass, no
directory under `tests/` had an `__init__.py`, so pytest's default
"prepend" import mode named each unpackaged test module after its bare
filename alone (`test_import_graph`, `test_import_side_effects`) —
with two files of the same name in different directories, the second
one collected raised "import file mismatch" rather than being treated
as a distinct module.

The fix: four empty, docstring-only `__init__.py` markers —
`tests/__init__.py`, `tests/architecture/__init__.py`,
`tests/memory/__init__.py`, `tests/memory/architecture/__init__.py` —
which let pytest resolve each file to a unique dotted module name
(`tests.architecture.test_import_graph` vs.
`tests.memory.architecture.test_import_graph`, and likewise for
`test_import_side_effects`) instead of colliding on the bare filename.
`tests/memory/__init__.py` is additionally required as the
intermediate package for `tests/memory/architecture/__init__.py` to
resolve correctly (otherwise the dotted path would stop at
`memory.architecture.<name>`, making bare `memory` itself resolve to
the test package and shadow the real `src/memory` package in
`sys.modules` for every other test doing `from memory.<x> import
...`).

Per the reviewer's Minor finding #6: `tests/architecture/__init__.py`
specifically is not strictly required for the fix to work — a 3-file
variant without it (`tests/__init__.py`, `tests/memory/__init__.py`,
`tests/memory/architecture/__init__.py`) also resolves the collision,
since only one side of a colliding pair needs a unique dotted name for
pytest to disambiguate both. It was included anyway, deliberately, for
symmetry: leaving Core's own `tests/architecture/` unpackaged would
protect the Memory side against a *future* same-named test file while
leaving Core's side exposed to the identical failure mode, an
asymmetry with no principled justification once the mechanism was
understood.

None of the four markers change which directories are packaged beyond
themselves — `tests/semantics/`, `tests/memory/semantics/`,
`tests/memory/integration/`, and other test directories remain
deliberately unpackaged because their test modules rely on pytest's
prepend-mode `sys.path` insertion to resolve bare sibling imports
(e.g. `from _side_effects import ...`), which only works when a
directory has no `__init__.py`.

## Public-surface audit

`src/memory/__init__.py` is a module docstring pointing at
`MEMORY_SPECIFICATION.md`/`MEMORY_LAWS.md`/`MEMORY_ARCHITECTURE.md` plus
`__version__ = "0.1.0"` — nothing else (verified by reading the file: 7
lines total). `src/memory/py.typed` is present (0-byte PEP 561 marker,
confirmed on disk). Both match `src/core/__init__.py`'s own shape
exactly — same docstring structure, same three-document pointer pattern,
same lone `__version__` assignment, and `src/core/py.typed` is likewise a
0-byte marker file.

## Frozen-document consistency audit

Narrow, factual corrections only — never a semantic change. As of this
pass: `MEMORY_ARCHITECTURE.md`'s `sqlite_store` dependency row was
already corrected once during Pass 3's closure — commit `18fb583`
("Memory Pass 3: adjudicate scoped re-review findings") removed a stale
`episode` entry, since `sqlite_store.py` reaches Episode transitions
entirely through `InMemoryStore`'s own public API and never imports
`memory.episode` directly. Confirmed via `git log`/`git show` that this
commit exists and made exactly that change. No other row has drifted the
same way — the import graph cross-check above (entry-for-entry manual
comparison, plus `test_every_import_edge_is_allowed` passing) is the
mechanical proof this holds for every row simultaneously.

`MEMORY_SPECIFICATION.md`'s "Derived constructions" section (lines
167-177) was checked against Table A above and found to already state,
verbatim, the same five constructions with the same "Built from" shapes
— no correction needed.

---

## Closure summary

- **462/462** tests pass under `tests/memory/` (`uv run pytest
  tests/memory/ -q`).
- `ruff check src/memory tests/memory` — all checks passed.
- `pyright src/memory tests/memory` — 0 errors, 0 warnings, 0
  informations.
- **824/824** tests pass under the full scoped repository gate
  (`uv run pytest --ignore=tests/personal_finance -q`); `ruff check
  src/core src/memory tests/architecture tests/memory` and `pyright
  src/core src/memory tests/architecture tests/memory` are both clean
  (0 errors, 0 warnings, 0 informations) — re-run and confirmed as part
  of this fix wave.
- Every citation in Tables A and B, the Ref-closure audit, the
  information-preservation audit, the cross-module scenarios table, and
  the import/dependency audit was independently re-verified against the
  live repository (not transcribed from the pass's pre-researched
  draft); corrections made along the way are called out in the note
  under each table/section above rather than silently folded in.
- Import graph, public package surface, and frozen-document consistency
  all confirmed against actual source, not merely re-stated from the
  planning documents.

---

## Closure checklist

Ports `docs/memory-passes/04-architectural-closure.md`'s §6 checklist,
each line verified against the finished implementation and checked off
with evidence — mirroring `docs/V0_AUDIT.md`'s own closure checklist
format at the Core layer.

```text
[x] All Passes 1-3 tests still pass (778 at the start of this pass),
    plus Pass 4's own additions. (462 passed under tests/memory/;
    824 passed under the full scoped repository gate)
[x] Import graph exactly obeys MEMORY_ARCHITECTURE.md's dependency table
    (both memory.* and core.* edges); actual memory.*-only subgraph is
    acyclic; every IM-01..08 negative/inventory seam holds, except
    IM-06 which has no dedicated negative-seam test (see "Import/
    dependency audit" above -- an honestly-recorded gap, not a failure).
    (test_import_graph.py::TestImportEdgesAreAllowed,
    TestGraphAcyclicity, TestCriticalNegativeSeams, TestModuleInventory,
    TestCoreNeverImportsMemory -- all passing)
[x] Every Memory module passes fresh-process import-side-effect
    verification (IM-09, IM-10), reusing
    tests/architecture/_side_effect_harness.py.
    (tests/memory/architecture/test_import_side_effects.py, 8 modules,
    all passing)
[x] Ruff clean; Pyright strict clean -- core + memory + their tests +
    tests/architecture (tests/personal_finance excluded, unrelated).
    (uv run ruff check src/core src/memory tests/architecture
    tests/memory -- all checks passed; uv run pyright src/core
    src/memory tests/architecture tests/memory -- 0/0/0)
[x] All five derived constructions + PersistedValue/MemoryStore (+
    InMemoryStore/SqliteMemoryStore as their v0 implementers) audited to
    a code/test home. (Table A above)
[x] All 16 Memory laws have concrete code enforcement + test evidence.
    (Table B above)
[x] Every Ref-targetable Memory construction is Entity-bearing (Episode);
    every non-Ref-targetable one confirmed not constructible as a Ref
    target, including SQLite's own storage-local sequence key (RF-01..08).
    (Ref-closure audit above)
[x] Core's information-preservation principle holds at the Memory layer
    for all ten IP-01..10 cases, with test evidence. (Information-
    preservation audit above)
[x] All ten X-01..10 cross-module scenarios have test evidence, existing
    or newly written. (Cross-module integration scenarios table above;
    X-03's test was corrected in this fix wave -- tests/memory/
    integration/test_cross_module_scenarios.py -- to genuinely produce
    and exclude a second, true candidate, rather than trivially pass
    with only one candidate ever returned)
[x] memory.__init__ remains intentionally small; py.typed present.
    (Public-surface audit above)
[x] Frozen documents (MEMORY_ARCHITECTURE.md's dependency table
    specifically) agree factually with the finished implementation.
    (Frozen-document consistency audit above)
[x] Working tree clean after the checkpoint commit. (confirmed via
    `git status` after this fix wave's closing commit)
```

When every box is satisfied, Memory v0 is closed. No further
implementation pass follows; subsequent work (a fifth pass, or an
application layer like `personal_finance` gaining a Memory integration)
builds through this substrate rather than reopening it -- exactly the
posture Core's own v0 closure established for Memory itself.

**Memory v0 is closed.** Five derived constructions, sixteen laws, no
new ontology — built entirely on Core's already-closed sixteen concepts,
eighteen derived constructions, and twenty laws.
