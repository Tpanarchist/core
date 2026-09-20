# Memory v0 — Adversarial Test Matrix

## Purpose

This matrix was written before implementation so that Memory's architecture is constrained by failure cases rather than by whichever storage/retrieval implementation happens to be easiest — the same discipline Core's propositions provided before its own passes began. `MEMORY_SPECIFICATION.md` and `MEMORY_LAWS.md` each cite sections of this matrix as their adversarial evidence; this document is where that evidence actually lives, and where implementation passes 1–4 (`MEMORY_ARCHITECTURE.md`) find the exact cases they must satisfy.

The suite attacks five boundaries:

```text
Core facts ≠ Memory projections
Storage ≠ Memory
Retrieval ≠ Truth
Recency ≠ Authority
Accessibility ≠ Deletion
```

A design that passes ordinary happy-path tests but collapses any of these distinctions is not acceptable.

---

# A. Episode adversaries

| ID | Scenario | Required behavior | Forbidden shortcut |
|---|---|---|---|
| EP-01 | Episode contains an `Event` whose Core representation has no subject | Episode preserves the Ref and asserts its own `subject` grouping | Inferring/adding a subject to Event |
| EP-02 | Observation subject differs from Episode subject | Preserve member unchanged; Episode grouping remains its own assertion | Rewriting member subject |
| EP-03 | Two identical Refs appended | Preserve both positions | Deduplicate automatically |
| EP-04 | Members' WallInstants are reverse chronological order | `items()` preserves append order exactly | Sorting by `at` |
| EP-05 | Two members have identical WallInstant | Append order remains unambiguous | Treat timestamps as authoritative ordering |
| EP-06 | Append after `.close()` | Raise | Silent no-op or reopen |
| EP-07 | `.close()` called twice | Raise | Overwrite `closed_at` |
| EP-08 | `close_at < opened_at` | Raise | Accept inconsistent interval |
| EP-09 | Episode constructed then immediately closed with equal timestamp | Valid | Artificial minimum-duration requirement |
| EP-10 | Caller mutates an input list used to construct initial refs, if initial refs are accepted | Episode contents unchanged | Holding mutable caller container |
| EP-11 | Ref A and differently-namespaced Ref B point to same Id | Preserve exact references as supplied | Normalize away namespaces |
| EP-12 | Episode contains Ref to another Episode | Preserve opaque Ref | Automatic recursive expansion |
| EP-13 | Episode contains Ref to itself | Preserve opaquely — **frozen v0 rule**, not merely default | Infinite traversal |
| EP-14 | Old `items()` snapshot retained, later append occurs | Old snapshot unchanged | Returning internal list |
| EP-15 | Episode is retrieved by Ref | Its own `id` makes it a valid Entity target | Special-case non-Ref Episode access |

Frozen v0 rule for EP-13: Episode groups references; it does not recursively interpret them, so a cycle is not inherently erroneous — self/nested references are preserved opaquely, never rejected.

---

# B. RecallCandidate adversaries

| ID | Scenario | Required behavior | Forbidden shortcut |
|---|---|---|---|
| RC-01 | Candidate surfaced by exact identity match | `relevance` contains the appropriate identity-match Kind | Bare relevance float |
| RC-02 | Candidate surfaced only lexically | Lexical Kind remains distinct from identity Kind | Treating lexical similarity as identity |
| RC-03 | Same item surfaced by identity and lexical match | Both evidence kinds may be represented | Collapse match mechanisms into one score |
| RC-04 | Candidate's `retrieved_at` is newer than another candidate | Does not imply epistemic precedence | Recency-as-truth |
| RC-05 | Candidate has stronger backend rank | Rank affects ordering before construction/admission only | Persisting backend-specific BM25/cosine score into semantic type |
| RC-06 | SQLite and future vector backend surface same item differently | Both can produce the same backend-neutral RecallCandidate form | Backend-specific fields in RecallCandidate |
| RC-07 | Candidate points to archived item because caller explicitly requested archived material | Candidate remains valid | Treating ARCHIVED as deletion |
| RC-08 | Candidate points to an entity Memory doesn't currently have locally | Ref may still be preserved if retrieval operation legitimately produced it | Assuming Ref implies local object availability |
| RC-09 | `relevance=()` | Reject — **frozen v0 rule** | Meaningless surfaced candidate |
| RC-10 | Duplicate relevance Kinds | Reject — **frozen v0 rule**; never accidentally multiply evidence | Treating duplicate labels as stronger relevance |

Frozen v0 rule: `relevance` is non-empty and contains no duplicate Kinds; caller-provided order is preserved but carries no ranking semantics.

---

# C. WorkingSet / attention adversaries

| ID | Scenario | Required behavior | Forbidden shortcut |
|---|---|---|---|
| WS-01 | Capacity smaller than candidate count | Admit prefix; return exact suffix separately | Silent dropping |
| WS-02 | Capacity equals candidate count | Admit all, excluded empty | Off-by-one loss |
| WS-03 | Capacity exceeds candidate count | Admit all | Padding/fabricated candidates |
| WS-04 | Capacity = 0 | Valid empty WorkingSet; all candidates excluded | Artificial minimum of 1 |
| WS-05 | Capacity < 0 | Reject | Python slicing semantics accidentally accepted |
| WS-06 | Candidates are intentionally ordered poorly | `admit()` preserves order anyway | Hidden reranking |
| WS-07 | Same item occurs twice in candidate sequence | `admit()` does not deduplicate | Inventing retrieval policy |
| WS-08 | Candidate A is identity match but placed after lexical candidate B | Input order remains authoritative to `admit()` | Relevance-Kind interpretation inside WorkingSet |
| WS-09 | Caller mutates original candidate collection after admission | WorkingSet unchanged | Retaining mutable container |
| WS-10 | Old WorkingSet exists while a later retrieval occurs | Old snapshot remains unchanged | WorkingSet as mutable global attention |
| WS-11 | Excluded candidates later become relevant | They remain available to caller because exclusion was explicit | Exclusion interpreted as forgetting |

Critical invariant:

```text
admit() bounds attention.
It does not decide relevance.
```

---

# D. Retention adversaries

| ID | Scenario | Required behavior | Forbidden shortcut |
|---|---|---|---|
| RT-01 | No marks exist for item | `current()` returns ACTIVE | UNKNOWN/deleted default |
| RT-02 | ACTIVE → DEPRIORITIZED | Current projection is DEPRIORITIZED | Mutation of prior mark |
| RT-03 | ACTIVE → ARCHIVED → ACTIVE | Current is ACTIVE; all marks preserved | Destructive archive |
| RT-04 | Later-appended mark has earlier `at` timestamp | Later append wins | Sorting by WallInstant |
| RT-05 | Mark applied through namespaced Ref A, queried through Id | Same underlying entity is affected | Raw Ref equality |
| RT-06 | Mark applied through Ref A, queried through differently-namespaced Ref B | Same underlying entity accessibility | Namespace bypass |
| RT-07 | Two exact duplicate marks | Both append facts may remain | Automatic log dedupe |
| RT-08 | Custom accessibility Kind | Preserve/return exact Kind | Closed enum behavior |
| RT-09 | Backend does not understand custom Kind | It must not silently reinterpret it as ACTIVE/ARCHIVED | Guessing policy |
| RT-10 | Archived record queried explicitly | Record remains recoverable | Physical deletion |
| RT-11 | RetentionMark rationale absent | Valid | Requiring explanation where not specified |
| RT-12 | Earlier snapshot of log entries retained | Snapshot unchanged after more marks | Exposing internal list |

Important separation:

```text
Retention answers accessibility.
It does not answer truth, correctness, or importance.
```

---

# E. BeliefProjection — ordinary projection adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| BP-01 | No matching Claim, no relevant contradiction | UNKNOWN |
| BP-02 | Exactly one compatible Claim, no relevant contradiction | DETERMINED |
| BP-03 | Two compatible Claims, no contradiction | AMBIGUOUS |
| BP-04 | Same subject, different predicate | Different slot; ignored |
| BP-05 | Same predicate, different subject | Different slot; ignored |
| BP-06 | Claim subject is Id, query subject is Ref of same Id | Match using `identity_of()` |
| BP-07 | Query subject Ref has different namespace but same Id | Subject matches underlying identity |
| BP-08 | Same slot, later Claim exists but both survive context compatibility | AMBIGUOUS; never latest-wins |
| BP-09 | Claim older than another but only older Claim is context-compatible | Older compatible Claim may be DETERMINED |
| BP-10 | Single Claim contains `value=UNKNOWN` | Projection status is DETERMINED; Claim's epistemic value remains Unknown |
| BP-11 | No Claim survives context filtering | UNKNOWN |
| BP-12 | Two semantically identical Claims with distinct Ids survive | AMBIGUOUS unless explicit epistemic machinery says otherwise |
| BP-13 | Same Claim object supplied twice | Preregistration must define whether input is normalized by Claim identity; never let accidental duplication fabricate epistemic ambiguity |

BP-10 is especially important:

```text
BeliefProjection.UNKNOWN
```

means:

> Memory cannot mechanically project a Claim for this slot.

It does **not** mean:

```text
Claim(value=Core.UNKNOWN)
```

Those are different dimensions.

---

# F. BeliefProjection — Context/time adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| CT-01 | Claim `as_of=Monday`, query `as_of=Friday` | Context conflict; Claim does not carry forward |
| CT-02 | Same `as_of`, all other fields equal | Compatible |
| CT-03 | Claim optional Context field populated, query field `None` | `Context.merge()` compatibility determines result |
| CT-04 | Claim and query contain incompatible non-None source | Exclude Claim |
| CT-05 | Contexts differ only in metadata | Existing Core merge semantics decide; Memory adds no custom merge |
| CT-06 | Query asks "current" without explicit temporal Context | Memory cannot invent now/carry-forward; caller must supply actual Context |
| CT-07 | Two balances on different `as_of` values | They do not compete in a single exact-Context projection unless caller explicitly supplies a higher policy |
| CT-08 | A timeless-ish fact has two claims at different times | Memory still does not invent "latest wins" semantics |
| CT-09 | Caller wants historical carry-forward | Must be implemented above Memory as predicate/domain policy, not hidden in `belief_state()` |

Memory v0 therefore has an explicit theorem-like consequence:

```text
Claim recency alone is never sufficient evidence of supersession.
```

---

# G. BeliefProjection — contradiction adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| CF-01 | Relevant unresolved Contradiction | UNRESOLVED_CONFLICT |
| CF-02 | Relevant Contradiction has later Resolution | RESOLVED_OPAQUE_CONFLICT |
| CF-03 | Resolution rationale says "Claim B wins" | Still RESOLVED_OPAQUE_CONFLICT |
| CF-04 | Resolution rationale includes machine-looking JSON naming a winner | Still opaque; never parsed |
| CF-05 | Multiple relevant Contradictions, one unresolved | UNRESOLVED_CONFLICT takes precedence |
| CF-06 | Multiple relevant Contradictions, all resolved | RESOLVED_OPAQUE_CONFLICT |
| CF-07 | Contradiction subject matches but none of its statement Refs touch a Claim in requested predicate slot | Not relevant to that slot |
| CF-08 | One statement touches requested predicate; another statement uses different predicate | Relevant conflict |
| CF-09 | Contradiction references candidate through namespaced Ref | Match by underlying Claim Id |
| CF-10 | Candidate set contains one Claim but a relevant unresolved Contradiction exists | UNRESOLVED_CONFLICT, not DETERMINED |
| CF-11 | Candidate set currently empty but relevant conflict history is supplied | Conflict status may still be surfaced; candidate cardinality does not define conflict existence |
| CF-12 | Resolved contradiction later receives another Resolution | Preserve all Resolution entries in log order; still outcome-opaque |
| CF-13 | Conflicting Claims have different timestamps | Memory does not pick newer |
| CF-14 | One source appears "more authoritative" in Context | Unless Core facts make selection mechanically unambiguous, Memory does not judge authority |
| CF-15 | Free-text rationale contradicts Resolution metadata | Structured fields govern structure; rationale remains opaque text |

Status precedence:

```text
relevant unresolved contradiction
    → UNRESOLVED_CONFLICT

else relevant resolved contradiction history
    → RESOLVED_OPAQUE_CONFLICT

else >1 compatible Claim
    → AMBIGUOUS

else exactly 1
    → DETERMINED

else
    → UNKNOWN
```

---

# H. Codec domain adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| CD-01 | `None` | Round-trip exactly |
| CD-02 | `False` and integer `0` | Remain distinguishable |
| CD-03 | `True` and integer `1` | Remain distinguishable |
| CD-04 | Large positive Python int beyond SQLite 64-bit range | **Frozen v0 rule** (round 5): round-trips exactly, via a canonical width-independent encoding — never truncated, never delegated to SQLite's native `INTEGER` |
| CD-05 | Large negative Python int | Same |
| CD-06 | Unicode string | Preserve exact code points |
| CD-07 | Two canonically equivalent but differently encoded Unicode strings | Do not silently normalize |
| CD-08 | Arbitrary bytes including `b"\x00"` | Round-trip exactly |
| CD-09 | Nested tuples | Round-trip structure exactly |
| CD-10 | Mapping with keys supplied in different iteration order | Canonical encoding identical |
| CD-11 | Mapping contains a list | Reject with exact path |
| CD-12 | Mapping contains unsupported custom object three levels deep | Error identifies precise path |
| CD-13 | Tuple contains unsupported object | Error identifies tuple index |
| CD-14 | Mapping key is non-string via runtime bypass/custom Mapping | Reject |
| CD-15 | Recursive/cyclic container | Reject deterministically rather than recurse forever |
| CD-16 | Same mutable mapping is validated then mutated | Persisted snapshot must not silently change |
| CD-17 | `Id` passed as arbitrary domain payload | Reject unless field is being encoded through the explicit Core-primitive encoder |
| CD-18 | `Context.scope=Id(...)` in object-typed Context field | Reject under v0 unless explicitly encoded by future earned domain policy |
| CD-19 | `repr()`-able custom object | Reject; repr is not persistence |
| CD-20 | Pickleable object | Reject; pickleability is irrelevant |
| CD-21 | `Decimal` | Reject in v0 unless explicitly added later |
| CD-22 | dataclass with PersistedValue-shaped fields | Reject; no structural auto-conversion |
| CD-23 | `list[str]` | Reject even though JSON could encode it; PersistedValue requires tuple |
| CD-24 | Mapping implementation changes after validation | Durable encoding uses validated snapshot, not live caller object |

Frozen consequence of CD-16/CD-24:

`as_persisted_value()` produces a defensive recursive snapshot of valid container data rather than merely handing the caller's mutable Mapping back unchanged.

That is still not semantic coercion: values retain their existing PersistedValue meanings; the boundary only freezes container structure.

---

# I. Float adversaries

`float` is the one PersistedValue member that can undermine deterministic encoding unless explicitly constrained.

| ID | Scenario | Required behavior |
|---|---|---|
| FL-01 | ordinary finite float | Deterministic round-trip |
| FL-02 | `-0.0` | **Frozen rule: sign preserved exactly** — round-trips as `-0.0`, never canonicalized to `0.0` |
| FL-03 | `inf` | **Frozen rule: rejected** |
| FL-04 | `-inf` | **Frozen rule: rejected** |
| FL-05 | `nan` | **Frozen rule: rejected** — moot once non-finite values are rejected outright |
| FL-06 | two NaN payloads | N/A — NaN is rejected at the boundary, not persisted |
| FL-07 | same finite float encoded on repeated runs | Byte-for-byte canonical output |

Frozen v0 rule:

```text
PersistedValue float must be finite.
```

`inf`, `-inf`, and `nan` are rejected outright. Finite values, including `-0.0`, are preserved exactly — IEEE754 encoding preserves sign for free, so preservation costs nothing extra while canonicalizing `-0.0` away would require added special-case logic purely to discard information. This is simpler and avoids inventing semantics for non-finite payloads before any real Memory use requires them.

---

# J. Core primitive codec adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| CP-01 | Kind | Exact round-trip |
| CP-02 | Id | Preserve Kind + value |
| CP-03 | Namespace | Preserve exact segment tuple |
| CP-04 | Ref without namespace | Exact round-trip |
| CP-05 | Ref with namespace | Preserve namespace |
| CP-06 | WallInstant non-UTC aware input already canonicalized by Core | Persist canonical UTC representation |
| CP-07 | Duration zero | Round-trip |
| CP-08 | Very large Duration | No truncation |
| CP-09 | Context with only required `as_of` | Round-trip |
| CP-10 | Context with every optional PersistedValue-compatible field populated | Round-trip |
| CP-11 | Context metadata iteration order differs | Canonical encoding identical |
| CP-12 | Context contains unsupported arbitrary object | Loud failure with field path |
| CP-13 | Decode malformed Kind/Id representation | Loud decode error |
| CP-14 | Decode unknown codec version/tag | Loud failure, no guess |
| CP-15 | Encode → decode → encode | Canonical bytes/representation stable |

---

# K. Persistence-admissibility adversaries

Persistence eligibility must be an explicit union, not `Entity`.

| ID | Scenario | Required behavior |
|---|---|---|
| PA-01 | Persist supported Entity-bearing Observation | Success |
| PA-02 | Persist supported non-Entity Resolution | Success |
| PA-03 | Persist supported non-Entity RetentionMark | Success |
| PA-04 | Persist arbitrary Core Entity not in admissible union | Reject |
| PA-05 | Persist arbitrary user class with `.id` | Reject |
| PA-06 | Persist unsupported Core record | Reject explicitly |
| PA-07 | Persist supported record containing unsupported payload | Codec failure propagates |
| PA-08 | Persist record causes no mutation to source object | Required |
| PA-09 | Non-Entity row receives SQLite sequence key | Key remains storage-local |
| PA-10 | Retrieve non-Entity row | Does not magically gain Core Id |
| PA-11 | Attempt to create Ref to storage sequence id | No API exists to do so |

**Frozen Pass-2 union** (`MEMORY_ARCHITECTURE.md`, `docs/memory-passes/02-persistence-boundary.md` §2):

```text
type EntityMemoryRecord = (
    Observation[object] | Claim[object] | Inference[object] | Contradiction
    | Event | Effect | Provenance | Error | Episode
)

type NonEntityMemoryRecord = Resolution | RetentionMark

type MemoryRecord = EntityMemoryRecord | NonEntityMemoryRecord

type PersistRecord = (
    Observation[object] | Claim[object] | Inference[object] | Contradiction
    | Resolution | Event | Effect | Provenance | Error | RetentionMark
)
```

`PersistRecord` — every `MemoryRecord` except `Episode` — is what generic `persist()` accepts; `Episode` uses the dedicated `create_episode`/`append_episode`/`close_episode` surface instead (matrix section M covers its transition semantics). No other Core or user type is admitted in Memory v0. The `MEMORY_ARCHITECTURE.md` dependency table now carries this union as a frozen (non-provisional) row.

Two admitted structures embed other admissible Entity records directly and pull them into the store atomically with their parent: `Inference.conclusion → Claim`, `Error.cause → Error | None`. `Error.exception` (a foreign `BaseException`) has no Memory v0 codec — an `Error` with `exception is not None` is rejected outright (`UnsupportedPersistedValue`), never reduced to a type name, message, `repr()`, or pickle.

---

# L. Identified-record collision adversaries

A semantic Id must never become an overwrite key.

| ID | Scenario | Required behavior |
|---|---|---|
| ID-01 | First record with Id X | Insert |
| ID-02 | Exact same record persisted again | **Frozen policy: idempotent success** |
| ID-03 | Different representation with same Id X | Reject identity collision |
| ID-04 | Event A and Event B share Id but differ payload; Core Event equality says equal by Id | Persistence must still detect representation conflict |
| ID-05 | Two different concrete admissible record types somehow use same `(kind,value)` Id | Reject unless architecture explicitly scopes tables/type identity; never silently treat as unrelated |
| ID-06 | Existing identified record retrieved after failed conflicting insert | Original unchanged |

Frozen v0 policy:

```text
same Id + canonically identical record
    → idempotent success

same Id + different canonical record
    → explicit collision error
```

That makes retrying persistence safe without permitting mutation-by-upsert.

---

# M. Episode SQLite-transition adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| ES-01 | Create Episode | Header exists, no items |
| ES-02 | Append first item | Position 0 |
| ES-03 | Append duplicate Ref | New position, preserved |
| ES-04 | Append refs with reversed member timestamps | Stored append order unchanged |
| ES-05 | Close Episode | `closed_at: None → time` once |
| ES-06 | Append after persisted close | Reject transactionally |
| ES-07 | Close twice | Reject |
| ES-08 | Close before opened_at | Reject |
| ES-09 | Failure during append transaction | No partial position/header corruption |
| ES-10 | Database reopen | Episode and exact item ordering preserved |
| ES-11 | Manually corrupted position sequence | Decode/read fails loudly or reports store corruption; never silently reorder |
| ES-12 | Persist Episode snapshot over existing Episode | Not used as mutation mechanism |

---

# N. Contradiction lookup adversaries

`Contradiction` has no predicate, so `conflicts_for(subject, predicate)` must derive relevance through referenced Claims.

| ID | Scenario | Required behavior |
|---|---|---|
| CL-01 | Contradiction subject differs | Exclude |
| CL-02 | Subject matches but no statement resolves to requested predicate | Exclude |
| CL-03 | One statement resolves to requested predicate | Include |
| CL-04 | Only the other conflicting Claim uses another predicate | Still include because requested-slot Claim participates |
| CL-05 | Statement Ref points to missing Claim | Do not fabricate predicate |
| CL-06 | One statement missing, another resolves to requested slot | Include with preserved unresolved reference |
| CL-07 | Same Claim referenced through namespace | Identity resolution still matches |
| CL-08 | Resolution follows included Contradiction | Return Resolution in conflict history |
| CL-09 | Multiple Resolutions | Preserve append order |
| CL-10 | Resolution rationale names winner | Store does not parse it |
| CL-11 | SQL query can optimize lookup | Result must remain semantically identical to in-memory reference implementation |

For testing, `InMemoryStore` (from Pass 2) is the deliberately simple reference conflict locator SQLite results are compared against.

---

# O. Retrieval and retention adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| RR-01 | ACTIVE lexical match | Eligible by default |
| RR-02 | ARCHIVED strong lexical match | Excluded from default retrieval but still stored |
| RR-03 | Explicit archive-inclusive query | May retrieve archived item |
| RR-04 | ARCHIVED then ACTIVE | Eligible again |
| RR-05 | DEPRIORITIZED item vs ACTIVE item | Backend-independent accessibility policy determines ordering/filtering |
| RR-06 | Custom accessibility Kind | **Frozen v0 rule** (round 5): default retrieval fails explicitly — never silently treated as ACTIVE, DEPRIORITIZED, or ARCHIVED; item remains queryable via direct retention APIs |
| RR-07 | Item has no retention mark | Treat ACTIVE |
| RR-08 | Retention timestamp order disagrees with append order | Append projection wins |
| RR-09 | Retrieval finds item, retention excludes it | It does not appear in returned default RecallCandidates |
| RR-10 | Retrieval returns zero candidates | Means nothing retrieved, not proof no matching memory exists |
| RR-11 | Different backend produces different rank | Allowed; semantic candidate representation remains stable |
| RR-12 | WorkingSet excludes candidate due capacity | Not forgotten/archived |

Frozen v0 retention/retrieval policy (settles what RR-05 means precisely):

```text
ACTIVE
    normal default retrieval

DEPRIORITIZED
    eligible, but ordered after ACTIVE results

ARCHIVED
    excluded by default; opt-in retrieval only

any other Kind
    default retrieval fails explicitly — never guessed as one of the above
```

---

# P. FTS lexical-index adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| FT-01 | Error.message if Error is admitted | Index exact string — `Error` carries an `Id`, so a hit is representable as a `RecallCandidate` |
| FT-02 | Resolution.rationale | **Not indexed for generic recall** (round 5 correction) — `Resolution` is non-`Entity`, so a lexical hit could never be a `RecallCandidate.item: Ref`; text remains queryable directly via `conflicts_for()` |
| FT-03 | RetentionMark.rationale | **Not indexed for generic recall** (round 5 correction) — same reasoning as FT-02; text remains queryable directly via retention APIs |
| FT-04 | Observation.value is bare str | Index |
| FT-05 | Observation.value is int | Do not `str()` and index |
| FT-06 | Observation.value is nested mapping containing strings | Do not recursively invent search document in v0 |
| FT-07 | Event.payload is bare str | Index if Event payload policy permits |
| FT-08 | Event.payload is bytes | Do not index as decoded text |
| FT-09 | Object defines exotic `__str__` | Never call it for indexing |
| FT-10 | Query contains FTS operators/metacharacters | Parameterized/safely interpreted according to frozen query semantics |
| FT-11 | Persist same record idempotently | FTS row not duplicated |
| FT-12 | Identity collision rejected | Existing FTS content unchanged |
| FT-13 | Archived item | FTS may physically contain row; retention layer controls accessibility |
| FT-14 | Database reopen/rebuild | Same eligible text remains searchable |

---

# Q. Backend-equivalence adversaries

Memory semantics must not depend on SQLite. `InMemoryStore` (`store.py`, Pass 2) is the reference implementation used by tests.

| ID | Scenario | Required behavior |
|---|---|---|
| BE-01 | claims_for | SQLite and InMemoryStore return semantically same Claim set |
| BE-02 | conflicts_for | Same relevant conflict history |
| BE-03 | Episode retrieval | Same exact ordered refs |
| BE-04 | Retention current | Same projection |
| BE-05 | belief_state on records fetched from either store | Identical BeliefProjection |
| BE-06 | admit on either backend's preordered candidates | Same behavior given same ordering |
| BE-07 | Codec round-trip | Persistent store reconstruction equals canonical source representation |
| BE-08 | Unsupported value | Both semantic boundary and SQLite boundary fail, never silently diverge |

Ranking itself is allowed to be backend-specific.

The semantic meaning of the records returned is not.

---

# R. Memory ≠ truth adversaries

These are system-level tests rather than individual class tests.

| ID | Scenario | Required behavior |
|---|---|---|
| MT-01 | Lexically closest Claim is false | Retrieval may surface it; BeliefProjection does not call it true because of lexical rank |
| MT-02 | Most recent Claim is false | Recency alone gives no precedence |
| MT-03 | Archived Claim is true | Archiving affects accessibility, not truth |
| MT-04 | ACTIVE Claim is false | Accessibility does not confer correctness |
| MT-05 | Resolution exists | Does not reveal winner |
| MT-06 | One Claim has authoritative-sounding source string | Memory does not judge authority |
| MT-07 | Relevant Claim wasn't retrieved due capacity | Its absence from WorkingSet is not evidence of absence from Memory |
| MT-08 | Retrieval returns no candidates | BeliefState only knows what claims it was actually supplied; no metaphysical "false" conclusion |
| MT-09 | An Episode groups unrelated member accidentally | Grouping assertion is preserved, not upgraded into truth |
| MT-10 | User asks higher reasoning layer to adjudicate | Memory supplies evidence/conflict structure; reasoning layer owns judgment |

---

# S. Memory ≠ storage adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| MS-01 | Same semantic operations run entirely in memory | Episode/admit/belief/retention semantics work without SQLite |
| MS-02 | SQLite unavailable | Pure semantic modules remain importable/testable |
| MS-03 | Store contains records but retrieval policy excludes them | Stored ≠ recalled |
| MS-04 | Record deleted externally/corrupted DB | Store failure is not rewritten as semantic forgetting |
| MS-05 | Different future backend | Same tier-0 semantics can sit above it |
| MS-06 | FTS implementation changes ranking | No changes required to BeliefProjection/WorkingSet/Episode/Retention semantics |

---

# T. Import/dependency adversaries

Memory should receive the same architectural closure treatment Core did.

| ID | Scenario | Required behavior |
|---|---|---|
| IM-01 | Tier-0 Memory module imports sibling unnecessarily | Architecture test fails |
| IM-02 | Core imports Memory | Fails boundary test |
| IM-03 | `belief` imports SQLite | Fails |
| IM-04 | `recall` imports store | Fails |
| IM-05 | `codec` imports sqlite3 | Fails |
| IM-06 | `store` imports sqlite implementation | Fails inversion |
| IM-07 | `sqlite_store` imports only explicitly allowed modules | Pass |
| IM-08 | New memory module added without dependency-map entry | Inventory test fails |
| IM-09 | Import-time SQLite connection/file creation | Side-effect test fails |
| IM-10 | Module import reads clock/UUID/random | Side-effect test fails |

---

# U. Crash/reopen/database-integrity adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| DB-01 | Store closed and reopened | Semantic records survive exactly |
| DB-02 | Transaction interrupted during Episode append | No partial append |
| DB-03 | Transaction interrupted during identified record + FTS insert | Neither or both visible |
| DB-04 | RetentionMark insert succeeds but retrieval index update fails | Atomic behavior according to preregistered transaction boundary |
| DB-05 | Unknown schema version | Loud failure |
| DB-06 | Corrupted encoded payload | Loud decode/storage error |
| DB-07 | Duplicate local sequence value through corruption | Detected |
| DB-08 | Multiple store instances read same completed data | Same semantic reconstruction |
| DB-09 | Unsupported concurrent write pattern | Explicitly unsupported/fails; no claim of thread safety |
| DB-10 | SQL injection-like lexical query text | Data remains intact |

---

# V. Ref-closure adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| RF-01 | Episode Ref | Valid because Episode carries Id |
| RF-02 | RecallCandidate Ref attempted | Impossible/not supported; candidate is not Entity |
| RF-03 | WorkingSet Ref attempted | Impossible |
| RF-04 | RetentionMark Ref attempted | Impossible |
| RF-05 | BeliefProjection Ref attempted | Impossible |
| RF-06 | Resolution persisted | Persistence does not make Resolution Ref-targetable |
| RF-07 | SQLite local sequence key exists | Does not satisfy Entity |
| RF-08 | Application wants to reference a non-Entity storage row | Must reference some semantic Entity or change higher design; storage id is not promoted |

---

# W. Law-13 information-preservation adversaries

| ID | Scenario | Required behavior |
|---|---|---|
| IP-01 | WorkingSet over capacity | Excluded returned |
| IP-02 | Contradiction resolved | Original Contradiction preserved |
| IP-03 | Retention changes | Prior marks preserved |
| IP-04 | Episode closed | Items preserved |
| IP-05 | Retrieval cannot resolve a referenced Claim | Missing Ref remains explicit where relevant |
| IP-06 | Codec encounters unsupported path | Path + offending type/value information retained in error safely |
| IP-07 | Identity collision | Existing and attempted identity information exposed sufficiently to diagnose |
| IP-08 | FTS cannot index non-string payload | Payload persists if otherwise supported; merely not lexically indexed |
| IP-09 | Claim incompatible with query Context | Exclusion occurs mechanically; original Claim unchanged |
| IP-10 | Belief conflict opaque after Resolution | Conflict history surfaced rather than winner fabricated |

---

# X. Minimum cross-module adversarial scenarios

These should become high-level integration tests because each crosses several semantic boundaries.

### X-01 — Changing balance without contradiction

```text
Claim A:
subject = checking
predicate = balance
context.as_of = Monday
value = 900

Claim B:
subject = checking
predicate = balance
context.as_of = Friday
value = 1200
```

Monday query:

```text
DETERMINED → Claim A
```

Friday query:

```text
DETERMINED → Claim B
```

Saturday query:

```text
UNKNOWN
```

unless a Saturday-compatible Claim exists.

No temporal carry-forward.

---

### X-02 — Conflicting birth date

Two compatible Claims:

```text
birth_date = 1990-04-12
birth_date = 1991-04-12
```

No Contradiction recorded:

```text
AMBIGUOUS
```

Contradiction recorded:

```text
UNRESOLVED_CONFLICT
```

Resolution added with:

```text
rationale = "1990 date verified against original certificate"
```

Result:

```text
RESOLVED_OPAQUE_CONFLICT
```

Never parse the rationale to select the first Claim.

---

### X-03 — Retrieval is not belief

A false old Claim is a perfect lexical match and ranks first.

A newer correct Claim ranks second.

Retrieval returns both in backend order.

`WorkingSet(capacity=1)` may therefore contain only the false Claim.

This must **not** alter persisted belief/conflict state.

The test proves:

```text
attention ≠ truth
```

---

### X-04 — Archive is not deletion

1. Persist Claim X.
2. Retrieve X successfully.
3. Record ARCHIVED.
4. Default retrieval no longer surfaces X.
5. Direct archival retrieval still finds X.
6. Record ACTIVE.
7. Default retrieval can surface X again.

Same Claim Id throughout.

---

### X-05 — Identity vs namespace

Persist Claim whose subject is:

```text
Id(account, "123")
```

Query using:

```text
Ref(Id(account, "123"), Namespace(("finance", "checking")))
```

Subject matching works.

Retention mark recorded through another namespace affects same underlying item.

Exact Ref namespaces remain preserved wherever the reference itself is evidence.

---

### X-06 — Unsupported persistence payload

Observation value:

```python
SomeCustomClass()
```

Core permits the Observation.

Memory semantics may hold it in-process.

SQLite persistence rejects it with:

```text
UnsupportedPersistedValue
path = observation.value
```

Core object is unchanged.

No repr, pickle or string fallback.

This proves:

```text
Core representability ≠ durable Memory persistability
```

---

### X-07 — Episode ordering defeats wall time

Append:

```text
Ref(A at 12:03)
Ref(B at 11:59)
Ref(C at 12:01)
```

Episode returns:

```text
A, B, C
```

before and after SQLite round-trip.

---

### X-08 — Cross-predicate conflict

Claim A:

```text
predicate = account.owner
```

Claim B:

```text
predicate = account.legal_control
```

Core Contradiction references A and B.

Query belief for `account.owner`.

The conflict is relevant because A participates, despite B belonging to another predicate.

Memory surfaces conflict rather than pretending `account.owner` is uncontested.

---

### X-09 — Resolved and unresolved conflict coexist

Two Contradictions touch the same requested belief slot.

One has a Resolution.

One does not.

Projection:

```text
UNRESOLVED_CONFLICT
```

and conflict history includes both contradictions and the resolution.

---

### X-10 — Forgetting vs failure-to-recall

Item is ACTIVE and stored.

Backend lexical query fails to retrieve it because query terms don't match.

Result:

```text
no candidate returned
```

Retention still reports:

```text
ACTIVE
```

The system cannot infer:

```text
forgotten
deleted
false
nonexistent
```

from failed retrieval.

---

# Y. Decisions frozen by this matrix, and what's still open

Frozen (adopted into `MEMORY_SPECIFICATION.md`/`MEMORY_ARCHITECTURE.md`):

```text
1.  RecallCandidate.relevance: non-empty, duplicate Kinds forbidden.
2.  Retention: DEPRIORITIZED is eligible but ordered after ACTIVE;
    ARCHIVED excluded by default, opt-in only; any other Kind makes
    default retrieval fail explicitly rather than guess (round 5).
3.  PersistedValue float: finite-only; sign preserved exactly (no -0.0 canonicalization).
4.  Persisted container snapshot: defensive recursive snapshot.
5.  Identified duplicate persistence: identical = idempotent; conflicting same Id = error.
6.  Episode self-reference: opaque preservation.
7.  store.py owns MemoryStore (protocol) + InMemoryStore (real implementer), from Pass 2;
    store depends on codec (round 5) so both backends share one validation/collision authority.
8.  PersistedValue int: arbitrary-precision, round-trips exactly via a canonical
    width-independent encoding, never a backend's native integer width (round 5).
9.  BeliefProjection retains query_context; belief_state()'s conflict input is
    conflict_entries: tuple[Contradiction | Resolution, ...], supplied directly,
    not a reconstructed ContradictionLog (round 5).
10. Generic FTS indexing is restricted to Ref-targetable (Entity-bearing) records —
    Resolution.rationale and RetentionMark.rationale are excluded (round 5; see FT-02/FT-03).
11. MemoryStore.retrieve() takes an explicit retrieved_at: WallInstant; never an
    implicit wall-clock read (round 5).
12. Exact persisted-record union (EntityMemoryRecord/NonEntityMemoryRecord/MemoryRecord/
    PersistRecord) and the store/sqlite_store dependency edges it implies. (Pass 2)
13. Episode's create/append/close store surface — its own API shape, distinct from
    generic persist(). (Pass 2)
14. Identity-collision policy uses a private canonical PersistedValue representation for
    comparison, never a record's own __eq__ (frozen specifically to catch Event's
    Id-only equality). IdentityCollision/UnsupportedMemoryRecord as the two supporting
    exceptions. (Pass 2)
15. Persistence is closed over directly embedded admissible Entity records
    (Inference.conclusion, Error.cause); Error.exception is unconditionally unsupported. (Pass 2)
16. Lexical query contract: literal, case-sensitive substring matching over a frozen
    per-record-type lexical_content() field list — no FTS operators, no normalization,
    no case folding, no stemming. (Pass 2)
17. belief_state duplicate Claim inputs: same Id + identical content collapses to one
    (first-occurrence position); same Id + different content raises ValueError. (Pass 1,
    shipped in belief.py — recorded here for completeness, not left open.)
18. Store corruption/decode exception shapes: StoreCorruption(sequence: int | None,
    reason: str), UnsupportedSchemaVersion(found: str, supported: int), SqliteStoreClosed —
    all plain RuntimeError subclasses, resource-lifecycle/corruption machinery rather than
    new Memory concepts. (Pass 3, docs/memory-passes/03-sqlite-backend.md §41-46)
19. SqliteMemoryStore's durable architecture, dependency edges, and schema shape: an
    append-only operation journal (memory_meta + memory_ops, each row SHA-256-checksummed;
    memory_fts as a rebuildable derived index only) is authoritative durable state, not a
    record-per-table schema — replay reconstructs a fresh InMemoryStore through its public
    API only, never private internals. Schema version frozen at 1, no migration framework.
    Query methods (resolve/claims_for/conflicts_for/retention_for/retrieve) delegate to the
    replayed reference projection in v0. Final dependency row frozen in
    MEMORY_ARCHITECTURE.md's dependency graph. (Pass 3, docs/memory-passes/03-sqlite-backend.md)
```

Still open, deferred to pass preregistration (`MEMORY_ARCHITECTURE.md`):

```text
(none — Pass 3's preregistration resolved both items previously listed here; Pass 4 is
architectural closure/audit and is not expected to require new open semantic decisions)
```

None of the open items requires a new ontology concept. They are implementation semantics to decide when the relevant pass's code is actually being written.

---

# Z. Pass/fail criterion for the design

The Memory design is ready for implementation only if every adversarial case can be answered using one of:

```text
existing Core semantics
one of Memory's five constructions
a Memory operation
an explicit persistence rule
an explicit unsupported case
```

If an adversarial case requires inventing a sixth semantic concept merely to explain it, stop and reconsider the theory before implementation.

If it merely requires another helper, exception, serializer detail, SQL index, or private implementation mechanism, that does not by itself enlarge the Memory ontology.

The target is not "every possible memory system."

The target is:

> the smallest system that can preserve Core facts, retrieve them without conflating relevance with truth, bound attention without losing information, alter accessibility without deleting history, and project only those beliefs that are mechanically justified by the evidence already represented.

This matrix drives the next step exactly the way Core's propositions drove its passes. The semantic theory looks stable: what remains unanswered is runtime-policy edges (Pass 2/3 preregistration items above), not missing ontology.
