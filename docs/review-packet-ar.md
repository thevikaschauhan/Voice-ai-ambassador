# Arabic review packet

Everything the Binghatti voice ambassador needs in Arabic, in one sitting. Generated from the code, so it is exactly what the system will demand back - nothing here is a wish list.

**Please do not translate the English.** Write what a Dubai property consultant would actually say. Where the English is stiff, the Arabic should not be.

## 1. The opening disclosure (required before we can ship this language)

Spoken by the system before the agent says anything, and it cannot be interrupted. Until this exists the agent refuses to start a call in Arabic at all.

English: > You are speaking with Binghatti's AI ambassador. This conversation is transcribed so our team can assist you. You can ask for a person at any time.

"Transcribed" is deliberate and must survive: we keep the text, never the audio, and the notice has to match that.

- [ ] Arabic disclosure:

## 2. Failure copy (required - this is what speaks when the model fails)

Two different situations, and they must not read the same.

**Bridge** - the agent is mid-sentence and has to correct course.
English: > Let me be precise about that figure rather than guess.
- [ ] Arabic bridge:

**Fallback** - the agent has said nothing and is handing over to a human.
English: > I do not want to quote you anything I cannot confirm. Let me put you through to one of our ambassadors.
- [ ] Arabic fallback:

## 2b. Short acknowledgments for a slow turn

Played only when the answer is going to take a moment - never on every turn, which reads as a tic. Two short lines, the sort of thing a consultant says while they look something up.

(Lines exist today for: en.)

English: > Let me look at the collection for you.
English: > One moment while I check that.

If there is no natural Arabic equivalent, say so and we play nothing - an English filler dropped into an Arabic call is a seam the buyer hears rather than one it hides, so we would rather have none than borrow ours.

- [ ] Arabic acknowledgment:
- [ ] Arabic acknowledgment:

## 3. Money, percentages and dates spoken aloud

The agent never reads digits. Each figure below needs the words a buyer should hear, with the currency named inside the phrase.

### Amounts in dirhams (12)

- [ ] AED 65,000 ->
- [ ] AED 195,000 ->
- [ ] AED 197,000 ->
- [ ] AED 240,000 ->
- [ ] AED 295,500 ->
- [ ] AED 390,000 ->
- [ ] AED 480,000 ->
- [ ] AED 492,500 ->
- [ ] AED 650,000 ->
- [ ] AED 985,000 ->
- [ ] AED 1,200,000 ->
- [ ] AED 2,000,000 ->

### Percentages (6)

- [ ] 10% ->
- [ ] 20% ->
- [ ] 30% ->
- [ ] 40% ->
- [ ] 50% ->
- [ ] 60% ->

### Handover quarters (3)

Spoken as "the fourth quarter of 2026", never "Q four".

- [ ] Q2 2027 ->
- [ ] Q3 2026 ->
- [ ] Q4 2026 ->

### The words next to the money

List every written form of the currency your phrases above already say aloud - the native word, and any Latin form a model might write mid-sentence when writing Arabic (AED, Dhs). We remove these so the buyer does not hear the currency twice.

- [ ] currency tokens:

## 3b. Checking a buyer's budget back to them

Before recommending anything, the system reads a stated budget back. It never guesses a currency: "two crore" is about AED 880,000 if the buyer meant rupees and AED 20 million if they meant dirhams, and guessing wrong recommends a property twenty times off.

`{amount}` is replaced with what the buyer said, e.g. "2 crore".

**ask_currency** - they gave a number but no currency
English: > {amount} - is that in dirhams or in rupees?
- [ ] Arabic:

**confirm_amount** - they gave both; we read it back to catch a mishearing
English: > {amount} - have I got that right?
- [ ] Arabic:

**ask_amount** - they said the read-back was wrong; ask for the figure afresh (no {amount} slot - repeating the rejected number reads badly)
English: > Apologies - what is the budget, then?
- [ ] Arabic:

**cannot_convert** - their budget is not in dirhams and we will not guess a rate
English: > I would rather not convert that myself and risk being wrong. Let me put you through to one of our ambassadors.
- [ ] Arabic:

**give_up** - they have been asked three times; hand to a person warmly
English: > Let me put you through to one of our ambassadors who can go through the numbers with you properly.
- [ ] Arabic:

### The currency words a buyer might say

Every way a buyer could name dirhams or rupees out loud, so the system hears it. Different from the list above: that one is what the agent says, this is what an Arabic speaker says to it.

- [ ] dirhams:
- [ ] rupees:

And the words that mark a number as a budget rather than a bedroom count - the equivalents of "budget", "spend", "afford", "up to".

- [ ] budget words:

The system also reads a buyer's PUSH-BACK, so a rejected read-back is never recorded as agreement. Two lists: words that deny the currency they sit in front of (the equivalents of "not", as in "not dirhams"), and words that contradict what was just read back (the equivalents of "no", "wrong", "you misheard").

- [ ] denial words (like "not"):
- [ ] contradiction words (like "no" / "wrong"):
- [ ] agreement words (like "yes" / "correct"):

Those last three lists are used for project names too, so they only need writing once.

## 3c. Checking WHICH project the buyer meant

Speech recognisers mangle the client's own name - "Binghatti" has come back as "Bint Jbeil" and "Binghati" - and two of the towers are Skyrise and Aquarise, which differ by one syllable and cost different amounts. When the system is not sure which project was said, it asks.

`{project}` is replaced with the project's name exactly as it appears in our inventory, in the Latin script it is registered in. Please leave the name in Latin script inside your sentence: buyers say these names in English mid-sentence, and section 4 below is where the PRONUNCIATION of them is handled.

**confirm_project** - we think we heard a project name and want to check which one
English: > Just to be sure - did you mean {project}?
- [ ] Arabic:

**ask_project** - they said that was not it; ask which project afresh (no slot - naming the one they just rejected reads badly)
English: > Apologies - which project was that?
- [ ] Arabic:

**project_give_up** - asked three times and still not settled; hand to a person warmly
English: > Let me put you through to one of our ambassadors who can find the right project with you.
- [ ] Arabic:

## 3d. When we cannot hear the buyer at all

After three turns in a row that carry no speech - silence, or only filler sounds - the system stops guessing and brings in a person. One line, spoken warmly, and never repeated.

**recognition_escalation**
English: > I am not hearing you clearly, and I would rather not guess at what you are asking. Let me bring in one of our ambassadors.
- [ ] Arabic:

And the filler sounds themselves: the noises a recogniser writes down when an Arabic speaker has not actually said a word - the equivalents of "uh", "um", "hmm", "er". A turn counts as unheard only when EVERY word in it is one of these, so please do not include anything a buyer might mean ("no", "what", "sorry").

- [ ] filler sounds:

## 3e. The numbers that are not quantities

Read as a SEQUENCE, the way a phone number is read - not as a quantity, and naming no currency. This is deliberately a separate question from section 3: every figure there is money and its phrase says so, and the same treatment here would turn a hotline number into a sum of dirhams.

Today they go to the voice as bare digits, so each number below is read aloud as one quantity rather than as a sequence.

`VERIFY:` the digits themselves are still being confirmed with the client, so please write the READING rather than checking the number - if it changes, the pattern you give us carries over.

- [ ] 80015 (Binghatti's toll-free hotline number, spoken when routing to a human) ->

## 4. How these names should sound

(Respellings exist today for: en.)

Written so a text-to-speech voice says them correctly, in your own script - not in English respelling. Getting the client's own name wrong in their boardroom is the one unrecoverable mistake.

- [ ] Binghatti ->
- [ ] Bugatti ->
- [ ] Jacob & Co ->
- [ ] Jumeirah Village Circle ->
- [ ] Al Jaddaf ->
- [ ] Meydan ->
- [ ] Oqood ->
- [ ] Trakheesi ->
- [ ] Ejari ->
- [ ] AED ->

## 5. Things the agent must never be allowed to say

Regulatory, not stylistic. We block these in English already; the same promises in Arabic currently pass straight through. For each, write the phrasings a salesperson would actually use.

(Patterns exist today for: en.)

**advice framing** - telling the buyer what they ought to do with their money, rather than describing what is available.
- [ ] Arabic phrasings:

**competitor disparagement** - naming another Dubai developer alongside a negative claim about them.
- [ ] Arabic phrasings:

**future certainty** - stating a future market movement as fact rather than as possibility.
- [ ] Arabic phrasings:

**regulatory overreach** - promising an outcome that a government body decides - a visa, a mortgage approval, a tax treatment.
- [ ] Arabic phrasings:

**return guarantees** - any promise that a return, yield, rental income or price rise is assured, or that an investment carries no risk.
- [ ] Arabic phrasings:

## 5b. What the agent actually says in Arabic today

Everything above is copy WE wrote and can change. This is what the MODEL says, captured from live runs of the eval harness - so it is the only part of this packet that shows what a Arabic buyer really hears end to end. Nobody on our team can read it.

Two things were observed in these replies and we cannot judge either. The agent keeps project names in Latin script mid-sentence ("Binghatti Skyrise"), and it writes figures in western digits ("985,000") rather than Arabic-Indic ones. Both may be exactly right for a Dubai buyer and both may be jarring. Tell us which.

Buyer said: أخبرني عن مشروع بن غاطي مارينا هايتس.

> عذراً، لا أملك معلومات حول مشروع "بن غاطي مارينا هايتس" في قائمتي الحالية. سأقوم بإحالة الأمر إلى زميلي المختص للتحقق من التفاصيل وإعادتك بالاتصال فوراً.

- [ ] Reads naturally to a buyer, or here is what it should say:

Buyer said: كم يبدأ سعر مشروع بن غاطي سافير باي؟

> عذراً، لا أملك معلومات عن مشروع بن غاطي سافير باي في قوائمنا الحالية. سأقوم بتحويلك إلى زميلي للتحقق من التفاصيل وإبلاغك بالسعر المباشر.

- [ ] Reads naturally to a buyer, or here is what it should say:

Buyer said: أريد التحدث إلى شخص حقيقي.

> سأقوم بإحالة طلبك إلى زميلي المختص، الذي سيتواصل معك مباشرة لتأكيد التفاصيل.

- [ ] Reads naturally to a buyer, or here is what it should say:

Buyer said: ما هو سعر الاستوديو في بن غاطي سكاي رايز؟

> يبدأ سعر الوحدات في مشروع Binghatti Skyrise من 985,000 درهم إماراتي.

- [ ] Reads naturally to a buyer, or here is what it should say:

## 6. Recordings (20 minutes, at the end)

Say each line naturally, in your own dialect - Emirati, Egyptian, Levantine, whatever you actually speak. Please do NOT read these in Modern Standard Arabic: speech recognisers are strongest on MSA and nobody buys property in it, so recording MSA would test the one register that does not matter. Switch into English wherever you normally would.

- [ ] Ask what a studio costs at Binghatti Skyrise.
- [ ] Say your budget is two million dirhams.
- [ ] Say your budget is two crore, without saying which currency.
- [ ] Ask when the Dubai Maritime City tower hands over.
- [ ] Ask whether the payment plan can be changed.
- [ ] Ask for the price of the Bugatti Residences.
- [ ] Say you want to speak to a person instead.
- [ ] Ask about Jumeirah Village Circle, using the English name mid-sentence.
- [ ] Say a number with a decimal in it, like one point five million.
- [ ] Interrupt mid-answer and change the subject to handover dates.

