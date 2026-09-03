"""Generate the native-reviewer packet from the data files themselves.

Three issues (#4, #14, #15) are blocked on the same thing: a person who speaks
Arabic or Hindi authoring the copy nobody on this team may write (AGENTS.md).
That person's time is the long-lead dependency in the whole build, so the job
is to make their session filled-in rather than exploratory.

Generated, not hand-written, for one reason: the figure list has to be
COMPLETE. `verbalise.spoken_form_gaps()` derives it from inventory and the
whitelist, so adding a project cannot leave a price off the packet, and the
list can never drift from what the loaders will actually demand back.

    uv run python tools/reviewer_packet.py ar > packet-ar.md

The packet asks for money, percents and quarters and NOT for square footages:
see data/spoken-forms.yaml for why authoring a currency-naming form for a bare
quantity is a defect. It asks for the hotline SEPARATELY, in its own section,
for the same reason - the digit fallback reads a square footage correctly and
reads a phone number as "eighty thousand and fifteen", so one is not a gap and
the other is, and they must not be asked for on the same page as each other.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adapter.confirmations import load_confirmations  # noqa: E402
from adapter.disclosure import load_disclosures  # noqa: E402
from adapter.fallbacks import load_fallback_copy  # noqa: E402
from adapter.lexicon import load_lexicon  # noqa: E402
from adapter.prerolls import load_prerolls  # noqa: E402
from ambassador.ambassadors import load_ambassadors  # noqa: E402
from ambassador.farewell import load_farewells  # noqa: E402
from ambassador.figures import load_numerals  # noqa: E402
from ambassador.guardrails.prohibited import (  # noqa: E402
    languages_covered,
    load_patterns,
)
from ambassador.inventory import build_allowed_figures, load_inventory  # noqa: E402
from evals.cases import load_cases  # noqa: E402
from ambassador.verbalise import (  # noqa: E402
    identifier_gaps,
    load_spoken_forms,
    quarter_surface_gaps,
    spoken_form_gaps,
)

LANGUAGE_NAMES = {"ar": "Arabic", "hi": "Hindi"}

# Every data file that needs native-authored ar/hi copy, mapped to the heading
# of the section that asks for it.
#
# This exists because the packet's completeness claim was quietly false once.
# #81 added `farewells.yaml` - a new file needing native closing phrases, a
# courtesy list and an authored farewell - and did not add a section here, so
# the packet stopped asking for something the runtime demands and no test
# noticed. The native session it would have been collected in is the scarcest
# input this project has.
#
# The KEYS are checked against the data directory itself by
# `test_reviewer_packet.py`, which finds every file carrying a
# native-authorship `VERIFY:` marker. So this table cannot silently fall
# behind: add such a file without a section here and the suite fails. It is a
# declaration of what the packet asks, deliberately kept next to the code that
# asks it, and it is not the source of truth for which files exist.
NATIVE_COPY_SECTIONS: dict[str, str] = {
    "disclosures.yaml": "## 1. The opening disclosure",
    "fallbacks.yaml": "## 2. Failure copy",
    "prerolls.yaml": "## 2b. Short acknowledgments",
    "farewells.yaml": "## 2c. The end of the call",
    "spoken-forms.yaml": "## 3. Money, percentages and dates spoken aloud",
    "numerals.yaml": "### The magnitude words the guardrail has to read",
    "currencies.yaml": "### The currency words a buyer might say",
    "confirmations.yaml": "## 3b. Checking a buyer's budget back to them",
    "recognition.yaml": "## 3d. When we cannot hear the buyer at all",
    "lexicon.yaml": "## 4. How these names should sound",
    "ambassadors.yaml": "## 4b. What the ambassador is called",
    "prohibited-patterns.yaml": "## 5. Things the agent must never be allowed to say",
}

# Described, not quoted. A reviewer cannot read a regular expression, and a
# description generalises where an example anchors them to one phrasing. It
# also keeps this document clear of AGENTS.md's rule against writing
# guaranteed-return language into project copy - the point here is to have the
# reviewer write the phrasings so we can BLOCK them, but the packet itself
# should not read like a brochure that makes the promise.
# The register the recordings must be in. Arabic and Hindi fail differently:
# Arabic recognisers are trained hardest on Modern Standard Arabic, which
# nobody buys property in, so recording MSA would test the one register that
# does not matter. Hindi's problem is not register but code-switching.
# What to ask about the agent's OWN words, per language. The rest of this packet
# reviews copy WE wrote and can change; this section reviews what the MODEL says,
# which nobody has read and which is what a buyer actually hears. Each entry
# names concretely what was observed in the recorded replies below, because a
# reviewer given "does this read naturally?" and nothing else will say yes.
SPEECH_NOTE = {
    "ar": (
        "Two things were observed in these replies and we cannot judge either. "
        "The agent keeps project names in Latin script mid-sentence "
        '("Binghatti Skyrise"), and it writes figures in western digits '
        '("985,000") rather than Arabic-Indic ones. Both may be exactly right '
        "for a Dubai buyer and both may be jarring. Tell us which."
    ),
    "hi": (
        "Three things were observed and we cannot judge any of them. The agent "
        "wrote the currency as a transliteration rather than as AED or a Hindi "
        "word - would a buyer understand it, and what should it say instead? It "
        'writes figures in western digits ("650,000") rather than Devanagari '
        'ones. And it wrote the handover quarter as "Q3 2026" inside a Hindi '
        "sentence. Tell us what a buyer should hear in each case."
    ),
}

# The English glosses for the magnitude ask, per language.
#
# Deliberately NOT the same list twice. Hindi is asked about the Indian
# numbering it actually writes; Arabic is asked openly, without being primed
# with "lakh" and "crore". `numerals.yaml` does say the borrowings belong in
# the Arabic table, and an Arabic reviewer who writes them will volunteer them
# when asked for every magnitude word - whereas naming them first invites a
# dutiful yes, and a multiplier entered on a guess is a figure wrong by ten or
# a hundred times. The open question is the safer one, and it is also why the
# packet keeps Indian numbering out of the Arabic document entirely.
MAGNITUDE_GLOSS = {
    "ar": "thousand, million, billion",
    "hi": "thousand, lakh, crore",
}

DIALECT_NOTE = {
    "ar": (
        "Say each line naturally, in your own dialect - Emirati, Egyptian, "
        "Levantine, whatever you actually speak. Please do NOT read these in "
        "Modern Standard Arabic: speech recognisers are strongest on MSA and "
        "nobody buys property in it, so recording MSA would test the one "
        "register that does not matter. Switch into English wherever you "
        "normally would."
    ),
    "hi": (
        "Say each line the way you would to a colleague, not the way a "
        "newsreader would. Switch into English wherever you normally would - "
        "project names, numbers, whole clauses. That mixing is the thing we "
        "most need to test, so please do not clean it up."
    ),
}

CATEGORY_BRIEFS = {
    "return_guarantees": (
        "any promise that a return, yield, rental income or price rise is "
        "assured, or that an investment carries no risk."
    ),
    "advice_framing": (
        "telling the buyer what they ought to do with their money, rather "
        "than describing what is available."
    ),
    "future_certainty": (
        "stating a future market movement as fact rather than as possibility."
    ),
    "regulatory_overreach": (
        "promising an outcome that a government body decides - a visa, a "
        "mortgage approval, a tax treatment."
    ),
    "competitor_disparagement": (
        "naming another Dubai developer alongside a negative claim about them."
    ),
}

# The utterances the recording half of #4 needs. English glosses only: the
# reviewer says these naturally in their own dialect, which is the entire
# point - a script written in Modern Standard Arabic would test the one
# register nobody buys property in.
RECORDING_PROMPTS = [
    "Ask what a studio costs at Binghatti Skyrise.",
    "Say your budget is two million dirhams.",
    "Say your budget is two crore, without saying which currency.",
    "Ask when the Dubai Maritime City tower hands over.",
    "Ask whether the payment plan can be changed.",
    "Ask for the price of the Bugatti Residences.",
    "Say you want to speak to a person instead.",
    "Ask about Jumeirah Village Circle, using the English name mid-sentence.",
    "Say a number with a decimal in it, like one point five million.",
    "Interrupt mid-answer and change the subject to handover dates.",
]


def bullet(item: str) -> str:
    return f"- [ ] {item}"


def mag_example(language: str) -> str:
    """A magnitude word the reviewer will recognise, in their own script.

    Quoting one is safe where authoring a list is not: these two words are
    the ones `numerals.yaml` names in its own VERIFY comments as the examples
    of what is missing, so this echoes the repository rather than inventing
    copy (AGENTS.md).
    """
    return {
        "ar": "\u0645\u0644\u064a\u0648\u0646",
        "hi": "\u0915\u0930\u094b\u0921\u093c",
    }[language]


def article(word: str) -> str:
    """ "an Arabic", "a Hindi". The packet is the first thing a native reviewer
    reads, and it is generated - so the grammar has to be generated too."""
    return "an" if word[:1].upper() in "AEIOU" else "a"


def main(language: str) -> None:
    if language not in LANGUAGE_NAMES:
        raise SystemExit(f"usage: reviewer_packet.py {'|'.join(LANGUAGE_NAMES)}")
    name = LANGUAGE_NAMES[language]

    allowed = build_allowed_figures(load_inventory())
    forms = load_spoken_forms()
    gaps = spoken_form_gaps(forms, allowed, language)
    quarters = quarter_surface_gaps(forms, language)
    disclosures = load_disclosures()
    fallbacks = load_fallback_copy()
    lexicon = load_lexicon()
    prerolls = load_prerolls()
    farewells = load_farewells()
    numerals = load_numerals()
    patterns = load_patterns()
    confirmations = load_confirmations()

    out: list[str] = []
    w = out.append

    w(f"# {name} review packet")
    w("")
    w(
        f"Everything the Binghatti voice ambassador needs in {name}, in one "
        "sitting. Generated from the code, so it is exactly what the system "
        "will demand back - nothing here is a wish list."
    )
    w("")
    w(
        "**Please do not translate the English.** Write what a Dubai property "
        "consultant would actually say. Where the English is stiff, the "
        f"{name} should not be."
    )
    w("")
    w("## 1. The opening disclosure (required before we can ship this language)")
    w("")
    w(
        "Spoken by the system before the agent says anything, and it cannot be "
        "interrupted."
    )
    w("")
    w(
        '"Transcribed" is deliberate and must survive: we keep the text, never '
        "the audio, and the notice has to match that."
    )
    w("")
    w("The three commitments this line must carry, and nothing beyond them:")
    w("")
    w(
        "1. That the buyer is speaking with Binghatti's AI ambassador (both "
        "that it is AI, and that it is Binghatti's). In version B the "
        "ambassador's given name sits inside this same commitment - it "
        "identifies who is speaking and is not a fourth thing being claimed."
    )
    w(
        "2. That the conversation is transcribed, meaning the text is kept and "
        "the audio is not, so our team can assist."
    )
    w("3. That the buyer can ask for a person at any time.")
    w("")
    w(
        "Please do not add anything else. Not a welcome, not a project or a "
        "price, not a promise about response times, and not a request for "
        "permission: this is a notice the buyer hears, not a consent question "
        "they answer. The name in version B is not an exception to this - it "
        "is part of commitment 1, not something we added on top, so please do "
        "not drop it to satisfy the rule."
    )
    w("")
    w("Three choices we cannot make for you, and would like recorded with the copy:")
    w("")
    w(
        '- The word for "transcribed". It has to mean the text is kept and the '
        "audio is not. If there is no clean single word, a short clause is "
        'better than the word for "recorded", which implies we keep audio. We '
        "do not."
    )
    w('- The register of "you", and whether it stays that way for the whole call.')
    w(
        '- How "AI" is actually said to a property buyer, rather than how it '
        "is written. If the natural spoken form is the English initialism, say "
        "so and we will use it: a textbook-correct term nobody says out loud "
        "is the wrong answer here, and this is your judgement, not ours."
    )
    w("")
    w(
        "Two practical notes: there are no digits in this line, so nothing "
        "here needs a spoken-number decision. And it is the one line that "
        "opens every single call and cannot be interrupted, so please hear it "
        "back in the shipping voice before you sign it off rather than only "
        "reading it."
    )
    w("")
    # Two sentences, two different stakes, and conflating them overstates one
    # and understates the other. The unnamed sentence is what makes the
    # language shippable at all; the named one is an improvement whose absence
    # falls back rather than refusing.
    w("**We need TWO versions of it, and they are not the same ask.**")
    w("")
    w(
        f"**A. Without the ambassador's name** - required. Until this exists "
        f"the agent refuses to start a call in {name} at all."
    )
    w("")
    w(f"English: > {disclosures.copy['en']}")
    w("")
    w(bullet(f"{name}:"))
    w("")
    named = disclosures.named_copy.get("en", "")
    if named:
        w(
            "**B. With the ambassador's name** - wanted, not required. If this "
            f"is missing, calls in {name} simply open with version A and no "
            "name, which is what happens today."
        )
        w("")
        w(f"English: > {named}")
        w("")
        w(
            "Keep the placeholder `{name}` exactly as written, wherever the "
            "name belongs in your sentence. We substitute the ambassador's "
            "name into it. Where a name sits inside a sentence is an authoring "
            "question in your language and not something we can derive from "
            "version A, which is why we are asking for both rather than "
            "translating one into the other."
        )
        w("")
        w(bullet(f"{name}:"))
        w("")
    w("## 2. Failure copy (required - this is what speaks when the model fails)")
    w("")
    w("Two different situations, and they must not read the same.")
    w("")
    w("**Bridge** - the agent is mid-sentence and has to correct course.")
    w(f"English: > {fallbacks.bridge['en']}")
    w(bullet(f"{name} bridge:"))
    w("")
    w("**Fallback** - the agent has said nothing and is handing over to a human.")
    w(f"English: > {fallbacks.fallback['en']}")
    w(bullet(f"{name} fallback:"))
    w("")
    w("## 2b. Short acknowledgments for a slow turn")
    w("")
    w(
        "Played only when the answer is going to take a moment - never on every "
        "turn, which reads as a tic. Two short lines, the sort of thing a "
        "consultant says while they look something up."
    )
    w("")
    covered_prerolls = sorted(prerolls.languages_covered())
    w(f"(Lines exist today for: {', '.join(covered_prerolls) or 'none'}.)")
    w("")
    for line in prerolls.for_language("en"):
        w(f"English: > {line}")
    w("")
    w(
        f"If there is no natural {name} equivalent, say so and we play nothing "
        "- an English filler dropped into "
        f"{article(name)} {name} call is a seam the buyer hears rather than one "
        "it hides, so we would rather have none than borrow ours."
    )
    w("")
    existing = prerolls.for_language(language)
    for line in existing:
        w(f"Already have: > {line}")
    if existing:
        w("")
    w(bullet(f"{name} acknowledgment:"))
    w(bullet(f"{name} acknowledgment:"))
    w("")
    w("## 2c. The end of the call")
    w("")
    w(
        "Two different things are needed here, and the second one is the "
        "delicate one. The first is what the agent SAYS when a call ends. The "
        "second is how it recognises that the buyer is ending it."
    )
    w("")
    w("**The farewell itself** - spoken once, then the call closes.")
    stand_in = farewells.speech.get(language, "") == farewells.speech["en"]
    w(f"English: > {farewells.speech['en']}")
    if stand_in:
        w("")
        w(
            f"The {name} slot currently holds this same English text as a "
            "stand-in, because a call must never end in silence. It is not "
            f"{article(name)} {name} farewell and we are not asking you to "
            "approve it."
        )
    w("")
    w(bullet(f"{name} farewell:"))
    w("")
    w("**Recognising that the buyer is closing.** This is two lists, not one.")
    w("")
    w(
        "The rule is that a farewell must be what the utterance IS, not a word "
        "inside it: at least one closing phrase has to match, and everything "
        "else in the utterance has to be a courtesy word. That is what keeps "
        '"before we say goodbye, what about the payment plan" a question about '
        "the payment plan."
    )
    w("")
    w(
        "So the two lists are not interchangeable, and the split matters more than the contents:"
    )
    w("")
    w(
        "- **Closing phrases** are the closings themselves. Put something here "
        "only if hearing it ALONE should end the call."
    )
    w(
        "- **Courtesy words** may sit around a closing without changing what it "
        'is (the "ok" and "then" and "thanks" of "ok, thanks, bye then"). '
        "These never fire on their own, so a courtesy in the wrong list is "
        "harmless and a closing in the wrong list is a hang-up on a live "
        "buyer. When in doubt, put it in courtesies."
    )
    w("")
    w(
        "Please err SHORT on closing phrases. A missed goodbye leaves the call "
        "exactly as it behaves today; a false one hangs up on a buyer "
        "mid-sentence. We would rather ship ten certain phrases than forty "
        "probable ones."
    )
    w("")
    w(
        "Code-switched English matters here: a Dubai buyer may well end "
        f'{article(name)} {name} call with "ok bye". Tell us which English '
        f"closings genuinely occur in {name} calls and we will include them, "
        "marked as code-switched. We are not assuming them."
    )
    w("")
    w(
        "Also: please quote every entry when you write them down. A bare `no` "
        "or `ok` loads as a yes/no value rather than a word and becomes "
        "something that can never match, a trap two other files here already "
        "walked into."
    )
    w("")
    w(
        f"(For reference, English uses {len(farewells.phrases['en'])} closing "
        f"phrases and {len(farewells.courtesies['en'])} courtesy words. "
        f"Detection in {name} is off entirely today.)"
    )
    w("")
    w(bullet(f"{name} closing phrases (err short):"))
    w(bullet(f"{name} courtesy words:"))
    w(bullet(f"code-switched English closings that really occur in {name} calls:"))
    w("")
    w("## 3. Money, percentages and dates spoken aloud")
    w("")
    w(
        "The agent never reads digits. Each figure below needs the words a "
        "buyer should hear, with the currency named inside the phrase."
    )
    w("")
    if language == "hi":
        # Only meaningful where lakh and crore are the units people think in,
        # and it is a ten-times error rather than a rounding one.
        w("Please use lakh and crore as a buyer would - but note that AED")
        w("2,400,000 is 24 lakh, never 2.4 crore.")
        w("")
    amounts = gaps.get("amount", [])
    w(f"### Amounts in dirhams ({len(amounts)})")
    w("")
    for value in amounts:
        w(bullet(f"AED {int(value):,} ->"))
    w("")
    percents = gaps.get("percent", [])
    w(f"### Percentages ({len(percents)})")
    w("")
    for value in percents:
        w(bullet(f"{int(value)}% ->"))
    w("")
    w(f"### Handover quarters ({len(quarters)})")
    w("")
    w('Spoken as "the fourth quarter of 2026", never "Q four".')
    w("")
    for surface in quarters:
        w(bullet(f"{surface} ->"))
    w("")
    w("### The words next to the money")
    w("")
    w(
        "List every written form of the currency your phrases above already "
        "say aloud - the native word, and any Latin form a model might write "
        f"mid-sentence when writing {name} (AED, Dhs). We remove these so the "
        "buyer does not hear the currency twice."
    )
    w("")
    w(bullet("currency tokens:"))
    w("")
    w("### The magnitude words the guardrail has to read")
    w("")
    w(
        "Different job from everything above, and the highest-stakes ask on "
        "this page. The three sections above are about what the buyer HEARS. "
        "This one is about what the system can READ, so that it can refuse to "
        "say a figure it has not verified."
    )
    w("")
    w(
        f"The numeric guardrail reads a figure by finding the digits and the "
        f"magnitude word beside them. In {name} it currently knows no "
        f'magnitude words at all, so "8 {mag_example(language)}" reads as the '
        "number eight rather than as a large amount, and a figure that small "
        "keeps exemptions that a large one would not get. That is the "
        "guardrail seeing less than the buyer does."
    )
    w("")
    w(
        "We supply the factors, so please give only the words: you do not need "
        "to tell us what each one is worth, and we will confirm every pairing "
        "back to you before it ships."
    )
    w("")
    w(
        "Include every spelling and inflection a model might write, including "
        "plurals, because this is matched against written text rather than "
        "heard."
    )
    w("")
    known = (
        f"(The system knows {len(numerals.multipliers)} magnitude words today, "
        f"and reports authored words for "
        f"{', '.join(sorted(numerals.languages))} only."
    )
    if language == "hi":
        # Indian English writes these in Latin script and every list applies to
        # every language, so they are genuinely already covered - a point worth
        # making to a Hindi reviewer and noise to an Arabic one.
        known += (
            ' The Latin spellings "lakh" and "crore" are already covered and '
            "do not need repeating; what is missing is the same words written "
            "in Devanagari.)"
        )
    else:
        known += f" What is missing is the words written in {name}.)"
    w(known)
    w("")
    w(bullet(f"magnitude words in {name} ({MAGNITUDE_GLOSS[language]}):"))
    w(bullet(f'the word for "percent" spelled out in {name}:'))
    w(bullet(f"currency words written in {name} (dirhams, rupees):"))
    w("")
    w("## 3b. Checking a buyer's budget back to them")
    w("")
    w(
        "Before recommending anything, the system reads a stated budget back. "
        'It never guesses a currency: "two crore" is about AED 880,000 if '
        "the buyer meant rupees and AED 20 million if they meant dirhams, and "
        "guessing wrong recommends a property twenty times off."
    )
    w("")
    w('`{amount}` is replaced with what the buyer said, e.g. "2 crore".')
    w("")
    for key, gloss in (
        ("ask_currency", "they gave a number but no currency"),
        ("confirm_amount", "they gave both; we read it back to catch a mishearing"),
        (
            "ask_amount",
            "they said the read-back was wrong; ask for the figure afresh "
            "(no {amount} slot - repeating the rejected number reads badly)",
        ),
        (
            "cannot_convert",
            "their budget is not in dirhams and we will not guess a rate",
        ),
        ("give_up", "they have been asked three times; hand to a person warmly"),
    ):
        w(f"**{key}** - {gloss}")
        w(f"English: > {confirmations.line('en', key)}")
        w(bullet(f"{name}:"))
        w("")
    w("### The currency words a buyer might say")
    w("")
    w(
        "Every way a buyer could name dirhams or rupees out loud, so the system "
        f"hears it. Different from the list above: that one is what the agent "
        f"says, this is what {article(name)} {name} speaker says to it."
    )
    w("")
    w(bullet("dirhams:"))
    w(bullet("rupees:"))
    w("")
    w(
        "And the words that mark a number as a budget rather than a bedroom "
        'count - the equivalents of "budget", "spend", "afford", "up to".'
    )
    w("")
    w(bullet("budget words:"))
    w("")
    w(
        "The system also reads a buyer's PUSH-BACK, so a rejected read-back is "
        "never recorded as agreement. Two lists: words that deny the currency "
        'they sit in front of (the equivalents of "not", as in "not dirhams"), '
        "and words that contradict what was just read back (the equivalents "
        'of "no", "wrong", "you misheard").'
    )
    w("")
    w(bullet('denial words (like "not"):'))
    w(bullet('contradiction words (like "no" / "wrong"):'))
    w(bullet('agreement words (like "yes" / "correct"):'))
    w("")
    w(
        "Those last three lists are used for project names too, so they only "
        "need writing once."
    )
    w("")
    w("## 3c. Checking WHICH project the buyer meant")
    w("")
    w(
        'Speech recognisers mangle the client\'s own name - "Binghatti" has '
        'come back as "Bint Jbeil" and "Binghati" - and two of the '
        "towers are Skyrise and Aquarise, which differ by one syllable and "
        "cost different amounts. When the system is not sure which project was "
        "said, it asks."
    )
    w("")
    w(
        "`{project}` is replaced with the project's name exactly as it appears "
        "in our inventory, in the Latin script it is registered in. Please "
        "leave the name in Latin script inside your sentence: buyers say these "
        "names in English mid-sentence, and section 4 below is where the "
        "PRONUNCIATION of them is handled."
    )
    w("")
    for key, gloss in (
        (
            "confirm_project",
            "we think we heard a project name and want to check which one",
        ),
        (
            "ask_project",
            "they said that was not it; ask which project afresh (no slot - "
            "naming the one they just rejected reads badly)",
        ),
        (
            "project_give_up",
            "asked three times and still not settled; hand to a person warmly",
        ),
    ):
        w(f"**{key}** - {gloss}")
        w(f"English: > {confirmations.line('en', key)}")
        w(bullet(f"{name}:"))
        w("")
    w("## 3d. When we cannot hear the buyer at all")
    w("")
    w(
        "After three turns in a row that carry no speech - silence, or only "
        "filler sounds - the system stops guessing and brings in a person. One "
        "line, spoken warmly, and never repeated."
    )
    w("")
    w("**recognition_escalation**")
    w(f"English: > {confirmations.line('en', 'recognition_escalation')}")
    w(bullet(f"{name}:"))
    w("")
    w(
        "And the filler sounds themselves: the noises a recogniser writes down "
        f"when {article(name)} {name} speaker has not actually said a word - "
        "the equivalents "
        'of "uh", "um", "hmm", "er". A turn counts as unheard only when EVERY '
        "word in it is one of these, so please do not include anything a buyer "
        'might mean ("no", "what", "sorry").'
    )
    w("")
    w(bullet("filler sounds:"))
    w("")
    identifiers = identifier_gaps(forms, allowed, language)
    if identifiers:
        w("## 3e. The numbers that are not quantities")
        w("")
        w(
            "Read as a SEQUENCE, the way a phone number is read - not as a "
            "quantity, and naming no currency. This is deliberately a separate "
            "question from section 3: every figure there is money and its "
            "phrase says so, and the same treatment here would turn a hotline "
            "number into a sum of dirhams."
        )
        w("")
        w(
            "Today they go to the voice as bare digits, so each number below "
            "is read aloud as one quantity rather than as a sequence."
        )
        w("")
        w(
            "`VERIFY:` the digits themselves are still being confirmed with "
            "the client, so please write the READING rather than checking the "
            "number - if it changes, the pattern you give us carries over."
        )
        w("")
        notes = _identifier_notes()
        for value in identifiers:
            note = notes.get(int(value))
            w(bullet(f"{int(value)}{f' ({note})' if note else ''} ->"))
        w("")
    w("## 4. How these names should sound")
    w("")
    already = sorted(lexicon.languages_covered())
    w(f"(Respellings exist today for: {', '.join(already) or 'none'}.)")
    w("")
    w(
        "Written so a text-to-speech voice says them correctly, in your own "
        "script - not in English respelling. Getting the client's own name "
        "wrong in their boardroom is the one unrecoverable mistake."
    )
    w("")
    # The ambassador's given name is a lexicon term (its respelling has to land
    # somewhere, and this is the file the TTS path reads), but it is asked for
    # in 4b rather than here. Two reasons, both visible only in the rendered
    # document: this list's preamble is about the client's name and Dubai
    # places, so a person's given name reads as though it were one of them; and
    # section 4 would be asking how to SAY a name whose written form is still an
    # open question one section below. Answering that question and pronouncing
    # its answer belong together.
    ambassador_name = load_ambassadors().name_for("en")
    for term in _terms():
        if term == ambassador_name:
            continue
        w(bullet(f"{term} ->"))
    w("")
    # Wording is pam's (packet owner). The one thing it deliberately does NOT
    # ask for is a replacement name: handing a reviewer that decision is the
    # same error as asking this team to author the disclosure, pointed the
    # other way. Their reading is what the client needs in order to choose.
    w("## 4b. What the ambassador is called")
    w("")
    english = load_ambassadors().name_for("en") or "(not chosen yet)"
    w(
        f"The client named the English ambassador {english}. The name is their "
        "decision and not yours to change, and it is not language copy: it is "
        "the same word whoever is listening."
    )
    w("")
    w(
        "What we cannot answer is how it should be written and said to a "
        f"{name} buyer. Two answers, both squarely yours:"
    )
    w("")
    w(bullet(f"written in {name} (the form that appears on screen):"))
    w(bullet("said aloud (respelled so a voice says it right, as in section 4):"))
    w("")
    w(
        "The second one is asked here rather than in section 4 because it "
        "depends on the first: there is no point respelling a form nobody has "
        "chosen yet. If your answer to the written form is the English name as "
        "it stands, the respelling is how a "
        f"{name} voice should say it."
    )
    w("")
    w(
        "Then one judgement, the one we most need and cannot get anywhere "
        "else: does an English given name land naturally on a "
        f"{name} buyer's ear for a brand's ambassador, or does it read as "
        "foreign, hard to say, or simply odd? Say so plainly if it does not, "
        "and say why."
    )
    w("")
    w(
        "We are deliberately NOT asking you to choose a different name. If "
        f"your answer is that {english} does not land, that goes to the client "
        "as a question, because the name is theirs. Your reading is what they "
        "need in order to decide; a name picked in this room would be the "
        "wrong way round."
    )
    w("")
    w(
        "It is spoken in the first sentence of every call, so a form that "
        f"reads oddly is the first thing the buyer hears. Until you answer, "
        f"calls in {name} open without a name, exactly as they do today."
    )
    w("")
    w("## 5. Things the agent must never be allowed to say")
    w("")
    w(
        "Regulatory, not stylistic. We block these in English already; the "
        f"same promises in {name} currently pass straight through. For each, "
        "write the phrasings a salesperson would actually use."
    )
    w("")
    covered = languages_covered(patterns)
    w(f"(Patterns exist today for: {', '.join(sorted(covered))}.)")
    w("")
    for category in sorted({p.category for p in patterns}):
        w(f"**{category.replace('_', ' ')}** - {CATEGORY_BRIEFS.get(category, '')}")
        w(bullet(f"{name} phrasings:"))
        w("")
    w(f"## 5b. What the agent actually says in {name} today")
    w("")
    w(
        "Everything above is copy WE wrote and can change. This is what the "
        "MODEL says, captured from live runs of the eval harness - so it is the "
        f"only part of this packet that shows what a {name} buyer really hears "
        "end to end. Nobody on our team can read it."
    )
    w("")
    w(SPEECH_NOTE[language])
    w("")
    recorded = _recorded_replies(language)
    if not recorded:
        # Stated rather than left blank: an empty section reads as "nothing to
        # review here", and the truth is that nobody has recorded this language
        # yet, which is itself the finding.
        w(
            "**Nothing recorded yet for this language.** Run "
            "`uv run eval --live --category <name>` and regenerate this packet "
            "before the session, or this page asks a reviewer to bless copy "
            "the product may not even produce."
        )
        w("")
    for buyer, reply in recorded:
        w(f"Buyer said: {buyer}")
        w("")
        w(f"> {reply}")
        w("")
        w(bullet("Reads naturally to a buyer, or here is what it should say:"))
        w("")

    w("## 6. Recordings (20 minutes, at the end)")
    w("")
    w(DIALECT_NOTE[language])
    w("")
    for prompt in RECORDING_PROMPTS:
        w(bullet(prompt))
    w("")
    print("\n".join(out))


def _recorded_replies(language: str) -> list[tuple[str, str]]:
    """(buyer utterance, model reply) for every RECORDED fixture in this language.

    Recorded only, deliberately. An authored fixture is text the build team
    invented to stand for a model behaviour, and asking a native speaker to
    judge our invented Arabic would waste the scarcest time in the project on
    something no buyer will ever hear. A recorded reply is the product speaking.

    Read from the eval cases rather than pasted here, so re-recording a fixture
    updates the packet instead of leaving it a stale snapshot.
    """
    replies: list[tuple[str, str]] = []
    for case in load_cases():
        if case.language != language:
            continue
        for turn in case.turns:
            fixture = turn.model
            while fixture is not None:
                if fixture.source == "recorded" and fixture.text.strip():
                    entry = (turn.buyer.strip(), " ".join(fixture.text.split()))
                    if entry not in replies:
                        replies.append(entry)
                fixture = fixture.retry
    return replies


def _identifier_notes() -> dict[int, str]:
    """What each whitelisted identifier IS, in the reviewer's words.

    Generated rather than typed for the reason the rest of this file is: today
    there is exactly one identifier and a hand-written "Binghatti's toll-free
    hotline" beside it would read correctly and be wrong the day a permit
    number is whitelisted, with nothing to notice.

    Taken from the whitelist's own `why`, up to its first full stop. The rest
    of that field is the VERIFY: note and the reasoning, which is written for
    us and not for a reviewer.
    """
    import yaml

    path = Path(__file__).resolve().parents[2] / "data" / "whitelist.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    notes: dict[int, str] = {}
    for entry in data.get("amounts") or []:
        if entry.get("kind") != "identifier":
            continue
        why = str(entry.get("why") or "").strip()
        first = why.split(".")[0].strip()
        if first:
            notes[int(entry["value"])] = first
    return notes


def _terms() -> list[str]:
    """Lexicon terms, read from the file so a new one cannot be missed."""
    import yaml

    path = Path(__file__).resolve().parents[2] / "data" / "lexicon.yaml"
    entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [str(e["term"]) for e in entries]


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ar")
