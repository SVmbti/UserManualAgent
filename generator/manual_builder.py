import base64
import os
import logging
from datetime import datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class ManualBuilder:
    """Builds a user manual document from analyzed page data."""

    def __init__(self, scan_id, base_url, output_dir):
        self.scan_id = scan_id
        self.base_url = base_url
        self.output_dir = output_dir
        self.app_name = urlparse(base_url).netloc or "Web Application"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, pages):
        """Build and save the manual in HTML and Markdown formats. Returns paths."""
        # Sort pages: put main/index page first, then alphabetically
        pages = self._sort_pages(pages)

        md_content = self._build_markdown(pages)
        html_content = self._build_html(pages, md_content)

        md_path = os.path.join(self.output_dir, "user_manual.md")
        html_path = os.path.join(self.output_dir, "user_manual.html")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info("Manual saved: %s, %s", md_path, html_path)
        return {"html": html_path, "markdown": md_path}

    # ------------------------------------------------------------------
    # Markdown builder
    # ------------------------------------------------------------------

    def _build_markdown(self, pages):
        lines = []
        lines.append(f"# User Manual — {self.app_name}")
        lines.append("")
        lines.append(f"*Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
        lines.append(f"*Source: {self.base_url}*")
        lines.append("")

        # Table of contents
        lines.append("## Table of Contents")
        lines.append("")
        for i, page in enumerate(pages, 1):
            title = page.get("title", "") or page.get("url", "Untitled")
            anchor = f"section-{i}"
            lines.append(f"{i}. [{title}](#{anchor})")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Page sections
        for i, page in enumerate(pages, 1):
            title = page.get("title", "") or "Untitled Page"
            url = page.get("url", "")
            purpose = page.get("page_purpose", "")
            features = page.get("key_features", [])
            howto = page.get("how_to_use", [])
            screenshot_filename = page.get("screenshot_filename", "")
            trigger_action = page.get("trigger_action", "")

            lines.append(f"## {i}. {title} {{#{f'section-{i}'}}}")
            lines.append("")
            lines.append(f"**URL:** `{url}`")
            lines.append("")

            if trigger_action:
                lines.append(f"**How to access:** {trigger_action}")
                lines.append("")

            if screenshot_filename:
                lines.append(f"![{title}](screenshots/{screenshot_filename})")
                lines.append("")

            if purpose:
                lines.append(f"**Overview:** {purpose}")
                lines.append("")

            if features:
                lines.append("### Key Features")
                lines.append("")
                for feat in features:
                    lines.append(f"- {feat}")
                lines.append("")

            if howto:
                lines.append("### How to Use")
                lines.append("")
                for step_idx, step in enumerate(howto, 1):
                    lines.append(f"{step_idx}. {step}")
                lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # HTML builder (self-contained)
    # ------------------------------------------------------------------

    def _build_html(self, pages, md_content):
        """Build a self-contained HTML manual with embedded screenshots."""
        # Build page sections
        sections_html = []
        toc_items = []

        for i, page in enumerate(pages, 1):
            title = page.get("title", "") or "Untitled Page"
            url = page.get("url", "")
            purpose = page.get("page_purpose", "")
            features = page.get("key_features", [])
            howto = page.get("how_to_use", [])
            screenshot_path = page.get("screenshot", "")
            trigger_action = page.get("trigger_action", "")

            anchor = f"section-{i}"
            toc_items.append(f'<li><a href="#{anchor}">{title}</a></li>')

            # Embed screenshot as base64
            img_html = ""
            if screenshot_path and os.path.exists(screenshot_path):
                try:
                    with open(screenshot_path, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode()
                    img_html = f'<div class="screenshot"><img src="data:image/png;base64,{img_b64}" alt="{title}"></div>'
                except Exception:
                    pass

            features_html = ""
            if features:
                items = "".join(f"<li>{feat}</li>" for feat in features)
                features_html = f'<div class="features"><h3>Key Features</h3><ul>{items}</ul></div>'

            howto_html = ""
            if howto:
                items = "".join(f"<li>{step}</li>" for step in howto)
                howto_html = f'<div class="howto"><h3>How to Use</h3><ol>{items}</ol></div>'

            sections_html.append(f"""
            <section id="{anchor}" class="page-section">
                <h2>{i}. {title}</h2>
                <p class="page-url"><strong>URL:</strong> <code>{url}</code></p>
                {f'<p class="page-trigger"><strong>How to access:</strong> {trigger_action}</p>' if trigger_action else ''}
                {img_html}
                {f'<p class="purpose"><strong>Overview:</strong> {purpose}</p>' if purpose else ''}
                {features_html}
                {howto_html}
            </section>
            <hr>
            """)

        toc_html = "\n".join(toc_items)
        body_html = "\n".join(sections_html)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>User Manual — {self.app_name}</title>
    <style>
        :root {{
            --bg: #0f1117;
            --surface: #1a1d27;
            --border: #2a2d3a;
            --text: #e4e6ed;
            --text-secondary: #9198a8;
            --accent: #38bdf8;
            --accent-dim: #1e6fa0;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.7;
            max-width: 960px;
            margin: 0 auto;
            padding: 40px 24px;
        }}
        h1 {{
            font-size: 2rem;
            background: linear-gradient(135deg, var(--accent), #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 8px;
        }}
        .meta {{ color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 32px; }}
        h2 {{
            font-size: 1.5rem;
            color: var(--accent);
            margin: 32px 0 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--border);
        }}
        h3 {{ font-size: 1.15rem; color: var(--text); margin: 20px 0 10px; }}
        .toc {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px 32px;
            margin-bottom: 40px;
        }}
        .toc h2 {{ border: none; margin-top: 0; }}
        .toc ol {{ padding-left: 20px; }}
        .toc li {{ margin: 6px 0; }}
        .toc a {{ color: var(--accent); text-decoration: none; }}
        .toc a:hover {{ text-decoration: underline; }}
        .page-section {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 28px 32px;
            margin: 24px 0;
        }}
        .page-url {{ color: var(--text-secondary); margin-bottom: 16px; }}
        .page-url code {{
            background: var(--bg);
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.85rem;
        }}
        .screenshot {{
            margin: 20px 0;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid var(--border);
        }}
        .screenshot img {{
            width: 100%;
            display: block;
        }}
        .purpose {{ margin: 16px 0; }}
        ul, ol {{ padding-left: 24px; margin: 8px 0; }}
        li {{ margin: 4px 0; }}
        hr {{
            border: none;
            border-top: 1px solid var(--border);
            margin: 32px 0;
        }}
        code {{
            background: var(--bg);
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.9em;
        }}
        @media print {{
            body {{ background: white; color: #1a1a1a; max-width: none; }}
            .page-section {{ border: 1px solid #ddd; box-shadow: none; }}
            h1 {{ -webkit-text-fill-color: #1a1a1a; background: none; }}
            h2 {{ color: #2563eb; }}
            .screenshot {{ page-break-inside: avoid; }}
        }}
    </style>
</head>
<body>
    <h1>User Manual — {self.app_name}</h1>
    <p class="meta">Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} &bull; Source: {self.base_url}</p>

    <div class="toc">
        <h2>Table of Contents</h2>
        <ol>{toc_html}</ol>
    </div>

    {body_html}

    <footer style="text-align:center; color:var(--text-secondary); padding:40px 0 20px; font-size:0.85rem;">
        Generated by User Manual Agent
    </footer>
</body>
</html>"""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sort_pages(self, pages):
        """Put the root/index page first, then sort by URL path length."""
        def sort_key(p):
            url = p.get("url", "")
            path = urlparse(url).path.rstrip("/")
            # Root page first
            if not path or path == "/":
                return (0, "")
            return (1, path)

        return sorted(pages, key=sort_key)
