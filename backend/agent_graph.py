"""YUKTI — the Talk voice companion's agent graph.

The graph answers one question before it answers the user's: *where does this turn's evidence
come from*. Four sources are possible — the memory bank, the user's own notes, the live web,
and nothing at all — and the routing between them is keyword matching in code, not a judgement
handed to a 1B model, because the model is the thing being kept honest here.

One node runs before all of them and can end the turn on its own. `gate` checks whether the
user has asked for a faculty that does not exist: the screen, the inbox, the calendar, another
provider's model. Those turns never reach generation. A model instructed not to describe a
screen it cannot see will still occasionally describe one, and by voice there is nothing for
the listener to check it against — so the refusal is deterministic and the model is never
given the opportunity.
"""

import asyncio
from datetime import datetime
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from backend.config import Settings
from backend.harness import ContextSource, assemble_context
from backend.model import GemmaRuntime
from backend.tools import research_context, web_search
from backend.yukti import honorific, refusal_for, speakable, system_prompt


class TalkState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    voice_input: str
    requires_animation: bool
    requires_research: bool
    requires_recall: bool
    requires_briefing: bool
    research_context: str
    #: Notes and memories assembled before generation, already bounded.
    recall_context: str
    user_preferences: dict[str, str]
    response: str
    #: The same answer with its markup removed, for the speech synthesiser.
    spoken: str
    token_queue: asyncio.Queue[str] | None
    context_manifest: list[dict]
    #: Which spoken phrases triggered research, recall or animation, so the decision is
    #: inspectable rather than merely correct.
    route_reason: str
    sources: list[dict]
    notes: list[dict]
    memories: list[dict]
    research_failed: bool
    #: Set when the turn asked for a faculty that is not connected. Ends the turn.
    refused: str
    completion: dict
    #: The second brain, supplied per turn. It is bound to one owner's vault and memory bank,
    #: so it cannot live on the graph: the graph is a module-level singleton and another
    #: owner's memories must never reach this turn's answer.
    recall_provider: object | None


class TalkAgentGraph:
    VISUAL_TERMS = {
        "visualize", "animation", "animate", "diagram", "graph", "geometry", "equation",
        "algorithm", "calculus", "physics", "matrix", "probability", "explain visually",
    }

    RESEARCH_TERMS = {
        "latest", "today's news", "todays news", "current news", "breaking news",
        "search the web", "search internet", "look up", "recent news", "weather",
        "current price", "current events", "this week", "this month", "this year",
        "alternative to", "alternatives to", "cheaper than", "compare prices", "pricing for",
    }

    #: The second brain is asked for by name, or by asking about something that is the user's
    #: own rather than the world's. "My" is doing most of the work: *my funnel* is in the
    #: vault, *the funnel* is a question about funnels.
    RECALL_TERMS = {
        "my notes", "in my notes", "second brain", "my documents", "my files",
        "what did i write", "what did i say", "did i note", "my project", "my funnel",
        "remind me what", "do you remember", "you remembered", "from my notes",
    }

    BRIEFING_TERMS = {
        "daily briefing", "morning update", "morning brief", "brief me", "my briefing",
        "what's my day", "what is my day", "start of day", "daily update",
    }

    def __init__(self, runtime: GemmaRuntime, settings: Settings):
        self.runtime = runtime
        self.settings = settings
        graph = StateGraph(TalkState)
        graph.add_node("gate", self._gate)
        graph.add_node("route_visual", self._route_visual)
        graph.add_node("recall", self._recall)
        graph.add_node("research", self._research)
        graph.add_node("companion", self._companion)
        graph.add_edge(START, "gate")
        graph.add_conditional_edges(
            "gate",
            lambda state: END if state["refused"] else "route_visual",
            {END: END, "route_visual": "route_visual"},
        )
        # Recall runs before research: what the user already wrote is cheaper, more specific
        # and more trustworthy than what the web says, and a turn answered from their own
        # notes should not also spend thirty seconds searching.
        graph.add_conditional_edges(
            "route_visual",
            self._after_routing,
            {"recall": "recall", "research": "research", "companion": "companion"},
        )
        graph.add_conditional_edges(
            "recall",
            lambda state: "research" if state["requires_research"] else "companion",
            {"research": "research", "companion": "companion"},
        )
        graph.add_edge("research", "companion")
        graph.add_edge("companion", END)
        self.graph = graph.compile()

    @staticmethod
    def _after_routing(state: TalkState) -> str:
        if state["requires_recall"] or state["requires_briefing"]:
            return "recall"
        return "research" if state["requires_research"] else "companion"

    async def _gate(self, state: TalkState) -> dict:
        """Refuse an unwired faculty before the model can improvise one."""
        address = honorific(state.get("user_preferences"))
        refusal = refusal_for(state["voice_input"], address)
        if not refusal:
            return {"refused": ""}
        return {
            "refused": refusal,
            "response": refusal,
            "spoken": speakable(refusal),
            "messages": [AIMessage(content=refusal)],
            "route_reason": (
                "The turn asked for a faculty that is not connected, so it was declined in "
                "code rather than answered by the model."
            ),
        }

    async def _route_visual(self, state: TalkState) -> dict:
        lowered = state["voice_input"].lower()
        visual = sorted(term for term in self.VISUAL_TERMS if term in lowered)
        research = sorted(term for term in self.RESEARCH_TERMS if term in lowered)
        recall = sorted(term for term in self.RECALL_TERMS if term in lowered)
        briefing = sorted(term for term in self.BRIEFING_TERMS if term in lowered)
        reasons = []
        if briefing:
            reasons.append(f"a daily briefing triggered by {', '.join(repr(x) for x in briefing)}")
        if recall:
            reasons.append(f"your second brain triggered by {', '.join(repr(x) for x in recall)}")
        if research:
            reasons.append(f"live research triggered by {', '.join(repr(x) for x in research)}")
        if visual:
            reasons.append(f"a visual explanation triggered by {', '.join(repr(x) for x in visual)}")
        return {
            "requires_animation": bool(visual),
            "requires_research": bool(research),
            "requires_recall": bool(recall),
            "requires_briefing": bool(briefing),
            "route_reason": (
                "You asked for " + " and ".join(reasons) + "."
                if reasons
                else "No second-brain, live-data or visual trigger matched, so this is a "
                     "spoken conversation."
            ),
        }

    async def _recall(self, state: TalkState) -> dict:
        """Read the second brain: the user's own notes, and what YUKTI was told to remember."""
        provider = state.get("recall_provider")
        if provider is None:
            return {"recall_context": "", "notes": [], "memories": []}
        try:
            found = await provider(
                state["voice_input"], briefing=state["requires_briefing"]
            )
        except Exception as exc:
            # The vault is one evidence source among several. A turn that could still be
            # answered from the web or from conversation must not die because a directory
            # moved — but the failure is stated, never swallowed into a confident answer.
            return {
                "recall_context": (
                    "SECOND BRAIN UNAVAILABLE. Tell the user plainly that their notes could "
                    f"not be read this turn and do not answer as though you had read them. "
                    f"Reason: {exc}"
                ),
                "notes": [],
                "memories": [],
            }
        return {
            "recall_context": found.get("context", ""),
            "notes": found.get("notes", []),
            "memories": found.get("memories", []),
        }

    async def _research(self, state: TalkState) -> dict:
        try:
            results = await web_search(state["voice_input"], limit=5)
        except Exception as exc:
            return {
                "research_failed": True,
                "sources": [],
                "research_context": (
                    "LIVE WEB RESEARCH FAILED. Clearly tell the user current web data could "
                    f"not be retrieved and do not invent an answer. Technical reason: {exc}"
                ),
            }
        if not results:
            return {
                "research_failed": True,
                "sources": [],
                "research_context": (
                    "LIVE WEB RESEARCH RETURNED NO SOURCES. Say so plainly and do not invent "
                    "an answer or cite sources."
                ),
            }
        return {
            "research_failed": False,
            "sources": [
                {
                    "title": str(item.get("title") or "Result"),
                    "url": str(item.get("url") or ""),
                    "characters": len(str(item.get("content") or "")),
                }
                for item in results
            ],
            "research_context": "LIVE WEB SOURCES:\n" + research_context(results),
        }

    async def _companion(self, state: TalkState) -> dict:
        current = datetime.now().astimezone()
        address = honorific(state.get("user_preferences"))
        # The user's own material outranks the web. When a note and a search result disagree
        # about the user's own project, the note is right.
        context, context_manifest = assemble_context(
            [
                ContextSource(
                    "second-brain", "Your notes and remembered facts",
                    state.get("recall_context", ""), 95, trusted=True,
                ),
                ContextSource(
                    "live-research", "Public web research",
                    state.get("research_context", ""), 90,
                ),
            ],
            self.settings.document_max_chars,
        )
        briefing = _BRIEFING_RULE if state.get("requires_briefing") else ""
        messages = [{
            "role": "system",
            "content": system_prompt(
                address,
                now=current.strftime("%A, %B %d, %Y, %I:%M %p %Z"),
                context=context,
                briefing=briefing,
            ),
        }]
        turns: list[dict[str, str]] = []
        for message in state["messages"][-self.settings.model_context_messages:]:
            role = "assistant" if isinstance(message, AIMessage) else "user"
            content = str(message.content)
            if not turns and role == "assistant":
                continue
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + content
            else:
                turns.append({"role": role, "content": content})
        messages.extend(turns)
        completion: dict = {}
        response = await self.runtime.generate(
            messages, state.get("token_queue"), stats=completion
        )
        return {
            "response": response,
            # Shaped once, here, so the socket, the synthesiser and the ledger all agree on
            # what was said aloud.
            "spoken": speakable(response),
            "messages": [AIMessage(content=response)],
            "context_manifest": context_manifest,
            "completion": completion,
        }

    async def invoke(
        self,
        history: list[BaseMessage],
        transcript: str,
        preferences: dict[str, str],
        token_queue: asyncio.Queue[str] | None = None,
        recall_provider=None,
    ) -> TalkState:
        return await self.graph.ainvoke(
            {
                "messages": history + [HumanMessage(content=transcript)],
                "voice_input": transcript,
                "requires_animation": False,
                "requires_research": False,
                "requires_recall": False,
                "requires_briefing": False,
                "research_context": "",
                "recall_context": "",
                "user_preferences": preferences,
                "response": "",
                "spoken": "",
                "token_queue": token_queue,
                "context_manifest": [],
                "route_reason": "",
                "sources": [],
                "notes": [],
                "memories": [],
                "research_failed": False,
                "refused": "",
                "completion": {},
                "recall_provider": recall_provider,
            }
        )


#: What a briefing is allowed to contain.
#:
#: The specification's briefing protocol wants calendar events and unread mail. Neither is
#: connected, and a briefing that invents either is worse than no briefing — it is a list of
#: meetings the user will plan a morning around. So the rule names the gap out loud and briefs
#: from what genuinely exists: the memory bank and the notes vault.
_BRIEFING_RULE = """<briefing>
The user asked for a briefing. Give it in this order, and keep the whole thing under 120
words:
1. Anything in their notes or your memory bank marked as pending, due, or in progress.
2. Standing preferences or decisions that bear on today.
3. State plainly, in one short clause, that their calendar and mail are not connected to you,
   so the briefing covers their notes only. Do not invent meetings, times or messages.
</briefing>"""
