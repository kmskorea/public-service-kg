from pathlib import Path

import pytest

from judge import load_kg

KG_DIR = Path(__file__).resolve().parent.parent.parent / "public_service_kg_final_20261011" / "output"


@pytest.fixture(scope="session")
def kg_final():
    return load_kg(KG_DIR / "kg.ttl")
