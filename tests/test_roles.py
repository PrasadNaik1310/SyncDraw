
"""Admin / viewer role tests. Starts its own server on port 8765.

Run from the project root (stop any running server first):
    python -m unittest tests.test_roles -v
"""

import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from common.protocol import send_message, recv_message

HOST = "127.0.0.1"
PORT = 8765
ROOT = Path(__file__).resolve().parent.parent
TEST_PIN = "pin-7391"
VIEW_ONLY = "View-only: only the admin can draw"


def port_open():
    try:
        socket.create_connection((HOST, PORT), timeout=0.2).close()
        return True
    except OSError:
        return False


def start_server(admin_pin, log_file):
    if port_open():
        raise RuntimeError(f"Port {PORT} is in use; stop the running server first")

    # Explicit env values win over .env (python-dotenv does not override).
    # An empty GEMINI_API_KEY guarantees no real Gemini call.
    env = dict(os.environ, ADMIN_PIN=admin_pin, GEMINI_API_KEY="", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "server.main"],
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    for _ in range(50):
        if port_open():
            return proc
        time.sleep(0.1)

    proc.kill()
    raise RuntimeError("Server did not start")


class Client:
    def __init__(self, client_id=None):
        self.id = client_id or f"role_{uuid.uuid4().hex[:6]}"
        self.sock = socket.create_connection((HOST, PORT), timeout=5)
        send_message(self.sock, {"type": "HELLO", "client_id": self.id})
        self.snapshot = recv_message(self.sock)
        assert self.snapshot["type"] == "SNAPSHOT", self.snapshot
        self.first_role = recv_message(self.sock)

    def send(self, message):
        send_message(self.sock, message)

    def collect(self, seconds=0.5):
        """Every message that arrives within `seconds`."""
        out, end = [], time.time() + seconds
        while (left := end - time.time()) > 0:
            self.sock.settimeout(left)
            try:
                out.append(recv_message(self.sock))
            except socket.timeout:
                break
        return out

    def draw(self, y=50):
        self.send({"type": "DRAW", "tool": "pen", "x1": 10, "y1": y,
                   "x2": 100, "y2": y, "color": "#000000", "width": 3})

    def close(self, abrupt=False):
        if abrupt:
            # RST instead of a clean FIN, like a crashed client.
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self.sock.close()


def of_type(messages, msg_type):
    return [m for m in messages if m["type"] == msg_type]


class ServerTestCase(unittest.TestCase):
    ADMIN_PIN = ""

    @classmethod
    def setUpClass(cls):
        cls.log = tempfile.TemporaryFile(mode="w+")
        cls.proc = start_server(cls.ADMIN_PIN, cls.log)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.log.close()

    def setUp(self):
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            try:
                c.close()
            except OSError:
                pass
        time.sleep(0.3)  # let the server free the admin slot

    def connect(self, client_id=None):
        c = Client(client_id)
        self.clients.append(c)
        return c

    def make_admin(self, c, pin=None):
        message = {"type": "ADMIN_REQUEST"}
        if pin is not None:
            message["pin"] = pin
        c.send(message)
        roles = of_type(c.collect(), "ROLE_UPDATE")
        self.assertTrue(roles and roles[-1]["role"] == "admin", roles)

    def current_version(self):
        return self.connect().snapshot["version"]


class TestRolesNoPin(ServerTestCase):
    ADMIN_PIN = ""

    def test_01_new_client_is_viewer(self):
        a = self.connect()
        self.assertEqual(a.first_role["type"], "ROLE_UPDATE")
        self.assertEqual(a.first_role["role"], "viewer")
        self.assertIsNone(a.first_role["admin_id"])
        self.assertFalse(a.first_role["pin_required"])

    def test_02_viewer_draw_rejected(self):
        a, b = self.connect(), self.connect()
        v = a.snapshot["version"]
        a.draw()
        ma, mb = a.collect(), b.collect()
        self.assertEqual(of_type(ma, "ERROR"), [{"type": "ERROR", "message": VIEW_ONLY}])
        self.assertEqual(of_type(ma + mb, "DRAW_UPDATE"), [])
        self.assertEqual(self.current_version(), v)

    def test_03_grant_broadcast(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        a.send({"type": "ADMIN_REQUEST"})
        ra = of_type(a.collect(), "ROLE_UPDATE")
        rb = of_type(b.collect(), "ROLE_UPDATE")
        rc = of_type(c.collect(), "ROLE_UPDATE")
        self.assertEqual((ra[-1]["role"], ra[-1]["admin_id"]), ("admin", a.id))
        for r in (rb, rc):
            self.assertEqual((r[-1]["role"], r[-1]["admin_id"]), ("viewer", a.id))

    def test_04_second_admin_refused(self):
        a, b = self.connect(), self.connect()
        self.make_admin(a)
        b.collect()
        b.send({"type": "ADMIN_REQUEST"})
        mb, ma = b.collect(), a.collect()
        self.assertEqual(of_type(mb, "ERROR")[0]["message"], f"Admin already taken by {a.id}")
        self.assertEqual(of_type(mb + ma, "ROLE_UPDATE"), [])

    def test_05_admin_draw_no_gap(self):
        a, b = self.connect(), self.connect()
        self.make_admin(a)
        b.collect()
        v = self.current_version()
        b.draw()      # rejected: must not consume a version
        a.draw(60)
        a.draw(70)
        versions = [m["version"] for m in of_type(b.collect(), "DRAW_UPDATE")]
        self.assertEqual(versions, [v + 1, v + 2])

    def test_06_release(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        self.make_admin(a)
        b.collect(), c.collect()
        a.send({"type": "ADMIN_RELEASE"})
        for client in (a, b, c):
            r = of_type(client.collect(), "ROLE_UPDATE")[-1]
            self.assertEqual((r["role"], r["admin_id"]), ("viewer", None))
        self.make_admin(b)

    def test_07_admin_disconnect_frees_slot(self):
        a, b = self.connect(), self.connect()
        self.make_admin(a)
        b.collect()
        a.close(abrupt=True)
        self.clients.remove(a)
        r = of_type(b.collect(1), "ROLE_UPDATE")
        self.assertTrue(r and r[-1]["admin_id"] is None, r)
        self.make_admin(b)

    def test_08_simultaneous_requests(self):
        for _ in range(10):
            a, b = self.connect(), self.connect()
            barrier = threading.Barrier(2)

            def request(c):
                barrier.wait()
                c.send({"type": "ADMIN_REQUEST"})

            threads = [threading.Thread(target=request, args=(c,)) for c in (a, b)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            results = {}
            for c in (a, b):
                msgs = c.collect()
                roles = of_type(msgs, "ROLE_UPDATE")
                results[c.id] = "admin" if roles and roles[-1]["role"] == "admin" else "refused"
                if results[c.id] == "refused":
                    self.assertIn("Admin already taken", of_type(msgs, "ERROR")[0]["message"])

            self.assertEqual(sorted(results.values()), ["admin", "refused"], results)
            for c in (a, b):
                c.close()
            self.clients.clear()
            time.sleep(0.3)

    def test_09_no_pin_one_click(self):
        a = self.connect()
        self.make_admin(a)  # no "pin" field at all

    def test_10_spoofed_client_id(self):
        a = self.connect()
        self.make_admin(a)
        x = self.connect(client_id=a.id)  # same client ID, different connection
        self.assertEqual(x.first_role["role"], "viewer")
        v = x.snapshot["version"]
        x.draw()
        self.assertEqual(of_type(x.collect(), "ERROR")[0]["message"], VIEW_ONLY)
        self.assertEqual(of_type(a.collect(), "DRAW_UPDATE"), [])
        self.assertEqual(self.current_version(), v)

    def test_11_viewer_summary(self):
        a = self.connect()
        a.send({"type": "SUMMARY_REQUEST"})
        msgs = a.collect(1.5)
        self.assertEqual(of_type(msgs, "ERROR"), [])
        response = of_type(msgs, "SUMMARY_RESPONSE")
        self.assertEqual(len(response), 1, msgs)
        # No key in tests, so it either fails on the key or the empty board.
        self.assertIn(response[0]["error"], (
            "Gemini API key not configured on server.",
            "Board is empty, nothing to summarize.",
        ))


class TestRolesWithPin(ServerTestCase):
    ADMIN_PIN = TEST_PIN

    @classmethod
    def tearDownClass(cls):
        cls.log.seek(0)
        log = cls.log.read()
        super().tearDownClass()
        assert TEST_PIN not in log, "server logged the PIN"

    def test_09_pin(self):
        a, b = self.connect(), self.connect()
        self.assertTrue(a.first_role["pin_required"])

        for bad in ({"type": "ADMIN_REQUEST"},
                    {"type": "ADMIN_REQUEST", "pin": "wrong"},
                    {"type": "ADMIN_REQUEST", "pin": 7391}):
            a.send(bad)
            msgs = a.collect()
            self.assertEqual(of_type(msgs, "ERROR"), [{"type": "ERROR", "message": "Wrong admin PIN"}])
            self.assertEqual(of_type(msgs + b.collect(), "ROLE_UPDATE"), [])

        self.make_admin(a, pin=TEST_PIN)
        r = of_type(b.collect(), "ROLE_UPDATE")[-1]
        self.assertEqual(r["admin_id"], a.id)


if __name__ == "__main__":
    unittest.main()
