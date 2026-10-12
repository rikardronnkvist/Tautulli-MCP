import pytest

from tautulli_mcp.formatting import (
    compact_lines,
    format_date,
    format_duration,
    format_size,
    format_timestamp,
    format_title,
    identifiers,
    media_title,
    playback_decision,
    stream_details,
    text,
    transcode_reason,
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


@pytest.mark.parametrize("zone", ["America/Los_Angeles", "Pacific/Kiritimati", "Not/AZone"])
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2025-03-04", "2025-03-04"),
        ("2025-03-04T00:00:00Z", "2025-03-04"),
        ("2025-03-04T00:00:00+14:00", "2025-03-04"),
        ("2025-03-04T00:00:00-08:00", "2025-03-04"),
        ("2025-03-04 00:00:00", "2025-03-04"),
        (1767225600, "2026-01-01"),
        ("1767225600", "2026-01-01"),
        (0, "1970-01-01"),
        (-86400, "1969-12-31"),
        (None, ""),
        ("unknown", ""),
        ("2025-02-30", ""),
        ("bad date", ""),
        (10**100, ""),
        (float("inf"), ""),
    ],
)
def test_calendar_dates_do_not_shift_with_timezone(monkeypatch, zone, value, expected):
    monkeypatch.setenv("TZ", zone)
    assert format_date(value) == expected


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
        "HW decode: yes",
        "HW encode: no",
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


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "SouthLAnd - Mozambique", "grandparent_title": "SouthLAnd"},
        {"full_title": "SouthLAnd - Mozambique", "grandparent_title": "SouthLAnd"},
        {"title": "SouthLAnd - Mozambique"},
        {"title": "Mozambique", "full_title": "SouthLAnd - Mozambique"},
        {"title": "Mozambique", "last_played": "SouthLAnd - Mozambique"},
        {"last_played": "SouthLAnd - Mozambique"},
        {"title": "SouthLAnd — Mozambique"},
    ],
)
def test_episode_titles_strip_prefix_and_infer_missing_show(fields):
    record = {
        "media_type": "episode",
        "parent_media_index": 1,
        "media_index": 2,
        **fields,
    }
    assert format_title(record) == 'SouthLAnd — S01E02 "Mozambique"'
    assert media_title(record) == format_title(record)
    assert stream_details(record) == format_title(record)


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"media_type": "movie", "title": "Film", "year": 2025}, "Film (2025)"),
        ({"type": "movie", "full_title": "Film", "year": "2025"}, "Film (2025)"),
        ({"type": "movie", "title": "Film (2025)", "year": "2025"}, "Film (2025)"),
        ({"type": "movie", "year": 2025}, ""),
        ({"media_type": "episode", "title": "Episode"}, '"Episode"'),
        ({"media_type": "episode", "grandparent_title": "Show"}, "Show"),
        ({"title": None, "full_title": "unknown"}, ""),
    ],
)
def test_format_title_missing_fields(record, expected):
    assert format_title(record) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "no"),
        (1, "yes"),
        (0.0, "no"),
        (1.0, "yes"),
        ("0", "no"),
        ("1", "yes"),
        (True, "yes"),
        (False, "no"),
        ("true", "yes"),
        ("FALSE", "no"),
        (" Yes ", "yes"),
        ("NO", "no"),
        ("NVIDIA NVDEC", "NVIDIA NVDEC"),
        ("Intel Quick Sync", "Intel Quick Sync"),
        (None, ""),
        ("unknown", ""),
    ],
)
@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("transcode_hw_decoding", "HW decode"),
        ("transcode_hw_encoding", "HW encode"),
        ("transcode_hw_decode", "HW decode"),
        ("transcode_hw_encode", "HW encode"),
    ],
)
def test_hardware_boolean_normalization_preserves_device_names(key, label, value, expected):
    output = stream_details({key: value})
    assert output == (f"{label}: {expected}" if expected else "")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("direct_play", "direct play"),
        ("Direct Play", "direct play"),
        ("directplay", "direct play"),
        ("direct-stream", "direct stream"),
        ("DIRECT_STREAM", "direct stream"),
        ("copy", "direct stream"),
        ("TRANSCODE", "transcode"),
    ],
)
def test_stream_decisions_are_normalized(value, expected):
    output = stream_details({"stream_video_decision": value, "audio_decision": value})
    assert f"Video: {expected}" in output
    assert f"Audio: {expected}" in output
    assert playback_decision({"stream_video_decision": value}) == expected


def test_audio_only_transcode_has_derived_reason_and_codec_hint():
    record = {
        "stream_video_decision": "direct_play",
        "video_codec": "hevc",
        "stream_video_codec": "hevc",
        "stream_audio_decision": "transcode",
        "audio_codec": "dca",
        "stream_audio_codec": "aac",
        "transcode_hw_requested": True,
    }
    output = stream_details(record)
    assert "Video: direct play" in output
    assert "Audio: transcode (DTS → AAC)" in output
    assert "Transcode reason: audio: DTS → AAC" in output
    assert "Source: hevc, dca" in output
    assert "Delivered: hevc, aac" in output
    assert "HW" not in output


def test_video_changes_and_burned_subtitles_provide_derived_reason():
    record = {
        "video_codec": "hevc",
        "stream_video_codec": "h264",
        "video_full_resolution": "2160p",
        "stream_video_full_resolution": "1080p",
        "video_decision": "transcode",
        "subtitle_decision": "burn",
        "subtitle_codec": "pgs",
        "file_reason": "/private/movie.mkv",
        "ip_reason": "192.0.2.10",
    }
    output = stream_details(record)
    assert "Video: transcode (hevc → h264, 2160p → 1080p)" in output
    assert "Subtitles: burn (pgs)" in output
    assert transcode_reason(record) == (
        "video: hevc → h264; video: 2160p → 1080p; subtitles: burn (pgs)"
    )
    assert "/private" not in output
    assert "192.0.2.10" not in output
    assert "file_reason" not in output


def test_subtitle_transcode_shows_source_and_delivered_codec():
    output = stream_details(
        {
            "subtitle_codec": "srt",
            "stream_subtitle_codec": "ass",
            "stream_subtitle_decision": "transcode",
        }
    )
    assert "Subtitles: transcode (srt → ass)" in output
    assert "Transcode reason: subtitles: transcode (srt → ass)" in output


@pytest.mark.parametrize(
    "key", ["subtitle_codec", "source_subtitle_codec", "stream_subtitle_codec"]
)
def test_subtitle_codec_is_shown_without_decision(key):
    assert stream_details({key: "pgs"}) == "Subtitles: pgs"


def test_changed_codecs_derive_missing_decisions_and_preserve_video_names():
    output = stream_details(
        {
            "video_codec": "hevc",
            "stream_video_codec": "h264",
            "audio_codec": "dca",
            "stream_audio_codec": "aac",
            "subtitle_codec": "srt",
            "stream_subtitle_codec": "ass",
        }
    )
    assert "Video: transcode (hevc → h264)" in output
    assert "Audio: transcode (DTS → AAC)" in output
    assert "Subtitles: transcode (srt → ass)" in output
    assert "Transcode reason: video: hevc → h264; audio: DTS → AAC; subtitles: srt → ass" in output


def test_missing_decisions_without_codec_changes_are_not_inferred():
    output = stream_details(
        {
            "video_codec": "hevc",
            "stream_video_codec": "hevc",
            "audio_codec": "aac",
            "stream_audio_codec": "aac",
        }
    )
    assert "Video:" not in output
    assert "Audio:" not in output
    assert "Transcode reason:" not in output


def test_multiple_explicit_reasons_are_deduplicated():
    assert (
        transcode_reason(
            {
                "transcode_reason": "Client limitation",
                "stream_video_reason": "Client limitation",
                "audio_reason": "Audio codec unsupported",
            }
        )
        == "Client limitation; Audio codec unsupported"
    )


def test_allowlisted_stream_fields_do_not_dump_nested_sensitive_values():
    sensitive = {"file": "/private/movie.mkv", "ip_address": "192.0.2.10"}
    output = stream_details(
        {
            "transcode_hw_decode": sensitive,
            "transcode_hw_encode": [sensitive],
            "video_codec": sensitive,
            "quality_profile": sensitive,
            "stream_audio_decision": sensitive,
        }
    )
    assert output == ""


@pytest.mark.parametrize(
    "key",
    [
        "transcode_reason",
        "transcode_decision_string",
        "video_reason",
        "audio_reason",
        "subtitle_reason",
        "stream_video_reason",
        "stream_audio_reason",
        "stream_subtitle_reason",
        "video_transcode_reason",
        "audio_transcode_reason",
        "subtitle_transcode_reason",
        "video_decision_reason",
        "audio_decision_reason",
        "subtitle_decision_reason",
        "stream_video_decision_reason",
        "stream_audio_decision_reason",
        "stream_subtitle_decision_reason",
    ],
)
def test_explicit_allowlisted_reasons_take_precedence(key):
    record = {
        key: "Client codec unsupported",
        "audio_codec": "dca",
        "stream_audio_codec": "aac",
        "file_reason": "/private/movie.mkv",
        "session_info": {"ip_address": "192.0.2.10"},
    }
    assert transcode_reason(record) == "Client codec unsupported"
    assert "Transcode reason: Client codec unsupported" in stream_details(record)


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"transcode_hw_requested": 1},
        {"video_decision": "transcode"},
        {"audio_codec": "dca"},
        {"stream_audio_codec": "aac"},
        {"audio_codec": "AAC", "stream_audio_codec": "aac"},
        {"transcode_reason": "unknown"},
        {"transcode_reason": {"file": "/private/movie.mkv", "ip_address": "192.0.2.10"}},
        {"transcode_reason": ["/private/movie.mkv", "192.0.2.10"]},
        {"arbitrary_reason": "/private/movie.mkv"},
    ],
)
def test_missing_evidence_does_not_fabricate_or_dump_reason(record):
    assert transcode_reason(record) == ""
    output = stream_details(record)
    assert "Transcode reason:" not in output
    assert "/private" not in output
    assert "192.0.2.10" not in output
