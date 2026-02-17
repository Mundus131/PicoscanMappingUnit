"""
LMS4000 Receiver - TCP CoLa-A client for LMDscandata telegrams
"""
import logging
import socket
import struct
import time
import threading
from typing import List, Optional
import numpy as np

logger = logging.getLogger(__name__)


def _safe_exc_text(exc: Exception) -> str:
    """
    Ensure exception text is safe for legacy console encodings (e.g. cp1252 on Windows).
    """
    try:
        txt = str(exc)
    except Exception:
        txt = repr(exc)
    try:
        return txt.encode("ascii", "backslashreplace").decode("ascii")
    except Exception:
        return repr(exc)


def _parse_int_auto(token: str) -> int:
    t = token.strip()
    if t.startswith("+") or t.startswith("-"):
        try:
            return int(t, 10)
        except Exception:
            pass
    try:
        # SOPAS telegrams are typically hex-encoded values.
        return int(t, 16)
    except Exception:
        return int(float(t))


def _parse_float_u32_hex(token: str) -> float:
    v = _parse_int_auto(token) & 0xFFFFFFFF
    return struct.unpack(">f", struct.pack(">I", v))[0]


def _is_finite_reasonable(v: float, lo: float, hi: float) -> bool:
    return np.isfinite(v) and lo <= v <= hi


class Lms4000Receiver:
    """Receives LMS4000 scan data via CoLa-A over TCP."""

    def __init__(self, sensor_ip: str, sensor_port: int = 2111, timeout_s: float = 2.0):
        self.sensor_ip = sensor_ip
        self.sensor_port = sensor_port
        self.timeout_s = timeout_s
        self.sock: Optional[socket.socket] = None
        self.connected = False
        self._rx_buf = bytearray()
        self.last_receive_ts: float | None = None
        self.total_telegrams_received = 0
        self._last_reconnect_ts: float | None = None
        self._last_socket_drop_log_ts: float | None = None
        self._io_lock = threading.Lock()
        # Heuristic fallback can create arc artifacts on mixed binary telegrams.
        # Keep it disabled by default for stable visualization.
        self.allow_heuristic_fallback = False
        # Keep raw scanner angles unchanged; mounting correction is applied in calibration.
        self.scan_angle_offset_deg = 0.0
        # Prefer continuous stream only. Poll fallback can destabilize some LMS firmwares.
        self.use_poll_fallback = False

    def start_listening(self) -> bool:
        try:
            self.sock = socket.create_connection((self.sensor_ip, self.sensor_port), timeout=self.timeout_s)
            self.sock.settimeout(self.timeout_s)
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass
            self.connected = True
            self._rx_buf.clear()
            self._force_cola_a_best_effort()
            # Best-effort startup sequence for LMS4xxx.
            # Keep it minimal and robust across firmware variants.
            self._send_and_log("sMN SetAccessMode 03 F4724744")
            self._send_and_log("sMN LMCstartmeas")
            self._send_and_log("sMN Run")
            self._send_and_log("sWN ScanDataEnable 1")
            self._wait_until_ready(max_wait_s=8.0)
            self._enable_lmdscandata_stream(True)
            logger.info("Connected LMS4000 receiver on %s:%s", self.sensor_ip, self.sensor_port)
            return True
        except Exception as exc:
            logger.error("LMS4000 start_listening failed: %s", _safe_exc_text(exc))
            self.connected = False
            self._safe_close()
            return False

    def stop_listening(self) -> bool:
        try:
            if self.connected:
                try:
                    self._enable_lmdscandata_stream(False)
                except Exception:
                    pass
            self._safe_close()
            self.connected = False
            return True
        except Exception as exc:
            logger.error("LMS4000 stop_listening failed: %s", _safe_exc_text(exc))
            return False

    def _reconnect_once(self) -> bool:
        # Limit reconnect churn when preview polls fast.
        now = time.time()
        if self._last_reconnect_ts is not None and (now - self._last_reconnect_ts) < 0.8:
            return False
        self._last_reconnect_ts = now
        self.connected = False
        self._safe_close()
        self._rx_buf.clear()
        return self.start_listening()

    def _safe_close(self):
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def _send_cola_ascii(self, payload: str):
        if not self.sock:
            raise RuntimeError("Socket not connected")
        telegram = b"\x02" + payload.encode("ascii", errors="ignore") + b"\x03"
        self.sock.sendall(telegram)

    def _send_cola_binary_ascii_payload(self, payload: str):
        """
        Send CoLa-B style frame with ascii payload.
        Frame: 0x02 0x02 0x02 0x02 + uint32(len) + payload + xor-checksum(payload)
        """
        if not self.sock:
            raise RuntimeError("Socket not connected")
        payload_bytes = payload.encode("ascii", errors="ignore")
        length = len(payload_bytes).to_bytes(4, "big", signed=False)
        checksum = 0
        for b in payload_bytes:
            checksum ^= b
        telegram = b"\x02\x02\x02\x02" + length + payload_bytes + bytes([checksum & 0xFF])
        self.sock.sendall(telegram)

    def _send_cola_binary_payload(self, payload_bytes: bytes):
        if not self.sock:
            raise RuntimeError("Socket not connected")
        length = len(payload_bytes).to_bytes(4, "big", signed=False)
        checksum = 0
        for b in payload_bytes:
            checksum ^= b
        telegram = b"\x02\x02\x02\x02" + length + payload_bytes + bytes([checksum & 0xFF])
        self.sock.sendall(telegram)

    def _force_cola_a_best_effort(self):
        """
        Some LMS devices come up in CoLa-B with binary LMDscandata payload,
        while our parser is CoLa-A text-oriented. Try switching to CoLa-A.
        """
        commands = [
            "sMN SetToColaA",
            "sWN EIHstCola 0",
        ]
        for cmd in commands:
            try:
                self._send_cola_ascii(cmd)
            except Exception:
                pass
            try:
                self._send_cola_binary_ascii_payload(cmd)
            except Exception:
                pass
            try:
                _ = self._recv_cola_ascii_once(timeout_s=0.3)
            except Exception:
                pass
        # Drop any mixed protocol leftovers.
        self._rx_buf.clear()

    def _enable_lmdscandata_stream(self, enabled: bool):
        # CoLa-A
        self._send_and_log(f"sEN LMDscandata {1 if enabled else 0}")
        # CoLa-B with binary enum byte (per SOPAS telegram definition)
        try:
            payload = b"sEN LMDscandata " + (b"\x01" if enabled else b"\x00")
            self._send_cola_binary_payload(payload)
            reply = self._recv_cola_ascii_once(timeout_s=0.6)
            if reply:
                logger.debug("LMS4000 stream enable(binary) -> %s", reply[:180])
        except Exception as exc:
            logger.debug("LMS4000 stream enable(binary) failed: %s", exc)

    def _wait_until_ready(self, max_wait_s: float = 8.0):
        t_end = time.time() + max_wait_s
        while time.time() < t_end:
            state_msg = None
            for mode in ("cola_a", "cola_b"):
                try:
                    if mode == "cola_a":
                        self._send_cola_ascii("sRN SCdevicestate")
                    else:
                        self._send_cola_binary_ascii_payload("sRN SCdevicestate")
                    state_msg = self._recv_cola_ascii_once(timeout_s=0.8)
                    if state_msg:
                        break
                except Exception:
                    continue
            if state_msg and ("SCdevicestate" in state_msg):
                # expected examples: "sRA SCdevicestate 1" or binary-equivalent decoded text
                tail = state_msg.split()[-1] if state_msg.split() else ""
                try:
                    state_val = _parse_int_auto(tail)
                    if state_val == 1:
                        return
                except Exception:
                    pass
            time.sleep(0.2)

    def _send_and_log(self, payload: str):
        # Try CoLa-A first, fallback to CoLa-B frame.
        for mode in ("cola_a", "cola_b"):
            try:
                if mode == "cola_a":
                    self._send_cola_ascii(payload)
                else:
                    self._send_cola_binary_ascii_payload(payload)
                reply = self._recv_cola_ascii_once(timeout_s=0.6)
                if reply:
                    logger.debug("LMS4000 cmd[%s] '%s' -> '%s'", mode, payload, reply[:180])
                    return
            except Exception as exc:
                logger.debug("LMS4000 cmd[%s] '%s' failed: %s", mode, payload, exc)
        logger.debug("LMS4000 cmd '%s' -> no reply", payload)

    def _recv_cola_ascii_once(self, expect_prefix: str | None = None, timeout_s: float | None = None) -> Optional[str]:
        if not self.sock:
            return None
        t_end = time.time() + (timeout_s if timeout_s is not None else self.timeout_s)
        while time.time() < t_end:
            # Parse complete telegram from buffer.
            # CoLa-B: 4xSTX + len(4) + payload + crc(1)
            while True:
                stx4_pos = self._rx_buf.find(b"\x02\x02\x02\x02")
                if stx4_pos < 0:
                    break
                if stx4_pos > 0:
                    del self._rx_buf[:stx4_pos]
                if len(self._rx_buf) < 8:
                    break
                msg_len = int.from_bytes(self._rx_buf[4:8], "big", signed=False)
                if msg_len <= 0 or msg_len > 2_000_000:
                    # Resync on invalid length.
                    del self._rx_buf[0]
                    continue
                total_len = 8 + msg_len + 1
                if len(self._rx_buf) < total_len:
                    break
                raw = bytes(self._rx_buf[8:8 + msg_len])
                crc = self._rx_buf[8 + msg_len]
                calc_crc = 0
                for b in raw:
                    calc_crc ^= b
                del self._rx_buf[:total_len]
                if calc_crc != crc:
                    # CRC mismatch -> keep searching next frame.
                    continue
                # Preserve full 0..255 payload for mixed binary telegrams.
                # ASCII decode with "ignore" drops bytes and corrupts DIST data.
                msg = raw.decode("latin1", errors="ignore").strip()
                if msg:
                    if expect_prefix and not msg.startswith(expect_prefix):
                        continue
                    return msg

            # CoLa-A: STX + payload + ETX
            stx_pos = self._rx_buf.find(0x02)
            if stx_pos >= 0:
                etx_pos = self._rx_buf.find(0x03, stx_pos + 1)
                if etx_pos >= 0:
                    raw = bytes(self._rx_buf[stx_pos + 1:etx_pos])
                    del self._rx_buf[:etx_pos + 1]
                    # Keep binary bytes intact for downstream DIST parser.
                    msg = raw.decode("latin1", errors="ignore").strip()
                    if msg:
                        if expect_prefix and not msg.startswith(expect_prefix):
                            # skip other notifications
                            continue
                        return msg
            try:
                chunk = self.sock.recv(65535)
                if not chunk:
                    return None
                self._rx_buf.extend(chunk)
                # Diagnostic for sensors sending unexpected payload format.
                if len(self._rx_buf) > 0 and b"\x02" not in self._rx_buf[: min(32, len(self._rx_buf))]:
                    logger.debug("LMS4000 raw chunk(no STX) len=%s head=%s", len(chunk), chunk[:24].hex())
            except socket.timeout:
                continue
        return None

    def _request_scan_once(self) -> Optional[str]:
        """
        Fallback mode: actively request one scan (sRN LMDscandata) instead of waiting
        for unsolicited event telegrams.
        """
        for mode in ("cola_a", "cola_b"):
            try:
                if mode == "cola_a":
                    self._send_cola_ascii("sRN LMDscandata")
                else:
                    self._send_cola_binary_ascii_payload("sRN LMDscandata")
                msg = self._recv_cola_ascii_once(timeout_s=self.timeout_s)
                if msg:
                    return msg
            except Exception:
                continue
        return None

    def _parse_lmdscandata(self, telegram: str) -> np.ndarray:
        """
        Parse CoLa-A 'sSN LMDscandata ...' into Nx4 array [x,y,z,rssi].
        Coordinates: mm in scanner plane (z=0).
        """
        if not telegram.startswith("sSN LMDscandata"):
            # Some firmwares send "sRA LMDscandata" notifications.
            if telegram.startswith("sRA LMDscandata"):
                telegram = "sSN" + telegram[3:]
            else:
                return np.array([], dtype=np.float32).reshape(0, 4)

        if not telegram.startswith("sSN LMDscandata"):
            return np.array([], dtype=np.float32).reshape(0, 4)

        # This LMS4000 variant streams mixed/binary payload after DIST1=.
        # Parsing as plain ASCII tokens is unstable and causes geometric artifacts.
        if "DIST1" in telegram:
            pts_bin = self._parse_lmdscandata_binary_dist1(telegram)
            if pts_bin.shape[0] > 0:
                return pts_bin
            if self.allow_heuristic_fallback:
                return self._parse_lmdscandata_binary_fallback(telegram)
            return np.array([], dtype=np.float32).reshape(0, 4)

        tokens = telegram.split()
        if len(tokens) < 10:
            # CoLa-B event can contain mixed ascii header + binary payload.
            # In that case tokenized ascii parsing is not applicable.
            if self.allow_heuristic_fallback:
                return self._parse_lmdscandata_binary_fallback(telegram)
            return np.array([], dtype=np.float32).reshape(0, 4)

        # Find measurement channel blocks (DIST*, RSSI*)
        dist_block = None
        rssi_block = None
        for i, tok in enumerate(tokens):
            if tok.upper().startswith("DIST") and i + 6 < len(tokens):
                dist_block = i
                break
        for i, tok in enumerate(tokens):
            if tok.upper().startswith("RSSI") and i + 6 < len(tokens):
                rssi_block = i
                break

        if dist_block is None:
            # "DIST1=" may exist in binary payload even if split() cannot see tokens.
            if "DIST1" in telegram:
                if self.allow_heuristic_fallback:
                    return self._parse_lmdscandata_binary_fallback(telegram)
                return np.array([], dtype=np.float32).reshape(0, 4)
            logger.debug("LMS4000 telegram without DIST block: %s", telegram[:220])
            return np.array([], dtype=np.float32).reshape(0, 4)

        try:
            d_scale = _parse_float_u32_hex(tokens[dist_block + 1])
            d_offset = _parse_float_u32_hex(tokens[dist_block + 2])
            start_angle_raw = _parse_int_auto(tokens[dist_block + 3])
            step_raw = _parse_int_auto(tokens[dist_block + 4])
            n = _parse_int_auto(tokens[dist_block + 5])
            d0 = dist_block + 6
            d1 = d0 + n
            if d1 > len(tokens):
                return np.array([], dtype=np.float32).reshape(0, 4)
            dist_vals = np.array([_parse_int_auto(t) for t in tokens[d0:d1]], dtype=np.float64)
            distances = dist_vals * d_scale + d_offset
            angles_deg = (start_angle_raw + np.arange(n, dtype=np.float64) * step_raw) / 10000.0
            angles_deg = angles_deg + float(self.scan_angle_offset_deg)
            angles_rad = np.deg2rad(angles_deg)
        except Exception as exc:
            if "DIST1" in telegram:
                # Mixed binary payload: parse from raw bytes when available.
                pts_bin = self._parse_lmdscandata_binary_dist1(telegram)
                if pts_bin.shape[0] > 0:
                    return pts_bin
                if self.allow_heuristic_fallback:
                    return self._parse_lmdscandata_binary_fallback(telegram)
                return np.array([], dtype=np.float32).reshape(0, 4)
            logger.debug("LMS4000 DIST parse failed: %s, telegram=%s", _safe_exc_text(exc), telegram[:220])
            return np.array([], dtype=np.float32).reshape(0, 4)

        rssi = np.zeros((n,), dtype=np.float64)
        if rssi_block is not None:
            try:
                r_scale = _parse_float_u32_hex(tokens[rssi_block + 1])
                r_offset = _parse_float_u32_hex(tokens[rssi_block + 2])
                rn = _parse_int_auto(tokens[rssi_block + 5])
                r0 = rssi_block + 6
                r1 = r0 + rn
                if r1 <= len(tokens):
                    raw = np.array([_parse_int_auto(t) for t in tokens[r0:r1]], dtype=np.float64)
                    if rn == n:
                        rssi = raw * r_scale + r_offset
                    else:
                        # best-effort resample to distance count
                        x_src = np.linspace(0.0, 1.0, rn)
                        x_dst = np.linspace(0.0, 1.0, n)
                        rssi = np.interp(x_dst, x_src, raw * r_scale + r_offset)
            except Exception:
                pass

        valid = np.isfinite(distances) & (distances > 0.0)
        if not np.any(valid):
            return np.array([], dtype=np.float32).reshape(0, 4)

        d = distances[valid]
        a = angles_rad[valid]
        # SOPAS-like Cartesian orientation:
        # center beam points "forward" from the sensor (here: +Z),
        # lateral axis is X.
        x = d * np.sin(a)
        y = np.zeros_like(x)
        z = d * np.cos(a)
        r = rssi[valid]
        pts = np.column_stack([x, y, z, r]).astype(np.float32)
        return pts

    def _parse_lmdscandata_binary_dist1(self, telegram: str) -> np.ndarray:
        """
        Parse CoLa-B DIST1 block directly from raw bytes.
        Expected binary fields after DIST1 marker:
          scale(float32), offset(float32), start_angle(int32, 1/10000 deg),
          step(uint16, 1/10000 deg), count(uint16), distances(uint16[count]).
        """
        try:
            raw = telegram.encode("latin1", errors="ignore")
        except Exception:
            return np.array([], dtype=np.float32).reshape(0, 4)

        m = raw.find(b"DIST1")
        if m < 0:
            return np.array([], dtype=np.float32).reshape(0, 4)

        best = None
        best_score = -1e18
        # Try small offsets to account for optional channel flags between marker and fields.
        for off in range(0, 12):
            p = m + 5 + off
            if p + 16 >= len(raw):
                continue
            try:
                scale = struct.unpack(">f", raw[p:p + 4])[0]
                offset = struct.unpack(">f", raw[p + 4:p + 8])[0]
                start_raw = struct.unpack(">i", raw[p + 8:p + 12])[0]
                step_raw = struct.unpack(">H", raw[p + 12:p + 14])[0]
                n = struct.unpack(">H", raw[p + 14:p + 16])[0]
            except Exception:
                continue

            if not _is_finite_reasonable(scale, 1e-6, 1000.0):
                continue
            if not _is_finite_reasonable(offset, -100000.0, 100000.0):
                continue
            if n < 20 or n > 4000:
                continue
            if step_raw < 1 or step_raw > 20000:
                continue

            q = p + 16
            need = q + 2 * n
            if need > len(raw):
                continue

            # Evaluate both byte orders; pick physically plausible one.
            for endian in (">", "<"):
                try:
                    dist_vals = np.frombuffer(raw[q:need], dtype=f"{endian}u2").astype(np.float64)
                except Exception:
                    continue
                d = dist_vals * scale + offset
                if d.size < 20:
                    continue
                valid = np.isfinite(d) & (d > 50.0) & (d < 50000.0)
                valid_ratio = float(np.mean(valid)) if d.size > 0 else 0.0
                if valid_ratio < 0.8:
                    continue
                dv = np.abs(np.diff(d[valid]))
                smooth_penalty = float(np.mean(dv)) if dv.size > 0 else 0.0
                score = (d[valid].size * 5.0) + (valid_ratio * 500.0) - smooth_penalty
                if score > best_score:
                    best_score = score
                    best = (d[valid], start_raw, step_raw)

        if best is None:
            return np.array([], dtype=np.float32).reshape(0, 4)

        d, start_raw, step_raw = best
        n = d.shape[0]
        angles_deg = (start_raw + np.arange(n, dtype=np.float64) * float(step_raw)) / 10000.0
        angles_deg = angles_deg + float(self.scan_angle_offset_deg)
        a = np.deg2rad(angles_deg)

        x = d * np.sin(a)
        y = np.zeros_like(x)
        z = d * np.cos(a)
        r = np.zeros_like(x)
        pts = np.column_stack([x, y, z, r]).astype(np.float32)
        return pts

    def _normalize_mixed_msg(self, msg: str) -> str:
        """
        Recover scan telegram start token without destroying binary payload layout.
        """
        if not msg:
            return msg
        # Keep original bytes (latin1 string) intact and only crop prefix.
        for token in ("sSN LMDscandata", "sRA LMDscandata"):
            pos = msg.find(token)
            if pos >= 0:
                return msg[pos:]
        return msg

    def _safe_log_snippet(self, msg: str, limit: int = 180) -> str:
        if not msg:
            return ""
        out = []
        for ch in msg[:limit]:
            c = ord(ch)
            out.append(ch if 32 <= c <= 126 else ".")
        return "".join(out)

    def _parse_lmdscandata_binary_fallback(self, telegram: str) -> np.ndarray:
        """
        Fallback for binary/mixed CoLa-B LMDscandata payloads where DIST data is not
        represented as ASCII hex tokens. Heuristically extracts the longest plausible
        uint16 distance run after 'DIST1' marker.
        """
        try:
            raw = telegram.encode("latin1", errors="ignore")
        except Exception:
            return np.array([], dtype=np.float32).reshape(0, 4)

        marker_pos = raw.find(b"DIST1")
        if marker_pos < 0:
            marker_pos = raw.find(b"DIST")
        if marker_pos < 0:
            return np.array([], dtype=np.float32).reshape(0, 4)

        scan = raw[marker_pos:]
        if len(scan) < 40:
            return np.array([], dtype=np.float32).reshape(0, 4)

        best_start = -1
        best_len = 0
        best_u16: list[int] = []
        # Try both byte alignments. Mixed payload may shift by one byte.
        for off in (0, 1):
            u16 = []
            for i in range(off, len(scan) - 1, 2):
                u16.append((scan[i] << 8) | scan[i + 1])
            if not u16:
                continue
            i = 0
            n_u16 = len(u16)
            while i < n_u16:
                if not (200 <= u16[i] <= 30000):
                    i += 1
                    continue
                j = i + 1
                while j < n_u16:
                    v_prev = u16[j - 1]
                    v_cur = u16[j]
                    if not (200 <= v_cur <= 30000):
                        break
                    if abs(int(v_cur) - int(v_prev)) > 3000:
                        break
                    j += 1
                run_len = j - i
                if run_len > best_len:
                    best_len = run_len
                    best_start = i
                    best_u16 = u16
                i = j + 1

        if best_start < 0 or best_len < 20:
            logger.debug("LMS4000 binary fallback: no plausible distance run found")
            return np.array([], dtype=np.float32).reshape(0, 4)

        distances = np.array(best_u16[best_start:best_start + best_len], dtype=np.float64)
        valid = np.isfinite(distances) & (distances > 0.0)
        if not np.any(valid):
            return np.array([], dtype=np.float32).reshape(0, 4)
        d = distances[valid]
        if d.shape[0] < 20:
            return np.array([], dtype=np.float32).reshape(0, 4)

        # Remove broken chunks often caused by mixed binary fields:
        # keep the longest smooth contiguous run.
        dd = np.abs(np.diff(d))
        break_idx = np.where(dd > 1400.0)[0]
        if break_idx.size > 0:
            starts = np.concatenate([[0], break_idx + 1])
            ends = np.concatenate([break_idx + 1, [d.shape[0]]])
            lengths = ends - starts
            k = int(np.argmax(lengths))
            d = d[starts[k]:ends[k]]
            if d.shape[0] < 20:
                return np.array([], dtype=np.float32).reshape(0, 4)

        # Fallback angular model for visualization when exact angle metadata is binary.
        # LMS4000 typical FOV is around 70 deg; keep a conservative default.
        fov_deg = 70.0
        a = np.deg2rad(
            np.linspace(-fov_deg / 2.0, fov_deg / 2.0, d.shape[0], dtype=np.float64)
            + float(self.scan_angle_offset_deg)
        )
        # Same orientation as in _parse_lmdscandata.
        x = d * np.sin(a)
        y = np.zeros_like(x)
        z = d * np.cos(a)
        r = np.zeros_like(x)
        pts = np.column_stack([x, y, z, r]).astype(np.float32)
        logger.debug("LMS4000 binary fallback parsed %s points", pts.shape[0])
        return pts

    def receive_point_cloud(self, num_scans: int = 1) -> np.ndarray:
        # Serialize socket I/O for this receiver; concurrent readers can corrupt CoLa framing.
        if not self._io_lock.acquire(timeout=max(1.0, self.timeout_s)):
            return np.array([], dtype=np.float32).reshape(0, 4)
        try:
            if not self.connected:
                if not self._reconnect_once():
                    return np.array([], dtype=np.float32).reshape(0, 4)
            chunks = []
            try:
                need = max(1, int(num_scans))
                non_scan_msgs = 0
                while len(chunks) < need:
                    msg = self._recv_cola_ascii_once(timeout_s=self.timeout_s)
                    if not msg and self.use_poll_fallback:
                        # Optional fallback: poll one scan on demand.
                        msg = self._request_scan_once()
                    if not msg:
                        break
                    scan_msg = self._normalize_mixed_msg(msg)
                    if scan_msg.startswith("sSN LMDscandata") or scan_msg.startswith("sRA LMDscandata"):
                        pts = self._parse_lmdscandata(scan_msg)
                        if pts.shape[0] > 0:
                            chunks.append(pts)
                            self.total_telegrams_received += 1
                            self.last_receive_ts = time.time()
                    else:
                        non_scan_msgs += 1
                        if non_scan_msgs <= 2:
                            logger.debug("LMS4000 non-scan telegram: %s", self._safe_log_snippet(msg))
                            if "LMDscandata" in msg:
                                logger.debug(
                                    "LMS4000 appears to stream binary CoLa-B scan payload. "
                                    "Automatic CoLa-A switch attempted; if still no points, set protocol to CoLa-A in SOPAS."
                                )
                if not chunks:
                    logger.debug("LMS4000 receive_point_cloud: no scan telegram parsed (need=%s)", need)
                    return np.array([], dtype=np.float32).reshape(0, 4)
                return np.vstack(chunks).astype(np.float32)
            except (ConnectionResetError, BrokenPipeError, TimeoutError, OSError) as exc:
                self.connected = False
                self._safe_close()
                now = time.time()
                if self._last_socket_drop_log_ts is None or (now - self._last_socket_drop_log_ts) >= 15.0:
                    logger.warning("LMS4000 socket dropped: %s", _safe_exc_text(exc))
                    self._last_socket_drop_log_ts = now
                self._reconnect_once()
                return np.array([], dtype=np.float32).reshape(0, 4)
            except Exception as exc:
                logger.error("LMS4000 receive_point_cloud failed: %s", _safe_exc_text(exc))
                return np.array([], dtype=np.float32).reshape(0, 4)
        finally:
            self._io_lock.release()

    def get_metrics(self) -> dict:
        return {
            "sensor_ip": self.sensor_ip,
            "sensor_port": self.sensor_port,
            "connected": self.connected,
            "total_telegrams_received": self.total_telegrams_received,
            "last_receive_ts": self.last_receive_ts,
        }

    def get_info(self) -> dict:
        return {
            "sensor_ip": self.sensor_ip,
            "sensor_port": self.sensor_port,
            "listening": self.connected,
            "format": "lmdscandata",
            "protocol": "tcp",
        }
