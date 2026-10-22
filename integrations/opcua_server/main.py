from __future__ import annotations

import asyncio
import os
from typing import Any

import requests
from asyncua import Server, ua


BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://backend:8000/api/v1").rstrip("/")
OPCUA_ENDPOINT = os.getenv("OPCUA_ENDPOINT", "opc.tcp://0.0.0.0:4840/freeopcua/server/")
TOPIC_ROOT = os.getenv("MQTT_TOPIC_ROOT", "picocan/pmu")
NAMESPACE_URI = os.getenv("OPCUA_NAMESPACE_URI", "urn:picocan:mapping-unit")
POLL_INTERVAL_S = float(os.getenv("POLL_INTERVAL_S", "1.0"))


def fetch_bundle() -> dict[str, Any]:
    response = requests.get(
        f"{BACKEND_BASE_URL}/integration/active-bundle",
        params={"topic_root": TOPIC_ROOT, "namespace_uri": NAMESPACE_URI},
        timeout=5.0,
    )
    response.raise_for_status()
    return response.json()


async def ensure_path(objects_node, path: str, namespace_idx: int, folders: dict[str, Any]):
    current = objects_node
    prefix_parts: list[str] = []
    for part in path.split("."):
        prefix_parts.append(part)
        key = ".".join(prefix_parts)
        node = folders.get(key)
        if node is None:
            node = await current.add_object(namespace_idx, part)
            folders[key] = node
        current = node
    return current


def variant_type_for(data_type: str) -> ua.VariantType:
    mapping = {
        "Boolean": ua.VariantType.Boolean,
        "Int64": ua.VariantType.Int64,
        "Double": ua.VariantType.Double,
        "String": ua.VariantType.String,
    }
    return mapping.get(data_type, ua.VariantType.String)


async def set_variable_value(node, value: Any, data_type: str, status_code: int = ua.StatusCodes.Good):
    await node.write_value(ua.DataValue(
        ua.Variant(value, variant_type_for(data_type)),
        StatusCode_=ua.StatusCode(status_code),
    ))


def result_status_code(full_name: str, meta: dict[str, Any]) -> int:
    if full_name.startswith("Applications.") and meta.get("data_type") in {"Double", "Int64"}:
        quality = meta.get("quality_status", "unknown")
        if quality == "invalid":
            return ua.StatusCodes.BadDataLost
        if quality != "valid":
            return ua.StatusCodes.Uncertain
    return ua.StatusCodes.Good


class BundleWriter:
    def __init__(self, objects, namespace_idx: int):
        self.objects = objects
        self.namespace_idx = namespace_idx
        self.folders: dict[str, Any] = {}
        self.variables: dict[str, Any] = {}
        self.last_values: dict[str, dict[str, Any]] = {}

    async def mark_unavailable(self, names=None):
        # Do not read the old DataValue: reads can raise for an already bad node.
        for name in list(self.variables) if names is None else names:
            meta = self.last_values[name]
            value = meta["value"]
            if name.endswith((".result_ready", ".measurement_valid")):
                value = False
            try:
                await set_variable_value(
                    self.variables[name], value, meta["data_type"], ua.StatusCodes.BadNoCommunication,
                )
            except Exception as exc:
                # Continue invalidating other nodes even if one write fails.
                print(f"[opcua-server] cannot invalidate {name}: {exc}", flush=True)

    async def apply(self, bundle: dict[str, Any]):
        try:
            node_map = bundle.get("opcua_nodes")
            if not isinstance(node_map, dict):
                raise ValueError("Bundle must contain an opcua_nodes mapping")
            updated = set()
            for full_name, meta in node_map.items():
                if not isinstance(meta, dict):
                    continue
                parts = str(full_name or "").split(".")
                if len(parts) < 2 or "value" not in meta:
                    continue
                data_type = str(meta.get("data_type") or "String")
                value = meta["value"]
                node = self.variables.get(full_name)
                if node is None:
                    parent = await ensure_path(self.objects, ".".join(parts[:-1]), self.namespace_idx, self.folders)
                    node = await parent.add_variable(
                        self.namespace_idx, parts[-1], ua.Variant(value, variant_type_for(data_type)),
                    )
                    self.variables[full_name] = node
                cached = {"value": value, "data_type": data_type}
                self.last_values.setdefault(full_name, cached)
                await set_variable_value(node, value, data_type, result_status_code(full_name, meta))
                self.last_values[full_name] = cached
                updated.add(full_name)
            await self.mark_unavailable(set(self.variables) - updated)
        except Exception:
            await self.mark_unavailable()
            raise

    async def poll(self):
        try:
            bundle = await asyncio.to_thread(fetch_bundle)
            await self.apply(bundle)
            return bundle
        except Exception:
            await self.mark_unavailable()
            raise


async def main():
    server = Server()
    await server.init()
    server.set_endpoint(OPCUA_ENDPOINT)
    server.set_server_name("PicoScan Mapping Unit OPC UA")
    namespace_idx = await server.register_namespace(NAMESPACE_URI)
    objects = server.nodes.objects
    writer = BundleWriter(objects, namespace_idx)

    async with server:
        print(f"[opcua-server] listening on {OPCUA_ENDPOINT}", flush=True)
        while True:
            try:
                bundle = await writer.poll()
                node_map = bundle.get("opcua_nodes") or {}
                print(
                    f"[opcua-server] updated {len(node_map)} node(s), active_app={bundle.get('active_app')}",
                    flush=True,
                )
            except Exception as exc:
                print(f"[opcua-server] update loop error: {exc}", flush=True)
            await asyncio.sleep(max(0.2, POLL_INTERVAL_S))


if __name__ == "__main__":
    asyncio.run(main())
