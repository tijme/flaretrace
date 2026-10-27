"""Allow python -m flaretrace.runtime to run the same container command as the installed CLI."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
