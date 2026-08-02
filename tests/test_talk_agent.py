from langchain_core.messages import HumanMessage

from backend.agent_graph import TalkAgentGraph
from backend.config import Settings


class RuntimeStub:
    def __init__(self):
        self.messages = []

    async def generate(self, messages, token_queue=None):
        self.messages = messages
        if token_queue is not None:
            await token_queue.put("Hello")
        return "Hello"


async def test_talk_router_requests_visual_for_math():
    agent = TalkAgentGraph(RuntimeStub(), Settings(phoenix_enabled=False))
    result = await agent._route_visual(
        {
            "messages": [HumanMessage(content="Visualize this equation")],
            "voice_input": "Visualize this equation",
            "requires_animation": False,
            "requires_research": False,
            "research_context": "",
            "user_preferences": {},
            "response": "",
            "token_queue": None,
        }
    )
    assert result["requires_animation"] is True


async def test_talk_router_uses_web_for_latest_questions():
    agent = TalkAgentGraph(RuntimeStub(), Settings(phoenix_enabled=False))
    result = await agent._route_visual(
        {
            "messages": [HumanMessage(content="latest AI news")],
            "voice_input": "latest AI news",
            "requires_animation": False,
            "requires_research": False,
            "research_context": "",
            "user_preferences": {},
            "response": "",
            "token_queue": None,
        }
    )
    assert result["requires_research"] is True


async def test_talk_graph_preserves_multi_turn_state():
    runtime = RuntimeStub()
    agent = TalkAgentGraph(runtime, Settings(phoenix_enabled=False))
    result = await agent.invoke([], "Hello friend", {})
    assert result["response"] == "Hello"
    assert result["messages"][-1].content == "Hello"
    assert "Current local date and time:" in runtime.messages[0]["content"]
