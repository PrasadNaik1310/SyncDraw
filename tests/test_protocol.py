
import json
import socket
import struct
import unittest

from common.protocol import (
    send_message,
    recv_message,
)


class TestProtocol(unittest.TestCase):

    def setUp(self):
        self.sender, self.receiver = socket.socketpair()

    def tearDown(self):
        self.sender.close()
        self.receiver.close()

    def test_single_message(self):
        message = {"type": "HELLO", "client_id": "client_A"}

        send_message(self.sender, message)

        self.assertEqual(
            recv_message(self.receiver),
            message,
        )

    def test_multiple_messages(self):
        messages = [
            {"type": "DRAW", "x1": 10, "y1": 20},
            {"type": "DRAW", "x1": 30, "y1": 40},
        ]

        for message in messages:
            send_message(self.sender, message)

        for message in messages:
            self.assertEqual(
                recv_message(self.receiver),
                message,
            )

    def test_rejects_non_object_json(self):
        payload = json.dumps([1, 2, 3]).encode()
        frame = struct.pack("!I", len(payload)) + payload

        self.sender.sendall(frame)

        with self.assertRaises(ValueError):
            recv_message(self.receiver)


if __name__ == "__main__":
    unittest.main()
