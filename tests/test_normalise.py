import pytest

from fusion.normalise import normalise_name, normalise_phone


@pytest.mark.parametrize("raw", ["082 123 4567", "0821234567", "+27821234567", "27821234567",
                                 "+27 82 123 4567", "(082) 123-4567"])
def test_every_way_of_writing_a_phone_becomes_one_value(raw):
    assert normalise_phone(raw) == "+27821234567"


@pytest.mark.parametrize("raw", ["12345", "", "011 123 4567", "+44 7700 900123", "082 123 456"])
def test_invalid_or_non_mobile_numbers_are_rejected(raw):
    assert normalise_phone(raw) is None


def test_names_normalise_case_accents_titles_and_punctuation():
    assert normalise_name("  Mr SIPHO  M. Dlamini ") == "sipho m dlamini"
    assert normalise_name("Zoë Nkosi-Mthembu") == "zoe nkosi mthembu"
    assert normalise_name("Mama Zenzile") == "zenzile"
