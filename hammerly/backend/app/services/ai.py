"""AI Listing Studio. Uses the Anthropic API when a key is set, otherwise a clearly labelled heuristic draft."""
from __future__ import annotations

import base64
import json
import re

import httpx

from ..config import get_settings

CATEGORIES = ["Cards", "Sneakers", "Vinyl", "Art toys", "Memorabilia", "Art"]
_KEYWORDS = {"Cards": ["card", "holo", "foil", "grade", "pack"], "Sneakers": ["sneaker", "shoe", "runner", "size", "dunk"],
             "Vinyl": ["vinyl", "lp", "record", "pressing", "album"], "Art toys": ["toy", "figure", "vinyl toy", "edition of"],
             "Memorabilia": ["signed", "autograph", "jersey", "bat", "ticket"], "Art": ["print", "poster", "painting", "canvas"]}

PROMPT = """You are helping a seller list a collectible. Look at the photos and the seller's note.
Reply with JSON only, no prose, with keys:
title (max 90 chars, factual, no hype), category (one of %s), description (2-4 plain sentences describing only what is visible
or stated), condition_grade (number 1-10 or null if you cannot judge), condition_notes (what you based the grade on),
confidence (low, medium, or high). Do not invent authenticity claims, edition numbers, or provenance." """ % CATEGORIES


def guess_category(text: str) -> str:
    t = text.lower()
    best, score = "Cards", 0
    for cat, words in _KEYWORDS.items():
        s = sum(1 for w in words if w in t)
        if s > score:
            best, score = cat, s
    return best


def heuristic_draft(hint: str) -> dict:
    hint = (hint or "").strip()
    cat = guess_category(hint)
    return {"title": (hint[:90] or "Untitled collectible"), "category": cat,
            "description": hint or "Add a description of the item's condition and history.",
            "condition_grade": None, "condition_notes": "No AI key is configured, so condition was not assessed.",
            "confidence": "low", "source": "heuristic"}


async def draft_from_images(images: list[tuple[bytes, str]], hint: str) -> dict:
    s = get_settings()
    if not s.anthropic_api_key or not images:
        return heuristic_draft(hint)
    content = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(b).decode()}}
               for b, mime in images[:4]]
    content.append({"type": "text", "text": PROMPT + f"\nSeller note: {hint or '(none)'}"})
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post("https://api.anthropic.com/v1/messages",
                             headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"},
                             json={"model": s.anthropic_model, "max_tokens": 700,
                                   "messages": [{"role": "user", "content": content}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        cat = data.get("category") if data.get("category") in CATEGORIES else guess_category(hint + " " + str(data.get("title", "")))
        grade = data.get("condition_grade")
        return {"title": str(data.get("title", ""))[:90], "category": cat,
                "description": str(data.get("description", "")),
                "condition_grade": float(grade) if isinstance(grade, (int, float)) and 1 <= grade <= 10 else None,
                "condition_notes": str(data.get("condition_notes", "")),
                "confidence": data.get("confidence") if data.get("confidence") in {"low", "medium", "high"} else "low",
                "source": "ai"}
    except Exception as e:  # network, auth, or parse failure: never block listing
        d = heuristic_draft(hint)
        d["condition_notes"] = f"The AI draft failed ({type(e).__name__}); this is a basic draft instead."
        return d


def recommend_format(comp_count: int, median_cents: int | None, quick_sale: bool = False) -> dict:
    """Format Engine: choose the mechanism that fits how well buyers can anchor the price."""
    if quick_sale:
        return {"format": "dutch", "why": "You want a fast sale. The price falls until someone claims it, down to your floor."}
    if comp_count >= 6 and (median_cents or 0) < 50_000:
        return {"format": "english", "why": "Plenty of comparable sales exist, so buyers know the price. Open bidding with Fair-Close works well."}
    if comp_count < 4:
        return {"format": "sealed", "why": "Few comparable sales exist, so nobody can anchor the price. Sealed bids avoid a guess, and the winner pays the second-highest bid."}
    return {"format": "english", "why": "Some comparable sales exist. Open bidding lets buyers find the price."}
