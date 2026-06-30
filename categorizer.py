"""Transaction categorization with categories and subcategories.

Strategy (hybrid):
1. If an LLM API key is configured via environment variables, ask a free hosted
   model (OpenAI-compatible, e.g. Groq / OpenRouter) to pick a category.
2. Otherwise (or on any error / timeout), fall back to a fast built-in
   keyword/merchant heuristic that runs fully offline with no dependencies.

The learned merchant->category memory in the app takes priority over both, so
manual corrections always win on the next matching payment.

Configure the optional LLM with environment variables:
    LLM_API_KEY     - your API key (e.g. a free Groq key)
    LLM_BASE_URL    - default https://api.groq.com/openai/v1
    LLM_MODEL       - default llama-3.1-8b-instant
"""

import os
import re
import json
import urllib.request
import urllib.error

# Top-level categories the app understands.
CATEGORIES = [
    "Groceries",
    "Dining",
    "Transport",
    "Fuel",
    "Shopping",
    "Utilities",
    "Health",
    "Entertainment",
    "Travel",
    "Cash",
    "Other",
]

# Keyword -> (category, subcategory). Matched as whole words against the
# lowercased merchant name. Keep keywords lowercase.
_KEYWORDS = {
    # --- Groceries ---
    "market": ("Groceries", "Supermarket"),
    "supermarket": ("Groceries", "Supermarket"),
    "grocery": ("Groceries", "Supermarket"),
    "maxi": ("Groceries", "Supermarket"),
    "lidl": ("Groceries", "Supermarket"),
    "idea": ("Groceries", "Supermarket"),
    "aldi": ("Groceries", "Supermarket"),
    "tempo": ("Groceries", "Supermarket"),
    "univerexport": ("Groceries", "Supermarket"),
    "mercator": ("Groceries", "Supermarket"),
    "roda": ("Groceries", "Supermarket"),
    "shop&go": ("Groceries", "Convenience"),
    "minimax": ("Groceries", "Supermarket"),
    "pijaca": ("Groceries", "Market stall"),
    "green market": ("Groceries", "Market stall"),
    "butcher": ("Groceries", "Butcher"),
    "mesara": ("Groceries", "Butcher"),
    # --- Dining ---
    "restaurant": ("Dining", "Restaurant"),
    "restoran": ("Dining", "Restaurant"),
    "kafana": ("Dining", "Restaurant"),
    "cafe": ("Dining", "Cafe"),
    "caffe": ("Dining", "Cafe"),
    "coffee": ("Dining", "Cafe"),
    "kafa": ("Dining", "Cafe"),
    "starbucks": ("Dining", "Cafe"),
    "pizzeria": ("Dining", "Fast food"),
    "pizza": ("Dining", "Fast food"),
    "burger": ("Dining", "Fast food"),
    "mcdonald": ("Dining", "Fast food"),
    "kfc": ("Dining", "Fast food"),
    "fast food": ("Dining", "Fast food"),
    "wolt": ("Dining", "Delivery"),
    "glovo": ("Dining", "Delivery"),
    "donesi": ("Dining", "Delivery"),
    "bar": ("Dining", "Bar"),
    "pub": ("Dining", "Bar"),
    "bakery": ("Dining", "Bakery"),
    "pekara": ("Dining", "Bakery"),
    # --- Transport ---
    "uber": ("Transport", "Ride-hailing"),
    "bolt": ("Transport", "Ride-hailing"),
    "cardo": ("Transport", "Ride-hailing"),
    "yandex": ("Transport", "Ride-hailing"),
    "taxi": ("Transport", "Taxi"),
    "bus": ("Transport", "Public transit"),
    "metro": ("Transport", "Public transit"),
    "tram": ("Transport", "Public transit"),
    "train": ("Transport", "Public transit"),
    "gsp": ("Transport", "Public transit"),
    "bus plus": ("Transport", "Public transit"),
    "parking": ("Transport", "Parking"),
    "parking servis": ("Transport", "Parking"),
    "toll": ("Transport", "Tolls"),
    "putarina": ("Transport", "Tolls"),
    # --- Fuel ---
    "nis petrol": ("Fuel", "Gas station"),
    "petrol": ("Fuel", "Gas station"),
    "gazprom": ("Fuel", "Gas station"),
    "omv": ("Fuel", "Gas station"),
    "mol": ("Fuel", "Gas station"),
    "lukoil": ("Fuel", "Gas station"),
    "eko": ("Fuel", "Gas station"),
    "gas station": ("Fuel", "Gas station"),
    "fuel": ("Fuel", "Gas station"),
    "benzin": ("Fuel", "Gas station"),
    "pumpa": ("Fuel", "Gas station"),
    "charging": ("Fuel", "EV charging"),
    "ev charge": ("Fuel", "EV charging"),
    # --- Shopping ---
    "mall": ("Shopping", "General"),
    "store": ("Shopping", "General"),
    "shop": ("Shopping", "General"),
    "zara": ("Shopping", "Clothing"),
    "h&m": ("Shopping", "Clothing"),
    "bershka": ("Shopping", "Clothing"),
    "pull&bear": ("Shopping", "Clothing"),
    "c&a": ("Shopping", "Clothing"),
    "nike": ("Shopping", "Clothing"),
    "adidas": ("Shopping", "Clothing"),
    "amazon": ("Shopping", "Online"),
    "aliexpress": ("Shopping", "Online"),
    "ebay": ("Shopping", "Online"),
    "kupujemprodajem": ("Shopping", "Online"),
    "tehnomanija": ("Shopping", "Electronics"),
    "gigatron": ("Shopping", "Electronics"),
    "winwin": ("Shopping", "Electronics"),
    "ctrl": ("Shopping", "Electronics"),
    "ikea": ("Shopping", "Home"),
    "jysk": ("Shopping", "Home"),
    "uradi sam": ("Shopping", "Home"),
    "decathlon": ("Shopping", "Sports"),
    "intersport": ("Shopping", "Sports"),
    "dm": ("Shopping", "Drugstore"),
    "lilly drogerie": ("Shopping", "Drugstore"),
    # --- Utilities ---
    "electric": ("Utilities", "Electricity"),
    "eps": ("Utilities", "Electricity"),
    "struja": ("Utilities", "Electricity"),
    "infostan": ("Utilities", "Communal"),
    "water": ("Utilities", "Water"),
    "vodovod": ("Utilities", "Water"),
    "grejanje": ("Utilities", "Heating"),
    "toplana": ("Utilities", "Heating"),
    "telekom": ("Utilities", "Telecom"),
    "mts": ("Utilities", "Telecom"),
    "yettel": ("Utilities", "Telecom"),
    "a1": ("Utilities", "Telecom"),
    "sbb": ("Utilities", "Internet/TV"),
    "internet": ("Utilities", "Internet/TV"),
    "phone": ("Utilities", "Telecom"),
    "bill": ("Utilities", "Bills"),
    "racun": ("Utilities", "Bills"),
    # --- Health ---
    "pharmacy": ("Health", "Pharmacy"),
    "apoteka": ("Health", "Pharmacy"),
    "benu": ("Health", "Pharmacy"),
    "lilly": ("Health", "Pharmacy"),
    "hospital": ("Health", "Medical"),
    "clinic": ("Health", "Medical"),
    "klinika": ("Health", "Medical"),
    "dom zdravlja": ("Health", "Medical"),
    "poliklinika": ("Health", "Medical"),
    "doctor": ("Health", "Medical"),
    "dentist": ("Health", "Dental"),
    "stomatolog": ("Health", "Dental"),
    "optika": ("Health", "Optics"),
    "gym": ("Health", "Fitness"),
    "teretana": ("Health", "Fitness"),
    "fitpass": ("Health", "Fitness"),
    # --- Entertainment ---
    "cinema": ("Entertainment", "Cinema"),
    "bioskop": ("Entertainment", "Cinema"),
    "cineplexx": ("Entertainment", "Cinema"),
    "netflix": ("Entertainment", "Streaming"),
    "spotify": ("Entertainment", "Streaming"),
    "youtube": ("Entertainment", "Streaming"),
    "hbo": ("Entertainment", "Streaming"),
    "disney": ("Entertainment", "Streaming"),
    "apple music": ("Entertainment", "Streaming"),
    "steam": ("Entertainment", "Games"),
    "playstation": ("Entertainment", "Games"),
    "xbox": ("Entertainment", "Games"),
    "nintendo": ("Entertainment", "Games"),
    "epic games": ("Entertainment", "Games"),
    "theater": ("Entertainment", "Events"),
    "pozoriste": ("Entertainment", "Events"),
    "concert": ("Entertainment", "Events"),
    "koncert": ("Entertainment", "Events"),
    "ticket": ("Entertainment", "Events"),
    "gigstix": ("Entertainment", "Events"),
    # --- Travel ---
    "hotel": ("Travel", "Accommodation"),
    "hostel": ("Travel", "Accommodation"),
    "booking": ("Travel", "Accommodation"),
    "airbnb": ("Travel", "Accommodation"),
    "airlines": ("Travel", "Flights"),
    "air serbia": ("Travel", "Flights"),
    "wizz": ("Travel", "Flights"),
    "ryanair": ("Travel", "Flights"),
    "lufthansa": ("Travel", "Flights"),
    "flight": ("Travel", "Flights"),
    "aerodrom": ("Travel", "Flights"),
    "rent a car": ("Travel", "Car rental"),
    "rentacar": ("Travel", "Car rental"),
    # --- Cash ---
    "atm": ("Cash", "ATM withdrawal"),
    "bankomat": ("Cash", "ATM withdrawal"),
    "withdrawal": ("Cash", "ATM withdrawal"),
    "cash": ("Cash", "ATM withdrawal"),
}

# category -> sorted list of known subcategories (handy for the UI / API).
SUBCATEGORIES = {}
for _cat, _sub in _KEYWORDS.values():
    SUBCATEGORIES.setdefault(_cat, set()).add(_sub)
SUBCATEGORIES = {c: sorted(s) for c, s in SUBCATEGORIES.items()}

# Match longer keywords first so e.g. "nis petrol" wins over "petrol".
_SORTED_KEYWORDS = sorted(_KEYWORDS.items(), key=lambda kv: len(kv[0]), reverse=True)


def _heuristic(merchant: str):
    """Return (category, subcategory) from the offline keyword map."""
    text = (merchant or "").lower().strip()
    if not text:
        return "Other", None
    for keyword, (category, sub) in _SORTED_KEYWORDS:
        # Whole-word / substring match: word boundaries avoid false hits like
        # "dis" inside "disney".
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            return category, sub
    return "Other", None


def _llm(merchant: str, amount):
    """Return (category, subcategory) from an LLM, or (None, None)."""
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return None, None

    base_url = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model = os.environ.get("LLM_MODEL", "llama-3.1-8b-instant")

    prompt = (
        "Categorize this card payment. Choose one category from this list:\n"
        f"{', '.join(CATEGORIES)}.\n"
        "Also give a short subcategory (1-2 words).\n"
        f"Merchant: {merchant}\nAmount: {amount}\n"
        'Reply ONLY as JSON: {"category": "...", "subcategory": "..."}'
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 40,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        parsed = json.loads(content)
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return None, None

    raw_cat = str(parsed.get("category", "")).lower()
    sub = parsed.get("subcategory") or None
    for cat in CATEGORIES:
        if cat.lower() in raw_cat:
            return cat, sub
    return None, None


def categorize_detailed(merchant: str, amount=None):
    """Return {'category': ..., 'subcategory': ...} using LLM then heuristic."""
    cat, sub = _llm(merchant, amount)
    if cat is None:
        cat, sub = _heuristic(merchant)
    return {"category": cat, "subcategory": sub}


def llm_category(merchant: str, amount=None):
    """LLM-only categorization (used when DB rules don't match). None if no key."""
    cat, sub = _llm(merchant, amount)
    if cat is None:
        return None
    return {"category": cat, "subcategory": sub}


def categorize(merchant: str, amount=None) -> str:
    """Return just the top-level category (backward-compatible)."""
    return categorize_detailed(merchant, amount)["category"]
