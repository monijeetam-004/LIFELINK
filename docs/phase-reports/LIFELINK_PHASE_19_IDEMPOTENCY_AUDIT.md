# LifeLink — Phase 19 Blood Request Idempotency: Design & Proof

## 1. Contract

`POST /blood-requests` accepts an optional `idempotency_key` (string,
1–200 chars). When present:

- **Scope**: unique per `(requester_user_id, idempotency_key)` — never
  global. Two different users may use the identical key string with no
  interaction between them (`test_same_key_different_users_creates_two_rows`).
- **Retry-safe**: the exact same key, submitted again by the same user,
  never creates a second row. The second (and any further) call returns
  the *original* request with HTTP `200` (not `201` — the project's
  existing convention, matched from `create_emergency`).
- **Payload-mismatch protection**: the same key with *different* request
  content returns `409 Conflict` with a message naming the conflict, using
  the project's existing `HTTPException(status_code=..., detail=...)`
  error shape — never a silent replay of the first payload, and never an
  internal detail (no DB row id, no other user's data) beyond the plain
  English message.
- **Concurrency-safe**: two simultaneous, identical-key requests from the
  same user resolve to exactly one row — proven with a real two-thread
  test, not just sequential calls.
- **Side-effect-safe**: a replay (same key, same payload) does not
  re-notify donors, does not write a second audit log entry, and does not
  touch inventory/matching/emergency-linkage/timeline/analytics/email a
  second time — because it never re-enters the creation code path at all
  (see section 2).
- **Missing key → unchanged behavior**: omitting `idempotency_key`
  (`None`) always creates a new row, exactly as before Phase 19 — no
  existing client is required to adopt this to keep working.

## 2. Implementation

### Schema (migration `a3b4c5d6e7f8`, `down_revision = f4a5b6c7d8e9`)

```python
op.add_column("blood_requests", sa.Column("idempotency_key", sa.String(length=200), nullable=True))
op.add_column("blood_requests", sa.Column("idempotency_payload_hash", sa.String(length=64), nullable=True))
op.create_unique_constraint(
    "uq_blood_request_idempotency_key", "blood_requests", ["requester_user_id", "idempotency_key"],
)
```

Both columns are nullable. Postgres treats every `NULL` in a unique
constraint as distinct from every other `NULL`, so every existing
keyless row (and every future keyless creation) is completely unaffected
— the constraint only ever activates when a real, non-null key is
supplied. Verified against the real dev Postgres database:
`alembic upgrade head` → `alembic downgrade f4a5b6c7d8e9` → `alembic
upgrade head`, all three clean, `alembic current` confirming
`a3b4c5d6e7f8 (head)` at the end.

### Route logic (`app/api/routes/blood_requests.py::create_request`)

```
payload_hash = sha256(canonical JSON of the request fields)

if payload.idempotency_key is not None:
    existing = lookup by (requester_user_id, idempotency_key)
    if existing:
        if existing.idempotency_payload_hash != payload_hash:
            raise 409  # same key, different content
        response.status_code = 200
        return existing  # replay -- no new work of any kind

# ... normal beneficiary resolution, hospital lookup (unchanged) ...

if payload.idempotency_key is not None:
    with db.begin_nested():          # SAVEPOINT
        db.add(new_request)
        db.flush()                    # IntegrityError surfaces here, inside the SAVEPOINT
else:
    db.add(new_request)
    db.flush()

# unchanged from here on: matching, donor notifications, audit log,
# BloodRequestEvent, commit -- reached exactly once per real creation
```

The pre-check (before any beneficiary resolution) is what makes a plain
sequential replay cheap and side-effect-free. The `begin_nested()`
savepoint around the actual insert is what makes two *simultaneous*
requests safe: if both threads pass the pre-check (neither sees the
other's row yet) and both attempt the insert, the database's unique
constraint lets exactly one `INSERT` succeed; the other raises
`IntegrityError` *inside* the savepoint, which is caught, rolled back to
the savepoint (not the whole transaction), and resolved by re-fetching
the now-committed row from the winning thread — same payload-hash check,
same 200-with-existing-row response. If the row still isn't found after
rollback (a truly pathological interleaving), the exception is re-raised
rather than silently swallowed (`# pragma: no cover`), because inventing
a fake success there would violate the project's anti-fake-functionality
rule.

## 3. Deliberate deviation from the Emergency (Phase 12) precedent

The Emergency idempotency mechanism this pattern is copied from does
**not** detect payload mismatches at all — a same-key replay with
different content silently returns the *first* payload's result. This
was confirmed by reading `create_emergency` directly, not assumed.

Phase 19's own spec explicitly and repeatedly requires payload-mismatch
detection for blood requests ("Payload mismatch protection... This is
critical"). Rather than silently diverging from the established pattern,
this is a **named, deliberate, documented extension**: one additional
nullable column (`idempotency_payload_hash`, a SHA-256 hex digest of the
canonicalized request body) is compared on every key-hit before deciding
whether to replay (200) or reject (409). Everything else — the unique
constraint shape, the savepoint-guarded insert, the 200-vs-201 status
convention — is copied exactly from the proven Emergency implementation,
per the instruction to follow the established project pattern unless
there is a concrete, stated reason not to.

This divergence is intentionally **not** back-ported to Emergency in this
phase — that would be scope creep on a system the spec explicitly says
not to touch beyond what's required ("do not modify Neo4j/Emergency
behavior unless genuinely required"). It's noted here as a legitimate
follow-up, not done.

## 4. Frontend

`frontend/src/lib/idempotency.ts` (`newIdempotencyKey()`) — extracted
from `SosModal.tsx`'s private, pre-existing implementation (Phase 18) so
`BloodRequestModal.tsx` doesn't duplicate the same logic; `SosModal.tsx`
now imports it instead of defining its own copy.

`BloodRequestModal.tsx`: one key is minted per open/attempt
(`useState(newIdempotencyKey)`, re-minted in the same `useEffect` that
resets the form on open). It is **kept stable** across:
- a double-click / rapid Enter-key resubmit (`if (create.isPending)
  return;` guards `submit()` in addition to the disabled button — closes
  a real race the disabled attribute alone doesn't fully cover, since a
  native form submit via Enter can fire before React re-renders the
  disabled state),
- a network failure / timeout / lost response (the key is only re-minted
  in `onSuccess` or on a real `409`),
- any other error class (validation 400/422, auth 401/403, server 5xx) —
  the user can simply press Submit again with the same key and get the
  same safe, deduped outcome.

It is **re-minted** only when:
- the modal is (re)opened for a genuinely new attempt, or
- the mutation gets a real `409` — which, given the key never changes on
  its own, only happens if the *form content* changed since the key was
  minted (the user edited a field after an earlier failed attempt). A
  fresh key means the next submit isn't rejected again for a reason that
  no longer applies.

The submit button is never disabled forever: `create.isPending` resets to
`false` on any settled outcome (success or error), confirmed by reading
React Query's mutation state machine and by the E2E test
`offline_network_failure.spec.ts`'s "a blood-request submission failure...
never disable[s] forever" assertion.

## 5. Test matrix (`backend/tests/test_phase19_blood_request_idempotency.py`, 17 tests, real Postgres throughout)

1. Normal creation without a key still works unchanged.
2. Same key + same payload replay → `200`, exactly one DB row.
3. Same key + different payload → `409`, detail mentions the mismatch,
   still exactly one DB row (the second attempt never created anything).
4. Same key, two different users → two separate rows (scope proof).
5. No key → always creates a new row, every time.
6. Empty-string key → `422` (schema `min_length=1`).
7. Non-string key → `422`.
8. Oversized key (201 chars) → `422`.
9. Max-length key (200 chars, boundary) → `201`.
10. **Real concurrency**: two threads, `threading.Barrier(2)`, identical
    key, identical payload → exactly one `201` + one `200`, same request
    id in both responses, exactly one row in the database.
11. Retry-after-simulated-response-loss: the "client never saw the first
    response" scenario — second call with the same key returns `200`
    with matching data.
12. A **failed** first attempt (family posting for an unlinked patient
    email, ≥400) leaves nothing behind to collide with — retrying the
    same key with a valid payload afterward succeeds with `201`.
13. Replay does not double-notify donors or duplicate the audit log entry
    (queried directly: exactly 1 `Notification` row, exactly 1 `AuditLog`
    row for the key, after two identical calls).
14. Two *different* keys each notify donors once (control case, proving
    #13 isn't a coincidence of the matching logic).
15. Unauthenticated request with an idempotency key is still a clean
    `401`.
16. A donor (wrong role) cannot create a request even with a key present
    — `403`, same as without one.
17. A replayed (200) request can still be cancelled normally afterward —
    idempotency doesn't leave the row in some special, un-actionable
    state.

**Result: 17/17 passing**, against a real PostgreSQL test database
(`lifelink_test`), no mocking of the database or the ORM layer.

## 6. E2E proof through the real UI (`frontend/e2e/blood_request_idempotency.spec.ts`)

Unit tests above prove the backend contract directly; these two
Playwright tests prove the same properties are actually reachable and
correct through the shipped `BloodRequestModal` component and build:

- **Rapid double-click**: two real, near-simultaneous clicks on the same
  mounted Submit button produce exactly one new row in the patient's own
  request list — not two.
- **Resubmit-after-lost-response through the real form**: the first
  `POST /blood-requests` is aborted at the network level
  (`page.route(...).abort("failed")`, simulating a genuinely lost
  response), the user sees the real "Can't reach the server" error, and
  clicking Submit again (same form state, same idempotency key) reaches
  the real backend and creates exactly one row — not a duplicate, and not
  a silent no-op.

Both pass against the real backend, the real production build, and the
real Postgres dev database.

## 7. What is explicitly *not* claimed

- This mechanism does not attempt to deduplicate requests with
  *different* idempotency keys but identical content — that's a
  fundamentally different (and much harder, heuristic) problem the spec
  does not ask for, and inventing it would be exactly the kind of
  unrequested scope expansion the spec warns against.
- The payload-hash comparison is content-equality, not semantic
  equality — reordering unrelated optional fields in the JSON body
  doesn't change the hash (the hash is computed from a canonical,
  sorted-keys dict of the specific fields that define the request, not
  raw request bytes), but this is documented behavior, not a loophole:
  the fields hashed are exactly the fields that determine what request
  gets created.
