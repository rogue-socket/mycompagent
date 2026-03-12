"""Tests for the two-tiered memory system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from browser_agent.memory import (
    Lesson,
    MemoryStore,
    _domain_from_url,
    extract_lessons_from_run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_memory_path(tmp_path: Path) -> Path:
    return tmp_path / "memory.json"


@pytest.fixture()
def store(tmp_memory_path: Path) -> MemoryStore:
    ms = MemoryStore(path=tmp_memory_path)
    ms.load()  # seeds defaults
    return ms


# ---------------------------------------------------------------------------
# MemoryStore — persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_seed_defaults_creates_file(self, tmp_memory_path: Path) -> None:
        ms = MemoryStore(path=tmp_memory_path)
        ms.load()
        assert tmp_memory_path.exists()
        assert len(ms.lessons) >= 3  # at least the 3 seeds

    def test_save_and_reload(self, store: MemoryStore, tmp_memory_path: Path) -> None:
        store.record_lesson(Lesson(lesson="test", category="error_recovery"))
        store.save()

        reloaded = MemoryStore(path=tmp_memory_path)
        reloaded.load()
        assert any(ls.lesson == "test" for ls in reloaded.lessons)

    def test_corrupted_file_reseeds(self, tmp_memory_path: Path) -> None:
        tmp_memory_path.write_text("{bad json", encoding="utf-8")
        ms = MemoryStore(path=tmp_memory_path)
        ms.load()
        assert len(ms.lessons) >= 3  # reseeded

    def test_list_root_reseeds(self, tmp_memory_path: Path) -> None:
        tmp_memory_path.write_text('[{"lesson": "stray"}]', encoding="utf-8")
        ms = MemoryStore(path=tmp_memory_path)
        ms.load()
        assert len(ms.lessons) >= 3  # reseeded, not crashed


# ---------------------------------------------------------------------------
# Tier 1 — proactive retrieval
# ---------------------------------------------------------------------------


class TestTier1:
    def test_only_eligible_categories(self, store: MemoryStore) -> None:
        store.record_lesson(Lesson(lesson="err", category="error_recovery"))
        store.record_lesson(Lesson(lesson="site", category="site_specific", domain="x.com"))
        tier1 = store.get_tier1()
        categories = {ls.category for ls in tier1}
        assert categories <= {"tool_fallback", "best_practice"}

    def test_max_items_cap(self, store: MemoryStore) -> None:
        for i in range(20):
            store.record_lesson(
                Lesson(lesson=f"bp_{i}", category="best_practice")
            )
        assert len(store.get_tier1(max_items=5)) <= 5


# ---------------------------------------------------------------------------
# Tier 2 — reactive retrieval
# ---------------------------------------------------------------------------


class TestTier2:
    def test_recall_on_error_matches_command(self, store: MemoryStore) -> None:
        store.record_lesson(
            Lesson(
                lesson="use type instead",
                category="error_recovery",
                failed_command="fill",
                error_pattern="too many arguments",
            )
        )
        results = store.recall_on_error("fill", "too many arguments: expected 2")
        assert any("type instead" in r.lesson for r in results)

    def test_recall_on_error_no_match(self, store: MemoryStore) -> None:
        results = store.recall_on_error("goto", "some random error xyz")
        assert len(results) == 0

    def test_recall_on_domain(self, store: MemoryStore) -> None:
        store.record_lesson(
            Lesson(
                lesson="click cookie banner first",
                category="site_specific",
                domain="amazon.com",
            )
        )
        results = store.recall_on_domain("amazon.com")
        assert len(results) == 1
        assert "cookie" in results[0].lesson

    def test_recall_on_subdomain(self, store: MemoryStore) -> None:
        store.record_lesson(
            Lesson(lesson="sub", category="site_specific", domain="google.com")
        )
        assert len(store.recall_on_domain("www.google.com")) == 1

    def test_recall_on_domain_no_match(self, store: MemoryStore) -> None:
        assert store.recall_on_domain("unknown-site.example") == []

    def test_recall_on_error_case_insensitive(self, store: MemoryStore) -> None:
        store.record_lesson(
            Lesson(
                lesson="wait and retry",
                category="error_recovery",
                failed_command="click",
                error_pattern="timeout 30000ms exceeded",
            )
        )
        results = store.recall_on_error(
            "click", "Timeout 30000ms exceeded waiting for selector"
        )
        assert any("wait and retry" in r.lesson for r in results)


# ---------------------------------------------------------------------------
# Recording, deduplication, and promotion
# ---------------------------------------------------------------------------


class TestRecording:
    def test_deduplication(self, store: MemoryStore) -> None:
        lesson_a = Lesson(
            lesson="try X",
            category="error_recovery",
            failed_command="fill",
            error_pattern="bad arg",
        )
        lesson_b = Lesson(
            lesson="try Y",
            category="error_recovery",
            failed_command="fill",
            error_pattern="bad arg",
        )
        before = len(store.lessons)
        store.record_lesson(lesson_a)
        store.record_lesson(lesson_b)  # duplicate by (failed_command, error_pattern)
        assert len(store.lessons) == before + 1

    def test_promotion(self, store: MemoryStore) -> None:
        lesson = Lesson(
            lesson="recovery tip",
            category="error_recovery",
            failed_command="fill",
            error_pattern="err",
            use_count=4,
            triggered_domains=["a.com", "b.com"],
        )
        store.record_lesson(lesson)
        store.increment_use(lesson, "c.com")  # 5 uses, 3 domains → promote
        assert lesson.category == "best_practice"

    def test_no_promotion_with_domain_set(self, store: MemoryStore) -> None:
        lesson = Lesson(
            lesson="site thing",
            category="error_recovery",
            failed_command="fill",
            error_pattern="err",
            domain="specific.com",
            use_count=10,
            triggered_domains=["a.com", "b.com", "c.com", "d.com"],
        )
        store.record_lesson(lesson)
        store.increment_use(lesson, "e.com")
        assert lesson.category == "error_recovery"  # not promoted — domain-specific


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


class TestPruning:
    def test_seeds_never_pruned(self, store: MemoryStore) -> None:
        for ls in store.lessons:
            ls.last_used = "2000-01-01"
            ls.use_count = 0
        store.prune_stale(max_age_days=1)
        seeds = [ls for ls in store.lessons if ls.source == "seed"]
        assert len(seeds) >= 3

    def test_old_low_use_pruned(self, store: MemoryStore) -> None:
        store.record_lesson(
            Lesson(
                lesson="old",
                category="error_recovery",
                use_count=1,
                last_used="2020-01-01",
            )
        )
        store.prune_stale(max_age_days=1)
        assert not any(ls.lesson == "old" for ls in store.lessons)

    def test_old_high_use_kept(self, store: MemoryStore) -> None:
        store.record_lesson(
            Lesson(
                lesson="veteran",
                category="error_recovery",
                use_count=10,
                last_used="2020-01-01",
            )
        )
        store.prune_stale(max_age_days=1)
        assert any(ls.lesson == "veteran" for ls in store.lessons)


# ---------------------------------------------------------------------------
# Post-run learning
# ---------------------------------------------------------------------------


class TestPostRunLearning:
    def test_learns_from_failure_recovery(
        self, store: MemoryStore, tmp_path: Path
    ) -> None:
        actions_log = tmp_path / "actions.jsonl"
        actions_log.write_text(
            json.dumps(
                {
                    "step": 1,
                    "command": "playwright-cli fill e37 padel rackets",
                    "execution_result": "error",
                    "stderr": "too many arguments: expected 2, received 3",
                    "stdout": "",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "step": 2,
                    "command": "playwright-cli click e37",
                    "execution_result": "ok",
                    "stderr": "",
                    "stdout": "Page URL: https://www.google.com",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        before = len(store.lessons)
        extract_lessons_from_run(actions_log, store)
        assert len(store.lessons) > before

    def test_ignores_consecutive_failures(
        self, store: MemoryStore, tmp_path: Path
    ) -> None:
        actions_log = tmp_path / "actions.jsonl"
        actions_log.write_text(
            json.dumps(
                {
                    "step": 1,
                    "command": "playwright-cli fill e37 x",
                    "execution_result": "error",
                    "stderr": "too many arguments",
                    "stdout": "",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "step": 2,
                    "command": "playwright-cli fill e37 x",
                    "execution_result": "error",
                    "stderr": "too many arguments",
                    "stdout": "",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        before = len(store.lessons)
        extract_lessons_from_run(actions_log, store)
        assert len(store.lessons) == before  # no new lessons

    def test_ignores_same_command_pair(
        self, store: MemoryStore, tmp_path: Path
    ) -> None:
        actions_log = tmp_path / "actions.jsonl"
        actions_log.write_text(
            json.dumps(
                {
                    "step": 1,
                    "command": "playwright-cli click e5",
                    "execution_result": "error",
                    "stderr": "element not found and some other text to be long enough",
                    "stdout": "",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "step": 2,
                    "command": "playwright-cli click e6",
                    "execution_result": "ok",
                    "stderr": "",
                    "stdout": "",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        before = len(store.lessons)
        extract_lessons_from_run(actions_log, store)
        # click → click is same command, so not worthy
        assert len(store.lessons) == before


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


class TestDomainFromUrl:
    def test_basic(self) -> None:
        assert _domain_from_url("https://www.google.com/search?q=hi") == "www.google.com"

    def test_with_port(self) -> None:
        assert _domain_from_url("http://localhost:8080/path") == "localhost"

    def test_empty(self) -> None:
        assert _domain_from_url("") is None

    def test_no_scheme(self) -> None:
        assert _domain_from_url("about:blank") is None
