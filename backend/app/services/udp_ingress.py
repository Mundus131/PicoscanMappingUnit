"""Bounded UDP ingress isolated from parser and application GIL contention."""
import multiprocessing as mp
import queue
import socket
import time


def _receive(address, port, packet_size, capacity_bytes, output, stop, ready, stats):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, capacity_bytes)
        sock.bind((address, port))
        sock.settimeout(0.1)
        ready.send((True, sock.getsockname()[1], sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)))
        while not stop.is_set():
            try:
                data, sender = sock.recvfrom(packet_size)
            except socket.timeout:
                continue
            item = (data, sender, time.time(), time.monotonic())
            with stats.get_lock():
                stats[0] += 1
            try:
                output.put_nowait(item)
            except queue.Full:
                with stats.get_lock():
                    stats[1] += 1
    except Exception as exc:
        try:
            ready.send((False, str(exc), 0))
        except (BrokenPipeError, OSError):
            pass
    finally:
        sock.close()
        ready.close()
        # Shutdown discards pending data; never hang joining a full IPC pipe.
        output.cancel_join_thread()


class ProcessUDPHandler:
    """ScanSegmentAPI transport contract with timestamps captured in the child."""

    def __init__(self, address, port, packet_size=65535, queue_capacity=512,
                 receive_buffer_bytes=4 * 1024 * 1024):
        ctx = mp.get_context("spawn")
        self._queue = ctx.Queue(maxsize=queue_capacity)
        self._stop = ctx.Event()
        self._stats = ctx.Array("q", 2)
        self._consumed = 0
        self.queue_capacity = queue_capacity
        self.last_host_receipt = None
        self.last_queue_age_s = 0.0
        self.max_queue_age_s = 0.0
        self.no_error_flag = True
        self.last_error_code = None
        self.last_error_message = ""
        self.counter = 0
        self._closed = False
        parent, child = ctx.Pipe(duplex=False)
        self._process = ctx.Process(target=_receive, args=(
            address, port, packet_size, receive_buffer_bytes, self._queue,
            self._stop, child, self._stats), daemon=True, name=f"udp-ingress-{port}")
        self._process.start()
        child.close()
        try:
            if not parent.poll(10):
                raise TimeoutError("UDP ingress startup timed out")
            ok, value, effective = parent.recv()
            if not ok:
                raise OSError(value)
            self.port = value
            self.receive_buffer_bytes = effective
        except Exception:
            self.close()
            raise
        finally:
            parent.close()

    def receive_new_scan_segment(self):
        self.last_host_receipt = None
        try:
            data, sender, wall, monotonic = self._queue.get(timeout=0.1)
        except queue.Empty:
            self.no_error_flag = False
            self.last_error_message = "UDP ingress timeout" if self._process.is_alive() else "UDP ingress process stopped"
            if not self._process.is_alive():
                raise RuntimeError("transport_layer ingress process stopped")
            raise TimeoutError(self.last_error_message)
        self._consumed += 1
        self.counter += 1
        self.no_error_flag = True
        self.last_host_receipt = (wall, monotonic)
        self.last_queue_age_s = max(0.0, time.monotonic() - monotonic)
        self.max_queue_age_s = max(self.max_queue_age_s, self.last_queue_age_s)
        return data, sender

    def has_no_error(self):
        return self.no_error_flag

    def get_last_error_code(self):
        return self.last_error_code

    def get_data_counter(self):
        return self.counter

    def get_metrics(self):
        with self._stats.get_lock():
            received, dropped = self._stats[:]
        return {"datagrams_received": received, "ingress_queue_dropped": dropped,
                "ingress_queue_depth": max(0, received - dropped - self._consumed),
                "ingress_queue_capacity": self.queue_capacity,
                "ingress_queue_age_s": self.last_queue_age_s,
                "ingress_queue_max_age_s": self.max_queue_age_s,
                "socket_receive_buffer_bytes": self.receive_buffer_bytes,
                "ingress_alive": self._process.is_alive()}

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._process.join(timeout=2)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
        self._queue.close()
        self._queue.cancel_join_thread()
