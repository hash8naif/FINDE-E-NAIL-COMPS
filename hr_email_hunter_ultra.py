#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║        HR EMAIL HUNTER  ULTRA  v7.0  –  MEGA UNIFIED EDITION           ║
║   Website Crawler · Hunter.io · O365 API · SMTP · Instagram · TikTok   ║
║                  + Optional Tkinter GUI launcher                        ║
╚══════════════════════════════════════════════════════════════════════════╝

Install all dependencies:
    pip install requests beautifulsoup4 dnspython aiohttp rich tqdm colorama

Run CLI (no GUI needed):
    python hr_email_hunter_ultra.py -d google.com
    python hr_email_hunter_ultra.py -d apple.com -k HUNTER_KEY -n "Jane Doe"
    python hr_email_hunter_ultra.py -d shopify.com --instagram shopify --tiktok shopify --report
    python hr_email_hunter_ultra.py -d microsoft.com -k KEY -n "Jane Doe" -w 20 --report --export

Run with GUI:
    python hr_email_hunter_ultra.py --gui
"""

import os
import re
import sys
import json
import csv
import uuid
import time
import socket
import smtplib
import sqlite3
import argparse
from datetime import datetime
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Set

import requests
import dns.resolver

# ── Optional: aiohttp ──────────────────────────────────────────────────
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

# ── Optional: BeautifulSoup ────────────────────────────────────────────
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# ── Optional: Rich (pretty terminal output) ────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.rule import Rule
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    class _Console:
        def print(self, *a, **kw):
            import re as _re
            msg = str(a[0]) if a else ""
            msg = _re.sub(r'\[.*?\]', '', msg)
            print(msg)
    console = _Console()

# ── Optional: tqdm ─────────────────────────────────────────────────────
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ══════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════
VERSION   = "7.0 MEGA"
EMAIL_RE  = re.compile(r'[\w.\-+]+@[\w.\-]+\.[a-zA-Z]{2,}')
DB_PATH   = "hr_cache.db"

HR_ALIASES = [
    "hr", "careers", "recruitment", "jobs", "talent", "hiring", "recruiter",
    "humanresources", "hrdept", "staffing", "people", "talentacquisition",
    "career", "joinus", "workwithus", "hrteam", "peopleops", "peopleandculture",
    "talentteam", "hrbp", "talentpartner", "recruiting", "hroffice", "peopleteam",
    "hradmin", "hrmail", "careerservice", "employer", "recruit", "apply",
    "opportunities", "work", "join", "hq", "contact", "info", "hello", "team",
    "office", "admin", "general", "enquiries", "jobseekers", "hiringteam",
    "hr.team", "talent.team", "people.team", "careers.team",
]

CRAWL_PATHS = [
    "/", "/contact", "/contact-us", "/about", "/about-us", "/team",
    "/our-team", "/careers", "/jobs", "/work-with-us", "/join-us",
    "/people", "/hr", "/company", "/meet-the-team", "/who-we-are",
    "/hire", "/hiring", "/apply",
]

NAME_FORMATS = [
    "{first}.{last}", "{f}.{last}", "{first}{last}", "{first}_{last}",
    "{last}.{first}", "{f}{last}", "{first}.{l}", "{first}",
    "{last}", "{f}_{last}", "{first}-{last}", "{f}-{last}",
    "{last}{f}", "{last}{first}", "{first}{l}",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ══════════════════════════════════════════════════════════════════════
#  SQLITE CACHE
# ══════════════════════════════════════════════════════════════════════
class Cache:
    def __init__(self, path: str = DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._setup()

    def _setup(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                email   TEXT PRIMARY KEY,
                domain  TEXT,
                status  TEXT,
                score   INTEGER,
                sources TEXT,
                ts      TEXT
            )""")
        self.conn.commit()

    def get(self, email: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM results WHERE email=?", (email,)).fetchone()
        if row:
            return {"email": row[0], "domain": row[1], "status": row[2],
                    "score": row[3], "sources": json.loads(row[4]), "ts": row[5]}
        return None

    def save(self, email: str, domain: str, status: str, score: int, sources: list):
        self.conn.execute(
            "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?)",
            (email, domain, status, score,
             json.dumps(sources), datetime.now().isoformat()))
        self.conn.commit()

    def all_for_domain(self, domain: str) -> list:
        rows = self.conn.execute(
            "SELECT * FROM results WHERE domain=?", (domain,)).fetchall()
        return [{"email": r[0], "status": r[2], "score": r[3],
                 "sources": json.loads(r[4])} for r in rows]

# ══════════════════════════════════════════════════════════════════════
#  WEBSITE CRAWLER
# ══════════════════════════════════════════════════════════════════════
class WebsiteCrawler:
    FALSE_POSITIVES = {"example.com", "sentry.io", "schema.org",
                       "w3.org", ".png", ".jpg", ".svg", "noreply", "no-reply"}

    def __init__(self, domain: str, timeout: int = 10):
        self.domain  = domain
        self.timeout = timeout

    def _crawl_one(self, url: str) -> Set[str]:
        emails = set()
        try:
            r = requests.get(url, headers=HEADERS,
                             timeout=self.timeout, allow_redirects=True)
            if r.status_code != 200:
                return emails
            text = r.text
            for m in EMAIL_RE.findall(text):
                m = m.lower().strip(".,;")
                if any(fp in m for fp in self.FALSE_POSITIVES):
                    continue
                emails.add(m)
            if HAS_BS4:
                soup = BeautifulSoup(text, "html.parser")
                for tag in soup.find_all("a", href=True):
                    href = tag["href"]
                    if href.startswith("mailto:"):
                        e = href[7:].split("?")[0].lower().strip()
                        if e and "@" in e:
                            emails.add(e)
        except Exception:
            pass
        return emails

    def crawl(self, workers: int = 10) -> Dict[str, List[str]]:
        urls = []
        for proto in ("https", "http"):
            for path in CRAWL_PATHS:
                urls.append(f"{proto}://{self.domain}{path}")

        all_found: Dict[str, Set[str]] = {}
        company_part = self.domain.split(".")[-2]

        with ThreadPoolExecutor(max_workers=workers) as ex:
            fmap = {ex.submit(self._crawl_one, u): u for u in urls}
            for future in as_completed(fmap):
                url = fmap[future]
                for email in future.result():
                    if company_part in email:
                        if email not in all_found:
                            all_found[email] = set()
                        all_found[email].add(url)

        return {e: sorted(pages) for e, pages in all_found.items()}

# ══════════════════════════════════════════════════════════════════════
#  SOCIAL SCRAPER  (Instagram + TikTok)
# ══════════════════════════════════════════════════════════════════════
class SocialScraper:
    MOBILE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.MOBILE_HEADERS)

    def _find_email(self, text: str) -> Optional[str]:
        m = EMAIL_RE.search(text or "")
        return m.group(0).lower() if m else None

    def instagram(self, username: str) -> dict:
        try:
            r = self.session.get(
                f"https://www.instagram.com/{username}/", timeout=12)
            if r.status_code != 200:
                return {"platform": "Instagram", "username": username,
                        "email": None, "error": f"HTTP {r.status_code}"}
            for pattern in [
                r'<meta property="og:description" content="([^"]*)"',
                r'"biography":"(.*?)"',
            ]:
                m = re.search(pattern, r.text)
                if m:
                    bio = m.group(1).replace("\\n", " ").replace('\\"', '"')
                    return {"platform": "Instagram", "username": username,
                            "bio": bio[:200], "email": self._find_email(bio)}
            return {"platform": "Instagram", "username": username,
                    "bio": "(raw scan)", "email": self._find_email(r.text)}
        except Exception as e:
            return {"platform": "Instagram", "username": username,
                    "email": None, "error": str(e)}

    def tiktok(self, username: str) -> dict:
        try:
            r = self.session.get(
                f"https://www.tiktok.com/@{username}", timeout=12)
            if r.status_code != 200:
                return {"platform": "TikTok", "username": username,
                        "email": None, "error": f"HTTP {r.status_code}"}
            # Method 1: rehydration JSON
            m = re.search(
                r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                r.text, re.DOTALL)
            if m:
                try:
                    data  = json.loads(m.group(1))
                    scope = data.get('__DEFAULT_SCOPE__', {})
                    user  = (scope.get('webapp.user-detail', {})
                                  .get('userInfo', {})
                                  .get('user', {}))
                    bio = user.get('bioDescription') or user.get('signature', '')
                    if bio:
                        return {"platform": "TikTok", "username": username,
                                "bio": bio[:200], "email": self._find_email(bio)}
                except (json.JSONDecodeError, KeyError):
                    pass
            # Method 2: direct regex
            m = re.search(r'"bioDescription":"(.*?)"', r.text)
            if m:
                bio = m.group(1)
                return {"platform": "TikTok", "username": username,
                        "bio": bio[:200], "email": self._find_email(bio)}
            return {"platform": "TikTok", "username": username,
                    "bio": "(raw scan)", "email": self._find_email(r.text)}
        except Exception as e:
            return {"platform": "TikTok", "username": username,
                    "email": None, "error": str(e)}

# ══════════════════════════════════════════════════════════════════════
#  O365 CHECKER
# ══════════════════════════════════════════════════════════════════════
class O365Checker:
    URL = "https://login.microsoftonline.com/common/GetCredentialType"

    @classmethod
    def check(cls, email: str) -> bool:
        try:
            r = requests.post(cls.URL,
                              headers={"Content-Type": "application/json"},
                              json={"Username": email}, timeout=7)
            return r.json().get("IfExistsResult") == 0
        except Exception:
            return False

    @classmethod
    def bulk(cls, emails: List[str], workers: int = 12,
             progress_cb=None) -> List[dict]:
        out = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fmap = {ex.submit(cls.check, e): e for e in emails}
            for f in as_completed(fmap):
                email = fmap[f]
                try:
                    valid = f.result()
                except Exception:
                    valid = False
                out.append({"email": email, "valid": valid,
                            "status": "valid" if valid else "invalid",
                            "method": "O365"})
                if progress_cb:
                    progress_cb(email, valid)
        return out

# ══════════════════════════════════════════════════════════════════════
#  SMTP VERIFIER
# ══════════════════════════════════════════════════════════════════════
class SMTPVerifier:
    def __init__(self, mx_records: List[str]):
        self.mx       = mx_records
        self.catchall = False

    def _probe(self, email: str) -> str:
        for mx in self.mx[:1]:
            try:
                srv = smtplib.SMTP(mx, 25, timeout=7)
                srv.ehlo_or_helo_if_needed()
                srv.mail("probe@example.com")
                code, _ = srv.rcpt(email)
                srv.quit()
                if code in (250, 251): return "valid"
                if code in (550, 553, 554): return "invalid"
                return "unknown"
            except (socket.timeout, ConnectionRefusedError,
                    smtplib.SMTPServerDisconnected):
                return "timeout"
            except Exception:
                return "error"
        return "error"

    def detect_catchall(self, domain: str) -> bool:
        test = f"{uuid.uuid4().hex[:10]}@{domain}"
        self.catchall = (self._probe(test) == "valid")
        return self.catchall

    def bulk(self, emails: List[str], workers: int = 12,
             progress_cb=None) -> List[dict]:
        if self.catchall:
            return [{"email": e, "valid": None, "status": "catch_all",
                     "method": "SMTP"} for e in emails]
        out = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fmap = {ex.submit(self._probe, e): e for e in emails}
            for f in as_completed(fmap):
                email = fmap[f]
                try:
                    status = f.result()
                except Exception:
                    status = "error"
                valid = (status == "valid")
                out.append({"email": email, "valid": valid,
                            "status": status, "method": "SMTP"})
                if progress_cb:
                    progress_cb(email, valid)
        return out

# ══════════════════════════════════════════════════════════════════════
#  HUNTER.IO
# ══════════════════════════════════════════════════════════════════════
class HunterAPI:
    BASE = "https://api.hunter.io/v2"

    def __init__(self, key: str, domain: str):
        self.key    = key
        self.domain = domain

    def search(self) -> dict:
        try:
            r = requests.get(
                f"{self.BASE}/domain-search",
                params={"domain": self.domain, "api_key": self.key},
                timeout=15)
            d = r.json().get("data", {})
            return {
                "pattern": d.get("pattern"),
                "emails": [
                    {"email": e["value"],
                     "confidence": e.get("confidence", 0),
                     "type": e.get("type", "?")}
                    for e in d.get("emails", [])
                ]
            }
        except Exception as e:
            console.print(f"Hunter error: {e}")
            return {}

# ══════════════════════════════════════════════════════════════════════
#  EMAIL GENERATOR
# ══════════════════════════════════════════════════════════════════════
class EmailGenerator:
    @staticmethod
    def aliases(domain: str) -> List[str]:
        return [f"{a}@{domain}" for a in HR_ALIASES]

    @staticmethod
    def from_names(names: List[str], domain: str,
                   pattern: Optional[str] = None) -> List[str]:
        out = set()
        for full in names:
            parts = full.strip().lower().split()
            if len(parts) < 2:
                continue
            first, last = parts[0], parts[-1]
            f, l = first[0], last[0]
            ctx = {"first": first, "last": last, "f": f, "l": l}
            if pattern:
                try:
                    out.add(f"{pattern.format(**ctx)}@{domain}")
                except Exception:
                    pass
            for fmt in NAME_FORMATS:
                try:
                    out.add(f"{fmt.format(**ctx)}@{domain}")
                except Exception:
                    pass
        return list(out)

# ══════════════════════════════════════════════════════════════════════
#  CONFIDENCE SCORER
# ══════════════════════════════════════════════════════════════════════
def score_email(email: str, sources: List[str],
                verified: bool, hunter_conf: int = 0) -> int:
    s = 0
    if verified:                       s += 50
    if "website" in sources:           s += 20
    if "hunter"  in sources:           s += 15
    if "social"  in sources:           s += 10
    if hunter_conf:                    s += min(hunter_conf // 10, 5)
    if "o365" in sources and verified: s += 5
    local = email.split("@")[0]
    if local in ("info", "hello", "contact", "admin", "general"):
        s = max(s - 10, 0)
    return min(s, 100)

# ══════════════════════════════════════════════════════════════════════
#  HTML REPORT GENERATOR
# ══════════════════════════════════════════════════════════════════════
def generate_html_report(domain: str, results: List[dict],
                          social: List[dict], hunter_data: dict,
                          is_outlook: bool, website_emails: dict,
                          elapsed: float) -> str:
    valid = sorted([r for r in results if r.get("valid")],
                   key=lambda x: x.get("score", 0), reverse=True)
    ts    = datetime.now().strftime("%Y-%m-%d %H:%M")

    def badge(score):
        if score >= 70: return f'<span class="badge high">{score}</span>'
        if score >= 40: return f'<span class="badge med">{score}</span>'
        return f'<span class="badge low">{score}</span>'

    rows = "".join(
        f"<tr><td><a href='mailto:{r['email']}'>{r['email']}</a></td>"
        f"<td>{badge(r.get('score',0))}</td>"
        f"<td>{r.get('method','?')}</td>"
        f"<td>{', '.join(r.get('sources',[]))}</td></tr>"
        for r in valid
    )
    social_rows = "".join(
        f"<tr><td>{s.get('platform','?')}</td>"
        f"<td>@{s.get('username','?')}</td>"
        f"<td><a href='mailto:{s['email']}'>{s['email']}</a></td></tr>"
        for s in social if s.get("email")
    )
    web_rows = "".join(
        f"<tr><td>{email}</td>"
        f"<td>{'  '.join(f'<a href=\"{p}\" target=\"_blank\">link</a>' for p in pages[:3])}</td></tr>"
        for email, pages in website_emails.items()
    )
    hunter_rows = "".join(
        f"<tr><td>{e['email']}</td><td>{e['confidence']}%</td>"
        f"<td>{e.get('type','?')}</td></tr>"
        for e in hunter_data.get("emails", [])
    )
    pattern   = hunter_data.get("pattern", "Unknown")
    total_chk = len(results)
    total_val = len(valid)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HR Email Report — {domain}</title>
<style>
  :root {{ --bg:#0f1117; --card:#1a1d27; --accent:#7c6aff;
           --green:#22c55e; --yellow:#eab308; --red:#ef4444;
           --text:#e2e8f0; --muted:#94a3b8; --border:#2d3250; }}
  *{{ box-sizing:border-box; margin:0; padding:0; }}
  body{{ background:var(--bg); color:var(--text);
         font-family:'Segoe UI',system-ui,sans-serif; padding:2rem; }}
  h1{{ font-size:2rem; color:var(--accent); margin-bottom:.25rem; }}
  h2{{ font-size:1.1rem; color:var(--accent); margin:1.5rem 0 .75rem; }}
  .sub{{ color:var(--muted); font-size:.9rem; margin-bottom:2rem; }}
  .grid{{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:1rem; margin-bottom:2rem; }}
  .stat{{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:1.25rem; text-align:center; }}
  .stat .num{{ font-size:2.5rem; font-weight:700; color:var(--accent); }}
  .stat .lbl{{ color:var(--muted); font-size:.8rem; margin-top:.25rem; }}
  table{{ width:100%; border-collapse:collapse; font-size:.9rem; }}
  thead tr{{ background:var(--card); }}
  th{{ padding:.65rem 1rem; text-align:left; color:var(--muted); font-weight:600; font-size:.8rem; text-transform:uppercase; }}
  td{{ padding:.65rem 1rem; border-bottom:1px solid var(--border); }}
  tr:hover td{{ background:rgba(124,106,255,.06); }}
  a{{ color:var(--accent); text-decoration:none; }}
  a:hover{{ text-decoration:underline; }}
  .badge{{ display:inline-block; padding:.15rem .55rem; border-radius:99px; font-size:.8rem; font-weight:700; }}
  .high{{ background:rgba(34,197,94,.2); color:var(--green); }}
  .med{{ background:rgba(234,179,8,.2); color:var(--yellow); }}
  .low{{ background:rgba(239,68,68,.2); color:var(--red); }}
  .card{{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:1.5rem; margin-bottom:1.5rem; }}
  .tip{{ background:rgba(124,106,255,.1); border-left:3px solid var(--accent); padding:.75rem 1rem; border-radius:0 8px 8px 0; font-size:.9rem; margin-top:1rem; }}
  footer{{ margin-top:3rem; color:var(--muted); font-size:.8rem; border-top:1px solid var(--border); padding-top:1rem; }}
</style>
</head>
<body>
<h1>🎯 HR Email Hunter ULTRA v{VERSION}</h1>
<p class="sub">Report for <strong>{domain}</strong> — {ts} — {elapsed:.1f}s</p>
<div class="grid">
  <div class="stat"><div class="num">{total_val}</div><div class="lbl">Valid Emails</div></div>
  <div class="stat"><div class="num">{total_chk}</div><div class="lbl">Total Checked</div></div>
  <div class="stat"><div class="num">{len(website_emails)}</div><div class="lbl">Website Emails</div></div>
  <div class="stat"><div class="num">{'M365' if is_outlook else 'SMTP'}</div><div class="lbl">Mail Server</div></div>
  <div class="stat"><div class="num">{pattern or '?'}</div><div class="lbl">Email Pattern</div></div>
</div>
<div class="card">
  <h2>✅ Verified HR Emails</h2>
  {"<p style='color:var(--muted)'>No verified emails found. Try adding a Hunter API key or recruiter names.</p>" if not valid else ""}
  {"<table><thead><tr><th>Email</th><th>Score</th><th>Method</th><th>Sources</th></tr></thead><tbody>" + rows + "</tbody></table>" if valid else ""}
  <div class="tip">💡 Sort by score — 70+ = high confidence. Start with those.</div>
</div>
{"<div class='card'><h2>🌐 Found on Company Website</h2><table><thead><tr><th>Email</th><th>Pages</th></tr></thead><tbody>" + web_rows + "</tbody></table></div>" if web_rows else ""}
{"<div class='card'><h2>📌 Hunter.io Known Emails</h2><table><thead><tr><th>Email</th><th>Confidence</th><th>Type</th></tr></thead><tbody>" + hunter_rows + "</tbody></table></div>" if hunter_rows else ""}
{"<div class='card'><h2>📱 Social Media Emails</h2><table><thead><tr><th>Platform</th><th>Handle</th><th>Email</th></tr></thead><tbody>" + social_rows + "</tbody></table></div>" if social_rows else ""}
<div class="card">
  <h2>🗺️ Action Plan</h2>
  <ol style="padding-left:1.5rem;line-height:2.2">
    <li>Start with emails scored <strong>70+</strong> — highest confidence.</li>
    <li>Use pattern <strong>{pattern}@{domain}</strong> with recruiter names from LinkedIn.</li>
    <li>Write a personalised email — mention a specific role or project.</li>
    <li>Follow up once after 5–7 business days if no reply.</li>
    <li>Connect with the recruiter on LinkedIn the same day.</li>
  </ol>
</div>
<footer>HR Email Hunter ULTRA v{VERSION} — for job searching use only.</footer>
</body>
</html>"""

    ts2   = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"hr_report_{domain.replace('.','_')}_{ts2}.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    return fname

# ══════════════════════════════════════════════════════════════════════
#  RICH TERMINAL SUMMARY
# ══════════════════════════════════════════════════════════════════════
BANNER = """
 ██╗  ██╗██████╗     ██╗  ██╗██╗   ██╗███╗   ██╗████████╗███████╗██████╗
 ██║  ██║██╔══██╗    ██║  ██║██║   ██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗
 ███████║██████╔╝    ███████║██║   ██║██╔██╗ ██║   ██║   █████╗  ██████╔╝
 ██╔══██║██╔══██╗    ██╔══██║██║   ██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗
 ██║  ██║██║  ██║    ██║  ██║╚██████╔╝██║ ╚████║   ██║   ███████╗██║  ██║
 ╚═╝  ╚═╝╚═╝  ╚═╝    ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝
"""

def print_summary(domain, results, social_hits, hunter_data,
                  website_emails, is_outlook, catch_all):
    valid = sorted([r for r in results if r.get("valid")],
                   key=lambda x: x.get("score", 0), reverse=True)

    if HAS_RICH:
        console.print(Rule("[bold cyan]FINAL RESULTS[/bold cyan]"))
        if valid:
            t = Table(title=f"✅ Verified HR Emails — {domain}",
                      show_header=True, header_style="bold cyan",
                      border_style="dim", show_lines=True)
            t.add_column("Email",   style="green",  min_width=38)
            t.add_column("Score",   justify="center", width=7)
            t.add_column("Method",  width=8)
            t.add_column("Sources", style="dim")
            for r in valid:
                sc = r.get("score", 0)
                sc_str = (f"[green]{sc}[/]" if sc >= 70 else
                          f"[yellow]{sc}[/]" if sc >= 40 else f"[red]{sc}[/]")
                t.add_row(r["email"], sc_str, r.get("method", "?"),
                          ", ".join(r.get("sources", [])))
            console.print(t)
        else:
            console.print("[yellow]No verified emails found. Try adding a Hunter API key.[/]")

        if website_emails:
            wt = Table(title="🌐 Emails on company website",
                       header_style="bold blue", border_style="dim")
            wt.add_column("Email", style="cyan")
            wt.add_column("Found on", style="dim")
            for email, pages in website_emails.items():
                wt.add_row(email, pages[0] if pages else "?")
            console.print(wt)

        if any(s.get("email") for s in social_hits):
            st = Table(title="📱 Social Bio Emails",
                       header_style="bold magenta", border_style="dim")
            st.add_column("Platform"); st.add_column("Handle")
            st.add_column("Email", style="cyan")
            for s in social_hits:
                if s.get("email"):
                    st.add_row(s.get("platform", "?"),
                               f"@{s.get('username','?')}", s["email"])
            console.print(st)

        console.print(Panel(
            "[bold]Your action plan:[/bold]\n"
            "1. Email addresses with score [green]70+[/] first.\n"
            f"2. Pattern: [cyan]{hunter_data.get('pattern','?')}@{domain}[/] — use with LinkedIn names.\n"
            "3. Personalise your message — mention a specific role.\n"
            "4. Follow up once after 5–7 days if no reply.\n"
            "5. Connect on LinkedIn the same day you email.",
            title="🗺️  Action Plan", border_style="green"
        ))
    else:
        print("\n" + "="*60)
        print(f"  RESULTS — {domain}")
        print("="*60)
        for r in valid:
            print(f"  ✅  {r['email']:<42}  score:{r.get('score',0)}")
        if not valid:
            print("  ❌  No verified emails found.")
        print("="*60)

# ══════════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════
def run(args, log_cb=None):
    """
    Core scan function.
    args      – argparse Namespace (or a simple object with the same attrs)
    log_cb    – optional callable(message: str) for GUI live-log streaming
    """
    def log(msg):
        if log_cb:
            log_cb(msg)
        else:
            console.print(msg)

    def step(n, total, label):
        log(f"[{n}/{total}] {label}")

    t_start       = time.time()
    domain        = args.domain.lower().strip()
    cache         = Cache()
    all_emails: Dict[str, dict] = {}
    social_hits   = []
    hunter_data   = {}
    website_emails: Dict[str, List[str]] = {}
    is_outlook    = False
    catch_all     = False
    pattern       = None

    if HAS_RICH and not log_cb:
        console.print(f"[bold cyan]{BANNER}[/bold cyan]")
        console.print(f"[bold]  ULTRA v{VERSION}  —  Target: [cyan]{domain}[/cyan][/bold]\n")
    elif not log_cb:
        print(f"\n🎯 HR EMAIL HUNTER ULTRA v{VERSION}\nTarget: {domain}\n")

    # ── 1. MX ─────────────────────────────────────────────────────────
    step(1, 7, "Detecting mail server (MX records)…")
    mx_hosts = []
    try:
        answers    = dns.resolver.resolve(domain, "MX")
        mx_records = sorted((r.preference, str(r.exchange).rstrip("."))
                            for r in answers)
        mx_hosts   = [h for _, h in mx_records]
        is_outlook = any("outlook" in h.lower() for h in mx_hosts)
        log(f"   MX: {mx_hosts[0]}" + (" ✅ Microsoft 365 / Outlook" if is_outlook else ""))
    except Exception as e:
        log(f"   ⚠ MX lookup failed: {e}")

    # ── 2. HUNTER.IO ──────────────────────────────────────────────────
    step(2, 7, "Hunter.io API lookup…")
    if getattr(args, 'hunter_key', None):
        hunter      = HunterAPI(args.hunter_key, domain)
        hunter_data = hunter.search()
        pattern     = hunter_data.get("pattern")
        for e in hunter_data.get("emails", []):
            email = e["email"].lower()
            if email not in all_emails:
                all_emails[email] = {"sources": [], "score": 0, "method": "Hunter",
                                     "valid": True, "status": "hunter_found",
                                     "hunter_conf": e["confidence"]}
            all_emails[email]["sources"].append("hunter")
        n = len(hunter_data.get("emails", []))
        log(f"   Found {n} emails. Pattern: {pattern}@{domain}" if pattern
            else f"   Found {n} emails.")
    else:
        log("   (no Hunter key — skipped)")

    # ── 3. WEBSITE CRAWLER ────────────────────────────────────────────
    step(3, 7, f"Crawling company website ({len(CRAWL_PATHS)} pages)…")
    crawler        = WebsiteCrawler(domain)
    website_emails = crawler.crawl(workers=10)
    for email, pages in website_emails.items():
        if email not in all_emails:
            all_emails[email] = {"sources": [], "score": 0, "method": "Website",
                                 "valid": True, "status": "website_found"}
        all_emails[email]["sources"].append("website")
    log(f"   Found {len(website_emails)} email(s) on website.")

    # ── 4. SOCIAL MEDIA ───────────────────────────────────────────────
    step(4, 7, "Scraping social media bios…")
    scraper = SocialScraper()
    for handle, fn, platform in [
        (getattr(args, 'instagram', None), scraper.instagram, "Instagram"),
        (getattr(args, 'tiktok',    None), scraper.tiktok,    "TikTok"),
    ]:
        if not handle:
            log(f"   {platform}: (skipped — no handle)")
            continue
        log(f"   {platform} @{handle}…")
        result = fn(handle)
        social_hits.append(result)
        email = result.get("email")
        if email:
            if email not in all_emails:
                all_emails[email] = {"sources": [], "score": 0,
                                     "method": platform, "valid": True,
                                     "status": "social_found"}
            all_emails[email]["sources"].append("social")
            log(f"   {platform}: ✅ found {email}")
        else:
            log(f"   {platform}: no email in bio.")

    # ── 5. GENERATE CANDIDATES ────────────────────────────────────────
    step(5, 7, "Generating candidate emails…")
    candidates = set(EmailGenerator.aliases(domain))
    if getattr(args, 'names', None):
        for e in EmailGenerator.from_names(args.names, domain, pattern):
            candidates.add(e)
    fresh = [e for e in candidates if e not in all_emails]
    log(f"   {len(candidates)} total candidates, {len(fresh)} fresh to verify.")

    # ── 6. VERIFICATION ───────────────────────────────────────────────
    step(6, 7, "Verifying emails…")
    workers = getattr(args, 'workers', 15)

    def pb(email, valid):
        mark = "✅" if valid else "❌"
        log(f"   {mark}  {email}")

    if is_outlook or not mx_hosts:
        log("   Using Microsoft O365 API (fast, no SMTP needed)…")
        smtp_results = O365Checker.bulk(fresh, workers=workers, progress_cb=pb)
    else:
        log("   Using SMTP verifier…")
        verifier = SMTPVerifier(mx_hosts)
        log("   Catch-all test…")
        if verifier.detect_catchall(domain):
            catch_all = True
            log("   ⚠ Catch-all detected! SMTP results may be unreliable.")
        else:
            log("   Not catch-all ✅")
        smtp_results = verifier.bulk(fresh, workers=workers, progress_cb=pb)

    for r in smtp_results:
        email = r["email"].lower()
        if r.get("valid"):
            if email not in all_emails:
                all_emails[email] = {"sources": [], "score": 0,
                                     "method": r["method"], "valid": True,
                                     "status": r["status"]}
            all_emails[email]["sources"].append(
                "o365" if r["method"] == "O365" else "smtp")
            all_emails[email]["valid"]  = True
            all_emails[email]["method"] = r["method"]

    # ── SCORING ───────────────────────────────────────────────────────
    results_list = []
    for email, meta in all_emails.items():
        if not meta.get("valid"):
            continue
        sc = score_email(email, meta["sources"], True,
                         meta.get("hunter_conf", 0))
        meta["score"] = sc
        meta["email"] = email
        results_list.append(meta)
        cache.save(email, domain, meta.get("status", "found"),
                   sc, meta["sources"])

    # ── 7. EXPORT ─────────────────────────────────────────────────────
    step(7, 7, "Saving results…")
    elapsed = time.time() - t_start

    if getattr(args, 'export', False) or getattr(args, 'report', False):
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre = f"hr_{domain.replace('.','_')}_{ts}"

        valid_list = [r for r in results_list if r.get("valid")]
        if valid_list and getattr(args, 'export', False):
            csv_path = f"{pre}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["email", "score", "method", "sources"])
                w.writeheader()
                for r in sorted(valid_list, key=lambda x: x.get("score", 0), reverse=True):
                    w.writerow({"email": r["email"], "score": r.get("score", 0),
                                "method": r.get("method", "?"),
                                "sources": ",".join(r.get("sources", []))})
            log(f"   CSV  saved → {csv_path}")

            json_path = f"{pre}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({"domain": domain, "ts": ts, "results": valid_list}, f, indent=2)
            log(f"   JSON saved → {json_path}")

        if getattr(args, 'report', False):
            html_path = generate_html_report(
                domain, results_list, social_hits, hunter_data,
                is_outlook, website_emails, elapsed)
            log(f"   HTML report → {html_path}  ← open in your browser!")

    # ── SUMMARY ───────────────────────────────────────────────────────
    if not log_cb:
        print_summary(domain, results_list, social_hits, hunter_data,
                      website_emails, is_outlook, catch_all)

    elapsed = time.time() - t_start
    log(f"\n✔ Done in {elapsed:.1f}s — Good luck! 🍀\n")

    return results_list, social_hits, hunter_data, website_emails

# ══════════════════════════════════════════════════════════════════════
#  TKINTER GUI
# ══════════════════════════════════════════════════════════════════════
def launch_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, scrolledtext, messagebox, filedialog
    except ImportError:
        print("❌ Tkinter not available. Run: python hr_email_hunter_ultra.py -d yourdomain.com")
        sys.exit(1)

    import threading

    root = tk.Tk()
    root.title(f"HR Email Hunter Ultra v{VERSION}")
    root.geometry("860x720")
    root.configure(bg="#0f1117")
    root.resizable(True, True)

    DARK   = "#0f1117"
    CARD   = "#1a1d27"
    ACCENT = "#7c6aff"
    GREEN  = "#22c55e"
    YELLOW = "#eab308"
    RED    = "#ef4444"
    TEXT   = "#e2e8f0"
    MUTED  = "#94a3b8"
    BORDER = "#2d3250"

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TNotebook",        background=DARK, borderwidth=0)
    style.configure("TNotebook.Tab",    background=CARD, foreground=MUTED,
                    padding=[14, 6], font=("Segoe UI", 10))
    style.map("TNotebook.Tab",
              background=[("selected", DARK)],
              foreground=[("selected", ACCENT)])
    style.configure("TFrame",           background=DARK)
    style.configure("TLabel",           background=DARK, foreground=TEXT, font=("Segoe UI", 10))
    style.configure("TEntry",           fieldbackground=CARD, foreground=TEXT,
                    insertcolor=TEXT, bordercolor=BORDER, relief="flat", padding=6)
    style.configure("TCheckbutton",     background=DARK, foreground=TEXT, font=("Segoe UI", 10))
    style.configure("Accent.TButton",   background=ACCENT, foreground="#ffffff",
                    font=("Segoe UI", 11, "bold"), padding=10, relief="flat")
    style.map("Accent.TButton",         background=[("active", "#6a58e0")])
    style.configure("TScale",           background=DARK, troughcolor=CARD)
    style.configure("Treeview",         background=CARD, fieldbackground=CARD,
                    foreground=TEXT, font=("Segoe UI", 10))
    style.configure("Treeview.Heading", background=BORDER, foreground=MUTED,
                    font=("Segoe UI", 9, "bold"))
    style.map("Treeview",               background=[("selected", ACCENT)])

    # ── Title bar ─────────────────────────────────────────────────────
    title_frame = tk.Frame(root, bg=CARD, pady=12, padx=18)
    title_frame.pack(fill=tk.X)
    tk.Label(title_frame, text="🎯  HR Email Hunter Ultra",
             bg=CARD, fg=ACCENT, font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)
    tk.Label(title_frame, text=f"v{VERSION}",
             bg=CARD, fg=MUTED, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=8)

    # ── Notebook ──────────────────────────────────────────────────────
    nb = ttk.Notebook(root)
    nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 10))

    def card_frame(parent, padx=12, pady=8):
        f = tk.Frame(parent, bg=CARD, bd=0, relief="flat",
                     highlightbackground=BORDER, highlightthickness=1)
        f.pack(fill=tk.X, padx=padx, pady=pady)
        return f

    def label(parent, text, fg=MUTED, font_size=9):
        tk.Label(parent, text=text, bg=parent["bg"],
                 fg=fg, font=("Segoe UI", font_size)).pack(anchor="w", padx=10, pady=(6, 0))

    def entry(parent, var, show=""):
        e = ttk.Entry(parent, textvariable=var, show=show, font=("Segoe UI", 10))
        e.pack(fill=tk.X, padx=10, pady=(2, 8))
        return e

    # ════ TAB 1: SCAN ════════════════════════════════════════════════
    tab_scan = ttk.Frame(nb)
    nb.add(tab_scan, text="  ⚡ Scan  ")
    scan_canvas = tk.Canvas(tab_scan, bg=DARK, highlightthickness=0)
    scan_scroll = ttk.Scrollbar(tab_scan, orient="vertical", command=scan_canvas.yview)
    scan_inner  = tk.Frame(scan_canvas, bg=DARK)
    scan_inner.bind("<Configure>", lambda e: scan_canvas.configure(
        scrollregion=scan_canvas.bbox("all")))
    scan_canvas.create_window((0, 0), window=scan_inner, anchor="nw")
    scan_canvas.configure(yscrollcommand=scan_scroll.set)
    scan_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    scan_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    v_domain   = tk.StringVar()
    v_names    = tk.StringVar()
    v_instagram= tk.StringVar()
    v_tiktok   = tk.StringVar()
    v_hunter   = tk.StringVar()
    v_abstract = tk.StringVar()
    v_workers  = tk.IntVar(value=15)

    cf1 = card_frame(scan_inner)
    tk.Label(cf1, text="🌐  Target domain", bg=CARD, fg=ACCENT,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
    label(cf1, "Company domain (e.g. google.com)")
    entry(cf1, v_domain)
    label(cf1, "Recruiter names — optional (e.g. Jane Doe, John Smith)")
    entry(cf1, v_names)

    cf2 = card_frame(scan_inner)
    tk.Label(cf2, text="📱  Social media handles", bg=CARD, fg=ACCENT,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
    label(cf2, "Instagram handle (optional)")
    entry(cf2, v_instagram)
    label(cf2, "TikTok handle (optional)")
    entry(cf2, v_tiktok)

    cf3 = card_frame(scan_inner)
    tk.Label(cf3, text="🔑  API keys", bg=CARD, fg=ACCENT,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
    label(cf3, "Hunter.io API key (optional)")
    entry(cf3, v_hunter, show="•")
    label(cf3, "AbstractAPI key (optional — extra validation)")
    entry(cf3, v_abstract, show="•")

    cf4 = card_frame(scan_inner)
    tk.Label(cf4, text="⚙️  Options", bg=CARD, fg=ACCENT,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 0))

    opt_row = tk.Frame(cf4, bg=CARD)
    opt_row.pack(fill=tk.X, padx=10, pady=4)
    v_export = tk.BooleanVar(value=True)
    v_report = tk.BooleanVar(value=True)
    ttk.Checkbutton(opt_row, text="Export CSV + JSON", variable=v_export).pack(side=tk.LEFT, padx=8)
    ttk.Checkbutton(opt_row, text="Generate HTML report", variable=v_report).pack(side=tk.LEFT, padx=8)

    worker_row = tk.Frame(cf4, bg=CARD)
    worker_row.pack(fill=tk.X, padx=10, pady=(4, 10))
    tk.Label(worker_row, text="Threads:", bg=CARD, fg=MUTED,
             font=("Segoe UI", 10)).pack(side=tk.LEFT)
    worker_lbl = tk.Label(worker_row, text="15", bg=CARD, fg=ACCENT,
                          font=("Segoe UI", 10, "bold"), width=3)
    worker_lbl.pack(side=tk.RIGHT)

    def on_worker_change(val):
        worker_lbl.config(text=str(int(float(val))))

    ttk.Scale(worker_row, from_=1, to=30, variable=v_workers,
              orient=tk.HORIZONTAL, command=on_worker_change).pack(
              side=tk.LEFT, fill=tk.X, expand=True, padx=8)

    run_btn = ttk.Button(scan_inner, text="▶  Start Scan", style="Accent.TButton")
    run_btn.pack(fill=tk.X, padx=12, pady=(4, 8))

    # ════ TAB 2: LOG ═════════════════════════════════════════════════
    tab_log = ttk.Frame(nb)
    nb.add(tab_log, text="  📟 Log  ")

    log_top = tk.Frame(tab_log, bg=DARK)
    log_top.pack(fill=tk.X, padx=10, pady=(8, 4))
    tk.Label(log_top, text="Live scan log", bg=DARK, fg=MUTED,
             font=("Segoe UI", 10)).pack(side=tk.LEFT)
    clear_btn = tk.Button(log_top, text="Clear", bg=CARD, fg=MUTED,
                          bd=0, font=("Segoe UI", 9), cursor="hand2",
                          command=lambda: log_box.delete("1.0", tk.END))
    clear_btn.pack(side=tk.RIGHT)

    log_box = scrolledtext.ScrolledText(
        tab_log, font=("Consolas", 9), bg="#090c12", fg=TEXT,
        insertbackground=TEXT, bd=0, relief="flat", wrap=tk.WORD)
    log_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
    log_box.tag_config("ok",   foreground=GREEN)
    log_box.tag_config("warn", foreground=YELLOW)
    log_box.tag_config("err",  foreground=RED)
    log_box.tag_config("info", foreground=ACCENT)

    def gui_log(msg):
        tag = ""
        ml  = msg.lower()
        if any(k in ml for k in ("✅", "found", "saved", "ok", "valid")):
            tag = "ok"
        elif any(k in ml for k in ("⚠", "catch-all", "skip", "blocked")):
            tag = "warn"
        elif any(k in ml for k in ("❌", "error", "fail")):
            tag = "err"
        elif any(k in ml for k in ("[1/", "[2/", "[3/", "[4/", "[5/", "[6/", "[7/")):
            tag = "info"

        ts = datetime.now().strftime("%H:%M:%S")
        root.after(0, _append_log, f"{ts}  {msg}\n", tag)

    def _append_log(text, tag):
        log_box.insert(tk.END, text, tag)
        log_box.see(tk.END)

    # ════ TAB 3: RESULTS ═════════════════════════════════════════════
    tab_res = ttk.Frame(nb)
    nb.add(tab_res, text="  📬 Results  ")

    res_top = tk.Frame(tab_res, bg=DARK)
    res_top.pack(fill=tk.X, padx=10, pady=(8, 4))

    metrics_frame = tk.Frame(res_top, bg=DARK)
    metrics_frame.pack(fill=tk.X)

    def metric_card(parent, label_text, value, color=ACCENT):
        f = tk.Frame(parent, bg=CARD, bd=0,
                     highlightbackground=BORDER, highlightthickness=1)
        f.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        tk.Label(f, text=value, bg=CARD, fg=color,
                 font=("Segoe UI", 20, "bold")).pack(pady=(8, 0))
        tk.Label(f, text=label_text, bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).pack(pady=(0, 8))
        return f

    mc_total = metric_card(metrics_frame, "Total found", "—")
    mc_web   = metric_card(metrics_frame, "Website",     "—", GREEN)
    mc_ver   = metric_card(metrics_frame, "Verified",    "—", ACCENT)
    mc_hunt  = metric_card(metrics_frame, "Hunter",      "—", YELLOW)

    def update_metrics(results, website_emails):
        total  = len(results)
        web    = len(website_emails)
        ver    = sum(1 for r in results if r.get("method") in ("O365","SMTP"))
        hunt   = sum(1 for r in results if r.get("method") == "Hunter")
        for card, val in [(mc_total, total),(mc_web, web),(mc_ver, ver),(mc_hunt, hunt)]:
            for w in card.winfo_children():
                if w.cget("font") and "20" in str(w.cget("font")):
                    w.config(text=str(val))

    # Filter + sort bar
    filter_bar = tk.Frame(tab_res, bg=DARK)
    filter_bar.pack(fill=tk.X, padx=10, pady=(6, 2))
    tk.Label(filter_bar, text="Filter:", bg=DARK, fg=MUTED,
             font=("Segoe UI", 9)).pack(side=tk.LEFT)
    v_filter = tk.StringVar(value="All")
    for opt in ("All", "website", "o365", "smtp", "hunter", "social"):
        rb = tk.Radiobutton(filter_bar, text=opt, variable=v_filter,
                            value=opt, bg=DARK, fg=MUTED, selectcolor=DARK,
                            activebackground=DARK, activeforeground=ACCENT,
                            font=("Segoe UI", 9))
        rb.pack(side=tk.LEFT, padx=4)

    cols = ("Email", "Score", "Method", "Sources")
    tree = ttk.Treeview(tab_res, columns=cols, show="headings", height=16)
    for c, w in zip(cols, (320, 60, 80, 160)):
        tree.heading(c, text=c)
        tree.column(c, width=w, minwidth=w)
    tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 4))

    res_scroll = ttk.Scrollbar(tab_res, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=res_scroll.set)

    tree.tag_configure("high",   background="#0a2415", foreground=GREEN)
    tree.tag_configure("medium", background="#1a1600", foreground=YELLOW)
    tree.tag_configure("low",    background="#1a0a0a", foreground=RED)

    _all_results_cache = []

    def populate_tree(results):
        nonlocal _all_results_cache
        _all_results_cache = results
        refresh_tree()

    def refresh_tree():
        for row in tree.get_children():
            tree.delete(row)
        flt = v_filter.get()
        for r in sorted(_all_results_cache,
                        key=lambda x: x.get("score", 0), reverse=True):
            srcs = r.get("sources", [])
            if flt != "All" and flt not in srcs:
                continue
            sc  = r.get("score", 0)
            tag = "high" if sc >= 70 else "medium" if sc >= 40 else "low"
            tree.insert("", tk.END, values=(
                r.get("email", ""),
                sc,
                r.get("method", "?"),
                ", ".join(srcs)
            ), tags=(tag,))

    v_filter.trace_add("write", lambda *_: refresh_tree())

    copy_btn = tk.Button(tab_res, text="📋  Copy selected email",
                         bg=CARD, fg=TEXT, bd=0, font=("Segoe UI", 9),
                         cursor="hand2",
                         command=lambda: (
                             root.clipboard_clear(),
                             root.clipboard_append(
                                 tree.item(tree.selection()[0], "values")[0]
                                 if tree.selection() else ""
                             )
                         ))
    copy_btn.pack(pady=(2, 6))

    # ════ TAB 4: EXPORT ══════════════════════════════════════════════
    tab_exp = ttk.Frame(nb)
    nb.add(tab_exp, text="  💾 Export  ")

    ef = card_frame(tab_exp, pady=10)
    tk.Label(ef, text="Generated CLI command", bg=CARD, fg=ACCENT,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 4))

    cli_var = tk.StringVar()
    cli_entry = tk.Entry(ef, textvariable=cli_var, font=("Consolas", 9),
                         bg="#090c12", fg=GREEN, bd=0, state="readonly",
                         readonlybackground="#090c12")
    cli_entry.pack(fill=tk.X, padx=10, pady=(0, 10))

    def copy_cli():
        root.clipboard_clear()
        root.clipboard_append(cli_var.get())

    tk.Button(ef, text="📋 Copy command", bg=BORDER, fg=TEXT, bd=0,
              font=("Segoe UI", 9), cursor="hand2",
              command=copy_cli).pack(anchor="w", padx=10, pady=(0, 10))

    ef2 = card_frame(tab_exp, pady=10)
    tk.Label(ef2, text="Download results", bg=CARD, fg=ACCENT,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 4))

    _scan_results = {"data": []}

    def save_csv():
        if not _scan_results["data"]:
            messagebox.showinfo("No data", "Run a scan first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["email","score","method","sources"])
            w.writeheader()
            for r in _scan_results["data"]:
                w.writerow({"email": r["email"], "score": r.get("score",0),
                            "method": r.get("method","?"),
                            "sources": ",".join(r.get("sources",[]))})
        messagebox.showinfo("Saved", f"CSV saved:\n{path}")

    def save_json():
        if not _scan_results["data"]:
            messagebox.showinfo("No data", "Run a scan first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path: return
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"results": _scan_results["data"]}, f, indent=2)
        messagebox.showinfo("Saved", f"JSON saved:\n{path}")

    btn_row = tk.Frame(ef2, bg=CARD)
    btn_row.pack(fill=tk.X, padx=10, pady=(0, 10))
    for txt, cmd in [("💾 Save CSV", save_csv), ("{ } Save JSON", save_json)]:
        tk.Button(btn_row, text=txt, bg=BORDER, fg=TEXT, bd=0,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=cmd).pack(side=tk.LEFT, padx=(0, 8), pady=4)

    # ── CLI command builder ───────────────────────────────────────────
    def build_cli_cmd():
        d  = v_domain.get().strip()
        k  = v_hunter.get().strip()
        n  = v_names.get().strip()
        ig = v_instagram.get().strip()
        tt = v_tiktok.get().strip()
        w  = v_workers.get()
        cmd = f"python hr_email_hunter_ultra.py -d {d}" if d else "python hr_email_hunter_ultra.py -d <domain>"
        if k:  cmd += f" -k {k}"
        if n:  cmd += f' -n "{n}"'
        if ig: cmd += f" --instagram {ig}"
        if tt: cmd += f" --tiktok {tt}"
        if w != 15: cmd += f" -w {w}"
        if v_export.get(): cmd += " --export"
        if v_report.get(): cmd += " --report"
        cli_var.set(cmd)

    for var in (v_domain, v_names, v_instagram, v_tiktok, v_hunter, v_abstract):
        var.trace_add("write", lambda *_: build_cli_cmd())
    v_workers.trace_add("write", lambda *_: build_cli_cmd())
    v_export.trace_add("write", lambda *_: build_cli_cmd())
    v_report.trace_add("write", lambda *_: build_cli_cmd())

    # ── Run scan ──────────────────────────────────────────────────────
    def do_scan():
        domain = v_domain.get().strip()
        if not domain:
            messagebox.showwarning("Domain required",
                                   "Please enter a company domain first.")
            return

        run_btn.config(state="disabled", text="⏳  Scanning…")
        nb.select(tab_log)
        log_box.delete("1.0", tk.END)
        _scan_results["data"] = []

        class Args:
            pass

        a = Args()
        a.domain       = domain
        a.names        = [n.strip() for n in v_names.get().split(",")
                         if n.strip()] or None
        a.hunter_key   = v_hunter.get().strip() or None
        a.abstract_key = v_abstract.get().strip() or None
        a.instagram    = v_instagram.get().strip() or None
        a.tiktok       = v_tiktok.get().strip() or None
        a.workers      = v_workers.get()
        a.export       = v_export.get()
        a.report       = v_report.get()

        def thread_run():
            try:
                results, social, hunter_data, web_emails = run(a, log_cb=gui_log)
                _scan_results["data"] = results
                root.after(0, on_scan_done, results, web_emails)
            except Exception as e:
                gui_log(f"❌ Fatal error: {e}")
                root.after(0, run_btn.config, {"state": "normal", "text": "▶  Start Scan"})

        threading.Thread(target=thread_run, daemon=True).start()

    def on_scan_done(results, web_emails):
        update_metrics(results, web_emails)
        populate_tree(results)
        run_btn.config(state="normal", text="▶  Start Scan")
        nb.select(tab_res)
        gui_log(f"\n✅ Scan complete — {len(results)} emails found.")

    run_btn.config(command=do_scan)
    build_cli_cmd()

    root.mainloop()

# ══════════════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hr_email_hunter_ultra",
        description=f"HR Email Hunter ULTRA v{VERSION} — find HR emails for job applications.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python hr_email_hunter_ultra.py --gui
  python hr_email_hunter_ultra.py -d google.com
  python hr_email_hunter_ultra.py -d apple.com -k HUNTER_KEY -n "Jane Doe" --report
  python hr_email_hunter_ultra.py -d shopify.com --instagram shopify --tiktok shopify
  python hr_email_hunter_ultra.py -d microsoft.com -k KEY -n "Jane Doe" -w 20 --report --export
        """
    )
    p.add_argument("--gui",          action="store_true",  help="Launch graphical interface")
    p.add_argument("-d","--domain",  default=None,         help="Company domain (e.g. google.com)")
    p.add_argument("-k","--hunter-key",  default=None,     help="Hunter.io API key (optional)")
    p.add_argument("-a","--abstract-key",default=None,     help="AbstractAPI key (optional)")
    p.add_argument("-n","--names",   nargs="+",            help='Recruiter names e.g. "Jane Doe"')
    p.add_argument("--instagram",    default=None,         help="Instagram handle")
    p.add_argument("--tiktok",       default=None,         help="TikTok handle")
    p.add_argument("-w","--workers", type=int, default=15, help="Thread count (default 15)")
    p.add_argument("--export",       action="store_true",  help="Save CSV + JSON")
    p.add_argument("--report",       action="store_true",  help="Generate HTML report")
    return p


def main():
    parser = build_parser()

    if len(sys.argv) == 1:
        # No args at all → launch GUI
        launch_gui()
        return

    args = parser.parse_args()

    if args.gui:
        launch_gui()
        return

    if not args.domain:
        parser.print_help()
        print(f"\n👆 Tip: run with --gui for the graphical interface, or -d yourdomain.com\n")
        sys.exit(0)

    # Map dashes to underscores for attribute access
    if hasattr(args, 'hunter_key') is False:
        args.hunter_key = getattr(args, 'hunter-key', None)

    run(args)


if __name__ == "__main__":
    main()
