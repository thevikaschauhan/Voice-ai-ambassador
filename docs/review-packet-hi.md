# Hindi review packet

Everything the Binghatti voice ambassador needs in Hindi, in one sitting. Generated from the code, so it is exactly what the system will demand back - nothing here is a wish list.

**Please do not translate the English.** Write what a Dubai property consultant would actually say. Where the English is stiff, the Hindi should not be.

## 1. The opening disclosure (required before we can ship this language)

Spoken by the system before the agent says anything, and it cannot be interrupted. Until this exists the agent refuses to start a call in Hindi at all.

English: > You are speaking with Binghatti's AI ambassador. This conversation is transcribed so our team can assist you. You can ask for a person at any time.

"Transcribed" is deliberate and must survive: we keep the text, never the audio, and the notice has to match that.

- [ ] Hindi disclosure:

## 2. Failure copy (required - this is what speaks when the model fails)

Two different situations, and they must not read the same.

**Bridge** - the agent is mid-sentence and has to correct course.
English: > Let me be precise about that figure rather than guess.
- [ ] Hindi bridge:

**Fallback** - the agent has said nothing and is handing over to a human.
English: > I do not want to quote you anything I cannot confirm. Let me put you through to one of our ambassadors.
- [ ] Hindi fallback:

## 3. Money, percentages and dates spoken aloud

The agent never reads digits. Each figure below needs the words a buyer should hear, with the currency named inside the phrase.

Please use lakh and crore as a buyer would - but note that AED
2,400,000 is 24 lakh, never 2.4 crore.

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

List every written form of the currency your phrases above already say aloud - the native word, and any Latin form a model might write mid-sentence when writing Hindi (AED, Dhs). We remove these so the buyer does not hear the currency twice.

- [ ] currency tokens:

## 3b. Checking a buyer's budget back to them

Before recommending anything, the system reads a stated budget back. It never guesses a currency: "two crore" is about AED 880,000 if the buyer meant rupees and AED 20 million if they meant dirhams, and guessing wrong recommends a property twenty times off.

`{amount}` is replaced with what the buyer said, e.g. "2 crore".

**ask_currency** - they gave a number but no currency
English: > {amount} - is that in dirhams or in rupees?
- [ ] Hindi:

**confirm_amount** - they gave both; we read it back to catch a mishearing
English: > {amount} - have I got that right?
- [ ] Hindi:

**ask_amount** - they said the read-back was wrong; ask for the figure afresh (no {amount} slot - repeating the rejected number reads badly)
English: > Apologies - what is the budget, then?
- [ ] Hindi:

**cannot_convert** - their budget is not in dirhams and we will not guess a rate
English: > I would rather not convert that myself and risk being wrong. Let me put you through to one of our ambassadors.
- [ ] Hindi:

**give_up** - they have been asked three times; hand to a person warmly
English: > Let me put you through to one of our ambassadors who can go through the numbers with you properly.
- [ ] Hindi:

### The currency words a buyer might say

Every way a buyer could name dirhams or rupees out loud, so the system hears it. Different from the list above: that one is what the agent says, this is what a Hindi speaker says to it.

- [ ] dirhams:
- [ ] rupees:

And the words that mark a number as a budget rather than a bedroom count - the equivalents of "budget", "spend", "afford", "up to".

- [ ] budget words:

The system also reads a buyer's PUSH-BACK, so a rejected read-back is never recorded as agreement. Two lists: words that deny the currency they sit in front of (the equivalents of "not", as in "not dirhams"), and words that contradict what was just read back (the equivalents of "no", "wrong", "you misheard").

- [ ] denial words (like "not"):
- [ ] contradiction words (like "no" / "wrong"):
- [ ] agreement words (like "yes" / "correct"):

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

Regulatory, not stylistic. We block these in English already; the same promises in Hindi currently pass straight through. For each, write the phrasings a salesperson would actually use.

(Patterns exist today for: en.)

**advice framing** - telling the buyer what they ought to do with their money, rather than describing what is available.
- [ ] Hindi phrasings:

**competitor disparagement** - naming another Dubai developer alongside a negative claim about them.
- [ ] Hindi phrasings:

**future certainty** - stating a future market movement as fact rather than as possibility.
- [ ] Hindi phrasings:

**regulatory overreach** - promising an outcome that a government body decides - a visa, a mortgage approval, a tax treatment.
- [ ] Hindi phrasings:

**return guarantees** - any promise that a return, yield, rental income or price rise is assured, or that an investment carries no risk.
- [ ] Hindi phrasings:

## 6. Recordings (20 minutes, at the end)

Say each line the way you would to a colleague, not the way a newsreader would. Switch into English wherever you normally would - project names, numbers, whole clauses. That mixing is the thing we most need to test, so please do not clean it up.

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

