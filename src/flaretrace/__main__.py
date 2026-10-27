"""Run locally with python -m flaretrace from the installation directory."""

import sys
from pathlib import Path

from . import create_app


def main():
    """Start the local development server or acknowledge the legacy cleanup command."""
    application = create_app()
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        print("Automatic cleanup is disabled. Records are kept until manually deleted.")
    else:
        password_file = Path(application.config["DATA_DIR"]) / "admin-password"
        print(
            f"Administrator password: read {password_file} (or your ADMIN_PASSWORD environment variable)."
        )
        application.run(host="127.0.0.1", port=8000, debug=False)


if __name__ == "__main__":
    main()
