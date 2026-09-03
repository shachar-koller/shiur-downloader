from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import sync


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.yutorah.org/lectures/1116616/Some-Title", "1116616"),
        ("https://www.yutorah.org/lectures/1117459/", "1117459"),
        ("https://www.yutorah.org/lectures/details?shiurid=1117409", "1117409"),
        ("https://www.yutorah.org/lectures/lecture.cfm/831642/title", "831642"),
        ("https://www.yutorah.org/lectures/lecture.cfm?shiurID=765432", "765432"),
        ("http://media.yutorah.org/yuriets/2026/201682/1187261.MP3", "1187261"),
        ("https://download.yutorah.org/2026/201682/1187260/title.mp3", "1187260"),
    ],
)
def test_extract_shiur_id_common_formats(url, expected):
    assert sync.extract_shiur_id(url) == expected


def test_extract_shiur_id_returns_none_for_unrelated_url():
    assert sync.extract_shiur_id("https://www.yutorah.org/teachers/example") is None


def test_parse_feed_extracts_page_id_title_and_enclosure():
    rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Test</title><item>
      <title>הלכות שבת: שיעור / ראשון?</title>
      <link>https://www.yutorah.org/lectures/1234567/Hebrew-title</link>
      <guid>http://media.yutorah.org/2026/1/1234567.MP3</guid>
      <enclosure url="https://download.yutorah.org/2026/1/1234567/test.mp3"
                 type="audio/mpeg" length="123" />
    </item></channel></rss>"""

    assert sync.parse_feed(rss) == [
        sync.Shiur(
            shiur_id="1234567",
            title="הלכות שבת: שיעור / ראשון?",
            page_url="https://www.yutorah.org/lectures/1234567/Hebrew-title",
            mp3_url="https://download.yutorah.org/2026/1/1234567/test.mp3",
        )
    ]


def test_parse_feed_uses_guid_id_and_constructs_missing_page_url():
    rss = """<rss version="2.0"><channel><item>
      <title>Test shiur</title>
      <guid>http://media.yutorah.org/yuriets/2020/1/998877.MP3</guid>
    </item></channel></rss>"""

    [shiur] = sync.parse_feed(rss)
    assert shiur.shiur_id == "998877"
    assert shiur.page_url.endswith("?shiurid=998877")
    assert shiur.mp3_url is None


def test_sanitize_title_preserves_unicode_and_replaces_unsafe_characters():
    title = "  הלכות שבת: שיעור / ראשון?  \n חלק * א  "
    assert sync.sanitize_title(title) == "הלכות שבת_ שיעור _ ראשון_ חלק _ א"


def test_archive_filename_stays_within_safe_utf8_length():
    shiur = sync.Shiur("1234567", "שלום" * 100, "https://example.test", None)
    filename = sync.archive_filename(shiur)
    assert filename.startswith("1234567 - שלום")
    assert filename.endswith(".mp3")
    assert len(filename.encode("utf-8")) <= 240


def test_is_archived_requires_id_dash_prefix():
    files = ["123 - Existing.mp3", "9123 - Different.mp3", "notes.txt"]
    assert sync.is_archived("123", files)
    assert not sync.is_archived("12", files)


def test_list_remote_filenames_uses_rclone_lsf(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="123 - One.mp3\nשלום.mp3\n")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    assert sync.list_remote_filenames("gdrive:Archive") == ["123 - One.mp3", "שלום.mp3"]
    assert calls[0][0] == ["rclone", "lsf", "gdrive:Archive", "--files-only"]
    assert calls[0][1]["check"] is True


class FakeResponse:
    def __init__(self, chunks=None, error=None):
        self.chunks = chunks or []
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.error:
            raise self.error

    def iter_content(self, chunk_size):
        assert chunk_size == sync.CHUNK_SIZE
        yield from self.chunks


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        return next(self.responses)


def test_stream_download_writes_chunks_and_uses_timeout(tmp_path):
    session = FakeSession([FakeResponse([b"abc", b"", b"def"])])
    destination = tmp_path / "shiur.mp3"

    sync.stream_download(session, "https://example.test/audio.mp3", destination)

    assert destination.read_bytes() == b"abcdef"
    assert session.urls[0][1]["stream"] is True
    assert session.urls[0][1]["timeout"] == sync.REQUEST_TIMEOUT
    assert not (tmp_path / "shiur.mp3.part").exists()


def test_download_shiur_falls_back_when_enclosure_fails(monkeypatch, tmp_path):
    session = FakeSession(
        [
            FakeResponse(error=requests.HTTPError("bad enclosure")),
            FakeResponse([b"fallback audio"]),
        ]
    )
    monkeypatch.setattr(sync, "fallback_download_url", lambda _url: "https://fallback.test/audio.mp3")
    shiur = sync.Shiur("123", "Title", "https://www.yutorah.org/lectures/123/Title", "https://bad.test/audio.mp3")
    destination = tmp_path / "123 - Title.mp3"

    sync.download_shiur(session, shiur, destination)

    assert destination.read_bytes() == b"fallback audio"
    assert [url for url, _kwargs in session.urls] == [
        "https://bad.test/audio.mp3",
        "https://fallback.test/audio.mp3",
    ]


def test_sync_continues_after_one_item_fails(monkeypatch):
    shiurim = [
        sync.Shiur("1", "Old", "https://example.test/1", None),
        sync.Shiur("2", "Fails", "https://example.test/2", None),
        sync.Shiur("3", "Works", "https://example.test/3", None),
    ]
    attempted = []
    uploaded = []

    monkeypatch.setattr(sync, "fetch_feed", lambda _session, _teacher_id: shiurim)
    monkeypatch.setattr(sync, "list_remote_filenames", lambda _destination: ["1 - Old.mp3"])

    def fake_download(_session, shiur, destination):
        attempted.append(shiur.shiur_id)
        if shiur.shiur_id == "2":
            raise requests.RequestException("network failure")
        destination.write_bytes(b"audio")

    monkeypatch.setattr(sync, "download_shiur", fake_download)
    monkeypatch.setattr(sync, "upload_file", lambda path, _destination: uploaded.append(path.name))

    result = sync.sync_teacher("42", "Rabbi Example", "gdrive:Archive")

    assert result == sync.SyncResult(found=3, skipped=1, uploaded=1, failed=1)
    assert attempted == ["2", "3"]
    assert uploaded == ["3 - Works.mp3"]


def test_required_environment_rejects_missing_value(monkeypatch):
    monkeypatch.delenv("YU_TORAH_TEACHER_ID", raising=False)
    with pytest.raises(ValueError, match="YU_TORAH_TEACHER_ID"):
        sync.required_environment("YU_TORAH_TEACHER_ID")
