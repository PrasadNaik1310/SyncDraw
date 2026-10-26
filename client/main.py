
import socket
import threading
import tkinter as tk
import queue
import uuid
import time

import customtkinter as ctk

from common.protocol import send_message, recv_message
from client.theme import THEME
from client.ui import SyncDrawUI, ask_confirm, ask_name, ask_pin

HOST = "100.48.93.224"
PORT = 8765
CLIENT_ID = f"client_{uuid.uuid4().hex[:8]}"

# Eraser strokes are drawn in the canvas background colour.
CANVAS_BG = THEME["canvas_bg"]

# Longer than the server's worst case (main model + fallback).
SUMMARY_TIMEOUT_MS = 150_000

# Live cursors: at most ~20 messages per second.
CURSOR_INTERVAL = 0.05

# Versioned board operations: all go through the same version check.
BOARD_UPDATES = ("DRAW_UPDATE", "UNDO_UPDATE", "CLEAR_UPDATE")


class SyncDrawClient(SyncDrawUI):
    def __init__(self, root):
        self.root = root
        self.client_id = CLIENT_ID

        self.color = "#2563EB"
        self.last_point = None
        self.sock = None
        self.send_lock = threading.Lock()
        self.messages = queue.Queue()
        self.running = True
        self.last_version = 0
        self.sync_requested = False

        # Tool state: "pen" or "eraser".
        self.tool = "pen"
        self.pen_width = 3
        self.eraser_size = tk.IntVar(value=30)
        self.mouse_pos = None

        # Board summary panel state.
        self.summary_pending = False
        self.summary_timeout_id = None
        self.last_summary = None

        # Role from the server. Everyone starts as viewer; the role only
        # changes when a ROLE_UPDATE arrives.
        self.role = "viewer"
        self.admin_id = None
        self.pin_required = False

        # Stroke being drawn: groups DRAW segments for undo.
        self.stroke_id = None

        # Live cursor throttle (timestamps, never sleep).
        self.last_cursor_time = 0.0
        self.last_cursor_pos = None
        self.cursor_hidden = True
        self.cursor_pending = None
        self.cursor_after_id = None

        # Layout, widgets and shortcuts live in client/ui.py.
        self.build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.set_tool("pen")
        self.apply_role()

        # Display name for the online list (not an identity).
        self.display_name = ask_name(self.root, CLIENT_ID)
        self.connect()
        self.root.after(50, self.process_messages)

    def request_admin(self):
        message = {"type": "ADMIN_REQUEST"}

        # Ask for the shared PIN only if the server requires one.
        if self.pin_required:
            pin = ask_pin(self.root)
            if pin is None:
                return  # cancelled
            message["pin"] = pin

        self.send_role_message(message)

    def release_admin(self):
        self.send_role_message({"type": "ADMIN_RELEASE"})

    def send_role_message(self, message):
        try:
            with self.send_lock:
                send_message(self.sock, message)

        except (OSError, ConnectionError, AttributeError) as exc:
            print("Admin request failed:", exc)
            self.show_server_error("Not connected")

    def request_undo(self):
        # The server enforces admin-only; this just avoids a useless request.
        if self.role == "admin":
            self.send_request({"type": "UNDO_REQUEST"})

    def request_clear(self):
        if self.role != "admin":
            return

        if ask_confirm(
            self.root,
            "Clear the board for everyone?",
            "This cannot be undone.",
            "Clear board",
        ):
            self.send_request({"type": "CLEAR_REQUEST"})

    def send_request(self, message):
        try:
            with self.send_lock:
                send_message(self.sock, message)

        except (OSError, ConnectionError, AttributeError) as exc:
            print("Request failed:", exc)
            self.show_server_error("Not connected")

    def send_cursor(self, x=None, y=None, hidden=False):
        """Live cursor (presence data: no version, may be dropped).

        At most one message per CURSOR_INTERVAL, only when it moved.
        A throttled position is sent later with after(), so the last
        position is never lost.
        """
        if self.sock is None:
            return

        if hidden:
            if self.cursor_after_id is not None:
                self.root.after_cancel(self.cursor_after_id)
                self.cursor_after_id = None
            if self.cursor_hidden:
                return
            message = {"type": "CURSOR", "hidden": True}

        else:
            if (x, y) == self.last_cursor_pos and not self.cursor_hidden:
                return

            wait = CURSOR_INTERVAL - (time.monotonic() - self.last_cursor_time)

            if wait > 0:
                self.cursor_pending = (x, y)
                if self.cursor_after_id is None:
                    self.cursor_after_id = self.root.after(
                        int(wait * 1000) + 1,
                        self.flush_cursor,
                    )
                return

            message = {"type": "CURSOR", "x": x, "y": y}

        try:
            with self.send_lock:
                send_message(self.sock, message)
        except (OSError, ConnectionError, AttributeError):
            return  # best effort

        self.last_cursor_time = time.monotonic()
        self.cursor_hidden = hidden
        self.last_cursor_pos = None if hidden else (x, y)

    def flush_cursor(self):
        self.cursor_after_id = None
        if self.cursor_pending is not None:
            x, y = self.cursor_pending
            self.cursor_pending = None
            self.send_cursor(x, y)

    def connect(self):
        try:
            self.sock = socket.create_connection(
                (HOST, PORT),
                timeout=5,
            )
            self.sock.settimeout(None)

            send_message(self.sock, {
                "type": "HELLO",
                "client_id": CLIENT_ID,
                "name": self.display_name,
                "last_version": self.last_version,
            })

            response = recv_message(self.sock)

            if response.get("type") != "SNAPSHOT":
                raise ConnectionError(
                    "Expected initial snapshot"
                )

            self.last_version = response["version"]

            for operation in response["operations"]:
                self.render_operation(operation)

            self.set_status("Connected", "ok")

            threading.Thread(
                target=self.receive_loop,
                daemon=True,
            ).start()

        except (OSError, ConnectionError, ValueError) as exc:
            self.set_status("Disconnected", "error")
            print("Connection error:", exc)


    def receive_loop(self):
        while self.running:
            try:
                # Receive messages while connected.
                while self.running:
                    message = recv_message(self.sock)
                    self.messages.put(message)

            except (OSError, ConnectionError, ValueError) as exc:
                if not self.running:
                    break

                self.messages.put({
                    "type": "CONNECTION_LOST",
                    "message": str(exc),
                })

                try:
                    self.sock.close()
                except (OSError, AttributeError):
                    pass

                # Retry connection every 2 seconds.
                while self.running:
                    time.sleep(2)

                    try:
                        new_sock = socket.create_connection(
                            (HOST, PORT),
                            timeout=5,
                        )

                        send_message(new_sock, {
                            "type": "HELLO",
                            "client_id": CLIENT_ID,
                            "name": self.display_name,
                            "last_version": self.last_version,
                        })

                        response = recv_message(new_sock)

                        if response.get("type") != "SNAPSHOT":
                            new_sock.close()
                            continue

                        new_sock.settimeout(None)
                        self.sock = new_sock

                        self.messages.put(response)
                        self.messages.put({
                            "type": "RECONNECTED",
                        })

                        break

                    except (OSError, ConnectionError, ValueError):
                        try:
                            new_sock.close()
                        except (OSError, UnboundLocalError):
                            pass

                if not self.running:
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

            if msg_type in BOARD_UPDATES:
                # DRAW, UNDO and CLEAR updates share one version check.
                incoming_version = message["version"]

                if incoming_version == self.last_version + 1:
                    self.apply_board_update(message)
                    self.last_version = incoming_version

                elif incoming_version <= self.last_version:
                    # Ignore duplicate or stale updates.
                    continue

                else:
                    # Request a snapshot when a version gap occurs.
                    self.set_status("Resynchronizing…", "warn")

                    if not self.sync_requested:
                        try:
                            with self.send_lock:
                                send_message(self.sock, {
                                    "type": "SYNC_REQUEST",
                                })
                            self.sync_requested = True

                        except (OSError, ConnectionError, AttributeError) as exc:
                            self.set_status("Disconnected", "error")
                            print("Sync request failed:", exc)

            elif msg_type == "SNAPSHOT":
                # Only strokes: keep remote cursors and the eraser preview.
                self.canvas.delete("stroke")

                for operation in message["operations"]:
                    self.render_operation(operation)

                self.last_version = message["version"]
                self.sync_requested = False
                self.set_status("Connected", "ok")

            elif msg_type == "ROLE_UPDATE":
                # Not a board update, so it skips the version-gap check.
                old_role, old_admin = self.role, self.admin_id
                self.role = "admin" if message.get("role") == "admin" else "viewer"
                self.admin_id = message.get("admin_id")
                self.pin_required = bool(message.get("pin_required"))
                self.apply_role()
                self.announce_role_change(old_role, old_admin)

            elif msg_type == "SUMMARY_RESPONSE":
                # Not a board update, so it skips the version-gap check.
                self.handle_summary_response(message)

            # Presence data: no version, never triggers SYNC_REQUEST.
            elif msg_type == "PRESENCE_UPDATE":
                self.update_presence(message.get("users", []))

            elif msg_type == "CURSOR_UPDATE":
                self.update_remote_cursor(message)

            elif msg_type == "CONNECTION_LOST":
                # receive_loop is already retrying every 2 seconds.
                self.set_status("Reconnecting…", "warn")
                print(message["message"])
                self.show_toast("Connection lost — reconnecting…", "warn")

                # The server frees our admin slot; we stay viewer after
                # reconnecting until a ROLE_UPDATE says otherwise.
                self.role = "viewer"
                self.admin_id = None
                self.role_known = False
                self.apply_role()

                # Presence is rebuilt from the next PRESENCE_UPDATE.
                self.clear_presence()
                self.cursor_hidden = True
                self.last_cursor_pos = None

                # A pending summary will never arrive on the old socket.
                if self.summary_pending:
                    self.finish_summary()
                    self.show_summary_card("error", "Connection lost, try again")

            elif msg_type == "RECONNECTED":
                self.show_toast("Reconnected", "ok")

            elif msg_type == "ERROR":
                print(
                    "Server error:",
                    message.get("message"),
                )
                self.show_server_error(message.get("message", "Server error"))

        self.refresh_version()
        self.root.after(50, self.process_messages)

    def request_summary(self):
        if self.summary_pending:
            return

        try:
            with self.send_lock:
                send_message(self.sock, {
                    "type": "SUMMARY_REQUEST",
                })

        except (OSError, ConnectionError, AttributeError) as exc:
            print("Summary request failed:", exc)
            self.show_summary_card("error", "Connection lost, try again")
            return

        self.summary_pending = True
        self.summary_busy(True)
        self.summary_timeout_id = self.root.after(
            SUMMARY_TIMEOUT_MS,
            self.summary_timed_out,
        )

    def finish_summary(self):
        """Clear the pending state and re-enable the button."""
        self.summary_pending = False

        if self.summary_timeout_id is not None:
            self.root.after_cancel(self.summary_timeout_id)
            self.summary_timeout_id = None

        self.summary_busy(False)

    def summary_timed_out(self):
        self.summary_timeout_id = None
        self.finish_summary()
        self.show_summary_card("error", "No response from server, try again")

    def handle_summary_response(self, message):
        self.finish_summary()

        if message.get("ok"):
            self.last_summary = message.get("summary", "")
            self.show_summary_card(
                "result",
                self.last_summary,
                footer=f"Board v{message.get('version')} · {time.strftime('%H:%M')}",
            )
        else:
            error = message.get("error", "Summary failed")
            # The cooldown is a soft warning; everything else is an error.
            kind = "warn" if error.startswith("Please wait") else "error"
            self.show_summary_card(kind, error)

    def apply_board_update(self, message):
        """Apply one versioned board operation (already version-checked)."""
        msg_type = message["type"]

        if msg_type == "DRAW_UPDATE":
            self.render_operation(message)

        elif msg_type == "UNDO_UPDATE":
            # Removing an eraser stroke makes the drawing under it reappear.
            self.canvas.delete(f"stroke_{message.get('stroke_id')}")

        elif msg_type == "CLEAR_UPDATE":
            # Strokes only: cursors and the eraser preview stay.
            self.canvas.delete("stroke")
            self.show_toast(f"{message.get('by', 'The admin')} cleared the board", "info")

    def render_operation(self, operation):
        if operation.get("type") not in ("DRAW", "DRAW_UPDATE"):
            return

        # Eraser ops are painted in the background colour;
        # ops without "tool" are treated as pen.
        if operation.get("tool", "pen") == "eraser":
            fill = CANVAS_BG
        else:
            fill = operation["color"]

        # Board units -> screen pixels (HiDPI scaling).
        s = self.canvas_scale

        # Tag by stroke so undo can delete the whole stroke.
        stroke_id = operation.get("stroke_id") or f"legacy_{operation.get('version')}"

        self.canvas.create_line(
            operation["x1"] * s,
            operation["y1"] * s,
            operation["x2"] * s,
            operation["y2"] * s,
            fill=fill,
            width=operation.get("width", 3) * s,
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
            tags=("stroke", f"stroke_{stroke_id}"),
        )

        # Keep cursors and the eraser preview above all strokes.
        self.canvas.tag_raise("remote_cursor")
        self.canvas.tag_raise("eraser_cursor")

    def start_draw(self, event):
        # Viewers can't draw: no start point, so drags send nothing.
        if self.role != "admin":
            return

        self.last_point = self.to_board(event.x, event.y)

        # New stroke: every segment until release shares this ID.
        self.stroke_id = uuid.uuid4().hex[:12]

    def draw(self, event):
        self.track_mouse(event)

        if (
            self.role != "admin"
            or self.last_point is None
            or self.sock is None
        ):
            return

        x1, y1 = self.last_point
        x2, y2 = self.to_board(event.x, event.y)

        message = {
            "type": "DRAW",
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "stroke_id": self.stroke_id,
        }

        # Eraser sends no colour; the server fills in the background.
        if self.tool == "eraser":
            message["tool"] = "eraser"
            message["width"] = self.eraser_size.get()
        else:
            message["tool"] = "pen"
            message["color"] = self.color
            message["width"] = self.pen_width

        # Do not draw locally; render the server's broadcast
        # to avoid duplicate strokes.
        try:
            with self.send_lock:
                send_message(self.sock, message)

        except (OSError, ConnectionError, AttributeError) as exc:
            self.set_status("Send failed", "error")
            print("Send error:", exc)

        self.last_point = (x2, y2)

    def stop_draw(self, event):
        self.last_point = None
        self.stroke_id = None

    def close(self):
        self.running = False

        if self.sock:
            self.sock.close()

        self.root.destroy()


def main():
    root = ctk.CTk()
    SyncDrawClient(root)
    root.mainloop()


if __name__ == "__main__":
    main()
