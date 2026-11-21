"""Matching. Three sources:
   1. experiences   - what other farmers on FarmLine say fixed the same problem
   2. researchers   - researchers who signed up on FarmLine and listed this crop or problem
   3. OpenAlex      - the wider research network: who at nearby universities publishes on it
"""
import json, re, time
from collections import defaultdict
from pathlib import Path

import httpx

import config, db
from geo import km

CACHE = Path(__file__).parent / "openalex_cache.json"
API = "https://api.openalex.org"
STOP = set("the a an and or of in on my is are with to it they have has there some all at was were "
           "this that from for not but our their them its into after before".split())
FIELD = {"disease": ["patholog", "disease", "fung"], "pest": ["entomolog", "pest", "insect"],
         "soil": ["soil", "nutrient", "agronom"], "water": ["water", "irrigat", "soil"]}


def tokens(*parts):
    words = re.findall(r"[a-z]+", " ".join(p or "" for p in parts).lower())
    return {w[:6] for w in words if w not in STOP and len(w) > 2}  # crude stemming


def _case_tokens(case):
    return tokens(case["description"], " ".join(case["symptoms"]), case["likely_issue"], case["research_terms"])


# ---------- 1. Farmers who solved it ----------

def find_experiences(case, limit=3):
    mine, out = _case_tokens(case), []
    for e in db.query("SELECT e.*, a.name AS farmer, a.demo FROM experiences e JOIN accounts a ON a.id=e.account_id "
                      "WHERE e.account_id != ?", (case["account_id"],)):
        theirs = tokens(e["title"], e["description"])
        overlap = len(mine & theirs) / max(1, len(mine | theirs))
        same_crop = e["crop"] == case["crop"] and case["crop"] != "unknown"
        dist = km(case["lat"], case["lon"], e["lat"], e["lon"])
        score = (4 * same_crop + 1.5 * (e["category"] == case["category"]) + 10 * overlap
                 + 1.5 * max(0, 1 - (dist or 999) / 400))
        if score >= 5 and (same_crop or overlap >= 0.2):
            out.append({"experience_id": e["id"], "farmer": e["farmer"], "title": e["title"],
                        "crop": e["crop"], "place": e["place"], "km": dist and round(dist),
                        "description": e["description"], "what_worked": e["what_worked"],
                        "days_to_fix": e["days_to_fix"], "allow_contact": bool(e["allow_contact"]), "demo": bool(e["demo"]),
                        "score": round(score, 1)})
    return sorted(out, key=lambda p: -p["score"])[:limit]


# ---------- 2. Researchers on FarmLine ----------

def score_researcher(r, case, trials):
    """How well one researcher fits one case. Used for both directions of the match."""
    mine = _case_tokens(case) | tokens(case["crop"])
    crops = tokens(r["crops"])
    theirs = tokens(r["expertise"]) | crops | tokens(*(f"{t['title']} {t['problem']}" for t in trials))
    dept = (r["department"] or "").lower() + " " + (r["expertise"] or "").lower()
    crop_hit = bool(tokens(case["crop"]) & crops)
    field_hit = any(w in dept for w in FIELD.get(case["category"], []))
    dist = km(case["lat"], case["lon"], r["lat"], r["lon"])
    score = 4 * crop_hit + 2 * field_hit + 1.2 * len(mine & theirs) + 2 * max(0, 1 - (dist or 999) / 500)
    return round(score, 1), dist


def find_researchers(case, limit=3):
    out = []
    for r in db.query("SELECT * FROM accounts WHERE role='researcher'"):
        trials = db.query("SELECT * FROM trials WHERE account_id=?", (r["id"],))
        score, dist = score_researcher(r, case, trials)
        if score >= 5:
            fitting = [t for t in trials if tokens(t["crop"]) & tokens(case["crop"])]
            out.append({"account_id": r["id"], "name": r["name"], "institution": r["institution"],
                        "department": r["department"], "expertise": r["expertise"], "demo": bool(r["demo"]),
                        "km": dist and round(dist), "trials": fitting, "score": score})
    return sorted(out, key=lambda r: -r["score"])[:limit]


# ---------- 3. OpenAlex: the wider research network ----------

def _cache():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {"institutions": {}, "searches": {}}


def _get(path, **params):
    if config.OPENALEX_API_KEY:  # works without a key too, on a small daily allowance
        params["api_key"] = config.OPENALEX_API_KEY
    r = httpx.get(f"{API}/{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def _short(openalex_url):
    return openalex_url.rsplit("/", 1)[-1]


def _institutions(cache):
    """Resolve the university names in config.py to OpenAlex IDs + coordinates (once, then cached)."""
    for name in config.UNIVERSITIES:
        if name not in cache["institutions"]:
            hits = _get("institutions", search=name, per_page=1)["results"]
            if hits:
                h, geo = hits[0], hits[0].get("geo") or {}
                cache["institutions"][name] = {"id": _short(h["id"]), "name": h["display_name"],
                                               "lat": geo.get("latitude"), "lon": geo.get("longitude")}
    return {v["id"]: v for v in cache["institutions"].values()}


def find_in_openalex(case, limit=4):
    """People at nearby universities, ranked by how much they publish on this exact problem."""
    if not config.OPENALEX_API_KEY:
        return {"available": False, "reason": "Add OPENALEX_API_KEY to .env to search the research network.", "people": []}
    terms = case["research_terms"] or f"{case['crop']} {case['category']}"
    try:
        cache = _cache()
        inst = _institutions(cache)
        if terms not in cache["searches"]:
            cache["searches"][terms] = _get(
                "works", search=terms, per_page=100,
                filter=f"authorships.institutions.id:{'|'.join(inst)},publication_year:>2015",
                select="id,title,publication_year,authorships")["results"]
        CACHE.write_text(json.dumps(cache))
        people = defaultdict(lambda: {"works": [], "inst": None})
        for w in cache["searches"][terms]:
            for a in w.get("authorships", []):
                home = next((inst[_short(i["id"])] for i in a.get("institutions", [])
                             if i.get("id") and _short(i["id"]) in inst), None)
                if home and (a.get("author") or {}).get("id"):
                    p = people[a["author"]["id"]]
                    p["name"], p["inst"] = a["author"]["display_name"], home
                    p["works"].append(w)
        this_year, ranked = time.gmtime().tm_year, []
        for author_id, p in people.items():
            dist = km(case["lat"], case["lon"], p["inst"]["lat"], p["inst"]["lon"])
            recent = sum(1 for w in p["works"] if (w.get("publication_year") or 0) >= this_year - 4)
            newest = max(p["works"], key=lambda w: w.get("publication_year") or 0)
            ranked.append({"name": p["name"], "institution": p["inst"]["name"], "km": dist and round(dist),
                           "papers_on_topic": len(p["works"]), "recent_papers": recent,
                           "example_paper": newest["title"], "profile_url": author_id,
                           "score": len(p["works"]) + recent + 3 * max(0, 1 - (dist or 999) / 600)})
        ranked.sort(key=lambda r: -r["score"])
        return {"available": True, "terms": terms, "people": ranked[:limit]}
    except Exception as e:
        return {"available": False, "reason": f"OpenAlex search failed ({type(e).__name__}).", "people": []}


def _abstract(inverted):
    """OpenAlex stores abstracts as {word: [positions]}. Put the words back in order."""
    if not inverted:
        return ""
    words = sorted((pos, w) for w, positions in inverted.items() for pos in positions)
    return " ".join(w for _, w in words)


def find_papers(case, limit=6):
    """Research papers on this exact problem, worldwide, plus the people who wrote them."""
    terms = case["research_terms"] or f"{case['crop']} {case['category']}"
    try:
        cache = _cache()
        key = "papers:" + terms
        if key not in cache["searches"]:
            cache["searches"][key] = _get("works", search=terms, per_page=25,
                                          filter="publication_year:>2012,type:article|review")["results"]
            CACHE.write_text(json.dumps(cache))
        works = cache["searches"][key]
    except Exception as e:
        return {"available": False, "reason": f"Research search failed ({type(e).__name__}).", "terms": terms,
                "papers": [], "people": []}
    papers, people = [], {}
    local = [u.lower() for u in config.UNIVERSITIES]
    for w in works:
        if not w.get("title"):
            continue
        url = w.get("doi") or w["id"]
        authors = []
        for a in w.get("authorships") or []:
            au = a.get("author") or {}
            if not au.get("id"):
                continue
            inst = (a.get("institutions") or [{}])[0]
            authors.append(au.get("display_name"))
            p = people.setdefault(au["id"], {"name": au.get("display_name"), "openalex_id": au["id"],
                                             "orcid": au.get("orcid"), "institution": "", "country": "",
                                             "papers": 0, "lead": 0, "paper_title": w["title"], "paper_url": url})
            p["papers"] += 1
            p["lead"] += bool(a.get("is_corresponding")) or a.get("author_position") == "first"
            if inst.get("display_name") and not p["institution"]:
                p["institution"], p["country"] = inst["display_name"], inst.get("country_code") or ""
        if len(papers) < limit:
            source = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
            papers.append({"title": w["title"], "year": w.get("publication_year"), "journal": source, "url": url,
                           "authors": authors[:4], "more_authors": max(0, len(authors) - 4),
                           "cited_by": w.get("cited_by_count") or 0,
                           "abstract": _abstract(w.get("abstract_inverted_index"))})
    ranked = sorted((p for p in people.values() if p["institution"]),
                    key=lambda p: -(2 * p["papers"] + p["lead"] + 3 * (p["institution"].lower() in local)))
    return {"available": True, "terms": terms, "papers": papers, "people": ranked[:6]}


def openalex_authors(name, institution=""):
    """Used on the researcher profile: look yourself up and import your research topics."""
    if not config.OPENALEX_API_KEY:
        return {"available": False, "reason": "Add OPENALEX_API_KEY to .env to use this.", "people": []}
    try:
        hits = _get("authors", search=name, per_page=8)["results"]
        people = []
        for h in hits:
            inst = ", ".join(i.get("display_name", "") for i in h.get("last_known_institutions") or [])
            people.append({"openalex_id": h["id"], "name": h["display_name"], "institution": inst,
                           "works_count": h.get("works_count"),
                           "topics": [t.get("display_name") for t in (h.get("topics") or [])[:8]]})
        want = institution.lower()
        people.sort(key=lambda p: (want not in p["institution"].lower() if want else False, -(p["works_count"] or 0)))
        return {"available": True, "people": people[:5]}
    except Exception as e:
        return {"available": False, "reason": f"OpenAlex search failed ({type(e).__name__}).", "people": []}
