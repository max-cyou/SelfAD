import io
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from selfad.runtime.types import (
    MAX_ARCHIVE_FILES,
    MAX_EXTRACTED_BYTES,
    RunnerError,
)


def _extract_repository_archive(archive_bytes: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_size = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz")
    except tarfile.TarError as error:
        raise RunnerError("Gitea returned an invalid repository archive.") from error

    with archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
                if len(path.parts) < 2 and member.isdir():
                    continue
                raise RunnerError("Repository archive contains an unsafe path.")
            relative_parts = path.parts[1:]
            if not relative_parts:
                continue
            target = destination.joinpath(*relative_parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RunnerError("Repository archive contains unsupported file types.")

            file_count += 1
            total_size += member.size
            if file_count > MAX_ARCHIVE_FILES or total_size > MAX_EXTRACTED_BYTES:
                raise RunnerError("Repository archive exceeds runner limits.")
            source = archive.extractfile(member)
            if source is None:
                raise RunnerError("Repository archive contains an unreadable file.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)
