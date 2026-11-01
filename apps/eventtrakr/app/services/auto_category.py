"""Categories a title makes certain, whatever the source's own category.

A source carries all sorts (Kultunaut, BrugByen, a district calendar), so most
events take their source's category. A few kinds are clear from the title
alone and get their own category wherever they're listed.
"""

from __future__ import annotations

import re

FLEA_MARKETS_KEY = "flea-markets"

# Loppemarked, loppetorv, Loppefest, kræmmermarked, genbrugsmarked, flea market --
# but not Loppen, Christiania's music venue ("Koncert på Loppen").
_FLEA_MARKET_RE = re.compile(
    r"\bloppe(?!n\b)|kr(?:æ|ae)mmermarked|genbrugsmarked|\bflea\s*-?\s*market",
    re.IGNORECASE,
)


def category_key_for_title(title: str | None) -> str | None:
    if title and _FLEA_MARKET_RE.search(title):
        return FLEA_MARKETS_KEY
    return None
