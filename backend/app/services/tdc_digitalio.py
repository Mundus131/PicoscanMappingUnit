import importlib
import logging
import threading
import sys
from pathlib import Path
from typing import Optional

try:
    import grpc  # type: ignore
    _GRPC_AVAILABLE = True
except Exception:
    grpc = None  # type: ignore
    _GRPC_AVAILABLE = False

from google.protobuf import empty_pb2

from app.core.device_manager import device_manager
from app.services.tdc_token_manager import tdc_token_manager

logger = logging.getLogger(__name__)


def _ensure_proto_generated():
    proto_dir = Path(__file__).parent / "tdc_proto"
    proto_path = proto_dir / "digitalio-service.proto"
    pb2_path = proto_dir / "digitalio_service_pb2.py"
    pb2_grpc_path = proto_dir / "digitalio_service_pb2_grpc.py"
    if pb2_path.exists() and pb2_grpc_path.exists():
        try:
            proto_dir_str = str(proto_dir)
            if proto_dir_str not in sys.path:
                sys.path.insert(0, proto_dir_str)
            from app.services.tdc_proto import digitalio_service_pb2  # type: ignore
            from app.services.tdc_proto import digitalio_service_pb2_grpc  # type: ignore
            return digitalio_service_pb2, digitalio_service_pb2_grpc
        except Exception:
            pass
    try:
        try:
            from grpc_tools import protoc
        except Exception as exc:
            raise RuntimeError("grpcio-tools is required to generate TDC proto stubs") from exc

        if not proto_path.exists():
            raise FileNotFoundError(f"Missing proto file: {proto_path}")

        try:
            import importlib.resources as ir
            well_known = str(ir.files("grpc_tools") / "_proto")
        except Exception:
            well_known = None

        args = ["grpc_tools.protoc", f"-I{proto_dir}"]
        if well_known:
            args.append(f"-I{well_known}")
        args.extend(
            [
                f"--python_out={proto_dir}",
                f"--grpc_python_out={proto_dir}",
                str(proto_path),
            ]
        )
        result = protoc.main(args)
        if result != 0:
            raise RuntimeError(f"protoc failed with code {result}")

        proto_dir_str = str(proto_dir)
        if proto_dir_str not in sys.path:
            sys.path.insert(0, proto_dir_str)

        digitalio_service_pb2 = importlib.import_module("app.services.tdc_proto.digitalio_service_pb2")
        digitalio_service_pb2_grpc = importlib.import_module("app.services.tdc_proto.digitalio_service_pb2_grpc")
        return digitalio_service_pb2, digitalio_service_pb2_grpc
    except Exception as exc:
        raise RuntimeError(f"Failed to generate TDC proto stubs: {exc}") from exc


class TdcDigitalIOClient:
    def __init__(self):
        self._lock = threading.Lock()
        self._channel = None
        self._stub = None
        self._config_cache: dict = {}
        self._pb2, self._pb2_grpc = _ensure_proto_generated()

    def _get_config(self) -> dict:
        cfg = device_manager.tdc_settings or {}
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "ip_address": cfg.get("ip_address", "192.168.0.100"),
            "port": int(cfg.get("port", 8081)),
            "timeout_s": float(cfg.get("grpc_timeout_s", 5.0)),
        }

    def _ensure_channel(self):
        if not _GRPC_AVAILABLE:
            raise RuntimeError("grpcio is not installed")
        config = self._get_config()
        if config != self._config_cache or self._channel is None:
            target = f"{config['ip_address']}:{config['port']}"
            self._channel = grpc.insecure_channel(target)
            self._stub = self._pb2_grpc.DigitalIOStub(self._channel)
            self._config_cache = config
            logger.info("TDC DigitalIO connected to %s", target)

    def _metadata(self):
        token = tdc_token_manager.get_token()
        if not token:
            return []
        return [("authorization", f"Bearer {token}")]

    def read(self, name: str) -> Optional[int]:
        with self._lock:
            self._ensure_channel()
            config = self._config_cache
            req = self._pb2.DigitalIOReadRequest(name=name)
            try:
                resp = self._stub.Read(req, metadata=self._metadata(), timeout=config["timeout_s"])
                return int(resp.state)
            except Exception as exc:
                logger.warning("TDC DigitalIO read failed: %s", exc)
                return None

    def set_direction(self, name: str, direction: int) -> bool:
        with self._lock:
            self._ensure_channel()
            config = self._config_cache
            req = self._pb2.DigitalIOSetDirectionRequest(name=name, direction=direction)
            try:
                self._stub.SetDirection(req, metadata=self._metadata(), timeout=config["timeout_s"])
                return True
            except Exception as exc:
                logger.warning("TDC DigitalIO set_direction failed: %s", exc)
                return False

    def list_devices(self):
        with self._lock:
            self._ensure_channel()
            config = self._config_cache
            try:
                resp = self._stub.ListDevices(empty_pb2.Empty(), metadata=self._metadata(), timeout=config["timeout_s"])
                return resp.devices
            except Exception as exc:
                logger.warning("TDC DigitalIO list_devices failed: %s", exc)
                return []


tdc_digitalio_client = TdcDigitalIOClient()


def tdc_grpc_available() -> bool:
    return _GRPC_AVAILABLE
