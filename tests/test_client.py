
import socket

from common.protocol import send_message, recv_message

HOST = "127.0.0.1"
PORT = 8765


def main():
    with socket.create_connection((HOST, PORT)) as sock:
        send_message(sock, {
            "type": "HELLO",
            "client_id": "test_client_1",
        })

        response = recv_message(sock)
        print("Server response:", response)

        send_message(sock, {"type": "PING"})

        response = recv_message(sock)
        print("Ping response:", response)


if __name__ == "__main__":
    main()
