import json

import pytest

import sync
from categories import Routing, load_routing
from organize import plan_moves


@pytest.mark.parametrize("title,expected", [
    ("Shabbos #22: שנים שעשאוה פטורין (3)", "Iyun"),
    (" shabbos # 20 - Introduction", "Iyun"),
    ("Shabbos #20_ Hebrew title", "Iyun"),
    ("Shabbos #20something", "Other"),
    ("Shabbos and the Parsha", "Other"),
    ("Parsha: Shabbos #22", "Other"),
    ("Shabbos", "Other"),
    ("Shabbos #", "Other"),
    ("Machshava: Emunah", "Other"),
    ("Parshas Noach", "Other"),
    ("Unknown topic", "Other"),
])
def test_conservative_routing(title, expected):
    assert load_routing("80002").category("123", title) == expected


def test_teacher_specific_rules_and_overrides():
    assert load_routing("42").category("1", "Shabbos #20") == "Other"
    rules = Routing(("Shabbos",), (("1", "Other"), ("2", "Iyun")))
    assert rules.category("1", "Shabbos #20") == "Other"
    assert rules.category("2", "Exceptional title") == "Iyun"


@pytest.mark.parametrize("rule", [
    {"iyun_series": "Shabbos"}, {"iyun_series": [""]},
    {"overrides": {"123": "../Iyun"}}, {"overrides": {"abc": "Iyun"}},
    {"iyun_serie": ["Shabbos"]},
])
def test_invalid_config_fails_before_sync(tmp_path, rule):
    config = tmp_path / "categories.json"
    config.write_text(json.dumps({"80002": rule}))
    with pytest.raises(ValueError):
        load_routing("80002", config)


def test_sync_routes_new_uploads_and_deduplicates_across_folders(monkeypatch):
    shiurim = [sync.Shiur(str(i), title, "", None) for i, title in [
        (1, "Old flat"), (2, "Old Iyun"), (3, "Old Other"),
        (4, "Shabbos #22: שנים שעשאוה"), (5, "Parshas Noach"), (4, "Duplicate feed item"),
    ]]
    monkeypatch.setattr(sync, "fetch_feed", lambda *_: shiurim)
    monkeypatch.setattr(sync, "list_remote_filenames", lambda *_: [
        "1 - Old flat.mp3", "Iyun/2 - Old Iyun.mp3", "Other/3 - Old Other.mp3",
    ])
    monkeypatch.setattr(sync, "download_shiur", lambda _session, _shiur, path: path.write_bytes(b"audio"))
    uploads = []
    monkeypatch.setattr(sync, "upload_file", lambda path, dest: uploads.append((path.name, dest)))
    result = sync.sync_teacher("80002", "Rabbi Adler", "gdrive:Archive")
    assert result == sync.SyncResult(6, 4, 2, 0)
    assert uploads == [
        (sync.archive_filename(shiurim[3]), "gdrive:Archive/Iyun"),
        (sync.archive_filename(shiurim[4]), "gdrive:Archive/Other"),
    ]


def test_organization_uses_archived_titles_without_feed_and_preserves_names():
    report = plan_moves([
        "1 - Shabbos #20_ שנים שעשאוה.mp3", "2 - Parsha.mp3",
        "Other/3 - Shabbos #21.mp3", "Iyun/4 - Shabbos #22.mp3",
        "notes.txt", "Unrelated/5 - Shabbos #23.mp3", "../6 - Shabbos #24.mp3",
    ], load_routing("80002"))
    assert report["moves"] == [
        {"shiur_id": "1", "source": "1 - Shabbos #20_ שנים שעשאוה.mp3", "target": "Iyun/1 - Shabbos #20_ שנים שעשאוה.mp3"},
        {"shiur_id": "2", "source": "2 - Parsha.mp3", "target": "Other/2 - Parsha.mp3"},
        {"shiur_id": "3", "source": "Other/3 - Shabbos #21.mp3", "target": "Iyun/3 - Shabbos #21.mp3"},
    ]
    assert len(report["skipped"]) == 4
    assert not report["conflicts"]


def test_organization_reports_duplicate_ids_without_proposing_moves():
    report = plan_moves(["1 - Shabbos #20.mp3", "Other/1 - Different title.mp3"], load_routing("80002"))
    assert len(report["conflicts"]) == 2
    assert not report["moves"]
