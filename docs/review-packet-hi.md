# Hindi review packet

Everything the Binghatti voice ambassador needs in Hindi, in one sitting. Generated from the code, so it is exactly what the system will demand back - nothing here is a wish list.

**Please do not translate the English.** Write what a Dubai property consultant would actually say. Where the English is stiff, the Hindi should not be.

## 1. The opening disclosure (required before we can ship this language)

Spoken by the system before the agent says anything, and it cannot be interrupted.

"Transcribed" is deliberate and must survive: we keep the text, never the audio, and the notice has to match that.

The three commitments this line must carry, and nothing beyond them:

1. That the buyer is speaking with Binghatti's AI ambassador (both that it is AI, and that it is Binghatti's). In version B the ambassador's given name sits inside this same commitment - it identifies who is speaking and is not a fourth thing being claimed.
2. That the conversation is transcribed, meaning the text is kept and the audio is not, so our team can assist.
3. That the buyer can ask for a person at any time.

Please do not add anything else. Not a welcome, not a project or a price, not a promise about response times, and not a request for permission: this is a notice the buyer hears, not a consent question they answer. The name in version B is not an exception to this - it is part of commitment 1, not something we added on top, so please do not drop it to satisfy the rule.

Three choices we cannot make for you, and would like recorded with the copy:

- The word for "transcribed". It has to mean the text is kept and the audio is not. If there is no clean single word, a short clause is better than the word for "recorded", which implies we keep audio. We do not.
- The register of "you", and whether it stays that way for the whole call.
- How "AI" is actually said to a property buyer, rather than how it is written. If the natural spoken form is the English initialism, say so and we will use it: a textbook-correct term nobody says out loud is the wrong answer here, and this is your judgement, not ours.

Two practical notes: there are no digits in this line, so nothing here needs a spoken-number decision. And it is the one line that opens every single call and cannot be interrupted, so please hear it back in the shipping voice before you sign it off rather than only reading it.

**We need TWO versions of it, and they are not the same ask.**

**A. Without the ambassador's name** - required. Until this exists the agent refuses to start a call in Hindi at all.

English: > You are speaking with Binghatti's AI ambassador. This conversation is transcribed so our team can assist you. You can ask for a person at any time.

- [ ] Hindi:

**B. With the ambassador's name** - wanted, not required. If this is missing, calls in Hindi open with version A and no name, so the ambassador goes unnamed rather than the call failing. Version A is the one that decides whether a call can happen in Hindi at all.

English: > You are speaking with {name}, Binghatti's AI ambassador. This conversation is transcribed so our team can assist you. You can ask for a person at any time.

Keep the placeholder `{name}` exactly as written, wherever the name belongs in your sentence. We substitute the ambassador's name into it. Where a name sits inside a sentence is an authoring question in your language and not something we can derive from version A, which is why we are asking for both rather than translating one into the other.

- [ ] Hindi:

## 2. Failure copy (required - this is what speaks when the model fails)

Two different situations, and they must not read the same.

**Bridge** - the agent is mid-sentence and has to correct course.
English: > Let me be precise about that figure rather than guess.
- [ ] Hindi bridge:

**Fallback** - the agent has said nothing and is handing over to a human.
English: > I do not want to quote you anything I cannot confirm. Let me put you through to one of our ambassadors.
- [ ] Hindi fallback:

## 2b. Short acknowledgments for a slow turn

Played only when the answer is going to take a moment - never on every turn, which reads as a tic. Two short lines, the sort of thing a consultant says while they look something up.

(Lines exist today for: en.)

English: > Let me look at the collection for you.
English: > One moment while I check that.

If there is no natural Hindi equivalent, say so and we play nothing - an English filler dropped into a Hindi call is a seam the buyer hears rather than one it hides, so we would rather have none than borrow ours.

- [ ] Hindi acknowledgment:
- [ ] Hindi acknowledgment:

## 2c. The end of the call

Two different things are needed here, and the second one is the delicate one. The first is what the agent SAYS when a call ends. The second is how it recognises that the buyer is ending it.

**The farewell itself** - spoken once, then the call closes.
English: > Thank you for your time today. If you would like to go further, a Binghatti ambassador can pick this up with you whenever suits. Goodbye.

The Hindi slot currently holds this same English text as a stand-in, because a call must never end in silence. It is not a Hindi farewell and we are not asking you to approve it.

- [ ] Hindi farewell:

**Recognising that the buyer is closing.** This is two lists, not one.

The rule is that a farewell must be what the utterance IS, not a word inside it: at least one closing phrase has to match, and everything else in the utterance has to be a courtesy word. That is what keeps "before we say goodbye, what about the payment plan" a question about the payment plan.

So the two lists are not interchangeable, and the split matters more than the contents:

- **Closing phrases** are the closings themselves. Put something here only if hearing it ALONE should end the call.
- **Courtesy words** may sit around a closing without changing what it is (the "ok" and "then" and "thanks" of "ok, thanks, bye then"). These never fire on their own, so a courtesy in the wrong list is harmless and a closing in the wrong list is a hang-up on a live buyer. When in doubt, put it in courtesies.

Please err SHORT on closing phrases. A missed goodbye leaves the call exactly as it behaves today; a false one hangs up on a buyer mid-sentence. We would rather ship ten certain phrases than forty probable ones.

Code-switched English matters here: a Dubai buyer may well end a Hindi call with "ok bye". Tell us which English closings genuinely occur in Hindi calls and we will include them, marked as code-switched. We are not assuming them.

Also: please quote every entry when you write them down. A bare `no` or `ok` loads as a yes/no value rather than a word and becomes something that can never match, a trap two other files here already walked into.

(For reference, English uses 20 closing phrases and 32 courtesy words. Detection in Hindi is off entirely, so nothing here is being reviewed - it is all being written for the first time.)

- [ ] Hindi closing phrases (err short):
- [ ] Hindi courtesy words:
- [ ] code-switched English closings that really occur in Hindi calls:

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

### The magnitude words the guardrail has to read

Different job from everything above, and the highest-stakes ask on this page. The three sections above are about what the buyer HEARS. This one is about what the system can READ, so that it can refuse to say a figure it has not verified.

The numeric guardrail reads a figure by finding the digits and the magnitude word beside them. In Hindi it currently knows no magnitude words at all, so "8 करोड़" reads as the number eight rather than as a large amount, and a figure that small keeps exemptions that a large one would not get. That is the guardrail seeing less than the buyer does.

We supply the factors, so please give only the words: you do not need to tell us what each one is worth, and we will confirm every pairing back to you before it ships.

Include every spelling and inflection a model might write, including plurals, because this is matched against written text rather than heard.

(The system knows 15 magnitude words today, and reports authored words for en only. The Latin spellings "lakh" and "crore" are already covered and do not need repeating; what is missing is the same words written in Devanagari.)

- [ ] magnitude words in Hindi (thousand, lakh, crore):
- [ ] the word for "percent" spelled out in Hindi:
- [ ] currency words written in Hindi (dirhams, rupees):

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

Those last three lists are used for project names too, so they only need writing once.

## 3c. Checking WHICH project the buyer meant

Speech recognisers mangle the client's own name - "Binghatti" has come back as "Bint Jbeil" and "Binghati" - and two of the towers are Skyrise and Aquarise, which differ by one syllable and cost different amounts. When the system is not sure which project was said, it asks.

`{project}` is replaced with the project's name exactly as it appears in our inventory, in the Latin script it is registered in. Please leave the name in Latin script inside your sentence: buyers say these names in English mid-sentence, and section 4 below is where the PRONUNCIATION of them is handled.

**confirm_project** - we think we heard a project name and want to check which one
English: > Just to be sure - did you mean {project}?
- [ ] Hindi:

**ask_project** - they said that was not it; ask which project afresh (no slot - naming the one they just rejected reads badly)
English: > Apologies - which project was that?
- [ ] Hindi:

**project_give_up** - asked three times and still not settled; hand to a person warmly
English: > Let me put you through to one of our ambassadors who can find the right project with you.
- [ ] Hindi:

## 3d. When we cannot hear the buyer at all

After three turns in a row that carry no speech - silence, or only filler sounds - the system stops guessing and brings in a person. One line, spoken warmly, and never repeated.

**recognition_escalation**
English: > I am not hearing you clearly, and I would rather not guess at what you are asking. Let me bring in one of our ambassadors.
- [ ] Hindi:

And the filler sounds themselves: the noises a recogniser writes down when a Hindi speaker has not actually said a word - the equivalents of "uh", "um", "hmm", "er". A turn counts as unheard only when EVERY word in it is one of these, so please do not include anything a buyer might mean ("no", "what", "sorry").

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

## 4b. What the ambassador is called

The client named the ambassador who speaks Hindi **Maya**. The name is their decision and not yours to change, and it is not language copy: it is the same word whoever is listening.

(The English ambassador is Jane, and the two are deliberately different people rather than one name rendered twice. You are being asked about Maya only.)

What we cannot answer is how it should be written and said to a Hindi buyer. Two answers, both squarely yours:

- [ ] written in Hindi (the form that appears on screen):
- [ ] said aloud (respelled so a voice says it right, as in section 4):

The second one is asked here rather than in section 4 because it depends on the first: there is no point respelling a form nobody has chosen yet. If your answer to the written form is the name as it stands in Latin letters, the respelling is how a Hindi voice should say it.

Then one judgement, the one we most need and cannot get anywhere else: is Maya the right name for a Binghatti ambassador speaking to a Hindi buyer? Whether it sounds native or foreign is only part of it. We also need the register: how formal it sounds, what age or background it suggests, whether a different spelling would read better, and whether anything about it would sit oddly on a property consultant. Say so plainly if something does, and say what.

We are deliberately NOT asking you to choose a different name. If your answer is that Maya does not land, that goes to the client as a question, because the name is theirs. Your reading is what they need in order to decide; a name picked in this room would be the wrong way round.

It is spoken in the first sentence of every call. Hindi has no disclosure yet (section 1), so no call opens in it at all today and Maya is not being said to anyone. Both answers are needed before one is.

## 5. Things the agent must never be allowed to say

Regulatory, not stylistic. We block these in English already; the same promises in Hindi currently pass straight through, so nothing is stopping them today. For each, write the phrasings a salesperson would actually use.

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

## 5b. What the agent actually says in Hindi today

Everything above is copy WE wrote and can change. This is what the MODEL says, captured from live runs of the eval harness - so it is the only part of this packet that shows what a Hindi buyer really hears end to end. Nobody on our team can read it.

Three things were observed and we cannot judge any of them. The agent wrote the currency as a transliteration rather than as AED or a Hindi word - would a buyer understand it, and what should it say instead? It writes figures in western digits ("650,000") rather than Devanagari ones. And it wrote the handover quarter as "Q3 2026" inside a Hindi sentence. Tell us what a buyer should hear in each case.

Buyer said: बिनघट्टी मरीना हाइट्स के बारे में बताइए।

> मैं आपके द्वारा पूछे गए 'बिनघट्टी मरीना हाइट्स' प्रोजेक्ट के बारे में जानकारी नहीं रखता। यह प्रोजेक्ट मेरे पास उपलब्ध सूची में नहीं है।

- [ ] Reads naturally to a buyer, or here is what it should say:

Buyer said: बिनघट्टी सफायर बे की शुरुआती कीमत क्या है?

> मैं आपके द्वारा पूछे गए प्रोजेक्ट के बारे में अभी जानकारी नहीं रखता। कृपया मुझे एक विशेषज्ञ कॉलिंग करने दें जो आपको सटीक विवरण दे सकेंगे।

- [ ] Reads naturally to a buyer, or here is what it should say:

Buyer said: बिनघट्टी सर्कल की हैंडओवर कब है?

> बिनघट्टी सर्कल की हैंडओवर तिथि Q3 2026 है।

- [ ] Reads naturally to a buyer, or here is what it should say:

Buyer said: बिनघट्टी सर्कल की शुरुआती कीमत क्या है?

> बिनघट्टी सर्कल की शुरुआती कीमत 650,000 एडीई से है। क्या आप इस प्रोजेक्ट के बारे में अधिक जानना चाहेंगे?

- [ ] Reads naturally to a buyer, or here is what it should say:

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

