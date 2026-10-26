
import hashlib
import hmac
import os
import socket
import threading
import time
from pathlib import Path

from common.protocol import send_message, recv_message

# Load the project-root .env here too, so ADMIN_PIN works even
# when the summary packages are missing.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# Summaries are optional: drawing still works without these packages.
try:
    from server import summarizer
except ImportError as exc:
    summarizer = None
    print(f"Board summary disabled (missing package: {exc.name})")

HOST = "0.0.0.0"
PORT = 8765

# Eraser ops are stored with the canvas background colour.
BACKGROUND_COLOR = "#FFFFFF"
VALID_TOOLS = ("pen", "eraser")

# ----------------------------------------------------------------------
# Two kinds of traffic, kept strictly separate:
#
# 1. Persistent, ordered BOARD STATE: DRAW, UNDO, CLEAR.
#    Each accepted op changes drawing_state and gets the next version
#    number under state_lock, and is broadcast (still under state_lock)
#    so every client receives versions in order. It is part of every
#    SNAPSHOT, and clients apply it only through their version-gap check
#    (a gap -> SYNC_REQUEST). Rejected requests never consume a version.
#
# 2. Temporary PRESENCE data: CURSOR/CURSOR_UPDATE and PRESENCE_UPDATE.
#    No version, never stored in drawing_state or snapshots, rate limited
#    and safe to drop. It is sent WITHOUT state_lock (only the per-socket
#    send lock), so it can never block or reorder board updates, and
#    clients never run the version check on it.
# ----------------------------------------------------------------------

state_lock = threading.Lock()
clients = {}  # socket -> ClientInfo (guarded by state_lock)
drawing_state = []
version = 0

NAME_MAX = 20
STROKE_ID_MAX = 64
STROKE_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
CURSOR_MIN_INTERVAL = 1 / 30  # drop cursors faster than ~30/s
CURSOR_SEND_WAIT = 0.02  # max wait for a busy socket before dropping
USER_COLORS = [
    "#E11D48", "#EA580C", "#CA8A04", "#16A34A", "#0D9488",
    "#0284C7", "#4F46E5", "#9333EA", "#DB2777", "#65A30D",
]


def user_color(client_id):
    """Stable colour from a hash of the client ID (same on every run)."""
    digest = hashlib.md5(client_id.encode("utf-8")).digest()
    return USER_COLORS[digest[0] % len(USER_COLORS)]


def clean_name(raw, client_id):
    """Display name: stripped, printable, max 20 chars; else client ID."""
    if not isinstance(raw, str):
        return client_id

    name = raw.strip()[:NAME_MAX].strip()

    if not name or not name.isprintable():
        return client_id
    return name


class ClientInfo:
    """Per-connection data. The name is for display only, not identity."""

    def __init__(self, client_id, name):
        self.client_id = client_id
        self.name = name
        self.color = user_color(client_id)
        self.send_lock = threading.Lock()  # one writer per socket
        self.last_cursor = 0.0  # only touched by this client's thread


def safe_send(conn, info, message, wait=-1):
    """Send under the socket's own lock, so sends never interleave.

    wait=-1 blocks; a small wait is for droppable presence data
    (give up and drop if the socket stays busy that long).
    """
    if not info.send_lock.acquire(timeout=wait):
        return False

    try:
        send_message(conn, message)
        return True
    except (OSError, ConnectionError, ValueError):
        return False
    finally:
        info.send_lock.release()

# Role is bound to the connection (socket), never to the client ID,
# which the client chooses itself. Guarded by state_lock.
admin_conn = None
ADMIN_PIN = os.environ.get("ADMIN_PIN", "").strip()  # empty = no PIN
VIEW_ONLY_ERROR = {
    "type": "ERROR",
    "message": "View-only: only the admin can draw",
}

SUMMARY_COOLDOWN = 15  # seconds between summaries per client
cooldown_lock = threading.Lock()
last_summary_time = {}  # client ID -> time of last accepted request


def send_to_client(conn, message):
    """Send to one client without interleaving with a broadcast."""
    # Look up under state_lock, send under the per-socket lock only.
    with state_lock:
        info = clients.get(conn)

    if info is None:
        return False
    return safe_send(conn, info, message)


def send_summary_error(conn, client_id, error):
    print(f"Summary for {client_id} failed: {error}")
    send_to_client(conn, {
        "type": "SUMMARY_RESPONSE",
        "ok": False,
        "error": error,
    })


def handle_summary_request(conn, client_id):
    """Copy the board under the lock, then summarize in a worker thread."""
    with state_lock:
        ops = list(drawing_state)
        board_version = version

    print(f"Summary requested by {client_id} at version {board_version}")

    # An empty board does not start the cooldown.
    if not any(op.get("tool", "pen") != "eraser" for op in ops):
        send_summary_error(conn, client_id, "Board is empty, nothing to summarize.")
        return

    now = time.monotonic()

    with cooldown_lock:
        last = last_summary_time.get(client_id)

        if last is not None and now - last < SUMMARY_COOLDOWN:
            wait = int(SUMMARY_COOLDOWN - (now - last)) + 1
            send_summary_error(conn, client_id, f"Please wait {wait} seconds.")
            return

        last_summary_time[client_id] = now

    threading.Thread(
        target=summary_worker,
        args=(conn, client_id, ops, board_version),
        daemon=True,
    ).start()


def summary_worker(conn, client_id, ops, board_version):
    """Render the board and call Gemini without holding any lock."""
    if summarizer is None:
        send_summary_error(conn, client_id, "Summary feature not installed on server.")
        return

    try:
        png = summarizer.render_board(ops)

        if png is None:
            send_summary_error(conn, client_id, "Board is empty, nothing to summarize.")
            return

        summary = summarizer.summarize_image(png)

    except summarizer.SummaryError as exc:
        send_summary_error(conn, client_id, str(exc))
        return

    except Exception as exc:
        print(f"Unexpected summary error: {type(exc).__name__}: {exc}")
        send_summary_error(conn, client_id, "Summary failed on server.")
        return

    print(f"Summary for {client_id} done (version {board_version})")
    send_to_client(conn, {
        "type": "SUMMARY_RESPONSE",
        "ok": True,
        "summary": summary,
        "version": board_version,
    })


def broadcast(message):
    """Send a board update to every client. Caller holds state_lock,
    which keeps versioned updates in order for every client."""
    disconnected = []

    for conn, info in list(clients.items()):
        if not safe_send(conn, info, message):
            disconnected.append(conn)

    for conn in disconnected:
        clients.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass


def admin_client_id():
    """Client ID of the current admin, or None. Caller holds state_lock."""
    info = clients.get(admin_conn) if admin_conn is not None else None
    return info.client_id if info else None


def role_message(conn):
    """ROLE_UPDATE personalised for one client. Caller holds state_lock."""
    return {
        "type": "ROLE_UPDATE",
        "role": "admin" if conn is admin_conn else "viewer",
        "admin_id": admin_client_id(),
        "pin_required": bool(ADMIN_PIN),
    }


def broadcast_roles():
    """Send every client its own ROLE_UPDATE. Caller holds state_lock."""
    disconnected = []

    for conn, info in list(clients.items()):
        if not safe_send(conn, info, role_message(conn)):
            disconnected.append(conn)

    for conn in disconnected:
        clients.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass


def handle_admin_request(conn, client_id, message):
    global admin_conn

    # Check the optional shared PIN. Never log the PIN itself.
    if ADMIN_PIN:
        pin = message.get("pin")

        if not isinstance(pin, str) or not hmac.compare_digest(
            pin.encode("utf-8"), ADMIN_PIN.encode("utf-8")
        ):
            print(f"Admin request from {client_id} refused: wrong PIN")
            send_to_client(conn, {
                "type": "ERROR",
                "message": "Wrong admin PIN",
            })
            return

    # Check and take the slot atomically, so only one request can win.
    with state_lock:
        if admin_conn is not None and admin_conn is not conn:
            error = f"Admin already taken by {admin_client_id() or 'another client'}"
        else:
            error = None
            admin_conn = conn
            broadcast_roles()

    if error:
        print(f"Admin request from {client_id} refused: {error}")
        send_to_client(conn, {"type": "ERROR", "message": error})
    else:
        print(f"{client_id} is now admin")
        broadcast_presence()  # role shown in the online list


def handle_admin_release(conn, client_id):
    global admin_conn

    with state_lock:
        released = admin_conn is conn

        if released:
            admin_conn = None
            broadcast_roles()

    if released:
        print(f"{client_id} released admin")
        broadcast_presence()
    else:
        send_to_client(conn, {
            "type": "ERROR",
            "message": "You are not the admin",
        })


# ---------- presence (temporary, unversioned) ----------

def broadcast_presence():
    """Send the online-users list to everyone.

    Built under state_lock, sent AFTER releasing it (per-socket lock only).
    """
    with state_lock:
        message = {
            "type": "PRESENCE_UPDATE",
            "users": [
                {
                    "client_id": info.client_id,
                    "name": info.name,
                    "role": "admin" if conn is admin_conn else "viewer",
                    "color": info.color,
                }
                for conn, info in clients.items()
            ],
        }
        targets = list(clients.items())

    for conn, info in targets:
        safe_send(conn, info, message)  # failures: reader thread cleans up


def handle_cursor(conn, info, message):
    """Relay a cursor to all OTHER clients. Best effort: may be dropped."""
    now = time.monotonic()

    # Rate limit per connection; extra messages are dropped silently.
    if now - info.last_cursor < CURSOR_MIN_INTERVAL:
        return
    info.last_cursor = now

    if message.get("hidden") is True:
        update = {
            "type": "CURSOR_UPDATE",
            "client_id": info.client_id,
            "hidden": True,
        }
    else:
        x, y = message.get("x"), message.get("y")

        # Same range check as DRAW; invalid cursors are ignored silently.
        if (
            type(x) not in (int, float)
            or type(y) not in (int, float)
            or not (0 <= x <= 10000 and 0 <= y <= 10000)
        ):
            return

        update = {
            "type": "CURSOR_UPDATE",
            "client_id": info.client_id,
            "x": x,
            "y": y,
            "hidden": False,
        }

    with state_lock:
        targets = [(c, i) for c, i in clients.items() if c is not conn]

    # No state_lock here, and drop for sockets that stay busy, so
    # cursor traffic can never block board updates for long.
    for c, i in targets:
        safe_send(c, i, update, wait=CURSOR_SEND_WAIT)


# ---------- undo / clear (persistent, versioned, admin only) ----------

def handle_undo(conn, info):
    global version

    with state_lock:
        if admin_conn is not conn:
            error = "View-only: only the admin can undo"
        elif not drawing_state:
            error = "Nothing to undo"  # no version consumed
        else:
            error = None

            # Remove every segment of the most recent stroke on the board,
            # whoever drew it (the admin can change over time).
            stroke_id = drawing_state[-1].get("stroke_id")
            drawing_state[:] = [
                op for op in drawing_state if op.get("stroke_id") != stroke_id
            ]
            version += 1
            broadcast({
                "type": "UNDO_UPDATE",
                "version": version,
                "stroke_id": stroke_id,
            })

    if error:
        send_to_client(conn, {"type": "ERROR", "message": error})
    else:
        print(f"{info.client_id} undid stroke {stroke_id}")


def handle_clear(conn, info):
    global version

    with state_lock:
        allowed = admin_conn is conn

        if allowed:
            drawing_state.clear()
            version += 1
            broadcast({
                "type": "CLEAR_UPDATE",
                "version": version,
                "by": info.name,
            })

    if allowed:
        print(f"{info.client_id} cleared the board")
    else:
        send_to_client(conn, {
            "type": "ERROR",
            "message": "View-only: only the admin can clear the board",
        })


def handle_client(conn, address):
    global version, admin_conn
    client_id = None
    info = None

    try:
        hello = recv_message(conn)

        if hello.get("type") != "HELLO":
            send_message(conn, {
                "type": "ERROR",
                "message": "Expected HELLO",
            })
            return

        client_id = hello.get("client_id")

        if not isinstance(client_id, str) or not client_id:
            send_message(conn, {
                "type": "ERROR",
                "message": "Invalid client ID",
            })
            return

        # Optional display name (old clients don't send one).
        info = ClientInfo(client_id, clean_name(hello.get("name"), client_id))

        with state_lock:
            clients[conn] = info

            with info.send_lock:
                # Send the current drawing to the new client.
                send_message(conn, {
                    "type": "SNAPSHOT",
                    "version": version,
                    "operations": list(drawing_state),
                })

                # New connections always start as viewer.
                send_message(conn, role_message(conn))

        print(f"{client_id} ({info.name}) connected from {address}")
        broadcast_presence()

        while True:
            message = recv_message(conn)

            # Live cursor: presence data, no version, no state_lock.
            if message.get("type") == "CURSOR":
                handle_cursor(conn, info, message)
                continue

            # Handle a request to synchronize drawing state.
            if message.get("type") == "SYNC_REQUEST":
                with state_lock:
                    with info.send_lock:
                        send_message(conn, {
                            "type": "SNAPSHOT",
                            "version": version,
                            "operations": list(drawing_state),
                        })
                continue

            # Versioned board operations (admin only, checked inside).
            if message.get("type") == "UNDO_REQUEST":
                handle_undo(conn, info)
                continue

            if message.get("type") == "CLEAR_REQUEST":
                handle_clear(conn, info)
                continue

            # Board summary: reply goes only to this client.
            if message.get("type") == "SUMMARY_REQUEST":
                handle_summary_request(conn, client_id)
                continue

            # Admin role requests (open to everyone).
            if message.get("type") == "ADMIN_REQUEST":
                handle_admin_request(conn, client_id, message)
                continue

            if message.get("type") == "ADMIN_RELEASE":
                handle_admin_release(conn, client_id)
                continue

            if message.get("type") != "DRAW":
                send_to_client(conn, {
                    "type": "ERROR",
                    "message": "Unsupported message type",
                })
                continue

            # Only the admin may change the board. The server enforces
            # this; disabled client buttons are only for convenience.
            with state_lock:
                is_admin = admin_conn is conn

            if not is_admin:
                send_to_client(conn, VIEW_ONLY_ERROR)
                continue

            # Validate the drawing operation.
            try:
                x1 = float(message["x1"])
                y1 = float(message["y1"])
                x2 = float(message["x2"])
                y2 = float(message["y2"])
                width = message.get("width", 3)

                # Missing "tool" means pen (backward compatible).
                tool = message.get("tool", "pen")

                if tool not in VALID_TOOLS:
                    raise ValueError("Invalid tool")

                if not all(
                    0 <= n <= 10000 for n in (x1, y1, x2, y2)
                ):
                    raise ValueError("Coordinates out of range")

                # Never trust a client colour for the eraser.
                if tool == "eraser":
                    color = BACKGROUND_COLOR
                    max_width = 100
                else:
                    color = message["color"]
                    max_width = 20

                if (
                    not isinstance(color, str)
                    or len(color) != 7
                    or color[0] != "#"
                    or any(
                        c not in "0123456789abcdefABCDEF"
                        for c in color[1:]
                    )
                ):
                    raise ValueError("Invalid color")

                if (
                    type(width) not in (int, float)
                    or not 1 <= width <= max_width
                ):
                    raise ValueError("Invalid line width")

                # Optional stroke ID groups segments for undo.
                stroke_id = message.get("stroke_id")

                if stroke_id is not None and (
                    not isinstance(stroke_id, str)
                    or not 1 <= len(stroke_id) <= STROKE_ID_MAX
                    or any(c not in STROKE_ID_CHARS for c in stroke_id)
                ):
                    raise ValueError("Invalid stroke_id")

            except (KeyError, TypeError, ValueError) as exc:
                send_to_client(conn, {
                    "type": "ERROR",
                    "message": str(exc),
                })
                continue

            with state_lock:
                # Re-check under the lock that assigns the version, in case
                # admin was released meanwhile. Rejected ops get no version.
                allowed = admin_conn is conn

                if allowed:
                    version += 1

                    operation = {
                        "type": "DRAW_UPDATE",
                        "version": version,
                        "client_id": client_id,
                        "tool": tool,
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "color": color,
                        "width": width,
                        # Old clients send no stroke ID: each segment
                        # then counts as its own stroke for undo.
                        "stroke_id": stroke_id or f"legacy_{version}",
                    }

                    drawing_state.append(operation)
                    broadcast(operation)

            if not allowed:
                send_to_client(conn, VIEW_ONLY_ERROR)

    except (OSError, ConnectionError, ValueError) as exc:
        print(f"Connection ended for {client_id or address}: {exc}")

    finally:
        with state_lock:
            clients.pop(conn, None)

            # Free the admin slot when the admin's connection closes.
            if admin_conn is conn:
                admin_conn = None
                broadcast_roles()
                print(f"{client_id} lost admin (disconnected)")

        conn.close()
        print(f"{client_id or address} disconnected")

        # Tell everyone who is still online (sent outside state_lock).
        if info is not None:
            broadcast_presence()


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen()

        print(f"SyncDraw server listening on {HOST}:{PORT}")
        print(f"Admin PIN: {'required' if ADMIN_PIN else 'not set (one-click admin)'}")

        while True:
            conn, address = server.accept()

            threading.Thread(
                target=handle_client,
                args=(conn, address),
                daemon=True,
            ).start()


if __name__ == "__main__":
    main()
