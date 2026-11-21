"""The article library: real pages and datasets, stored locally and searched by text similarity.

  python library.py add-urls sources.txt                       read every web address in a file
  python library.py add-url https://example.edu/factsheet      read one page or PDF
  python library.py add-csv data.csv --question Q --answer A   import a question/answer dataset
  python library.py search "sorghum sticky leaves aphids"      try a search
  python library.py stats

Retrieval is TF-IDF with cosine similarity, a standard machine-learning text method. It is
written out here in plain Python so there is nothing heavy to install.
"""
import csv, io, math, re, sys, threading
from collections import Counter
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import httpx

import db

AGENT = "FarmLineBot/0.1 (hackathon research project)"
STOP = set("""the a an and or of in on is are was were be been with to it they this that from for not but as at by
have has had can may will which their its into than then these those such also more most other some any all when
where what how who our your you we i if so do does did there here about over under between during after before
per one two three use used using however while within without each both very much many""".split())
_index = {"key": None}
_lock = threading.Lock()


def tokenize(text):
    return [w[:8] for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in STOP]


# ---------- reading pages and datasets ----------

def _allowed(url):
    """Respect the site's robots.txt. If it cannot be read, allow."""
    try:
        p = urlparse(url)
        rp = robotparser.RobotFileParser()
        r = httpx.get(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": AGENT}, timeout=6,
                      follow_redirects=True)
        if r.status_code != 200:
            return True
        rp.parse(r.text.splitlines())
        return rp.can_fetch(AGENT, url)
    except Exception:
        return True


def _extract(response):
    """Returns (title, text) from an HTML page or a PDF."""
    ctype = response.headers.get("content-type", "")
    if "pdf" in ctype or str(response.url).lower().endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(response.content))
        text = " ".join((page.extract_text() or "") for page in reader.pages[:25])
        title = (reader.metadata.title if reader.metadata else None) or Path(urlparse(str(response.url)).path).stem
        return title, text
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else str(response.url)
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"]):
        tag.decompose()
    body = soup.find("article") or soup.find("main") or soup.body or soup
    return title, body.get_text(" ", strip=True)


def _passages(text, size=110, overlap=20):
    words = text.split()
    step = size - overlap
    return [" ".join(words[i:i + size]) for i in range(0, max(1, len(words) - overlap), step)]


def _store(url, title, text, kind, source, added_by=None):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text.split()) < 40:
        return {"ok": False, "url": url, "reason": "Too little readable text on that page."}
    if db.one("SELECT id FROM documents WHERE url=?", (url,)):
        return {"ok": False, "url": url, "reason": "Already in the library."}
    doc_id = db.insert("documents", {"url": url, "title": (title or url)[:200], "source": source, "kind": kind,
                                     "added_by": added_by, "words": len(text.split())})
    with db.conn() as c:
        c.executemany("INSERT INTO passages (doc_id, text) VALUES (?,?)", [(doc_id, p) for p in _passages(text)])
    return {"ok": True, "url": url, "title": title, "words": len(text.split())}


def add_url(url, added_by=None, kind="article"):
    """Fetch one page or PDF and add it to the library. Never raises."""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "url": url, "reason": "Use a full web address starting with https://"}
    if db.one("SELECT id FROM documents WHERE url=?", (url,)):
        return {"ok": False, "url": url, "reason": "Already in the library."}
    try:
        if not _allowed(url):
            return {"ok": False, "url": url, "reason": "That site asks robots not to read this page."}
        r = httpx.get(url, headers={"User-Agent": AGENT}, timeout=20, follow_redirects=True)
        r.raise_for_status()
        title, text = _extract(r)
        final = str(r.url)  # after redirects, so search-result links are saved under the real address
        return _store(final, title, text, kind, urlparse(final).netloc.replace("www.", ""), added_by)
    except Exception as e:
        return {"ok": False, "url": url, "reason": f"Could not read it ({type(e).__name__})."}


def add_urls_file(path):
    lines = [ln.strip() for ln in Path(path).read_text().splitlines()]
    return [add_url(ln) for ln in lines if ln and not ln.startswith("#")]


def add_csv(path, question, answer, crop=None, source=None):
    """Import a question/answer dataset (for example farmer help-line logs). One row = one entry."""
    added = 0
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        for i, rowd in enumerate(csv.DictReader(f)):
            q, a = (rowd.get(question) or "").strip(), (rowd.get(answer) or "").strip()
            if len(q) < 8 or len(a) < 8:
                continue
            head = f"{rowd.get(crop, '').strip()}: " if crop and rowd.get(crop) else ""
            text = f"Question: {head}{q} Answer: {a}"
            url = f"dataset://{Path(path).name}#{i}"
            if db.one("SELECT id FROM documents WHERE url=?", (url,)):
                continue
            doc_id = db.insert("documents", {"url": url, "title": (head + q)[:200], "kind": "dataset",
                                             "source": source or Path(path).name, "words": len(text.split())})
            db.execute("INSERT INTO passages (doc_id, text) VALUES (?,?)", (doc_id, text))
            added += 1
    return added


def stats():
    docs = db.one("SELECT COUNT(*) AS n FROM documents")["n"]
    passages = db.one("SELECT COUNT(*) AS n FROM passages")["n"]
    by_kind = {r["kind"]: r["n"] for r in db.query("SELECT kind, COUNT(*) AS n FROM documents GROUP BY kind")}
    return {"documents": docs, "passages": passages, "by_kind": by_kind}


def seed_in_background():
    """On first start, read sources.txt without holding up the app."""
    path = Path(__file__).parent / "sources.txt"
    if path.exists() and stats()["documents"] == 0:
        threading.Thread(target=add_urls_file, args=(path,), daemon=True).start()


# ---------- search: TF-IDF + cosine similarity ----------

def _build():
    rows = db.query("SELECT p.id, p.doc_id, p.text FROM passages p")
    df, docs = Counter(), []
    for r in rows:
        tf = Counter(tokenize(r["text"]))
        docs.append((r, tf))
        df.update(tf.keys())
    n = max(1, len(docs))
    idf = {t: math.log((1 + n) / (1 + d)) + 1 for t, d in df.items()}
    vectors = []
    for r, tf in docs:
        vec = {t: (1 + math.log(c)) * idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append((r, vec, norm))
    return {"idf": idf, "vectors": vectors}


def _get_index():
    key = db.one("SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS m FROM passages")
    key = (key["n"], key["m"])
    with _lock:
        if _index["key"] != key:
            _index.update(_build(), key=key)
        return _index


def search(query, k=5):
    """Best-matching passages, at most one per document. Each result has a short snippet and its link."""
    index = _get_index()
    q_tf = Counter(tokenize(query))
    q = {t: (1 + math.log(c)) * index["idf"].get(t, 0) for t, c in q_tf.items()}
    q_norm = math.sqrt(sum(v * v for v in q.values())) or 1.0
    scored = []
    for r, vec, norm in index["vectors"]:
        dot = sum(w * vec.get(t, 0) for t, w in q.items())
        if dot:
            scored.append((dot / (q_norm * norm), r))
    scored.sort(key=lambda s: -s[0])
    out, seen = [], set()
    best = scored[0][0] if scored else 0
    for score, r in scored:
        if score < 0.1 or score < 0.5 * best or len(out) >= k:  # weak or far-behind matches are noise
            break
        if r["doc_id"] in seen:
            continue
        seen.add(r["doc_id"])
        doc = db.one("SELECT * FROM documents WHERE id=?", (r["doc_id"],))
        out.append({"doc_id": doc["id"], "title": doc["title"], "url": doc["url"], "source": doc["source"],
                    "kind": doc["kind"], "passage": r["text"], "snippet": r["text"][:260].rsplit(" ", 1)[0] + "…",
                    "score": round(score, 3)})
    return out


if __name__ == "__main__":
    db.init()
    args = sys.argv[1:]
    cmd = args[0] if args else "stats"
    if cmd == "add-urls" and len(args) > 1:
        for res in add_urls_file(args[1]):
            print("added  " if res["ok"] else "skipped", res["url"], "" if res["ok"] else "- " + res["reason"])
    elif cmd == "add-url" and len(args) > 1:
        print(add_url(args[1]))
    elif cmd == "add-csv" and len(args) > 1:
        opt = dict(zip(args[2::2], args[3::2]))
        if "--question" not in opt or "--answer" not in opt:
            sys.exit("Use: python library.py add-csv file.csv --question COLUMN --answer COLUMN [--crop COLUMN]")
        print("rows added:", add_csv(args[1], opt["--question"], opt["--answer"], opt.get("--crop")))
    elif cmd == "search" and len(args) > 1:
        for hit in search(" ".join(args[1:])):
            print(f"{hit['score']:.2f}  {hit['title']}\n      {hit['url']}\n      {hit['snippet']}\n")
    print(stats())
