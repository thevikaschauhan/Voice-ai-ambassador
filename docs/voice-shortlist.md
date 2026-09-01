# Fish voice shortlist - candidates to audition

`TTS_VOICE_ID_EN`, `_AR` and `_HI` are all empty, so every run so far has used
Fish's default voice. `docs/04-` asks for two or three samples to be brought to
the meeting rather than a choice already made: it converts assumption A8 (will
they accept a synthetic voice) into something the client enjoys deciding.

**This page is the shortlist, not the decision. Nobody on the build team has
listened to any of these.** Every candidate below was found by reading Fish's
public catalogue - an unauthenticated `GET https://api.fish.audio/model`, no
synthesis, no key, no spend. The sample links are Fish's own hosted previews of
each voice, so the whole shortlist can be auditioned in a browser before a
single paid character is synthesised.

`VERIFY:` the Arabic and Hindi candidates are shortlisted on their catalogue
metadata and their accent tags, which is all this team can read. Whether any of
them sounds like a person a Dubai buyer would take property advice from is a
native judgement, and it belongs in the same session as the review packet
(#4).

## Read this before playing anything to the client

Two findings about the catalogue matter more than which voice wins, and both
are checkable rather than opinion.

**1. Nothing in English, Arabic or Hindi is licensed.** Fish's model records
carry a `licensed` flag, and filtering the public catalogue on it returns
exactly seven voices - all seven Japanese, all of them narration or
customer-support voices. There is no licensed voice in any language this system
speaks. So the licence question for a client-facing demo is not answered by
picking carefully from this list; it is answered by Fish, by the account tier,
or by commissioning a voice. `VERIFY:` with Fish what the paid tier actually
grants for a public-library voice, before a voice is played in a Binghatti
boardroom.

**2. The top of the Arabic and Hindi catalogues is clones of identifiable real
people.** Not a stylistic problem, a likeness one. The most-used Arabic voices
in the catalogue are a named football commentator, a serving head of state, a
named streamer and, among the female voices, a member of the Saudi royal
family; the Hindi list has a former prime-ministerial voice and a named
cricketer. Every one of them scores well on the register we want, and any one
of them in a Dubai developer's boardroom is a problem that arrives before
anyone comments on the audio. **The shortlist below deliberately excludes every
voice whose title names a real person**, which is why some obvious
high-scoring candidates are missing from it.

English gets a third option the other two do not: Fish publishes its own
first-party voices under the author `Fish Official`, and all three English
candidates are theirs. There are none for Arabic or Hindi - a sweep of the top
300 by score in each language found zero - so those two shortlists are
community uploads of unverified provenance, and that difference should be said
out loud rather than hidden by presenting all three languages as one table.

## English

Brief (`docs/04-`): neutral international English, warm and measured.

| Voice | Voice id | Why it is here |
|---|---|---|
| Ethan (Fish Official) | `536d3a5e000945adb7038665781a4aca` | The closest thing in the catalogue to the brief: calm, clear, professional, documentary register, no performance in it. [Sample](https://platform.r2.fish.audio/task/bdd677ed767744fc91f166468786264b.mp3) |
| Laura (Fish Official) | `e3cd384158934cc9a01029cd7d278634` | The female option, so gender is a choice the client makes rather than one we made for them. Conversational rather than narrated, which suits a hotline better than a documentary voice. [Sample](https://platform.r2.fish.audio/task/e821bef995344d519caa1c93d808db23.mp3) |
| Jordan (Fish Official) | `79d0bd3e4e5444b18f7b6d89b5927bf1` | Older and slower. Worth having in the room if Ethan reads as too junior to be discussing eight-figure inventory. [Sample](https://platform.r2.fish.audio/task/edecbc0bdd4c46c39eda8520a0f729ef.mp3) |

## Arabic

Brief (`docs/04-`): Gulf-accented rather than a neutral MSA voice. The same
reasoning as the review packet's recording section - MSA is the register nobody
buys property in.

| Voice | Voice id | Why it is here |
|---|---|---|
| شاب سعودي | `10c5c2a37a284a81bb0cf3c53955d795` | Saudi, male, tagged narration and professional rather than announcer. The most-used voice in the catalogue that is both Gulf and not a named person. [Sample](https://platform.r2.fish.audio/task/214291316a0a4d9084bbb15532049f5e.mp3) |
| V صوت خليجي | `0d5a7ad85f8e4dc19935b734ddbdd22a` | Explicitly tagged Khaleeji, female, warm and measured. The generic title is a point in its favour here rather than a shrug. [Sample](https://platform.r2.fish.audio/task/e26ba79c87b54c7d92c97cda3b6aafb2.mp3) |
| عبير | `8c476d81a36a40d68975b5cd6640f028` | Khaleeji accent, conversational rather than narrated, and uploaded under what appears to be the speaker's own name - the best provenance available in this list, which is not the same as good provenance. [Sample](https://platform.r2.fish.audio/task/b7e5b67722a64f80854b8148fd4329f0.mp3) |

## Hindi

Brief (`docs/04-`, `docs/06-`): the Hindi risk is code-switching, not register.
The voice has to carry English project names and English clauses mid-sentence
without seams.

`docs/06-` already flags that Fish claims Hindi without naming it in their
material, and the catalogue supports the worry: the Hindi voices have usage
counts two to three orders of magnitude below the English ones, so there is
much less evidence behind any of them. If day 0 finds Hindi disappointing, the
TTS swap decision happens then, not on day 3.

| Voice | Voice id | Why it is here |
|---|---|---|
| neel | `6209a5682085409fa935f901f0bce950` | The best register match in the Hindi catalogue: calm, measured, conversational, professional, none of the storytelling performance most Hindi uploads carry. [Sample](https://platform.r2.fish.audio/task/c22322938c6a49b68e6aeca91bdd3e54.mp3) |
| Vikkram Kumar | `f2307002f45f4f729b8e1da17c527620` | The only shortlisted voice tagged for Hindi *and* English, which is the code-switching case rather than the Hindi case. Uploaded under the uploader's own name. Very low usage. [Sample](https://platform.r2.fish.audio/task/8d69723d015641a9b7b331888b666694.mp3) |
| हिंदी कथावाचक महिला | `9132b0d28b73481a83a14117753e5ede` | The female option, and a descriptive rather than personal title. Tagged narration, so audition it specifically for whether it performs the sentence instead of saying it. [Sample](https://platform.r2.fish.audio/task/f8a573688e8a455187b852f323e82cbd.mp3) |

## How to decide

1. Play the nine previews above. That costs nothing and rules most of them out.
2. For the two or three that survive, synthesise the same real sentences in
   each - take them from `data/spoken-forms.yaml`, which is where the awkward
   material lives (a lakh figure, a quarter, the client's own name through
   `data/lexicon.yaml`). A voice that handles a brochure sentence and mangles
   "Binghatti" is the wrong voice, and it is the sentence nobody thinks to
   test. This is the first step that spends anything; it is cents, but ask
   before running it.
3. Set `TTS_VOICE_ID_EN` / `_AR` / `_HI` in `agent/.env`, and note in
   `docs/06-` that day 1's "voices shortlisted" item is closed.
4. Take two per language to the meeting, not one. The point of this page is
   that the client chooses.

`PHASE-2:` cloning a named Binghatti ambassador's voice is technically easy and
contractually loaded (`docs/04-`). Raise it as roadmap; let them ask.
