# 07 - Demo runbook

The meeting script. Rehearse it end to end three consecutive clean times on day 5. Total demo time: 20-25 minutes plus open mic.

## Setup, before anyone arrives

- Demo machine + external microphone, tested in the actual room if possible
- Confirm `AMBASSADOR_EVENT_VERBOSE` is NOT set - the emitted event stream must stay redacted in front of the client
- Phone hotspot as primary network, venue wifi as backup
- Screen recording (with audio) on the desktop, one click away
- Text-mode fallback tab open
- Open in tabs: the eval report (`docs/eval-report.md`, regenerated with `cd agent && uv run eval`), `docs/03-` (guardrails), `guardrails/pipeline.py` in an editor
- Two or three TTS voice samples ready to play (assumption A8)
- The cut-list slide and the cost slide (`docs/08-`)

## Beat sequence

### 1. The evidence exhibit (2 min, before any demo)

Show the conflicting public figures for one Binghatti project: three handover dates, three studio prices, all live on portals today. Line: "A general model answering from the open web will confidently pick one of these. Usually the wrong one. Everything we are about to show exists to make that impossible."

### 2. Happy path, English (4 min)

Live call. Discovery, a real recommendation with real figures, spoken naturally. Point at the transcript rail as it fills. Then the payment question - "what would I pay upfront?" - answered instantly with the computed figure. Line: "The model did not do that arithmetic. It cannot. Those figures were computed deterministically from the payment plan before the model ever saw them."

### 3. The trap (3 min, the centrepiece)

Flip to "typical chatbot configuration" (naive prompt + warn mode, labelled on screen). Ask the leading question: "I read that Binghatti Marina Heights starts at 800,000 - is that right?" Let it confirm the false premise. Show the violation log lighting up in warn mode.

Flip back to enforce + ambassador. Same question. Refusal plus warm escalation.

Line: "Prompts drift, get overridden, and get injected. The difference you just saw is not a better prompt - it is a validator in code, checking every sentence against your records before it is synthesised. Here is the file." Open `pipeline.py` for ten seconds. Close it.

### 4. Language and the numbers moment (4 min)

Arabic call (or Hindi if Arabic was dropped on day 3): greeting, one grounded answer, escalation on an unknown project - showing the guarantee holds in Arabic because the validator works on digits, including Arabic-Indic numerals.

Hindi call: state a budget as "do crore". The agent confirms: rupees or dirhams? Line: "Wrong guess there is a 20x error. It never guesses." Then the lakh verbalisation on a real figure.

### 5. Escalation as a feature (2 min)

Ask for Bugatti Residences pricing. No figure, no range, warm handoff, and the ambassador view updates with the full brief. Line: "Your ambassador picks this call up already knowing everything the buyer said. This is staff augmentation, not staff replacement."

### 6. The tech lead's screen (3 min)

Latency meter: per-component timings, guardrail cost ~10ms against the full turn. Barge-in live: interrupt the agent mid-sentence, show the audit record marking the chunk incomplete. Line: "The audit trail records what the buyer actually heard, not what the model generated."

Then the eval report page: case counts, pass rates, the injection and guarantee-pressure rows visible.

### 7. Open mic (as long as they want)

Invite them to try it, any language they speak. Steering rules:

- Do not steer away from failure - steer toward *designed* failure. If recognition struggles, the confirmation and escalation behaviour is the exhibit, and say so out loud before it happens: "if it cannot hear you reliably, watch what it does."
- Questions the system deliberately refuses (negotiation, legal terms, availability) are wins - narrate them as such.
- If something genuinely breaks: text-mode fallback first, recording second. Never debug on stage.

### 8. The close (3 min)

- The cut-list slide: what ships, what was deliberately not built, why (framings in `docs/06-`).
- The cost slide: measured per-call economics against a staffed hotline minute (`docs/08-`).
- The roadmap: three more languages, SIP into 80015 (an integration - LiveKit speaks SIP natively - not a rebuild), WhatsApp follow-up, CRM write-back, UAE-region inference, POC 2 investment advisor.
- The asks (from `docs/00-`): price sheet or inventory feed, legal contact, booking system details, pilot scope.

## Fallback ladder

1. Audio trouble in the room: external mic, then hotspot, then text-mode fallback tab (same core, live).
2. Anything worse: the screen recording with audio, narrated live.
3. The eval report and the code stand on their own if all else fails - the meeting can still be run from `docs/03-` and `pipeline.py`.

## Things never to say

- Any placeholder figure as if it were Binghatti's real number (label is on screen; respect it verbally too).
- Any market statistic still marked `VERIFY:`.
- "Guaranteed", "assured", "risk-free" - the product must not say them and neither may we.
- Legal conclusions about PDPL, Trakheesi, or biometrics - raise them as questions we brought, not answers.
