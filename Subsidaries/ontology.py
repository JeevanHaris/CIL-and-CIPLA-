"""
CMPDI/CIL — Domain Ontology
────────────────────────────
Defines the vocabulary, entity types, unit mappings and subsidiary
registry for the CMPDI/CIL sovereign document intelligence platform.

Used by:
  - extractor.py  (entity recognition, unit normalisation)
  - validator.py  (conflict grouping by entity + metric)
  - knowledge_base.py (schema constants)
  - router.py     (domain signal keywords)
"""

# ─── CIL Subsidiaries ─────────────────────────────────────────────
SUBSIDIARIES = {
    "ECL":  "Eastern Coalfields Limited",
    "BCCL": "Bharat Coking Coal Limited",
    "CCL":  "Central Coalfields Limited",
    "NCL":  "Northern Coalfields Limited",
    "WCL":  "Western Coalfields Limited",
    "SECL": "South Eastern Coalfields Limited",
    "MCL":  "Mahanadi Coalfields Limited",
    "NEC":  "North Eastern Coalfields",
    "CIL":  "Coal India Limited",
    "CMPDI":"Central Mine Planning and Design Institute Limited",
    "CMPDIL":"Central Mine Planning and Design Institute Limited",
}

# All known subsidiary aliases (lowercase for matching).
# IMPORTANT: We only add the short code itself as an alias.
# We do NOT blindly split the full subsidiary name into words — generic words
# like "limited", "eastern", "coalfields", "central", "western", "northern",
# "southern", "mahanadi" appear in MULTIPLE subsidiary names and would create
# false mappings (e.g. "limited" → CMPDIL because it appears last in the loop).
# Instead we maintain a curated set of unambiguous single-word triggers.
SUBSIDIARY_ALIASES: dict[str, str] = {
    # Short codes  (primary, always safe)
    "ecl":    "ECL",
    "bccl":   "BCCL",
    "ccl":    "CCL",
    "ncl":    "NCL",
    "wcl":    "WCL",
    "secl":   "SECL",
    "mcl":    "MCL",
    "nec":    "NEC",
    "cil":    "CIL",
    "cmpdi":  "CMPDI",
    "cmpdil": "CMPDIL",

    # Unambiguous unique tokens from full names
    "bharat":   "BCCL",    # Bharat Coking Coal Limited → BCCL
    "coking":   "BCCL",    # only BCCL is "coking"
    "mahanadi": "MCL",     # Mahanadi Coalfields Limited → MCL
    "secl":     "SECL",    # South Eastern Coalfields
}


# ─── Mining Activity Types ─────────────────────────────────────────
ACTIVITY_TYPES = {
    # Production & dispatch
    "production":               "coal_production",
    "dispatch":                 "coal_dispatch",
    "offtake":                  "coal_offtake",
    "washery production":       "washery_production",

    # Exploration
    "exploration":              "exploration",
    "detailed exploration":     "detailed_exploration",
    "regional exploration":     "regional_exploration",
    "promotional exploration":  "promotional_exploration",

    # Drilling
    "drilling":                 "drilling",
    "core drilling":            "core_drilling",
    "borehole":                 "borehole",
    "bore hole":                "borehole",

    # Seismic surveys
    "seismic":                  "seismic_survey",
    "2d seismic":               "seismic_2d",
    "3d seismic":               "seismic_3d",
    "2d/3d seismic":            "seismic_2d3d",

    # Geological
    "geological report":        "geological_report",
    "geo report":               "geological_report",
    "resources":                "coal_resources",
    "reserves":                 "coal_reserves",
    "coal resources":           "coal_resources",
    "coal reserves":            "coal_reserves",

    # Financial
    "turnover":                 "financial_turnover",
    "profit":                   "financial_profit",
    "revenue":                  "financial_revenue",
    "capex":                    "capital_expenditure",

    # Infrastructure
    "projects":                 "projects",
    "mines":                    "mine_count",
    "collieries":               "colliery_count",
    "washeries":                "washery_count",
    "employees":                "employee_count",
    "manpower":                 "employee_count",
}


# ─── Resource Classification ──────────────────────────────────────
RESOURCE_CATEGORIES = {
    "measured":   "Measured Resources",
    "indicated":  "Indicated Resources",
    "inferred":   "Inferred Resources",
    "proved":     "Proved Reserves",
    "probable":   "Probable Reserves",
}


# ─── Unit Mappings (raw → canonical) ─────────────────────────────
UNIT_CANONICAL: dict[str, str] = {
    # Mass / production
    "mt":       "MT",
    "million tonnes": "MT",
    "million tons":   "MT",
    "bt":       "BT",
    "billion tonnes": "BT",
    "lakh tonnes":    "lakh_T",
    "lakh t":         "lakh_T",
    "kt":       "KT",
    "thousand tonnes":"KT",
    "t":        "T",
    "tonnes":   "T",
    "ton":      "T",

    # Length
    "line km":  "line_km",
    "lkm":      "line_km",
    "km":       "km",
    "m":        "m",
    "metre":    "m",
    "meter":    "m",

    # Area
    "sq km":    "sq_km",
    "km2":      "sq_km",
    "sq m":     "sq_m",
    "hectare":  "ha",
    "ha":       "ha",

    # Currency
    "crore":    "INR_crore",
    "cr":       "INR_crore",
    "crores":   "INR_crore",
    "lakh":     "INR_lakh",
    "rs crore": "INR_crore",
    "inr crore":"INR_crore",

    # Energy
    "mw":       "MW",
    "gwh":      "GWh",
    "kwh":      "kWh",

    # Count
    "nos":      "count",
    "no.":      "count",
    "numbers":  "count",
}

# Conversion to MT (for normalization)
UNIT_TO_MT: dict[str, float] = {
    "MT":       1.0,
    "BT":       1000.0,
    "lakh_T":   0.0001,
    "KT":       0.001,
    "T":        0.000001,
}


# ─── Time Period Patterns ─────────────────────────────────────────
# Used by extractor to normalise year references
PERIOD_PATTERNS = [
    # FY like "2023-24", "2023–24"
    r"(\d{4})[-–](\d{2,4})",
    # Plain year "2024"
    r"\b(20\d{2})\b",
    # "FY 2024", "FY2024"
    r"FY\s?(\d{4})",
    # "Q1 2024", "Q3 FY2023"
    r"Q([1-4])\s+(?:FY\s?)?(\d{4})",
]


# ─── Document Type Vocabulary ─────────────────────────────────────
DOCUMENT_TYPES = {
    "annual_report":        "Annual Report",
    "geological_report":    "Geological Report",
    "exploration_report":   "Exploration Report",
    "project_report":       "Project Report",
    "parliamentary_question":"Parliamentary Question",
    "ministry_letter":      "Ministry Communication",
    "gazette_notification": "Gazette Notification",
    "internal_memo":        "Internal Memo / Note",
    "data_sheet":           "Data Sheet / Excel",
    "presentation":         "Presentation",
    "other":                "Other",
}

# Document type priority for conflict resolution (higher index = higher priority)
DOC_TYPE_PRIORITY = [
    "other",
    "internal_memo",
    "data_sheet",
    "presentation",
    "exploration_report",
    "project_report",
    "geological_report",
    "parliamentary_question",
    "ministry_letter",
    "gazette_notification",
    "annual_report",
]


# ─── Signal Keywords (for router.py) ──────────────────────────────
MINING_QUERY_SIGNALS = [
    # Entities
    "coal", "cil", "cmpdi", "ecl", "bccl", "ccl", "ncl", "wcl", "secl",
    "mcl", "nec", "mine", "colliery", "washery",
    # Activities
    "production", "dispatch", "exploration", "drilling", "seismic",
    "geological", "resources", "reserves", "survey", "borehole",
    # Document types
    "annual report", "geological report", "parliamentary",
    # Metrics
    "target", "achievement", "offtake", "turnover", "profit",
    # Time
    "2020", "2021", "2022", "2023", "2024", "2025",
    "fy", "quarter", "yearly", "trend", "historical",
]

REPORT_GENERATION_SIGNALS = [
    "prepare", "generate", "create report", "draft", "compile",
    "write report", "summarize", "parliamentary response",
    "briefing note", "status report",
]

ANALYTICS_SIGNALS = [
    "trend", "compare", "comparison", "growth", "decline",
    "word cloud", "topics", "historical", "year over year",
    "chart", "graph", "visualize", "analytics",
]
