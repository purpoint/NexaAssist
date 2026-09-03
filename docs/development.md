# Development

How to work on NexaAssist: what to run, what to do when adding something, and
what will stop you.

## The loop

```bash
pytest                                    # everything that can run here
python -m pyflakes backend/app backend/alembic tests
cd frontend && npm run test && npm run typecheck
```

`pytest` passes on a fresh clone with nothing installed. Tests that need
infrastructure skip themselves rather than fail:

| Package | Needs | Skips when |
| --- | --- | --- |
| `tests/backend/db/` | PostgreSQL + pgvector at `nexaassist_test` | not reachable |
| `tests/backend/redis/` | Redis, index 15 | not reachable |
| `tests/backend/docker/` | a Docker daemon | not running |

Those three are the only packages allowed to open a socket, and each narrows
the suite-wide guard to localhost rather than removing it. That distinction is
not academic: replacing the guard with "anything goes" is what once let a test
reach a real provider with a real key, billed and unnoticed.

To run them, make the dependency reachable and create the one database the
suite is permitted to touch:

```bash
createdb nexaassist_test && pytest
```

Nothing in the suite touches `nexaassist`, and nothing touches a Redis index
other than 15.

## Adding things

**A setting.** Add the field to `app/core/config.py` and the variable to
`.env.example`, with a comment. A test fails if you do one without the other.
Read it through `Depends(get_settings)`, never `os.environ`.

**A dependency.** `backend/requirements.txt` states what the project accepts;
`backend/requirements.lock` states what the image is built from. Add to both —
the lock is what the container installs, and a test asserts every declared
dependency is pinned there. Test-only dependencies go in
`requirements-dev.txt` and must not appear in the lock.

**A migration.** From `backend/`:

```bash
alembic revision --autogenerate -m "add the thing"
```

Then read it. Autogenerate is a drafting aid, not an oracle: it misses renames
and reads them as drop-then-add, and it has proposed spurious constraint drops
in this repository before. Every revision needs a working `downgrade`, proven
by an upgrade/downgrade/upgrade test against a live database. Never edit a
migration that has been pushed — correct it with a new one. Import each new
model in `app/models/__init__.py`, or autogenerate will not see it and will
propose dropping its table.

**An endpoint.** A module in `api/v1/`, one `include_router` line, a
`response_model` from `schemas/`, and the logic in `services/`. Then document
it in [`api.md`](api.md) and the architecture diagram — a test asserts every
served route appears in both.

**An external dependency of any kind.** Define the protocol first, then write
two implementations: the real one and a deterministic in-process one. The
application depends on the protocol. This is why the suite runs offline and
why a new checkout runs at all.

## What will stop you

Before a push, CI runs the backend suite on 3.11 and 3.12, the client suite,
a secret scan, the database and Redis packages against real servers, and a
job that builds the whole stack and brings it up. You can run the scan
yourself:

```bash
./scripts/scan-secrets.sh
```

It scans tracked files, so run it after staging — a secret in an unstaged file
is not yet a problem, and one in a staged file is about to be. A fixture that
must be key-shaped, because it exists to prove redaction redacts something,
carries `secret-scan: synthetic` on its own line. Per line, never per file.

Things that will fail a review even when the tests pass:

- Weakening a guard to make a test pass. If a guard is wrong, fix the guard
  and say why; if it is right, fix the code.
- A test that passes for the wrong reason. This has happened here more than
  once — an assertion that held vacuously because the logging handler had been
  replaced, a "disappearing case" test whose case was also a regression. When
  a test passes, check it can fail.
- Anything that creates a schema outside Alembic.
- A log line, an error body, or a URL carrying a customer's message, a
  credential, or a card number.

## Commits

One milestone, a small number of focused commits, each independently tested
and pushed. History is never rewritten: no amend, no squash, no rebase, no
force-push. A commit message says why the change is shaped the way it is —
what the code does is already in the diff.
