
import socket
import threading
import tkinter as tk
import queue
import uuid
from common.protocol import send_message, recv_message

HOST = "127.0.0.1"
PORT = 8765
CLIENT_ID = "client_A"


class SyncDrawClient:
    def __init__(self, root):
        self.root = root
        self.root.title("SyncDraw")
        self.root.geometry("900x600")

        self.color = "#2563EB"
        self.last_point = None
        self.sock = None
        self.send_lock = threading.Lock()
        self.messages = queue.Queue()
        self.running = True
        self.last_version = 0

        toolbar = tk.Frame(root)
        toolbar.pack(fill="x", padx=8, pady=8)

        for name, color in [
            ("Black", "#000000"),
            ("Red", "#FF0000"),
            ("Blue", "#2563EB"),
            ("Green", "#16A34A"),
        ]:
            tk.Button(
                toolbar,
                text=name,
                command=lambda c=color: self.set_color(c),
            ).pack(side="left", padx=4)

        self.status = tk.Label(toolbar, text="Connecting...", fg="orange")
        self.status.pack(side="right", padx=8)

        self.canvas = tk.Canvas(root, bg="white", cursor="cross")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)

        self.canvas.bind("<Button-1>", self.start_draw)
        self.canvas.bind("<B1-Motion>", self.draw)
        self.canvas.bind("<ButtonRelease-1>", self.stop_draw)

        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.connect()
        self.root.after(50, self.process_messages)

    def set_color(self, color):
        self.color = color

    def connect(self):
        try:
            self.sock = socket.create_connection((HOST, PORT), timeout=5)
            self.sock.settimeout(None)

            send_message(self.sock, {
                "type": "HELLO",
                "client_id": CLIENT_ID,
                "last_version": self.last_version,
            })

            response = recv_message(self.sock)

            if response.get("type") != "SNAPSHOT":
                raise ConnectionError("Expected initial snapshot")

            self.last_version = response["version"]

            for operation in response["operations"]:
                self.render_operation(operation)

            self.status.config(text="Connected", fg="green")

            threading.Thread(
                target=self.receive_loop,
                daemon=True,
            ).start()

        except (OSError, ConnectionError, ValueError) as exc:
            self.status.config(text="Connection failed", fg="red")
            print("Connection error:", exc)

    def receive_loop(self):
        while self.running and self.sock:
            try:
                message = recv_message(self.sock)
                self.messages.put(message)
            except (OSError, ConnectionError, ValueError) as exc:
                if self.running:
                    self.messages.put({
                        "type": "CONNECTION_LOST",
                        "message": str(exc),
                    })
                break

    def process_messages(self):
        if not self.running:
            return

        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break

            msg_type = message.get("type")

            if msg_type == "DRAW_UPDATE":
                incoming_version = message["version"]

                if incoming_version == self.last_version + 1:
                    self.render_operation(message)
                    self.last_version = incoming_version
                elif incoming_version <= self.last_version:
                    # Ignore duplicate or stale updates.
                    continue
                else:
                    # We'll implement gap recovery next.
                    self.status.config(text="Version gap detected", fg="orange")

            elif msg_type == "SNAPSHOT":
                self.canvas.delete("all")

                for operation in message["operations"]:
                    self.render_operation(operation)

                self.last_version = message["version"]

            elif msg_type == "CONNECTION_LOST":
                self.status.config(text="Disconnected", fg="red")
                print(message["message"])

            elif msg_type == "ERROR":
                print("Server error:", message.get("message"))

        self.root.after(50, self.process_messages)

    def render_operation(self, operation):
        if operation.get("type") not in ("DRAW", "DRAW_UPDATE"):
            return

        self.canvas.create_line(
            operation["x1"],
            operation["y1"],
            operation["x2"],
            operation["y2"],
            fill=operation["color"],
            width=operation.get("width", 3),
            capstyle=tk.ROUND,
        )

    def start_draw(self, event):
        self.last_point = (event.x, event.y)

    def draw(self, event):
        if self.last_point is None:
            return

        x1, y1 = self.last_point
        x2, y2 = event.x, event.y

        # Render immediately for responsive drawing.
        self.canvas.create_line(
            x1, y1, x2, y2,
            fill=self.color,
            width=3,
            capstyle=tk.ROUND,
        )

        try:
            with self.send_lock:
                send_message(self.sock, {
                    "type": "DRAW",
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "color": self.color,
                    "width": 3,
                })
        except (OSError, ConnectionError, AttributeError) as exc:
            self.status.config(text="Send failed", fg="red")
            print("Send error:", exc)

        self.last_point = (x2, y2)

    def stop_draw(self, event):
        self.last_point = None

    def close(self):
        self.running = False

        if self.sock:
            self.sock.close()

        self.root.destroy()


def main():
    root = tk.Tk()
    SyncDrawClient(root)
    root.mainloop()


if __name__ == "__main__":
    main()
