"""Client side of the Blender bridge (newline-delimited JSON over TCP)."""

from __future__ import annotations

import itertools
import json
import os
import socket
import threading
from typing import Any, Dict, Optional

DEFAULT_HOST = os.environ.get("BLENDER_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("BLENDER_PORT", "9877"))
DEFAULT_TIMEOUT = float(os.environ.get("BLENDER_TIMEOUT", "180"))


class BlenderError(RuntimeError):
    """Blender reported an error, or could not be reached."""


class BlenderConnection:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def _connect(self) -> socket.socket:
        if self._sock is None:
            try:
                self._sock = socket.create_connection((self.host, self.port), timeout=5.0)
            except OSError as exc:
                raise BlenderError(
                    "Cannot reach Blender at %s:%d (%s). Open Blender with the 'Blender Animate MCP' add-on "
                    "enabled and press 'Start MCP Bridge' in the 3D View sidebar (N panel > Animate MCP)."
                    % (self.host, self.port, exc)) from exc
            self._sock.settimeout(self.timeout)
            self._buf = b""
        return self._sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def call(self, command: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Send one command and wait for its result. Retries once on a stale socket."""
        payload = {"id": next(self._ids), "command": command,
                   "params": {k: v for k, v in (params or {}).items() if v is not None}}
        line = (json.dumps(payload) + "\n").encode("utf-8")
        with self._lock:
            for attempt in (0, 1):
                sock = self._connect()
                try:
                    sock.sendall(line)
                    response = self._read_line(sock)
                    break
                except (OSError, ConnectionError) as exc:
                    self.close()
                    if attempt == 1 or isinstance(exc, socket.timeout):
                        raise BlenderError("Lost connection to Blender during %r: %s" % (command, exc)) from exc
        if not response.get("ok"):
            raise BlenderError(response.get("error", "Unknown Blender error"))
        return response.get("result")

    def _read_line(self, sock: socket.socket) -> Dict[str, Any]:
        while b"\n" not in self._buf:
            chunk = sock.recv(1 << 16)
            if not chunk:
                raise ConnectionError("Blender closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))
