"""Verification helper for automatic simulator orchestration with real Grok API."""

from __future__ import annotations

import json
import os
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import create_app, wire_runtime_dependencies


def main() -> int:
    if not (os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")):
        raise SystemExit("Set GROK_API_KEY or XAI_API_KEY before running this verification script.")

    os.environ.setdefault("SIMULATION_OUTPUT_DIR", "outputs")
    runtime = wire_runtime_dependencies()
    client = create_app(runtime=runtime).test_client()

    response = client.post(
        "/simulator/api/whatsapp-init",
        json={
            "phone": "+15550001111",
            "message": "Create a concise final deliverable from this intake.",
            "auto_process": True,
            "use_grok": True,
            "require_grok": True,
        },
    )
    payload = response.get_json() or {}
    if response.status_code != 200:
        raise SystemExit(f"Run failed with status {response.status_code}: {json.dumps(payload, indent=2)}")

    if payload.get("status") != "completed":
        raise SystemExit(f"Run did not complete: {json.dumps(payload, indent=2)}")

    artifacts = payload.get("artifacts", {}).get("files", [])
    task_dir = payload.get("artifacts", {}).get("task_dir")
    if not task_dir:
        raise SystemExit(f"No task artifact directory captured: {json.dumps(payload, indent=2)}")

    task_path = Path(task_dir)
    required = ["grok_response.json", "module_planning_output.json", "final_deliverable.txt", "execution_manifest.json"]
    missing = [name for name in required if not (task_path / name).exists()]
    if missing:
        raise SystemExit(f"Missing required artifacts: {missing} in {task_path}")

    print("Verification succeeded")
    print(f"Task ID: {payload.get('task_id')}")
    print(f"Status: {payload.get('status')}")
    print(f"Output directory: {task_path}")
    print(f"Artifacts tracked: {len(artifacts)}")
    print(f"Return message: {payload.get('return_message')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
