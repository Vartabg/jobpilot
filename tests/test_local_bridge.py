"""Transport/install tests for scripts/jobpilot_local.py (stdlib only)."""
import base64
import importlib.util
import io
import json
import os
import socket
import struct
import tempfile
import threading
import time
from pathlib import Path

import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "jobpilot_local.py"
spec = importlib.util.spec_from_file_location("jobpilot_local", MODULE_PATH)
jl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jl)

EXT = "a" * 32


class FramingTests(unittest.TestCase):
    def test_roundtrip(self):
        buf = io.BytesIO()
        jl.write_frame(buf, {"op": "tabs", "id": "1"})
        buf.seek(0)
        self.assertEqual(jl.read_frame(buf), {"op": "tabs", "id": "1"})
        hdr = buf.getvalue()[:4]
        self.assertEqual(struct.unpack("<I", hdr)[0], len(buf.getvalue()) - 4)

    def test_truncated_header_and_body(self):
        with self.assertRaises(EOFError):
            jl.read_frame(io.BytesIO(b"\x02\x00"))
        buf = io.BytesIO(struct.pack("<I", 10) + b"12345")
        with self.assertRaises(EOFError):
            jl.read_frame(buf)

    def test_invalid_json_and_utf8(self):
        bad = struct.pack("<I", 3) + b"\xff\xfe\xfd"
        with self.assertRaises(ValueError):
            jl.read_frame(io.BytesIO(bad))
        bad2 = struct.pack("<I", 3) + b"{{{"
        with self.assertRaises(ValueError):
            jl.read_frame(io.BytesIO(bad2))

    def test_limits_both_directions(self):
        big = "x" * (jl.MAX_FRAME_BYTES + 1)
        with self.assertRaises(ValueError):
            jl.encode_frame({"v": big})
        hdr = struct.pack("<I", jl.MAX_FRAME_BYTES + 1)
        with self.assertRaises(ValueError):
            jl.read_frame(io.BytesIO(hdr + b"{}"))

    def test_pdf_limits_and_header(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t).resolve() / "r.pdf"
            p.write_bytes(b"not a pdf")
            with self.assertRaisesRegex(ValueError, "PDF"):
                jl.load_resume_file(p)
            p.write_bytes(b"%PDF-1.4\n" + b"0" * (jl.MAX_PDF_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "too large"):
                jl.load_resume_file(p)
            p.write_bytes(b"%PDF-1.4 hello")
            att = jl.load_resume_file(p)
            self.assertEqual(att["metadata"]["type"], "application/pdf")
            self.assertTrue(att["dataUrl"].startswith("data:application/pdf;base64,"))
            raw = base64.b64decode(att["dataUrl"].split(",", 1)[1])
            self.assertTrue(raw.startswith(b"%PDF-"))

    def test_client_adds_id_and_deadline(self):
        before = int(time.time() * 1000)
        req = jl.build_request({"op": "tabs"}, 90, now_ms=before)
        self.assertTrue(req["id"])
        self.assertEqual(req["deadline"], before + 90000)
        self.assertEqual(req["op"], "tabs")


class InstallTests(unittest.TestCase):
    def test_install_exact_origin_quoted_paths_private_perms(self):
        with tempfile.TemporaryDirectory() as t:
            data = Path(t).resolve() / "dir with space" / "jobpilot-local"
            mani = Path(t).resolve() / "Native Hosts"
            info = jl.install_host(EXT, data, mani)
            self.assertEqual(info["origin"], f"chrome-extension://{EXT}/")
            self.assertEqual((data.stat().st_mode & 0o777), 0o700)
            manifest = json.loads(Path(info["manifest"]).read_text())
            self.assertEqual(manifest["name"], jl.HOST_NAME)
            self.assertEqual(manifest["allowed_origins"], [f"chrome-extension://{EXT}/"])
            launcher = Path(info["launcher"]).read_text()
            self.assertIn("'", launcher)  # shell-safe quoting for spaced path
            self.assertIn(str(data), launcher.replace("'", ""))
            cfg = json.loads((data / jl.CONFIG_NAME).read_text())
            self.assertEqual(cfg["origin"], f"chrome-extension://{EXT}/")
            self.assertTrue(Path(info["manifest"]).exists())
            # Unrelated files preserved.
            keep = mani / "keep.json"
            keep.write_text("{}")
            jl.install_host(EXT, data, mani)
            self.assertTrue(keep.is_file())

    def test_install_rejects_bad_id_and_symlink(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                jl.install_host("SHORT", Path(t).resolve() / "d", Path(t).resolve() / "m")
            with self.assertRaises(ValueError):
                jl.install_host("A" * 32, Path(t).resolve() / "d", Path(t).resolve() / "m")
            real = Path(t).resolve() / "real"
            real.mkdir()
            link = Path(t).resolve() / "link"
            os.symlink(real, link)
            with self.assertRaisesRegex(ValueError, "symlink"):
                jl.install_host(EXT, link, Path(t).resolve() / "m2")

    def test_install_refuses_live_socket(self):
        with tempfile.TemporaryDirectory() as t:
            data = Path(t).resolve() / "d"
            mani = Path(t).resolve() / "m"
            jl.install_host(EXT, data, mani)
            sp = data / jl.SOCKET_NAME
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(sp))
            os.chmod(sp,0o600)
            srv.listen(1)
            try:
                with self.assertRaisesRegex(ValueError, "live socket"):
                    jl.install_host(EXT, data, mani)
            finally:
                srv.close()
                sp.unlink(missing_ok=True)


class HostBehaviorTests(unittest.TestCase):
    def test_origin_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            data = Path(t).resolve() / "d"
            jl.install_host(EXT, data, Path(t).resolve() / "m")
            other = "b" * 32
            host = jl.BridgeHost(data, f"chrome-extension://{other}/")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                host.check_origin()

    def test_client_timeout_no_replay(self):
        with tempfile.TemporaryDirectory() as t:
            data = Path(t).resolve() / "d"
            jl.install_host(EXT, data, Path(t).resolve() / "m")
            sp = data / jl.SOCKET_NAME
            seen: list = []
            stop = threading.Event()

            def server():
                srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    srv.bind(str(sp))
                    os.chmod(sp,0o600)
                except OSError:
                    return
                srv.listen(1)
                srv.settimeout(5)
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    srv.close()
                    return
                with conn, srv:
                    f = conn.makefile("rwb")
                    try:
                        req = jl.read_frame(f)
                    except Exception:
                        return
                    seen.append(req)
                    # Never reply: force client timeout. Single receive only.
                    stop.wait(timeout=5)

            th = threading.Thread(target=server, daemon=True)
            th.start()
            time.sleep(0.2)
            req = jl.build_request({"op": "tabs"}, 0.3)
            t0 = time.time()
            resp = jl.client_request(data, req, 0.3)
            dt = time.time() - t0
            self.assertFalse(resp.get("ok"))
            self.assertTrue(resp["error"].get("possibleMutation"))
            self.assertEqual(resp["id"], req["id"])
            self.assertLess(dt, 5)
            stop.set()
            th.join(timeout=5)
            self.assertEqual(len(seen), 1)

    def test_stale_socket_unlinked_unsafe_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            data = Path(t).resolve() / "d"
            jl.install_host(EXT, data, Path(t).resolve() / "m")
            sp = data / jl.SOCKET_NAME
            # Existing files are never removed by a reconnect attempt.
            sp.write_bytes(b"stale")
            host = jl.BridgeHost(data, f"chrome-extension://{EXT}/")
            host.check_origin()
            with self.assertRaisesRegex(ValueError,"existing socket or file"):
                host.bind()
            self.assertEqual(sp.read_bytes(),b"stale")
            sp.unlink()
            lst = host.bind()
            self.assertTrue(sp.exists())
            lst.close()
            host.cleanup()
            self.assertFalse(sp.exists())
            # Symlinked socket path refused.
            real = Path(t).resolve() / "real.sock"
            os.symlink(str(sp), real) if False else None
            linkdir = Path(t).resolve() / "ld"
            linkdir.mkdir()
            target = linkdir / "t"
            target.write_bytes(b"x")
            splink = data / jl.SOCKET_NAME
            if splink.exists():
                splink.unlink()
            os.symlink(str(target), str(splink))
            host2 = jl.BridgeHost(data, f"chrome-extension://{EXT}/")
            with self.assertRaisesRegex(ValueError, "symlink"):
                host2.bind()
            splink.unlink()

    def test_native_roundtrip_single_op(self):
        with tempfile.TemporaryDirectory() as t:
            data = Path(t).resolve() / "d"
            jl.install_host(EXT, data, Path(t).resolve() / "m")
            origin = f"chrome-extension://{EXT}/"
            # Fake Chrome stdio: host reads responses from cin, writes to cout.
            cin_r, cin_w = os.pipe()
            cout_r, cout_w = os.pipe()
            cin_rf = os.fdopen(cin_r, "rb", buffering=0)
            cin_wf = os.fdopen(cin_w, "wb", buffering=0)
            cout_rf = os.fdopen(cout_r, "rb", buffering=0)
            cout_wf = os.fdopen(cout_w, "wb", buffering=0)

            host = jl.BridgeHost(data, origin, stdin=cin_rf, stdout=cout_wf)
            host.check_origin()
            host.start_reader()
            lst = host.bind()

            def chrome():
                # Read forwarded request, reply correlated response.
                req = jl.read_frame(cout_rf)
                jl.write_frame(cin_wf, {"id": req["id"], "ok": True, "result": {"tabs": []}})

            cth = threading.Thread(target=chrome, daemon=True)
            cth.start()
            def serve_one():
                conn,_=lst.accept()
                with conn: host.handle_one(conn)
            sth=threading.Thread(target=serve_one,daemon=True);sth.start()
            req = jl.build_request({"op": "tabs"}, 5)
            resp = jl.client_request(data, req, 5)
            sth.join(timeout=5)
            cth.join(timeout=5)
            lst.close()
            host.cleanup()
            cin_wf.close()
            cout_wf.close()
            host._reader.join(timeout=1)
            cin_rf.close(); cout_rf.close()
            self.assertEqual(resp.get("ok"), True)
            self.assertEqual(resp.get("id"), req["id"])

    def test_expired_request_not_forwarded(self):
        with tempfile.TemporaryDirectory() as t:
            data = Path(t).resolve() / "d"
            jl.install_host(EXT, data, Path(t).resolve() / "m")
            origin = f"chrome-extension://{EXT}/"
            host = jl.BridgeHost(data, origin, stdin=io.BytesIO(b""),
                                 stdout=io.BytesIO())
            req = {"id": "e1", "op": "tabs",
                   "deadline": int(time.time() * 1000) - 1000}
            srv = socket.socketpair()
            # Drive handle_one directly with an in-memory socket pair.
            done = {}
            orig_client = jl.client_request  # noqa: F841 (no replay check)
            a, b = srv
            af = a.makefile("rwb")
            jl.write_frame(af, req)
            af.flush()
            host.handle_one(b)
            resp = jl.read_frame(af)
            af.close()
            a.close()
            b.close()
            self.assertEqual(resp["id"], "e1")
            self.assertFalse(resp["ok"])
            self.assertIn("expired", json.dumps(resp))


if __name__ == "__main__":
    unittest.main()
