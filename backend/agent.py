import asyncio
from datetime import datetime
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from backend.config import Settings
from backend.model import GemmaRuntime
from backend.tools import generate_image, research_context, web_search


SYSTEM_PROMPT = """You are Gemma Studio, a precise local AI assistant.
Answer directly and use Markdown. For code, provide complete runnable snippets, explain key decisions,
and mention security or correctness risks. Never claim to have searched, read, or generated an artifact
unless context explicitly shows it. Research answers must cite supplied sources with Markdown links.
Document answers must distinguish document evidence from inference."""


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    mode: str
    attachment_context: str
    tool_context: str
    artifact_url: str | None
    token_queue: asyncio.Queue[str] | None


class ChatAgent:
    def __init__(self, runtime: GemmaRuntime, settings: Settings):
        self.runtime = runtime
        self.settings = settings
        graph = StateGraph(AgentState)
        graph.add_node("route", self._route)
        graph.add_node("research", self._research)
        graph.add_node("image", self._image)
        graph.add_node("respond", self._respond)
        graph.add_edge(START, "route")
        graph.add_conditional_edges(
            "route", self._next, {"research": "research", "image": "image", "respond": "respond"}
        )
        graph.add_edge("research", "respond")
        graph.add_edge("image", END)
        graph.add_edge("respond", END)
        self.graph = graph.compile()

    async def _route(self, state: AgentState) -> dict:
        if state["mode"] != "auto":
            return {}
        text = state["messages"][-1].content.lower()
        if any(x in text for x in ("generate an image", "create an image", "draw ", "illustrate ")):
            return {"mode": "image"}
        if any(x in text for x in ("search web", "research ", "latest ", "current ", "look up")):
            return {"mode": "research"}
        if state.get("attachment_context"):
            return {"mode": "document"}
        if any(x in text for x in ("write code", "implement ", "debug ", "python", "typescript")):
            return {"mode": "code"}
        return {"mode": "chat"}

    def _next(self, state: AgentState) -> Literal["research", "image", "respond"]:
        return state["mode"] if state["mode"] in {"research", "image"} else "respond"

    async def _research(self, state: AgentState) -> dict:
        query = str(state["messages"][-1].content)
        results = await web_search(query)
        return {"tool_context": "WEB RESEARCH RESULTS:\n" + research_context(results)}

    async def _image(self, state: AgentState) -> dict:
        prompt = str(state["messages"][-1].content)
        try:
            url = await generate_image(prompt, self.settings)
            return {
                "artifact_url": url,
                "messages": [AIMessage(content=f"Generated image:\n\n![{prompt}]({url})")],
            }
        except Exception as exc:
            return {"messages": [AIMessage(content=f"Image generation failed: {exc}")]}

    async def _respond(self, state: AgentState) -> dict:
        current = datetime.now().astimezone()
        prompt = (
            SYSTEM_PROMPT
            + f"\nCurrent local date and time: {current.strftime('%A, %B %d, %Y, %I:%M %p %Z')}."
            + " Never infer the current date from model training data."
        )
        if state["mode"] == "code":
            prompt += "\nYou are in code mode. Prefer production-quality, tested code."
        context = "\n\n".join(x for x in (state.get("attachment_context"), state.get("tool_context")) if x)
        if context:
            prompt += "\n\n" + context
        messages = [{"role": "system", "content": prompt}]
        turns: list[dict[str, str]] = []
        for message in state["messages"][-self.settings.model_context_messages:]:
            role = "assistant" if isinstance(message, AIMessage) else "user"
            content = str(message.content)
            # Failed generations can leave consecutive user turns in persistent
            # history. Gemma requires strict user/assistant alternation, so merge
            # adjacent turns of the same role before applying its chat template.
            if not turns and role == "assistant":
                continue
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + content
            else:
                turns.append({"role": role, "content": content})
        messages.extend(turns)
        answer = await self.runtime.generate(messages, state.get("token_queue"))
        return {"messages": [AIMessage(content=answer)]}

    async def invoke(
        self,
        history: list[BaseMessage],
        message: str,
        mode: str,
        attachment_context: str = "",
        token_queue: asyncio.Queue[str] | None = None,
    ) -> dict:
        return await self.graph.ainvoke(
            {
                "messages": history + [HumanMessage(content=message)],
                "mode": mode,
                "attachment_context": attachment_context,
                "tool_context": "",
                "artifact_url": None,
                "token_queue": token_queue,
            }
        )
