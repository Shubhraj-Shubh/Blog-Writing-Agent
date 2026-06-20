import os
import re
import time
from datetime import date, timedelta
from typing import List, Optional

from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.types import Send

# Custom Imports
from backend.config import llm
from backend.state import State, RouterDecision, EvidencePack, EvidenceItem, Plan, Task

# -----------------------------
# 1) Router Node
# -----------------------------
ROUTER_SYSTEM = """You are a routing module for a technical blog planner.

Decide whether web research is needed BEFORE planning.

Modes:
- closed_book (needs_research=false): evergreen concepts.
- hybrid (needs_research=true): evergreen + needs up-to-date examples/tools/models.
- open_book (needs_research=true): volatile weekly/news/"latest"/pricing/policy.

If needs_research=true:
- Output 3–10 high-signal, scoped queries.
- For open_book weekly roundup, include queries reflecting last 7 days.
"""

def router_node(state: State) -> dict:
    decider = llm.with_structured_output(RouterDecision)
    decision = decider.invoke([
        SystemMessage(content=ROUTER_SYSTEM),
        HumanMessage(content=f"Topic: {state['topic']}\nAs-of date: {state['as_of']}"),
    ])
    if decision.mode == "open_book":
        recency_days = 7
    elif decision.mode == "hybrid":
        recency_days = 45
    else:
        recency_days = 3650
    return {
        "needs_research": decision.needs_research,
        "mode": decision.mode,
        "queries": decision.queries,
        "recency_days": recency_days,
    }

def route_next(state: State) -> str:
    return "research" if state["needs_research"] else "orchestrator"

# -----------------------------
# 2) Research Node
# -----------------------------
def _tavily_search(query: str, max_results: int = 5) -> List[dict]:
    if not os.getenv("TAVILY_API_KEY"):
        return []
    try:
        from langchain_tavily import TavilySearchResults
        tool = TavilySearchResults(max_results=max_results)
        results = tool.invoke({"query": query})
        out: List[dict] = []
        for r in results or []:
            out.append({
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                "snippet": r.get("content") or r.get("snippet") or "",
                "published_at": r.get("published_date") or r.get("published_at"),
                "source": r.get("source"),
            })
        return out
    except Exception:
        return []

def _iso_to_date(s: Optional[str]) -> Optional[date]:
    if not s: return None
    try: return date.fromisoformat(s[:10])
    except Exception: return None

RESEARCH_SYSTEM = """You are a research synthesizer.

Given raw web search results, produce EvidenceItem objects.

Rules:
- Only include items with a non-empty url.
- Prefer relevant + authoritative sources.
- Normalize published_at to ISO YYYY-MM-DD if reliably inferable; else null (do NOT guess).
- Keep snippets short.
- Deduplicate by URL.
"""

def research_node(state: State) -> dict:
    queries = (state.get("queries") or [])[:10]
    raw: List[dict] = []
    for q in queries:
        raw.extend(_tavily_search(q, max_results=6))
    if not raw:
        return {"evidence": []}

    extractor = llm.with_structured_output(EvidencePack)
    pack = extractor.invoke([
        SystemMessage(content=RESEARCH_SYSTEM),
        HumanMessage(content=f"As-of date: {state['as_of']}\nRecency days: {state['recency_days']}\n\nRaw results:\n{raw}"),
    ])

    dedup = {}
    for e in pack.evidence:
        if e.url:
            dedup[e.url] = e
    evidence = list(dedup.values())

    if state.get("mode") == "open_book":
        as_of = date.fromisoformat(state["as_of"])
        cutoff = as_of - timedelta(days=int(state["recency_days"]))
        evidence = [e for e in evidence if (d := _iso_to_date(e.published_at)) and d >= cutoff]

    return {"evidence": evidence}

# -----------------------------
# 3) Orchestrator Node
# -----------------------------
ORCH_SYSTEM = """You are a senior technical writer and developer advocate.
Produce a highly actionable outline for a technical blog post.

Requirements:
- 5–9 tasks, each with goal + 3–6 bullets + target_words.
- Tags are flexible; do not force a fixed taxonomy.

Grounding:
- closed_book: evergreen, no evidence dependence.
- hybrid: use evidence for up-to-date examples; mark those tasks requires_research=True and requires_citations=True.
- open_book: weekly/news roundup:
  - Set blog_kind="news_roundup"
  - No tutorial content unless requested
  - If evidence is weak, plan should explicitly reflect that (don’t invent events).

Output must match Plan schema.
"""


def orchestrator_node(state: State) -> dict:
    planner = llm.with_structured_output(Plan)
    mode = state.get("mode", "closed_book")
    evidence = state.get("evidence", [])
    forced_kind = "news_roundup" if mode == "open_book" else None

    plan = planner.invoke([
        SystemMessage(content=ORCH_SYSTEM),
        HumanMessage(content=f"Topic: {state['topic']}\nMode: {mode}\nAs-of: {state['as_of']} (recency_days={state['recency_days']})\n{'Force blog_kind=news_roundup' if forced_kind else ''}\n\nEvidence:\n{[e.model_dump() for e in evidence][:16]}"),
    ])
    if forced_kind:
        plan.blog_kind = "news_roundup"
    return {"plan": plan}


# -----------------------------
# Fanout
# -----------------------------
def fanout(state: State):
    assert state["plan"] is not None
    return [
        Send(
            "worker",
            {
                "task": task.model_dump(),
                "topic": state["topic"],
                "mode": state["mode"],
                "as_of": state["as_of"],
                "recency_days": state["recency_days"],
                "plan": state["plan"].model_dump(),
                "evidence": [e.model_dump() for e in state.get("evidence", [])],
            },
        )
        for task in state["plan"].tasks
    ]

# -----------------------------
# 4) Worker Node
# -----------------------------
WORKER_SYSTEM = """You are a senior technical writer and developer advocate.
Write ONE section of a technical blog post in Markdown.

CRITICAL RULES:
- Output ONLY the Markdown text.
- NEVER wrap your output in JSON, dicts, or {'type': 'text', ...} structures.
- Start directly with "## <Section Title>".
- Do not add any conversational filler.

Constraints:
- Cover ALL bullets in order.
- Target words ±15%.
- Output only section markdown starting with "## <Section Title>".

Scope guard:
- If blog_kind=="news_roundup", do NOT drift into tutorials (scraping/RSS/how to fetch).
  Focus on events + implications.

Grounding:
- If mode=="open_book": do not introduce any specific event/company/model/funding/policy claim unless supported by provided Evidence URLs.
  For each supported claim, attach a Markdown link ([Source](URL)).
  If unsupported, write "Not found in provided sources."
- If requires_citations==true (hybrid tasks): cite Evidence URLs for external claims.

Code:
- If requires_code==true, include at least one minimal snippet.
"""

def worker_node(payload: dict) -> dict:
    task = Task(**payload["task"])
    plan = Plan(**payload["plan"])
    evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]
    
     # --- ADDING SLEEP HERE to prevent rate limiting ---
    delay = int(task.id) * 6  
    print(f"⏳ Task {task.id} sleeping for {delay} seconds...")
    time.sleep(delay)

    bullets_text = "\n- " + "\n- ".join(task.bullets)
    evidence_text = "\n".join(f"- {e.title} | {e.url} | {e.published_at or 'date:unknown'}" for e in evidence[:20])

    response = llm.invoke([
        SystemMessage(content=WORKER_SYSTEM),
        HumanMessage(content=f"Blog title: {plan.blog_title}\nAudience: {plan.audience}\nTone: {plan.tone}\nBlog kind: {plan.blog_kind}\nTopic: {payload['topic']}\nSection title: {task.title}\nGoal: {task.goal}\nTarget words: {task.target_words}\nrequires_research: {task.requires_research}\nrequires_citations: {task.requires_citations}\nrequires_code: {task.requires_code}\nBullets:{bullets_text}\n\nEvidence:\n{evidence_text}\n"),
    ])

# 1. Extract text content from the response, handling both string and list formats.
    if isinstance(response.content, list):
        section_md = "".join([part.text if hasattr(part, 'text') else str(part) for part in response.content])
    else:
        section_md = str(response.content)
 # 2. REGEX CLEANUP to remove any accidental JSON/dict wrappers, ensuring we only keep the Markdown text.
    section_md = re.sub(r"\{['\"]type['\"]:\s*['\"]text['\"],\s*['\"]text['\"]:\s*['\"](.*?)['\"]\}", r"\1", section_md, flags=re.DOTALL)
  # 3. Final cleanup   
    section_md = section_md.replace("\\n", "\n").strip()

    return {"sections": [(task.id, section_md)]}