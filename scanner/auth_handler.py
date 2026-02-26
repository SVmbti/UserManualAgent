import json
import logging

logger = logging.getLogger(__name__)


class AuthHandler:
    """Handles authentication for the crawler's browser session."""

    def __init__(self, page, context):
        self.page = page
        self.context = context

    def authenticate(self, auth_config):
        """Dispatch to the appropriate auth method based on config type."""
        auth_type = auth_config.get("type", "none")
        if auth_type == "form":
            self._form_login(auth_config)
        elif auth_type == "basic":
            self._basic_auth(auth_config)
        elif auth_type == "cookies":
            self._cookie_auth(auth_config)
        else:
            logger.info("No authentication configured.")

    # ------------------------------------------------------------------
    # Auth strategies
    # ------------------------------------------------------------------

    def _form_login(self, config):
        """Fill and submit a login form."""
        login_url = config.get("login_url", "")
        username = config.get("username", "")
        password = config.get("password", "")
        username_selector = config.get("username_selector", 'input[name="username"], input[type="email"], #username, #email')
        password_selector = config.get("password_selector", 'input[name="password"], input[type="password"], #password')
        submit_selector = config.get("submit_selector", 'button[type="submit"], input[type="submit"]')

        if not login_url:
            logger.error("Form login requires a login_url.")
            return

        logger.info("Performing form login at %s", login_url)
        self.page.goto(login_url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

        # Try each selector until one works
        self._fill_field(username_selector, username)
        self._fill_field(password_selector, password)

        # Submit
        try:
            submit = self.page.locator(submit_selector).first
            submit.click()
            self.page.wait_for_load_state("domcontentloaded")
            self.page.wait_for_timeout(3000)
            logger.info("Form login submitted. Current URL: %s", self.page.url)
        except Exception as exc:
            logger.error("Failed to submit login form: %s", exc)

    def _basic_auth(self, config):
        """Set HTTP Basic Auth credentials on the browser context."""
        username = config.get("username", "")
        password = config.get("password", "")
        if username:
            logger.info("Setting HTTP Basic Auth credentials.")
            self.context.set_extra_http_headers({
                "Authorization": "Basic " + self._encode_basic(username, password)
            })

    def _cookie_auth(self, config):
        """Inject cookies into the browser context."""
        cookies_raw = config.get("cookies", "")
        if not cookies_raw:
            logger.error("Cookie auth requires cookies data.")
            return

        try:
            if isinstance(cookies_raw, str):
                cookies = json.loads(cookies_raw)
            else:
                cookies = cookies_raw

            # Ensure cookies have required fields
            for cookie in cookies:
                if "url" not in cookie and "domain" not in cookie:
                    cookie["url"] = config.get("login_url", "")

            self.context.add_cookies(cookies)
            logger.info("Injected %d cookies.", len(cookies))
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("Failed to parse cookies: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fill_field(self, selector, value):
        """Try multiple comma-separated selectors to fill a form field."""
        selectors = [s.strip() for s in selector.split(",")]
        for sel in selectors:
            try:
                locator = self.page.locator(sel).first
                if locator.is_visible():
                    locator.fill(value)
                    return True
            except Exception:
                continue
        logger.warning("Could not fill field with selectors: %s", selector)
        return False

    @staticmethod
    def _encode_basic(username, password):
        import base64
        credentials = f"{username}:{password}"
        return base64.b64encode(credentials.encode()).decode()
