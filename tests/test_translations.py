import json

from sakuramedia_javbus_metadata import translations


def test_text_helpers_fall_back_when_keys_are_missing(monkeypatch):
    monkeypatch.setattr(
        translations,
        "_CATALOGS",
        {"zh-CN": {"only.zh": "中文"}, "en": {}},
    )

    assert translations.text("only.zh") == "中文"
    assert translations.text("only.zh", locale="en") == "中文"
    assert translations.text("missing", default="fallback") == "fallback"
    assert translations.text("missing") == "missing"
    assert translations.text_map("only.zh") == {"zh-CN": "中文"}
    assert translations.text_map("missing") == {}


def test_load_catalog_returns_empty_for_missing_and_invalid_files(tmp_path):
    assert translations._load_catalog(tmp_path / "missing.json") == {}

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    assert translations._load_catalog(invalid) == {}

    not_object = tmp_path / "list.json"
    not_object.write_text(json.dumps(["x"]), encoding="utf-8")
    assert translations._load_catalog(not_object) == {}


def test_load_catalog_keeps_only_string_entries(tmp_path):
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps({"ok": "text", "bad": 1}),
        encoding="utf-8",
    )

    assert translations._load_catalog(path) == {"ok": "text"}
