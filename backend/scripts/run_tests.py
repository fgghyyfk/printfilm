"""Run pytest using the isolated local test environment from any directory."""
from pathlib import Path
import os
import sys

from dotenv import load_dotenv


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    env_file = backend / ".env.test"
    if not env_file.is_file():
        raise SystemExit("Copy backend/.env.test.example to backend/.env.test first.")
    load_dotenv(env_file, override=True)
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    import pytest

    return pytest.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
