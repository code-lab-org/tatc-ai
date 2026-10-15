# demo — role-based access UI

A standalone demo that exercises the role system end to end: log in as a seeded
user, ask a natural-language question, and watch the orchestrator call the `tatc`
tools your role is allowed to use. Decoupled from the Dex/LibreChat deploy stack
so it is fast to run and test.

## Run

```
pip install -r requirements.txt
uvicorn app:app --reload        # run from this folder
# open http://127.0.0.1:8000
```

With no `GEMINI_API_KEY` set, a deterministic offline stand-in (`demo_client.py`)
maps the canonical questions to tool calls, so the demo computes real `tatc`
answers without a key. Set `GEMINI_API_KEY` (see `apps/mcp-server/.env.example`)
to use real Gemini function-calling for arbitrary questions.

## Seeded users

Credential scheme (demo only): `codelab_{role}_{user_id}@lab.com` with password
`{role}_{user_id}`. Seeded for user_id 1 and 2, e.g. `codelab_admin_1@lab.com` /
`admin_1`. Passwords are **pbkdf2-hashed at rest** — never stored in plaintext.

Note: deriving the password from the email is predictable by design and is fine
only for a throwaway lab demo. Any real deployment must replace this scheme.

## Access matrix (enforced server-side, in code)

| Role | Analysis (`/ask`) | `author` | `run_eval` | `promote` | `manage_users` |
| --- | --- | --- | --- | --- | --- |
| user | ✅ | ❌ | ❌ | ❌ | ❌ |
| contributor | ✅ | ✅ | ❌ | ❌ | ❌ |
| developer | ✅ | ✅ | ✅ | ❌ | ❌ |
| admin | ✅ | ✅ | ✅ | ✅ | ✅ |

Capabilities come from `src/roles` (`Role.capabilities`); endpoints check them
with `require(<capability>)`. The gate is in code, not in the prompt — a role
that lacks a capability gets 403 regardless of what the model is asked. The
`/author`, `/run_eval`, `/promote`, `/users` endpoints are demo stubs that prove
the gate; the real authoring pipeline is a later track.

## Endpoints

`POST /login` · `GET /me` · `POST /ask` (capability `query`) ·
`POST /author` · `POST /run_eval` · `POST /promote` · `GET /users` (gated) ·
`GET /` (the UI).

## Tests

```
python test_demo.py
```

Covers login success/failure, per-role capability gating, unauthenticated
rejection, and a real end-to-end `/ask` that computes a `tatc` revisit answer
through the orchestrator with the offline client.
