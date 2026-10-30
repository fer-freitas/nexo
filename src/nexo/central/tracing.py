"""Traces do coletor: um span por requisição (método, rota-modelo, status, duração), exportado em OTLP
para `NX_COLLECTOR_OTLP_ENDPOINT` a cada 10 s. Sem corpo, parâmetros ou identidade — só a rota-modelo."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any

from nexo.kernel.monitoring.otlp import STATUS_ERROR, Span, new_id, to_otlp

_queue: deque[Span] = deque(maxlen=10_000)
_lock = threading.Lock()


def enabled() -> bool:
    return bool(os.environ.get("NX_COLLECTOR_OTLP_ENDPOINT"))


def record(method: str, route: str, status: int, started_ns: int) -> None:
    if not enabled():
        return
    span = Span(f"{method} {route}", new_id(16), new_id(8), start_ns=started_ns, end_ns=time.time_ns())
    span.set(**{"http.request.method": method, "http.route": route, "http.response.status_code": status})
    if status >= 500:
        span.status = STATUS_ERROR
    with _lock:
        _queue.append(span)


def flush() -> int:
    endpoint = os.environ.get("NX_COLLECTOR_OTLP_ENDPOINT", "").rstrip("/")
    with _lock:
        spans = list(_queue)
        _queue.clear()
    if not endpoint or not spans:
        return 0
    from nexo.central import distribution

    resource: dict[str, Any] = {"service.name": os.environ.get("NX_COLLECTOR_SERVICE_NAME", "nexo-collector")}
    headers = (
        {"Authorization": os.environ["NX_COLLECTOR_OTLP_AUTH"]} if os.environ.get("NX_COLLECTOR_OTLP_AUTH") else {}
    )
    try:
        distribution.transport(f"{endpoint}/v1/traces", to_otlp(spans, resource, "nexo-collector"), headers)
    except (OSError, ValueError):
        with _lock:  # devolve à fila (limitada) para a próxima tentativa
            _queue.extendleft(reversed(spans))
        return 0
    return len(spans)


def start_exporter(interval: float = 10.0) -> threading.Thread:
    def loop() -> None:
        while True:
            time.sleep(interval)
            flush()

    thread = threading.Thread(target=loop, name="nexo-otlp", daemon=True)
    thread.start()
    return thread
