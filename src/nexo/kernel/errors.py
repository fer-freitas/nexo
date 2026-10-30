"""Erros de domínio com código de saída estável para a CLI."""

from __future__ import annotations


class ExitCode:
    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_AVAILABLE = 3
    POLICY_VIOLATION = 4
    INTEGRITY_FAILURE = 5


class NexoError(Exception):
    exit_code = ExitCode.ERROR

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotAvailableInPhase(NexoError):
    exit_code = ExitCode.NOT_AVAILABLE

    def __init__(self, command: str, phase: int) -> None:
        super().__init__(f"'{command}' disponível na fase {phase}")
        self.command = command
        self.phase = phase


class PolicyViolation(NexoError):
    exit_code = ExitCode.POLICY_VIOLATION


class IntegrityFailure(NexoError):
    exit_code = ExitCode.INTEGRITY_FAILURE
