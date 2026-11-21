"""The AI: summaries, suggested solutions and contact lookups.

Works with Google Gemini (GEMINI_API_KEY) or Claude (ANTHROPIC_API_KEY). With neither key it
falls back to keyword rules so the app still runs offline (a safety net during a live demo).
"""
import json, re, time

import config

SYSTEM = """You read a rural farmer's description of a crop problem and summarise it.
Reply with ONLY a JSON object, no other text:
{
 "crop": "single lowercase crop name in English, or 'unknown'",
 "category": "disease | pest | soil | water | other",
 "symptoms": ["3-6 short English symptom phrases"],
 "severity": "low | medium | high",
 "likely_issue": "most likely cause in plain English, or 'unclear'",
 "confidence": 0.0-1.0,
 "research_terms": "2-5 English keywords a scientist would use, e.g. 'sorghum sugarcane aphid'",
 "summary": "2 sentences in plain English: what is happening, on which crop, how bad. Written so an expert can read it in 10 seconds.",
 "first_steps": ["2-3 short steps the farmer can safely do today, in the farmer's own language"]
}
Rules for first_steps:
- Plain words, one sentence each. Safe and low-cost: look closer, count affected plants, remove affected leaves, change watering, keep a sample or photo.
- Never give chemical names, doses or mixing instructions. If treatment may be needed, say an expert will confirm it.
severity is high when the problem is spreading fast, covers much of the field, or could wipe out the crop.
If you are unsure of the cause, say 'unclear' and set confidence low. Do not guess confidently."""

CATEGORY_WORDS = {
    "pest": ["worm", "aphid", "bug", "insect", "mite", "hole", "caterpillar", "chew", "beetle",
             "thrip", "webbing", "sticky", "larva", "grasshopper", "weevil"],
    "disease": ["spot", "rust", "blight", "mold", "mould", "rot", "wilt", "mildew", "powder",
                "lesion", "stripe", "blotch", "fungus", "scab", "canker", "curl", "collaps"],
    "soil": ["soil", "salt", "fertilizer", "nitrogen", "nutrient", "pale", "stunted"],
    "water": ["drought", "dry", "irrigat", "flood", "rolling", "burnt", "scorch", "heat"],
}
SYMPTOM_STEMS = {"yellow": "yellowing", "brown": "browning", "black": "blackening", "white": "white patches",
                 "orange": "orange colour", "spot": "spots", "hole": "holes", "wilt": "wilting",
                 "curl": "curling", "sticky": "sticky leaves", "powder": "powdery coating",
                 "stripe": "stripes", "web": "webbing", "rot": "rotting", "drop": "dropping leaves or fruit",
                 "stunt": "stunted growth", "roll": "leaf rolling", "dying": "plants dying", "collaps": "collapse"}
URGENT = ["whole field", "all of", "everything", "dying", "spreading fast", "half the", "wiped", "most of"]
CAUSE = {"pest": "an insect pest", "disease": "a plant disease", "soil": "a soil or nutrient problem",
         "water": "a water or heat problem", "other": "a problem that needs a closer look"}


def _fallback(text, crop_hint="", area=""):
    t = text.lower()
    crop = (crop_hint or "").strip().lower() or next((c for c in config.CROPS if re.search(rf"\b{c}", t)), "unknown")
    scores = {cat: sum(w in t for w in words) for cat, words in CATEGORY_WORDS.items()}
    category = max(scores, key=scores.get) if max(scores.values()) else "other"
    symptoms = [label for stem, label in SYMPTOM_STEMS.items() if stem in t][:6]
    severity = "high" if any(u in t for u in URGENT) or area in ("Most of it", "All of it") else "medium"
    crop_word = crop if crop != "unknown" else "crop"
    signs = ", ".join(symptoms) if symptoms else "signs not yet clear"
    return {
        "crop": crop, "category": category, "symptoms": symptoms, "severity": severity,
        "likely_issue": "unclear", "confidence": 0.3,
        "research_terms": f"{crop_word} {category}",
        "summary": f"Farmer reports what looks like {CAUSE[category]} on {crop_word}. Signs: {signs}. Severity looks {severity}.",
        "first_steps": ["Check ten plants in different parts of the field and count how many are affected.",
                        "Take a clear photo and keep one affected leaf in a bag for an expert.",
                        "Do not spray anything until the cause is confirmed."],
        "engine": "offline rules",
    }


def _json(text):
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def _claude(system, prompt, web, max_tokens):
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": prompt}]
    kwargs = dict(model=config.CLAUDE_MODEL, max_tokens=max(max_tokens, 3000), system=system, timeout=90)
    tools = [WEB_TOOL] if web else None

    def call():
        started = time.time()
        msg = client.messages.create(messages=messages, tools=tools, **kwargs) if tools else \
            client.messages.create(messages=messages, **kwargs)
        log(f"Claude {config.CLAUDE_MODEL} search={bool(tools)}: {msg.stop_reason} in {time.time() - started:.0f}s")
        return msg

    try:
        msg = call()
    except Exception as e:
        if not web:
            raise
        log(f"Claude web search call failed ({str(e)[:120]}); answering without search")
        tools = None  # web search not enabled for this key or model
        msg = call()
    text, found = parse_reply(msg.content)
    for _ in range(4):
        if msg.stop_reason == "pause_turn":
            # a long search turn was paused: hand the partial turn back so Claude can finish it
            messages.append({"role": "assistant", "content": msg.content})
        elif not text.strip() and tools:
            # Claude sometimes ends its turn right after searching without writing anything
            messages.append({"role": "assistant", "content": msg.content})
            messages.append({"role": "user", "content": "Write your final answer now, in the format described."})
        else:
            break
        msg = call()
        more, pages = parse_reply(msg.content)
        text, found = text + more, found | pages
    return text, found, config.CLAUDE_MODEL


_gemini_model = {"name": None}


NO_TOOLS = ("\n\nNo tools are available in this request. Do not call any function or search. "
            "Answer directly from the material you are given.")


def _research_brief(system):
    """The search-phase version of a prompt: same task and rules, but a plain-text answer.

    Gemini often breaks (MALFORMED_FUNCTION_CALL) when asked to search and produce strict JSON in one go,
    so the search call asks for prose and a second call does the structuring."""
    head, _, tail = system.partition("Finish with ONLY")
    rules = tail[tail.index("Rules:"):] if "Rules:" in tail else ""
    return (head.strip() + "\n\nUse Google Search, then write what you found as plain text. For each finding, name "
            "the website it came from and keep every specific detail (places, dates, page titles, and any email "
            "address shown on an official page). Do not output JSON.\n" + rules).strip()


def _gemini(system, prompt, web, max_tokens, json_mode=False):
    """Google Gemini over plain HTTPS. Web search is Gemini's built-in Google Search grounding."""
    import httpx
    def payload(use_web):
        text = _research_brief(system) if use_web else system + NO_TOOLS
        cfg = {"maxOutputTokens": max(max_tokens, 8192)}  # leaves room for model "thinking"
        if not use_web and (json_mode or web):  # strict JSON is only possible without the search tool
            cfg["responseMimeType"] = "application/json"
        body = {"systemInstruction": {"parts": [{"text": text}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg}
        return dict(body, tools=[{"google_search": {}}]) if use_web else body

    models = [_gemini_model["name"]] if _gemini_model["name"] else config.GEMINI_MODELS
    last = "no model tried"
    for model in models:
        # search twice (its failures are often one-off), then answer without search rather than fail
        for use_web in ([True, True, False] if web else [False, False]):
            started = time.time()
            try:
                r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                               headers={"x-goog-api-key": config.GEMINI_API_KEY}, json=payload(use_web), timeout=60)
            except httpx.TimeoutException:
                last = f"{model} took longer than 60 seconds"
                log(f"Gemini {model} search={use_web}: timed out")
                continue
            if r.status_code == 200:
                cand = (r.json().get("candidates") or [{}])[0]
                text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
                chunks = (cand.get("groundingMetadata") or {}).get("groundingChunks") or []
                found = {c["web"]["uri"]: c["web"].get("title") or "web page" for c in chunks if c.get("web", {}).get("uri")}
                if not text.strip():
                    last = f"{model} returned an empty answer ({cand.get('finishReason', 'no reason given')})"
                    log(f"Gemini {model} search={use_web}: empty answer, {cand.get('finishReason')}, trying again")
                    continue
                log(f"Gemini {model} search={use_web}: answered in {time.time() - started:.0f}s, {len(found)} web pages")
                _gemini_model["name"] = model
                return text, found, model
            try:
                reason = r.json()["error"]["message"]
            except Exception:
                reason = r.text
            last = f"{model}: HTTP {r.status_code}, {reason[:160]}"
            log(f"Gemini {model} search={use_web}: HTTP {r.status_code} {reason[:120]}")
            if r.status_code == 404:  # this model name is not available to the key: try the next one
                break
            if r.status_code == 429 and use_web:
                time.sleep(4)  # free-tier rate limit: pause briefly before the next attempt
    raise RuntimeError(f"Gemini request failed ({last})")


def log(message):
    """Shows in the terminal where the app is running, so a slow or failing AI call is visible."""
    print(f"[FarmLine AI] {message}", flush=True)


def _complete(system, prompt, web=False, max_tokens=900, json_mode=False):
    """Ask whichever AI is configured. Returns (text, {url: title} of web pages it read, model name)."""
    if config.AI_PROVIDER == "gemini":
        return _gemini(system, prompt, web, max_tokens, json_mode)
    return _claude(system, prompt, web, max_tokens)


def _complete_json(system, prompt, web=False, max_tokens=900):
    """Like _complete, but always ends with a parsed JSON object.

    Models that have just searched the web often answer in prose. When that happens the prose is kept as
    a draft and a second, search-free call turns it into the JSON the app needs."""
    text, found, engine = _complete(system, prompt, web, max_tokens, json_mode=not web)
    try:
        return _json(text), found, engine
    except ValueError:
        if not text.strip():
            raise RuntimeError("the AI returned an empty answer")
    log("answer was not JSON; asking the AI to restructure its draft")
    fix = (f"{prompt}\n\nYOUR DRAFT ANSWER (already researched)\n{text}\n\n"
           "Rewrite the draft as the JSON object described in your instructions. Keep its facts. Output only JSON.")
    text, _, engine = _complete(system, fix, False, max_tokens, json_mode=True)
    try:
        return _json(text), found, engine
    except ValueError:
        raise RuntimeError("the AI answered, but not in a form the app could read")


def understand(text, crop_hint="", started="", area=""):
    """Returns the structured case dict. Never raises: any AI failure falls back to rules."""
    base = _fallback(text, crop_hint, area)
    if not config.AI_PROVIDER:
        return base
    try:
        prompt = (f"Crop the farmer named: {crop_hint or 'not given'}\nStarted: {started or 'not given'}\n"
                  f"How much of the field: {area or 'not given'}\nThe farmer's words: {text}")
        data, _, engine = _complete_json(SYSTEM, prompt)
        base.update({k: v for k, v in data.items() if v not in (None, "", [])})
        base["engine"] = engine
    except Exception as e:  # network, bad key, bad JSON: keep going
        log(f"summary step failed: {str(e)[:200] or type(e).__name__}")
        base["engine"] = f"offline rules (AI error: {str(e)[:120] or type(e).__name__})"
    return base


# ---------- AI over articles, research papers and the live web ----------

SOLUTION_SYSTEM = """You help a rural farmer act on a crop problem. You are given the case, passages from a
library of extension articles and datasets, and abstracts of research papers. You can also search the web.
Search for university extension or government guidance on this exact problem, preferring the farmer's region.
Base every step on the sources. If the sources disagree or do not cover the problem, say so.
Finish with ONLY a JSON object, no other text after it:
{
 "likely_cause": "plain English, or 'unclear'",
 "confidence": "low | medium | high",
 "steps": ["3-6 steps in order, one or two plain sentences each, in the farmer's own language"],
 "avoid": "one sentence on a common mistake, or ''",
 "see_expert_if": "one sentence on when to stop and get an expert",
 "research_summary": "2-3 plain sentences on what the research papers found, or '' if none were given",
 "sources": [{"title": "...", "url": "exact URL of a source you actually used"}]
}
Rules: never give chemical doses, rates or mixing instructions. You may say that a treatment type exists and that
the label and a local extension office set the rate. Only list URLs that appear in the material you were given or
in your search results. Never invent a URL."""

CONTACT_SYSTEM = """Find the official university or institute profile page of a researcher, using web search.
Finish with ONLY a JSON object: {"profile_url": "...", "email": "...", "department": "..."}
Use null for anything you did not find. Report an email only if it is shown on an official institutional page for
this exact person. Never guess or construct an email address."""

WEB_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}


def _ask(system, prompt, web=True, max_tokens=1800):
    """One AI call that ends in JSON. Returns (parsed dict, {url: title} of pages the web search returned)."""
    data, found, engine = _complete_json(system, prompt, web, max_tokens)
    data["_engine"] = engine
    return data, found


def parse_reply(blocks):
    """Claude replies come as blocks: text, plus the pages each web search returned."""
    text, found = "", {}
    for b in blocks:
        kind = getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else None)
        get = (lambda o, k: o.get(k) if isinstance(o, dict) else getattr(o, k, None))
        if kind == "text":
            text += get(b, "text") or ""
        elif kind == "web_search_tool_result":
            results = get(b, "content")
            for r in results if isinstance(results, list) else []:
                if get(r, "url"):
                    found[get(r, "url")] = get(r, "title") or get(r, "url")
    return text, found


def _domain(url):
    from urllib.parse import urlparse
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def suggest_solution(case, passages, papers):
    """Writes a suggested solution from library passages, research abstracts and a live web search."""
    offline = {"mode": "library", "steps": [], "research_summary": "", "library_hits": len(passages),
               "sources": [{"title": p["title"], "url": p["url"], "snippet": p["snippet"], "kind": p["kind"]}
                           for p in passages]}
    if not config.AI_PROVIDER:
        return offline
    library = "\n".join(f"[L{i}] {p['title']} | {p['url']}\n{p['passage']}" for i, p in enumerate(passages, 1))
    research = "\n".join(f"[P{i}] {p['title']} ({p['year']}) | {p['url']}\n{(p.get('abstract') or '')[:900]}"
                         for i, p in enumerate(papers[:5], 1))
    prompt = (f"CASE\nPlace: {case['place']}\nCrop: {case['crop']}\nStarted: {case['started']}\n"
              f"Field affected: {case['area_affected']}\nSummary: {case['summary']}\n"
              f"The farmer's words: {case['description']}\n\n"
              f"LIBRARY PASSAGES\n{library or 'none'}\n\nRESEARCH PAPERS\n{research or 'none'}")
    try:
        data, found = _ask(SOLUTION_SYSTEM, prompt)
    except Exception as e:
        return dict(offline, error=f"The AI step failed: {str(e)[:160] or type(e).__name__}. Showing library articles instead.")
    given = {p["url"]: (p["title"], "library") for p in passages} | {p["url"]: (p["title"], "research") for p in papers}
    sources, seen = [], set()
    for s in data.get("sources") or []:
        url = (s or {}).get("url")
        if url in seen:
            continue
        if url in given:  # a library article or paper we handed over
            sources.append({"title": s.get("title") or given[url][0], "url": url, "kind": given[url][1]})
            seen.add(url)
        elif url in found:  # a page the AI's own web search returned (Claude gives real addresses)
            sources.append({"title": s.get("title") or found[url], "url": url, "kind": "web"})
            seen.add(url)
        # anything else is a link the model could not have read, so it is dropped
    if config.AI_PROVIDER == "gemini":  # Gemini reports its web pages separately, as Google redirect links
        for url, title in list(found.items())[:5]:
            if title not in {x["title"] for x in sources}:
                sources.append({"title": title, "url": url, "kind": "web"})
                seen.add(url)
    return {"mode": "ai", "engine": data.get("_engine", ""), "likely_cause": data.get("likely_cause", ""),
            "confidence": data.get("confidence", "low"), "steps": [str(s) for s in data.get("steps") or []][:6],
            "avoid": data.get("avoid", ""), "see_expert_if": data.get("see_expert_if", ""),
            "research_summary": data.get("research_summary", ""), "sources": sources,
            "library_hits": len(passages), "web_pages": len(found),
            "new_urls": [u for u in found if u in seen]}


def find_contact(name, institution, topic):
    """Looks up a researcher's public university profile. Returns {} when no AI key is set."""
    if not config.AI_PROVIDER:
        return {}
    try:
        data, found = _ask(CONTACT_SYSTEM, f"Researcher: {name}\nInstitution: {institution}\nWorks on: {topic}",
                           max_tokens=500)
    except Exception:
        return {}
    email = data.get("email") or ""
    if not re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", email):
        email = ""
    profile = data.get("profile_url") or ""
    # keep the page only if the web search really returned it (Claude: same address; Gemini: same site)
    sites = {_domain(u) for u in found} | {(t or "").lower().removeprefix("www.") for t in found.values()}
    if profile not in found and not any(d and _domain(profile).endswith(d) for d in sites):
        profile = ""
    return {"email": email, "profile_url": profile, "department": data.get("department") or ""}


# ---------- real cases reported on the web (forums, Reddit, extension field reports, news) ----------

CASES_SYSTEM = """You find real, documented cases of a crop problem using web search.
Search where growers describe what happened to them: Reddit (for example r/farming, r/agriculture, r/homestead,
r/gardening), AgTalk and other grower forums, university extension field reports and pest alerts, and farm news.
Run several different searches. Include the word reddit in one search and the word forum in another.
Prefer cases from the farmer's region and from recent years, but include others if they match the problem closely.
Finish with ONLY a JSON object, no other text after it:
{
 "cases": [{
   "who": "describe the grower without any username or personal name, e.g. 'A sorghum grower'",
   "where": "place if stated, else ''",
   "when": "year or season if stated, else ''",
   "what_happened": "1-2 plain sentences",
   "what_they_did": "1-2 plain sentences",
   "outcome": "what resulted, or 'not reported'",
   "source_site": "domain only, e.g. reddit.com",
   "source_title": "title of the thread or page",
   "url": "exact address from your search results, or ''"
 }],
 "pattern": "2 sentences on what these cases have in common, or ''"
}
Rules: report only cases you actually found in search results. Never invent a case, place, date or outcome.
Up to 5 cases. If you found none, return an empty list. Paraphrase in your own words. Never include usernames.
Never give chemical doses or mixing rates."""


def find_real_cases(case):
    """Uses the AI's web search to find real reported cases of the same problem, each with its source."""
    if not config.AI_PROVIDER:
        return {"available": False, "reason": "Add an AI key to search the web for real cases.", "cases": []}
    prompt = (f"Crop: {case['crop']}\nLikely problem: {case['likely_issue']}\nSearch terms: {case['research_terms']}\n"
              f"Farmer's region: {case['place']}\nSummary: {case['summary']}\nThe farmer's words: {case['description']}")
    try:
        data, found = _ask(CASES_SYSTEM, prompt, max_tokens=2000)
    except Exception as e:
        return {"available": False, "reason": f"The web search for real cases failed: {str(e)[:160]}", "cases": []}
    if not found:  # no search happened, so anything returned would be from the model's memory, not a real source
        return {"available": False, "cases": [],
                "reason": "The AI could not search the web on this key, so no real cases can be shown."}
    cases = []
    for c in _keep_sourced((data.get("cases") or [])[:5], found):  # a case with no page behind it is dropped
        if c.get("what_happened"):
            cases.append({k: str(c.get(k) or "") for k in ("who", "where", "when", "what_happened", "what_they_did",
                                                           "outcome", "source_site", "source_title", "url")})
    return {"available": True, "cases": cases, "pattern": str(data.get("pattern") or "") if cases else "",
            "pages_read": len(found), "engine": data.get("_engine", "")}


# ---------- real people and offices a farmer can contact (extension agents, specialists, labs) ----------

EXPERTS_SYSTEM = """You find real people and offices a farmer can contact about a crop problem, using web search.
Look for: the county or district extension office or agent for the farmer's area; university extension specialists
for this crop and this kind of problem (entomologist, plant pathologist, agronomist) in the farmer's state or
region; and the nearest plant disease and insect diagnostic lab. Run several searches.
Finish with ONLY a JSON object, no other text after it:
{
 "experts": [{
   "name": "the person's name, or the office name if no person is listed",
   "role": "their job title",
   "organization": "university, extension service or agency",
   "why": "one plain sentence on why they fit this problem and this farmer",
   "phone": "only if shown on the official page, else ''",
   "email": "only if shown on the official page, else ''",
   "source_site": "domain only, e.g. agrilifeextension.tamu.edu",
   "source_title": "title of the page you found them on",
   "url": "exact address from your search results, or ''"
 }]
}
Rules: list only people and offices you found in search results on official university, extension or government
pages. Never invent a name, title, phone number or email, and never guess an email from a pattern. Never include
private individuals, social media accounts or home addresses. Up to 5, the closest and most relevant first."""


def _keep_sourced(items, found):
    """Keep only entries that point at a page the web search really returned, and attach that page's link."""
    by_site = {}
    for url, title in found.items():
        by_site.setdefault((title or "").lower().removeprefix("www."), url)
        by_site.setdefault(_domain(url), url)
    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue
        site = (item.get("source_site") or "").lower().removeprefix("www.")
        url = item.get("url") or ""
        if url not in found:
            url = by_site.get(site) or by_site.get(_domain(url)) or ""
        if url:
            kept.append(dict(item, url=url))
    return kept


def find_experts(case):
    """Uses the AI's web search to find real extension staff, specialists and labs near the farmer."""
    if not config.AI_PROVIDER:
        return {"available": False, "reason": "Add an AI key to search the web for experts.", "experts": []}
    prompt = (f"Farmer's location: {case['place']}\nCrop: {case['crop']}\nLikely problem: {case['likely_issue']}\n"
              f"Type of problem: {case['category']}\nSummary: {case['summary']}")
    try:
        data, found = _ask(EXPERTS_SYSTEM, prompt, max_tokens=2000)
    except Exception as e:
        return {"available": False, "reason": f"The web search for experts failed: {str(e)[:160]}", "experts": []}
    if not found:
        return {"available": False, "experts": [],
                "reason": "The AI could not search the web on this key, so no experts can be shown."}
    experts = []
    for x in _keep_sourced((data.get("experts") or [])[:5], found):
        if not x.get("name"):
            continue
        email = x.get("email") or ""
        x = {k: str(x.get(k) or "") for k in ("name", "role", "organization", "why", "phone", "source_site",
                                               "source_title", "url")}
        x["email"] = email if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", email) else ""
        experts.append(x)
    return {"available": True, "experts": experts, "pages_read": len(found), "engine": data.get("_engine", "")}
