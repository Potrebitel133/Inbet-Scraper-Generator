
def cleanup_tournament_name(name):
    """
    Turn an Excel tournament name into candidate slugs to match the site's `path`.

    The site always names tournaments COUNTRY-FIRST, e.g.
        "bulgaria-first-professional-league", "norway-eliteserien".
    The Excel writes them league-first with the country after a hyphen, e.g.
        "First Professional League- Bulgaria", "Eliteserien- Norway".

    So the main trick is to SWAP around the hyphen (country to the front) rather
    than reverse every word — reversing breaks multi-word league names
    ("First Professional League" would become "league-professional-first").

    "First Professional League- Bulgaria"
        -> ["first-professional-league-bulgaria",     # as written
            "bulgaria-first-professional-league",     # country moved to front  <-- matches
            "bulgaria-league-professional-first"]     # full reverse (legacy fallback)
    """
    def slug(words):
        return "-".join(words)

    def norm(text):
        text = text.lower()
        text = "".join(c for c in text if c.isalnum() or c.isspace())
        return text.split()

    raw = name or ""
    # Split on the hyphen FIRST so we know which part is the country.
    parts = [p for p in raw.split("-") if p.strip()]

    candidates = []
    all_words = norm(raw.replace("-", " "))
    if all_words:
        candidates.append(slug(all_words))                 # as written
        if len(parts) == 2:
            left, right = norm(parts[0]), norm(parts[1])
            if left and right:
                candidates.append(slug(right + left))      # swap around the hyphen
                candidates.append(slug(left + right))      # same as written, kept for clarity
        candidates.append(slug(list(reversed(all_words)))) # full reverse (legacy)

    # Dedupe, preserve order
    slugs = []
    for s in candidates:
        if s and s not in slugs:
            slugs.append(s)
    return slugs


if __name__ == "__main__":
    # Self-check: every real Excel name must produce the site's actual path.
    cases = [
        ("First League- Bulgaria",              "bulgaria-first-league"),
        ("First Professional League- Bulgaria", "bulgaria-first-professional-league"),
        ("Eliteserien- Norway",                 "norway-eliteserien"),
        ("Superliga- Argentina",                "argentina-superliga"),
        ("  Superliga- Argentina ",             "argentina-superliga"),
    ]
    for name, expected in cases:
        slugs = cleanup_tournament_name(name)
        assert expected in slugs, f"{name!r} -> {slugs}, missing {expected!r}"
        print(f"OK  {name:38} -> {expected}")
    print("utils self-check ok")
