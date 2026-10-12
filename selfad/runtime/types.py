import re
import threading
from dataclasses import dataclass

from selfad.database import DATA_DIR

WORK_DIR = DATA_DIR / "work"
JURY_IMAGE = "python:3.13-alpine"
MAX_ARCHIVE_FILES = 2_000
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024
MAX_COMMAND_OUTPUT = 128 * 1024
MAX_REQUIREMENTS_BYTES = 64 * 1024
DEFAULT_FLAG_PATTERN = re.compile(r"^[A-Z0-9]{32}$")
PARTICIPANT_REQUIREMENT_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*"
    r"(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?"
    r"==[A-Za-z0-9][A-Za-z0-9_.+!~-]*$"
)
RUNNER_LABEL = "selfad.managed=true"
SERVICE_CACHE_LABEL = "selfad.cache=canonical-service"
MAX_CACHED_SERVICE_IMAGES = 32
RUNNER_USER = "10001:10001"
_SERVICE_CACHE_BUILD_LOCK = threading.Lock()


class RunnerError(Exception):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


@dataclass(frozen=True)
class RuntimeCheckResult:
    passed: bool
    functionality_passed: bool
    message: str
    matched_flags: int
    log: str
    injected_flags: int = 0
    completed: bool = False
    stdout_noise: bool = False
