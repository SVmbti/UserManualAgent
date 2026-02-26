import base64
import logging
import os

logger = logging.getLogger(__name__)


class PageAnalyzer:
    """Analyzes crawled pages and produces human-readable descriptions."""

    def __init__(self, openai_api_key=None):
        self.openai_api_key = openai_api_key
        self._client = None
        if openai_api_key:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=openai_api_key)
                logger.info("OpenAI client initialized — AI analysis enabled.")
            except ImportError:
                logger.warning("openai package not installed. Falling back to structural analysis.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, page_info):
        """Analyze a single page and return enriched info with descriptions."""
        if self._client and page_info.get("screenshot"):
            try:
                ai_result = self._analyze_with_ai(page_info)
                page_info.update(ai_result)
                page_info["analysis_method"] = "ai"
                return page_info
            except Exception as exc:
                logger.warning("AI analysis failed for %s, falling back: %s", page_info.get("url"), exc)

        structural = self._analyze_structurally(page_info)
        page_info.update(structural)
        page_info["analysis_method"] = "structural"
        return page_info

    # ------------------------------------------------------------------
    # AI-powered analysis (OpenAI GPT-4 Vision)
    # ------------------------------------------------------------------

    def _analyze_with_ai(self, page_info):
        screenshot_path = page_info.get("screenshot", "")
        if not screenshot_path or not os.path.exists(screenshot_path):
            raise FileNotFoundError(f"Screenshot not found: {screenshot_path}")

        with open(screenshot_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        dom_summary = self._build_dom_summary(page_info)

        response = self._client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a technical writer creating a user manual for a web application. "
                        "Analyze the provided screenshot and page structure, then produce a clear, "
                        "helpful section for a user manual."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Page title: {page_info.get('title', 'Untitled')}\n"
                                f"URL: {page_info.get('url', '')}\n\n"
                                f"DOM structure:\n{dom_summary}\n\n"
                                "Please provide:\n"
                                "1. **Page Purpose** — A one-sentence description of what this page is for.\n"
                                "2. **Key Features** — A bullet list of the main features/elements on this page.\n"
                                "3. **How to Use** — Step-by-step instructions for using this page.\n"
                                "Format your response in Markdown."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                    ],
                },
            ],
            max_tokens=1000,
            temperature=0.3,
        )

        content = response.choices[0].message.content
        return self._parse_ai_response(content)

    # ------------------------------------------------------------------
    # Structural analysis (no AI)
    # ------------------------------------------------------------------

    def _analyze_structurally(self, page_info):
        """Generate descriptions purely from DOM structure."""
        result = {
            "page_purpose": "",
            "key_features": [],
            "how_to_use": [],
        }

        title = page_info.get("title", "")
        headings = page_info.get("headings", [])
        forms = page_info.get("forms", [])
        buttons = page_info.get("buttons", [])
        tables = page_info.get("tables", [])
        navigation = page_info.get("navigation", [])

        # Determine page type
        page_type = self._classify_page(page_info)
        result["page_type"] = page_type

        # Page purpose
        if title:
            result["page_purpose"] = f"This page is the **{title}** page"
            if page_type:
                result["page_purpose"] += f", serving as a {page_type} page"
            result["page_purpose"] += "."
        elif page_type:
            result["page_purpose"] = f"This is a {page_type} page."

        # Key features
        if navigation:
            nav_items = []
            for nav_group in navigation:
                nav_items.extend([item["text"] for item in nav_group])
            if nav_items:
                result["key_features"].append(
                    f"Navigation menu with links: {', '.join(nav_items[:10])}"
                )

        if forms:
            for i, form in enumerate(forms, 1):
                field_names = [
                    f.get("label") or f.get("placeholder") or f.get("name") or f.get("type")
                    for f in form.get("fields", [])
                ]
                field_names = [n for n in field_names if n]
                result["key_features"].append(
                    f"Form ({form.get('method', 'GET')}) with fields: {', '.join(field_names)}"
                )

        if buttons:
            btn_texts = [b["text"] for b in buttons if b.get("text")]
            if btn_texts:
                result["key_features"].append(f"Action buttons: {', '.join(btn_texts[:10])}")

        if tables:
            for table in tables:
                headers = table.get("headers", [])
                rows = table.get("row_count", 0)
                if headers:
                    result["key_features"].append(
                        f"Data table with {rows} rows — columns: {', '.join(headers[:10])}"
                    )
                else:
                    result["key_features"].append(f"Data table with {rows} rows")

        if headings:
            h1s = [h["text"] for h in headings if h["level"] == 1]
            h2s = [h["text"] for h in headings if h["level"] == 2]
            if h2s:
                result["key_features"].append(f"Sections: {', '.join(h2s[:8])}")

        # How to use
        if page_type == "login":
            result["how_to_use"] = [
                "Enter your credentials in the login form.",
                "Click the submit/login button to authenticate.",
            ]
        elif forms:
            result["how_to_use"].append("Fill in the form fields with the required information.")
            if buttons:
                result["how_to_use"].append(
                    f"Click '{buttons[0]['text']}' to submit."
                )
        if navigation:
            result["how_to_use"].append("Use the navigation menu to access other sections.")

        if not result["how_to_use"]:
            result["how_to_use"].append("Browse the content displayed on this page.")

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _classify_page(self, page_info):
        """Guess the page type from its content."""
        title = (page_info.get("title", "") + " " + page_info.get("text_summary", "")[:500]).lower()
        forms = page_info.get("forms", [])
        tables = page_info.get("tables", [])

        password_fields = any(
            f.get("type") == "password"
            for form in forms
            for f in form.get("fields", [])
        )
        if password_fields:
            return "login"
        if any(w in title for w in ["dashboard", "overview", "home"]):
            return "dashboard"
        if any(w in title for w in ["settings", "preferences", "config"]):
            return "settings"
        if tables:
            return "data listing"
        if forms:
            return "form / data entry"
        if any(w in title for w in ["about", "help", "faq", "contact"]):
            return "informational"
        return "content"

    def _build_dom_summary(self, page_info):
        """Build a concise text summary of the DOM structure for the AI prompt."""
        parts = []
        for h in page_info.get("headings", [])[:15]:
            parts.append(f"{'#' * h['level']} {h['text']}")
        for form in page_info.get("forms", [])[:5]:
            fields_str = ", ".join(
                f.get("label") or f.get("name") or f.get("type", "?")
                for f in form.get("fields", [])
            )
            parts.append(f"Form ({form.get('method', '')}): {fields_str}")
        for btn in page_info.get("buttons", [])[:10]:
            parts.append(f"Button: {btn['text']}")
        for nav in page_info.get("navigation", [])[:3]:
            items = ", ".join(i["text"] for i in nav[:10])
            parts.append(f"Nav: {items}")
        for table in page_info.get("tables", [])[:5]:
            parts.append(f"Table ({table.get('row_count', 0)} rows): {', '.join(table.get('headers', []))}")
        return "\n".join(parts) or "(minimal page structure)"

    def _parse_ai_response(self, content):
        """Parse the AI response into structured fields."""
        result = {
            "page_purpose": "",
            "key_features": [],
            "how_to_use": [],
            "ai_raw": content,
        }

        lines = content.split("\n")
        current_section = None

        for line in lines:
            stripped = line.strip()
            lower = stripped.lower()

            if "page purpose" in lower or "purpose" in lower and stripped.startswith("#"):
                current_section = "purpose"
                continue
            elif "key features" in lower or "features" in lower and stripped.startswith("#"):
                current_section = "features"
                continue
            elif "how to use" in lower or "instructions" in lower and stripped.startswith("#"):
                current_section = "howto"
                continue

            if not stripped:
                continue

            if current_section == "purpose":
                result["page_purpose"] += stripped + " "
            elif current_section == "features":
                if stripped.startswith(("-", "*", "•")):
                    result["key_features"].append(stripped.lstrip("-*• "))
            elif current_section == "howto":
                if stripped.startswith(("-", "*", "•")) or (len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ".)"):
                    import re
                    clean = re.sub(r"^[\d\.\)\-\*•]+\s*", "", stripped)
                    result["how_to_use"].append(clean)

        result["page_purpose"] = result["page_purpose"].strip()

        # If parsing failed, store the whole thing as purpose
        if not result["page_purpose"] and not result["key_features"]:
            result["page_purpose"] = content[:500]

        return result
