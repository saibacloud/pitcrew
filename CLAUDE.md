# CLAUDE.md - pitcrew

AI car workshop assistant. FastAPI + SQLite + Gemini + vanilla JS. Deployed to Coolify.

## Location

Lives at `Documents/Code/pitcrew` — back where it started. It spent a while nested
inside `old_nemesis/`, which is what broke the venv's console-script shebangs; moving
it back repaired them, since `.venv` was always built against this path. Both forms
work now:

```
.venv/bin/uvicorn backend.app:app --reload
.venv/bin/python -m uvicorn backend.app:app --reload
```

`old_nemesis/` (the retired dorking engine) is now at `Documents/Code/archive/old_nemesis`.

The local `.env` `GENAI_API_KEY` is **invalid**. The live key is in Coolify.

## Session 2026-07-22 - full test pass (uncommitted)

Fixed:

- Add-car 500, caused by a legacy `cars.options NOT NULL`. Resolved with a startup rebuild migration, run with foreign keys off to avoid a cascade wipe. Verified against a DB copy.
- Legacy `parts.status='needed'` default.
- Untracked `pitcrew.db-wal` / `-shm` from git. These were pushing live data.

Added:

- Google Search grounding on research and pin research. **Untestable locally - verify on deploy.**
- Car notes fed into the AI prompts.
- Doc-search answers auto-persist to the journal. The Save button was removed; the response now carries `journal_entry`.
- New Service Log journal tab (`type='service'` plus an `odometer` column).
- Orphan-pin soft-delete migration.
- `secrets.compare_digest`, and X-Forwarded-For keying on the rate limiter.

All of the above verified against a DB copy.

## Session 2026-08-04 - auth rebuild (uncommitted)

Replaced the bearer-token gate with traxd's session model:

- Access code hashed with Argon2id into `PITCREW_CODE_HASH`; sessions are an
  `itsdangerous` `TimestampSigner` cookie (`pitcrew_session`, HttpOnly + Secure
  + SameSite=strict, 12h) signed with `PITCREW_SECRET_KEY`.
- Fails closed - no `CODE_HASH` means nobody logs in. There is no dev-mode
  bypass any more.
- Generate the hash with `.venv/bin/python -m backend.auth` (prompts, so the
  code stays out of shell history). Must run from the project root.
- `API_TOKEN` is gone. **Coolify env must be updated before the next deploy**
  or the app is unreachable.
- Login rate limited 8/15min per IP, keyed on X-Forwarded-For like the AI limiter.
- Frontend: real `#view-login` in `index.html` + `static/js/login.js`, replacing
  the injected inline-styled overlay. `localStorage` token gone; `authHeaders()`
  replaced by `apiUpload()` for the four FormData call sites. Lock button in the
  garage header.

Verified end to end against a DB copy - 19 backend checks (401 gating, cookie
flags, tamper rejection, logout, rate limit) plus a live curl login round trip.
The login **page** was not verified in a browser (no browser tooling this
session), only its markup/CSS/JS by inspection.

## Known state

- The Converse tab is still a stub (`/api/chat`). Deliberately deferred.
- Logout only clears the cookie client-side. A stolen cookie stays valid until
  it expires, since signed sessions carry no server-side revocation list.
