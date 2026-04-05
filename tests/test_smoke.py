from vacancysoft import __version__
from vacancysoft.classifiers.taxonomy import classify_against_legacy_taxonomy


def test_version_exists() -> None:
    assert __version__ == "0.1.0"


def test_legacy_taxonomy_mapping() -> None:
    result = classify_against_legacy_taxonomy("Senior Compliance Officer")
    assert result.primary_taxonomy_key == "compliance"
