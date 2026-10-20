"""Apply a file-size rlimit in a fresh process, never via threaded preexec_fn."""

import os
import resource
import sys


def main():
    limit = int(sys.argv[1])
    if limit <= 0 or len(sys.argv) < 3:
        raise SystemExit(64)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    os.execvp(sys.argv[2], sys.argv[2:])


if __name__ == "__main__":
    main()
