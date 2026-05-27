"""
Credential Loader - Reads Etere credentials from .env (or credentials.env)

Keeps credentials out of source code. Env files are excluded from git via .gitignore.

Usage:
    from credential_loader import load_credentials

    username, password = load_credentials()
"""

from pathlib import Path
from typing import Tuple


def load_credentials(env_path: Path | None = None) -> Tuple[str, str]:
    """
    Load Etere username and password from .env or credentials.env.

    Looks for .env first, then credentials.env in the project root,
    or accepts a custom path.

    Args:
        env_path: Optional path to env file. Defaults to the first
                  existing file among .env, credentials.env.

    Returns:
        (username, password) tuple

    Raises:
        FileNotFoundError: If no env file exists
        ValueError: If required keys are missing or still placeholder
    """
    if env_path is None:
        root = Path(__file__).parent
        env_path = next(
            (root / name for name in (".env", "credentials.env") if (root / name).is_file()),
            root / ".env",
        )

    if not env_path.exists():
        raise FileNotFoundError(
            f"Credentials file not found: {env_path}\n"
            f"Create it with ETERE_USERNAME and ETERE_PASSWORD entries."
        )

    values = _parse_env_file(env_path)

    username = values.get("ETERE_USERNAME", "")
    password = values.get("ETERE_PASSWORD", "")

    if not username or username == "your_username_here":
        raise ValueError(
            f"ETERE_USERNAME not set in {env_path.name}. "
            "Please replace the placeholder with your actual username."
        )

    if not password or password == "your_password_here":
        raise ValueError(
            f"ETERE_PASSWORD not set in {env_path.name}. "
            "Please replace the placeholder with your actual password."
        )

    return username, password


def _parse_env_file(path: Path) -> dict[str, str]:
    """
    Parse a simple .env file into a dict.

    Handles:
        - KEY=VALUE lines
        - Comments (# ...) and blank lines
        - Quoted values (strips surrounding quotes)

    Args:
        path: Path to .env file

    Returns:
        Dict of key-value pairs
    """
    result: dict[str, str] = {}

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        # Skip comments and blanks
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Strip surrounding quotes if present
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        result[key] = value

    return result
