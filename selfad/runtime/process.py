import os
import selectors
import subprocess
import time

from selfad.runtime.types import (
    CommandResult,
    RunnerError,
)


def _terminate_process(process: subprocess.Popen, reason: str) -> None:
    """Stop a misbehaving command and convert a stubborn child into a RunnerError.

    ``wait`` after ``kill`` can still raise ``TimeoutExpired`` for processes
    stuck in uninterruptible I/O; without this the exception escapes the
    worker's error handling and the batch stays in PROCESSING until restart.
    """
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise RunnerError(f"{reason} The command also ignored the termination signal.") from error


def _run_command(
    arguments: list[str],
    *,
    timeout: int,
    max_output: int,
    environment: dict[str, str],
) -> CommandResult:
    try:
        process = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as error:
        raise RunnerError(f"Could not start {arguments[0]}.") from error
    assert process.stdout is not None
    assert process.stderr is not None

    stdout = bytearray()
    stderr = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout
    failure: str | None = None
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                failure = f"Command timed out after {timeout} seconds."
                break
            for key, _ in selector.select(timeout=0.2):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output = stdout if key.data == "stdout" else stderr
                output.extend(chunk)
                if len(stdout) + len(stderr) > max_output:
                    failure = f"Command output exceeded {max_output} bytes."
                    break
            if failure:
                break
        if failure:
            _terminate_process(process, failure)
            raise RunnerError(failure)
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired as error:
            raise RunnerError(
                f"Command {arguments[0]} did not exit after closing its output."
            ) from error
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # The child is stuck in uninterruptible I/O; nothing more to
                # do here. Resource cleanup by label catches it later.
                pass

    return CommandResult(
        returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )
