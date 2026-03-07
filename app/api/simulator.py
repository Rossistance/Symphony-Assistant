"""Simple local simulation UI + API for WhatsApp/agent/drive return workflow."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.model_runtime import generate_response


@dataclass
class SimulationStateStore:
    """In-memory simulation state mirrored to a temp JSON file for local QA."""

    path: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "symphony_simulation_state.json")

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
            "approval": {"status": "pending", "note": ""},
            "drive": {"root": "Simulated Drive", "folders": [], "files": []},
            "return_message": None,
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
            self._persist()
            return self.snapshot()

    def apply_agent_simulation(self, *, use_groq: bool) -> dict[str, Any]:
        with self._lock:
            if not self._state["task_id"]:
                raise ValueError("No simulation task has been started")
            prompt = str(self._state.get("prompt") or "")
            lead_summary = "Lead agent decomposed request into retrieval + response workstreams."
            sub_summary = "Sub-agent compiled return handling checklist and status updates."
            model_summary = "Groq disabled for this run."
            if use_groq:
                if not os.getenv("GROQ_API_KEY"):
                    model_summary = "GROQ_API_KEY not set; using deterministic fallback summary."
                else:
                    generated = generate_response(
                        messages=[
                            {"role": "system", "content": "You are simulating internal orchestration status updates."},
                            {"role": "user", "content": prompt or "Generate a short simulation summary."},
                        ],
                        run_id=self._state["task_id"],
                        agent_id="sim-lead-agent",
                        task_type="simulation",
                        budget_context={"mode": "demo"},
                    )
                    model_summary = str(generated.get("content") or "No content returned by model adapter.")
            self._state["agent"] = {
                "events": [
                    {"agent": "lead", "status": "started", "detail": "Lead agent accepted task."},
                    {"agent": "lead", "status": "planning", "detail": lead_summary},
                    {"agent": "sub-agent", "status": "running", "detail": sub_summary},
                    {"agent": "lead", "status": "synthesized", "detail": model_summary},
                    {"agent": "lead", "status": "completed", "detail": "Ready for approval."},
                ]
            }
            self._state["status"] = "awaiting_approval"
            self._persist()
            return self.snapshot()

    def apply_approval(self, *, approved: bool, note: str) -> dict[str, Any]:
        with self._lock:
            if not self._state["task_id"]:
                raise ValueError("No simulation task has been started")
            self._state["approval"] = {
                "status": "approved" if approved else "rejected",
                "note": note,
            }
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
    <button onclick=\"startTask()\">Start Task</button>
    <pre id=\"whatsapp\"></pre>
  </section>
  <section class=\"panel\">
    <h3>2) Agent / Sub-agent Status</h3>
    <label><input type=\"checkbox\" id=\"useGroq\" checked /> Use Groq-backed generation if configured</label>
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
  document.getElementById('reply').textContent = JSON.stringify({status: state.status, return_message: state.return_message, messages: state.whatsapp.messages}, null, 2);
  document.getElementById('dbFile').textContent = `Temp DB file: ${state.temp_db_file}`;
  sessionStorage.setItem('sim_state', JSON.stringify(state));
}

async function refreshState() { render(await callApi('/simulator/api/state')); }
async function startTask() {
  const state = await callApi('/simulator/api/whatsapp-init', {
    phone: document.getElementById('phone').value,
    message: document.getElementById('prompt').value,
  });
  render(state);
}
async function runAgents() {
  render(await callApi('/simulator/api/agent-run', {use_groq: document.getElementById('useGroq').checked}));
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
