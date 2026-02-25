"""
News Sentiment Analyzer
Uses Scrapling for stealthy web scraping of crypto news.
Based on @hasantoxr tweet: Scrapling bypasses bot detection.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SentimentAnalyzer:
    """
    Scrapes crypto news headlines and analyzes sentiment.
    Uses Scrapling for bot-detection bypass.
    """

    NEWS_SOURCES = [
        "https://cryptopanic.com/news/bitcoin/",
        "https://cryptopanic.com/news/ethereum/",
    ]

    def __init__(self):
        self._scrapling_available = False
        self._try_init()

    def _try_init(self):
        try:
            import scrapling  # noqa
            self._scrapling_available = True
            logger.info("Scrapling available for sentiment analysis")
        except ImportError:
            logger.info("Scrapling not installed - sentiment analysis disabled")
            logger.info("Install with: pip install 'scrapling[ai]' --break-system-packages")

    async def get_sentiment(self, keywords: list[str]) -> dict:
        """
        Get sentiment score for keywords from news headlines.

        Returns:
        {
            "score": -1.0 to 1.0 (negative to positive),
            "headlines": [list of relevant headlines],
            "signal": "BULLISH/BEARISH/NEUTRAL"
        }
        """
        if not self._scrapling_available:
            return {"score": 0, "headlines": [], "signal": "NEUTRAL"}

        try:
            headlines = await self._fetch_headlines()
            relevant = [h for h in headlines if any(k.lower() in h.lower() for k in keywords)]
            score = self._score_headlines(relevant)

            signal = "NEUTRAL"
            if score > 0.3:
                signal = "BULLISH"
            elif score < -0.3:
                signal = "BEARISH"

            return {"score": score, "headlines": relevant[:5], "signal": signal}
        except Exception as e:
            logger.warning(f"Sentiment fetch failed: {e}")
            return {"score": 0, "headlines": [], "signal": "NEUTRAL"}

    async def _fetch_headlines(self) -> list[str]:
        """Fetch news headlines using Scrapling."""
        try:
            from scrapling import Fetcher
            fetcher = Fetcher(auto_match=True)

            headlines = []
            loop = asyncio.get_event_loop()

            for url in self.NEWS_SOURCES:
                try:
                    page = await loop.run_in_executor(
                        None, lambda u=url: fetcher.get(u)
                    )
                    if page:
                        # Extract headline text elements
                        for el in page.css("a.news-article, .title, h3, .headline"):
                            text = el.text.strip()
                            if len(text) > 20:
                                headlines.append(text)
                except Exception:
                    continue

            return headlines[:50]
        except Exception as e:
            logger.warning(f"Scrapling fetch error: {e}")
            return []

    def _score_headlines(self, headlines: list[str]) -> float:
        """Simple keyword-based sentiment scoring."""
        if not headlines:
            return 0.0

        positive_words = [
            "surge", "rally", "bullish", "breakout", "record", "high",
            "gain", "profit", "adoption", "upgrade", "buy", "long",
            "moon", "pump", "support", "recover",
        ]
        negative_words = [
            "crash", "dump", "bearish", "drop", "fall", "fear",
            "ban", "hack", "scam", "loss", "sell", "short",
            "decline", "collapse", "warning", "risk",
        ]

        pos_count = 0
        neg_count = 0

        for h in headlines:
            lower = h.lower()
            pos_count += sum(1 for w in positive_words if w in lower)
            neg_count += sum(1 for w in negative_words if w in lower)

        total = pos_count + neg_count
        if total == 0:
            return 0.0

        return (pos_count - neg_count) / total


# Singleton
_sentiment: Optional[SentimentAnalyzer] = None


def get_sentiment_analyzer() -> SentimentAnalyzer:
    global _sentiment
    if _sentiment is None:
        _sentiment = SentimentAnalyzer()
    return _sentiment
