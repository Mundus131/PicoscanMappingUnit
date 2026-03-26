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


async def set_variable_value(node, value: Any, data_type: str):
    await node.write_value(ua.DataValue(ua.Variant(value, variant_type_for(data_type))))


async def main():
    server = Server()
    await server.init()
    server.set_endpoint(OPCUA_ENDPOINT)
    server.set_server_name("PicoScan Mapping Unit OPC UA")
    namespace_idx = await server.register_namespace(NAMESPACE_URI)
    objects = server.nodes.objects
    folders: dict[str, Any] = {}
    variables: dict[str, Any] = {}

    async with server:
        print(f"[opcua-server] listening on {OPCUA_ENDPOINT}", flush=True)
        while True:
            try:
                bundle = fetch_bundle()
                node_map = bundle.get("opcua_nodes") or {}
                for full_name, meta in node_map.items():
                    if not isinstance(meta, dict):
                        continue
                    parts = str(full_name or "").split(".")
                    if len(parts) < 2:
                        continue
                    folder_path = ".".join(parts[:-1])
                    leaf = parts[-1]
                    parent = await ensure_path(objects, folder_path, namespace_idx, folders)
                    node = variables.get(full_name)
                    value = meta.get("value")
                    data_type = str(meta.get("data_type") or "String")
                    if node is None:
                        node = await parent.add_variable(namespace_idx, leaf, value)
                        await node.set_writable(False)
                        variables[full_name] = node
                    await set_variable_value(node, value, data_type)
                print(
                    f"[opcua-server] updated {len(node_map)} node(s), active_app={bundle.get('active_app')}",
                    flush=True,
                )
            except Exception as exc:
                print(f"[opcua-server] update loop error: {exc}", flush=True)
            await asyncio.sleep(max(0.2, POLL_INTERVAL_S))


if __name__ == "__main__":
    asyncio.run(main())
