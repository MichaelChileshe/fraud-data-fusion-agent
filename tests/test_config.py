"""The settings module: .env parsing, precedence, and read-only settings."""

import dataclasses

import pytest

from fusion import config


def test_dotenv_values_are_loaded_and_real_environment_variables_win(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment line\n"
        "\n"
        "FOO_A=1\n"
        'FOO_B = "two"\n'
        "FOO_C='x=y'\n"
        "NOT_A_SETTING\n"
        "FOO_D=from-file\n",
        encoding="utf-8",
    )
    fake_environment = {"FOO_D": "from-real-env"}
    monkeypatch.setattr(config.os, "environ", fake_environment)

    config._load_dotenv(env_file)

    assert fake_environment["FOO_A"] == "1"
    assert fake_environment["FOO_B"] == "two"          # spaces and double quotes stripped
    assert fake_environment["FOO_C"] == "x=y"          # split on the FIRST "=" only
    assert fake_environment["FOO_D"] == "from-real-env"  # a real variable is never overwritten
    assert "NOT_A_SETTING" not in fake_environment     # lines without "=" are ignored


def test_a_missing_dotenv_file_is_not_an_error(tmp_path):
    config._load_dotenv(tmp_path / "does-not-exist")


def test_env_falls_back_to_the_default():
    assert config._env("FUSION_SURELY_UNSET_VARIABLE", "fallback") == "fallback"


def test_settings_are_read_only_and_typed():
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.settings.ch_port = 1
    assert isinstance(config.settings.ch_port, int)
    assert isinstance(config.settings.embed_dim, int)
    assert isinstance(config.settings.otel_enabled, bool)


def test_one_raw_topic_per_source_plus_a_separate_dead_letter_topic():
    assert set(config.TOPICS) == {"kyc", "transactions", "logins", "sanctions", "case_notes"}
    assert all(topic.startswith("raw.") for topic in config.TOPICS.values())
    assert config.DLQ_TOPIC not in config.TOPICS.values()
