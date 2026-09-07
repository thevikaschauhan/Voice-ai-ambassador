# 10 - Admin leads and knowledge base

This is the Phase 2 implementation contract. It extends the working voice POC
without changing its central safety claim: the model still does not originate
facts or arithmetic, and every figure still passes the numeric guardrail before
synthesis. Phase 1 remains unchanged until the Phase 2 gates in `docs/06-` pass.

## Decision status

All five product decisions are approved. Each row records the boundary so an
implementation card cannot silently reopen or expand it.

| Decision | Status | Design assumption | If the answer changes |
|---|---|---|---|
| Durable store | **Approved** | Supabase managed Postgres on the free tier, used as portable Postgres through its connection pooler | No alternative remains open. Railway still hosts the three application services |
| Contact capture | **Approved** | Ask once after a high-intent turn or at the first farewell for name plus phone or email; declining is valid | No alternative remains open. Arabic and Hindi remain gated on native-reviewed copy |
| Admin access | **Approved** | One shared access code for the POC | Per-user login adds users, roles, sessions and a real actor id to `AdminDecision`; this is deferred |
| Document figures and formats | **Approved** | PDF, DOCX, TXT, Markdown and pasted text; roughly 10-15 documents; approve extracted figures individually | No alternative remains open. Scans/OCR, legacy DOC, XLSX, images and URLs are deferred |
| Interest score | **Approved** | A 0-100 explainable score; the model extracts structured signals and code does the arithmetic; manual qualify/reject | Weights remain data so later Binghatti criteria do not require a code change |

The knowledge base is global and single-tenant for this Binghatti POC. Every
table leaves room for a future `tenant_id`, but tenant isolation, tenant-aware
retrieval and tenant-specific encryption keys are deferred.

## Service topology

```
browser
  -> Next.js web service (/admin and fixed /api/admin/* proxies)
       -> admin-api over Railway private networking
            -> Supabase Postgres pooler over TLS

LiveKit -> agent-worker
             -> Supabase Postgres pooler over TLS for call finalisation and retrieval
             -> existing model, STT and TTS providers
```

The `admin-api` is FastAPI in the Python image, started as a third Railway
application service. It owns ingestion, knowledge CRUD, lead reads and admin
decisions. The worker and the admin API share Pydantic domain contracts and an
`asyncpg` repository adapter; only the admin API serves HTTP. Versioned plain
SQL migrations live under `agent/` and run once through Railway's
`preDeployCommand` on the `admin-api` service, before it receives traffic.
Neither process runs migrations at ordinary startup. A confirmed incompatible
schema is fatal; a connection timeout starts the
admin API not-ready and leaves the worker in an observable base-inventory mode
so a transient database failure cannot take voice offline.

The web service does not receive `DATABASE_URL`. Its server routes call the
admin API's private Railway address and add `Authorization: Bearer
<ADMIN_API_TOKEN>` server-side. The browser sees neither that token nor the
private address. The admin API has no public domain. `DATABASE_URL` is present
only on the worker and admin API as a preserved Railway variable and points to
the TLS **session-pooler** URL from Supabase's Connect dialog. That mode is for
persistent IPv4 clients and supports prepared statements. The free-tier direct
endpoint is IPv6-only and Railway outbound IPv6 is disabled on the current
worker; transaction-pooler mode is not selected because it is for short-lived
clients and does not support prepared statements. The human chooses Supabase
`eu-central-1` Frankfurt, the closest offered region to the Railway services in
Amsterdam. `VERIFY:` Supabase does not document whether a region can later
change, so the runbook treats project creation as irreversible. Supabase Auth,
Storage, Edge Functions, RLS and client SDKs are not used; the database remains
ordinary Postgres and can move without rewriting the domain or API.

Each Python process starts with an explicit asyncpg pool of at most five
connections and bounded acquisition/query timeouts. That is deliberately tiny
beside the free Nano instance's 200 pooler-client limit and avoids recreating
the burst shape in the still-open Supabase asyncpg session-pooler timeout report
(issue #39227). Transaction mode is not a fallback: the same report observes
asyncpg prepared-statement failures there even with its statement cache
disabled.

The free tier's published database cap is 500 MB, which is ample for this lead
and extracted-text scope; `VERIFY:` confirm that figure in the dashboard at
creation because another Supabase page describes disk allowance differently.
The free tier can pause after roughly seven days of low activity. A single
asyncio lifespan task in the one-replica admin API issues one bounded low-cost
`SELECT 1` each day; it emits only the clear `database_health_probe` outcome and
elapsed time. This is a POC keep-active mitigation, not a guarantee; the demo
runbook checks the project state before doors open.
Moving to a paid plan is the production answer. A paused or unreachable
database never blocks the voice path or the authored farewell: lead persistence
and retrieval fail closed, emit classified clear events, and leave the agent
able to finish the call.

This accepts the extra Python service over letting Next.js own the database.
The one-service alternative is smaller operationally, but it would put scoring,
document parsing and a second copy of the domain schema in TypeScript, outside
the pure core and its branch-coverage gate. The service boundary is worth one
additional process because it keeps one owner for the rules that decide what a
buyer record means and what knowledge can reach the voice prompt.

## Lead finalisation

The redacted JSON event stream is not a lead source. Buyer utterances, model
sentences, escalation detail and most of `LeadBrief` are deliberately absent
there. The source is the in-process `EventLog.turns`, the last accepted
`LeadBrief`, and the adapter's closed session facts.

`shutdown_session` owns the finalisation sequence for every ending, including
`buyer_left` and the duration cap:

1. Seal any pending turn, preserving `audit_incomplete`.
2. Drain brief extraction so the final accepted brief cannot be missed.
3. Freeze a `LeadSnapshot` from the full-fidelity turns, last good brief,
   disclosure state, deterministic budget/project settlements, call-end reason
   and timestamps.
4. Insert the snapshot idempotently using the session id as the unique key.
   The raw call is now durable even if analysis fails.
5. Ask the existing server-side model for one Pydantic-validated
   `LeadAnalysisDraft`: a concise summary, semantic rubric signals and the turn
   indexes supporting each signal. Retry one invalid response. The model never
   returns a score.
6. Validate every evidence index against the saved turns, calculate the score
   in `ambassador/leads.py`, and update the lead. A failed analysis leaves
   `analysis_status=failed`, not a missing lead; the admin API exposes a bounded
   retry action.
7. Emit a classified clear event, then close provider clients, emit
   `session_end`, close the bridge and drain the event log in the existing
   order.

Database and analysis calls have explicit timeouts. Failure emits an enum stage
and error code, never an exception string or buyer text, and never prevents the
job from shutting down. A local durable retry queue is deferred; this POC is
idempotent and observable, not an at-least-once delivery system.

Every call becomes a lead, even a short disconnect. `ended_cleanly=false`, the
fixed `call_end_reason`, and per-turn `audit_incomplete` make truncated calls
visible instead of silently mixing them with complete conversations.

### Contact capture

A pure `ContactPolicy` owns whether a request is still owed and records
`captured`, `declined` or `not_asked`. A high-intent model turn may invoke a
`request_contact` tool whose speech is fixed copy; the policy, not the model,
enforces the one-request limit. If a buyer says goodbye before that happens,
the first farewell is intercepted by the same deterministic seam and gets the
one contact request instead of the closing line. A second goodbye is honoured
immediately. Once contact is captured, confirmed or declined, the existing
authored farewell takes the turn and the close remains attached to its seal.

Only the reply to the contact request is eligible for extraction. A
Pydantic-validated `ContactCapture` must point back to a literal span in that
reply; it cannot lift a number or address from an older property discussion.
The policy reads a phone number back before accepting it, because one misheard
digit is worse than no number. That echo is deterministic: the normalized
sequence is rendered from reviewer-authored digit forms in the existing
verbalisation data, never by the model, and no generated number can enter it.
Email capture uses the same bounded buyer-echo rule. A refusal or failed
confirmation records `declined` or `unconfirmed` and proceeds to the farewell;
it never asks for contact a second time.

`DRAFT - VERIFY with Binghatti and legal before implementation:`

> If you would like a member of the team to follow up, may I take your name and either a callback number or email address? You can decline.

No Arabic or Hindi version is drafted here. Those lines are native-review data
and must exist before the policy is enabled for either language.

## Interest score

`data/interest-score.yaml` owns the weights and thresholds. Its loader rejects
unknown or missing signals and rejects a maximum other than 100. The initial
rubric is deliberately generic because the human approved no minimum budget,
priority-project or cash-versus-finance rule:

| Signal | Maximum points | Evidence |
|---|---:|---|
| Budget stated | 15 | Model signal plus supporting buyer-turn indexes |
| Project named | 15 | Model signal; referenced ids must resolve to inventory |
| Timeline stated | 10 | Model signal plus supporting buyer-turn indexes |
| Contact shared | 20 | Valid contact value and contact permission in the captured record |
| Viewing or human requested | 25 | Model signal plus supporting turn indexes; actions are shown as corroboration |
| Questions asked | 10 | 5 points per distinct validated buyer-turn index, capped at 2 |
| Call length | 5 | One point per complete 60 seconds, capped at 5; duration comes from timestamps |

`score_interest(signals, rubric)` returns the total and a per-signal breakdown.
It clamps nothing silently: invalid inputs or a malformed rubric fail
validation. The saved `score_version` identifies the exact rubric. Changing
weights creates a new version and does not rewrite historic scores.

The score is guidance. It never auto-qualifies or auto-rejects a buyer. Only an
admin decision changes `lead_status`, and every change appends an immutable
`AdminDecision` carrying the previous state, new state, reason code, optional
note, actor and timestamp. Under the approved shared-code decision the actor is
honestly `admin`; the audit proves when and how the state changed,
not which person knew the code.

## Knowledge ingestion

At the approved scale of 10-15 documents, ingestion is synchronous and bounded:

1. The admin API accepts pasted UTF-8 text or an uploaded PDF, DOCX, TXT or
   Markdown (`.md`, `.markdown`) file. It verifies extension, MIME type, byte
   limit and expanded archive limit before parsing.
2. An adapter extracts text without an LLM. PDF page numbers are retained;
   DOCX paragraphs and table cells preserve order; TXT, Markdown and pasted
   text require valid UTF-8. Markdown is reduced to prose before chunking -
   emphasis, code, link and list markers removed, images and fenced code
   dropped, each table row one line of cells joined by ', ' - because chunk
   text is spoken, and a table pipe or a `**` inside a figure's sentence is
   read aloud. Heading markers alone are kept: the chunker starts a new chunk
   at a leading `#` and strips it, so removing it here would flatten a
   sectioned document into one unheaded chunk. A PDF with no extractable text
   ends as `failed/no_extractable_text` and tells the admin that scans need
   OCR, which is deferred.
3. Extracted text, the source content hash, filename/MIME metadata and parse
   status are saved. Original request bytes are discarded at the end of the
   request on success or failure; re-parsing requires re-upload. Pasted text is
   already its own source. Re-ingestion creates a new immutable revision so a
   spoken answer can always name the revision it used. No Supabase Storage is
   used.
4. `ambassador/knowledge.py` chunks headings and paragraphs deterministically.
   Defaults live in `data/knowledge.yaml`: target 1,600 characters, hard maximum
   2,400 characters and one-paragraph overlap. Tests use injected smaller
   limits rather than duplicating the algorithm.
5. The existing deterministic figure extractor records every occurrence as a
   `KnowledgeFigure`: normalized value and kind, currency/unit when present,
   source sentence, page, chunk and document revision. Parsing does not approve
   it.
6. The admin reviews that extracted list. Each checked occurrence gets an
   append-only approval record; unchecking records revocation. Only checked,
   currently active figures in eligible chunks can extend a turn's allowed set,
   and approving a figure never changes an `inventory_governed` chunk into
   prompt material.
7. Every chunk defaults to `admin_only`. The reviewer may mark non-project
   process and FAQ material `general_knowledge`, or bind descriptive prose to
   an existing inventory project as `project_knowledge`. The latter requires a
   project id at publish time. Structured prices, sizes, payment plans,
   handover, status, unit types and the amenities enumeration are
   `inventory_governed`; conflicts are flagged and remain admin-only, while an
   unknown project is never publishable. Scope changes are append-only and
   attributed. Structured facts change only through the existing
   `data/inventory.json` review and deploy.
8. Publishing makes reviewed general/project chunks searchable. When project
   context is known, project chunks rank first; general knowledge is always
   eligible. Archiving removes chunks from new retrievals without erasing the
   revision used by historic turns.

Postgres full-text search uses the `simple` configuration so English stemming
does not corrupt Arabic, Hindi or mixed-language terms. It searches published
`general_knowledge` and bound `project_knowledge` chunks only and returns at
most four ranked chunks. Ten to fifteen documents do not justify embeddings, a
vector service or an ingestion queue. Embeddings become a new ADR only after
measured retrieval quality or corpus size proves full-text search inadequate.

## Retrieval and the figures gate

Retrieval happens only after the existing deterministic confirmation/farewell
policy declines to own the turn. It runs once against the final buyer
utterance, is cached by turn index, and the same result is reused if LiveKit
invokes `llm_node` again for preemptive generation or a tool split. The target
is at most 250ms added before `llm_ttft`; the adapter emits only result counts
and elapsed time.

The retrieved context is added to a copy of `chat_ctx`, never session history,
as one fixed system message immediately before the model call. The wrapper
labels every chunk id, document id and revision, delimits the excerpt, and says
that excerpts are reference data rather than instructions. Text inside an
excerpt cannot change tools, guardrails, persona or policy. A prompt-injection
eval uploads instruction-shaped prose and asserts those controls remain in
force.

Figure handling has no source-based bypass:

- An approved `KnowledgeFigure` remains in the excerpt. Its typed value is
  added to a copy of the base `AllowedFigures` for this turn only, and only when
  the occurrence belongs to a retrieved general or bound project chunk.
- An unapproved or revoked figure occurrence is replaced with a
  `[figure withheld pending verification]` marker before the excerpt reaches
  the model. It never joins the allowed set.
- If retrieval identifies a direct match to a withheld figure sentence, a pure
  `KnowledgeFigurePolicy` takes the turn, speaks fixed native-reviewed
  human-confirmation copy and routes a human. The model does not get that turn.
  The English copy is part of the contact/copy review card; no Arabic or Hindi
  copy is authored here.
- If the model nevertheless emits an unapproved figure, the unchanged numeric
  guardrail blocks it and the existing bridge/fallback route resolves the turn.
  `GUARDRAIL_MODE=warn` remains a demo mode, never the hosted admin default.

This is a source-scoped extension of ADR-008, not an exemption. Inventory and
whitelist figures remain the global base set. Knowledge figures are added only
from the retrieved, approved occurrences whose provenance is stored alongside
the turn. A retrieval miss therefore fails closed. Revocation affects the next
turn; a turn already spoken keeps its immutable document revision and approval
ids in the audit.

Descriptive prose from an eligible published `general_knowledge` or bound
`project_knowledge` chunk may be spoken without figure approval. Structured
inventory-governed facts, conflict-marked prose and unknown projects remain out
of that path. The audit proves which chunks were supplied, not that every
adjective in a sentence is entailed by one. The numeric guarantee is stronger
and remains the one stated in `docs/03-`.

## Admin HTTP and web surface

### The web shell

`/admin` is a dashboard: a persistent left navigation (Overview, Leads,
Knowledge, and Sign out at the foot), a title bar, and one content region. It
is built on Meta's Astryx design system (`@astryxdesign/core`, exact-pinned
because Astryx is beta) under a Binghatti theme of our own, mounted by
`web/src/app/admin/layout.tsx`. See **Design language** below.

That layout is what keeps the two surfaces apart. AGENTS.md requires Binghatti
restraint and the human asked for a SaaS dashboard here; both hold because they
are about different surfaces, and in the App Router the CSS a layout imports is
only loaded for routes beneath it - so `/talk` never inherits an Astryx token
and never pays for the bytes. The demo keeps `ink`/`brass`.

Two things are deliberate rather than incidental. The shell lives inside the
**signed-in** branch, so an unauthenticated visitor never sees a nav that
implies data behind it. And the Overview counts are derived from the two list
reads the Leads and Knowledge pages already perform - there is no count
endpoint, so the numbers cannot disagree with the list a reviewer opens next.
Their labels are the closure's own words (`unreviewed`, not `pending`, which
`AnalysisStatus` owns).

Inside the shell the pages are Astryx too: the lead list and the document list
are tables, the lead detail and the document's chunks are cards, and every
status is a badge whose label is a word rather than a colour - a badge
distinguished only by its variant is a badge a colour-blind reviewer cannot
read. The status vocabularies live in one module
(`web/src/components/admin/status-badge.tsx`) because three surfaces show them
and three copies of a label map is how one status came to be spelled three
ways.

Two elements stay native inside Astryx's `Field`, and both are measured rather
than lazy. The scope, project and reason **selects** stay `<select>` because
Astryx's `Selector` is a combobox exposing `role=listbox` through its own
popup: adopting it would change the control's role. The **file input** stays
native because Astryx's `FileInput` is controlled by a `File | null` value
where intake resets through a ref after a successful add. In both cases `Field`
supplies the label wiring, and for the file input its `description` is what
finally attaches the accepted formats and the scanned-PDF warning to the field
itself.

That native file input is now **visually hidden behind a "Choose file"
button**, because a native file input's own button is shadow DOM and cannot be
themed cross-browser - it rendered as the browser's "Choose File / No file
chosen" chrome beside a themed primary, which is what the human asked to fix.
The Button is `secondary` so "Add document" stays the only primary, and it
opens the picker by forwarding a click to the input. The input is hidden with
`sr-only` rather than `display:none`: a display-none input cannot be clicked
programmatically in every browser, which would break the control that drives
it. It keeps its id, its `accept` string and its ref, so the label
association, the size check and the post-add reset are unchanged. Hiding the
chrome also hid the file's name, so the chosen name and size are rendered in
prose beside the button.

The picker's `accept` string is the browser's half of one contract with the
server, and both halves must agree: the API refuses anything outside its own
extension table, so a format offered here and refused there becomes a 422
rendered as prose, and a format the server takes but the picker hides is a
capability nobody can reach. It reads `.pdf,.docx,.txt,.md,.markdown` -
Markdown was added on the browser side only after the API could reduce it to
prose (schema 0005). `sourceLabel()` spells the stored type for both the list
and the document header, and falls back to the RAW VALUE for a type this tier
has not been told about: indexing the map directly yields `undefined`, which
React renders as an empty cell, so a reviewer would see a document with no
source and nothing to search for. Same rule as `endReasonLabel`.

The intake itself is a COLLAPSED PANEL above the list, opened by a "New
document" button that reports `aria-expanded`. The form is unmounted when
closed rather than hidden, so its fields stay out of the tab order, and the
trigger is deliberately not also called "Add document" - two buttons with one
name gave a screen reader user nothing to choose between while the panel was
open.

A detail route passes `heading` as well as `title`: `title` is the section
label the navigation marks, the `h1` says which record ("sess-1"). Without that
split a detail page had to choose between a useless `h1` and a second one of
its own, and both detail routes had picked the second - two level-one headings
each.

The **top bar** is a breadcrumb trail plus a session indicator. The trail
carries only the levels ABOVE the current page, so every crumb is a link to
somewhere else and the page names itself once, in the `h1`. `/admin` has no
ancestors and shows no trail. This departs from a trail that ends at the
current page, and deliberately: ending it there prints the page's name in the
top bar and again in the heading below it, which is the duplication the shell
was carrying before ("Overview" above "Overview"). Astryx's `BreadcrumbItem`
marks its LAST item `aria-current="page"` by itself unless told otherwise, so
every crumb passes `isCurrent={false}` - without it the section a reviewer came
from announces itself as the page they are on.

The **footer** names the session, offers Sign out, and states which commit the
service was built from. The sha comes from `RAILWAY_GIT_COMMIT_SHA`, read at
request time by the layout (a server component) and carried to the shell by
`BuildInfoProvider` - not by each page, because a rule every page has to
remember is the rule that cost all five routes their `h1` once already. It says
"built from" rather than "running": the web service redeploys on `web/**` and
`data/**` only, so a merge touching neither leaves the sha at the previous
commit, which is the true identity of the running image and the same one the
deploy sweeps print. When the variable is unset - locally, in CI, anywhere but
Railway - the line is omitted rather than filled with "dev" or a blank, because
a placeholder on a deployed page tells a reviewer something false.

### Design language

One theme decides how `/admin` looks: `web/src/theme/binghatti.theme.ts`. The
`.css`/`.js`/`.d.ts` beside it are GENERATED by `astryx theme build` and are
committed, because an unbuilt theme injects its component overrides at
hydration and flashes them on every SSR page load. Gate 10 runs `astryx theme
build --check`, which fails when the committed output no longer matches the
source - a stale stylesheet is otherwise silent, since the app keeps rendering
from it and an edit to the source appears to do nothing.

**Tokens.** The palette is GENERATED from a seed rather than pasted: the accent
is the brand's brass as a `[light, dark]` pair and `neutralStyle: 'warm'`
produces the ink ramp around it. That matters for contrast - Astryx guarantees
generated text at >= 4.5:1 against its surface and `--color-border-emphasized`
at >= 3:1, and hand-writing one side of a pair transfers that pair's contrast
to us. The mode is PINNED to dark rather than following
`prefers-color-scheme`: unpinned, the product's identity is the visitor's
operating system and a measured ratio describes only the machine it was
measured on.

Resolved dark values, all AA or better against the surfaces
(`--color-background-body` #171001, `--color-background-surface` #201B12):
text-primary #E9E1D5 at 13.2:1, text-secondary #B4AA99 at 7.5:1, accent
#EAC16C at 10.1:1, on-accent #452D00 on the accent at 7.6:1,
border-emphasized #706758 at 3.1:1. The status hues are NOT derived from the
accent - Astryx keeps its own, tuned for its own surfaces - so they are
measured separately: warning #f8d36a 11.8:1, error #ffc4be 11.3:1, success
#a4d6a3 10.4:1.

**Type.** A serif display face (Cormorant Garamond) for the wordmark and page
titles, a sans (Manrope) for body. Both are OFL, pinned as `@fontsource`
dependencies and self-hosted by `next/font/local` - not `next/font/google`,
which fetches from fonts.googleapis.com during the build and makes the build
depend on a third party being up. Astryx sets `--font-family-*` but never
loads a file, and a named family with no file loaded silently becomes its own
fallback, so the theme references CSS variables that `web/src/theme/faces.ts`
declares. next/font forbids importing those names (it is a compile-time
transform and requires written literals), so the two sides are held together by
a case in `admin-theme.test.tsx` that reads the source. Numerals are lining and
tabular across the route group: Cormorant ships old-style figures, and a score
of 61 rendered as "6ı" until that was set.

**Radii.** 2px inner, 4px element, container and page. The ramp is
`base x multiplier` and is not linear in the base - 2/1 and 4/0.5 both give
inner 2, element 4, container 6, page 14 - so the two outer steps are pinned in
`tokens` rather than chased by tuning the multiplier.

**Where brass may appear.** Four places, and never as a large fill: the active
navigation marker (a 3px inset rule on the leading edge, not a pill), the
keyboard focus ring, a primary action's border and text, and a "needs action"
state. Two of those had to be taken back by hand. Astryx's `variant="primary"`
renders a solid accent bar, so it is overridden in the theme to a brass outline
- otherwise the largest brass area on the sign-in screen was the button. And
`globals.css` declares `:focus-visible` OUTSIDE any `@layer`, which beats every
layered rule, so the demo's brass was painting the ring on every admin control
and the theme's `--focus-outline-color` was a token nothing read;
`app/admin/astryx.css` re-points it at `var(--color-accent)` so the theme stays
the single source.

### The API

All admin API routes except `/health` require the shared bearer token. The API
surface is deliberately small:

| Route group | Operations |
|---|---|
| `/v1/leads` | List/filter leads; fetch detail with turns, brief, summary and score breakdown; retry failed analysis |
| `/v1/leads/{id}/decisions` | Append qualify or reject decisions with optimistic revision checking |
| `/v1/knowledge/documents` | Create from paste/upload; list, each row carrying `figures_pending` (figures awaiting approval on the current revision) so the overview needs no per-document read; fetch parse result, chunks and extracted figures; publish, revise or archive |
| `/v1/knowledge/chunks/{id}/reviews` | Append a general-knowledge, bound project-knowledge, inventory-governed or reset-to-admin-only scope review; project scope requires an inventory id |
| `/v1/knowledge/figures/{id}/reviews` | Append approval or revocation |
| `/health` | Unauthenticated process liveness only; remains 200 during a database pause so Railway does not restart-loop |
| `/ready` | Bearer-protected database and schema readiness; no record counts or secrets |

The Next.js `/admin` surface is a thin server-backed UI. `ADMIN_ACCESS_CODE` is
unset-closed, rate-limited and compared with the same constant-time,
length-guarded pattern as the demo gate. A successful login sets a signed,
short-lived `HttpOnly`, `Secure`, `SameSite=Strict` session cookie using a
separate server-side `ADMIN_SESSION_SECRET`; fixed same-origin proxy routes
validate it, check origin on mutations and add the internal bearer token. The
browser never chooses an upstream URL and cannot turn the proxy into an open
relay. Per-user login and roles are deferred; `AdminDecision.actor_id` is
nullable beside the current `actor_kind=admin`, so adding identity does not
change the meaning of historic shared-code decisions.

The lead list shows only operational fields: status, score, language, project
ids, call time, completeness and contact-present. Buyer words and contact
values appear on the detail page only. The detail makes model provenance
visible by labelling the summary as generated, showing score evidence and
showing the immutable decision history. Knowledge review shows source text and
figure context, because approving a value without its sentence and page is not
review.

## Data handling and observability

- Full transcripts, summaries, contacts and admin notes live only in Postgres
  and authenticated API responses. Buyer-derived payloads are encrypted with
  authenticated application-layer encryption before they reach Postgres; the
  envelope records a key version and binds lead id plus field name as
  associated data. They never enter stdout, the existing JSON event stream,
  health responses or list-page HTML.
- Phone and email remain decryptable because a human must contact the buyer. A
  separate keyed HMAC-SHA-256 fingerprint of each canonical value supports
  equality and duplicate detection without indexing the clear value. Hashing
  is not encryption and is not presented as one. `PII_ENCRYPTION_KEY` and
  `PII_HASH_KEY` are server-only Railway variables shared by worker and admin
  API; neither reaches the web service.
- **Key format: any string of at least 32 characters.** Each variable's working
  key is DERIVED from it with HKDF-SHA256, using the variable name as the info
  parameter, so the same generated value pasted into both variables still
  yields two unrelated keys. Generate one with `openssl rand -base64 32` or
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`; both produce
  43 characters, which is neither hex nor 32 bytes of text. The first
  implementation parsed the variable instead of deriving from it and accepted
  only 64 hex characters, which would have put a worker on Railway that refused
  every job - a key format is not something anyone should have to infer from a
  variable name. The derivation is what `key_version` identifies, so changing
  it changes what every stored envelope means.
- No raw audio is stored. Extracted knowledge text is commercial content,
  protected by the database and authenticated admin route. Original PDF/DOCX
  bytes are not retained by default.
- Durable audit events use the existing classification discipline. A new event
  must be either explicitly redacted or admitted to `CLEAR_EVENTS` with a
  reason. Exception strings, buyer words, summaries, filenames, document
  titles, source sentences, contact values and admin notes are never clear.
- Retention duration remains `VERIFY:` with Binghatti legal. The schema carries
  `retention_expires_at`; the admin can delete a lead through an audited route.
  Automated retention execution is deferred until the period is approved.

Implementation adds narrowly scoped Python dependencies and must record them in
the owning GREEN commit: `asyncpg` for non-blocking portable Postgres access,
FastAPI plus its production ASGI server for the private API, `pypdf` and
`python-docx` for the two approved document formats, and `cryptography` for
authenticated application-layer encryption. No Supabase SDK is added.

## Implementation cards

Every card follows RED commit, GREEN commit, optional refactor commit. The PR
description names those commits and they are not squashed before god merges.

The names in the last column must be the names `grep` finds in
`agent/tests/`. Two of them were not: they were written before the tests
existed and never reconciled, so the column that exists to point a reader at
the failing case pointed at nothing. If you add a card here before its test,
describe the case in prose rather than inventing an identifier - a wrong name
costs a reader more than no name, because they assume the test is missing.

| Suggested owner | Card | First RED test |
|---|---|---|
| toby/core | Phase 2 Pydantic contracts and score rubric | `test_every_rubric_signal_contributes_only_its_documented_points` fails because the models and scorer do not exist |
| toby/core | Deterministic knowledge chunking, closed-by-default scope and figure context | `test_revoked_unretrieved_or_inventory_governed_facts_and_unbound_project_prose_never_extend_allowed_figures` fails on the missing pure context builder |
| dwight/adapter | Postgres migrations and async repository | `test_migrations_round_trip_every_phase_2_contract` fails against an empty temporary Postgres schema |
| dwight/adapter | Persist every call from `shutdown_session` | `test_a_call_becomes_a_lead`, `test_a_truncated_call_does_not_read_as_a_complete_one` and `test_per_turn_audit_incomplete_survives_to_the_row` (`agent/tests/test_persist_call.py`) fail because shutdown has no repository hook |
| dwight/adapter | Structured summary analysis and scoring finaliser | `test_two_invalid_responses_fail_and_keep_the_lead` and `test_the_score_is_computed_here_and_never_taken_from_the_model` (`agent/tests/test_analysis_finaliser.py`) fail because no finaliser exists |
| dwight/adapter | Knowledge parsers, full-text retrieval and prompt injection | `test_retrieval_runs_once_per_turn_and_reuses_the_same_revision_and_figure_set` fails because `llm_node` has no retrieval seam |
| toby/adapter | FastAPI admin API and bearer boundary | `test_every_non_health_admin_route_refuses_a_missing_or_wrong_bearer` fails before the API exists |
| dwight/adapter | Authenticated PII envelopes and classified durable audit | `test_buyer_payloads_encrypt_while_phase_2_events_contain_no_buyer_words` fails before the encryption and event projections exist |
| jim/web | Admin access session and fixed proxy routes | `admin routes stay closed when ADMIN_ACCESS_CODE is absent and never expose ADMIN_API_TOKEN` fails before the gate exists |
| jim/web | Lead list, detail and manual decisions | `an admin can inspect score evidence and append, but not overwrite, a decision` fails before the UI exists |
| jim/web | Knowledge upload, chunk-scope and per-figure review | `a scanned PDF reports no text, chunks default closed, and an approved figure can be revoked` fails before the review UI exists |
| ryan/ops | Supabase database variables and private Railway admin-api topology | `the IaC plan adds admin-api, preserves pooler variables only on Python services and gives web no DATABASE_URL` fails against the two-service graph |
| ryan/ops | Phase 2 live smoke and recovery runbook | `a persisted disconnect, upload, retrieval, revocation and manual decision survive service restarts` fails on the undeployed topology |
| dwight/adapter | Contact capture, confirmation and native-copy gate | `a first goodbye asks once, a second goodbye closes, and no contact value reaches events` fails before the policy exists |
