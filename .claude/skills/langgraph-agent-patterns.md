---
description: Conventions for building LangGraph agents in this codebase
---

# LangGraph Agent Patterns

## State Definition

Use `TypedDict` with `total=False` so all fields are optional. Carry domain objects (Pydantic models) in state, not raw dicts.

```python
class AgentState(TypedDict, total=False):
    market_snapshot: dict[str, Any]
    signal: CandidateSignal | None
    debate_result: DebateResult | None
    risk_result: RiskAssessment | None
    submitted: bool
    error: str
```

## Graph Construction

```python
def _build_graph(self) -> Any:
    g = StateGraph(AgentState)

    g.add_node("generate", self._node_generate)
    g.add_node("debate",   self._node_debate)
    g.add_node("risk",     self._node_risk)
    g.add_node("submit",   self._node_submit)

    g.set_entry_point("generate")

    g.add_conditional_edges("generate", self._route_after_generate, {
        "debate": "debate",
        END: END,
    })
    g.add_edge("debate", "risk")
    g.add_conditional_edges("risk", self._route_after_risk, {
        "submit": "submit",
        END: END,
    })
    g.add_edge("submit", END)

    return g.compile()
```

Rules:
- One entry point via `set_entry_point()`
- Use `add_edge` for guaranteed sequential flow; `add_conditional_edges` for branching
- Routing function return values must match keys in the edge mapping dict (or `END`)

## Node Functions

Signature: `(self, state: AgentState) -> AgentState`

```python
def _node_generate(self, state: AgentState) -> AgentState:
    signal = self.signal_agent.generate(state["market_snapshot"])
    return {**state, "signal": signal}

def _node_debate(self, state: AgentState) -> AgentState:
    if not state.get("signal"):
        return state                     # guard clause — skip and pass state through
    debate = self.debate_agent.debate(state["signal"])
    return {**state, "debate_result": debate}
```

Rules:
- Mutate state via dict spread `{**state, "key": value}` — never mutate in place
- Guard at top of node if upstream dependency might be absent
- No side effects in nodes; keep them deterministic

## Routing Functions

Signature: `(self, state: AgentState) -> str`

```python
def _route_after_generate(self, state: AgentState) -> str:
    return "debate" if state.get("signal") else END

def _route_after_risk(self, state: AgentState) -> str:
    risk = state.get("risk_result")
    if risk and not risk.is_blocked:
        return "submit"
    log.info("orchestrator.signal_blocked",
             symbol=getattr(state.get("signal"), "symbol", "?"),
             reason=getattr(risk, "block_reason", "unknown"))
    return END
```

Rules:
- Return a string matching a key in `add_conditional_edges` mapping, or the `END` sentinel
- Log at routing decision points so blocked paths are traceable

## Execution

```python
def run(self, market_snapshot: dict[str, Any]) -> AgentState:
    initial: AgentState = {
        "market_snapshot": market_snapshot,
        "signal": None,
        "debate_result": None,
        "risk_result": None,
        "submitted": False,
    }
    return self._graph.invoke(initial)
```

- Initialize all fields (even optional ones) to avoid KeyErrors in nodes
- `graph.invoke(state)` runs synchronously and returns the accumulated final state
- Check `result.get("error")` or domain fields to determine outcome

## Logging

Use module-level structlog. All event names use dot-notation. Pass context as kwargs — no f-strings.

```python
import structlog
log = structlog.get_logger(__name__)

log.info("orchestrator.submitted", signal_id=signal.signal_id, accepted=accepted)
log.error("orchestrator.http_error", error=str(exc))
```
