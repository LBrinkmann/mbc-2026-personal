#!/usr/bin/env python3
"""Build the MBC 2026 personal site (index.html + people.html).

Reads:
  - /tmp/mbc_program.html  (Squarespace program HTML, single-decoded entities)
  - /Users/brinkmann/repros/research/people/*.md  (45 speaker profiles)
  - /Users/brinkmann/repros/research/people/_mbc_speakers_index.md  (roster)

Writes (next to this script):
  - index.html        (program timeline, Mon/Tue/Wed tabs)
  - people.html       (single list of all 45 profiled speakers)
  - manifest.webmanifest
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESEARCH = Path("/Users/brinkmann/repros/research")
PEOPLE_DIR = RESEARCH / "people"
PROGRAM_HTML = Path("/tmp/mbc_program.html")
INDEX_MD = PEOPLE_DIR / "_mbc_speakers_index.md"


# --------------------------------------------------------------------------- #
# Tier markers — hand-curated match scoring                                    #
# --------------------------------------------------------------------------- #
# ★ "priority" — closest fit / must-talk
# ● "strong"   — substantive multi-project overlap
# (slug omitted) → no marker
TIERS: dict[str, str] = {
    # Priority — attending 2026
    "tom-griffiths": "priority",
    "marcel-binz": "priority",
    "nori-jacoby": "priority",
    "danica-dillion": "priority",
    "christopher-summerfield": "priority",
    "indira-sen": "priority",
    "kinga-makovi": "priority",
    "eric-schulz": "priority",
    "saeedeh-mohammadi": "priority",
    "thore-graepel": "priority",
    "raja-marjieh": "priority",  # invited but cannot attend; keep marker
    "raphael-koster": "priority",  # not at MBC; bridge via Summerfield
    # Priority — 2024 alumni, not attending (kept for cross-conf reference)
    "moritz-hardt": "priority",
    "krishna-gummadi": "priority",
    "joel-leibo": "priority",
    "james-evans": "priority",
    "jean-francois-bonnefon": "priority",
    # Strong — attending 2026
    "jessica-thompson": "strong",
    "andrea-baronchelli": "strong",
    "yannik-keller": "strong",
    "yaomin-jiang": "strong",
    # Strong — 2024 alumni
    "meeyoung-cha": "strong",
}


def tier_of(slug: str) -> str:
    return TIERS.get(slug, "")


# --------------------------------------------------------------------------- #
# Name normalisation                                                          #
# --------------------------------------------------------------------------- #

def slugify(name: str) -> str:
    """Match the existing kebab-case file slugs.

    Strip accents, lowercase, drop non-alphanumerics except '-'.
    """
    n = unicodedata.normalize("NFKD", name)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    n = n.lower()
    n = re.sub(r"[^a-z0-9]+", "-", n).strip("-")
    return n


def norm_name(name: str) -> str:
    """Loose key for joining program names to bio names."""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    n = n.lower()
    n = n.replace("&", "and")
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


# --------------------------------------------------------------------------- #
# Parse program HTML                                                          #
# --------------------------------------------------------------------------- #

def load_program() -> str:
    raw = PROGRAM_HTML.read_text(encoding="utf-8", errors="replace")
    # Squarespace code-block content is double-encoded; unescape twice.
    decoded = html_lib.unescape(html_lib.unescape(raw))
    return decoded


def parse_program(decoded: str) -> dict:
    """Return {day_key: [slot, ...]} for 'mon', 'tue', 'wed'.

    'wed' is the workshop programme which we encode inline (Squarespace doesn't
    include it in the main program HTML).
    """
    days = {"mon": [], "tue": [], "wed": []}

    # Cut at day h2 markers.
    day1_idx = decoded.find("Day 1 | May 18")
    day2_idx = decoded.find("Day 2 | May 19")
    if day1_idx < 0 or day2_idx < 0:
        raise SystemExit("could not find Day 1 / Day 2 headers in program HTML")

    # Day 2 ends roughly at the closing of the program-wrapper.
    end_idx = decoded.find('</body>', day2_idx)
    if end_idx < 0:
        end_idx = len(decoded)

    days["mon"] = parse_day_slots(decoded[day1_idx:day2_idx])
    days["tue"] = parse_day_slots(decoded[day2_idx:end_idx])
    days["wed"] = workshop_slots()
    return days


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


# Slot regex captures the time-line plus everything until the next slot
# (or end of input).
SLOT_RE = re.compile(
    r'<div[^>]*class="program-slot"[^>]*>(?P<body>.*?)'
    r'(?=<div[^>]*class="program-slot"|</div>\s*</body>|</body>|\Z)',
    re.S,
)
TIME_RE = re.compile(
    r'<span[^>]*class="time"[^>]*>(?P<t>[^<]+)</span>'
    r'\s*<span[^>]*class="label"[^>]*>(?P<l>[^<]+)</span>',
    re.S,
)
# Talk-entry pattern (most flexible — content varies a lot)
TALK_RE = re.compile(
    r'<div[^>]*class="talk-entry"[^>]*>(?P<body>.*?)</div>\s*</div>',
    re.S,
)


def parse_day_slots(chunk: str) -> list[dict]:
    slots = []
    for m in SLOT_RE.finditer(chunk):
        body = m.group("body")
        tm = TIME_RE.search(body)
        if not tm:
            # Sometimes label is a <div class="label"> not <span>; loosen.
            tm = re.search(
                r'<span[^>]*class="time"[^>]*>(?P<t>[^<]+)</span>'
                r'.*?class="label"[^>]*>(?P<l>[^<]+)<',
                body, re.S,
            )
        if not tm:
            continue
        time_range = tm.group("t").strip()
        label = tm.group("l").strip()
        # Normalize whitespace
        label = re.sub(r"\s+", " ", label)
        slot = {
            "time": time_range,
            "title": label,
            "kind": classify_slot(label),
            "items": [],
        }
        # Parse talk-entries within this slot
        for tm2 in TALK_RE.finditer(body):
            t = tm2.group("body")
            item = parse_talk_entry(t)
            if item:
                slot["items"].append(item)
        # Parse session-expand (lightning / posters lists on Day 2)
        for mm in re.finditer(
            r'<details[^>]*class="session-expand"[^>]*>(.*?)</details>', body, re.S
        ):
            for li in re.finditer(
                r'<li>\s*<strong>([^<]+)</strong>\s*<br\s*/?>\s*'
                r'<span[^>]*class="compact-title"[^>]*>([^<]+)</span>',
                mm.group(1), re.S,
            ):
                slot["items"].append({
                    "speaker": li.group(1).strip(),
                    "aff": "",
                    "title": li.group(2).strip(),
                    "abstract": "",
                })
        # Editor's panel inline list
        for ep in re.finditer(
            r'<div[^>]*class="editor-panel-speakers"[^>]*>(.*?)</div>',
            body, re.S,
        ):
            for line in re.split(r"<br\s*/?>", ep.group(1)):
                txt = _strip_tags(line).strip()
                if txt:
                    # "Name (Affiliation)"
                    nm = re.match(r"([^(]+?)\s*\(([^)]+)\)", txt)
                    if nm:
                        slot["items"].append({
                            "speaker": nm.group(1).strip(),
                            "aff": nm.group(2).strip(),
                            "title": "",
                            "abstract": "",
                        })
                    else:
                        slot["items"].append({
                            "speaker": txt, "aff": "", "title": "", "abstract": "",
                        })
        slots.append(slot)
    return slots


def parse_talk_entry(body: str) -> dict | None:
    tt = re.search(r'class="talk-time"[^>]*>([^<]+)</span>', body)
    talk_time = tt.group(1).strip() if tt else ""

    sp = re.search(r'class="talk-speaker"[^>]*>([^<]+)', body)
    speaker = ""
    aff = ""
    if sp:
        text = sp.group(1).strip()
        # "Name (Affiliation)"
        nm = re.match(r"(.+?)\s*\((.+)\)\s*$", text)
        if nm:
            speaker = nm.group(1).strip()
            aff = nm.group(2).strip()
        else:
            speaker = text

    tt2 = re.search(r'class="talk-title"[^>]*>([^<]+)', body)
    title = tt2.group(1).strip() if tt2 else ""

    abstract = ""
    ab = re.search(r'class="talk-abstract"[^>]*>.*?<p[^>]*>(.*?)</p>', body, re.S)
    if ab:
        abstract = _strip_tags(ab.group(1)).strip()

    # Panel-discussion rows have a label inside the talk-entry but no speaker.
    label = re.search(r'class="label"[^>]*>([^<]+)', body)
    if not speaker and label:
        return {
            "speaker": "",
            "aff": "",
            "title": label.group(1).strip(),
            "abstract": "",
            "time": talk_time,
            "is_panel": True,
        }
    if not speaker and not title:
        return None
    return {
        "speaker": speaker,
        "aff": aff,
        "title": title,
        "abstract": abstract,
        "time": talk_time,
    }


def classify_slot(label: str) -> str:
    L = label.lower()
    if "coffee" in L or "break" in L:
        return "break"
    if "lunch" in L:
        return "lunch"
    if "dinner" in L or "bbq" in L or "reception" in L:
        return "dinner"
    if "poster" in L:
        return "poster"
    if "lightning" in L:
        return "lightning"
    if "panel" in L:
        return "panel"
    if "welcome" in L or "opening" in L or "closing" in L or "remarks" in L:
        return "framing"
    if "registration" in L:
        return "framing"
    if "session" in L or "editor" in L or "keynote" in L:
        return "session"
    return "other"


# --------------------------------------------------------------------------- #
# Day 3 — Behavioral Clones Workshop (re-uses the reference site's TIMELINE)  #
# --------------------------------------------------------------------------- #

def workshop_slots() -> list[dict]:
    """Hard-coded from the reference site's TIMELINE (matches workshop page)."""
    raw = [
        ("09:00 – 09:20", "Welcome by Levin & Dirk", "framing", []),
        ("09:20 – 09:50", "Invited talk — Danica Dillion", "session",
         [("Danica Dillion", "Complexity Science Hub / OSU", "", "")]),
        ("09:50 – 10:10", "Lightning talks (block A)", "lightning", [
            ("Indira Sen", "University of Mannheim",
             "Evaluating Generative Social Simulations", ""),
            ("Jelena Meyer", "MPIB",
             "When Reliability Misleads: Psychometric Scale Directionality "
             "Inflates Apparent Consistency in LLM Scale Responses", ""),
            ("Daniel Relihan", "USC",
             "Where Do LLM Outputs Fall Relative to Human Severity "
             "Assessments? A Distributional Calibration Framework", ""),
            ("Sharif Kazemi", "World Bank Group",
             "Machine-Augmented Social Simulation (MASS)", ""),
        ]),
        ("10:10 – 10:30", "Break", "break", []),
        ("10:30 – 11:45", "Contributed talks (block 1)", "session", [
            ("Felipe Valencia-Clavijo", "Dataplicada",
             "Anchoring as a Behavioral Clone of Human Judgment in LLMs", ""),
            ("Valentin Kriegmair", "MPIB",
             "Machine Individuality: Multi-Level Variance Partitioning "
             "Reveals Stable Behavioral Idiosyncrasies in LLMs", ""),
            ("Faezeh Fadaei", "University College Dublin",
             "Gender Dynamics and Homophily in a Social Network of LLM Agents",
             ""),
            ("Louis Schiekiera", "Humboldt-Universität zu Berlin",
             "Aligning Behavioral and Hidden-State Semantic Geometry in LLMs",
             ""),
        ]),
        ("11:45 – 12:05", "Break", "break", []),
        ("12:05 – 12:35", "Invited talk — Marcel Binz", "session",
         [("Marcel Binz", "Helmholtz Munich", "", "")]),
        ("12:35 – 13:35", "Lunch", "lunch", []),
        ("13:35 – 14:10", "Lightning talks (block B)", "lightning", [
            ("Jessica Thompson", "University of Oxford",
             "Using behavioural cloning to accelerate human language learning",
             ""),
            ("Leonel Aguilar", "ETH Zürich",
             "Beyond the Vessel: Ground-Truth Behavioural Traces for "
             "Foundational Models of Human Behaviour", ""),
            ("Saeedeh Mohammadi", "University College Dublin",
             "Bickering Machine: AI feedback enhances community-based "
             "content moderation", ""),
            ("Taisiia Tikhomirova", "MPIB",
             "Where meaning lives: Layer-wise accessibility of "
             "psycholinguistic features", ""),
            ("Jan Pfänder", "Eawag",
             "Predicting the effects of a megastudy with behavioral clones",
             ""),
        ]),
        ("14:10 – 14:40", "Invited talk — Nori Jacoby", "session",
         [("Nori Jacoby", "Cornell University", "", "")]),
        ("14:40 – 15:00", "Break", "break", []),
        ("15:00 – 16:15", "Contributed talks (block 2)", "session", [
            ("Kristin Witte", "Helmholtz Munich",
             "Evaluating Emotionally Loaded User-LLM Interactions Across "
             "Long Horizons", ""),
            ("Maxime Saxena", "University College London",
             "Reasoning Induced Bubbles in LLM Financial Markets", ""),
            ("Yannik Keller", "MPIB",
             "Improving Long-Run Group Welfare Through AI-Managed "
             "Sanctioning Institutions", ""),
            ("Yaomin Jiang", "MPIB",
             "Emergent Strategic Communication in Goal-Optimized Language "
             "Agents", ""),
        ]),
        ("16:15 – 16:25", "Break", "break", []),
        ("16:25 – 17:45", "Poster session", "poster", []),
        ("17:45 – 18:15", "Panel — Open Questions and the Future",
         "panel", []),
        ("18:15 – 23:59", "BBQ on the institute terrace", "dinner", []),
    ]
    return [
        {
            "time": t, "title": title, "kind": k,
            "items": [
                {"speaker": s, "aff": a, "title": tt, "abstract": ab}
                for (s, a, tt, ab) in items
            ],
        }
        for (t, title, k, items) in raw
    ]


# --------------------------------------------------------------------------- #
# Parse people/*.md profiles                                                  #
# --------------------------------------------------------------------------- #

SECTION_HEADERS = [
    "Research summary",
    "Active research threads",
    "Key recent papers",
    "Methodological signature",
    "Collaborators of note",
    "Connections to Levin's work",
    "Notes",
    "MBC talk",
]


def split_md_block(body: str) -> dict:
    """Detect bulleted vs paragraph body, preserving any lead-in prose.

    Returns {'kind': 'bullets'|'paragraph'|'mixed',
             'lead': '<markdown prose before the first bullet>',
             'items': [...]}.
    A `### subheader` inside `lead` is stripped (acts as a section divider
    between lead and bullets in source markdown).
    """
    raw = body.strip()
    raw_lines = raw.splitlines()
    # find first bullet line
    first_bullet = next(
        (i for i, ln in enumerate(raw_lines) if re.match(r"^\s*-\s+", ln)),
        None,
    )
    if first_bullet is None:
        return {"kind": "paragraph", "lead": "", "items": [raw]}

    lead_raw = "\n".join(raw_lines[:first_bullet]).strip()
    # Drop trailing H3 sub-header that just labels the bullet list
    # (e.g. "### Project-by-project") so the rendered lead stays prose-only.
    lead_lines = [ln for ln in lead_raw.splitlines()
                  if not re.match(r"^\s*###\s+", ln)]
    lead = "\n".join(lead_lines).strip()

    bullets = bulletify("\n".join(raw_lines[first_bullet:]))
    kind = "mixed" if lead else "bullets"
    return {"kind": kind, "lead": lead, "items": bullets}


def parse_person(md_path: Path) -> dict:
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Title
    name = ""
    if lines and lines[0].startswith("# "):
        name = lines[0][2:].strip()
    # Bullet-style metadata block at the top: `- **Affiliation:** ...`
    meta = {}
    for line in lines[1:]:
        if line.startswith("## "):
            break
        m = re.match(r"^\s*-\s*\*\*([^:]+):\*\*\s*(.+)$", line)
        if m:
            key = m.group(1).strip().lower()
            meta[key] = m.group(2).strip()

    # Section bodies
    sections = parse_sections(text)
    talk_title = ""
    talk_takeaway = ""
    talk_date = ""
    mbc_block = sections.get("MBC talk", "")
    for ln in mbc_block.splitlines():
        m = re.match(r"^\s*-\s*\*\*Title:\*\*\s*(.+)$", ln)
        if m:
            talk_title = m.group(1).strip()
        m = re.match(r"^\s*-\s*\*\*One-line takeaway:\*\*\s*(.+)$", ln)
        if m:
            talk_takeaway = m.group(1).strip()
        m = re.match(r"^\s*-\s*\*\*Date:\*\*\s*(.+)$", ln)
        if m:
            talk_date = m.group(1).strip()

    threads = bulletify(sections.get("Active research threads", ""))
    papers = bulletify(sections.get("Key recent papers", ""))

    connections_block = split_md_block(
        sections.get("Connections to Levin's work", "")
    )
    return {
        "slug": md_path.stem,
        "name": name,
        "name_key": norm_name(name),
        "affiliation": meta.get("affiliation", ""),
        "role": meta.get("role", ""),
        "topics": meta.get("topics", ""),
        "homepage": meta.get("homepage", ""),
        "scholar": meta.get("scholar / orcid", "") or meta.get("scholar", ""),
        "talk_title": talk_title,
        "talk_takeaway": talk_takeaway,
        "talk_date": talk_date,
        "summary": sections.get("Research summary", "").strip(),
        "threads": threads,
        "papers": papers,
        "methods": sections.get("Methodological signature", "").strip(),
        "collaborators": bulletify(sections.get("Collaborators of note", "")),
        "connections_kind": connections_block["kind"],
        "connections_lead": connections_block.get("lead", ""),
        "connections_items": connections_block["items"],
        "notes": sections.get("Notes", "").strip(),
    }


def parse_sections(text: str) -> dict[str, str]:
    """Split a md file by `## <header>` into a dict of body strings."""
    out: dict[str, str] = {}
    parts = re.split(r"^##\s+(.+)$", text, flags=re.M)
    # parts[0] = preface; then header, body, header, body, ...
    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        out[header] = body.strip()
    return out


def bulletify(body: str) -> list[str]:
    """Extract top-level `- ...` bullets, joining continuation lines."""
    bullets = []
    cur = None
    for line in body.splitlines():
        if re.match(r"^\s*-\s+", line):
            if cur is not None:
                bullets.append(cur.strip())
            cur = re.sub(r"^\s*-\s+", "", line)
        elif cur is not None and line.strip() and not line.startswith("#"):
            cur += " " + line.strip()
        else:
            if cur is not None:
                bullets.append(cur.strip())
                cur = None
    if cur is not None:
        bullets.append(cur.strip())
    return bullets


def load_people() -> list[dict]:
    people = []
    for p in sorted(PEOPLE_DIR.glob("*.md")):
        if p.name in ("README.md", "_mbc_speakers_index.md"):
            continue
        try:
            people.append(parse_person(p))
        except Exception as e:
            print(f"warn: failed to parse {p}: {e}", file=sys.stderr)
    return people


# --------------------------------------------------------------------------- #
# Parse the index for day-badges                                              #
# --------------------------------------------------------------------------- #

INDEX_ROW_RE = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
)


def load_index() -> dict[str, dict]:
    """Map name_key -> {date, talk_title, affiliation, day_badge}."""
    out: dict[str, dict] = {}
    if not INDEX_MD.exists():
        return out
    for ln in INDEX_MD.read_text().splitlines():
        m = INDEX_ROW_RE.match(ln)
        if not m:
            continue
        date, name, aff, title = (s.strip() for s in m.groups())
        if name.lower() == "speaker":
            continue
        badge = ""
        if date == "2026-05-18":
            badge = "Mon"
        elif date == "2026-05-19":
            badge = "Tue"
        elif date == "2026-05-20":
            badge = "Wed"
        elif date.startswith("2024-"):
            badge = "2024"
        out[norm_name(name)] = {
            "date": date,
            "talk_title": title,
            "affiliation": aff,
            "day_badge": badge,
        }
    return out


# --------------------------------------------------------------------------- #
# CSS + JS shared by both pages                                               #
# --------------------------------------------------------------------------- #

CSS_VARS = """
  :root {
    --fg: #1a1a1a; --mute: #6a6a6a; --bg: #fafaf8; --card: #fff;
    --border: #e3e3e0; --accent: #2a5da8; --accent-soft: #eaf0fa;
    --now-bg: #fff4d4; --now-border: #d4a226; --now-fg: #6e5210;
    --past: #b5b5b5;
    /* kind colors */
    --session-bg: #fff; --session-fg: #1a1a1a;
    --lightning-bg: #fff2e8; --lightning-fg: #8a3d12;
    --panel-bg: #efe6f5; --panel-fg: #4a2768;
    --poster-bg: #e8f3e5; --poster-fg: #2c5a23;
    --break-bg: #f4f1ea; --break-fg: #6e5210;
    --lunch-bg: #fef6db; --lunch-fg: #6e5210;
    --dinner-bg: #fbe5e0; --dinner-fg: #802b1d;
    --framing-bg: #eef0f4; --framing-fg: #2a3a55;
  }
"""

CSS_BASE = """
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--fg);
    font-size: 16px; line-height: 1.5;
    padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);
  }
  .wrap { max-width: 720px; margin: 0 auto; padding: 20px 16px 64px; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }

  .hero h1 { font-size: 24px; margin: 0 0 4px; line-height: 1.25; }
  .hero .meta { color: var(--mute); font-size: 14px; margin-bottom: 16px; }
  .navbar { display: flex; gap: 6px; margin-bottom: 12px; }
  .navbar a {
    flex: 1; text-align: center;
    padding: 8px 12px; border-radius: 8px;
    border: 1px solid var(--border); background: var(--card);
    color: var(--fg); font-size: 14px; font-weight: 600;
  }
  .navbar a.active {
    background: var(--accent); color: #fff; border-color: var(--accent);
  }

  .status {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px; margin: 0 0 16px;
    display: flex; align-items: center; gap: 12px; min-height: 64px;
  }
  .status.live { background: var(--now-bg); border-color: var(--now-border); color: var(--now-fg); }
  .status .dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; background: var(--mute); }
  .status.live .dot { background: var(--now-border); animation: pulse 1.6s infinite ease-in-out; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
  .status .text { flex: 1; min-width: 0; }
  .status .text .label { font-size: 11px; letter-spacing: 0.7px; text-transform: uppercase; font-weight: 600; color: var(--mute); }
  .status.live .text .label { color: var(--now-border); }
  .status .text .now { font-size: 16px; font-weight: 600; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; }
  .status .text .next { font-size: 13px; color: var(--mute); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; }
  .status.live .text .next { color: var(--now-fg); opacity: 0.75; }

  .toolbar { position: sticky; top: 0; background: var(--bg); z-index: 10;
    padding: 10px 0 8px; margin: 0 -16px 6px; padding-left: 16px; padding-right: 16px;
  }
  .toolbar input { width: 100%; padding: 11px 14px; font: inherit; border: 1px solid var(--border); border-radius: 8px; background: var(--card); outline: none; -webkit-appearance: none; }
  .toolbar input:focus { border-color: var(--accent); }
  .pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .pill {
    padding: 5px 11px; font-size: 13px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--card); color: var(--mute);
    cursor: pointer; font-family: inherit;
  }
  .pill.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .actions { margin-top: 6px; display: flex; gap: 10px; align-items: center; font-size: 13px; color: var(--mute); }
  .actions .count { margin-left: auto; }

  footer { color: var(--mute); font-size: 13px; margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--border); }
  footer a { color: var(--accent); }

  @media (min-width: 600px) {
    .hero h1 { font-size: 30px; }
    body { font-size: 17px; }
  }
"""

CSS_PROGRAM = """
  .timeline { list-style: none; padding: 0; margin: 8px 0 0; }
  .slot { border: 1px solid var(--border); border-radius: 10px; margin-bottom: 8px; background: var(--card); overflow: hidden; }
  .slot.hidden { display: none; }
  .slot.now { box-shadow: 0 1px 4px rgba(0,0,0,0.05); border-color: var(--now-border); background: var(--now-bg); }
  .slot.past { opacity: 0.55; }
  .slot.past .summary .what, .slot.past summary .what { color: var(--past); }

  /* kind-coding (subtle backgrounds, only when not now) */
  .slot.kind-lightning:not(.now) { background: var(--lightning-bg); }
  .slot.kind-lightning:not(.now) .what { color: var(--lightning-fg); }
  .slot.kind-panel:not(.now) { background: var(--panel-bg); }
  .slot.kind-panel:not(.now) .what { color: var(--panel-fg); }
  .slot.kind-poster:not(.now) { background: var(--poster-bg); }
  .slot.kind-poster:not(.now) .what { color: var(--poster-fg); }
  .slot.kind-break:not(.now) { background: var(--break-bg); }
  .slot.kind-break:not(.now) .what { color: var(--break-fg); }
  .slot.kind-lunch:not(.now) { background: var(--lunch-bg); }
  .slot.kind-lunch:not(.now) .what { color: var(--lunch-fg); }
  .slot.kind-dinner:not(.now) { background: var(--dinner-bg); }
  .slot.kind-dinner:not(.now) .what { color: var(--dinner-fg); }
  .slot.kind-framing:not(.now) { background: var(--framing-bg); }
  .slot.kind-framing:not(.now) .what { color: var(--framing-fg); }

  .slot details > summary, .slot .summary {
    list-style: none; cursor: pointer; padding: 12px 14px;
    display: flex; gap: 14px; align-items: center; user-select: none;
  }
  .slot details > summary::-webkit-details-marker { display: none; }
  .slot .summary { cursor: default; }
  .slot .time { flex-shrink: 0; width: 110px; font-size: 13px; color: var(--mute); font-variant-numeric: tabular-nums; }
  .slot.now .time { color: var(--now-fg); font-weight: 600; }
  .slot .what { flex: 1; font-size: 15px; line-height: 1.35; font-weight: 600; }
  .slot .chevron { flex-shrink: 0; color: var(--mute); font-size: 14px; transition: transform 0.18s; }
  .slot details[open] > summary .chevron { transform: rotate(90deg); }

  .slot .body { padding: 0 14px 12px 14px; border-top: 1px solid rgba(0,0,0,0.06); }
  .slot .body .item { padding: 10px 0; border-bottom: 1px solid rgba(0,0,0,0.05); }
  .slot .body .item:last-child { border-bottom: none; }
  .slot .body .item .head-line { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; }
  .slot .body .item .tier { font-size: 13px; line-height: 1; margin-right: 4px; vertical-align: middle; }
  .slot .body .item .tier-priority { color: #d4a226; }
  .slot .body .item .tier-strong   { color: var(--accent); font-size: 9px; }
  .slot .body .item .time-mini { font-size: 12px; color: var(--mute); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .slot .body .item .name { font-weight: 600; font-size: 14px; }
  .slot .body .item .name a { color: var(--accent); }
  .slot .body .item .aff { color: var(--mute); font-size: 13px; }
  .slot .body .item .title { font-size: 14px; color: #2a2a2a; margin-top: 3px; }
  .slot .body .item.panel-row .title { font-style: italic; color: var(--panel-fg); }
"""

CSS_PEOPLE = """
  .roster { list-style: none; padding: 0; margin: 8px 0 0; }
  .card { border: 1px solid var(--border); border-radius: 10px; margin-bottom: 8px; background: var(--card); overflow: hidden; scroll-margin-top: 80px; }
  .card.hidden { display: none; }
  .card.target { box-shadow: 0 0 0 2px var(--accent); }
  .card details > summary { list-style: none; cursor: pointer; padding: 12px 14px; user-select: none; }
  .card details > summary::-webkit-details-marker { display: none; }
  .card .head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  .card .name { font-weight: 600; font-size: 15px; }
  .card .tier {
    font-size: 15px; line-height: 1; flex-shrink: 0;
    margin-right: -4px;
  }
  .card .tier-priority { color: #d4a226; }
  .card .tier-strong   { color: var(--accent); font-size: 9px; transform: translateY(-2px); }
  .card[data-tier="priority"] { border-color: #e4c074; box-shadow: 0 0 0 1px rgba(212,162,38,0.15) inset; }
  .card[data-tier="strong"]   { border-color: #b8c9e2; }
  .card .badge {
    font-size: 11px; font-weight: 700; letter-spacing: 0.5px;
    padding: 2px 7px; border-radius: 4px; text-transform: uppercase;
    background: var(--accent-soft); color: var(--accent);
  }
  .card .badge.mon { background: #e8f0fb; color: #16447d; }
  .card .badge.tue { background: #f4e8ff; color: #5b21b6; }
  .card .badge.wed { background: #fde8e0; color: #92400e; }
  .card .badge.y2024 { background: #f1f1ee; color: #555; }
  .card .aff { color: var(--mute); font-size: 13px; width: 100%; }
  .card .talk { font-size: 14px; color: #2a2a2a; margin-top: 4px; }
  .card .chevron-row { display: flex; align-items: center; gap: 10px; }
  .card .chevron { color: var(--mute); font-size: 16px; transition: transform 0.18s; flex-shrink: 0; }
  .card details[open] > summary .chevron { transform: rotate(90deg); }

  .card .body { padding: 4px 14px 14px; border-top: 1px solid rgba(0,0,0,0.06); }
  .card .body section { margin-top: 12px; }
  .card .body section h4 { margin: 0 0 6px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--mute); font-weight: 700; }
  .card .body p { margin: 0 0 6px; font-size: 14px; line-height: 1.55; }
  .card .body ul { margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.55; }
  .card .body ul li { margin-bottom: 4px; }
  .card .body .links { margin-top: 6px; font-size: 13px; }
  .card .body .links a { margin-right: 12px; }

  .connections {
    margin-top: 14px;
    background: var(--accent-soft);
    border: 1px solid #b8c9e2;
    border-radius: 8px;
    padding: 12px 14px;
  }
  .connections h4 { color: var(--accent) !important; margin: 0 0 6px !important; }
  .connections p, .connections li { color: #1c3a66; }
  .connections ul { margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.55; }
  .connections ul li { margin-bottom: 6px; }
  .connections strong { color: #0f2a55; }
  .connections p:first-child em {
    font-style: italic; color: #0f2a55; font-weight: 500;
    display: block; padding: 2px 0 4px;
  }
"""

JS_SHARED = """
  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }
  function escAttr(s){ return escapeHtml(s).replace(/\\s+/g,' '); }
  // Minimal inline markdown: **bold**, *italic*, `code`, [text](url).
  // Operates AFTER escapeHtml so we are safe.
  function inlineMd(s) {
    s = escapeHtml(s);
    s = s.replace(/\\[([^\\]]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)/g,
                  '<a href="$2" target="_blank" rel="noopener">$1</a>');
    s = s.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[\\s(])\\*([^*\\n]+)\\*(?=[\\s.,;:!?)]|$)/g,
                  '$1<em>$2</em>');
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    return s;
  }
  function berlinNow() {
    const fmt = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Berlin', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
    const parts = Object.fromEntries(fmt.formatToParts(new Date()).map(p => [p.type, p.value]));
    return { dateStr: parts.year+'-'+parts.month+'-'+parts.day,
             minutes: parseInt(parts.hour, 10) * 60 + parseInt(parts.minute, 10) };
  }
  function parseTimeRange(str) {
    // "09:50 – 10:50" -> [590, 650]
    const m = str.replace(/[–—-]+/g, '|').split('|').map(s => s.trim());
    function toMin(t){ const x = t.match(/(\\d{1,2}):(\\d{2})/); return x ? +x[1]*60+ +x[2] : null; }
    return [toMin(m[0]), toMin(m[1])];
  }
"""


# --------------------------------------------------------------------------- #
# Render index.html (program)                                                 #
# --------------------------------------------------------------------------- #

def lookup_slug(name: str, name_to_slug: dict) -> str:
    """Try several keys against the slug index.

    Handles:
      - 'A & B' joint speakers  -> match A (first)
      - 'A, B, C and D ...'     -> match A (first author)
      - exact normalised match
    """
    if not name:
        return ""
    primary = norm_name(name)
    if primary in name_to_slug:
        return name_to_slug[primary]
    # split by ' and ', '&', ','
    for sep in [" & ", " and ", ","]:
        if sep in name:
            first = name.split(sep, 1)[0].strip()
            k = norm_name(first)
            if k in name_to_slug:
                return name_to_slug[k]
    return ""


def render_index(days: dict, name_to_slug: dict) -> str:
    # Inject slug + tier for each item if we have one
    for key, slots in days.items():
        for s in slots:
            for it in s["items"]:
                slug = lookup_slug(it.get("speaker", ""), name_to_slug)
                it["slug"] = slug
                it["tier"] = tier_of(slug) if slug else ""

    data = {
        "days": [
            {"key": "mon", "label": "Mon 18 May", "date": "2026-05-18",
             "slots": days["mon"]},
            {"key": "tue", "label": "Tue 19 May", "date": "2026-05-19",
             "slots": days["tue"]},
            {"key": "wed", "label": "Wed 20 May (Workshop)",
             "date": "2026-05-20", "slots": days["wed"]},
        ],
    }
    payload = json.dumps(data, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#fafaf8">
<title>MBC 2026 — Program · Personal</title>
<meta name="description" content="Personal program one-pager for the Machine+Behavior Conference 2026 + Behavioral Clones Workshop, Harnack House, Berlin.">

<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="MBC 2026">
<style>
{CSS_VARS}
{CSS_BASE}
{CSS_PROGRAM}
</style>
</head>
<body>
<div class="wrap">

  <div class="hero">
    <h1>MBC 2026 — Program</h1>
    <div class="meta">
      Machine+Behavior Conference · 18–19 May 2026 · Behavioral Clones Workshop 20 May<br>
      Harnack House, Berlin · times Europe/Berlin (CEST)
    </div>
  </div>

  <div class="navbar">
    <a class="active" href="index.html">Program</a>
    <a href="people.html">Speakers</a>
  </div>

  <div class="status" id="status">
    <div class="dot"></div>
    <div class="text">
      <div class="label" id="statusLabel">Loading…</div>
      <div class="now" id="statusNow">—</div>
      <div class="next" id="statusNext"></div>
    </div>
  </div>

  <div class="toolbar">
    <input id="q" type="search" placeholder="Search by name, affiliation, title…" autocomplete="off">
    <div class="pills" id="dayPills"></div>
    <div class="actions">
      <span class="count" id="count"></span>
    </div>
  </div>

  <ol class="timeline" id="timeline"></ol>

  <footer>
    Personal one-pager. Roster + commentary by Levin Brinkmann.<br>
    Canonical site: <a href="https://machinebehavior.science/program-1" target="_blank" rel="noopener">machinebehavior.science/program-1</a>
    · workshop: <a href="https://machinebehavior.science/behavioral-clones-ws" target="_blank" rel="noopener">behavioral-clones-ws</a>
  </footer>
</div>

<script type="application/json" id="data">{payload}</script>
<script>
{JS_SHARED}
const DATA = JSON.parse(document.getElementById('data').textContent);
let activeDay = (function(){{
  const {{ dateStr }} = berlinNow();
  const found = DATA.days.find(d => d.date === dateStr);
  if (found) return found.key;
  // before conference -> Mon; after -> Wed
  if (dateStr < '2026-05-18') return 'mon';
  if (dateStr > '2026-05-20') return 'wed';
  return 'mon';
}})();
let query = '';

function kindClass(k) {{ return 'kind-' + (k || 'session'); }}

function renderPills() {{
  const wrap = document.getElementById('dayPills');
  wrap.innerHTML = DATA.days.map(d =>
    `<button class="pill${{''}}" data-day="${{d.key}}">${{escapeHtml(d.label)}}</button>`
  ).join('');
  Array.from(wrap.children).forEach(btn => {{
    if (btn.dataset.day === activeDay) btn.classList.add('active');
    btn.addEventListener('click', () => {{
      activeDay = btn.dataset.day;
      Array.from(wrap.children).forEach(b => b.classList.toggle('active', b === btn));
      renderTimeline();
      applyFilter();
      updateStatus();
    }});
  }});
}}

function renderSlot(s, idx) {{
  const hasItems = s.items && s.items.length > 0;
  const cls = ['slot', kindClass(s.kind), `slot-${{idx}}`].join(' ');
  const itemsHtml = hasItems ? `
    <div class="body">
      ${{s.items.map(it => {{
        const search = escAttr(((it.speaker||'')+' '+(it.aff||'')+' '+(it.title||'')+' '+(it.abstract||'')).toLowerCase());
        const isPanel = it.is_panel || (it.title && it.title.toLowerCase().includes('panel discussion') && !it.speaker);
        const tierMark = it.tier === 'priority'
          ? `<span class="tier tier-priority" title="Priority match">★</span>`
          : (it.tier === 'strong'
             ? `<span class="tier tier-strong" title="Strong match">●</span>`
             : '');
        const nameHtml = it.speaker
          ? (it.slug
              ? `<span class="name">${{tierMark}}<a href="people.html#${{escapeHtml(it.slug)}}">${{escapeHtml(it.speaker)}}</a></span>`
              : `<span class="name">${{escapeHtml(it.speaker)}}</span>`)
          : '';
        return `<div class="item${{isPanel?' panel-row':''}}" data-tier="${{escapeHtml(it.tier||'')}}" data-search="${{search}}">
          <div class="head-line">
            ${{it.time ? `<span class="time-mini">${{escapeHtml(it.time)}}</span>` : ''}}
            ${{nameHtml}}
            ${{it.aff ? `<span class="aff">${{escapeHtml(it.aff)}}</span>` : ''}}
          </div>
          ${{it.title ? `<div class="title">${{escapeHtml(it.title)}}</div>` : ''}}
        </div>`;
      }}).join('')}}
    </div>` : '';
  const inner = `
    <div class="time">${{escapeHtml(s.time)}}</div>
    <div class="what">${{escapeHtml(s.title)}}</div>
    ${{hasItems ? '<span class="chevron">›</span>' : ''}}
  `;
  if (hasItems) return `<li class="${{cls}}" data-idx="${{idx}}"><details><summary>${{inner}}</summary>${{itemsHtml}}</details></li>`;
  return `<li class="${{cls}}" data-idx="${{idx}}"><div class="summary">${{inner}}</div></li>`;
}}

function currentDay() {{ return DATA.days.find(d => d.key === activeDay); }}

function renderTimeline() {{
  const day = currentDay();
  document.getElementById('timeline').innerHTML = day.slots.map((s, i) => renderSlot(s, i)).join('');
}}

function updateStatus() {{
  const {{ dateStr, minutes }} = berlinNow();
  const day = currentDay();
  document.querySelectorAll('.slot').forEach(el => el.classList.remove('now','past'));

  const status = document.getElementById('status');
  const label = document.getElementById('statusLabel');
  const now = document.getElementById('statusNow');
  const next = document.getElementById('statusNext');

  const isToday = dateStr === day.date;
  let currentIdx = -1;
  if (isToday) {{
    for (let i = 0; i < day.slots.length; i++) {{
      const [a,b] = parseTimeRange(day.slots[i].time);
      if (a !== null && b !== null && minutes >= a && minutes < b) {{ currentIdx = i; break; }}
    }}
    day.slots.forEach((s,i) => {{
      const el = document.querySelector('.slot-'+i);
      if (!el) return;
      const [,b] = parseTimeRange(s.time);
      if (i === currentIdx) {{
        el.classList.add('now');
        const det = el.querySelector('details');
        if (det && !det.hasAttribute('data-user-toggled')) det.open = true;
      }} else if (b !== null && minutes >= b) {{
        el.classList.add('past');
      }}
    }});
  }}

  if (dateStr < '2026-05-18') {{
    status.classList.remove('live');
    const dDay = new Date(day.date);
    const dNow = new Date(dateStr);
    const days = Math.ceil((dDay - dNow) / 86400000);
    label.textContent = 'Coming up';
    now.textContent = days === 1 ? 'Tomorrow' : ('In ' + days + ' days');
    next.textContent = day.label + ' · ' + (day.slots[0] ? day.slots[0].time + ' · ' + day.slots[0].title : '');
  }} else if (dateStr > '2026-05-20') {{
    status.classList.remove('live');
    label.textContent = 'Conference';
    now.textContent = 'Concluded';
    next.textContent = 'Thanks for joining.';
  }} else if (!isToday) {{
    status.classList.remove('live');
    label.textContent = 'Selected day';
    now.textContent = day.label;
    next.textContent = (day.slots[0]?.time || '') + ' · ' + (day.slots[0]?.title || '');
  }} else if (currentIdx === -1) {{
    status.classList.remove('live');
    if (day.slots.length && parseTimeRange(day.slots[0].time)[0] !== null && minutes < parseTimeRange(day.slots[0].time)[0]) {{
      label.textContent = 'Starts today';
      const mins = parseTimeRange(day.slots[0].time)[0] - minutes;
      now.textContent = 'In ' + mins + ' min';
      next.textContent = day.slots[0].time + ' — ' + day.slots[0].title;
    }} else {{
      label.textContent = 'Today';
      now.textContent = 'Between sessions';
      // find next slot
      let nx = null;
      for (const s of day.slots) {{ const [a] = parseTimeRange(s.time); if (a !== null && a > minutes) {{ nx = s; break; }} }}
      next.textContent = nx ? ('Next, ' + nx.time + ': ' + nx.title) : 'Day concluded.';
    }}
  }} else {{
    status.classList.add('live');
    const s = day.slots[currentIdx];
    const nx = day.slots[currentIdx+1];
    label.textContent = 'Live now';
    now.textContent = s.title;
    next.textContent = nx ? ('Next, ' + nx.time + ': ' + nx.title) : '(last block of the day)';
  }}
}}

function applyFilter() {{
  const q = query.trim().toLowerCase();
  let total = 0;
  document.querySelectorAll('.slot').forEach(slot => {{
    const items = slot.querySelectorAll('.body .item');
    if (!q) {{
      items.forEach(it => it.style.display = '');
      slot.classList.remove('hidden');
      total += items.length;
      return;
    }}
    if (items.length === 0) {{
      const what = slot.querySelector('.what').textContent.toLowerCase();
      slot.classList.toggle('hidden', !what.includes(q));
      return;
    }}
    let matches = 0;
    items.forEach(it => {{
      const hit = it.dataset.search && it.dataset.search.includes(q);
      it.style.display = hit ? '' : 'none';
      if (hit) matches++;
    }});
    if (matches > 0) {{
      slot.classList.remove('hidden');
      total += matches;
      const det = slot.querySelector('details');
      if (det) det.open = true;
    }} else {{
      slot.classList.add('hidden');
    }}
  }});
  document.getElementById('count').textContent = q ? (total + ' match' + (total===1?'':'es')) : '';
}}

renderPills();
renderTimeline();
updateStatus();
applyFilter();

document.getElementById('q').addEventListener('input', e => {{ query = e.target.value; applyFilter(); }});
document.addEventListener('toggle', e => {{ if (e.target.tagName === 'DETAILS') e.target.setAttribute('data-user-toggled', '1'); }}, true);
setInterval(updateStatus, 60_000);
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Render people.html                                                          #
# --------------------------------------------------------------------------- #

def render_people(people: list[dict], idx: dict) -> str:
    # Augment each person with day badge from the speakers index
    out_people = []
    for p in people:
        meta = idx.get(p["name_key"], {})
        badge = meta.get("day_badge", "")
        # also pull a fallback talk_title from the index if person md is empty
        talk_title = p.get("talk_title") or meta.get("talk_title", "")
        out_people.append({**p, "day_badge": badge,
                           "index_date": meta.get("date", ""),
                           "talk_title": talk_title,
                           "tier": tier_of(p.get("slug", ""))})

    # Sort: by day order (Mon, Tue, Wed, then 2024 / unknown), then name
    order = {"Mon": 0, "Tue": 1, "Wed": 2, "2024": 3, "": 4}
    out_people.sort(key=lambda p: (order.get(p["day_badge"], 5), p["name"]))

    payload = json.dumps(out_people, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#fafaf8">
<title>MBC 2026 — Speakers · Personal</title>
<meta name="description" content="Annotated speaker overview for the Machine+Behavior Conference 2026 + Behavioral Clones Workshop. Personal notes for Levin Brinkmann.">

<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="MBC 2026">
<style>
{CSS_VARS}
{CSS_BASE}
{CSS_PEOPLE}
</style>
</head>
<body>
<div class="wrap">

  <div class="hero">
    <h1>MBC 2026 — Speakers</h1>
    <div class="meta">
      Annotated speaker overview · Machine+Behavior Conference + Behavioral Clones Workshop · Harnack House, Berlin
    </div>
  </div>

  <div class="navbar">
    <a href="index.html">Program</a>
    <a class="active" href="people.html">Speakers</a>
  </div>

  <div class="toolbar">
    <input id="q" type="search" placeholder="Search name, affiliation, talk title, research…" autocomplete="off">
    <div class="pills" id="dayPills">
      <button class="pill active" data-day="all">All</button>
      <button class="pill" data-day="Mon">Mon 18</button>
      <button class="pill" data-day="Tue">Tue 19</button>
      <button class="pill" data-day="Wed">Wed 20</button>
      <button class="pill" data-day="2024">2024 alumni</button>
    </div>
    <div class="actions">
      <span class="count" id="count"></span>
    </div>
  </div>

  <ol class="roster" id="roster"></ol>

  <footer>
    Personal one-pager. Speaker briefs and "Connections to Levin's work" annotations live in <code>research/people/&lt;slug&gt;.md</code>.<br>
    Canonical site: <a href="https://machinebehavior.science/program-1" target="_blank" rel="noopener">machinebehavior.science</a>
  </footer>
</div>

<script type="application/json" id="data">{payload}</script>
<script>
{JS_SHARED}
const PEOPLE = JSON.parse(document.getElementById('data').textContent);

let activeDay = 'all';
let query = '';

function badgeClass(b) {{
  if (b === 'Mon') return 'mon';
  if (b === 'Tue') return 'tue';
  if (b === 'Wed') return 'wed';
  if (b === '2024') return 'y2024';
  return '';
}}

function renderPerson(p) {{
  const searchBlob = ((p.name||'')+' '+(p.affiliation||'')+' '+(p.talk_title||'')+' '+(p.summary||'')+' '+(p.topics||'')+' '+(p.role||'')+' '+(p.connections||'')).toLowerCase();
  const links = [];
  if (p.homepage) links.push(`<a href="${{escapeHtml(p.homepage)}}" target="_blank" rel="noopener">homepage</a>`);
  if (p.scholar) links.push(`<a href="${{escapeHtml(p.scholar)}}" target="_blank" rel="noopener">scholar</a>`);
  const threadsHtml = (p.threads && p.threads.length)
    ? `<section><h4>Active research threads</h4><ul>${{p.threads.map(t => `<li>${{inlineMd(t)}}</li>`).join('')}}</ul></section>` : '';
  const papersHtml = (p.papers && p.papers.length)
    ? `<section><h4>Key recent papers</h4><ul>${{p.papers.map(t => `<li>${{inlineMd(t)}}</li>`).join('')}}</ul></section>` : '';
  const methodsHtml = p.methods
    ? `<section><h4>Methodological signature</h4><p>${{inlineMd(p.methods)}}</p></section>` : '';
  const summaryHtml = p.summary
    ? `<section><h4>Research summary</h4><p>${{inlineMd(p.summary)}}</p></section>` : '';
  let connBody = '';
  if (p.connections_lead) {{
    // Render lead-in prose (hook + summary paragraph) as one or more <p>.
    const leadParas = p.connections_lead.split(/\\n\\s*\\n/)
      .map(s => s.trim()).filter(Boolean);
    connBody += leadParas.map(t => `<p>${{inlineMd(t)}}</p>`).join('');
  }}
  if (p.connections_items && p.connections_items.length) {{
    if (p.connections_kind === 'paragraph') {{
      connBody += p.connections_items.map(t => `<p>${{inlineMd(t)}}</p>`).join('');
    }} else {{
      connBody += `<ul>${{p.connections_items.map(t => `<li>${{inlineMd(t)}}</li>`).join('')}}</ul>`;
    }}
  }}
  const connHtml = connBody
    ? `<div class="connections"><h4>Connections to Levin's work</h4>${{connBody}}</div>` : '';
  const collabHtml = (p.collaborators && p.collaborators.length)
    ? `<section><h4>Collaborators of note</h4><ul>${{p.collaborators.map(t => `<li>${{inlineMd(t)}}</li>`).join('')}}</ul></section>` : '';
  const notesHtml = p.notes
    ? `<section><h4>Notes</h4><p>${{inlineMd(p.notes)}}</p></section>` : '';
  const linksHtml = links.length ? `<div class="links">${{links.join(' · ')}}</div>` : '';

  const badge = p.day_badge
    ? `<span class="badge ${{badgeClass(p.day_badge)}}">${{escapeHtml(p.day_badge)}}</span>` : '';
  const talkHtml = p.talk_title
    ? `<div class="talk">${{escapeHtml(p.talk_title)}}</div>` : '';
  const tierMark = p.tier === 'priority'
    ? `<span class="tier tier-priority" title="Priority match for Levin's work" aria-label="priority match">★</span>`
    : (p.tier === 'strong'
       ? `<span class="tier tier-strong" title="Strong match for Levin's work" aria-label="strong match">●</span>`
       : '');

  return `<li class="card" id="${{escapeHtml(p.slug)}}" data-day="${{escapeHtml(p.day_badge || '')}}" data-tier="${{escapeHtml(p.tier || '')}}" data-search="${{escAttr(searchBlob)}}">
    <details><summary>
      <div class="head">
        ${{tierMark}}
        <span class="name">${{escapeHtml(p.name)}}</span>
        ${{badge}}
        <span class="chevron-row"><span class="chevron">›</span></span>
      </div>
      <div class="aff">${{escapeHtml(p.affiliation || '')}}</div>
      ${{talkHtml}}
    </summary>
    <div class="body">
      ${{summaryHtml}}
      ${{threadsHtml}}
      ${{papersHtml}}
      ${{methodsHtml}}
      ${{collabHtml}}
      ${{connHtml}}
      ${{notesHtml}}
      ${{linksHtml}}
    </div>
    </details>
  </li>`;
}}

function renderAll() {{
  document.getElementById('roster').innerHTML = PEOPLE.map(renderPerson).join('');
  // If URL hash, open that card and scroll
  if (location.hash) {{
    const id = decodeURIComponent(location.hash.slice(1));
    const el = document.getElementById(id);
    if (el) {{
      el.classList.add('target');
      const det = el.querySelector('details');
      if (det) det.open = true;
      setTimeout(() => el.scrollIntoView({{ behavior: 'smooth', block: 'start' }}), 50);
    }}
  }}
}}

function applyFilter() {{
  const q = query.trim().toLowerCase();
  let total = 0;
  document.querySelectorAll('.card').forEach(card => {{
    const dayOk = (activeDay === 'all') || (card.dataset.day === activeDay);
    const qOk = !q || (card.dataset.search && card.dataset.search.includes(q));
    const show = dayOk && qOk;
    card.classList.toggle('hidden', !show);
    if (show) {{
      total++;
      if (q) {{
        const det = card.querySelector('details');
        if (det) det.open = true;
      }}
    }}
  }});
  document.getElementById('count').textContent =
    q ? (total + ' match' + (total===1?'':'es')) : (total + ' speakers');
}}

renderAll();
applyFilter();

document.querySelectorAll('#dayPills .pill').forEach(btn => {{
  btn.addEventListener('click', () => {{
    activeDay = btn.dataset.day;
    document.querySelectorAll('#dayPills .pill').forEach(b => b.classList.toggle('active', b === btn));
    applyFilter();
  }});
}});
document.getElementById('q').addEventListener('input', e => {{ query = e.target.value; applyFilter(); }});
window.addEventListener('hashchange', () => {{
  document.querySelectorAll('.card.target').forEach(el => el.classList.remove('target'));
  if (location.hash) {{
    const id = decodeURIComponent(location.hash.slice(1));
    const el = document.getElementById(id);
    if (el) {{
      el.classList.add('target');
      const det = el.querySelector('details');
      if (det) det.open = true;
      el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    }}
  }}
}});
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Manifest                                                                    #
# --------------------------------------------------------------------------- #

MANIFEST = {
    "name": "MBC 2026 — Personal",
    "short_name": "MBC 2026",
    "description": ("Personal program + annotated speaker overview for the "
                    "Machine+Behavior Conference 2026 and Behavioral Clones "
                    "Workshop, Harnack House Berlin."),
    "start_url": "./index.html",
    "scope": "./",
    "display": "standalone",
    "background_color": "#fafaf8",
    "theme_color": "#2a5da8",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any maskable"},
    ],
}


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def main() -> None:
    print("Parsing program HTML…")
    decoded = load_program()
    days = parse_program(decoded)
    n_slots = sum(len(v) for v in days.values())
    print(f"  Mon: {len(days['mon'])} slots  Tue: {len(days['tue'])} slots  "
          f"Wed: {len(days['wed'])} slots  total: {n_slots}")

    print("Loading speakers…")
    people = load_people()
    print(f"  {len(people)} speaker profiles")

    print("Loading speaker index…")
    idx = load_index()
    name_to_slug = {p["name_key"]: p["slug"] for p in people}

    # Sanity-check name joins from program → people
    joined, unjoined = 0, []
    for slots in days.values():
        for s in slots:
            for it in s["items"]:
                if not it.get("speaker"):
                    continue
                if lookup_slug(it["speaker"], name_to_slug):
                    joined += 1
                else:
                    unjoined.append(it["speaker"])
    print(f"  Program→speaker joins: {joined} matched, {len(unjoined)} unmatched")
    if unjoined:
        # Show a few uniques
        uniq = sorted(set(unjoined))
        print(f"  e.g. unmatched: {uniq[:8]}")

    print("Rendering pages…")
    (ROOT / "index.html").write_text(render_index(days, name_to_slug),
                                     encoding="utf-8")
    (ROOT / "people.html").write_text(render_people(people, idx),
                                      encoding="utf-8")
    (ROOT / "manifest.webmanifest").write_text(
        json.dumps(MANIFEST, indent=2) + "\n", encoding="utf-8")
    print("Wrote index.html, people.html, manifest.webmanifest")


if __name__ == "__main__":
    main()
