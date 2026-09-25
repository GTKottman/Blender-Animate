"""TCP bridge between the MCP server and Blender.

Wire protocol: newline-delimited JSON.

    -> {"id": 1, "command": "animate", "params": {...}}
    <- {"id": 1, "ok": true, "result": {...}}
    <- {"id": 1, "ok": false, "error": "message"}

Sockets are served on a background thread, but bpy is not thread-safe, so
every command is queued and executed on Blender's main thread by a timer.
"""

import json
import queue
import socket
import threading
import traceback

import bpy

from . import commands

JOB_TIMEOUT_S = 300.0


class _Job:
    __slots__ = ("request", "response", "done")

    def __init__(self, request):
        self.request = request
        self.response = None
        self.done = threading.Event()


class BridgeServer:
    def __init__(self, host="127.0.0.1", port=9877):
        self.host = host
        self.port = port
        self._sock = None
        self._thread = None
        self._jobs = queue.Queue()
        self._running = False
        self.clients = 0
        self.last_command = ""

    @property
    def running(self):
        return self._running

    def start(self):
        if self._running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, name="blender-animate-mcp", daemon=True)
        self._thread.start()
        if not bpy.app.timers.is_registered(self._pump):
            bpy.app.timers.register(self._pump, first_interval=0.05, persistent=True)
        print("[Blender Animate MCP] listening on %s:%d" % (self.host, self.port))

    def stop(self):
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if bpy.app.timers.is_registered(self._pump):
            bpy.app.timers.unregister(self._pump)
        # Release any waiting client threads.
        while not self._jobs.empty():
            job = self._jobs.get_nowait()
            job.response = {"id": job.request.get("id"), "ok": False, "error": "Blender bridge stopped"}
            job.done.set()
        print("[Blender Animate MCP] stopped")

    # -- background thread -------------------------------------------------

    def _accept_loop(self):
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve_client, args=(conn,), daemon=True).start()

    def _serve_client(self, conn):
        self.clients += 1
        buf = b""
        try:
            conn.settimeout(None)
            while self._running:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    response = self._handle_line(line)
                    conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            self.clients -= 1
            try:
                conn.close()
            except OSError:
                pass

    def _handle_line(self, line):
        try:
            request = json.loads(line.decode("utf-8"))
        except ValueError as exc:
            return {"id": None, "ok": False, "error": "Invalid JSON: %s" % exc}
        job = _Job(request)
        self._jobs.put(job)
        if not job.done.wait(JOB_TIMEOUT_S):
            return {"id": request.get("id"), "ok": False,
                    "error": "Timed out waiting for Blender's main thread (is a modal operator or render running?)"}
        return job.response

    # -- main thread -------------------------------------------------------

    def _pump(self):
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            req = job.request
            name = req.get("command", "")
            self.last_command = name
            try:
                result = commands.dispatch(name, req.get("params") or {})
                job.response = {"id": req.get("id"), "ok": True, "result": result}
            except commands.CommandError as exc:
                job.response = {"id": req.get("id"), "ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - report everything back to the AI
                traceback.print_exc()
                job.response = {"id": req.get("id"), "ok": False,
                                "error": "%s: %s" % (type(exc).__name__, exc)}
            job.done.set()
        return 0.02 if self._running else None


_server = None


def get_server():
    return _server


def start(host, port):
    global _server
    if _server is not None and _server.running:
        return _server
    _server = BridgeServer(host, port)
    _server.start()
    return _server


def stop():
    global _server
    if _server is not None:
        _server.stop()
    _server = None
