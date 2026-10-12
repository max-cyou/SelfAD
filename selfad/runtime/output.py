from selfad.runtime.types import (
    DEFAULT_FLAG_PATTERN,
    MAX_COMMAND_OUTPUT,
)


def _output_tokens(output: str) -> set[str]:
    return {
        line.strip() for line in output.splitlines() if DEFAULT_FLAG_PATTERN.fullmatch(line.strip())
    }


def _stdout_has_noise(output: str) -> bool:
    return any(not DEFAULT_FLAG_PATTERN.fullmatch(line.strip()) for line in output.splitlines())


def _join_logs(*logs: tuple[str, str]) -> str:
    return "\n".join(_format_log(label, output) for label, output in logs if output.strip())[
        :MAX_COMMAND_OUTPUT
    ]


def _script_failure_message(label: str, output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    detail = lines[-1][:500] if lines else "exited with a non-zero status"
    return f"{label} failed: {detail}"


def _format_log(label: str, output: str) -> str:
    return f"[{label}]\n{output.strip()}"[:MAX_COMMAND_OUTPUT]
