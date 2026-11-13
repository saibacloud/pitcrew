# CLAUDE.md - pitcrew

AI car workshop assistant. FastAPI + SQLite + Google Gemini or OpenAI + vanilla JS. Runs as a container.

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

The local `.env` `GENAI_API_KEY` is **invalid**. The live AI key is entered in the deployed app's Settings.

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

## Fitment gate

- `backend/fitment.py` is the pure confidence gate for agent part claims (`verified` /
  `conditional` / `unverified`, computed server-side; stored level = min(claim, ceiling)).
- `build_fields` holds the build sheet (superseded, never overwritten). Agent-written values
  stay pending (`confirmed_at` NULL) until the owner confirms them; the gate sees confirmed
  values only. A confirmed-value change flags agent parts whose `fitment_json` used that
  field (`needs_recheck`), stales their verifications and re-gates them.
- Agent parts (`parts.source='agent'`) cannot leave the wishlist unless `verified`, or the owner
  overrides (409, then `override_fitment: true`, recorded in `override_at`). Owner parts are ungated.
- The AI mechanic is an external agent session (Claude Code / Codex) that reads `/llms.txt`
  (`static/llms.txt`) and works through `/api/agent/*` with a bearer key
  (`python -m backend.agent_keys LABEL` prints a token and its `label:sha256`; the hashes go
  in the `PITCREW_AGENT_KEYS` env secret, synced to `agent_keys` at startup, unset = none). Pitcrew makes no
  confidence-bearing LLM calls; the in-app chat / Doc search is lookup only.
- Agents write gated parts via `fitment_claims`; `verified` also needs a blind
  `fitment_verifications` row from a different key. Evidence comes only from `sources`
  pitcrew captured itself (`backend/fetch.py`: public https only) or manual pages. The
  capturing agent rates each source's authority (`tier` + `tier_reason`) and the blind
  verifier must also judge it authoritative. Do not add allowlists or other limits on what
  the agent may search or cite; reliability is the agent's judgement, checked by the verifier.
- `static/llms.txt` is the agent contract: keep it in step with `backend/routes/agent.py`.
- Tests: `.venv/bin/python -m pytest tests` (pytest is a dev-only venv install, not in requirements.txt).

## AI providers

- `backend/services/ai/` is the only AI layer; routes import from it. `google.genai` is imported only in
  `gemini_adapter.py` and `openai` only in `openai_adapter.py`. Prompts (`prompts.py`), image prep
  (`images.py`) and BM25 ranking (`ranking.py`) are provider-neutral. Inputs are neutral too: text
  strings, `Image`, `PdfFile` (path + cache key, uploaded once) and `PdfBytes`.
- The provider is resolved per call (`settings.resolve()`): the `ai_settings` row, else env
  `GENAI_API_KEY` as Gemini (`GEMINI_MODEL` or `gemini-flash-latest`), else 503 "No AI API key set"
  (`require_ai` dependency on every AI route). The provider comes from the key prefix: `AIza` Gemini,
  `sk-` OpenAI (default model `gpt-6-astra`).
- Settings API `GET/PUT/DELETE /api/settings/ai`. PUT checks the key before storing (Gemini: a token count on the model; OpenAI: a model lookup).
  The key is Fernet-encrypted with an HKDF-SHA256 key from `PITCREW_SECRET_KEY`; saving is refused (409)
  while that is unset, and a changed secret reads as `unreadable`. No response or log line carries the key.
- Each adapter declares its PDF limits (`pdf_limits`), which `manuals.py` uses for its page and byte budgets.
- Gemini answers keep Google's Search Suggestions (`search_suggestions`); OpenAI's are None.
- Live check against the configured provider: `.venv/bin/python -m backend.services.ai.check`.

## In-app chat and inventory

- Chat (`backend/routes/chat.py`): logged threads (`chat_threads`, `chat_messages`). Every
  answer is the configured AI provider with web search, `CHAT_SYSTEM_INSTRUCTION` plus `car_contract()` rebuilt
  from the DB per message (identity, VIN, colour, confirmed/pending build sheet, parts,
  service log, inventory). Chat answers are research, never fitment evidence.
- Inventory (`inventory`, garage-wide): a second AI call per message extracts adds/removes
  from the owner's message and the previous answer, run in parallel with the answer.
  `valid_changes` keeps only changes whose quote is the owner's verbatim words naming the item;
  inferred items must also be named verbatim in the previous answer. Undo is soft delete/restore.
- The Journal tab's Converse list holds agent session summaries (`type='converse'`).

## Known state

- Login is a 6-12 digit PIN (`PITCREW_PIN_HASH`, replacing `PITCREW_CODE_HASH`).
  Sessions live in the `sessions` table and logout revokes them server-side.
