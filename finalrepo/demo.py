"""A safety net for live demos.

For one prepared case (demo_case.json) the app still runs every live AI step first. If a step
fails, returns nothing, or takes longer than DEMO_WAIT seconds, the saved answer for that step is
shown instead, and the screen says it is a saved answer. Nothing saved is ever presented as live AI.
Turn the whole thing off with DEMO_CASE=0 in .env.
"""
import copy, json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config

DATA = json.loads((Path(__file__).parent / "demo_case.json").read_text())
POOL = ThreadPoolExecutor(max_workers=8)


def matches(crop, text):
    """True when a request is the prepared demo case."""
    if not config.DEMO_CASE:
        return False
    rule, text = DATA["match"], f"{crop} {text}".lower()
    return rule["crop"] in text and any(word in text for word in rule["any_of"])


def is_demo(case):
    return matches(case.get("crop", ""), f"{case.get('description', '')} {case.get('likely_issue', '')}")


def saved(step, reason):
    out = copy.deepcopy(DATA[step])
    out.update(prepared=True, prepared_reason=reason)
    return out


def live_or_saved(case, step, live, good, shape):
    """Run the live step. For the demo case only, fall back to the saved answer when it does not deliver.

    live:  function that does the real work        good: function(result) -> True if it is usable
    shape: function(saved_dict) -> result in the same form the live step returns
    """
    if not is_demo(case):
        return live()
    future = POOL.submit(live)
    try:
        result = future.result(timeout=config.DEMO_WAIT)
        if good(result):
            return result
        reason = result.get("reason") or result.get("error") or "the live step returned nothing usable"
        if not config.AI_PROVIDER and step != "research":
            reason = "no AI key is set"
    except TimeoutError:
        reason = f"the live step took longer than {config.DEMO_WAIT} seconds"
    except Exception as e:
        reason = str(e)[:160] or type(e).__name__
    return shape(saved(step, reason))
