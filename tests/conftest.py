from pathlib import Path

import pytest

from odata_openapi import parse_metadata

DATA = Path(__file__).parent / "data"


@pytest.fixture
def model():
    return parse_metadata(DATA / "metadata.xml")


@pytest.fixture
def attributes():
    import json
    return json.loads((DATA / "attributes.json").read_text(encoding="utf-8"))
