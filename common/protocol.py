
import json
import socket
import struct

HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 1_000_000


def recv_exact(sock: socket.socket, size: int) -> bytes:
    """Read exactly size bytes from a TCP connection."""
    chunks = []
    remaining = size

    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("Peer disconnected")
        chunks.append(chunk)
        remaining -= len(chunk)

    return b"".join(chunks)


def send_message(sock: socket.socket, message: dict) -> None:
    """Send a JSON message with a four-byte length prefix."""
    payload = json.dumps(
        message,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    if not payload or len(payload) > MAX_MESSAGE_SIZE:
        raise ValueError("Invalid message size")

    header = struct.pack("!I", len(payload))
    sock.sendall(header + payload)


def recv_message(sock: socket.socket) -> dict:
    """Receive one complete JSON message."""
    header = recv_exact(sock, HEADER_SIZE)
    (size,) = struct.unpack("!I", header)

    if size == 0 or size > MAX_MESSAGE_SIZE:
        raise ValueError(f"Invalid message length: {size}")

    payload = recv_exact(sock, size)
    message = json.loads(payload.decode("utf-8"))

    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")

    return message
