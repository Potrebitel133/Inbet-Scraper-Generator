"""
Stake rules per ticket type (5..10 matches), as set in the dashboard.

Each type has its own rule, saved in settings.json next to the app so it is
remembered between runs:

    {"mode": "formula", "fixed": 100, "formula": "20000/(I12-1)"}

mode "fixed"    stake = the fixed amount, used exactly as typed
mode "formula"  stake = the formula, rounded UP to the next multiple of 5
                (the same round-up the bot has always used, resolver.round_up_to_5)

Formula variables:
    I12   the sheet's Product cell (its total odds as written in the sheet)
    ODDS  the live total odds the site confirms just before the ticket is made

Only numbers, I12, ODDS, + - * / and brackets are accepted, so a typo can
never run anything else. An optional "wager =" / "stake =" in front is ignored.
"""

import ast
import json
import operator
import os
import re

import config
from resolver import round_up_to_5

MODES = ("fixed", "formula")
VARIABLES = ("I12", "ODDS")
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv}
_PREFIX = re.compile(r"^\s*(?:(?:wager|stake)\s*)?=\s*", re.IGNORECASE)


class StakeError(ValueError):
    """A stake rule or formula that cannot produce a valid stake."""


# --- rules -------------------------------------------------------------------
def default_rule():
    return {"mode": "formula", "fixed": config.DEFAULT_FIXED_STAKE,
            "formula": config.DEFAULT_STAKE_FORMULA}


def default_settings():
    return {"match_type": config.DEFAULT_MATCH_TYPE,
            "rules": {str(t): default_rule() for t in config.MATCH_TYPES}}


def load_settings(path):
    """Saved dashboard settings, falling back to defaults for anything missing or broken."""
    settings = default_settings()
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return settings
    if not isinstance(saved, dict):
        return settings
    if saved.get("match_type") in config.MATCH_TYPES:
        settings["match_type"] = saved["match_type"]
    for t, rule in (saved.get("rules") or {}).items():
        if t in settings["rules"] and isinstance(rule, dict):
            merged = dict(settings["rules"][t])
            if rule.get("mode") in MODES:
                merged["mode"] = rule["mode"]
            if rule.get("fixed") is not None:
                merged["fixed"] = rule["fixed"]
            if isinstance(rule.get("formula"), str) and rule["formula"].strip():
                merged["formula"] = rule["formula"]
            settings["rules"][t] = merged
    return settings


def save_settings(path, settings):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --- formula -----------------------------------------------------------------
def _clean(formula):
    text = _PREFIX.sub("", str(formula or "")).strip()
    return text.replace("×", "*").replace("÷", "/")


def _parse(formula):
    text = _clean(formula)
    if not text:
        raise StakeError("The stake formula is empty.")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise StakeError(f"The stake formula '{formula}' is not valid. Example: 20000/(I12-1)")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Load,
                             ast.UAdd, ast.USub, *_OPS)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            continue
        if isinstance(node, ast.Name) and node.id.upper() in VARIABLES:
            continue
        what = node.id if isinstance(node, ast.Name) else type(node).__name__
        raise StakeError(f"The stake formula '{formula}' contains '{what}'. "
                         f"Use only numbers, I12, ODDS, + - * / and brackets.")
    return tree


def formula_variables(formula):
    """Which of I12 / ODDS a formula uses (raises StakeError if it is invalid)."""
    tree = _parse(formula)
    return {n.id.upper() for n in ast.walk(tree) if isinstance(n, ast.Name)}


def evaluate(formula, i12=None, odds=None):
    """The formula's raw value (not rounded)."""
    tree = _parse(formula)
    values = {"I12": i12, "ODDS": odds}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            name = node.id.upper()
            if values[name] is None:
                raise StakeError("I12 (the Product cell) is empty or not a number."
                                 if name == "I12" else "The live total odds are not known yet.")
            return float(values[name])
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise StakeError(f"The stake formula divides by zero (I12 = {i12}).")
            return _OPS[type(node.op)](left, right)
        raise StakeError("Unsupported formula.")  # unreachable: _parse already checked

    return ev(tree)


# --- stake -------------------------------------------------------------------
def check_rule(rule):
    """Raise StakeError if a rule can never give a stake (bad mode/amount/formula)."""
    if not isinstance(rule, dict) or rule.get("mode") not in MODES:
        raise StakeError("Choose Fixed stake or Formula.")
    if rule["mode"] == "fixed":
        try:
            amount = float(rule.get("fixed"))
        except (TypeError, ValueError):
            raise StakeError("The fixed stake must be a number, for example 100.")
        if amount <= 0:
            raise StakeError("The fixed stake must be more than 0.")
    else:
        formula_variables(rule.get("formula"))


def compute_stake(rule, i12=None, odds=None):
    """Stake for one ticket under `rule`. Fixed: as typed. Formula: rounded up to the next 5."""
    check_rule(rule)
    if rule["mode"] == "fixed":
        amount = float(rule["fixed"])
        return int(amount) if amount.is_integer() else amount
    value = evaluate(rule["formula"], i12=i12, odds=odds)
    if value <= 0:
        raise StakeError(f"The stake formula gives {value:g}, which is not a valid stake.")
    return round_up_to_5(value)


def describe(rule):
    """One line for logs and confirm dialogs."""
    if rule.get("mode") == "fixed":
        return f"Fixed stake {rule.get('fixed')}"
    return f"Formula {_clean(rule.get('formula'))}, rounded up to the next 5"


def uses_live_odds(rule):
    return rule.get("mode") == "formula" and "ODDS" in formula_variables(rule.get("formula"))


def preview_stakes(rule, products, sheets):
    """({sheet: stake or None}, {sheet: problem}) before a run.

    The stake is None when it can only be known from the live odds (formula uses
    ODDS). A sheet lands in problems when its stake can already be seen to be
    impossible: Product (I12) missing for a formula that uses it, division by
    zero, or a result of 0 or less.
    """
    stakes, problems = {}, {}
    check_rule(rule)
    needs = formula_variables(rule["formula"]) if rule["mode"] == "formula" else set()
    for sheet in sheets:
        i12 = products.get(sheet)
        try:
            if "ODDS" in needs:
                if "I12" in needs and i12 is None:
                    raise StakeError("I12 (the Product cell) is empty or not a number.")
                stakes[sheet] = None
            else:
                stakes[sheet] = compute_stake(rule, i12=i12)
        except StakeError as e:
            problems[sheet] = f"{config.PRODUCT_CELL} = {i12 if i12 is not None else 'empty'}: {e}"
    return stakes, problems


def stake_problems(rule, products, sheets):
    """Just the problems part of preview_stakes."""
    return preview_stakes(rule, products, sheets)[1]


if __name__ == "__main__":
    r = default_rule()
    # The client's own example sheets (I12 values) -> 275, 145, 100, 40.
    assert [compute_stake(r, i12=v) for v in
            (74.95670826, 140.84824532624998, 210.92817704363998, 554.414846938445)] == [275, 145, 100, 40]
    assert compute_stake({"mode": "fixed", "fixed": 100}) == 100
    assert compute_stake({"mode": "fixed", "fixed": "12.5"}) == 12.5
    assert compute_stake({"mode": "formula", "formula": "wager = 20000/(i12-1)"}, i12=74.95670826) == 275
    assert compute_stake({"mode": "formula", "formula": "20000/ODDS"}, odds=250) == 80
    for bad in ("", "20000/(I12-1", "__import__('os')", "I12**2", "abs(I12)", "X+1"):
        try:
            check_rule({"mode": "formula", "formula": bad})
            raise AssertionError(f"accepted {bad!r}")
        except StakeError:
            pass
    for bad_i12 in (None, 1):
        try:
            compute_stake(r, i12=bad_i12)
            raise AssertionError("accepted a missing / zero-dividing I12")
        except StakeError:
            pass
    print("stake_rules self-check ok")
