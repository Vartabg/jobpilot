"""Static scoring rules for gig revenue matching.

These weights are the default calibration (an AI-application-engineering
tech-stack weighting); a per-user override is a phase-3 follow-up.

Calibrated 2026-05-08 against:
- `data/preferences.json` `tailoring.skill_keywords` (the target stack)
- the user's resume target roles + skills
- a ranked working baseline (Forward Deployed / Applied AI / Agent Builder
  titles dominate)

The 2026-04-30 first cut leaned heavily on full-text keyword counting, which
drove DevOps and Product-Marketing ads to 100/100 because their bodies all
say "we use AI and automation". The current scheme separates two layers:

1. **Title-level signals** (`TITLE_ENGINEERING_PATTERNS`, `TITLE_NEGATIVES`) —
   strong, asymmetric, and decide whether the role is on-track at all. A
   "Senior DevOps Engineer" body cannot rescue itself with description hits.
2. **Full-text skill weights** (`SKILL_WEIGHTS`) — capped contribution; common
   words like "ai" / "automation" / "workflow" are deliberately small so that
   a Marketing ad listing them all doesn't outscore a real AI-engineering
   role.
"""

# Title-level engineering job-title patterns. Match against the lowercased
# title only. Each match adds the weight; a match here also "rescues" the
# role from the TITLE_NEGATIVES cap.
TITLE_ENGINEERING_PATTERNS = {
    # Strongest — exact target role shapes from the default calibration
    "forward deployed": 30,
    "applied ai engineer": 30,
    "applied ai architect": 25,
    "applied ai": 22,
    "ai engineer": 28,
    "llm engineer": 28,
    "agent engineer": 28,
    "agent builder": 26,
    "ai architect": 22,
    "ai automation engineer": 30,
    "ai automation": 24,
    "ai/ml engineer": 22,
    "ml engineer": 18,
    "ai solutions": 18,
    "solutions engineer": 18,
    # NOTE: bare "automation engineer" is deliberately absent — it rescued
    # "Senior QA Automation Engineer" from the QA title cap. AI-flavored
    # automation roles match via "ai automation engineer" / "ai automation".
    "founding engineer": 18,
    "implementation engineer": 20,
    "integration engineer": 16,
    "deployment engineer": 16,
    "technical consultant": 18,
    "independent contractor": 22,
    "freelance": 16,
    "full stack engineer": 12,
    "fullstack engineer": 12,
    "full-stack engineer": 12,
    "software engineer, ai": 22,
    "software engineer - ai": 22,
}

# Title tech-keyword bonuses. Match against title only. These add weight but
# do NOT rescue from TITLE_NEGATIVES — a "Senior Product Manager, Agentic
# Commerce" still gets capped despite "agentic" appearing in the title.
TITLE_TECH_BONUS = {
    "agentic": 8,
    "rag": 12,
    "three.js": 18,
    "threejs": 18,
    "react three fiber": 18,
    "webgl": 12,
    "webgpu": 18,
    "mcp": 14,
}

# Title-level negatives. Match against title only. If any of these match and
# nothing in TITLE_ENGINEERING_PATTERNS matches, the role is hard-capped at
# `TITLE_NEGATIVE_CAP` so description noise can't push it past the threshold.
TITLE_NEGATIVES = {
    # DevOps / SRE / sysadmin — the target stack is AI-application-layer, not infra
    "devops": -45,
    "site reliability": -35,
    "sre engineer": -35,
    "sysadmin": -45,
    "system administrator": -45,
    # Marketing / Sales / PM / BD — non-engineering tracks
    "product marketing": -55,
    "marketing manager": -50,
    "marketing": -30,
    "product manager": -55,
    "project manager": -30,
    "program manager": -30,
    "sales engineer": -25,
    "sales executive": -55,
    "account executive": -55,
    "business development": -45,
    "customer success": -35,
    "customer support": -45,
    "support engineer": -30,
    # QA / test (Playwright is in the target stack but QA isn't the target role)
    "qa engineer": -30,
    "qa automation": -25,
    "test engineer": -30,
    "test automation": -20,
    # Data engineering — adjacent but not the target stack
    "data engineer": -15,
    "data analyst": -25,
    # Senior IC ladder — user wants generalist/contract, not seniority theater
    "senior engineer": -20,
    "senior software": -20,
    "staff engineer": -40,
    # Seniority / experience floors
    "intern": -60,
    "internship": -60,
    "junior": -35,
    "entry level": -40,
    # Adjacent non-targets
    "recruiter": -55,
    "designer": -25,
    "ui/ux": -25,
    "content writer": -50,
}

# Hard cap applied when a title hits TITLE_NEGATIVES with no engineering rescue.
TITLE_NEGATIVE_CAP = 45

# Full-text skill weights. Apply against title + description + tags + company
# (lowercased). Calibrated low for words that appear in almost every modern
# tech ad ("ai", "automation", "workflow") and high for words that genuinely
# differentiate the target stack ("rag", "claude", "mcp", "three.js").
SKILL_WEIGHTS = {
    # AI core stack (preferences.json: claude, anthropic, mcp, agentic, rag)
    "claude": 16,
    "anthropic": 14,
    "openai": 8,
    "llm": 16,
    "rag": 18,
    "retrieval-augmented": 18,
    "agentic": 16,
    "agent": 12,
    "mcp": 18,
    "model context protocol": 18,
    "vector database": 12,
    "vector store": 12,
    "embedding": 8,
    # Browser automation (preferences.json: playwright, browser-use, chrome devtools)
    "playwright": 14,
    "browser-use": 16,
    "chrome devtools": 14,
    # 3D / WebGPU stack (preferences.json: three.js, webgpu, r3f)
    "three.js": 18,
    "threejs": 18,
    "react three fiber": 18,
    "r3f": 14,
    "webgl": 10,
    "webgpu": 18,
    # Web stack (next.js, fastapi, typescript, postgres, vercel, tailscale)
    "next.js": 8,
    "nextjs": 8,
    "fastapi": 8,
    "tailscale": 6,
    # Workflow tools (lower weight — generic ad words)
    "n8n": 16,
    "zapier": 8,
    "make.com": 8,
    "automation": 6,
    "workflow": 6,
    # Generic — kept low so they don't dominate
    "ai": 4,
    "ml": 3,
    "python": 4,
    "typescript": 3,
    # Veteran / clearance signals (still useful for filter)
    "ts/sci": 12,
    "clearance": 8,
    "veteran": 6,
    # Location preferences
    "remote": 4,
    "austin": 6,
    "new york": 3,
    "nyc": 3,
    "queens": 6,
    # Autonomy / async / contract body signals
    "async": 10,
    "flexible hours": 10,
    "flexible schedule": 10,
    "milestone": 8,
    "deliverable": 8,
    "1099": 12,
    "freelance": 10,
    "hourly": 6,
    "project-based": 8,
}

# Cap on total contribution from SKILL_WEIGHTS so a Marketing ad that
# mentions "ai", "automation", "workflow", "agent", and "llm" can't ride
# description noise past the title-cap.
SKILL_WEIGHTS_CAP = 35

# Spam / fee-required indicators.
SCAM_SIGNALS = {
    "work from home opportunity": -40,
    "unlimited earning": -40,
    "no experience needed": -20,
    "training fee": -50,
    "mlm": -50,
    "pyramid": -50,
    "commission only": -25,
    "pay to start": -50,
    "buy materials": -40,
}

# Description-level negatives (kept narrow — title gating handles role-shape).
NEGATIVE_TERMS = {
    "9-5": -18,
    "9 to 5": -18,
    "core hours": -15,
    "daily standup": -12,
    "daily stand-up": -12,
    "must be online": -10,
    "in-office 5 days": -20,
    "on-site 5 days": -20,
    "full-time employee only": -25,
    "w2 only": -20,
    "must be a student": -30,
    "students only": -30,
    "nft project": -25,
    "crypto trading": -20,
    "mlm": -50,
}

# Strong-fit phrases — applied against the title. Mirrors the default
# calibration's target roles and matched-keyword set.
STRONG_FIT_TERMS = (
    "forward deployed",
    "applied ai",
    "ai engineer",
    "llm engineer",
    "agent engineer",
    "agent builder",
    "ai architect",
    "ai automation",
    "solutions engineer",
    # NOTE: bare "automation engineer" is NOT here — QA Automation Engineer
    # would falsely qualify. AI Automation roles match via "ai automation"
    # above instead.
)

# Companies whose roles are differentially valuable in the default
# calibration (AI-native and AI-forward employers top the target list).
DOMAIN_BONUS = {
    "anthropic": 12,
    "palantir": 10,
    "openai": 8,
    "scale ai": 6,
    "mongodb": 4,
    "cloudflare": 6,
    "datadog": 4,
    "stripe": 6,
    "brex": 6,
    "snowflake": 4,
    "databricks": 4,
}

# Words signalling revenue-generating engagement shapes.
REVENUE_TERMS = (
    "rag",
    "llm",
    "agent",
    "n8n",
    "mcp",
    "openai",
    "anthropic",
    "claude",
    "automation",
    "consultant",
    "contract",
    "part-time",
    "hourly",
    "prototype",
    "mvp",
)

# Sources that ship aggregated tech-job listings. Roles from these without a
# title-level engineering signal get penalized — most WWR/RemoteOK postings
# match an "automation" or "ai" keyword incidentally.
JOB_BOARD_SOURCES = {"remoteok", "wwr", "himalayas", "hn"}
