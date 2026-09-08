"""Language changes use final STT evidence and retain conversation state.

Closes review finding P1a: at 3ab4928 a SINGLE utterance could flip the
response language for the rest of the call, and a project name was enough to
do it: `Binghatti Skyrise` is 16 letters, and once the recogniser tagged those
letters as German that was enough. So saying the client's own product name in
an English call could switch the agent out of English - and the language
decides which guardrail patterns, farewell phrases and disclosure copy apply
for the rest of the call (ADR-010).

(god's report named `Binghatti Skyhall` and `Mercedes Benz Places`. Neither is
one of ours - they were illustrations - which matters here only because a term
list can exclude a real project and can never exclude the second kind.)

Two independent barriers, and they fail apart, which is why both are here:

  the terms      brand and project names are removed BEFORE the letters are
                 counted, so a name carries no evidence at all. Supplied as a
                 pure parameter - the list lives in the adapter beside the
                 recogniser's keyterms and the core may not import it
                 (ADR-002).
  the turns      a challenger must lead on TWO CONSECUTIVE TURNS. Counted per
                 INVOCATION, not per segment: one buyer turn can produce
                 several finals (Soniox emits one per endpoint, which is why
                 segments are a list), so a per-segment count would let one
                 talkative utterance satisfy the requirement by itself and
                 `Mercedes Benz Places` would flip the call after all. Caught
                 by dwight before this was written; the test that would fail
                 on the per-segment version is
                 `test_two_finals_in_one_turn_are_still_only_one_turn`.

The terms alone do not stop `Mercedes Benz Places`, which is nobody's brand
term, and the turns alone do not stop a buyer who keeps saying the client's
name across several turns. Neither barrier is redundant.
"""

from typing import get_args

import pytest

from ambassador.schemas import Language

# Imports of the module under test live INSIDE each test and helper, so a RED
# run reads N failed cases rather than one collection error (docs/06- counts
# cases; same convention as test_ingestion.py).

# What the adapter will pass in (dwight owns the real list: BRAND_KEYTERMS plus
# project and area names from inventory). Kept EXPLICIT here rather than
# imported from his assembly on purpose: a change to inventory must never
# quietly turn one of these assertions green.
# These are the real sixteen `switch_excluded_terms()` returns, longest first.
# `Skyhall` is NOT among them: it was one of god's illustrations, not one of
# our projects, and a test asserting it is excluded would go green on the
# LETTER THRESHOLD (seven letters) whether the exclusion existed or not -
# dwight's catch, and the reason the cases below assert both directions.
TERMS = (
    "Bugatti Residences by Binghatti",
    "Jumeirah Village Circle",
    "Dubai Maritime City",
    "Binghatti Aquarise",
    "Bugatti Residences",
    "Binghatti Skyrise",
    "Binghatti Circle",
    "Burj Binghatti",
    "Business Bay",
    "Jacob and Co",
    "Al Jaddaf",
    "Binghatti",
    "Skyrise",
    "dirhams",
    "Meydan",
    "AED",
)


def settle(current, *turns, terms=TERMS):
    """Drive several TURNS through the rule and return the language it lands on.

    Each argument is one turn's list of `(code, transcript)` segments, so a
    test says how many turns it took as plainly as what was said in them.
    """
    from ambassador.language_switch import SwitchProgress, choose_language

    progress = SwitchProgress()
    language = current
    for segments in turns:
        language, progress = choose_language(
            language, segments, progress=progress, excluded_terms=terms
        )
    return language


# --- the switch still works ------------------------------------------------


@pytest.mark.parametrize("language", get_args(Language))
def test_a_sustained_speaker_of_any_supported_language_is_followed(language):
    # The `language in get_args(Language)` assertion that used to be here was
    # a tautology - the parameter comes from get_args(Language), so it could
    # not fail, and it added ten cases without asking anything.
    speech = [(language, "a sufficiently long recognised utterance")]
    assert settle("en", speech, speech) == language


def test_a_genuine_two_turn_german_buyer_switches():
    """god's positive case. The rule must not be so cautious it never fires."""
    assert (
        settle(
            "en",
            [("de", "Guten Tag ich suche eine Wohnung in Dubai")],
            [("de", "Was kostet eine Wohnung mit zwei Schlafzimmern")],
        )
        == "de"
    )


# --- one utterance can never flip the call ---------------------------------


def test_one_turn_is_never_enough_however_strong_the_evidence():
    assert settle("en", [("de", "Guten Tag ich suche eine Wohnung in Dubai")]) == "en"


def test_two_finals_in_one_turn_are_still_only_one_turn():
    """The hole a per-SEGMENT count would leave open.

    One buyer turn can carry several finals. If the streak advanced per
    segment this single turn would satisfy the two-turn requirement by itself,
    and every other case in this file would still pass - which is exactly the
    shape of bug that ships.
    """
    one_talkative_turn = [
        ("de", "Guten Tag ich suche eine Wohnung"),
        ("de", "Was kostet eine Wohnung mit zwei Schlafzimmern"),
    ]
    assert settle("en", one_talkative_turn) == "en"


def test_a_challenger_has_to_lead_on_CONSECUTIVE_turns():
    """Interrupted evidence starts over, or a buyer who says one German
    sentence every few minutes eventually switches the call by accumulation."""
    german = [("de", "Guten Tag ich suche eine Wohnung in Dubai")]
    english = [("en", "sorry I would rather continue in English please")]
    assert settle("en", german, english, german) == "en"


# --- a name is not evidence (god's single-word cases) ----------------------


@pytest.mark.parametrize(
    "name",
    [
        "Binghatti",
        "Binghatti Skyrise",
        "Bugatti Residences by Binghatti",
        "Business Bay",
    ],
)
def test_a_project_or_area_name_is_not_evidence_of_a_language(name):
    """A real inventory name, tagged as German by the recogniser.

    The SECOND assertion is what makes this a test of the exclusion rather
    than of the letter threshold. Each of these is long enough to take the
    call on its own once the terms are gone - `Binghatti` is 9, the others 12
    to 28 - so deleting the exclusion turns this red instead of leaving it
    green for the wrong reason.

    That distinction is dwight's catch. My first draft asserted `Skyhall` and
    `Binghatti Skyhall`, and `Skyhall` is seven letters: under the threshold,
    so it holds whether or not anything is excluded. Neither is one of our
    projects either - both were god's illustrations - so the list could not
    have contained them anyway.

    An area is here for the same reason as a project: a buyer naming the
    district they want to live in has not changed language.
    """
    said = [("de", name)]
    assert settle("en", said, said) == "en"
    assert settle("en", said, said, terms=()) == "de", (
        "with no terms excluded this name DOES take the call, which is what "
        "makes the assertion above about the exclusion and not the threshold"
    )


@pytest.mark.parametrize("utterance", ["okay", "ok sure", "AED", "Skyhall"])
def test_a_short_utterance_is_too_little_evidence(utterance):
    """The other barrier, named for what it actually exercises.

    These hold on the LETTER THRESHOLD - four, six, three and seven letters
    against a minimum of eight - so they are asserted with NO terms excluded.
    Keeping them separate from the names above is the point: read as exclusion
    cases they would be four tests that cannot fail.
    """
    said = [("de", utterance)]
    assert settle("en", said, said, terms=()) == "en"


def test_mercedes_benz_places_is_stopped_by_the_turns_not_the_terms():
    """18 letters and nobody's brand term - it is not in the list and cannot
    be, since it is not our product. One turn holds; this is the case that
    shows why the turn count is not redundant with the term list."""
    said = [("de", "Mercedes Benz Places")]
    assert settle("en", said) == "en"


# --- code-switching, from dwight's addendum -------------------------------


def test_an_arabic_caller_using_english_property_words_stays_in_arabic():
    """ar 23% / en 77% on one turn. A Dubai buyer says 'payment plan' in
    English inside an Arabic sentence; that is code-switching, not a change of
    language, and the disclosure they already heard was Arabic."""
    assert (
        settle(
            "ar",
            [
                ("ar", "عندي ميزانية"),
                ("en", "two bedroom payment plan Binghatti Skyhall"),
            ],
        )
        == "ar"
    )


def test_a_hindi_caller_asking_in_english_stays_in_hindi():
    """hi 6% / en 94% on one turn - the most lopsided of the three, and still
    one turn."""
    assert (
        settle(
            "hi",
            [
                ("hi", "मुझे"),
                ("en", "what is the payment plan for this unit"),
            ],
        )
        == "hi"
    )


def test_a_french_farewell_does_not_flip_an_english_call():
    """en 9% / fr 91%. The buyer is leaving; switching the whole call on the
    way out would change which farewell phrases and disclosure apply for a
    turn nobody hears."""
    assert settle("en", [("en", "ok"), ("fr", "merci beaucoup au revoir")]) == "en"


# --- the weighing rules that were already there ---------------------------


def test_ambiguous_or_short_evidence_keeps_the_current_language():
    assert settle("fr", [("en", "yes")]) == "fr"
    assert settle("en", [("fr", "bonjour ici"), ("en", "hello there")]) == "en"
    assert settle("en", [("unknown", "a long stretch of unrecognised speech")]) == "en"
    assert settle("en", []) == "en"


def test_regional_codes_are_normalised_and_chinese_needs_fewer_letters():
    """Kept even though the adapter now hands over an already-normalised base
    code (`LanguageCode.language`): the eval harness and these tests feed raw
    codes, and two lines here is a better place for that coverage than a live
    call."""
    assert (
        settle(
            "en",
            [("pt-BR", "quanto custa este apartamento")],
            [("pt-BR", "quando e a entrega das chaves")],
        )
        == "pt"
    )
    # Four CJK characters is the floor for these two, so the fixtures carry
    # more than that: a three-character question is genuinely too little
    # evidence and must NOT switch, which the last assertion pins.
    chinese = [("cmn", "这个 公寓 多少 钱")]
    assert settle("en", chinese, [("cmn", "何时 交房")]) == "zh"
    assert (
        settle("en", [("zh-CN", "这个 公寓 多少 钱")], [("zh-CN", "何时 交房")]) == "zh"
    )
    assert settle("en", [("cmn", "多少 钱")], [("cmn", "多少 钱")]) == "en"


def test_unsupported_speech_does_not_make_a_small_supported_fragment_win():
    italian = [
        ("it", "vorrei sapere il prezzo di questo appartamento"),
        ("en", "how much"),
    ]
    assert settle("fr", italian, italian) == "fr"


# --- the progress value itself ---------------------------------------------


def test_progress_is_returned_so_the_caller_can_hold_it():
    """The signature returns the next progress rather than mutating hidden
    state, so the whole rule is replayable from a list of turns."""
    from ambassador.language_switch import SwitchProgress, choose_language

    german = [("de", "Guten Tag ich suche eine Wohnung in Dubai")]
    language, progress = choose_language("en", german, excluded_terms=TERMS)
    assert language == "en"
    assert progress == SwitchProgress(candidate="de", turns=1)
    language, progress = choose_language(
        "en", german, progress=progress, excluded_terms=TERMS
    )
    assert language == "de"
    assert progress == SwitchProgress(), "a completed switch starts over"


def test_the_incumbent_winning_a_turn_clears_a_pending_challenger():
    from ambassador.language_switch import SwitchProgress, choose_language

    german = [("de", "Guten Tag ich suche eine Wohnung in Dubai")]
    _, progress = choose_language("en", german, excluded_terms=TERMS)
    assert progress.candidate == "de"
    _, progress = choose_language(
        "en",
        [("en", "sorry I would rather continue in English please")],
        progress=progress,
        excluded_terms=TERMS,
    )
    assert progress == SwitchProgress(), "the incumbent held the turn"


def test_no_excluded_terms_still_works():
    """The parameter defaults to empty, so the core is usable - and testable -
    without the adapter's list."""
    german = [("de", "Guten Tag ich suche eine Wohnung in Dubai")]
    assert settle("en", german, german, terms=()) == "de"
