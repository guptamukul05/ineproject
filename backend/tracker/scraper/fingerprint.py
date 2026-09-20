"""
Structure-change detection.

We hash a coarse description of the page's shape — which of our known
selectors matched, the tag path down to the price, and the sorted class names
on that element's ancestors. Content changes (a new price) leave the hash
alone; a redesign changes it. When the hash moves, we raise an alert instead
of quietly reading the wrong element.
"""

import hashlib

from . import config


def _ancestor_signature(el, depth=4):
    parts = []
    node = el
    for _ in range(depth):
        if node is None or not getattr(node, "name", None):
            break
        classes = sorted(node.get("class", []) if hasattr(node, "get") else [])
        parts.append(f"{node.name}.{'.'.join(classes)}")
        node = node.parent
    return ">".join(reversed(parts))


def fingerprint_soup(soup) -> str:
    matched = []
    for group, selectors in (
        ("price", config.PRICE_SELECTORS),
        ("stock", config.STOCK_SELECTORS),
        ("name", config.NAME_SELECTORS),
    ):
        for sel in selectors:
            els = soup.select(sel)
            if els:
                matched.append(f"{group}:{sel}:{_ancestor_signature(els[0])}")
                break
        else:
            matched.append(f"{group}:MISSING")

    matched.append(f"forms:{len(soup.find_all('form'))}")
    matched.append(f"imgs:{'many' if len(soup.find_all('img')) > 5 else 'few'}")
    blob = "|".join(matched)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:32]


def fingerprint_json(payload) -> str:
    """Shape hash for a JSON record: the sorted key names, not the values."""
    if isinstance(payload, dict):
        keys = sorted(payload.keys())
    elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
        keys = sorted(payload[0].keys())
    else:
        keys = [type(payload).__name__]
    return hashlib.sha1("|".join(keys).encode("utf-8")).hexdigest()[:32]
