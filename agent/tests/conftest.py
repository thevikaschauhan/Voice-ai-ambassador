import pytest

from ambassador.guardrails.prohibited import load_patterns
from ambassador.guardrails.vocative import load_vocative_context
from ambassador.inventory import build_allowed_figures, load_inventory
from ambassador.verbalise import load_spoken_forms


@pytest.fixture(scope="session")
def projects():
    return load_inventory()


@pytest.fixture(scope="session")
def allowed(projects):
    return build_allowed_figures(projects)


@pytest.fixture(scope="session")
def patterns():
    return load_patterns()


@pytest.fixture(scope="session")
def forms():
    return load_spoken_forms()


@pytest.fixture(scope="session")
def vocatives():
    """The production default for validator 5: our own vocabulary, no buyer
    name. Every call is in this state until the buyer answers the contact
    ask."""
    return load_vocative_context()
