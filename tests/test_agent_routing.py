from langchain_core.messages import HumanMessage

from backend.agent import ChatAgent
from backend.config import Settings


class RuntimeStub:
    def __init__(self):
        self.messages = []

    async def generate(self, messages, token_queue=None):
        self.messages = messages
        return "stub response"


async def test_routes_research():
    agent = ChatAgent(RuntimeStub(), Settings(phoenix_enabled=False))
    state = {"messages": [HumanMessage(content="research current Python releases")], "mode": "auto", "attachment_context": "", "tool_context": "", "artifact_url": None}
    result = await agent._route(state)
    assert result["mode"] == "research"


async def test_routes_document_when_attachment_present():
    agent = ChatAgent(RuntimeStub(), Settings(phoenix_enabled=False))
    state = {"messages": [HumanMessage(content="summarize this")], "mode": "auto", "attachment_context": "content", "tool_context": "", "artifact_url": None}
    result = await agent._route(state)
    assert result["mode"] == "document"


async def test_respond_normalizes_gemma_conversation_roles():
    runtime = RuntimeStub()
    agent = ChatAgent(runtime, Settings(phoenix_enabled=False))
    state = {
        "messages": [HumanMessage(content="failed request"), HumanMessage(content="retry")],
        "mode": "document",
        "attachment_context": "DOCUMENT: example",
        "tool_context": "",
        "artifact_url": None,
        "token_queue": None,
    }
    await agent._respond(state)
    assert [message["role"] for message in runtime.messages] == ["system", "user"]
    assert "DOCUMENT: example" in runtime.messages[0]["content"]
    assert "failed request\n\nretry" in runtime.messages[1]["content"]
