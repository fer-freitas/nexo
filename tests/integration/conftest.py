"""Fixtures compartilhadas entre arquivos de integração."""

from tests.integration.test_learning_eval import delivered  # noqa: F401 — projeto com história entregue
from tests.integration.test_story import planned  # noqa: F401 — fixture de história planejada
from tests.integration.test_telemetry import (
    central,  # noqa: F401 — coletor de teste (SQLite e PostgreSQL)
    pg_server,  # noqa: F401
)
from tests.integration.test_trackers import
