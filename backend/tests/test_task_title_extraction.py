"""P2-3（2026-08-08）：payload 显式 script=None 不得落库字面量 "None" 作 title。

dict.get("script", "") 的默认值只在键缺失时生效——键存在但值为 None 时
str(None)="None" 成为 title。修为先 ``or ""`` 再 str。
"""

from __future__ import annotations

import asyncio

import pytest

from app.gateway._shared.task_framework.service import BaseTaskService


class _FakeRepo:
    def __init__(self):
        self.created = {}

    async def create(self, **kwargs):
        self.created.update(kwargs)
        return type("Row", (), kwargs)()


class _Svc(BaseTaskService):
    domain = "test"
    id_prefix = "ts"

    def __init__(self, repo):
        self.repo = repo
        self._storage_dir = "/tmp"
        self._active_tasks = {}
        self._concurrency = asyncio.Semaphore(5)
        self._max_tasks_per_user = 3
        self._user_sems = {}
        self._deleting = set()
        self._recovering = set()
        self._reaper_task = None

    def _get_user_sem(self, user_id):
        return asyncio.Semaphore(3)

    async def _run_pipeline(self, *a, **k):
        raise NotImplementedError

    def _should_handoff_to_worker(self, task_type):
        return False

    async def _cleanup_task(self, task):
        pass


@pytest.mark.asyncio
async def test_script_none_does_not_become_literal_none_title():
    repo = _FakeRepo()
    svc = _Svc(repo)
    await svc.submit(user_id="u1", task_type="ppt_gen", payload={"script": None}, auto_start=False)
    assert repo.created["title"] is None, f"script=None 不得落库字面量 'None'，实际 {repo.created['title']!r}"


@pytest.mark.asyncio
async def test_script_string_still_truncates_to_title():
    repo = _FakeRepo()
    svc = _Svc(repo)
    script = "这是一个超过五十字的脚本" * 10
    await svc.submit(user_id="u1", task_type="ppt_gen", payload={"script": script}, auto_start=False)
    assert repo.created["title"] == script[:50]


@pytest.mark.asyncio
async def test_topic_takes_precedence_over_script():
    repo = _FakeRepo()
    svc = _Svc(repo)
    await svc.submit(user_id="u1", task_type="ppt_gen", payload={"topic": "主题", "script": None}, auto_start=False)
    assert repo.created["title"] == "主题"
