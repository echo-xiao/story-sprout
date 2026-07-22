"""Multi-agent architecture for picture book generation.

Agents:
- AnalyzerAgent: text extraction, NLP analysis, scene selection
- WriterAgent: text simplification for target age group
"""

from src.agents.analyzer import AnalyzerAgent
from src.agents.writer import WriterAgent

__all__ = ["AnalyzerAgent", "WriterAgent"]
