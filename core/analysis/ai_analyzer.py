"""
AI Market Analyzer
Uses Claude API to analyze prediction market mispricings.
Inspired by @thejayden tweet: AI-powered prediction market analysis.
"""
import asyncio
import logging
from typing import Optional

import config as cfg

logger = logging.getLogger(__name__)


class AIAnalyzer:
    """
    Uses Claude AI to identify potential mispricings in prediction markets.

    Workflow:
    1. Takes top markets with current probabilities
    2. Asks Claude to evaluate if any seem mispriced based on current events
    3. Returns actionable signals for the dashboard
    """

    def __init__(self):
        self._client = None
        self._ready = False
        self._last_analysis: list[dict] = []

    def initialize(self):
        if not cfg.ANTHROPIC_API_KEY:
            logger.info("No ANTHROPIC_API_KEY - AI analyzer disabled")
            return

        try:
            from anthropic import Anthropic
            self._client = Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
            self._ready = True
            logger.info("AI Analyzer ready")
        except Exception as e:
            logger.warning(f"AI Analyzer init failed: {e}")

    async def analyze_markets(self, markets: list[dict]) -> list[dict]:
        """
        Analyze a list of markets for potential mispricings.

        Returns list of signals:
        [{"market": name, "signal": "BUY_YES/BUY_NO/SKIP", "confidence": 0-100, "reason": str}]
        """
        if not self._ready or not markets:
            return []

        # Prepare market summary for Claude
        market_summaries = []
        for m in markets[:10]:  # Max 10 markets per analysis
            question = m.get("question", "")
            tokens = m.get("tokens") or []
            yes_prob = 0
            for t in tokens:
                if "YES" in (t.get("outcome") or "").upper():
                    yes_prob = float(t.get("price", 0)) * 100
                    break
            market_summaries.append(f"- {question}: YES={yes_prob:.1f}%")

        prompt = f"""You are an expert prediction market analyst. Analyze these Polymarket markets and identify potential mispricings.

Today's date: {__import__('datetime').date.today()}

Markets (with current YES probability):
{chr(10).join(market_summaries)}

For each market, evaluate:
1. Does the probability seem reasonable given current world events?
2. Is there a clear directional bet (BUY_YES or BUY_NO)?
3. What is your confidence level (0-100)?

Respond in this exact format for each mispriced market only:
SIGNAL: [market_question_first_5_words] | [BUY_YES/BUY_NO] | [confidence_0-100] | [brief_reason]

Only include markets where you see a clear edge. Skip if uncertain."""

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=500,
                    messages=[{"role": "user", "content": prompt}],
                )
            )

            text = response.content[0].text
            signals = self._parse_signals(text, markets)
            self._last_analysis = signals
            return signals

        except Exception as e:
            logger.warning(f"AI analysis failed: {e}")
            return []

    def _parse_signals(self, text: str, markets: list[dict]) -> list[dict]:
        """Parse Claude's response into structured signals."""
        signals = []
        for line in text.split("\n"):
            if not line.startswith("SIGNAL:"):
                continue
            try:
                parts = line.replace("SIGNAL:", "").strip().split("|")
                if len(parts) < 4:
                    continue
                market_hint = parts[0].strip().lower()
                action = parts[1].strip()
                confidence = int(parts[2].strip())
                reason = parts[3].strip()

                # Find matching market
                matched_market = None
                for m in markets:
                    q = m.get("question", "").lower()
                    if any(word in q for word in market_hint.split()[:3]):
                        matched_market = m
                        break

                if matched_market and action in ("BUY_YES", "BUY_NO") and confidence >= 60:
                    signals.append({
                        "market": matched_market.get("question", market_hint),
                        "condition_id": matched_market.get("condition_id", ""),
                        "action": action,
                        "confidence": confidence,
                        "reason": reason,
                    })
            except Exception:
                continue

        return signals

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def last_analysis(self) -> list[dict]:
        return self._last_analysis


# Singleton
_analyzer: Optional[AIAnalyzer] = None


def get_ai_analyzer() -> AIAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = AIAnalyzer()
        _analyzer.initialize()
    return _analyzer
