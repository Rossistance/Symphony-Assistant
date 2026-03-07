"""Simple local simulation UI + API for WhatsApp/agent/drive return workflow."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.api.http_surfaces import ApiResponse

LOGGER = logging.getLogger(__name__)


@dataclass
class SimulationStateStore:
    """In-memory simulation state mirrored to a temp JSON file for local QA."""

    path: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "symphony_simulation_state.json")
    output_root: Path = field(default_factory=lambda: Path(os.getenv("SIMULATION_OUTPUT_DIR", "outputs")))

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._state = self._fresh_state()
        self._persist()

    def _fresh_state(self) -> dict[str, Any]:
        return {
            "task_id": None,
            "status": "idle",
            "created_at": None,
            "prompt": "",
            "whatsapp": {"messages": []},
            "agent": {"events": []},
            "orchestration": {"current_stage": "idle", "completed_stages": [], "failed_stage": None, "stages": []},
            "approval": {"status": "pending", "note": ""},
            "drive": {"root": "Simulated Drive", "folders": [], "files": []},
            "artifacts": {"task_dir": None, "files": []},
            "return_message": None,
            "error": None,
            "temp_db_file": str(self.path),
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._state, indent=2, sort_keys=True), encoding="utf-8")

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._state = self._fresh_state()
            self._persist()
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._state))

    def initialize_task(self, *, phone: str, message: str) -> dict[str, Any]:
        with self._lock:
            task_id = f"sim-{int(time.time())}"
            self._state.update(
                {
                    "task_id": task_id,
                    "status": "initiated",
                    "created_at": time.time(),
                    "prompt": message,
                    "approval": {"status": "pending", "note": ""},
                    "drive": {"root": "Simulated Drive", "folders": [], "files": []},
                    "return_message": None,
                    "error": None,
                }
            )
            self._state["whatsapp"] = {
                "messages": [
                    {"direction": "inbound", "from": phone, "text": message},
                    {
                        "direction": "outbound",
                        "from": "assistant",
                        "text": "Task received. Starting lead/sub-agent processing simulation.",
                    },
                ]
            }
            self._state["agent"] = {"events": []}
            self._state["orchestration"] = {
                "current_stage": "intake_received",
                "completed_stages": ["intake_received"],
                "failed_stage": None,
                "stages": [],
            }
            self._persist()
            return self.snapshot()

    def _task_output_dir(self, task_id: str) -> Path:
        task_dir = self.output_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    def _record_stage(self, *, stage: str, status: str, detail: str, payload_summary: dict[str, Any] | None = None) -> None:
        orchestration = self._state.setdefault(
            "orchestration", {"current_stage": "idle", "completed_stages": [], "failed_stage": None, "stages": []}
        )
        orchestration["current_stage"] = stage
        stages = orchestration.setdefault("stages", [])
        stages.append({"stage": stage, "status": status, "detail": detail, "at": time.time(), "payload": payload_summary or {}})
        if status == "completed":
            completed = orchestration.setdefault("completed_stages", [])
            if stage not in completed:
                completed.append(stage)
        if status == "failed":
            orchestration["failed_stage"] = stage

    def _save_artifact(self, *, task_dir: Path, name: str, payload: Any) -> str:
        target = task_dir / name
        if isinstance(payload, (dict, list)):
            target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        else:
            target.write_text(str(payload), encoding="utf-8")
        artifacts = self._state.setdefault("artifacts", {"task_dir": str(task_dir), "files": []})
        artifacts["task_dir"] = str(task_dir)
        files = artifacts.setdefault("files", [])
        file_path = str(target)
        if file_path not in files:
            files.append(file_path)
        return file_path

    def _call_real_grok(self, *, prompt: str, task_id: str, task_dir: Path) -> dict[str, Any]:
        api_key = os.getenv("GROK_API_KEY", "").strip() or os.getenv("XAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("GROK_API_KEY (or XAI_API_KEY) is required for real Grok mode")

        model_name = os.getenv("GROK_MODEL", "grok-2-latest")
        endpoint = os.getenv("GROK_API_URL", "https://api.x.ai/v1/chat/completions")
        body = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You are a lead orchestration agent. Return concise task output."},
                {"role": "user", "content": prompt or "Generate execution summary and return payload."},
            ],
            "temperature": 0.2,
            "max_tokens": 800,
            "user": task_id,
        }
        self._save_artifact(task_dir=task_dir, name="grok_request_summary.json", payload={"endpoint": endpoint, "body": body})

        attempts = int(os.getenv("GROK_API_MAX_RETRIES", "3"))
        last_error: ValueError | None = None
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "X-Run-Id": task_id,
                },
                method="POST",
            )
            LOGGER.info("grok.request.start", extra={"task_id": task_id, "attempt": attempt, "endpoint": endpoint})
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    self._save_artifact(task_dir=task_dir, name="grok_response.json", payload=payload)
                    LOGGER.info("grok.request.success", extra={"task_id": task_id, "attempt": attempt})
                    break
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
                last_error = ValueError(f"Grok request failed ({exc.code}): {raw}")
            except urllib.error.URLError as exc:
                last_error = ValueError(f"Grok connection error: {exc.reason}")

            LOGGER.warning("grok.request.retry", extra={"task_id": task_id, "attempt": attempt, "error": str(last_error)})
            if attempt < attempts:
                time.sleep(min(2**attempt, 5))
        else:
            raise last_error or ValueError("Grok request failed")

        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("Grok returned no choices")
        message = choices[0].get("message", {})
        content = str(message.get("content") or "").strip()
        if not content:
            raise ValueError("Grok returned empty content")
        request_id = str(payload.get("id") or "")
        return {
            "provider": "grok",
            "content": content,
            "request_id": request_id,
            "model": str(payload.get("model") or model_name),
            "raw": payload,
        }

    def run_automatic_orchestration(
        self,
        *,
        use_grok: bool = True,
        require_grok: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            if not self._state["task_id"]:
                raise ValueError("No simulation task has been started")
            task_id = str(self._state["task_id"])
            task_dir = self._task_output_dir(task_id)
            prompt = str(self._state.get("prompt") or "")

            self._save_artifact(task_dir=task_dir, name="normalized_intake_payload.json", payload={"task_id": task_id, "prompt": prompt})
            self._record_stage(stage="intake_received", status="completed", detail="Simulated WhatsApp intake recorded.")

            lead_summary = "Lead module decomposed request into retrieval + drafting workstreams."
            sub_summary = "Worker module created structured execution context for downstream synthesis."
            model_summary = "Grok disabled for this run."
            provider_used = "none"
            provider_request_id = ""
            provider_model = ""

            self._record_stage(stage="planning", status="completed", detail=lead_summary)
            self._save_artifact(
                task_dir=task_dir,
                name="module_planning_output.json",
                payload={"lead_summary": lead_summary, "worker_summary": sub_summary, "prompt": prompt},
            )

            if use_grok:
                self._record_stage(stage="grok_request", status="in_progress", detail="Calling real Grok API.")
                try:
                    generated = self._call_real_grok(prompt=prompt, task_id=task_id, task_dir=task_dir)
                    model_summary = generated["content"]
                    provider_used = generated["provider"]
                    provider_request_id = str(generated.get("request_id") or "")
                    provider_model = str(generated.get("model") or "")
                    self._record_stage(
                        stage="grok_request",
                        status="completed",
                        detail="Grok response received.",
                        payload_summary={"provider": provider_used, "model": provider_model, "request_id": provider_request_id},
                    )
                except ValueError as exc:
                    self._record_stage(stage="grok_request", status="failed", detail=str(exc))
                    if require_grok:
                        self._state["error"] = str(exc)
                        self._state["status"] = "failed"
                        self._persist()
                        raise

            final_deliverable = (
                f"Task {task_id} deliverable\n\n"
                f"User request: {prompt}\n\n"
                f"Planning: {lead_summary}\n"
                f"Module handoff: {sub_summary}\n\n"
                f"Grok output:\n{model_summary}\n"
            )
            deliverable_path = self._save_artifact(task_dir=task_dir, name="final_deliverable.txt", payload=final_deliverable)
            manifest_path = self._save_artifact(
                task_dir=task_dir,
                name="execution_manifest.json",
                payload={
                    "task_id": task_id,
                    "status": "completed",
                    "provider": provider_used,
                    "provider_model": provider_model,
                    "provider_request_id": provider_request_id,
                    "artifacts": self._state.get("artifacts", {}).get("files", []),
                },
            )
            self._record_stage(stage="finalize", status="completed", detail="Final deliverable persisted.")

            self._state["agent"] = {
                "events": [
                    {"agent": "lead", "status": "started", "detail": "Lead agent accepted task."},
                    {"agent": "lead", "status": "planning", "detail": lead_summary},
                    {"agent": "sub-agent", "status": "running", "detail": sub_summary},
                    {
                        "agent": "lead",
                        "status": "synthesized",
                        "detail": model_summary,
                        "provider": provider_used,
                        "provider_model": provider_model,
                        "provider_request_id": provider_request_id,
                    },
                    {
                        "agent": "lead",
                        "status": "completed",
                        "detail": f"Pipeline completed. Final deliverable saved to {deliverable_path}",
                    },
                ]
            }
            self._state["approval"] = {"status": "approved", "note": "Auto-approved by automatic runtime."}
            self._state["drive"] = {
                "root": "Simulated Drive",
                "folders": ["returns", f"task-{task_id}"],
                "files": [{"name": "final_deliverable.txt", "url": f"file://{deliverable_path}"}],
            }
            self._state["return_message"] = f"Done — I completed {prompt}. Deliverable saved at {deliverable_path}"
            self._state["whatsapp"]["messages"].append(
                {"direction": "outbound", "from": "assistant", "text": self._state["return_message"]}
            )
            self._state["status"] = "completed"
            self._state["error"] = None
            self._state.setdefault("artifacts", {})["manifest_path"] = manifest_path
            self._persist()
            return self.snapshot()

    # Backward-compatible endpoint behavior for manual debug/testing APIs.
    def apply_agent_simulation(
        self,
        *,
        use_groq: bool,
        require_groq: bool = False,
        use_real_groq_api: bool = True,
    ) -> dict[str, Any]:
        del use_real_groq_api
        return self.run_automatic_orchestration(use_grok=use_groq, require_grok=require_groq)

    def apply_approval(self, *, approved: bool, note: str) -> dict[str, Any]:
        with self._lock:
            if not self._state["task_id"]:
                raise ValueError("No simulation task has been started")
            self._state["approval"] = {"status": "approved" if approved else "rejected", "note": note}
            self._state["status"] = "approved" if approved else "rejected"
            self._persist()
            return self.snapshot()

    def publish_return(self) -> dict[str, Any]:
        with self._lock:
            if self._state.get("approval", {}).get("status") != "approved":
                raise ValueError("Task must be approved before publishing a return")
            task_id = str(self._state["task_id"])
            file_name = f"{task_id}-result.txt"
            folder_name = f"task-{task_id}"
            share_url = f"https://drive.example/simulated/{task_id}/{file_name}"
            self._state["drive"] = {
                "root": "Simulated Drive",
                "folders": ["returns", folder_name],
                "files": [{"name": file_name, "url": share_url}],
            }
            reply_text = f"Your return is ready: {share_url}"
            self._state["whatsapp"]["messages"].append(
                {"direction": "outbound", "from": "assistant", "text": reply_text}
            )
            self._state["return_message"] = reply_text
            self._state["status"] = "completed"
            self._persist()
            return self.snapshot()

    def apply_orchestration_result(self, *, orchestration: ApiResponse) -> dict[str, Any]:
        with self._lock:
            body = orchestration.body
            if orchestration.status_code >= 400:
                self._state["status"] = "failed"
                self._state["error"] = str(body.get("error") or "orchestration_failed")
                self._persist()
                return self.snapshot()

            deliverables = body.get("deliverables") or []
            files = []
            for item in deliverables:
                share_url = str(item.get("share_url") or item.get("access_reference") or "")
                title = str(item.get("title") or item.get("artifact_id") or "deliverable")
                files.append({"name": title, "url": share_url})

            self._state["approval"] = {"status": "approved", "note": "Auto-approved by real orchestration chain."}
            self._state["drive"] = {
                "root": "Simulated Drive",
                "folders": ["returns", f"task-{self._state['task_id']}"],
                "files": files,
            }
            completion_message = str(body.get("completion_message") or "Return complete.")
            self._state["whatsapp"]["messages"].append(
                {"direction": "outbound", "from": "assistant", "text": completion_message}
            )
            self._state["return_message"] = completion_message
            self._state["status"] = "completed"
            self._state["error"] = None
            self._persist()
            return self.snapshot()


SIMULATION_UI_HTML = """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Symphony Workflow Simulator</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #f5f7fb; color: #1d2330; }
    header { background: #111827; color: white; padding: 12px 16px; }
    .grid { display: grid; grid-template-columns: repeat(5, minmax(220px, 1fr)); gap: 12px; padding: 12px; }
    .panel { background: white; border-radius: 8px; padding: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.12); min-height: 320px; }
    h3 { margin-top: 0; font-size: 14px; }
    textarea, input { width: 100%; margin-bottom: 6px; }
    button { margin-right: 4px; margin-bottom: 6px; }
    pre { white-space: pre-wrap; background: #f1f5f9; padding: 6px; border-radius: 4px; max-height: 220px; overflow: auto; }
    small { color: #475569; }
  </style>
</head>
<body>
<header>
  <strong>Symphony Local Web UI Test Suite</strong> — 5-window simulation for WhatsApp → Agent → Approval → Drive → WhatsApp Return
</header>
<div class=\"grid\">
  <section class=\"panel\">
    <h3>1) Simulated WhatsApp Initiation</h3>
    <input id=\"phone\" value=\"+15550001111\" />
    <textarea id=\"prompt\" rows=\"5\">Please process a return request and share the final file.</textarea>
    <label><input type=\"checkbox\" id=\"autoProcess\" checked /> Auto-run full workflow after start</label>
    <button onclick=\"startTask()\">Start Task</button>
    <button onclick=\"startAndAutoRun()\">Start + Auto Complete</button>
    <pre id=\"whatsapp\"></pre>
  </section>
  <section class=\"panel\">
    <h3>2) Agent / Sub-agent Status</h3>
    <label><input type=\"checkbox\" id=\"useGroq\" checked /> Use real Grok API generation</label>
    <label><input type=\"checkbox\" id=\"requireGroq\" checked /> Require GROK_API_KEY (fail if missing)</label>
    <label><input type=\"checkbox\" id=\"realGroq\" checked /> Use automatic end-to-end orchestration</label>
    <button onclick=\"runAgents()\">Run Agent Workflow</button>
    <pre id=\"agents\"></pre>
  </section>
  <section class=\"panel\">
    <h3>3) Simulated Approval</h3>
    <input id=\"approvalNote\" value=\"Looks good, approve and return to user.\" />
    <button onclick=\"approveTask(true)\">Approve</button>
    <button onclick=\"approveTask(false)\">Reject</button>
    <pre id=\"approval\"></pre>
  </section>
  <section class=\"panel\">
    <h3>4) Simulated Drive Tree</h3>
    <button onclick=\"publishResult()\">Publish Approved Result</button>
    <pre id=\"drive\"></pre>
  </section>
  <section class=\"panel\">
    <h3>5) WhatsApp Return Reply</h3>
    <button onclick=\"refreshState()\">Refresh</button>
    <pre id=\"reply\"></pre>
    <small id=\"dbFile\"></small>
  </section>
</div>
<script>
async function callApi(path, payload = null) {
  const res = await fetch(path, {
    method: payload ? 'POST' : 'GET',
    headers: payload ? { 'Content-Type': 'application/json' } : {},
    body: payload ? JSON.stringify(payload) : null,
  });
  return res.json();
}

function render(state) {
  document.getElementById('whatsapp').textContent = JSON.stringify(state.whatsapp, null, 2);
  document.getElementById('agents').textContent = JSON.stringify(state.agent, null, 2);
  document.getElementById('approval').textContent = JSON.stringify(state.approval, null, 2);
  document.getElementById('drive').textContent = JSON.stringify(state.drive, null, 2);
  document.getElementById('reply').textContent = JSON.stringify({status: state.status, error: state.error, return_message: state.return_message, messages: state.whatsapp.messages}, null, 2);
  document.getElementById('dbFile').textContent = `Temp DB file: ${state.temp_db_file}`;
  sessionStorage.setItem('sim_state', JSON.stringify(state));
}

function payloadFromUi(autoProcessValue) {
  return {
    phone: document.getElementById('phone').value,
    message: document.getElementById('prompt').value,
    auto_process: autoProcessValue,
    use_grok: document.getElementById('useGroq').checked,
    require_grok: document.getElementById('requireGroq').checked,
    approval_note: document.getElementById('approvalNote').value,
  };
}

async function refreshState() { render(await callApi('/simulator/api/state')); }
async function startTask() { render(await callApi('/simulator/api/whatsapp-init', payloadFromUi(document.getElementById('autoProcess').checked))); }
async function startAndAutoRun() { render(await callApi('/simulator/api/whatsapp-init', payloadFromUi(true))); }
async function runAgents() {
  render(await callApi('/simulator/api/agent-run', {
    use_grok: document.getElementById('useGroq').checked,
    require_grok: document.getElementById('requireGroq').checked,
  }));
}
async function approveTask(approved) {
  render(await callApi('/simulator/api/approve', {approved, note: document.getElementById('approvalNote').value}));
}
async function publishResult() { render(await callApi('/simulator/api/publish', {})); }
window.addEventListener('beforeunload', () => {
  navigator.sendBeacon('/simulator/api/reset');
  sessionStorage.removeItem('sim_state');
});
refreshState();
</script>
</body>
</html>
"""
