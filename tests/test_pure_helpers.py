"""Tests for server.py's pure, hardware-independent helper functions."""

from dataclasses import dataclass

import pytest

from server import _resolve_mode_code, _seconds_to_days, match_names_by_query


class TestMatchNamesByQuery:
    ROOMS = [(1, "Server Room"), (2, "Room 2"), (3, "Mech Room"), (4, "Bathroom"), (5, "Kitchen")]

    def test_exact_match_wins_over_substring_matches(self):
        # "Room 2" is also a substring-match candidate for "Server Room" and
        # "Mech Room" (both contain "room"); the exact match should be the
        # only result.
        assert match_names_by_query("Room 2", self.ROOMS) == [(2, "Room 2")]

    def test_case_insensitive_exact_match(self):
        assert match_names_by_query("kitchen", self.ROOMS) == [(5, "Kitchen")]

    def test_partial_substring_match(self):
        assert match_names_by_query("kit", self.ROOMS) == [(5, "Kitchen")]

    def test_ambiguous_substring_matches_multiple(self):
        # "room" is a substring of four different room names (including
        # "Bathroom") and isn't an exact match for any of them.
        results = match_names_by_query("room", self.ROOMS)
        assert {name for _, name in results} == {"Server Room", "Room 2", "Mech Room", "Bathroom"}

    def test_no_match(self):
        assert match_names_by_query("garage", self.ROOMS) == []

    def test_empty_query_matches_nothing(self):
        # A blank query is a substring of every name; without an explicit
        # guard this would match (and could trigger cleaning) every room.
        assert match_names_by_query("", self.ROOMS) == []
        assert match_names_by_query("   ", self.ROOMS) == []


@dataclass
class FakeModeOption:
    name: str
    value: str
    code: int


class TestResolveModeCode:
    OPTIONS = [
        FakeModeOption("QUIET", "quiet", 101),
        FakeModeOption("MAX", "max", 104),
        FakeModeOption("MAX_PLUS", "max_plus", 108),
    ]

    def test_resolve_by_name_case_insensitive(self):
        assert _resolve_mode_code("Max", self.OPTIONS) == 104

    def test_resolve_by_name_with_spaces_and_dashes(self):
        assert _resolve_mode_code("max-plus", self.OPTIONS) == 108
        assert _resolve_mode_code("max plus", self.OPTIONS) == 108

    def test_resolve_by_raw_valid_code(self):
        assert _resolve_mode_code(104, self.OPTIONS) == 104

    def test_resolve_by_raw_invalid_code_raises(self):
        with pytest.raises(ValueError):
            _resolve_mode_code(999, self.OPTIONS)

    def test_resolve_unknown_name_raises(self):
        with pytest.raises(ValueError):
            _resolve_mode_code("turbo", self.OPTIONS)


class TestSecondsToDays:
    def test_none_is_unknown(self):
        assert _seconds_to_days(None) == "unknown"

    def test_positive_seconds(self):
        assert _seconds_to_days(86400) == "1.0 days left"

    def test_zero_seconds(self):
        assert _seconds_to_days(0) == "0.0 days left"

    def test_negative_seconds_is_overdue(self):
        assert _seconds_to_days(-86400) == "overdue by 1.0 days"
