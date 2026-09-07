"""Central runtime paths. The product never imports the legacy personal config."""

import os
import secrets
from pathlib import Path

STATIC = Path(__file__).parent / "static"
MAX_BODY = 2 * 1024 * 1024


def data_path() -> Path:
    return Path(
        os.environ.get("JOBPILOT_PRODUCT_HOME", str(Path.home() / ".jobpilot-product"))
    ).expanduser()


def api_token(root: Path) -> str:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    path = root / "api-token"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        path.chmod(0o600)
        value = path.read_text().strip()
        if len(value) < 32 or not value.isascii():
            raise RuntimeError(
                "The local API credential is damaged. Move api-token out of the JobPilot data folder, then reopen the app."
            ) from None
        return value
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as handle:
        handle.write(token)
    return token
