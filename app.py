from flask import Flask, render_template, abort, jsonify
import requests
from collections import defaultdict, Counter
import urllib.parse

app = Flask(__name__)

# =============================
# CONFIG
# =============================
SHEET_ID = "175ErynCbO3X82AcPgc9QHeg4846G6Jk2EQmcpfKnL7A"

TABS = {
    "summary": "Dashboard_Summary",      # existing dashboard summary
    "missions": "Joint_Missions",        # missions table
    "mission_summary": "Summary_Missions", # missions summary for pie chart
    "quarters": "Missions_Quarters",
    "rag": "RAG_Legends",
    "countries": "Country_Profiles",
    "pefa": "PEFA_Tracker",
    "practices": "Good_Practices",
    "methodology": "PEFA_Score_Methodology",
}

PILLAR_COLUMNS = [
    "Pillar I: Budget Reliability (PI-1 to PI-3)",
    "Pillar II: Transparency of Public Finances (PI-4 to PI-9)",
    "Pillar III: Assets, Liabilities & Fiscal Strategy (PI-10 to PI-18)",
    "Pillar IV: Predictability & Control in Execution (PI-19 to PI-26)",
    "Pillar V: Accounting, Reporting & External Scrutiny (PI-27 to PI-31)",
]

TA_COLUMNS = [
    "Public Financial Management",
    "Revenue Administration",
    "Real Sector Statistics",
    "Debt Management",
    "Financial Sector Supervision",
    "Macroeconomic Frameworks",
    "Macroeconomic Programming and Analysis",
    "Government Finance Statistics",
    "Prices",
    "External Sector",
]

# =============================
# HELPERS
# =============================
def fetch(tab_key: str):
    tab = urllib.parse.quote(TABS[tab_key], safe="")
    url = f"https://opensheet.elk.sh/{SHEET_ID}/{tab}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def norm_code(v):
    return str(v or "").replace("\u00a0", "").strip().upper()

def safe_float(v):
    s = str(v or "").strip()
    if not s or s.upper() == "TBC":
        return None
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None

def score_band(score):
    # score may be float or None
    if score is None:
        return "N/A"
    try:
        s = float(score)
    except Exception:
        return "N/A"
    if s >= 85:
        return "Very Strong"
    elif s >= 70:
        return "Strong"
    elif s >= 55:
        return "Moderate"
    else:
        return "Weak"

def score_band_value(raw_value):
    """Banding that preserves explicit 'TBC' values.

    - If the cell is literally 'TBC' -> return 'TBC'
    - Else compute from numeric score
    - If missing/invalid -> 'N/A'
    """
    s = str(raw_value or "").strip()
    if s.upper() == "TBC":
        return "TBC"
    num = safe_float(s)
    if num is None:
        return "N/A"
    return score_band(num)

def extract_code(row: dict) -> str:
    for k, v in (row or {}).items():
        if "code" in str(k).lower():
            return norm_code(v)
    return ""


def load_quarter_legends():
    return fetch("quarters")


def load_rag_legends():
    return fetch("rag")

def strip_row_keys(rows):
    """Normalize opensheet rows: strip key whitespace and string values."""
    cleaned = []
    for r in rows or []:
        out = {}
        for k, v in (r or {}).items():
            kk = str(k).strip()
            vv = v.strip() if isinstance(v, str) else v
            out[kk] = vv
        cleaned.append(out)
    return cleaned

# =============================
# GLOBAL DATA LOAD
# =============================
def load_all_data():
    country_profiles = fetch("countries")
    pefa_rows = fetch("pefa")
    practices_rows = fetch("practices")
    return country_profiles, pefa_rows, practices_rows

def load_summary_metrics():
    """Read Dashboard_Summary sheet (Metric/Value) into a dict."""
    try:
        rows = fetch("summary")
    except Exception:
        return {}

    out = {}
    for r in rows or []:
        metric = (r.get("Metric") or "").strip()
        if not metric:
            continue
        out[metric] = r.get("Value")
    return out

# =============================
# ROUTES
# =============================
@app.route("/")
def overview():
    country_profiles, _, practices_rows = load_all_data()

    summary_sheet = load_summary_metrics()

    # Overview Summary Metrics
    # Prefer Dashboard_Summary sheet (authoritative), fall back to computed values.
    scores = []
    for c in country_profiles:
        val = safe_float(c.get("Overall Score"))
        if val is not None:
            scores.append(val)

    avg_overall_score = (sum(scores) / len(scores)) if scores else None

    def _pick_number(v, fallback=None):
        if v is None:
            return fallback
        s = str(v).strip()
        if not s:
            return fallback
        try:
            return int(float(s))
        except Exception:
            return fallback

    def _pick_float(v, fallback=None):
        if v is None:
            return fallback
        s = str(v).strip()
        if not s or s.upper() == "TBC":
            return fallback
        try:
            return float(s)
        except Exception:
            return fallback

    countries_total = _pick_number(
        summary_sheet.get("Total Forum Island Countries Covered"),
        fallback=len(country_profiles)
    )
    avg_from_sheet = _pick_float(summary_sheet.get("Average Overall PFM Score (scored countries only)"), fallback=None)
    avg_display = avg_from_sheet if avg_from_sheet is not None else avg_overall_score

    # Build the ONLY 8 metrics allowed on the Overview summary card
    overview_metrics = [
        ("Total Forum Island Countries Covered", countries_total),
        ("Countries with PEFA Assessments", _pick_number(summary_sheet.get("Countries with PEFA Assessments"), fallback="N/A")),
        ("Countries with PEFA Scores (not TBC)", _pick_number(summary_sheet.get("Countries with PEFA Scores (not TBC)"), fallback="N/A")),
        ("Countries with TBC scores (pending PEFA)", _pick_number(summary_sheet.get("Countries with TBC scores (pending PEFA)"), fallback="N/A")),
        ("Average Overall PFM Score (scored countries only)", round(avg_display, 1) if avg_display is not None else "N/A"),
        ("Countries with Reform Plans", _pick_number(summary_sheet.get("Countries with Reform Plans"), fallback="N/A")),
        ("Highest Performing Country", (summary_sheet.get("Highest Performing Country") or "N/A")),
        ("Lowest Performing Country (scored)", (summary_sheet.get("Lowest Performing Country (scored)") or "N/A")),
    ]

    # Score Bands
    band_counts = {b: 0 for b in ["Very Strong", "Strong", "Moderate", "Weak", "N/A"]}
    for c in country_profiles:
        band = score_band(safe_float(c.get("Overall Score")))
        band_counts[band] += 1

    # Pillar Averages
    dim_sums = defaultdict(float)
    dim_counts = defaultdict(int)

    for row in country_profiles:
        for dim in PILLAR_COLUMNS:
            val = safe_float(row.get(dim))
            if val is not None:
                dim_sums[dim] += val
                dim_counts[dim] += 1

    dimension_avgs = {
        dim: round(dim_sums[dim] / dim_counts[dim], 1) if dim_counts[dim] else 0
        for dim in PILLAR_COLUMNS
    }
    # Short labels for chart: I (PI-1 to PI-3), II (...), etc.
    pillar_short_labels = {
        PILLAR_COLUMNS[0]: "I (PI-1 to PI-3)",
        PILLAR_COLUMNS[1]: "II (PI-4 to PI-9)",
        PILLAR_COLUMNS[2]: "III (PI-10 to PI-18)",
        PILLAR_COLUMNS[3]: "IV (PI-19 to PI-26)",
        PILLAR_COLUMNS[4]: "V (PI-27 to PI-31)",
    }
    dimension_avgs_short = {pillar_short_labels.get(k, k): v for k, v in dimension_avgs.items()}


    # Country Scores (sorted safely with None)
    country_scores = []
    for c in country_profiles:
        name = (c.get("Country") or "").strip()
        code = norm_code(c.get("Code"))
        score = safe_float(c.get("Overall Score"))
        band = score_band(score)
        if name and code:
            country_scores.append({
                "name": name,
                "score": score,
                "band": band,
                "url": f"/country/{code}"
            })

    country_scores.sort(key=lambda x: (x["score"] is None, -(x["score"] or -1e9)))

    # TA Area Data (sorted safely with None)
    ta_area_data = {}
    for col in TA_COLUMNS:
        all_scores = []
        for c in country_profiles:
            name = (c.get("Country") or "").strip()
            code = norm_code(c.get("Code"))
            score = safe_float(c.get(col))
            if name and code:
                all_scores.append({
                    "name": name,
                    "score": score,
                    "url": f"/country/{code}"
                })
        all_scores.sort(key=lambda x: (x["score"] is None, -(x["score"] or -1e9)))
        ta_area_data[col] = all_scores

    return render_template(
        "overview.html",
        overview_metrics=overview_metrics,
        band_counts=band_counts,
        dimension_avgs=dimension_avgs,
        dimension_avgs_short=dimension_avgs_short,
        country_scores=country_scores,
        ta_area_data=ta_area_data,
        pillar_headers=PILLAR_COLUMNS,
    )

@app.route("/countries")
def countries():
    rows = fetch("countries")
    countries_list = []
    for r in rows:
        code = norm_code(r.get("Code"))
        overall_raw = (r.get("Overall Score") or "").strip()
        overall = safe_float(overall_raw)
        band = score_band_value(overall_raw)
        countries_list.append({
            "Country": (r.get("Country") or "").strip(),
            "Code": code,
            "Overall Score": overall,
            "Score Band": band,
        })
    countries_list.sort(key=lambda x: x.get("Country") or "")

    map_data = {
        c["Code"]: {
            "name": c["Country"],
            "band": c["Score Band"],
            "score": c["Overall Score"],
            "url": f"/country/{c['Code']}"
        }
        for c in countries_list if c.get("Code")
    }
    return render_template("countries.html", countries=countries_list, map_data=map_data)

@app.route("/country/<code>")
def country(code):
    code = norm_code(code)
    country_profiles, pefa_rows, practices_rows = load_all_data()

    country_row = next((c for c in country_profiles if norm_code(c.get("Code")) == code), None)
    if not country_row:
        abort(404)

    overall_val = (country_row.get("Overall Score") or "").strip()
    overall_num = safe_float(overall_val)

    country_obj = {
        "Country": (country_row.get("Country") or "").strip(),
        "Code": (country_row.get("Code") or "").strip(),
        "Overall Score": overall_val,
        "Score Band": score_band(overall_num),
    }

    dims = {dim: (country_row.get(dim) or "").strip() for dim in PILLAR_COLUMNS}

    # Workplan link from Country Profiles
    workplan_link = (country_row.get("Workplan (Link)") or "").strip()

    # PEFA rows
    country_pefa = []
    for p in pefa_rows:
        if extract_code(p) == code:
            country_pefa.append({
                "assessments": p.get("PEFA_Assessments") or p.get("# PEFA Assessments") or "",
                "latest_year": p.get("Latest PEFA Year") or "",
                "reform_plan": p.get("PFM Reform Plan") or "",
                "other_assessments": (
                    p.get("Other PFM & Fiscal Assessments") or
                    p.get("Other PFM & Climate Finance Assessments") or
                    ""
                ),
                "latest_pfm_activities": (
                    p.get("Latest PFM Activities (PFTAC Country Workplans FY 25/26)") or
                    p.get("Latest PFM Activities") or
                    ""
                ),
                "link": p.get("PEFA Report/Portal Link") or "",
            })

    # Good practices
    country_practices = []
    for g in practices_rows:
        if extract_code(g) == code:
            country_practices.append({
                "area": g.get("Practice Area") or "",
                "description": g.get("Description") or "",
                "replicability": g.get("Replicability") or "",
            })

    methodology_rows = fetch("methodology")

    return render_template(
        "country.html",
        country=country_obj,
        dims=dims,
        pefa=country_pefa,
        practices=country_practices,
        workplan_link=workplan_link,
        methodology=methodology_rows,
    )


# ===============================
# JOINT MISSIONS
# ===============================
def load_missions():
    """Load missions from the Joint_Missions sheet."""
    raw = strip_row_keys(fetch("missions"))
    if not raw:
        return []

    keys = list(raw[0].keys())
    if not keys:
        return []

    # If opensheet already used the first row as headers, just use it directly
    if "ID" in keys:
        rows = raw
    else:
        # Fallback: sheet has pre-header rows and header embedded in data
        first_col = keys[0]
        header_map = None
        header_idx = None

        for i, r in enumerate(raw):
            if str(r.get(first_col) or "").strip().upper() == "ID":
                header_map = {k: str(r.get(k) or "").strip() for k in keys}
                header_idx = i
                break

        if header_map is None:
            return []

        rows = []
        for r in raw[header_idx + 1:]:
            row = {}
            for k in keys:
                col = header_map.get(k)
                if col:
                    row[col] = r.get(k)
            rows.append(row)

    cleaned = []
    for r in rows:
        rid = str(r.get("ID") or "").strip()
        if not rid:
            continue

        if rid.upper().startswith("M-"):
            cleaned.append({"_type": "mission", **r})

    return cleaned


def load_quarter_legends():
    # Key fix: strips "Quarter " -> "Quarter" and "Description " -> "Description"
    return strip_row_keys(fetch("quarters"))


def load_rag_legends():
    return strip_row_keys(fetch("rag"))


def load_mission_summary():
    """Load mission summary numbers from Summary_Missions sheet (case/space tolerant)."""
    rows = strip_row_keys(fetch("mission_summary"))
    summary = {}

    for r in rows or []:
        metric_raw = (r.get("Metric") or "")
        metric = " ".join(metric_raw.split()).strip().lower()  # normalize spaces + lowercase
        value = r.get("Value")
        if metric:
            summary[metric] = value

    return summary


@app.route("/joint-missions")
def joint_missions():
    missions = load_missions()
    summary = load_mission_summary()

    quarters = load_quarter_legends()
    rag_legends = load_rag_legends()

    def to_int(v):
        try:
            return int(float(str(v)))
        except:
            return 0

    # NOTE: keys are lowercase now because load_mission_summary() normalizes them
    total = to_int(summary.get("total missions tracked"))

    status_counts = {
        "Completed": to_int(summary.get("completed")),
        "In Progress": to_int(summary.get("in progress")),
        "In Planning / Confirmed": to_int(summary.get("in planning / confirmed")),
        "Not Started": to_int(summary.get("not started")),
        "Deferred": to_int(summary.get("deferred")),
    }

    # Better fallback: also triggers if summary sheet returns 0s
    if (total == 0 or sum(status_counts.values()) == 0) and missions:
        total = len(missions)
        c = Counter((m.get("Status") or "").strip() for m in missions)
        status_counts = {
            "Completed": c.get("Completed", 0),
            "In Progress": c.get("In Progress", 0),
            "In Planning / Confirmed": c.get("In Planning / Confirmed", 0),
            "Not Started": c.get("Not Started", 0),
            "Deferred": c.get("Deferred", 0),
        }

    return render_template(
        "joint_missions.html",
        missions=missions,
        total=total,
        status_counts=status_counts,
        quarters=quarters,
        rag_legends=rag_legends
    )


@app.route("/api/mission/<mission_id>")
def mission_api(mission_id):
    rows = load_missions()

    mission = next(
        (
            r for r in rows
            if r.get("_type") == "mission"
            and str(r.get("ID") or "").strip() == mission_id
        ),
        None
    )

    if not mission:
        abort(404)

    payload = {}
    for k, v in mission.items():
        if k != "_type":
            payload[k] = v if v is not None else ""

    return jsonify(payload)


@app.route("/mission/<mission_id>")
def mission_detail(mission_id):
    rows = load_missions()
    mission = next(
        (r for r in rows if r.get("_type") == "mission" and str(r.get("ID") or "").strip() == mission_id),
        None
    )
    if not mission:
        abort(404)
    return render_template("mission_detail.html", mission=mission)


if __name__ == "__main__":
    app.run(debug=True)
