"""HTTP encoder polling isolated from API/analysis CPU work.

Tokens remain owned by the application. The child continues polling with the
last token while the parent refreshes it; no credentials are logged.
"""
import multiprocessing as mp
import os
import queue
import time


def _poll_encoder(config, commands, samples, stop, parent_pid):
    import requests

    previous_started = None
    with requests.Session() as http:
        while not stop.is_set() and os.getppid() == parent_pid:
            try:
                while True:
                    config = commands.get_nowait()
            except queue.Empty:
                pass
            started = time.monotonic()
            packet = dict(generation=config['generation'], started=started,
                          poll_gap_ms=None if previous_started is None else (started - previous_started) * 1000)
            previous_started = started
            try:
                with http.get(config['url'], headers={
                    'Cookie': 'access_token=' + config['token'],
                    'User-Agent': 'Picoscan-Encoder/1.0'}, timeout=(0.3, 0.5)) as response:
                    response.raise_for_status()
                    packet['payload'] = {'data': response.json(), 'format': 'iodd'}
            except Exception as exc:
                # Exception messages may contain URLs or response data.
                packet['error'] = type(exc).__name__
            packet['received'] = time.monotonic()
            try:
                samples.put_nowait(packet)
            except queue.Full:
                # Never silently discard history and continue presenting it as valid.
                break
            stop.wait(max(0.0, config['interval_s'] - (time.monotonic() - started)))
    samples.cancel_join_thread()


class EncoderProcess:
    def __init__(self):
        self.ctx = mp.get_context('spawn')
        self.process = None
        self.config = None
        self.generation = 0

    def configure(self, url, token, interval_s):
        config = dict(url=url, token=token, interval_s=interval_s)
        if self.config == config and self.process is not None:
            return
        # Restart and discard queued samples when the physical source changes.
        if self.config and self.config['url'] != url:
            self.close()
        self.config = config
        self.generation += 1
        command = dict(config, generation=self.generation)
        if self.process is None:
            self.commands = self.ctx.Queue(maxsize=8)
            self.samples = self.ctx.Queue(maxsize=64)
            self.stop = self.ctx.Event()
            self.process = self.ctx.Process(target=_poll_encoder,
                args=(command, self.commands, self.samples, self.stop, os.getpid()), daemon=True)
            self.process.start()
        else:
            self.commands.put(command, timeout=0.1)

    def receive(self):
        if self.process is None or not self.process.is_alive():
            raise RuntimeError('Encoder polling process stopped or sample queue overflowed')
        try:
            packet = self.samples.get(timeout=0.3)
        except queue.Empty:
            return None
        if packet['generation'] != self.generation:
            return None
        return packet

    def close(self):
        if self.process is not None:
            if self.process.is_alive():
                self.stop.set()
                self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=1)
            for channel in (self.commands, self.samples):
                channel.cancel_join_thread()
                channel.close()
            self.process = None
        self.config = None
