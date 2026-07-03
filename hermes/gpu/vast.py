"""Minimal Vast.ai API client: discover running instances and their SSH
endpoints, stop instances. Manual SSH-string paste remains the fallback."""

from __future__ import annotations

import httpx

API_BASE = "https://console.vast.ai/api/v0"


class VastError(Exception):
    pass


def _client(api_key: str) -> httpx.Client:
    if not api_key:
        raise VastError("no vast_api_key in config — `config set vast_api_key <key>`")
    return httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=30,
    )


def list_instances(api_key: str) -> list[dict]:
    with _client(api_key) as client:
        try:
            resp = client.get("/instances/", params={"owner": "me"})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise VastError(f"Vast.ai API error: {e}") from e
    rows = resp.json().get("instances", [])
    out = []
    for r in rows:
        out.append(
            {
                "id": r.get("id"),
                "status": r.get("actual_status"),
                "gpu_name": r.get("gpu_name"),
                "num_gpus": r.get("num_gpus"),
                "dph": r.get("dph_total"),
                "ssh_host": r.get("ssh_host"),
                "ssh_port": r.get("ssh_port"),
            }
        )
    return out


def running_instances(api_key: str) -> list[dict]:
    return [i for i in list_instances(api_key) if i["status"] == "running"]


def get_instance(api_key: str, instance_id: int) -> dict | None:
    """One instance by id (for polling a resume), or None if it's gone."""
    for inst in list_instances(api_key):
        if inst["id"] == instance_id:
            return inst
    return None


def stop_instance(api_key: str, instance_id: int) -> None:
    """Pause: stop compute but keep the disk (weights and built llama.cpp
    survive), so a later `start` resumes fast."""
    _set_state(api_key, instance_id, "stopped")


def start_instance(api_key: str, instance_id: int) -> None:
    """Resume a previously stopped instance onto the same persisted disk."""
    _set_state(api_key, instance_id, "running")


def _set_state(api_key: str, instance_id: int, desired: str) -> None:
    with _client(api_key) as client:
        try:
            resp = client.put(f"/instances/{instance_id}/", json={"state": desired})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise VastError(
                f"Vast.ai API error setting {instance_id} to {desired}: {e}"
            ) from e
