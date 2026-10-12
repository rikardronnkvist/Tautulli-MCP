import pytest

from tautulli_mcp.formatting import (
    compact_lines,
    format_duration,
    format_size,
    format_timestamp,
    identifiers,
    media_title,
    playback_decision,
    stream_details,
    text,
)


@pytest.mark.parametrize(
    ("tz", "value", "expected"),
    [
        ("Europe/Stockholm", 1767225600, "2026-01-01 01:00"),
        ("Europe/Stockholm", "1782864000", "2026-07-01 02:00"),
        ("UTC", "2026-01-01T12:30:00+02:00", "2026-01-01 10:30"),
        ("Europe/Stockholm", "2026-01-01", "2026-01-01 00:00"),
        ("Not/AZone", 1767225600, "2026-01-01 00:00"),
        ("../invalid", 1767225600, "2026-01-01 00:00"),
        ("UTC", None, ""),
        ("UTC", "unknown", ""),
        ("UTC", "not a date", ""),
        ("UTC", 10**100, ""),
    ],
)
def test_timestamp_uses_local_timezone(monkeypatch, tz, value, expected):
    monkeypatch.setenv("TZ", tz)
    assert format_timestamp(value) == expected


def test_timestamp_defaults_to_utc(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    assert format_timestamp(1767225600) == "2026-01-01 00:00"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(2700, "45m"), (4320, "1h 12m"), (183600, "2d 3h"), (0, "0s"), (45, "45s")],
)
def test_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_missing_fields_are_omitted():
    assert text(None) == text("") == text(" UNKNOWN ") == ""
    assert text(["Name", None, "unknown"]) == "Name"
    assert identifiers({"row_id": 0, "rating_key": "unknown"}) == "[row_id: 0]"
    output = compact_lines(
        "History",
        [{"row_id": 1, "date": 1767225600, "platform": "unknown", "user": None}],
        ("row_id", "date", "platform", "user"),
    )
    assert "unknown" not in output
    assert "1767225600" not in output
    assert "[row_id: 1]" in output


def test_media_titles_and_audio_transcode():
    assert (
        media_title(
            {
                "media_type": "episode",
                "grandparent_title": "Show",
                "title": "Episode",
                "parent_media_index": "2",
                "media_index": "3",
            }
        )
        == 'Show — S02E03 "Episode"'
    )
    assert media_title({"media_type": "movie", "title": "Film", "year": 2024}) == "Film (2024)"
    assert (
        playback_decision(
            {"stream_video_decision": "direct play", "stream_audio_decision": "transcode"}
        )
        == "transcode"
    )


def test_stream_info_is_allowlisted_and_handles_nested_payload():
    output = stream_details(
        {
            "session_info": {"title": "Film", "quality_profile": "1080p", "session_key": 3},
            "media_info": {
                "container": "mkv",
                "video_codec": "hevc",
                "video_resolution": "4k",
                "bitrate": 20000,
                "audio_codec": "truehd",
                "audio_channels": 8,
                "stream_video_codec": "h264",
                "stream_video_resolution": "1080",
                "stream_bitrate": 4000,
                "stream_audio_codec": "aac",
                "stream_audio_channels": 2,
                "stream_video_decision": "transcode",
                "stream_audio_decision": "transcode",
                "stream_subtitle_decision": "burn",
                "transcode_hw_decoding": 1,
                "transcode_hw_encoding": 0,
                "transcode_reason": "Bandwidth limit",
                "file": "/private/film.mkv",
                "ip_address": "192.0.2.10",
            },
        }
    )
    for part in (
        "[session_key: 3]",
        "Source: mkv, hevc, 4k, 20000 kbps, truehd 8 ch",
        "Delivered: h264, 1080, 4000 kbps, aac 2 ch",
        "Subtitles: burn",
        "HW decode: 1",
        "HW encode: 0",
        "Transcode reason: Bandwidth limit",
    ):
        assert part in output
    assert "/private" not in output
    assert "192.0.2.10" not in output
    assert "unknown" not in output
    assert format_size(1024**3) == "1.0 GiB"
    assert format_size("10.12 GB") == "10.12 GB"
    assert format_size("unknown") == ""
    assert format_size("not a size") == ""


def test_stream_details_accepts_actual_full_resolution_fields():
    output = stream_details(
        {"video_full_resolution": "4k", "stream_video_full_resolution": "1080p"}
    )
    assert "Source: 4k" in output
    assert "Delivered: 1080p" in output
