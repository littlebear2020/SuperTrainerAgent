"""Content generation sub-agents: outline, quiz, script.

Three LangGraph StateGraphs that produce structured training content.
Registered in ``langgraph.json`` and invoked via the Gateway router.
"""

from .outline import make_outline_agent
from .quiz import make_quiz_agent
from .script import make_script_agent

__all__ = [
    "make_outline_agent",
    "make_quiz_agent",
    "make_script_agent",
]
