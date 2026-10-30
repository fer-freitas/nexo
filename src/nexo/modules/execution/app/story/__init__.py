"""Execução da história.

QA test-first → tarefas em paralelo → integração → qualidade → revisões → portão humano → PR.
"""

from __future__ import annotations

from nexo.modules.execution.app.story.common import StoryReport, is_story, is_test_file, sha256_file
from nexo.modules.execution.app.story.runner import StoryRunner

__all__ = ["StoryReport", "StoryRunner", "is_story", "is_test_file", "sha256_file"]
