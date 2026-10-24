
"""Headless eraser test. Start the server first: python -m server.main"""

import os
import socket

from common.protocol import send_message, recv_message

HOST = "127.0.0.1"
PORT = 8765
BACKGROUND_COLOR = "#FFFFFF"


def connect(client_id):
    sock = socket.create_connection((HOST, PORT), timeout=5)
    send_message(sock, {"type": "HELLO", "client_id": client_id})
    snapshot = recv_message(sock)
    assert snapshot["type"] == "SNAPSHOT", snapshot
    role = recv_message(sock)  # every client starts as viewer
    assert role["type"] == "ROLE_UPDATE", role
    return sock, snapshot


def main():
    a, snap_a = connect("eraser_test_A")
    b, _ = connect("eraser_test_B")
    start = snap_a["version"]

    # Only the admin may draw. Set ADMIN_PIN in the shell if the server uses one.
    send_message(a, {"type": "ADMIN_REQUEST", "pin": os.environ.get("ADMIN_PIN", "")})
    for sock in (a, b):
        role = recv_message(sock)
        assert role["type"] == "ROLE_UPDATE" and role["admin_id"] == "eraser_test_A", role

    # Pen without "tool" (backward compatible).
    send_message(a, {
        "type": "DRAW",
        "x1": 10, "y1": 10, "x2": 200, "y2": 200,
        "color": "#FF0000", "width": 5,
    })

    # Eraser with a bogus colour the server must ignore.
    send_message(a, {
        "type": "DRAW", "tool": "eraser",
        "x1": 10, "y1": 200, "x2": 200, "y2": 10,
        "color": "#FF0000", "width": 30,
    })

    for name, sock in (("A", a), ("B", b)):
        pen = recv_message(sock)
        eraser = recv_message(sock)
        assert pen["type"] == eraser["type"] == "DRAW_UPDATE"
        assert pen["version"] == start + 1, pen
        assert eraser["version"] == start + 2, eraser
        assert pen["tool"] == "pen" and pen["color"] == "#FF0000", pen
        assert eraser["tool"] == "eraser", eraser
        assert eraser["color"] == BACKGROUND_COLOR, eraser
        assert eraser["width"] == 30, eraser
        print(f"client {name}: OK", pen["version"], eraser["version"])

    # Rejected: eraser too wide, unknown tool.
    send_message(a, {
        "type": "DRAW", "tool": "eraser",
        "x1": 1, "y1": 1, "x2": 2, "y2": 2, "width": 150,
    })
    error = recv_message(a)
    assert error["type"] == "ERROR", error
    print("width 150 rejected:", error["message"])

    send_message(a, {
        "type": "DRAW", "tool": "spray",
        "x1": 1, "y1": 1, "x2": 2, "y2": 2,
        "color": "#000000", "width": 3,
    })
    error = recv_message(a)
    assert error["type"] == "ERROR", error
    print("unknown tool rejected:", error["message"])

    # A late joiner's snapshot replays both ops in order.
    c, snap_c = connect("eraser_test_C")
    assert snap_c["version"] == start + 2, snap_c["version"]
    last_two = snap_c["operations"][-2:]
    assert [op["version"] for op in last_two] == [start + 1, start + 2]
    assert [op["tool"] for op in last_two] == ["pen", "eraser"]
    assert last_two[1]["color"] == BACKGROUND_COLOR
    print("client C snapshot: OK", [op["version"] for op in last_two])

    for sock in (a, b, c):
        sock.close()

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
