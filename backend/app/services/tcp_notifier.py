from __future__ import annotations

import json
import socket
import threading
import time
from typing import Dict, Any, List


class TcpNotifier:
    def __init__(self, host: str = "0.0.0.0", port: int = 2120, mode: str = "server", enabled: bool = True):
        self.enabled = bool(enabled)
        self.mode = mode if mode in {"server", "client"} else "server"
        self.host = host
        self.port = port
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._clients: List[socket.socket] = []
        self._client_socket: socket.socket | None = None
        self._last_client_connect_attempt = 0.0
        self._lock = threading.Lock()

    def configure(self, cfg: Dict[str, Any] | None) -> None:
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", self.enabled))
        self.mode = str(cfg.get("connection_mode", self.mode) or self.mode).lower()
        if self.mode not in {"server", "client"}:
            self.mode = "server"
        self.host = str(cfg.get("host", self.host) or self.host)
        self.port = int(cfg.get("port", self.port) or self.port)
        self.stop()
        if self.enabled:
            self.start()

    def start(self) -> None:
        if not self.enabled:
            return
        if self.mode != "server":
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
        if self._client_socket:
            try:
                self._client_socket.close()
            except OSError:
                pass
            self._client_socket = None
        with self._lock:
            for c in list(self._clients):
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()

    def broadcast(self, payload: Dict[str, Any] | str | bytes) -> None:
        if not self.enabled:
            return
        if isinstance(payload, bytes):
            data = payload
        elif isinstance(payload, str):
            data = payload.encode("utf-8")
        else:
            data = (json.dumps(payload) + "\n").encode("utf-8")
        if self.mode == "client":
            self._send_as_client(data)
            return
        with self._lock:
            alive: List[socket.socket] = []
            for c in self._clients:
                try:
                    c.sendall(data)
                    alive.append(c)
                except OSError:
                    try:
                        c.close()
                    except OSError:
                        pass
            self._clients = alive

    def _connect_client_socket(self) -> bool:
        now = time.time()
        # Avoid reconnect storm when remote is down.
        if now - self._last_client_connect_attempt < 0.5:
            return False
        self._last_client_connect_attempt = now
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            s.connect((self.host, self.port))
            s.settimeout(1.5)
            self._client_socket = s
            return True
        except OSError:
            if self._client_socket:
                try:
                    self._client_socket.close()
                except OSError:
                    pass
            self._client_socket = None
            return False

    def _send_as_client(self, data: bytes) -> None:
        if self._client_socket is None and not self._connect_client_socket():
            return
        try:
            assert self._client_socket is not None
            self._client_socket.sendall(data)
        except OSError:
            try:
                if self._client_socket:
                    self._client_socket.close()
            except OSError:
                pass
            self._client_socket = None

    def _run(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(5)
        srv.settimeout(1.0)
        self._server = srv
        while not self._stop.is_set():
            try:
                client, _ = srv.accept()
                client.settimeout(2.0)
                with self._lock:
                    self._clients.append(client)
            except socket.timeout:
                continue
            except OSError:
                break
