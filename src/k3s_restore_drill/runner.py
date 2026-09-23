from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Sequence

from .report import redact

MAX_LOG_BYTES = 16_384


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str
    timed_out: bool = False


class CommandRunner:
    """Run argv lists only; commands are never executed through a shell."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        self.secrets = secrets

    def run(self, argv: Sequence[str | Path], timeout: float) -> CommandResult:
        command = [str(item) for item in argv]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
            output = completed.stdout[-MAX_LOG_BYTES:]
            return CommandResult(completed.returncode, redact(output, self.secrets))
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "")[-MAX_LOG_BYTES:]
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            return CommandResult(124, redact(output, self.secrets), timed_out=True)
        except OSError as exc:
            return CommandResult(127, redact(str(exc), self.secrets))

    def start(self, argv: Sequence[str | Path]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [str(item) for item in argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )


def stop_process(process: subprocess.Popen[str], timeout: float = 15) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)
    if process.stdout is None:
        return ""
    return process.stdout.read()[-MAX_LOG_BYTES:]
