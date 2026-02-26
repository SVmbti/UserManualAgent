import os
import hashlib
import logging
from urllib.parse import urlparse, urljoin, urldefrag
from collections import deque

from playwright.sync_api import sync_playwright

from config import Config
from scanner.auth_handler import AuthHandler

logger = logging.getLogger(__name__)


class SiteCrawler:
    """Crawls a web application using Playwright, capturing screenshots and DOM structure."""

    def __init__(self, scan_id, url, auth_config=None, max_pages=None, progress_callback=None):
        self.scan_id = scan_id
        self.base_url = url.rstrip("/")
        self.auth_config = auth_config
        self.max_pages = max_pages or Config.MAX_PAGES
        self.progress_callback = progress_callback

        self.visited = set()
        self.pages = []
        self.queue = deque()

        self.output_dir = os.path.join(Config.OUTPUT_DIR, scan_id)
        self.screenshots_dir = os.path.join(self.output_dir, "screenshots")
        os.makedirs(self.screenshots_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def crawl(self):
        """Run the full crawl and return a list of page info dicts."""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=Config.HEADLESS)
            context = browser.new_context(
                viewport={"width": Config.SCREENSHOT_WIDTH, "height": Config.SCREENSHOT_HEIGHT},
                ignore_https_errors=True,
            )
            page = context.new_page()

            # Authenticate if configured
            if self.auth_config and self.auth_config.get("type") != "none":
                handler = AuthHandler(page, context)
                handler.authenticate(self.auth_config)

            # BFS crawl starting from the base URL
            self.queue.append(self.base_url)
            while self.queue and len(self.visited) < self.max_pages:
                url = self.queue.popleft()
                if url in self.visited:
                    continue
                self._crawl_page(page, url)

            browser.close()

        return self.pages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _crawl_page(self, page, url):
        """Visit a single page: screenshot, extract info, discover links."""
        canonical = self._canonicalize(url)
        if canonical in self.visited:
            return
        self.visited.add(canonical)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=Config.CRAWL_TIMEOUT)
            # Give dynamic content a moment to render
            page.wait_for_timeout(2000)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", url, exc)
            return

        # Capture screenshot
        screenshot_filename = self._screenshot_name(url)
        screenshot_path = os.path.join(self.screenshots_dir, screenshot_filename)
        try:
            page.screenshot(path=screenshot_path, full_page=True)
        except Exception as exc:
            logger.warning("Screenshot failed for %s: %s", url, exc)
            screenshot_path = None

        # Extract structured page data
        page_info = self._extract_page_info(page, url)
        page_info["screenshot"] = screenshot_path
        page_info["screenshot_filename"] = screenshot_filename
        self.pages.append(page_info)

        # Progress callback
        if self.progress_callback:
            self.progress_callback(
                visited=len(self.visited),
                total_queued=len(self.visited) + len(self.queue),
                current_url=url,
                page_title=page_info.get("title", ""),
            )

        # Discover new links and queue them
        links = self._discover_links(page)
        for link in links:
            c = self._canonicalize(link)
            if c not in self.visited and self._should_visit(link):
                self.queue.append(link)

    def _discover_links(self, page):
        """Extract all same-domain href links from the current page."""
        try:
            raw_links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href)",
            )
        except Exception:
            return []

        results = []
        for href in raw_links:
            if not href or href.startswith("javascript:") or href.startswith("mailto:"):
                continue
            absolute = urljoin(page.url, href)
            defragged, _ = urldefrag(absolute)
            if self._is_same_domain(defragged):
                results.append(defragged)
        return list(set(results))

    def _extract_page_info(self, page, url):
        """Run JS in the page to pull structured info from the DOM."""
        try:
            info = page.evaluate(
                r"""() => {
                const data = {
                    title: document.title || '',
                    url: window.location.href,
                    headings: [],
                    forms: [],
                    buttons: [],
                    navigation: [],
                    tables: [],
                    meta_description: '',
                    text_summary: ''
                };

                // Meta description
                const metaDesc = document.querySelector('meta[name="description"]');
                if (metaDesc) data.meta_description = metaDesc.content || '';

                // Headings
                document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => {
                    const text = h.textContent.trim();
                    if (text) data.headings.push({ level: parseInt(h.tagName[1]), text: text.substring(0, 200) });
                });

                // Forms
                document.querySelectorAll('form').forEach(form => {
                    const fields = [];
                    form.querySelectorAll('input,select,textarea').forEach(f => {
                        if (f.type === 'hidden') return;
                        fields.push({
                            tag: f.tagName.toLowerCase(),
                            type: f.type || '',
                            name: f.name || '',
                            placeholder: f.placeholder || '',
                            label: f.labels && f.labels[0] ? f.labels[0].textContent.trim().substring(0, 100) : '',
                            required: f.required || false
                        });
                    });
                    if (fields.length > 0) {
                        data.forms.push({ action: form.action || '', method: (form.method || 'get').toUpperCase(), fields });
                    }
                });

                // Buttons
                document.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"]').forEach(btn => {
                    const text = (btn.textContent || btn.value || '').trim().substring(0, 100);
                    if (text) data.buttons.push({ text, type: btn.type || 'button' });
                });

                // Navigation
                document.querySelectorAll('nav, [role="navigation"]').forEach(nav => {
                    const items = [];
                    nav.querySelectorAll('a').forEach(a => {
                        const text = a.textContent.trim().substring(0, 100);
                        if (text) items.push({ text, href: a.href });
                    });
                    if (items.length > 0) data.navigation.push(items);
                });

                // Tables
                document.querySelectorAll('table').forEach(table => {
                    const headers = [];
                    table.querySelectorAll('th').forEach(th => {
                        headers.push(th.textContent.trim().substring(0, 100));
                    });
                    data.tables.push({ headers, row_count: table.querySelectorAll('tr').length });
                });

                // Text summary
                const main = document.querySelector('main, [role="main"], article') || document.body;
                data.text_summary = main.innerText.trim().substring(0, 3000);

                return data;
            }"""
            )
        except Exception as exc:
            logger.warning("DOM extraction failed for %s: %s", url, exc)
            info = {"title": "", "url": url}

        return info

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _is_same_domain(self, url):
        base_parsed = urlparse(self.base_url)
        target_parsed = urlparse(url)
        return base_parsed.netloc == target_parsed.netloc

    def _should_visit(self, url):
        if not self._is_same_domain(url):
            return False
        skip_ext = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg",
                    ".zip", ".tar", ".gz", ".css", ".js", ".ico",
                    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3"}
        path = urlparse(url).path.lower()
        return not any(path.endswith(ext) for ext in skip_ext)

    def _canonicalize(self, url):
        defragged, _ = urldefrag(url)
        return defragged.rstrip("/")

    def _screenshot_name(self, url):
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        parsed = urlparse(url)
        path_part = parsed.path.strip("/").replace("/", "_")[:40] or "index"
        return f"{path_part}_{url_hash}.png"
