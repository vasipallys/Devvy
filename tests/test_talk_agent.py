from langchain_core.messages import HumanMessage

from backend.agent_graph import TalkAgentGraph
from backend.config import Settings


class RuntimeStub:
    def __init__(self):
        self.messages = []

    async def generate(self, messages, token_queue=None, stats=None, **_options):
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


async def test_talk_router_reports_why_it_routed(monkeypatch):
    """Talk shares Chat's rule: the decision is shown, not just asserted."""
    agent = TalkAgentGraph(RuntimeStub(), Settings(phoenix_enabled=False))

    async def route(text: str) -> dict:
        return await agent._route_visual({"voice_input": text})

    both = await route("show me the latest news and visualize the algorithm")
    assert both["requires_research"] is True and both["requires_animation"] is True
    # The reason names the *live topic* that decided it. "latest" is only a time marker: it
    # sharpens a request for information and never makes one on its own, so quoting it here
    # would credit the decision to the wrong word.
    assert "'news'" in both["route_reason"]
    assert "'algorithm'" in both["route_reason"]

    plain = await route("how was your day")
    assert plain["requires_research"] is False and plain["requires_animation"] is False
    assert "No second-brain, live-data or visual trigger matched" in plain["route_reason"]

    # The second brain is its own route, and it says which words asked for it.
    notes = await route("what did i write in my notes about the funnel")
    assert notes["requires_recall"] is True and notes["requires_research"] is False
    assert "'my notes'" in notes["route_reason"]


async def test_talk_research_failure_degrades_honestly(monkeypatch):
    async def failing_search(*_args, **_kwargs):
        raise TimeoutError("search timed out")

    monkeypatch.setattr("backend.agent_graph.web_search", failing_search)
    agent = TalkAgentGraph(RuntimeStub(), Settings(phoenix_enabled=False))
    result = await agent._research({"voice_input": "latest news"})

    assert result["research_failed"] is True
    assert result["sources"] == []
    assert "do not invent" in result["research_context"].lower()


async def test_talk_research_success_exposes_sources(monkeypatch):
    async def search(*_args, **_kwargs):
        return [{"title": "Story", "url": "https://example.org/x", "content": "z" * 40}]

    monkeypatch.setattr("backend.agent_graph.web_search", search)
    agent = TalkAgentGraph(RuntimeStub(), Settings(phoenix_enabled=False))
    result = await agent._research({"voice_input": "latest news"})

    assert result["research_failed"] is False
    assert result["sources"] == [
        {"title": "Story", "url": "https://example.org/x", "characters": 40}
    ]


async def test_talk_graph_preserves_multi_turn_state():
    runtime = RuntimeStub()
    agent = TalkAgentGraph(runtime, Settings(phoenix_enabled=False))
    result = await agent.invoke([], "Hello friend", {})
    assert result["response"] == "Hello"
    assert result["messages"][-1].content == "Hello"
    assert "Current local date and time:" in runtime.messages[0]["content"]
