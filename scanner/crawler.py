import os
import hashlib
import logging
import threading
from urllib.parse import urlparse, urljoin, urldefrag
from collections import deque

from playwright.sync_api import sync_playwright

from config import Config

logger = logging.getLogger(__name__)


class SiteCrawler:
    """Crawls a web application using Playwright, capturing screenshots and DOM structure."""

    def __init__(self, scan_id, url, max_pages=None, progress_callback=None, crawl_mode="bfs"):
        self.scan_id = scan_id
        self.base_url = url.rstrip("/")
        self.max_pages = max_pages or Config.MAX_PAGES
        self.progress_callback = progress_callback
        self.crawl_mode = crawl_mode

        self.visited = set()
        self.pages = []
        self.queue = deque()

        # Threading event: the background thread waits on this before crawling
        self._begin_event = threading.Event()

        self.output_dir = os.path.join(Config.OUTPUT_DIR, scan_id)
        self.screenshots_dir = os.path.join(self.output_dir, "screenshots")
        os.makedirs(self.screenshots_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def crawl(self):
        """
        Two-phase crawl:
        1) Launch a HEADED browser at the target URL and wait for the user to log in.
        2) When begin() is called (sets _begin_event), capture the current URL
           and start the BFS crawl with the authenticated session.
        """
        with sync_playwright() as pw:
            # Phase 1 — Open a visible browser for the user
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={"width": Config.SCREENSHOT_WIDTH, "height": Config.SCREENSHOT_HEIGHT},
                ignore_https_errors=True,
            )
            page = context.new_page()

            try:
                page.goto(self.base_url, wait_until="domcontentloaded", timeout=Config.CRAWL_TIMEOUT)
            except Exception as exc:
                logger.warning("Initial navigation failed: %s", exc)

            logger.info("Browser opened at %s — waiting for user to log in...", self.base_url)

            # Notify the caller that the browser is open
            if self.progress_callback:
                self.progress_callback(
                    visited=0,
                    total_queued=0,
                    current_url=self.base_url,
                    page_title="Browser opened — please log in",
                )

            # Wait until begin() is called (or timeout after 10 minutes)
            self._begin_event.wait(timeout=600)

            if not self._begin_event.is_set():
                logger.warning("Timed out waiting for user to begin crawl.")
                browser.close()
                return self.pages

            # Phase 2 — Grab the current URL (user may have navigated) and crawl
            start_url = page.url or self.base_url
            logger.info("User confirmed — starting crawl from %s", start_url)

            # Update base_url to where the user ended up (preserves same-domain check)
            self.base_url = self._derive_base_url(start_url)

            # Crawl the current page first (user is already on it)
            self._crawl_page(page, start_url, trigger_action="Initial Base Page", trigger_element_text="")

            if self.crawl_mode == "interactive":
                logger.info("Starting interactive menu crawl...")
                self._crawl_interactive_menus(page, start_url)
            else:
                logger.info("Starting BFS link crawl...")
                # BFS crawl remaining pages
                while self.queue and len(self.visited) < self.max_pages:
                    url = self.queue.popleft()
                    if url in self.visited:
                        continue
                    self._crawl_page(page, url)

            browser.close()

        return self.pages

    def begin(self):
        """Signal that the user has finished logging in and crawling should start."""
        self._begin_event.set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _crawl_interactive_menus(self, page, start_url):
        """Interactively identify and click menu items, resetting to base page each time."""
        # 1. Identify interactive elements on the base page
        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=Config.CRAWL_TIMEOUT)
            page.wait_for_timeout(2000)
            
            # Find interactive elements (navs, headers, buttons)
            menu_items = page.evaluate(r"""() => {
                const results = [];
                const selectors = ["nav a", "nav button", "header a", "header button", "[role='menuitem']", ".nav-link", ".menu-item"];
                const elements = document.querySelectorAll(selectors.join(', '));
                
                elements.forEach((el, index) => {
                    const text = (el.textContent || el.innerText || el.value || '').trim().substring(0, 100);
                    // Filter out empty or invisible items (basic check)
                    if (text && el.offsetWidth > 0 && el.offsetHeight > 0) {
                        results.push({ index: index, text: text, tag: el.tagName.toLowerCase() });
                    }
                });
                return results;
            }""")
        except Exception as exc:
            logger.warning("Failed to extract interactive menus from %s: %s", start_url, exc)
            menu_items = []

        logger.info("Found %d interactive menu items to crawl.", len(menu_items))

        # 2. Iterate and click
        for i, item in enumerate(menu_items):
            if len(self.visited) >= self.max_pages:
                break

            try:
                # Reset to base page
                if page.url != start_url:
                    page.goto(start_url, wait_until="domcontentloaded", timeout=Config.CRAWL_TIMEOUT)
                    page.wait_for_timeout(1000)

                logger.info("Clicking interactive menu item %d/%d: '%s'", i + 1, len(menu_items), item["text"])
                
                # We need to re-locate the element since the DOM might have changed after navigation
                # We use a JS evaluation to click the Nth valid interactive item.
                clicked = page.evaluate(r"""(targetIndex) => {
                    const selectors = ["nav a", "nav button", "header a", "header button", "[role='menuitem']", ".nav-link", ".menu-item"];
                    const elements = document.querySelectorAll(selectors.join(', '));
                    let validIndex = -1;
                    
                    for (let el of elements) {
                        const text = (el.textContent || el.innerText || el.value || '').trim();
                        if (text && el.offsetWidth > 0 && el.offsetHeight > 0) {
                            validIndex++;
                            if (validIndex === targetIndex) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }""", item["index"])

                if not clicked:
                    logger.warning("Element '%s' not found for clicking.", item["text"])
                    continue

                # Wait for any network or DOM updates
                page.wait_for_timeout(3000)
                
                # We treat the current page state as a new view
                # To maintain uniqueness in our BFS visited list if the URL didn't change (e.g., SPA modal),
                # we'll use a derived virtual URL if the actual URL remains the same
                virtual_url = page.url
                if virtual_url == start_url:
                    virtual_url = f"{start_url}#interaction_{i}_{hashlib.md5(item['text'].encode()).hexdigest()[:6]}"
                
                trigger_action = f"Clicked menu item '{item['text']}'"
                self._crawl_page(page, virtual_url, trigger_action=trigger_action, trigger_element_text=item['text'])

            except Exception as exc:
                logger.warning("Error interacting with menu item '%s': %s", item["text"], exc)

    def _crawl_page(self, page, url, trigger_action=None, trigger_element_text=None):
        """Visit a single page: screenshot, extract info, discover links."""
        canonical = self._canonicalize(url)
        if canonical in self.visited:
            return
        self.visited.add(canonical)

        # Only navigate if we aren't already on this page (and it's not a virtual URL)
        current = self._canonicalize(page.url)
        if current != canonical and not url.startswith(current + "#interaction_"):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=Config.CRAWL_TIMEOUT)
                page.wait_for_timeout(2000)
            except Exception as exc:
                logger.warning("Failed to load %s: %s", url, exc)
                return
        else:
            # Still wait a beat for any dynamic content
            page.wait_for_timeout(1000)

        # Capture screenshot
        screenshot_filename = self._screenshot_name(url)
        screenshot_path = os.path.join(self.screenshots_dir, screenshot_filename)
        try:
            page.screenshot(path=screenshot_path, full_page=True)
        except Exception as exc:
            logger.warning("Screenshot failed for %s: %s", url, exc)
            screenshot_path = None

        # Extract structured page data
        page_info = self._extract_page_info(page, page.url)
        page_info["screenshot"] = screenshot_path
        page_info["screenshot_filename"] = screenshot_filename
        
        if trigger_action:
            page_info["trigger_action"] = trigger_action
        if trigger_element_text:
            page_info["trigger_element_text"] = trigger_element_text
            
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

    def _derive_base_url(self, current_url):
        """Derive base URL (scheme + domain) from wherever the user ended up."""
        parsed = urlparse(current_url)
        return f"{parsed.scheme}://{parsed.netloc}"
