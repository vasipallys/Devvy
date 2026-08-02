import os

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
        events = [socket.receive_json() for _ in range(8)]

    assert {event["type"] for event in events} == {
        "transcript", "state", "token", "text_complete", "image_ready", "audio_ready"
    }
