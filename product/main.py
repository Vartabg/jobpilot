"""Launch the local product in an ordinary browser; no browser automation."""

import argparse
import fcntl
import json
import socket
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .api import create_app
from .config import data_path


def main():
    parser = argparse.ArgumentParser(description="Open JobPilot on this computer.")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=data_path())
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (root / "app.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if not args.no_open:
            try:
                state = json.loads((root / "running.json").read_text())
                if state.get("port") in range(1, 65536):
                    webbrowser.open(f"http://127.0.0.1:{state['port']}")
            except (OSError, ValueError):
                pass
        return
    sock = socket.socket()
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]
    state_path = root / "running.json"
    state_path.write_text(json.dumps({"port": port, "app": "jobpilot.product"}))
    server = None

    def quit_app():
        if server:
            server.should_exit = True

    app = create_app(root, on_quit=quit_app)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_config=None, access_log=False
        )
    )
    if not args.no_open:

        def open_when_ready():
            for _ in range(100):
                if server.started:
                    webbrowser.open(f"http://127.0.0.1:{port}")
                    return
                if server.should_exit:
                    return
                threading.Event().wait(0.1)

        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
        state_path.unlink(missing_ok=True)
        lock.close()


if __name__ == "__main__":
    main()
