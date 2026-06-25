"""
MarketFlow AI — Chief PM Orchestrator
LangGraph integration with Anthropic prompt caching

Usage:
    from marketflow_chief_pm import chief_pm_node, build_chief_pm_graph
"""

import anthropic
from pathlib import Path
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
import operator

PROMPT_PATH = Path(__file__).parent / "marketflow_chief_pm_prompt.xml"
STATIC_SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

client = anthropic.Anthropic()


class MarketFlowState(TypedDict):
    user_query: str
    department: str          # finance_research | tech_arch | developer | product
    messages: Annotated[list, operator.add]
    thinking: str
    prd_output: str
    gate_required: bool      # Green Light gate flag
    compliance_flags: dict   # Reg NMS / PSD2 / FIX 5.0 pass/fail
    cache_stats: dict        # token usage / cache hit metrics


def chief_pm_node(state: MarketFlowState) -> dict:
    """
    Chief PM Orchestrator node.

    Caching: system prompt is in the `system` parameter with cache_control.
    The cache boundary is stable — only messages[] changes per call.
    Cache hit reduces token cost ~85% and first-token latency ~500ms → ~50ms.

    Thinking: uses native adaptive thinking (Sonnet 4.6 / Opus 4.6+).
    ThinkingBlock objects are reliable — no XML tag parsing.
    """
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        thinking={"type": "adaptive"},          # adaptive thinking — budget_tokens is deprecated on 4.6
        system=[
            {
                "type": "text",
                "text": STATIC_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # stable cache boundary
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Department context: {state['department']}\n\n"
                    f"Query: {state['user_query']}"
                ),
            }
        ],
    )

    # Parse native ThinkingBlock and TextBlock — no XML fragility
    thinking = ""
    answer = ""
    for block in response.content:
        if block.type == "thinking":
            thinking = block.thinking
        elif block.type == "text":
            answer = block.text

    # Parse compliance flags from real thinking content
    compliance_flags = {
        "reg_nms_rule_611": _parse_flag(thinking, "Reg NMS Rule 611"),
        "green_light_gate": _parse_flag(thinking, "Green Light gate"),
        "fix_5_eti":        _parse_flag(thinking, "FIX 5.0"),
        "psd2_psd3":        _parse_flag(thinking, "PSD2/PSD3"),
        "stack_compat":     _parse_flag(thinking, "Stack compatibility"),
    }

    gate_required = any(v == "FAIL" for v in compliance_flags.values())

    usage = response.usage
    cache_stats = {
        "input_tokens":          usage.input_tokens,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0),
        "cache_read_tokens":     getattr(usage, "cache_read_input_tokens", 0),
        "output_tokens":         usage.output_tokens,
    }

    return {
        "thinking":         thinking,
        "prd_output":       answer,
        "compliance_flags": compliance_flags,
        "gate_required":    gate_required,
        "cache_stats":      cache_stats,
        "messages":         [{"role": "assistant", "content": answer}],
    }


def _parse_flag(thinking: str, label: str) -> str:
    """Extract PASS / FAIL / N-A from the self-consistency checklist in the thinking block."""
    for line in thinking.split("\n"):
        if label in line:
            if "PASS" in line:
                return "PASS"
            elif "FAIL" in line:
                return "FAIL"
            elif "N-A" in line or "N/A" in line:
                return "N-A"
    return "UNKNOWN"


def green_light_router(state: MarketFlowState) -> str:
    """Route to compliance violation handler or approved path."""
    if state.get("gate_required"):
        return "compliance_violation"
    return "approved"


def build_chief_pm_graph() -> StateGraph:
    """Build the MarketFlow Chief PM LangGraph with compliance routing."""
    graph = StateGraph(MarketFlowState)

    graph.add_node("chief_pm", chief_pm_node)
    graph.add_node("compliance_violation", lambda s: {
        "prd_output": (
            f"[COMPLIANCE VIOLATION DETECTED — GREEN LIGHT BLOCKED]\n\n"
            f"{s['prd_output']}\n\n"
            f"Flags: {s['compliance_flags']}"
        )
    })

    graph.set_entry_point("chief_pm")
    graph.add_conditional_edges(
        "chief_pm",
        green_light_router,
        {
            "approved":             END,
            "compliance_violation": "compliance_violation",
        },
    )
    graph.add_edge("compliance_violation", END)

    return graph.compile()


if __name__ == "__main__":
    app = build_chief_pm_graph()

    result = app.invoke({
        "user_query":       "Design the Smart Order Router for US equities in MarketFlow",
        "department":       "tech_arch",
        "messages":         [],
        "thinking":         "",
        "prd_output":       "",
        "gate_required":    False,
        "compliance_flags": {},
        "cache_stats":      {},
    })

    print("=== PRD OUTPUT ===")
    print(result["prd_output"])

    print("\n=== COMPLIANCE FLAGS ===")
    for k, v in result["compliance_flags"].items():
        print(f"  {k}: {v}")

    print(f"\n=== GREEN LIGHT REQUIRED: {result['gate_required']} ===")

    if result.get("cache_stats"):
        print("\n=== CACHE STATS ===")
        s = result["cache_stats"]
        print(f"  Input tokens:          {s['input_tokens']}")
        print(f"  Cache creation tokens: {s['cache_creation_tokens']}")
        print(f"  Cache read tokens:     {s['cache_read_tokens']}")
        print(f"  Output tokens:         {s['output_tokens']}")
        print(f"  Cache hit:             {'YES' if s['cache_read_tokens'] > 0 else 'NO (first call)'}")
