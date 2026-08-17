import json
import os
import pathlib

os.environ["PHOENIX_ENABLED"] = "false"

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from backend import api
from backend.api import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_conversation_lifecycle():
    with TestClient(app) as client:
        created = client.post("/api/conversations").json()
        assert created["title"] == "New conversation"
        assert client.get(f"/api/conversations/{created['id']}/messages").json() == []
        assert client.delete(f"/api/conversations/{created['id']}").status_code == 204


def test_talk_text_mode_returns_workspace_artifact(monkeypatch):
    async def invoke(history, message, mode, context, token_queue):
        assert message == "draw a moon"
        assert mode == "image"
        assert context == ""
        await token_queue.put("Generated")
        return {
            "messages": [*history, AIMessage(content="Generated image")],
            "artifact_url": "/generated/moon.png",
        }

    async def synthesize(_text):
        return "/generated/speech.wav"

    monkeypatch.setattr(api.agent, "invoke", invoke)
    monkeypatch.setattr(api.voice_engine, "synthesize", synthesize)
    with TestClient(app) as client, client.websocket_connect("/api/talk/ws") as socket:
        assert socket.receive_json() == {"type": "state", "value": "idle"}
        socket.send_json({"type": "text", "content": "draw a moon", "mode": "image"})
        events = []
        # The turn now runs as a background job, so the socket also relays heartbeats while
        # it waits. Read until the terminal event rather than assuming a fixed count.
        for _ in range(200):
            event = socket.receive_json()
            events.append(event)
            if event["type"] == "audio_ready":
                break
        else:
            raise AssertionError(f"audio_ready never arrived; saw {[e['type'] for e in events]}")

    assert {event["type"] for event in events} >= {
        "transcript", "state", "token", "text_complete", "image_ready", "audio_ready"
    }
    assert any(event["type"] == "agent_event" for event in events)
    # The turn is durable: it is submitted as a job the client can look up later.
    assert any(event["type"] == "job_started" and event["job_id"] for event in events)


def test_system_status_exposes_capabilities_without_secrets():
    with TestClient(app) as client:
        payload = client.get("/api/system/status").json()
    assert payload["app"]["deployment"] == "local-desktop"
    assert payload["model"]["generation"] == "serialized"
    assert payload["trust"]["privacy"] == "Local-first"
    assert "hf_token" not in str(payload).lower()
    assert "jira_api_token" not in str(payload).lower()


# -- Ingest bounds ---------------------------------------------------------------------------
#
# Every path that accepts bytes from a client needs a ceiling. HTTP uploads had one; the two
# below did not, and the audio path is reachable by leaving a microphone running rather than
# by anything adversarial.

def test_an_oversized_request_body_is_refused_before_it_is_parsed():
    """Starlette buffers the whole body before FastAPI validates it, so a route that would
    reject a payload as malformed has already paid to hold it in memory."""
    from backend.api import MAX_REQUEST_BYTES

    with TestClient(app) as client:
        response = client.post(
            "/api/estimate-code/upload/parse",
            json={"text": "x" * (MAX_REQUEST_BYTES + 1024)},
        )
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_an_ordinary_request_body_still_reaches_its_route():
    """A ceiling that also blocks legitimate payloads is not a fix."""
    with TestClient(app) as client:
        response = client.post("/api/estimate-code/upload/parse", json={"text": "small"})
    assert response.status_code != 413


def test_the_voice_socket_refuses_a_recording_past_its_ceiling():
    """Unbounded, a recording nobody stopped grew in server memory with no ceiling and no
    feedback. Truncating instead would transcribe half a sentence and answer it confidently."""
    from backend.api import MAX_VOICE_BYTES

    with TestClient(app) as client, client.websocket_connect("/api/talk/ws") as socket:
        assert socket.receive_json()["type"] == "state"
        socket.send_bytes(b"\x00" * (MAX_VOICE_BYTES + 1))
        message = socket.receive_json()
        assert message["type"] == "error"
        assert "longer than this session accepts" in message["message"]
        # The session stays usable rather than dying on a bad frame.
        assert socket.receive_json() == {"type": "state", "value": "idle"}


def test_the_voice_socket_accepts_an_ordinary_recording():
    with TestClient(app) as client, client.websocket_connect("/api/talk/ws") as socket:
        assert socket.receive_json()["type"] == "state"
        socket.send_bytes(b"\x00" * 4096)
        socket.send_json({"type": "reset"})
        assert socket.receive_json() == {"type": "reset_complete"}


# -- The one place text becomes live HTML ----------------------------------------------------

def test_the_chat_sanitiser_is_covered_by_a_real_browser_test():
    """The sanitiser is attacked properly in `frontend/src/renderMarkdown.test.ts`.

    It runs under vitest in jsdom, because the implementation builds a real DOM and a fake one
    would be testing a different function than the one that ships. Twenty-two payloads go in —
    script tags, event handlers, `javascript:`/`data:`/`vbscript:` URLs, mutation XSS through
    `noscript` and `annotation-xml` — and the assertion is about what survives serialise and
    re-parse, which is the step mutation XSS exists to exploit.

    This test does not duplicate that. It guards the thing a Python-only run would otherwise
    miss: that the suite still exists and is still wired to a command, so deleting it fails
    something rather than quietly reducing coverage on the one function where an injection
    could land.
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "frontend"
    suite = root / "src" / "renderMarkdown.test.ts"
    assert suite.is_file(), "the sanitiser's browser test has been removed"

    body = suite.read_text(encoding="utf-8")
    assert "renderMarkdown" in body
    assert body.count("['") >= 15, "the payload table has been thinned out"

    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    assert "test" in package["scripts"], "npm test no longer runs the frontend suite"
    assert "vitest" in package["scripts"]["test"]
    assert "vitest" in package.get("devDependencies", {})

    # jsdom, not node: `DOMParser` and `replaceWith` do not exist in the default environment,
    # so the wrong one would make every payload "pass" by throwing before it was tested.
    config = (root / "vite.config.ts").read_text(encoding="utf-8")
    assert "jsdom" in config
