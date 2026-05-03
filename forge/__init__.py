"""
FORGE — Free Open Reasoning & Generation Engine
================================================
The world's most powerful free AI agent.
Self-evolving. Zero cost. Forever.

  forge.ask("write me a web scraper")     # auto-selects best free model
  forge.code("fix this bug", file="x.py") # routes to coding specialist
  forge.research("quantum computing")     # deep multi-model research

GitHub : https://github.com/trenstudio/forge
Website: https://www.trenstudio.com/FORGE
License: MIT
"""

__version__ = "1.1.7"
__author__  = "TREN Studio"
__license__ = "MIT"

from forge.core.router   import ForgeRouter
from forge.core.session  import ForgeSession
from forge.brain.operator import ForgeOperator
from forge.memory.graph  import MemoryGraph
from forge.runtime import ForgeAgentRuntime

# One-line convenience API
_default_session: ForgeSession | None = None

def _session() -> ForgeSession:
    global _default_session
    if _default_session is None:
        _default_session = ForgeSession()
    return _default_session

def ask(prompt: str, **kwargs) -> str:
    """Send a prompt. FORGE picks the best free model automatically."""
    return _session().ask(prompt, **kwargs)

def code(prompt: str, **kwargs) -> str:
    """Coding task — routed to the strongest available coding model."""
    return _session().ask(prompt, task_type="code", **kwargs)

def research(prompt: str, **kwargs) -> str:
    """Deep research task with optional web search."""
    return _session().ask(prompt, task_type="research", **kwargs)

def operate(prompt: str, **kwargs) -> str:
    """Run the skill-based operator brain."""
    operator = ForgeOperator()
    return operator.handle_as_text(prompt, **kwargs)

__all__ = [
    "__version__",
    "ForgeRouter",
    "ForgeSession",
    "ForgeOperator",
    "ForgeAgentRuntime",
    "MemoryGraph",
    "ask",
    "code",
    "operate",
    "research",
]
