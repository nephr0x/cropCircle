"""FarmLine. Farmers find help and share what worked. Researchers answer cases and offer trials.
Answers draw on a local article library, research papers (OpenAlex) and a live AI web search.

Run:   uvicorn app:app --reload        then open http://localhost:8000
Phone: point a Twilio number's voice webhook at  https://<your-public-url>/voice
"""
import hashlib, json, secrets, time
from html import escape
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse

import brain, config, db, demo, geo, library, matching, notify

app = FastAPI(title="FarmLine")
ROOT = Path(__file__).parent
COOKIE = "farmline_session"
PROFILE_FIELDS = {
    "farmer": ["name", "phone", "email", "contact_pref", "address", "city", "county", "state", "zip",
               "crops", "farm_acres"],
    "researcher": ["name", "email", "phone", "contact_pref", "institution", "department", "expertise",
                   "crops", "city", "state", "zip", "openalex_id"],
}
PLACE_FIELDS = ["address", "city", "county", "state", "zip"]


# ---------- accounts ----------

def clean_phone(p):
    return "".join(ch for ch in (p or "") if ch.isdigit())[-10:]


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def place_name(a):
    return ", ".join(p for p in (a.get("city"), a.get("state")) if p) or a.get("zip") or "Location not set"


def save_profile(account_id, role, data, new=False):
    fields = {k: (data[k] or "").strip() if isinstance(data[k], str) else data[k]
              for k in PROFILE_FIELDS[role] if k in data}
    if "phone" in fields:
        fields["phone"] = clean_phone(fields["phone"])
    if "email" in fields:
        fields["email"] = fields["email"].lower()
    old = {} if new else db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    if data.get("lat") is not None and data.get("lon") is not None:  # "Use my current location"
        fields["lat"], fields["lon"] = float(data["lat"]), float(data["lon"])
    elif new or old.get("lat") is None or any(fields.get(k, old.get(k)) != old.get(k) for k in PLACE_FIELDS):
        merged = {**old, **fields}
        fields["lat"], fields["lon"], _ = geo.locate(merged.get("address"), merged.get("city"),
                                                     merged.get("county"), merged.get("state"), merged.get("zip"))
    db.update("accounts", account_id, fields)
    return db.one("SELECT * FROM accounts WHERE id=?", (account_id,))


def start_session(account_id):
    """Each browser tab keeps its own token, so a farmer tab and a researcher tab can be open side by side."""
    token = secrets.token_urlsafe(32)
    db.insert("sessions", {"token": token, "account_id": account_id})
    return token


def token_of(request):
    return request.headers.get("authorization", "").removeprefix("Bearer ").strip()


def current(request: Request):
    acct = db.one("SELECT a.* FROM accounts a JOIN sessions s ON s.account_id=a.id WHERE s.token=?",
                  (token_of(request),))
    if not acct:
        raise HTTPException(401, "Sign in first.")
    return acct


def farmer(acct=Depends(current)):
    if acct["role"] != "farmer":
        raise HTTPException(403, "This page is for farmers.")
    return acct


def researcher(acct=Depends(current)):
    if acct["role"] != "researcher":
        raise HTTPException(403, "This page is for researchers.")
    return acct


async def body(request: Request):
    return await request.json()


@app.post("/api/signup")
def signup(response: Response, data=Depends(body)):
    role = data.get("role")
    if role not in PROFILE_FIELDS:
        raise HTTPException(400, "Choose farmer or researcher.")
    if not (data.get("name") or "").strip() or len(data.get("password") or "") < 4:
        raise HTTPException(400, "Enter your name and a password of at least 4 characters.")
    if role == "farmer":
        phone = clean_phone(data.get("phone"))
        if len(phone) < 10:
            raise HTTPException(400, "Enter a 10-digit phone number.")
        existing = db.raw_one("SELECT * FROM accounts WHERE role='farmer' AND phone=?", (phone,))
        if existing and existing["pass_hash"]:
            raise HTTPException(400, "That phone number already has an account. Sign in instead.")
    else:
        email = (data.get("email") or "").strip().lower()
        if "@" not in email or not (data.get("institution") or "").strip():
            raise HTTPException(400, "Enter your email and your university.")
        existing = None
        if db.one("SELECT id FROM accounts WHERE role='researcher' AND email=?", (email,)):
            raise HTTPException(400, "That email already has an account. Sign in instead.")
        data.setdefault("contact_pref", "email")
    salt = secrets.token_hex(8)
    creds = {"pass_hash": hash_password(data["password"], salt), "salt": salt}
    if existing:  # someone who called the phone line before now claims their account
        account_id = existing["id"]
        db.update("accounts", account_id, creds)
    else:
        account_id = db.insert("accounts", {"role": role, **creds})
    acct = save_profile(account_id, role, data, new=True)
    if role == "researcher":  # cases a farmer emailed them about are waiting in their inbox
        for inv in db.query("SELECT * FROM invites WHERE email != '' AND email=?", (acct["email"],)):
            db.insert("requests", {"case_id": inv["case_id"], "kind": "researcher", "target_account_id": account_id})
    return dict(acct, token=start_session(account_id))


@app.post("/api/login")
def login(response: Response, data=Depends(body)):
    role, ident = data.get("role"), (data.get("identifier") or "").strip()
    if role == "farmer":
        row = db.raw_one("SELECT * FROM accounts WHERE role='farmer' AND phone=?", (clean_phone(ident),))
    else:
        row = db.raw_one("SELECT * FROM accounts WHERE role='researcher' AND email=?", (ident.lower(),))
    if not row or not row["pass_hash"] or not secrets.compare_digest(
            row["pass_hash"], hash_password(data.get("password") or "", row["salt"])):
        raise HTTPException(401, "Those details do not match an account.")
    return dict(db.one("SELECT * FROM accounts WHERE id=?", (row["id"],)), token=start_session(row["id"]))


@app.post("/api/logout")
def logout(request: Request, response: Response):
    db.execute("DELETE FROM sessions WHERE token=?", (token_of(request),))
    return {"ok": True}


@app.get("/api/me")
def me(acct=Depends(current)):
    return acct


@app.put("/api/me")
def update_me(data=Depends(body), acct=Depends(current)):
    return save_profile(acct["id"], acct["role"], data)


@app.get("/api/config")
def get_config():
    return {"helpline": config.HELPLINE_NUMBER, "ai": bool(config.AI_PROVIDER),
            "openalex": bool(config.OPENALEX_API_KEY), "sms": bool(config.TWILIO_ACCOUNT_SID),
            "email": bool(config.SMTP_HOST), "library": library.stats(),
            "demo_form": demo.DATA["form"] if config.DEMO_CASE else None}


@app.get("/api/messages")
def messages(acct=Depends(current)):
    return {"received": db.query("SELECT * FROM outbox WHERE account_id=? ORDER BY created DESC LIMIT 50", (acct["id"],)),
            "sent": db.query("SELECT * FROM outbox WHERE sender_id=? ORDER BY created DESC LIMIT 50", (acct["id"],))}


# ---------- farmer: find help ----------

def open_case(acct, description, crop="", started="", area="", trial_ok=False, source="app"):
    u = brain.understand(description, crop, started, area)
    if u["engine"].startswith("offline") and demo.matches(f"{crop} {u['crop']}", description):
        # demo safety net: the live summary step did not deliver, so use the saved one and say so
        u.update(demo.saved("understanding", u["engine"]), engine="saved demo answer")
    case = {"account_id": acct["id"], "source": source, "crop": u["crop"], "category": u["category"],
            "symptoms": u["symptoms"], "severity": u["severity"], "likely_issue": u["likely_issue"],
            "summary": u["summary"], "first_steps": u["first_steps"], "research_terms": u["research_terms"],
            "description": description, "started": started, "area_affected": area, "trial_ok": int(trial_ok),
            "place": place_name(acct), "lat": acct["lat"], "lon": acct["lon"]}
    case["id"] = db.insert("cases", case)
    return case, u


def summary_text(case, experiences, researchers):
    lines = [case["summary"]]
    if case["likely_issue"] and case["likely_issue"] != "unclear":
        lines.append(f"Likely cause: {case['likely_issue']}.")
    solution = case.get("solution") or {}
    if solution.get("steps"):
        lines.append("Suggested solution:")
        lines += [f"{i}. {s}" for i, s in enumerate(solution["steps"], 1)]
        if solution.get("sources"):
            lines.append("Sources: " + ", ".join(src["url"] for src in solution["sources"][:3]))
    else:
        lines.append("What to do first:")
        lines += [f"{i}. {s}" for i, s in enumerate(case["first_steps"], 1)]
    if experiences:
        e = experiences[0]
        lines.append(f"A farmer near {e['place']} fixed this: {e['what_worked']}")
    if researchers:
        lines.append("Researchers who study this: " + "; ".join(f"{r['name']} ({r['institution']})" for r in researchers))
    return "\n".join(lines)


def own_case(case_id, acct):
    case = db.one("SELECT * FROM cases WHERE id=? AND account_id=?", (case_id, acct["id"]))
    if not case:
        raise HTTPException(404, "Request not found.")
    return case


@app.post("/api/help")
def find_help(data=Depends(body), acct=Depends(farmer)):
    description = (data.get("description") or "").strip()
    if len(description) < 10:
        raise HTTPException(400, "Describe the problem in a sentence or two.")
    if data.get("details"):
        acct = save_profile(acct["id"], "farmer", data["details"])
    case, u = open_case(acct, description, data.get("crop", ""), data.get("started", ""),
                        data.get("area_affected", ""), data.get("trial_ok", False))
    return {"case": case, "experiences": matching.find_experiences(case),
            "researchers": matching.find_researchers(case), "engine": u["engine"], "account": acct}


def research_for(case):
    """Papers on the problem and the people behind them. Looked up once per case, then kept."""
    if case.get("research"):
        return case["research"]

    def live():
        found = matching.find_papers(case)
        found["nearby"] = matching.find_in_openalex(case)["people"]
        if found["available"] and found["papers"]:
            db.update("cases", case["id"], {"research": found})
        return found

    return demo.live_or_saved(case, "research", live, lambda r: r["available"] and r["papers"],
                              lambda saved: dict(saved, available=True, nearby=[]))


def compute_solution(case):
    if case.get("solution"):
        return case["solution"], []

    def live():
        query = " ".join([case["crop"], case["likely_issue"], " ".join(case["symptoms"]), case["description"]])
        passages = library.search(query, k=5)
        result = brain.suggest_solution(case, passages, research_for(case)["papers"])
        if result["mode"] == "ai" and result["steps"]:
            db.update("cases", case["id"], {"solution": {k: v for k, v in result.items() if k != "new_urls"}})
        return result

    result = demo.live_or_saved(case, "solution", live, lambda r: r["mode"] == "ai" and r["steps"],
                                lambda saved: dict(saved, mode="ai", library_hits=0, web_pages=0))
    return result, result.pop("new_urls", [])


@app.get("/api/cases/{case_id}/research")
def research(case_id: int, acct=Depends(farmer)):
    return research_for(own_case(case_id, acct))


@app.post("/api/cases/{case_id}/solution")
def solution(case_id: int, background: BackgroundTasks, acct=Depends(farmer)):
    """The AI step: library passages + research abstracts + live web search -> a suggested solution."""
    result, new_urls = compute_solution(own_case(case_id, acct))
    for url in new_urls:  # pages the web search found join the library for next time
        background.add_task(library.add_url, url, None, "web")
    return result


@app.post("/api/cases/{case_id}/real-cases")
def real_cases(case_id: int, acct=Depends(farmer)):
    """Real reported cases of the same problem, found by the AI's web search (forums, Reddit, field reports)."""
    case = own_case(case_id, acct)
    if case.get("real_cases"):
        return case["real_cases"]

    def live():
        result = brain.find_real_cases(case)
        if result["available"] and result["cases"]:
            db.update("cases", case_id, {"real_cases": result})
        return result

    return demo.live_or_saved(case, "real_cases", live, lambda r: r["available"] and r["cases"],
                              lambda saved: dict(saved, available=True, pages_read=0))


@app.post("/api/cases/{case_id}/experts")
def experts(case_id: int, acct=Depends(farmer)):
    """Real extension staff, specialists and labs near the farmer, found by the AI's web search."""
    case = own_case(case_id, acct)
    if case.get("experts"):
        return case["experts"]

    def live():
        result = brain.find_experts(case)
        if result["available"] and result["experts"]:
            db.update("cases", case_id, {"experts": result})
        return result

    return demo.live_or_saved(case, "experts", live, lambda r: r["available"] and r["experts"],
                              lambda saved: dict(saved, available=True, pages_read=0))


@app.post("/api/cases/{case_id}/contact")
def contact_researcher(case_id: int, data=Depends(body), acct=Depends(farmer)):
    """For a researcher who is not on FarmLine: find their public university page and draft an email."""
    case = own_case(case_id, acct)
    name, inst = (data.get("name") or "").strip(), (data.get("institution") or "").strip()
    if not name:
        raise HTTPException(400, "No researcher chosen.")
    if data.get("known"):  # an expert the web search already found, with their page and any listed email
        email = data.get("email") or ""
        info = {"email": email if "@" in email else "", "profile_url": data.get("profile_url") or ""}
    else:
        info = brain.find_contact(name, inst, case["research_terms"])
    reach = " or ".join(x for x in (acct["phone"], acct["email"]) if x)
    paper = f' I found your paper "{data["paper_title"]}".' if data.get("paper_title") else ""
    subject = f"A farmer near {case['place']} needs help with {case['crop']}"
    message = (f"Dear {name},\n\nI farm near {case['place']} and I am writing through FarmLine, a service that "
               f"connects farmers with researchers who study their problem.{paper}\n\n"
               f"My problem: {case['summary']}\nIn my words: {case['description']}\n"
               f"Started: {case['started'] or 'not sure'}. Field affected: {case['area_affected'] or 'not sure'}.\n\n"
               f"Could you tell me what this is likely to be, or who near me could look at it?"
               f"{' I am willing to try a solution on part of my field.' if case['trial_ok'] else ''}\n\n"
               f"You can reach me at {reach}.\n\nThank you,\n{acct['name']}")
    db.insert("invites", {"case_id": case_id, "name": name, "institution": inst, "email": info.get("email", ""),
                          "openalex_id": data.get("openalex_id")})
    db.insert("outbox", {"sender_id": acct["id"], "to_name": name, "channel": "email draft",
                         "to_addr": info.get("email", ""), "subject": subject, "body": message, "sent": 0})
    return {"email": info.get("email", ""), "profile_url": info.get("profile_url", ""),
            "department": info.get("department", ""), "looked_up": bool(config.AI_PROVIDER) or bool(data.get("known")),
            "subject": subject, "message": message}


@app.post("/api/cases/{case_id}/send")
def send_summary(case_id: int, data=Depends(body), acct=Depends(farmer)):
    case = own_case(case_id, acct)
    text = summary_text(case, matching.find_experiences(case), matching.find_researchers(case))
    return notify.send(acct, f"FarmLine: your {case['crop']} problem", text, data.get("channel"), sender=acct)


@app.post("/api/cases/{case_id}/ask-farmer/{experience_id}")
def ask_farmer(case_id: int, experience_id: int, acct=Depends(farmer)):
    case = own_case(case_id, acct)
    exp = db.one("SELECT * FROM experiences WHERE id=? AND allow_contact=1", (experience_id,))
    if not exp:
        raise HTTPException(404, "That farmer is not taking contact requests.")
    db.insert("requests", {"case_id": case_id, "kind": "farmer", "target_account_id": exp["account_id"],
                           "experience_id": experience_id})
    them = db.one("SELECT * FROM accounts WHERE id=?", (exp["account_id"],))
    reach = " or ".join(x for x in (acct["phone"], acct["email"]) if x)
    notify.send(them, "FarmLine: a farmer needs your experience",
                f"{acct['name']} near {case['place']} has the problem you shared: \"{exp['title']}\". "
                f"They asked to talk with you. Reach them at {reach}.", sender=acct)
    return {"ok": True}


@app.post("/api/cases/{case_id}/ask-researcher/{researcher_id}")
def ask_researcher(case_id: int, researcher_id: int, acct=Depends(farmer)):
    case = own_case(case_id, acct)
    them = db.one("SELECT * FROM accounts WHERE id=? AND role='researcher'", (researcher_id,))
    if not them:
        raise HTTPException(404, "Researcher not found.")
    if not db.one("SELECT id FROM requests WHERE case_id=? AND kind='researcher' AND target_account_id=?",
                  (case_id, researcher_id)):
        db.insert("requests", {"case_id": case_id, "kind": "researcher", "target_account_id": researcher_id})
        notify.send(them, f"FarmLine: new {case['crop']} case near {case['place']}",
                    f"{case['summary']}\nIn the farmer's words: {case['description']}\nOpen FarmLine to reply.",
                    sender=acct)
    return {"ok": True}


@app.get("/api/my/cases")
def my_cases(acct=Depends(farmer)):
    cases = db.query("SELECT * FROM cases WHERE account_id=? ORDER BY created DESC", (acct["id"],))
    for c in cases:
        c["requests"] = db.query(
            "SELECT r.*, a.name, a.institution FROM requests r JOIN accounts a ON a.id=r.target_account_id "
            "WHERE r.case_id=? ORDER BY r.created", (c["id"],))
    return cases


def add_experience(acct, data):
    return db.insert("experiences", {
        "account_id": acct["id"], "title": data["title"].strip(), "crop": data["crop"].strip().lower(),
        "category": data.get("category") or "other", "description": data["description"].strip(),
        "what_worked": data["what_worked"].strip(), "season": data.get("season", ""),
        "days_to_fix": int(data.get("days_to_fix") or 0), "allow_contact": int(data.get("allow_contact", True)),
        "place": place_name(acct), "lat": acct["lat"], "lon": acct["lon"]})


@app.post("/api/cases/{case_id}/solve")
def solve(case_id: int, data=Depends(body), acct=Depends(farmer)):
    case = own_case(case_id, acct)
    what = (data.get("what_worked") or "").strip()
    if len(what) < 10:
        raise HTTPException(400, "Say what fixed it, so the next farmer can use it.")
    db.update("cases", case_id, {"status": "solved", "resolution": what})
    title = case["likely_issue"] if case["likely_issue"] not in ("", "unclear") else f"{case['category']} problem"
    add_experience(acct, {"title": f"{title.capitalize()} on {case['crop']}", "crop": case["crop"],
                          "category": case["category"], "description": case["description"], "what_worked": what})
    return {"ok": True}


# ---------- farmer: share an experience ----------

@app.post("/api/experiences")
def share_experience(data=Depends(body), acct=Depends(farmer)):
    for field, label in [("title", "a title"), ("crop", "the crop"), ("description", "what you saw"),
                         ("what_worked", "what fixed it")]:
        if not (data.get(field) or "").strip():
            raise HTTPException(400, f"Add {label}.")
    return {"id": add_experience(acct, data)}


@app.get("/api/my/experiences")
def my_experiences(acct=Depends(farmer)):
    return db.query("SELECT * FROM experiences WHERE account_id=? ORDER BY created DESC", (acct["id"],))


# ---------- researcher ----------

@app.get("/api/researcher/cases")
def researcher_cases(acct=Depends(researcher)):
    trials = db.query("SELECT * FROM trials WHERE account_id=?", (acct["id"],))
    out = []
    for c in db.query("SELECT c.*, a.name AS farmer FROM cases c JOIN accounts a ON a.id=c.account_id "
                      "ORDER BY c.created DESC LIMIT 200"):
        req = db.one("SELECT * FROM requests WHERE case_id=? AND kind='researcher' AND target_account_id=?",
                     (c["id"], acct["id"]))
        score, dist = matching.score_researcher(acct, c, trials)
        if not req and (score < 5 or c["status"] != "open"):
            continue
        c["farmer"] = c["farmer"].split()[0]
        c.update(request=req, match=score, km=dist and round(dist), similar=len(db.query(
            "SELECT id FROM cases WHERE crop=? AND category=? AND created>? AND id!=?",
            (c["crop"], c["category"], time.time() - 30 * 86400, c["id"]))))
        out.append(c)
    return sorted(out, key=lambda c: (bool(c["request"] and c["request"]["status"] == "answered"),
                                      not c["request"], -c["match"]))


@app.post("/api/cases/{case_id}/reply")
def reply(case_id: int, data=Depends(body), acct=Depends(researcher)):
    case = db.one("SELECT * FROM cases WHERE id=?", (case_id,))
    message = (data.get("message") or "").strip()
    if not case or not message:
        raise HTTPException(400, "Write a message to the farmer.")
    kind = data.get("reply_type") if data.get("reply_type") in ("advice", "trial", "visit") else "advice"
    req = db.one("SELECT * FROM requests WHERE case_id=? AND kind='researcher' AND target_account_id=?",
                 (case_id, acct["id"]))
    req_id = req["id"] if req else db.insert("requests", {"case_id": case_id, "kind": "researcher",
                                                          "target_account_id": acct["id"]})
    db.update("requests", req_id, {"status": "answered", "reply_type": kind, "reply": message,
                                   "replied": time.time()})
    label = {"advice": "sent advice", "trial": "offers to test a solution on your field",
             "visit": "asks to visit or get a sample"}[kind]
    owner = db.one("SELECT * FROM accounts WHERE id=?", (case["account_id"],))
    notify.send(owner, f"FarmLine: reply about your {case['crop']} problem",
                f"{acct['name']} at {acct['institution']} {label}:\n{message}", sender=acct)
    return {"ok": True}


@app.get("/api/researcher/trials")
def list_trials(acct=Depends(researcher)):
    return db.query("SELECT * FROM trials WHERE account_id=? ORDER BY created DESC", (acct["id"],))


@app.post("/api/researcher/trials")
def add_trial(data=Depends(body), acct=Depends(researcher)):
    if not all((data.get(k) or "").strip() for k in ("title", "crop", "problem")):
        raise HTTPException(400, "Add a title, the crop, and the problem it targets.")
    return {"id": db.insert("trials", {"account_id": acct["id"], **{
        k: (data.get(k) or "").strip() for k in ("title", "crop", "problem", "description", "farmer_needs")}})}


@app.delete("/api/researcher/trials/{trial_id}")
def delete_trial(trial_id: int, acct=Depends(researcher)):
    db.execute("DELETE FROM trials WHERE id=? AND account_id=?", (trial_id, acct["id"]))
    return {"ok": True}


@app.get("/api/library")
def library_list(acct=Depends(current)):
    return {"stats": library.stats(),
            "recent": db.query("SELECT id, title, url, source, kind, words, created FROM documents "
                               "WHERE kind != 'dataset' ORDER BY created DESC LIMIT 40")}


@app.post("/api/library")
def library_add(data=Depends(body), acct=Depends(researcher)):
    result = library.add_url(data.get("url") or "", acct["id"])
    if not result["ok"]:
        raise HTTPException(400, result["reason"])
    return result




# ---------- the phone line (separate channel; Twilio voice webhooks, plain TwiML) ----------

def twiml(inner):
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>',
                    media_type="application/xml")


def say(text):
    return f'<Say language="{config.CALL_LANGUAGE}">{escape(text)}</Say>'


def caller_account(phone, city, state, zip_code):
    phone = clean_phone(phone)
    acct = db.one("SELECT * FROM accounts WHERE role='farmer' AND phone=?", (phone,))
    if acct:
        return acct
    account_id = db.insert("accounts", {"role": "farmer"})
    return save_profile(account_id, "farmer", {"name": "Caller", "phone": phone, "city": city.title(),
                                               "state": state, "zip": zip_code}, new=True)


@app.post("/voice")
def voice():
    return twiml(
        f'<Gather input="speech" action="/voice/problem" method="POST" speechTimeout="auto" '
        f'language="{config.CALL_LANGUAGE}">'
        + say("Welcome to Farm Line. Please tell me which crop you grow and what is wrong with it.")
        + "</Gather>" + say("I did not hear anything. Please call again."))


@app.post("/voice/problem")
def voice_problem(SpeechResult: str = Form(""), From: str = Form(""), FromCity: str = Form(""),
                  FromState: str = Form(""), FromZip: str = Form("")):
    if not SpeechResult.strip():
        return twiml(say("Sorry, I could not hear you.") + "<Redirect>/voice</Redirect>")
    acct = caller_account(From, FromCity, FromState, FromZip)
    case, _ = open_case(acct, SpeechResult.strip(), source="phone")
    experiences = matching.find_experiences(case)
    parts, menu = [" ".join(case["first_steps"])], []
    if experiences:
        e = experiences[0]
        parts.append(f"A farmer near {e['place']} had this on their {e['crop']}. This is what worked for them. {e['what_worked']}")
        if e["allow_contact"]:
            menu.append("To share your number with that farmer so they can call you, press 1.")
    menu.append("To send your case to a university researcher who studies this, press 2.")
    return twiml(say(" ".join(parts))
                 + f'<Gather numDigits="1" action="/voice/choice?case_id={case["id"]}" method="POST" timeout="6">'
                 + say(" ".join(menu)) + "</Gather>"
                 + f'<Redirect method="POST">/voice/choice?case_id={case["id"]}</Redirect>')


@app.post("/voice/choice")
def voice_choice(case_id: int, background: BackgroundTasks, Digits: str = Form(""), From: str = Form("")):
    case = db.one("SELECT * FROM cases WHERE id=?", (case_id,))
    acct = case and db.one("SELECT * FROM accounts WHERE id=?", (case["account_id"],))
    if not acct or acct["phone"] != clean_phone(From):
        return twiml(say("Sorry, something went wrong.") + "<Hangup/>")
    experiences, researchers = matching.find_experiences(case), matching.find_researchers(case)
    msg = ""
    if Digits == "1" and experiences and experiences[0]["allow_contact"]:
        ask_farmer(case_id, experiences[0]["experience_id"], acct)
        msg = "Done. We gave that farmer your number. "
    elif Digits == "2" and researchers:
        ask_researcher(case_id, researchers[0]["account_id"], acct)
        msg = "Done. We sent your case to a researcher. You will get a text when they reply. "
    elif Digits == "2":
        msg = "No researcher is signed up for this problem yet. We saved your case. "
    background.add_task(text_after_call, case_id, acct, experiences, researchers)
    return twiml(say(msg + "We are texting you a summary. Thank you for calling Farm Line.") + "<Hangup/>")


def text_after_call(case_id, acct, experiences, researchers):
    """After the caller hangs up: write the article-based solution, then text the summary."""
    case = db.one("SELECT * FROM cases WHERE id=?", (case_id,))
    if config.AI_PROVIDER:
        case["solution"], _ = compute_solution(case)
    notify.send(acct, f"FarmLine: your {case['crop']} problem", summary_text(case, experiences, researchers), "text")


# ---------- startup: tables, demo data, the app page ----------

def seed():
    data = json.loads((ROOT / "seed_data.json").read_text())
    for f in data["farmers"]:
        lat, lon = config.PLACES[f["city"]]
        salt = secrets.token_hex(8)
        fid = db.insert("accounts", {"role": "farmer", "name": f["name"], "phone": clean_phone(f["phone"]),
                                     "city": f["city"], "state": f["state"], "crops": f["crops"], "lat": lat,
                                     "lon": lon, "demo": 1, "salt": salt, "pass_hash": hash_password("1234", salt)})
        e = dict(f["experience"])
        db.insert("experiences", {"account_id": fid, "created": time.time() - e.pop("days_ago") * 86400,
                                  "place": f"{f['city']}, {f['state']}", "lat": lat, "lon": lon, **e})
    for r in data["researchers"]:
        lat, lon = config.PLACES[r["city"]]
        salt, trials = secrets.token_hex(8), r.pop("trials")
        rid = db.insert("accounts", {"role": "researcher", "contact_pref": "email", "lat": lat, "lon": lon,
                                     "demo": 1, "salt": salt, "pass_hash": hash_password("demo1234", salt), **r})
        for t in trials:
            db.insert("trials", {"account_id": rid, **t})
    for name, crop, area, words in [  # two open requests so the researcher view has something to answer
            ("Marcus T.", "corn", "Most of it", "Something is eating ragged holes in my young corn and I see small worms in the whorl. It is in most of the field now."),
            ("Elena R.", "watermelon", "About half", "Half the watermelon vines collapsed over a few days and the stems are brown inside when I cut them.")]:
        open_case(db.one("SELECT * FROM accounts WHERE name=?", (name,)), words, crop, "This week", area, True, "demo")


db.init()
if config.SEED_DEMO and not db.one("SELECT id FROM accounts LIMIT 1"):
    seed()
library.seed_in_background()  # reads sources.txt the first time, while the app is already usable


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/manifest.json")
def manifest():
    return FileResponse(ROOT / "static" / "manifest.json")


@app.get("/icon.svg")
def icon():
    return FileResponse(ROOT / "static" / "icon.svg")
