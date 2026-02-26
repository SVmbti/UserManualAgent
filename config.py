import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "user-manual-agent-dev-key")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    MAX_PAGES = int(os.getenv("MAX_PAGES", "50"))
    SCREENSHOT_WIDTH = 1280
    SCREENSHOT_HEIGHT = 900
    CRAWL_TIMEOUT = int(os.getenv("CRAWL_TIMEOUT", "30000"))  # ms
    HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
