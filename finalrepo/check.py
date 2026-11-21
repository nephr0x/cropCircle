"""Checks each outside service the app uses and says plainly what works.   Run:  python3 check.py"""
import time

import httpx

import brain, config, db, library, matching


def step(name, fn):
    started = time.time()
    try:
        print(f"OK    {name}: {fn()}  ({time.time() - started:.0f}s)")
    except Exception as e:
        print(f"FAIL  {name}: {str(e)[:300] or type(e).__name__}  ({time.time() - started:.0f}s)")


def plain():
    text, _, model = brain._complete("Answer in five words or fewer.", "Say hello to a farmer.")
    return f"{model} answered: {text.strip()[:60]}"


def with_search():
    text, found, model = brain._complete("Use web search, then answer in one sentence.",
                                         "What is the sugarcane aphid and which crop does it attack?", web=True)
    if not found:
        raise RuntimeError("the model answered, but without searching the web. Real cases and web sources "
                           "will be missing. On Gemini this usually means search is not included on this key.")
    return f"{model} searched the web and read {len(found)} pages"


def openalex():
    r = matching.find_papers({"research_terms": "sorghum sugarcane aphid", "crop": "sorghum", "category": "pest"})
    if not r["available"]:
        raise RuntimeError(r["reason"])
    return f"{len(r['papers'])} papers, first: {r['papers'][0]['title'][:60]}"


def web_page():
    r = httpx.get("https://extension.okstate.edu/", timeout=15, follow_redirects=True,
                  headers={"User-Agent": library.AGENT})
    return f"HTTP {r.status_code}"


def real_cases():
    r = brain.find_real_cases({"crop": "sorghum", "likely_issue": "sugarcane aphid", "place": "Lubbock, TX",
                               "research_terms": "sorghum sugarcane aphid",
                               "summary": "Sticky leaves and small yellow insects on sorghum, spreading fast.",
                               "description": "The leaves are sticky and there are tiny yellow bugs under them."})
    if not r["available"]:
        raise RuntimeError(r["reason"])
    first = f", first from {r['cases'][0]['source_site']}" if r["cases"] else ""
    return f"{len(r['cases'])} real cases found{first}"


def experts():
    r = brain.find_experts({"crop": "sorghum", "likely_issue": "sugarcane aphid", "place": "Lubbock, TX",
                            "category": "pest", "summary": "Sticky leaves and small yellow insects on sorghum."})
    if not r["available"]:
        raise RuntimeError(r["reason"])
    first = f", first: {r['experts'][0]['name']}" if r["experts"] else ""
    return f"{len(r['experts'])} experts found{first}"


db.init()
print(f"AI provider: {config.AI_PROVIDER or 'none (no AI key found in .env)'}")
print(f"Library: {library.stats()['documents']} articles and dataset entries saved\n")
if config.AI_PROVIDER:
    step("AI, plain question", plain)
    step("AI with web search", with_search)
    step("Real cases search (the full step the app runs)", real_cases)
    step("Experts search (the full step the app runs)", experts)
step("Research papers (OpenAlex)", openalex)
step("Reading a web page", web_page)
