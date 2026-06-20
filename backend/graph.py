from langgraph.graph import StateGraph, START, END

# Custom Imports
from backend.state import State
from backend.nodes import router_node, research_node, orchestrator_node, worker_node, route_next, fanout
from backend.reducer import reducer_subgraph

# Build main graph
g = StateGraph(State)
g.add_node("router", router_node)
g.add_node("research", research_node)
g.add_node("orchestrator", orchestrator_node)
g.add_node("worker", worker_node)
g.add_node("reducer", reducer_subgraph)

g.add_edge(START, "router")
g.add_conditional_edges("router", route_next, {"research": "research", "orchestrator": "orchestrator"})
g.add_edge("research", "orchestrator")
g.add_conditional_edges("orchestrator", fanout, ["worker"])
g.add_edge("worker", "reducer")
g.add_edge("reducer", END)

# Export the compiled app
app = g.compile()