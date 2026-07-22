import os

from dotenv import load_dotenv

load_dotenv()

FINNHUB_API_KEY = os.getenv(
    "FINNHUB_API_KEY",
    "",
)

OPENROUTER_API_KEY = os.getenv(
    "OPENROUTER_API_KEY",
    "",
)

OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "openai/gpt-4o-mini",
)

ALPHA_VANTAGE_API_KEY = os.getenv(
    "ALPHA_VANTAGE_API_KEY",
    "",
)

POLYGON_API_KEY = os.getenv(
    "POLYGON_API_KEY",
    "",
)