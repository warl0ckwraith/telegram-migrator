"""Tests for the helper functions in src/utils.py."""

import pytest

from utils import (
    estimate_eta,
    format_duration,
    format_size,
    get_media_type,
    parse_target,
    parse_tme_message_link,
    sanitize_filename,
    validate_media_types,
)


def test_parse_private_message_link():
    result = parse_tme_message_link("https://t.me/c/123456789/42")
    assert result == {
        "chat_ref": "-100123456789",
        "message_id": 42,
        "original_link": "https://t.me/c/123456789/42",
    }


def test_parse_public_message_link():
    result = parse_tme_message_link("https://t.me/durov/123")
    assert result["chat_ref"] == "durov"
    assert result["message_id"] == 123


def test_parse_message_link_without_scheme():
    result = parse_tme_message_link("t.me/c/1/2")
    assert result["chat_ref"] == "-1001"
    assert result["message_id"] == 2


@pytest.mark.parametrize("value", ["", "https://t.me/durov", "not a link", None])
def test_parse_message_link_rejects_non_links(value):
    assert parse_tme_message_link(value) is None


def test_sanitize_filename_replaces_unsafe_characters():
    assert sanitize_filename('a/b:c*.txt') == "a_b_c_.txt"


def test_sanitize_filename_empty_becomes_placeholder():
    assert sanitize_filename("   ") == "unnamed_file"


def test_sanitize_filename_truncates_and_keeps_extension():
    name = sanitize_filename("x" * 300 + ".txt", max_length=200)
    assert len(name) <= 200
    assert name.endswith(".txt")


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0.0 B"),
        (1536, "1.5 KB"),
        (1024, "1.0 KB"),
        (1024 ** 3, "1.0 GB"),
    ],
)
def test_format_size(value, expected):
    assert format_size(value) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"),
        (59, "59s"),
        (65, "1m 5s"),
        (3661, "1h 1m 1s"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("@foo", ("username", "foo")),
        ("foo", ("username", "foo")),
        ("https://t.me/foo", ("url", "https://t.me/foo")),
        ("+15551234567", ("phone", "+15551234567")),
        ("123", ("id", 123)),
        ("-100123", ("id", -100123)),
    ],
)
def test_parse_target(value, expected):
    assert parse_target(value) == expected


def test_validate_media_types():
    assert validate_media_types(["photo", "video"]) is True
    assert validate_media_types([]) is True
    assert validate_media_types(["photos"]) is False


def test_estimate_eta():
    assert estimate_eta(0, 100, 10) == 0.0
    assert estimate_eta(50, 100, 10) == 10.0


def test_get_media_type_none():
    assert get_media_type(None) is None
