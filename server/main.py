
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

state_lock = threading.Lock()
clients = {}  # socket -> client ID
drawing_state = []
version = 0

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
    # broadcast() runs under state_lock, so holding it here serializes
    # writes to the socket. Only a local send; never a Gemini call.
    with state_lock:
        if conn not in clients:
            return False

        try:
            send_message(conn, message)
            return True
        except (OSError, ConnectionError, ValueError):
            return False


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
    """Send an update to every connected client."""
    disconnected = []

    for conn in list(clients):
        try:
            send_message(conn, message)
        except (OSError, ConnectionError, ValueError):
            disconnected.append(conn)

    for conn in disconnected:
        clients.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass


def role_message(conn):
    """ROLE_UPDATE personalised for one client. Caller holds state_lock."""
    return {
        "type": "ROLE_UPDATE",
        "role": "admin" if conn is admin_conn else "viewer",
        "admin_id": clients.get(admin_conn) if admin_conn is not None else None,
        "pin_required": bool(ADMIN_PIN),
    }


def broadcast_roles():
    """Send every client its own ROLE_UPDATE. Caller holds state_lock."""
    disconnected = []

    for conn in list(clients):
        try:
            send_message(conn, role_message(conn))
        except (OSError, ConnectionError, ValueError):
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
            error = f"Admin already taken by {clients.get(admin_conn, 'another client')}"
        else:
            error = None
            admin_conn = conn
            broadcast_roles()

    if error:
        print(f"Admin request from {client_id} refused: {error}")
        send_to_client(conn, {"type": "ERROR", "message": error})
    else:
        print(f"{client_id} is now admin")


def handle_admin_release(conn, client_id):
    global admin_conn

    with state_lock:
        released = admin_conn is conn

        if released:
            admin_conn = None
            broadcast_roles()

    if released:
        print(f"{client_id} released admin")
    else:
        send_to_client(conn, {
            "type": "ERROR",
            "message": "You are not the admin",
        })


def handle_client(conn, address):
    global version, admin_conn
    client_id = None

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

        with state_lock:
            clients[conn] = client_id

            # Send the current drawing to the new client.
            send_message(conn, {
                "type": "SNAPSHOT",
                "version": version,
                "operations": list(drawing_state),
            })

            # New connections always start as viewer.
            send_message(conn, role_message(conn))

        print(f"{client_id} connected from {address}")

        while True:
            message = recv_message(conn)

            # Handle a request to synchronize drawing state.
            if message.get("type") == "SYNC_REQUEST":
                with state_lock:
                    send_message(conn, {
                        "type": "SNAPSHOT",
                        "version": version,
                        "operations": list(drawing_state),
                    })
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
                send_message(conn, {
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

            except (KeyError, TypeError, ValueError) as exc:
                send_message(conn, {
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
