from __future__ import annotations

import asyncio

from worker.tasks import moderation_hygiene as mh


def test_hygiene_runs_both_steps(monkeypatch) -> None:
    calls = {}

    async def _purge(db):
        calls["purge"] = True
        return 3

    async def _scrub(db):
        calls["scrub"] = True
        return 2

    monkeypatch.setattr(mh.search_cache, "purge_stale_queries", _purge)
    monkeypatch.setattr(mh.channel_flag_service, "scrub_resolved_titles", _scrub)
    out = asyncio.run(mh._run_hygiene())
    assert out == {"purged": 3, "scrubbed": 2}
    assert calls == {"purge": True, "scrub": True}


def test_hygiene_swallows_purge_error_and_still_scrubs(monkeypatch) -> None:
    async def _boom(db):
        raise RuntimeError("db down")

    async def _scrub(db):
        return 5

    monkeypatch.setattr(mh.search_cache, "purge_stale_queries", _boom)
    monkeypatch.setattr(mh.channel_flag_service, "scrub_resolved_titles", _scrub)
    out = asyncio.run(mh._run_hygiene())  # 不抛
    assert out["purged"] == 0
    assert out["scrubbed"] == 5


def test_hygiene_registered_in_beat() -> None:
    from worker.celery_app import celery_app

    sched = celery_app.conf.beat_schedule
    assert any(v["task"] == "worker.tasks.run_moderation_hygiene" for v in sched.values())
