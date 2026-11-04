"""JobPilot local native-messaging host + CLI (stdlib only).

Hostname: com.jobpilot.local
Framing (native + socket, both directions): 4-byte little-endian length
followed by that many UTF-8 JSON bytes. Final encoded JSON payload must be
<= 900 KiB. Reads are exact; truncated/invalid frames raise.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import stat
import os
import shlex
import socket
import struct
import sys
import threading
import time
import uuid
from pathlib import Path

HOST_NAME = "com.jobpilot.local"
MAX_FRAME_BYTES = 900 * 1024
MAX_PDF_BYTES = 512 * 1024
SOCKET_NAME = "bridge.sock"
CONFIG_NAME = "config.json"
RUNTIME_NAME = "jobpilot_local_runtime.py"
LAUNCHER_NAME = "jobpilot-local-host"
MANIFEST_NAME = HOST_NAME + ".json"

EXTENSION_ID_RE_PART = "extension id must be exactly 32 chars a-p"


def default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "jobpilot-local"


def default_manifest_dir() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Google"
        / "Chrome"
        / "NativeMessagingHosts"
    )


def validate_extension_id(ext_id: str) -> str:
    if len(ext_id) != 32 or any(c not in "abcdefghijklmnop" for c in ext_id):
        raise ValueError(EXTENSION_ID_RE_PART + f": {ext_id!r}")
    return ext_id


def origin_for(ext_id: str) -> str:
    validate_extension_id(ext_id)
    return f"chrome-extension://{ext_id}/"


def socket_path_for(data_dir: Path) -> Path:
    return Path(data_dir) / SOCKET_NAME


def config_path_for(data_dir: Path) -> Path:
    return Path(data_dir) / CONFIG_NAME


def ensure_private_dir(path: Path) -> Path:
    """Create owner-only 0700 dir; reject symlinks. Never deletes unrelated files."""
    p = Path(path)
    if p.is_symlink():
        raise ValueError(f"refusing unsafe symlinked directory: {p}")
    # Reject if any existing path component resolution differs (symlink inside).
    # Only check the final dir itself plus direct parent chain for symlinks.
    for parent in [p, *p.parents]:
        if parent.exists() and parent.is_symlink():
            raise ValueError(f"refusing symlinked path component: {parent}")
        # Stop at home to avoid scanning to root.
        if parent == Path.home():
            break
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = p.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise ValueError(f"refusing directory without current ownership and owner-only 0700 mode: {p}")
    return p


def check_private_dir_strict(path: Path) -> None:
    p = Path(path)
    if p.is_symlink():
        raise ValueError(f"refusing unsafe symlinked directory: {p}")
    if not p.is_dir():
        raise ValueError(f"not a directory: {p}")
    st = os.stat(p)
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise ValueError(f"refusing directory without owner-only 0700 mode: {p}")


def read_exact(stream, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if chunk is None:
            continue
        if isinstance(chunk, str):
            raise ValueError("byte stream required")
        if not chunk:
            raise EOFError(f"truncated frame: want {n} bytes, got {len(buf)}")
        buf.extend(chunk)
    return bytes(buf)


def read_frame(stream) -> object:
    hdr = read_exact(stream, 4)
    (size,) = struct.unpack("<I", hdr)
    if size > MAX_FRAME_BYTES:
        raise ValueError(f"frame too large: {size} > {MAX_FRAME_BYTES}")
    payload = read_exact(stream, size)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError("frame is not UTF-8") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError("frame is not JSON") from e


def encode_frame(obj: object) -> bytes:
    raw = json.dumps(obj).encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError(f"message too large: {len(raw)} > {MAX_FRAME_BYTES}")
    return struct.pack("<I", len(raw)) + raw


def write_frame(stream, obj: object) -> None:
    data = encode_frame(obj)
    stream.write(data)
    stream.flush()


def load_profile_file(path: Path) -> object:
    text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"profile is not valid JSON: {path}") from e


def load_resume_file(path: Path) -> dict:
    p = Path(path)
    raw = p.read_bytes()
    if len(raw) > MAX_PDF_BYTES:
        raise ValueError(f"resume too large: {len(raw)} > {MAX_PDF_BYTES}")
    if not raw.startswith(b"%PDF-"):
        raise ValueError("resume is not a real PDF (missing %PDF- header)")
    b64 = base64.b64encode(raw).decode("ascii")
    return {
        "metadata": {"name": p.name, "type": "application/pdf", "size": len(raw)},
        "dataUrl": "data:application/pdf;base64," + b64,
    }


def build_request(base: dict, timeout_s: float, now_ms: int | None = None) -> dict:
    if not isinstance(base, dict):
        raise ValueError("request file must contain a JSON object")
    if not math.isfinite(timeout_s) or not 0 < timeout_s <= 300:
        raise ValueError("timeout must be between zero and 300 seconds")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    req = dict(base)
    req["id"] = str(uuid.uuid4())
    req["deadline"] = now + int(timeout_s * 1000)
    return req


def is_expired(req: dict, now_ms: int | None = None) -> bool:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        return int(req.get("deadline", 0)) < now
    except (TypeError, ValueError):
        return True


def timeout_error(req_id: str) -> dict:
    return {
        "id": req_id,
        "ok": False,
        "error": {
            "code": "timeout",
            "message": "no response before deadline; state unknown, possible mutation; not retried",
            "possibleMutation": True,
        },
    }


def install_host(extension_id: str, data_dir: Path, manifest_dir: Path) -> dict:
    validate_extension_id(extension_id)
    origin = origin_for(extension_id)
    data_dir = Path(data_dir).expanduser().absolute()
    manifest_dir = Path(manifest_dir).expanduser().absolute()
    if manifest_dir.is_symlink():
        raise ValueError(f"refusing unsafe symlinked directory: {manifest_dir}")
    ensure_private_dir(data_dir)
    # Refuse to clobber a live competing socket.
    sp = socket_path_for(data_dir)
    if sp.is_symlink():
        raise ValueError(f"refusing symlinked socket path: {sp}")
    if sp.exists():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.5)
            probe.connect(str(sp))
        except OSError:
            pass
        else:
            raise ValueError(f"refusing to replace live socket: {sp}")
        finally:
            probe.close()
    ensure_private_dir(manifest_dir)
    # Copy self to persistent runtime (preserve unrelated files: only write ours).
    try:
        src = Path(__file__).resolve()
        src_text = src.read_text(encoding="utf-8")
    except OSError:
        src_text = None
    for path in [data_dir / RUNTIME_NAME, data_dir / LAUNCHER_NAME, config_path_for(data_dir), manifest_dir / MANIFEST_NAME]:
        if path.is_symlink() or path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid()):
            raise ValueError("refusing an unsafe managed host file")
    runtime = data_dir / RUNTIME_NAME
    if src_text is not None:
        runtime.write_text(src_text, encoding="utf-8")
        os.chmod(runtime, 0o700)
    else:
        # Fallback: runtime path points at current file.
        runtime = Path(__file__).resolve()
    interpreter = sys.executable
    if not os.path.isabs(interpreter):
        raise ValueError("interpreter path must be absolute")
    launcher = data_dir / LAUNCHER_NAME
    launcher_text = (
        "#!/bin/sh\n"
        f"exec {shlex.quote(interpreter)} {shlex.quote(str(runtime))}"
        f" host --data-dir {shlex.quote(str(data_dir))} \"$@\"\n"
    )
    launcher.write_text(launcher_text, encoding="utf-8")
    os.chmod(launcher, 0o700)
    config = {"extensionId": extension_id, "origin": origin, "hostName": HOST_NAME}
    config_path_for(data_dir).write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.chmod(config_path_for(data_dir), 0o600)
    manifest = {
        "name": HOST_NAME,
        "description": "JobPilot local application bridge",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [origin],
    }
    manifest_path = manifest_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    try:
        os.chmod(manifest_path, 0o600)
    except OSError:
        pass
    return {"dataDir": str(data_dir), "launcher": str(launcher),
            "manifest": str(manifest_path), "origin": origin}


def load_config(data_dir: Path) -> dict:
    p = config_path_for(data_dir)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ValueError(f"host not installed in {data_dir}; run install first") from e


class BridgeHost:
    """Unix-socket front end forwarding one serialized op to Chrome stdio."""

    def __init__(self, data_dir: Path, origin: str,
                 stdin=None, stdout=None):
        self.data_dir = Path(data_dir)
        self.origin = origin
        self.stdin = stdin if stdin is not None else sys.stdin.buffer
        self.stdout = stdout if stdout is not None else sys.stdout.buffer
        self._opin = self.stdin
        self._out = self.stdout
        self._responses: dict[str, object] = {}
        self._cond = threading.Condition()
        self._eof = threading.Event()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._own_socket = False
        self._socket_inode = None
        self._waiting = None
        self._listener: socket.socket | None = None
        self._shutdown = threading.Event()

    def check_origin(self) -> None:
        cfg = load_config(self.data_dir)
        if cfg.get("origin") != self.origin:
            raise ValueError(
                f"origin mismatch: got {self.origin!r}, installed {cfg.get('origin')!r}"
            )

    def socket_path(self) -> Path:
        return socket_path_for(self.data_dir)

    def start_reader(self) -> None:
        def run() -> None:
            try:
                while True:
                    try:
                        msg = read_frame(self._opin)
                    except EOFError:
                        break
                    except (ValueError, OSError):
                        break
                    if not isinstance(msg, dict):
                        continue
                    mid = msg.get("id")
                    with self._cond:
                        if isinstance(mid, str) and mid == self._waiting:
                            self._responses[mid] = msg
                        self._cond.notify_all()
            finally:
                self._eof.set()
                with self._cond:
                    self._cond.notify_all()

        t = threading.Thread(target=run, daemon=True, name="native-reader")
        t.start()
        self._reader = t

    def bind(self) -> socket.socket:
        ensure_private_dir(self.data_dir)
        check_private_dir_strict(self.data_dir)
        sp = self.socket_path()
        if sp.is_symlink():
            raise ValueError(f"refusing symlinked socket path: {sp}")
        if sp.exists():
            raise ValueError("refusing to replace an existing socket or file; reconcile the stopped host first")
        if len(os.fsencode(sp)) >= 104:
            raise ValueError("socket path exceeds the macOS length limit")
        lst = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        lst.bind(str(sp))
        os.chmod(sp, 0o600)
        self._socket_inode = sp.lstat().st_ino
        lst.listen(8)
        self._listener = lst
        self._own_socket = True
        return lst

    def cleanup(self) -> None:
        if self._own_socket:
            try:
                sp = self.socket_path()
                if sp.exists() and not sp.is_symlink() and sp.lstat().st_ino == self._socket_inode:
                    sp.unlink()
            except OSError:
                pass
            self._own_socket = False

    def handle_one(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        f = conn.makefile("rwb")
        try:
            try:
                req = read_frame(f)
            except (EOFError, ValueError, OSError) as e:
                try:
                    write_frame(f, {"id": "", "ok": False,
                                    "error": {"code": "bad-frame", "message": str(e)}})
                except OSError:
                    pass
                return
            if not isinstance(req, dict) or not isinstance(req.get("id"), str) or not 0 < len(req["id"]) <= 128:
                try:
                    write_frame(f, {"id": "", "ok": False,
                                    "error": {"code": "bad-request",
                                              "message": "request must be an object with id"}})
                except OSError:
                    pass
                return
            rid = str(req["id"])
            if is_expired(req) or not isinstance(req.get("deadline"), (int,float)) or not math.isfinite(req["deadline"]) or req["deadline"] > time.time()*1000 + 300000:
                try:
                    write_frame(f, {"id": rid, "ok": False,
                                    "error": {"code": "expired",
                                              "message": "request already expired; not forwarded"}})
                except OSError:
                    pass
                return
            with self._lock:
                if self._eof.is_set():
                    try:
                        write_frame(f, {"id": rid, "ok": False,
                                        "error": {"code": "host-gone",
                                                  "message": "native host closed"}})
                    except OSError:
                        pass
                    return
                try:
                    raw = encode_frame(req)
                except ValueError as e:
                    try:
                        write_frame(f, {"id": rid, "ok": False,
                                        "error": {"code": "too-large", "message": str(e)}})
                    except OSError:
                        pass
                    return
                try:
                    self._waiting = rid
                    self._out.write(raw)
                    self._out.flush()
                except (OSError, ValueError) as e:
                    try:
                        write_frame(f, {"id": rid, "ok": False,
                                        "error": {"code": "forward-failed",
                                                  "message": str(e)}})
                    except OSError:
                        pass
                    return
                # Wait for correlated response until the request deadline.
                deadline = req.get("deadline")
                try:
                    remain = max(0.0, (int(deadline) - int(time.time() * 1000)) / 1000.0)
                except (TypeError, ValueError):
                    remain = 0.0
                end = time.monotonic() + remain
                with self._cond:
                    while rid not in self._responses:
                        if self._eof.is_set():
                            break
                        wait = end - time.monotonic()
                        if wait <= 0:
                            break
                        self._cond.wait(timeout=wait)
                    resp = self._responses.pop(rid, None)
                    self._waiting = None
                if resp is None:
                    # Timeout or EOF: report unknown outcome, never replay.
                    try:
                        write_frame(f, timeout_error(rid))
                    except OSError:
                        pass
                    return
                try:
                    write_frame(f, resp)
                except (OSError, ValueError):
                    pass
        finally:
            try:
                f.close()
            except OSError:
                pass

    def serve_forever(self) -> int:
        self.check_origin()
        self.start_reader()
        lst = self.bind()
        # Stdout is exclusively framed native messages; diagnostics go to stderr.
        try:
            while not self._shutdown.is_set():
                if self._eof.is_set():
                    with self._lock:
                        # Idle EOF ends the host; a serialized op drains first
                        # because handle_one holds the lock.
                        break
                lst.settimeout(0.25)
                try:
                    conn, _ = lst.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with conn:
                    self.handle_one(conn)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                lst.close()
            except OSError:
                pass
            self.cleanup()
        return 0


def client_request(data_dir: Path, request: dict, timeout_s: float) -> dict:
    check_private_dir_strict(data_dir)
    sp = socket_path_for(data_dir)
    if not sp.exists():
        return {"id": request.get("id", ""), "ok": False, "error": {"code": "not-connected", "message": "Open JobPilot and click Connect.", "possibleMutation": False}}
    st = sp.lstat()
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600:
        raise ValueError("refusing socket without current ownership and owner-only 0600 mode")
    if sp.is_symlink():
        raise ValueError(f"refusing symlinked socket path: {sp}")
    raw = encode_frame(request)  # raises if over limit; single send only
    deadline_ms = int(request.get("deadline", 0))
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout_s)
        sock.connect(str(sp))
        f = sock.makefile("rwb")
        try:
            f.write(raw)
            f.flush()
            # Wait bounded by the earlier of socket timeout and request deadline.
            remain = max(0.0, (deadline_ms - int(time.time() * 1000)) / 1000.0)
            wait = min(timeout_s, remain) if remain > 0 else 0.0
            if wait <= 0:
                return timeout_error(str(request.get("id", "")))
            sock.settimeout(wait)
            try:
                resp = read_frame(f)
            except (EOFError, ValueError, OSError):
                return timeout_error(str(request.get("id", "")))
            if not isinstance(resp, dict) or resp.get("id") != request.get("id"):
                return timeout_error(str(request.get("id", "")))
            return resp
        finally:
            try:
                f.close()
            except OSError:
                pass
    except (OSError, ValueError):
        return timeout_error(str(request.get("id", "")))
    finally:
        try:
            sock.close()
        except OSError:
            pass


def cmd_install(args: argparse.Namespace) -> int:
    try:
        info = install_host(args.extension_id, Path(args.data_dir),
                            Path(args.manifest_dir or default_manifest_dir()))
    except ValueError as e:
        print(f"install failed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2))
    return 0


def cmd_host(args: argparse.Namespace) -> int:
    host = BridgeHost(Path(args.data_dir), args.origin)
    try:
        return host.serve_forever()
    except ValueError as e:
        print(f"host failed: {e}", file=sys.stderr)
        return 1


def cmd_request(args: argparse.Namespace) -> int:
    try:
        base = json.loads(Path(args.request).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"bad request file: {e}", file=sys.stderr)
        return 1
    if not isinstance(base, dict):
        print("bad request file: top level must be an object", file=sys.stderr)
        return 1
    try:
        if args.profile is not None:
            base["profile"] = load_profile_file(Path(args.profile))
        if args.resume is not None:
            base["resume"] = load_resume_file(Path(args.resume))
        req = build_request(base, float(args.timeout))
        resp = client_request(Path(args.data_dir), req, float(args.timeout))
    except ValueError as e:
        print(f"request failed: {e}", file=sys.stderr)
        return 1
    out = json.dumps(resp, indent=2)
    if args.output:
        target=Path(args.output)
        if target.is_symlink():
            raise ValueError("refusing a symlinked response file")
        fd=os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd,"w") as stream:
            stream.write(out + "\n")
        target.chmod(0o600)
    else:
        print(out)
    if isinstance(resp, dict) and resp.get("ok") is True:
        return 0
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    info: dict = {"dataDir": str(data_dir)}
    try:
        cfg = load_config(data_dir)
        info["installed"] = True
        info["origin"] = cfg.get("origin")
    except ValueError:
        info["installed"] = False
    sp = socket_path_for(data_dir)
    info["socket"] = str(sp)
    info["socketExists"] = sp.exists()
    live = False
    if sp.exists() and not sp.is_symlink():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.5)
            probe.connect(str(sp))
            live = True
        except OSError:
            live = False
        finally:
            probe.close()
    info["socketLive"] = live
    print(json.dumps(info, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="jobpilot_local")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("install", help="install native host and socket dir")
    pi.add_argument("--extension-id", required=True)
    pi.add_argument("--data-dir", default=str(default_data_dir()))
    pi.add_argument("--manifest-dir", default=None)
    pi.set_defaults(func=cmd_install)
    ph = sub.add_parser("host", help="run the native messaging host")
    ph.add_argument("--data-dir", required=True)
    ph.add_argument("origin")
    ph.set_defaults(func=cmd_host)
    pr = sub.add_parser("request", help="send one socket request")
    pr.add_argument("--request", required=True)
    pr.add_argument("--profile", default=None)
    pr.add_argument("--resume", default=None)
    pr.add_argument("--output", default=None)
    pr.add_argument("--data-dir", default=str(default_data_dir()))
    pr.add_argument("--timeout", type=float, default=90.0)
    pr.set_defaults(func=cmd_request)
    ps = sub.add_parser("status", help="show install/socket status")
    ps.add_argument("--data-dir", default=str(default_data_dir()))
    ps.set_defaults(func=cmd_status)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
