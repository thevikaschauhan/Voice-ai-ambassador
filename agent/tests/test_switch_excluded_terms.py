"""The words that must never count as evidence of a language change.

P1a on task-review-3ab4928-language-switch: on an Arabic call the single word
`Binghatti` - nine letters, the whole utterance, one hundred percent share -
flipped the reply language to English, against ADR-010 (docs/01:132, "an Arabic
speaker opening in English is not a language signal").
"""

import pytest

pytest.importorskip("livekit.agents")
from adapter.stt_factory import BRAND_KEYTERMS, switch_excluded_terms  # noqa: E402
from ambassador.inventory import load_inventory  # noqa: E402


def test_the_brand_and_every_inventory_name_and_area_are_excluded():
    terms = switch_excluded_terms(load_inventory())
    lowered = {term.casefold() for term in terms}
    assert "binghatti" in lowered
    for keyterm in BRAND_KEYTERMS:
        assert keyterm.casefold() in lowered
    for project in load_inventory():
        assert project.name.casefold() in lowered
        assert project.area.casefold() in lowered


def test_terms_come_back_longest_first_so_a_phrase_beats_its_own_prefix():
    """The load-bearing property, not a cosmetic one.

    The terms are stripped by phrase before letters are counted, so a longer
    term has to be tried first: matching "Binghatti" inside "Binghatti
    Skyrise" would leave "Skyrise" behind to be counted as English evidence,
    which is the whole defect in miniature.
    """
    terms = switch_excluded_terms(load_inventory())
    assert [len(term) for term in terms] == sorted(
        (len(term) for term in terms), reverse=True
    )
    for term in terms:
        prefixes = [
            other for other in terms if other != term and term.startswith(other)
        ]
        for prefix in prefixes:
            assert terms.index(term) < terms.index(prefix)


def test_a_name_the_keyterms_and_the_inventory_share_is_listed_once():
    projects = load_inventory()
    terms = switch_excluded_terms(projects)
    assert len(terms) == len({term.casefold() for term in terms})


def test_a_project_with_no_area_does_not_contribute_an_empty_term():
    projects = load_inventory()
    blanked = [projects[0].model_copy(update={"area": "  "})]
    assert all(term.strip() for term in switch_excluded_terms(blanked))
