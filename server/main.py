
import socket
import threading

from common.protocol import send_message, recv_message

HOST = "0.0.0.0"
PORT = 8765

state_lock = threading.Lock()
clients = {}  # socket -> client ID
drawing_state = []
version = 0


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


def handle_client(conn, address):
    global version
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

            if message.get("type") != "DRAW":
                send_message(conn, {
                    "type": "ERROR",
                    "message": "Unsupported message type",
                })
                continue

            # Validate the drawing operation.
            try:
                x1 = float(message["x1"])
                y1 = float(message["y1"])
                x2 = float(message["x2"])
                y2 = float(message["y2"])
                color = message["color"]
                width = message.get("width", 3)

                if not all(
                    0 <= n <= 10000 for n in (x1, y1, x2, y2)
                ):
                    raise ValueError("Coordinates out of range")

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
                    or not 1 <= width <= 20
                ):
                    raise ValueError("Invalid line width")

            except (KeyError, TypeError, ValueError) as exc:
                send_message(conn, {
                    "type": "ERROR",
                    "message": str(exc),
                })
                continue

            with state_lock:
                version += 1

                operation = {
                    "type": "DRAW_UPDATE",
                    "version": version,
                    "client_id": client_id,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "color": color,
                    "width": width,
                }

                drawing_state.append(operation)
                broadcast(operation)

    except (OSError, ConnectionError, ValueError) as exc:
        print(f"Connection ended for {client_id or address}: {exc}")

    finally:
        with state_lock:
            clients.pop(conn, None)

        conn.close()
        print(f"{client_id or address} disconnected")


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen()

        print(f"SyncDraw server listening on {HOST}:{PORT}")

        while True:
            conn, address = server.accept()

            threading.Thread(
                target=handle_client,
                args=(conn, address),
                daemon=True,
            ).start()


if __name__ == "__main__":
    main()
