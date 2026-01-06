from flask import Flask, render_template, abort
import requests

app = Flask(__name__)

# =============================
# GOOGLE SHEET CONFIG
# =============================
SHEET_ID = "1yz0k_rrv8QA5tDYljVjQFGvqINy1aniVpSW-lJqYOVE"

TABS = {
    "summary": "Dashboard_Summary",
    "countries": "Country_Profiles",
    "pefa": "PEFA_Tracker",
    "practices": "Good_Practices",
}

# =============================
# HELPERS
# =============================
def fetch(tab_key: str):
    url = f"https://opensheet.elk.sh/{SHEET_ID}/{TABS[tab_key]}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def norm_code(v):
    return str(v or "").replace("\u00a0", "").strip().upper()

def score_band(score):
    try:
        s = float(str(score).strip())
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

def clean_dashboard_summary(rows):
    cleaned = []
    for row in rows:
        metric = row.get("Metric")
        value = row.get("Value")
        if metric and str(metric).strip().lower() != "metric":
            cleaned.append({"metric": metric, "value": value})
    return cleaned

def extract_code(row: dict) -> str:
    # Robustly extract country code from any column containing 'code'
    for k, v in (row or {}).items():
        if "code" in str(k).lower():
            return norm_code(v)
    return ""

# =============================
# ROUTES
# =============================
@app.route("/")
def overview():
    summary = clean_dashboard_summary(fetch("summary"))
    countries_rows = fetch("countries")

    # --- Score band counts (for donut chart) ---
    band_counts = {
        "Very Strong": 0,
        "Strong": 0,
        "Moderate": 0,
        "Weak": 0,
        "N/A": 0,
    }

    # --- Dimension averages (for bar chart) ---
    dim_cols = [
        ("PFM Assessments", "Dim 1: PFM Assessments"),
        ("Climate Risk", "Dim 2: Climate Risk"),
        ("Fiscal Risk Mgmt", "Dim 3: Fiscal Risk Mgmt"),
        ("Finance Mobilisation", "Dim 4: Finance Mobilisation"),
        ("Capabilities", "Dim 5: Capabilities"),
    ]
    dim_vals = {k: [] for k, _ in dim_cols}

    # --- Country scores (for horizontal bar chart) ---
    country_scores = []

    for c in countries_rows:
        overall = c.get("Overall Score")
        band = score_band(overall)
        band_counts[band if band in band_counts else "N/A"] += 1

        for label, col in dim_cols:
            try:
                v = c.get(col)
                if v is None or str(v).strip() == "":
                    continue
                dim_vals[label].append(float(str(v).strip()))
            except Exception:
                pass

        try:
            if overall is not None and str(overall).strip() != "":
                code = norm_code(c.get("Code"))
                country_scores.append({
                    "name": c.get("Country") or "",
                    "code": code,
                    "score": float(str(overall).strip()),
                    "band": band,
                    "url": f"/country/{code}" if code else None,
                })
        except Exception:
            pass

    dimension_avgs = {
        k: (round(sum(vals) / len(vals), 1) if vals else None)
        for k, vals in dim_vals.items()
    }

    country_scores = sorted(country_scores, key=lambda x: x.get("score") or 0, reverse=True)

    return render_template(
        "overview.html",
        summary=summary,
        band_counts=band_counts,
        dimension_avgs=dimension_avgs,
        country_scores=country_scores,
    )

@app.route("/countries")
def countries():
    rows = fetch("countries")
    countries_list = []
    for r in rows:
        code = norm_code(r.get("Code"))
        overall = r.get("Overall Score")
        band = score_band(overall)
        countries_list.append({
            "Country": r.get("Country"),
            "Code": code,
            "Overall Score": overall,
            "Score Band": band,
        })

    countries_list = sorted(countries_list, key=lambda x: x.get("Country") or "")

    # Map data for SVG colouring + click navigation
    map_data = {
        c["Code"]: {
            "name": c["Country"],
            "band": c["Score Band"],
            "score": c["Overall Score"],
            "url": f"/country/{c['Code']}" if c.get("Code") else None,
        }
        for c in countries_list if c.get("Code")
    }

    return render_template("countries.html", countries=countries_list, map_data=map_data)

@app.route("/country/<code>")
def country(code):
    code = norm_code(code)

    countries_rows = fetch("countries")
    pefa_rows = fetch("pefa")
    practices_rows = fetch("practices")

    # -------------------------
    # FIND COUNTRY
    # -------------------------
    country_row = next(
        (c for c in countries_rows if norm_code(c.get("Code")) == code),
        None
    )
    if not country_row:
        abort(404)

    country_obj = {
        "Country": country_row.get("Country"),
        "Code": country_row.get("Code"),
        "Overall Score": country_row.get("Overall Score"),
        "Score Band": score_band(country_row.get("Overall Score")),
    }

    dims = {
        "Dim 1: PFM Assessments": country_row.get("Dim 1: PFM Assessments"),
        "Dim 2: Climate Risk": country_row.get("Dim 2: Climate Risk"),
        "Dim 3: Fiscal Risk Mgmt": country_row.get("Dim 3: Fiscal Risk Mgmt"),
        "Dim 4: Finance Mobilisation": country_row.get("Dim 4: Finance Mobilisation"),
        "Dim 5: Capabilities": country_row.get("Dim 5: Capabilities"),
    }

    # -------------------------
    # PEFA (filter by any code column)
    # -------------------------
    country_pefa = []
    for p in pefa_rows:
        if extract_code(p) == code:
            country_pefa.append({
                "assessments": (
                    p.get("PEFA_Assessments")
                    or p.get("#_PEFA_Assessments")
                    or p.get("# PEFA Assessments")
                ),
                "latest_year": p.get("Latest_PEFA_Year") or p.get("Latest PEFA Year"),
                "reform_plan": p.get("PFM_Reform_Plan") or p.get("PFM Reform Plan"),
                "other_assessments": (
                    p.get("Other_PFM_Climate_Finance_Assessments")
                    or p.get("Other PFM & Climate Finance Assessments")
                ),
                "latest_pfm_activities": (
                    p.get("Latest_PFM_Activities")
                    or p.get("Latest PFM Activities (PFTAC Country Workplans FY 25/26)")
                    or p.get("Latest PFM Activities")
                ),
                "link": (
                    p.get("PEFA_Report")
                    or p.get("PEFA_Report_Portal_Link")
                    or p.get("PEFA Report/Portal Link")
                    or p.get("PEFA Link")
                    or p.get("Report URL")
                ),
            })
# -------------------------
    # GOOD PRACTICES (filter by any code column)
    # -------------------------
    country_practices = []
    for g in practices_rows:
        if extract_code(g) == code:
            country_practices.append({
                "area": g.get("Practice_Area") or g.get("Practice Area"),
                "description": g.get("Description"),
                "replicability": g.get("Replicability"),
            })

    return render_template(
        "country.html",
        country=country_obj,
        dims=dims,
        pefa=country_pefa,
        practices=country_practices,
    )

if __name__ == "__main__":
    app.run(debug=True)
