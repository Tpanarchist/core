# Pass 1 preregistration — `value`, `result`

Pass-local API decisions not already fixed by SPECIFICATION.md/ARCHITECTURE.md. Implement, test, commit; do not reopen the frozen documents from here.

- **`Kind` grammar**: lowercase, dot-separated segments, each segment `[a-z][a-z0-9_]*` (starts with a letter, then letters/digits/underscores). Canonical form only — an invalid spelling is **rejected** at construction (`ValueError`), never normalized/coerced. `Kind` is a frozen, single-field dataclass; equality and hash follow from its one field.
- **`Known`/`Unknown`/`Maybe`**: `Known[T]` is a frozen, single-field generic dataclass — equality/hash follow `T`'s. `Unknown` is a true singleton (`__new__` returns the one shared instance); `Unknown() == Unknown()` is `True`, `Unknown() == False` and `Unknown() == None` are both `False`. `Maybe[T] = Known[T] | Unknown` (PEP 695 alias).
- **`Ok`/`Err`**: separate frozen, slotted, single-field generic dataclasses (`Ok[T].value`, `Err[E].error`) — not a shared base class, so exactly one branch's field exists on any given instance (no chance of both being populated). `Result[T, E] = Ok[T] | Err[E]`.
- **`map`/`map_err`/`and_then`**: defined directly on both `Ok` and `Err`, each a no-op passthrough on the branch it doesn't apply to (e.g. `Ok.map_err` returns `self` unchanged, never calling `fn`) — this is what "short-circuits on Err" and its `Ok` mirror actually mean at the API level.
- **`unwrap`/`unwrap_or`**: `Ok.unwrap()`/`.unwrap_or(default)` both return `.value`. `Err.unwrap()` raises `UnwrapError(self.error)`; `Err.unwrap_or(default)` returns `default`. `UnwrapError` carries the original `error` value verbatim (whatever `E` is) as `.error`, so misuse of the failure branch is always representable by one exception type regardless of what `E` is.
- **Immutability**: `frozen=True, slots=True` throughout — no dict per instance, no reassignment.
