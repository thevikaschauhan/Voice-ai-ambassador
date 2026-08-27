import pytest

from ambassador.guardrails.prohibited import load_patterns
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
