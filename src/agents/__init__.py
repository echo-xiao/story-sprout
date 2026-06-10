"""Multi-agent architecture for picture book generation.

Agents:
- AnalyzerAgent: text extraction, NLP analysis, scene selection
- WriterAgent: text simplification for target age group
- ArtistAgent: character sheets + page illustration generation
- QAAgent: per-page quality checks (spelling, consistency, style)
- OrchestratorAgent: Gemini-powered agent that coordinates the above
"""

from src.agents.analyzer import AnalyzerAgent
from src.agents.writer import WriterAgent
from src.agents.artist import ArtistAgent
from src.agents.qa import QAAgent
