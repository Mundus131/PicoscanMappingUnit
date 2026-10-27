"""Per-scanner processing process; only complete clouds cross into the API."""
import multiprocessing as mp
import os
import queue
import threading
import time


def _run_scan(config, settings, output, stop, ready, drops):
    manager = None
    receiver = None
    worker = None
    worker_stop = threading.Event()
    parent_pid = os.getppid()
    try:
        from app.core.device_manager import device_manager
        from app.services.picoscan_receiver import PicoscanReceiver, PicoscanReceiverManager
        device_manager.point_cloud_settings = settings
        receiver = PicoscanReceiver(config['listen_ip'], config['listen_port'], config['format_type'])
        if not receiver.start_listening():
            raise RuntimeError(receiver.last_error or 'Scanner socket startup failed')
        manager = PicoscanReceiverManager()
        info = manager._build_receiver_info(receiver=receiver, **config)
        device_id = config['device_id']
        manager.receivers[device_id] = info
        worker = threading.Thread(target=manager._worker_loop, args=(device_id, worker_stop), daemon=True)
        worker.start()
        ready.send((True, None))
        last_report = 0.0
        parent_check_at = time.monotonic() + 1
        while not stop.wait(0.02):
            if time.monotonic() >= parent_check_at:
                if os.getppid() != parent_pid:
                    break
                parent_check_at = time.monotonic() + 1
            frames = manager.drain_point_clouds(device_id)
            now = time.monotonic()
            if not frames and now - last_report < 0.2:
                continue
            packet = dict(frames=frames, health=manager.get_health_snapshot()[device_id],
                          metrics=receiver.get_metrics(), stats=info.get('last_segment_stats'))
            try:
                output.put_nowait(packet)
            except queue.Full:
                with drops.get_lock():
                    drops.value += len(frames)
            last_report = now
    except Exception as exc:
        try:
            ready.send((False, f'{type(exc).__name__}: {exc}'))
        except (OSError, BrokenPipeError):
            pass
    finally:
        worker_stop.set()
        if worker:
            worker.join(timeout=1)
        if receiver:
            receiver.stop_listening()
        ready.close()
        output.cancel_join_thread()


class ScanProcessReceiver:
    def __init__(self, config, settings):
        self.config = config
        self.settings = dict(settings or {})
        self.format_type = config['format_type']
        self.connected = False
        self.last_error = None
        self.metrics = {}
        self.stream_epoch = time.monotonic_ns()
        self._process = None

    def start_listening(self):
        ctx = mp.get_context('spawn')
        self._queue = ctx.Queue(maxsize=32)
        self._stop = ctx.Event()
        self._drops = ctx.Value('q', 0)
        parent, child = ctx.Pipe(duplex=False)
        # Non-daemon: this process owns the separate raw UDP ingress process.
        self._process = ctx.Process(target=_run_scan, args=(self.config, self.settings,
            self._queue, self._stop, child, self._drops), name='scanner-processing')
        self._process.start()
        child.close()
        try:
            if not parent.poll(20):
                raise TimeoutError('Scanner processing startup timeout')
            ok, error = parent.recv()
            if not ok:
                raise RuntimeError(error)
            self.connected = True
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.stop_listening()
            return False
        finally:
            parent.close()

    def receive_packet(self):
        try:
            packet = self._queue.get(timeout=0.2)
            self.metrics = packet['metrics']
            packet['health']['processing_queue_dropped'] = self._drops.value
            return packet
        except queue.Empty:
            if not self._process.is_alive():
                self.connected = False
                self.last_error = 'Scanner processing process stopped'
            return None

    def get_metrics(self):
        return dict(self.metrics, processing_alive=bool(self._process and self._process.is_alive()),
                    processing_queue_dropped=self._drops.value)

    def get_info(self):
        return dict(listen_ip=self.config['listen_ip'], listen_port=self.config['listen_port'],
                    listening=self.connected, format=self.format_type, processing='isolated')

    def stop_listening(self):
        self.connected = False
        if self._process is None:
            return True
        self._stop.set()
        self._process.join(timeout=5)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
        self._queue.close()
        self._queue.cancel_join_thread()
        self._process = None
        return True
