"""Erros de domínio com código de saída estável para a CLI."""

from __future__ import annotations


class ExitCode:
    OK = 0
    ERROR = 1
    USAGE = 2
    GATE = 3  # portão reprovado
    WAITING = 4  # aguardando pessoa
    POLICY_VIOLATION = 5
    DISABLED = 6  # desligado por flag ou kill switch
    INTEGRITY_FAILURE = 7
    NO_CREDENTIAL = 8
    NOT_AVAILABLE = 3  # mantido para quem usa o nome anterior


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


class FeatureDisabled(NexoError):
    exit_code = ExitCode.DISABLED


class IntegrityFailure(NexoError):
    exit_code = ExitCode.INTEGRITY_FAILURE
