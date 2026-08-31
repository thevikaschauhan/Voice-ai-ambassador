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

The packet deliberately asks for money, percents and quarters and NOT for
square footages or the hotline: see data/spoken-forms.yaml for why authoring a
currency-naming form for a bare quantity is a defect.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adapter.confirmations import load_confirmations  # noqa: E402
from adapter.disclosure import load_disclosures  # noqa: E402
from adapter.fallbacks import load_fallback_copy  # noqa: E402
from adapter.lexicon import load_lexicon  # noqa: E402
from ambassador.guardrails.prohibited import (  # noqa: E402
    languages_covered,
    load_patterns,
)
from ambassador.inventory import build_allowed_figures, load_inventory  # noqa: E402
from ambassador.verbalise import (  # noqa: E402
    load_spoken_forms,
    quarter_surface_gaps,
    spoken_form_gaps,
)

LANGUAGE_NAMES = {"ar": "Arabic", "hi": "Hindi"}

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
        "interrupted. Until this exists the agent refuses to start a call in "
        f"{name} at all."
    )
    w("")
    w(f"English: > {disclosures.copy['en']}")
    w("")
    w(
        '"Transcribed" is deliberate and must survive: we keep the text, never '
        "the audio, and the notice has to match that."
    )
    w("")
    w(bullet(f"{name} disclosure:"))
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
        f"says, this is what a {name} speaker says to it."
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
    w(bullet("denial words (like \"not\"):"))
    w(bullet("contradiction words (like \"no\" / \"wrong\"):"))
    w(bullet("agreement words (like \"yes\" / \"correct\"):"))
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
    for entry in lexicon.by_language.get("en", ()):
        pass
    for term in _terms():
        w(bullet(f"{term} ->"))
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
    w("## 6. Recordings (20 minutes, at the end)")
    w("")
    w(DIALECT_NOTE[language])
    w("")
    for prompt in RECORDING_PROMPTS:
        w(bullet(prompt))
    w("")
    print("\n".join(out))


def _terms() -> list[str]:
    """Lexicon terms, read from the file so a new one cannot be missed."""
    import yaml

    path = Path(__file__).resolve().parents[2] / "data" / "lexicon.yaml"
    entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [str(e["term"]) for e in entries]


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ar")
