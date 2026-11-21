"""Region settings. Change these three things to move the help line somewhere else."""
import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Tried in order; the first model your key can use is kept. Change if Google renames models.
GEMINI_MODELS = [m.strip() for m in os.getenv("GEMINI_MODEL", "gemini-3.5-flash,gemini-2.5-flash").split(",") if m.strip()]
# Which AI to use: "claude" or "gemini". Left empty, it uses whichever key is filled in (Gemini first).
AI_PROVIDER = os.getenv("AI_PROVIDER", "").lower() or ("gemini" if GEMINI_API_KEY else "claude" if ANTHROPIC_API_KEY else "")
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
CALL_LANGUAGE = os.getenv("CALL_LANGUAGE", "en-US")

# 1. Towns callers come from (name -> lat, lon). Used to work out "nearby".
PLACES = {
    "Lubbock": (33.58, -101.86),
    "Plainview": (34.18, -101.71),
    "Amarillo": (35.22, -101.83),
    "San Angelo": (31.46, -100.44),
    "Stephenville": (32.22, -98.20),
    "College Station": (30.63, -96.33),
    "El Campo": (29.20, -96.27),
    "Victoria": (28.81, -97.00),
    "Uvalde": (29.21, -99.79),
    "Corpus Christi": (27.80, -97.40),
    "Weslaco": (26.16, -97.99),
    "Beaumont": (30.08, -94.13),
}
DEFAULT_PLACE = "College Station"

# 2. Universities to search for researchers. Names are resolved to OpenAlex IDs
#    and coordinates the first time the app needs them, then cached on disk.
UNIVERSITIES = [
    "Texas A&M University",
    "Texas Tech University",
    "The University of Texas at Austin",
    "Texas A&M University-Kingsville",
    "West Texas A&M University",
    "Prairie View A&M University",
    "Oklahoma State University",
    "New Mexico State University",
    "Louisiana State University",
]

# 3. Crops the offline (no-AI) mode can recognise.
CROPS = ["cotton", "sorghum", "corn", "wheat", "rice", "peanut", "pecan",
         "tomato", "pepper", "onion", "citrus", "watermelon", "soybean", "hay"]

# The number farmers can call. Shown in the app. Use your Twilio number here.
HELPLINE_NUMBER = os.getenv("HELPLINE_NUMBER", "")

# Email (any SMTP account, e.g. a Gmail app password)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "") or SMTP_USER

# Demo farmers, researchers and experiences are invented. Set SEED_DEMO=0 to start with none.
SEED_DEMO = os.getenv("SEED_DEMO", "1") != "0"

# Demo safety net: for the one prepared case in demo_case.json, show its saved answer when a live
# AI step fails or takes longer than DEMO_WAIT seconds. The screen labels saved answers.
DEMO_CASE = os.getenv("DEMO_CASE", "1") != "0"
DEMO_WAIT = int(os.getenv("DEMO_WAIT", "60"))
