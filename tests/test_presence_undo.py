
"""Presence (online list, live cursors) and admin Undo / Clear tests.

Starts its own server on port 8765 (stop any running server first):
    python -m unittest tests.test_presence_undo -v
"""

import socket
import struct
import tempfile
import time
import unittest
import uuid

from common.protocol import send_message, recv_message
from tests.test_roles import start_server

VERSIONED = ("DRAW_UPDATE", "UNDO_UPDATE", "CLEAR_UPDATE")
PALETTE = {
    "#E11D48", "#EA580C", "#CA8A04", "#16A34A", "#0D9488",
    "#0284C7", "#4F46E5", "#9333EA", "#DB2777", "#65A30D",
}


class Client:
    def __init__(self, client_id=None, name=None):
        self.id = client_id or f"pu_{uuid.uuid4().hex[:6]}"
        self.sock = socket.create_connection(("127.0.0.1", 8765), timeout=5)
        hello = {"type": "HELLO", "client_id": self.id}
        if name is not None:
            hello["name"] = name
        send_message(self.sock, hello)
        self.snapshot = recv_message(self.sock)
        self.first_role = recv_message(self.sock)
        assert self.snapshot["type"] == "SNAPSHOT" and self.first_role["type"] == "ROLE_UPDATE"

    def send(self, message):
        send_message(self.sock, message)

    def collect(self, seconds=0.5):
        out, end = [], time.time() + seconds
        while (left := end - time.time()) > 0:
            self.sock.settimeout(left)
            try:
                out.append(recv_message(self.sock))
            except socket.timeout:
                break
        return out

    def wait_for(self, msg_type, seconds=2):
        """Read until a message of msg_type arrives; returns (it, skipped)."""
        skipped, end = [], time.time() + seconds
        while time.time() < end:
            self.sock.settimeout(max(0.01, end - time.time()))
            message = recv_message(self.sock)
            if message["type"] == msg_type:
                return message, skipped
            skipped.append(message)
        raise AssertionError(f"no {msg_type}")

    def stroke(self, stroke_id, y, segments=3):
        for i in range(segments):
            self.send({"type": "DRAW", "tool": "pen", "x1": 10 + i * 10, "y1": y,
                       "x2": 20 + i * 10, "y2": y, "color": "#000000", "width": 3,
                       "stroke_id": stroke_id})

    def close(self, abrupt=False):
        if abrupt:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self.sock.close()


def of_type(messages, *types):
    return [m for m in messages if m["type"] in types]


def last_presence(messages):
    users = of_type(messages, "PRESENCE_UPDATE")
    assert users, "no PRESENCE_UPDATE"
    return {u["client_id"]: u for u in users[-1]["users"]}


class TestPresenceUndo(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.log = tempfile.TemporaryFile(mode="w+")
        cls.proc = start_server("", cls.log)

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
        time.sleep(0.3)

    def connect(self, client_id=None, name=None):
        c = Client(client_id, name)
        self.clients.append(c)
        return c

    def version(self):
        return self.connect().snapshot["version"]

    def admin(self, c):
        c.send({"type": "ADMIN_REQUEST"})
        role, _ = c.wait_for("ROLE_UPDATE")
        self.assertEqual(role["role"], "admin")
        c.collect(0.3)

    # ---------- Part A: presence ----------

    def test_01_presence_join_leave_names(self):
        a = self.connect(name="  Alice  ")
        b = self.connect(name="Bob\x07")           # not printable -> client ID
        c = self.connect()                          # old client: no name
        d = self.connect(name="x" * 30)             # trimmed to 20 chars

        users = last_presence(a.collect())
        self.assertEqual(set(users), {a.id, b.id, c.id, d.id})
        self.assertEqual(users[a.id]["name"], "Alice")
        self.assertEqual(users[b.id]["name"], b.id)
        self.assertEqual(users[c.id]["name"], c.id)
        self.assertEqual(users[d.id]["name"], "x" * 20)
        self.assertTrue(all(u["color"] in PALETTE and u["role"] == "viewer" for u in users.values()))

        # Every client agrees on the list (and colours are stable).
        for other in (b, c, d):
            self.assertEqual(last_presence(other.collect()), users)

        # Abrupt disconnect -> removed for everyone.
        c.close(abrupt=True)
        self.clients.remove(c)
        for other in (a, b, d):
            self.assertNotIn(c.id, last_presence(other.collect(1)))

        # Role change -> presence shows the admin.
        a.send({"type": "ADMIN_REQUEST"})
        for other in (a, b, d):
            self.assertEqual(last_presence(other.collect())[a.id]["role"], "admin")

    def test_02_cursor_relay_not_versioned(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        v = self.version()
        for x in (a, b, c):
            x.collect(0.3)  # let join presence broadcasts finish

        a.send({"type": "CURSOR", "x": 120, "y": 340})
        for other in (b, c):
            updates = of_type(other.collect(), "CURSOR_UPDATE")
            self.assertEqual(updates, [{"type": "CURSOR_UPDATE", "client_id": a.id,
                                        "x": 120, "y": 340, "hidden": False}])
            self.assertNotIn("version", updates[0])
        self.assertEqual(of_type(a.collect(), "CURSOR_UPDATE"), [])  # not echoed

        # Invalid coordinates are ignored silently.
        time.sleep(0.05)
        a.send({"type": "CURSOR", "x": 20000, "y": 5})
        self.assertEqual(of_type(b.collect(), "CURSOR_UPDATE", "ERROR"), [])
        self.assertEqual(of_type(a.collect(), "ERROR"), [])

        time.sleep(0.05)
        a.send({"type": "CURSOR", "hidden": True})
        self.assertTrue(of_type(b.collect(), "CURSOR_UPDATE")[0]["hidden"])

        # Never versioned, never stored.
        late = self.connect()
        self.assertEqual(late.snapshot["version"], v)
        self.assertTrue(all(op["type"] == "DRAW_UPDATE" for op in late.snapshot["operations"]))

    def test_03_cursor_flood_rate_limited_and_draw_not_delayed(self):
        a, b = self.connect(), self.connect()
        self.admin(a)
        b.collect(0.3)

        for i in range(200):
            a.send({"type": "CURSOR", "x": i, "y": i})
        sent = time.perf_counter()
        a.stroke("flood1", 50, segments=1)

        draw, skipped = b.wait_for("DRAW_UPDATE")
        latency = time.perf_counter() - sent
        relayed = len(of_type(skipped + b.collect(), "CURSOR_UPDATE"))
        print(f"\n    flood: {relayed}/200 cursors relayed, DRAW latency {latency * 1000:.1f} ms")
        self.assertLess(relayed, 30)
        self.assertLess(latency, 0.25)

    # ---------- Part B: undo / clear ----------

    def test_04_undo_removes_whole_last_stroke(self):
        a, b = self.connect(), self.connect()
        self.admin(a)
        b.collect(0.3)
        v = self.version()

        a.stroke("s1", 100)
        a.stroke("s2", 200)
        a.send({"type": "UNDO_REQUEST"})

        for c in (a, b):
            msgs = of_type(c.collect(), *VERSIONED)
            self.assertEqual([m["version"] for m in msgs], list(range(v + 1, v + 8)))
            self.assertEqual(msgs[-1], {"type": "UNDO_UPDATE", "version": v + 7, "stroke_id": "s2"})

        snap = self.connect().snapshot
        self.assertEqual(snap["version"], v + 7)
        mine = [op for op in snap["operations"] if op["stroke_id"] in ("s1", "s2")]
        self.assertEqual([op["stroke_id"] for op in mine], ["s1"] * 3)

    def test_05_viewer_undo_and_clear_rejected(self):
        a, b = self.connect(), self.connect()
        self.admin(a)
        b.collect(0.3)
        a.stroke("keep", 100)
        a.collect(), b.collect()
        v = self.version()

        b.send({"type": "UNDO_REQUEST"})
        b.send({"type": "CLEAR_REQUEST"})
        errors = [m["message"] for m in of_type(b.collect(), "ERROR")]
        self.assertEqual(errors, ["View-only: only the admin can undo",
                                  "View-only: only the admin can clear the board"])
        self.assertEqual(of_type(a.collect(), *VERSIONED), [])
        self.assertEqual(self.version(), v)

    def test_06_undo_empty_board(self):
        a = self.connect()
        self.admin(a)
        a.send({"type": "CLEAR_REQUEST"})
        a.wait_for("CLEAR_UPDATE")
        v = self.version()

        a.send({"type": "UNDO_REQUEST"})
        self.assertEqual(of_type(a.collect(), "ERROR")[0]["message"], "Nothing to undo")
        self.assertEqual(self.version(), v)

    def test_07_clear(self):
        a, b = self.connect(name="Alice"), self.connect()
        self.admin(a)
        b.collect(0.3)
        a.stroke("c1", 100)
        a.collect(), b.collect()
        v = self.version()

        a.send({"type": "CLEAR_REQUEST"})
        update, _ = b.wait_for("CLEAR_UPDATE")
        self.assertEqual(update, {"type": "CLEAR_UPDATE", "version": v + 1, "by": "Alice"})

        snap = self.connect().snapshot
        self.assertEqual((snap["version"], snap["operations"]), (v + 1, []))

        b.send({"type": "SUMMARY_REQUEST"})
        reply, _ = b.wait_for("SUMMARY_RESPONSE")
        self.assertEqual(reply["error"], "Board is empty, nothing to summarize.")

    def test_08_mixed_sequence_no_gaps(self):
        a, b, c = self.connect(), self.connect(), self.connect()
        self.admin(a)
        for x in (b, c):
            x.collect(0.3)
        v = self.version()

        a.stroke("m1", 10, 1)
        a.stroke("m2", 20, 1)
        a.send({"type": "UNDO_REQUEST"})
        a.stroke("m3", 30, 1)
        a.send({"type": "CLEAR_REQUEST"})
        a.stroke("m4", 40, 1)

        for x in (a, b, c):
            msgs = of_type(x.collect(), *VERSIONED)
            self.assertEqual([m["type"] for m in msgs],
                             ["DRAW_UPDATE", "DRAW_UPDATE", "UNDO_UPDATE",
                              "DRAW_UPDATE", "CLEAR_UPDATE", "DRAW_UPDATE"])
            self.assertEqual([m["version"] for m in msgs], list(range(v + 1, v + 7)))

    def test_09_missed_undo_recovered_with_sync(self):
        a, b = self.connect(), self.connect()
        self.admin(a)
        b.collect(0.3)
        last = self.version()

        a.stroke("g1", 10, 1)
        a.send({"type": "UNDO_REQUEST"})
        a.stroke("g2", 20, 1)

        # Mimic the client's version check, but "lose" the UNDO_UPDATE.
        gap = False
        for m in of_type(b.collect(), *VERSIONED):
            if m["type"] == "UNDO_UPDATE":
                continue  # simulated packet loss
            if m["version"] == last + 1:
                last = m["version"]
            elif m["version"] > last + 1:
                gap = True
                b.send({"type": "SYNC_REQUEST"})
                break
        self.assertTrue(gap, "gap not detected")

        snap, _ = b.wait_for("SNAPSHOT")
        ids = [op["stroke_id"] for op in snap["operations"]]
        self.assertEqual(snap["version"], self.version())
        self.assertNotIn("g1", ids)
        self.assertIn("g2", ids)

    def test_10_legacy_and_invalid_stroke_ids(self):
        a = self.connect()
        self.admin(a)
        v = self.version()

        a.send({"type": "DRAW", "x1": 1, "y1": 1, "x2": 5, "y2": 5, "color": "#000000"})
        update, _ = a.wait_for("DRAW_UPDATE")
        self.assertEqual(update["stroke_id"], f"legacy_{v + 1}")

        a.send({"type": "DRAW", "x1": 1, "y1": 1, "x2": 5, "y2": 5, "color": "#000000",
                "stroke_id": "bad id!"})
        self.assertEqual(of_type(a.collect(), "ERROR")[0]["message"], "Invalid stroke_id")
        self.assertEqual(self.version(), v + 1)

        a.send({"type": "UNDO_REQUEST"})
        undo, _ = a.wait_for("UNDO_UPDATE")
        self.assertEqual(undo["stroke_id"], f"legacy_{v + 1}")


if __name__ == "__main__":
    unittest.main()
