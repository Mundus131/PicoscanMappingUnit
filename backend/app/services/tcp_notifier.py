from __future__ import annotations

import json
import socket
import threading
from typing import Dict, Any, List


class TcpNotifier:
    def __init__(self, host: str = "0.0.0.0", port: int = 2120):
        self.host = host
        self.port = port
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._clients: List[socket.socket] = []
        self._lock = threading.Lock()

    def start(self) -> None:
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
        with self._lock:
            for c in list(self._clients):
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()

    def broadcast(self, payload: Dict[str, Any] | str | bytes) -> None:
        if isinstance(payload, bytes):
            data = payload
        elif isinstance(payload, str):
            data = payload.encode("utf-8")
        else:
            data = (json.dumps(payload) + "\n").encode("utf-8")
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
