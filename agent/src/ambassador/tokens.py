r"""What the words in a turn are, for every script the product speaks.

`farewell.py` and `recognition.py` each asked the same question of a
transcript - what are the words in this? - and each answered it with
`[^\W_]+`. That pattern matches letters and digits and nothing else, so every
combining mark is a token boundary. In Devanagari the vowel signs ARE
combining marks, so "मुझे जाना है" came out as ('म','झ','ज','न','ह'): five
consonant fragments, no words. Arabic harakat did the same thing.

The defect hid because both sides shattered alike. A phrase table loaded
through the broken split and an utterance read through the broken split
produced matching skeletons, so a round-trip test passed - while the safeguard
underneath it, "every remaining token must be a courtesy", was comparing
fragments. Fragments are short and there are few of them, which is why a
courtesy list is far likelier to contain all of a word's fragments than the
word itself. That is how a Hindi QUESTION came to satisfy the closing rule and
hang up on a live buyer (Pam, PR #181).

Two kinds of mark, and they are not the same problem:

  KEPT      Devanagari matras and the nukta, and combining marks generally.
            These are obligatory spelling. "मुझे" is never written without
            them, and dropping them would put the consonant skeleton back -
            the very collision this module exists to remove.
  DROPPED   Arabic harakat, and the two zero-width joiners. These are
            optional: ordinary Arabic omits the short vowels, a recogniser may
            emit them or not, and a joiner only controls how a conjunct
            renders. Whether they appear is a transcription choice, so leaving
            them in would let the recogniser decide whether an authored phrase
            matches.

VERIFY: that harakat-insensitive matching is what Arabic detection should do.
It is the standard normalisation for Arabic search, and the authored tables are
unvocalised so nothing they could distinguish is lost - but two words that
differ only in harakat are different words, and a native reviewer should
confirm that trade rather than the build team assuming it.

Not a regex, because Python's `re` has no `\p{M}` and enumerating every
combining range by hand is a list that goes stale the next time Unicode adds a
script. `unicodedata.category` asks the question directly.

PRIOR ART, and the reason this module exists rather than a third fix:
`adapter/retrieval.py` already hit this. Its first version used `[^\W_]+`
under a comment warning about exactly this trap, shredded Devanagari, and was
rewritten to the Unicode categories `LMN`. That rewrite never reached the two
core modules, because there was nothing to reach - no shared module, so the
lesson stayed a comment in the adapter while `farewell.py` and
`recognition.py` kept the broken pattern. The character set here is the SAME
set, reached from the other side (letters and numbers via `str.isalnum`, marks
via category), and a test pins that equality.

`retrieval.py` is deliberately NOT converted onto this module. Its tokeniser
has to agree character for character with a Postgres index expression, so it
cannot take the harakat normalisation below without the index changing in the
same commit - that pairing is `task-zh-ja-retrieval-segmentation`. Core cannot
import from the adapter anyway (ADR-002); the dependency, when it happens, runs
this way.

Pure: no I/O, no framework import, no environment (ADR-002).

The honest limit: this finds word boundaries, it does not segment. Chinese,
Japanese and Thai write without spaces, so a run in those scripts is ONE
token. That is not word segmentation and this module does not pretend to be a
segmenter - it is only that one run is a great deal better than a scatter of
fragments. `retrieval.py` has the same gap and it is carded separately.
"""

from __future__ import annotations

import unicodedata

# Mn non-spacing, Mc spacing-combining, Me enclosing: everything that belongs
# to the letter in front of it.
_MARK_CATEGORIES = frozenset({"Mn", "Mc", "Me"})

# Arabic short vowels, tanween, shadda and sukun (U+064B-U+065F), the
# superscript alef, and the Quranic annotation marks. All optional in ordinary
# text. All 41 are category Mn, which is why they have to be named rather than
# derived: the category cannot separate them from a Devanagari matra, and the
# two want opposite treatment. Checked before _MARK_CATEGORIES for that
# reason.
_ARABIC_DIACRITICS = frozenset(
    chr(point)
    for point in [
        *range(0x064B, 0x0660),
        0x0670,
        *range(0x06D6, 0x06DD),
        *range(0x06DF, 0x06E5),
        0x06E7,
        0x06E8,
        *range(0x06EA, 0x06EE),
    ]
)

# ZWNJ and ZWJ, written as escapes on purpose: they are invisible, and a
# reviewer cannot check a character they cannot see. They sit inside a single
# word and control only how a conjunct renders.
_JOINERS = frozenset({"\u200c", "\u200d"})


def has_content(text: str) -> bool:
    """Did the recogniser hear anything at all?

    A letter or a digit in any script. Deliberately not marks: a stray matra
    emitted around silence is not speech, and `is_failed_recognition` treats a
    turn with no content as an empty turn.
    """
    return any(char.isalnum() for char in text)


def tokens(text: str) -> list[str]:
    """The lowercased words in `text`.

    Lowercased here rather than at each call site, because every caller needs
    it and a caller that forgets gets a table that silently never matches.

    Digits are words: `figures.normalise_digits` runs before this on the paths
    that care, so "985,000" arrives as ASCII and splits on the comma.
    """
    words: list[str] = []
    current: list[str] = []
    for char in text.lower():
        if char in _ARABIC_DIACRITICS or char in _JOINERS:
            # Dropped without ending the word: the neighbours join up.
            continue
        if char.isalnum():
            current.append(char)
        elif current and unicodedata.category(char) in _MARK_CATEGORIES:
            # A mark extends the word it sits on. Only when a word is open: a
            # leading mark has no letter to belong to and is malformed input,
            # not a word made of punctuation.
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words
