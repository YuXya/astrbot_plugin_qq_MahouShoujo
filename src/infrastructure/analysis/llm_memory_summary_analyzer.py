from __future__ import annotations

from .analyzers.memory_summary_analyzer import MemorySummaryAnalyzer


class LLMMemorySummaryAnalyzer:
    def __init__(self, context, config_manager, editable_manager=None):
        self.analyzer = MemorySummaryAnalyzer(context, config_manager, editable_manager)

    async def summarize_interactions(self, **kwargs):
        return await self.analyzer.summarize_interactions(**kwargs)

    async def compact_memories(self, **kwargs):
        return await self.analyzer.compact_memories(**kwargs)
