"""Tests for the temporal facts system.

Covers:
- Basic store and retrieve
- Per-fact timestamps
- Category supersession (job, location, relationship, education)
- No supersession for unrelated categories
- Exact-duplicate skipping
- load_facts() only returns active facts
- Backwards-compat reading of old plain-string format
- _MAX_FACTS cap applies to active facts only
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone, timedelta


# ── Helpers ───────────────────────────────────────────────────────────────────

def _store(user_id: str, facts: list[str], confidences: list[float] | None = None) -> None:
    from anjo.core.facts import merge_facts
    merge_facts(user_id, facts, confidences)


def _active(user_id: str) -> list[str]:
    from anjo.core.facts import load_facts
    return load_facts(user_id)


# ── Basic storage ─────────────────────────────────────────────────────────────

def test_stored_fact_is_retrievable():
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u1", ["works as a nurse"])
    assert "works as a nurse" in load_facts("u1")


def test_empty_list_is_noop():
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u_empty", [])
    assert load_facts("u_empty") == []


def test_facts_have_timestamps():
    from anjo.core.facts import merge_facts, load_facts_with_meta
    before = datetime.now(timezone.utc)
    merge_facts("u_ts", ["loves hiking"])
    meta = load_facts_with_meta("u_ts")
    assert meta
    added = datetime.fromisoformat(meta[0]["added_at"])
    assert added >= before


def test_newest_facts_appear_first():
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u_order", ["first fact"])
    merge_facts("u_order", ["second fact"])
    facts = load_facts("u_order")
    assert facts[0] == "second fact"
    assert facts[1] == "first fact"


# ── Exact-duplicate skipping ──────────────────────────────────────────────────

def test_exact_duplicate_is_skipped():
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u_dup", ["has a dog named Max"])
    merge_facts("u_dup", ["has a dog named Max"])
    assert load_facts("u_dup").count("has a dog named Max") == 1


def test_case_insensitive_duplicate_skipped():
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u_case", ["Works as a nurse"])
    merge_facts("u_case", ["works as a nurse"])
    assert len(load_facts("u_case")) == 1


# ── Category supersession ─────────────────────────────────────────────────────

def test_new_job_supersedes_old_job():
    from anjo.core.facts import merge_facts, load_facts, load_facts_with_meta
    merge_facts("u_job", ["works as a nurse"])
    merge_facts("u_job", ["works as a software engineer"])

    active = load_facts("u_job")
    assert "works as a software engineer" in active
    assert "works as a nurse" not in active, "Old job should be superseded"


def test_superseded_fact_has_timestamp():
    from anjo.core.facts import merge_facts, _load_all
    merge_facts("u_sup_ts", ["works as a nurse"])
    merge_facts("u_sup_ts", ["works as a software engineer"])

    all_records = _load_all("u_sup_ts")
    nurse = next((r for r in all_records if "nurse" in r["text"]), None)
    assert nurse is not None
    assert nurse["superseded_at"] is not None


def test_new_city_supersedes_old_city():
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u_city", ["lives in Tokyo"])
    merge_facts("u_city", ["moved to London"])

    active = load_facts("u_city")
    assert any("London" in f for f in active)
    assert not any("Tokyo" in f for f in active), "Old city should be superseded"


def test_relationship_status_supersedes():
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u_rel", ["single"])
    merge_facts("u_rel", ["started dating someone"])

    active = load_facts("u_rel")
    assert any("dating" in f for f in active)
    assert "single" not in active


def test_education_supersedes():
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u_edu", ["studying architecture"])
    merge_facts("u_edu", ["graduated last year"])

    active = load_facts("u_edu")
    assert any("graduated" in f for f in active)
    assert not any("studying" in f for f in active)


def test_unrelated_facts_do_not_supersede():
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u_unrelt", ["has a dog named Biscuit"])
    merge_facts("u_unrelt", ["loves Italian food"])

    active = load_facts("u_unrelt")
    assert len(active) == 2, "Unrelated facts should both remain active"


def test_different_categories_do_not_supersede():
    """A job fact should not supersede a location fact."""
    from anjo.core.facts import merge_facts, load_facts
    merge_facts("u_cross", ["lives in Seoul"])
    merge_facts("u_cross", ["works as a developer"])

    active = load_facts("u_cross")
    assert len(active) == 2, "Job and city are different categories — both stay"


# ── Cap behaviour ─────────────────────────────────────────────────────────────

def test_active_facts_capped_at_max():
    from anjo.core.facts import merge_facts, load_facts, _MAX_FACTS
    for i in range(_MAX_FACTS + 5):
        merge_facts("u_cap", [f"unique fact number {i}"])
    assert len(load_facts("u_cap")) <= _MAX_FACTS


# ── Confidence ────────────────────────────────────────────────────────────────

def test_confidence_stored_and_retrieved():
    from anjo.core.facts import merge_facts, load_facts_with_confidence
    merge_facts("u_conf", ["might be studying medicine"], confidences=[0.6])
    pairs = load_facts_with_confidence("u_conf")
    assert pairs
    text, conf = pairs[0]
    assert conf == pytest.approx(0.6)


# ── Backwards compatibility ───────────────────────────────────────────────────

def test_reads_old_plain_string_format(tmp_path, monkeypatch):
    """Existing rows stored as plain JSON string arrays should still load correctly."""
    import anjo.core.db as _db
    from anjo.core.crypto import encrypt_db
    from anjo.core.facts import load_facts, load_facts_with_confidence

    # Write a row in the old format directly
    db = _db.get_db()
    old_facts = json.dumps(["old fact one", "old fact two"])
    old_confs = json.dumps([1.0, 0.8])
    db.execute(
        "INSERT INTO facts (user_id, facts_json, confidence_json, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "facts_json = excluded.facts_json, "
        "confidence_json = excluded.confidence_json, "
        "updated_at = excluded.updated_at",
        ("u_old_fmt", encrypt_db(old_facts), encrypt_db(old_confs), "2025-12-01T00:00:00+00:00"),
    )
    db.commit()

    facts = load_facts("u_old_fmt")
    assert "old fact one" in facts
    assert "old fact two" in facts

    pairs = load_facts_with_confidence("u_old_fmt")
    conf_map = {t: c for t, c in pairs}
    assert conf_map.get("old fact two") == pytest.approx(0.8)


def test_old_format_migrates_to_new_on_merge(tmp_path, monkeypatch):
    """After merge_facts on an old-format row, subsequent reads use new format."""
    import anjo.core.db as _db
    from anjo.core.crypto import encrypt_db
    from anjo.core.facts import merge_facts, load_facts_with_meta

    db = _db.get_db()
    old_facts = json.dumps(["legacy fact"])
    db.execute(
        "INSERT INTO facts (user_id, facts_json, confidence_json, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "facts_json = excluded.facts_json, "
        "confidence_json = excluded.confidence_json, "
        "updated_at = excluded.updated_at",
        ("u_migrate", encrypt_db(old_facts), encrypt_db("[]"), "2025-11-01T00:00:00+00:00"),
    )
    db.commit()

    merge_facts("u_migrate", ["new fact"])

    meta = load_facts_with_meta("u_migrate")
    texts = [m["text"] for m in meta]
    assert "new fact" in texts
    assert "legacy fact" in texts
    # All records should now be dicts with added_at
    for m in meta:
        assert m["added_at"], "Every record should have an added_at timestamp"
