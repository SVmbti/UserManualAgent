import os
import uuid
import logging
import threading

from flask import Flask, render_template, request, jsonify, send_from_directory, abort

from config import Config
from scanner.crawler import SiteCrawler
from analyzer.page_analyzer import PageAnalyzer
from generator.manual_builder import ManualBuilder

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

os.makedirs(Config.OUTPUT_DIR, exist_ok=True)

# In-memory scan state  {scan_id: {...}}
scans = {}

# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scan/<scan_id>/progress")
def progress_page(scan_id):
    if scan_id not in scans:
        abort(404)
    return render_template("progress.html", scan_id=scan_id)


@app.route("/scan/<scan_id>/manual")
def manual_page(scan_id):
    scan = scans.get(scan_id)
    if not scan or scan["status"] != "done":
        abort(404)
    html_path = scan.get("manual_html", "")
    if html_path and os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            manual_content = f.read()
        return render_template("manual_view.html", scan_id=scan_id, manual_html=manual_content)
    abort(404)


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------


@app.route("/scan", methods=["POST"])
def start_scan():
    data = request.get_json() or request.form
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    max_pages = int(data.get("max_pages", Config.MAX_PAGES))

    # Build auth config
    auth_type = data.get("auth_type", "none")
    auth_config = {"type": auth_type}
    if auth_type == "form":
        auth_config.update({
            "login_url": data.get("login_url", ""),
            "username": data.get("username", ""),
            "password": data.get("password", ""),
            "username_selector": data.get("username_selector", ""),
            "password_selector": data.get("password_selector", ""),
            "submit_selector": data.get("submit_selector", ""),
        })
    elif auth_type == "basic":
        auth_config.update({
            "username": data.get("username", ""),
            "password": data.get("password", ""),
        })
    elif auth_type == "cookies":
        auth_config.update({
            "cookies": data.get("cookies", ""),
            "login_url": data.get("login_url", url),
        })

    scan_id = str(uuid.uuid4())[:8]
    scans[scan_id] = {
        "status": "starting",
        "url": url,
        "visited": 0,
        "total_queued": 0,
        "current_url": "",
        "current_title": "",
        "pages": [],
        "error": None,
        "manual_html": None,
        "manual_md": None,
        "phase": "crawling",
    }

    thread = threading.Thread(target=_run_scan, args=(scan_id, url, auth_config, max_pages), daemon=True)
    thread.start()

    return jsonify({"scan_id": scan_id, "status_url": f"/scan/{scan_id}/status"})


@app.route("/scan/<scan_id>/status")
def scan_status(scan_id):
    scan = scans.get(scan_id)
    if not scan:
        return jsonify({"error": "Scan not found"}), 404
    return jsonify({
        "status": scan["status"],
        "phase": scan.get("phase", ""),
        "visited": scan["visited"],
        "total_queued": scan["total_queued"],
        "current_url": scan["current_url"],
        "current_title": scan.get("current_title", ""),
        "page_count": len(scan["pages"]),
        "error": scan["error"],
        "manual_url": f"/scan/{scan_id}/manual" if scan["status"] == "done" else None,
    })


@app.route("/scan/<scan_id>/download/<fmt>")
def download_manual(scan_id, fmt):
    scan = scans.get(scan_id)
    if not scan or scan["status"] != "done":
        abort(404)
    if fmt == "html":
        path = scan.get("manual_html", "")
    elif fmt == "md" or fmt == "markdown":
        path = scan.get("manual_md", "")
    else:
        abort(400)

    if path and os.path.exists(path):
        directory = os.path.dirname(path)
        filename = os.path.basename(path)
        return send_from_directory(directory, filename, as_attachment=True)
    abort(404)


@app.route("/output/<path:filepath>")
def serve_output(filepath):
    return send_from_directory(Config.OUTPUT_DIR, filepath)


# ---------------------------------------------------------------------------
# Background scan runner
# ---------------------------------------------------------------------------


def _run_scan(scan_id, url, auth_config, max_pages):
    scan = scans[scan_id]
    scan["status"] = "crawling"
    scan["phase"] = "crawling"

    def on_progress(visited, total_queued, current_url, page_title):
        scan["visited"] = visited
        scan["total_queued"] = total_queued
        scan["current_url"] = current_url
        scan["current_title"] = page_title

    try:
        # Phase 1: Crawl
        crawler = SiteCrawler(
            scan_id=scan_id,
            url=url,
            auth_config=auth_config,
            max_pages=max_pages,
            progress_callback=on_progress,
        )
        pages = crawler.crawl()
        scan["pages"] = pages
        scan["visited"] = len(pages)

        if not pages:
            scan["status"] = "done"
            scan["error"] = "No pages were discovered."
            return

        # Phase 2: Analyze
        scan["status"] = "analyzing"
        scan["phase"] = "analyzing"
        analyzer = PageAnalyzer(openai_api_key=Config.OPENAI_API_KEY)
        for i, page_info in enumerate(pages):
            scan["current_url"] = page_info.get("url", "")
            scan["current_title"] = f"Analyzing page {i + 1}/{len(pages)}"
            analyzer.analyze(page_info)

        # Phase 3: Build manual
        scan["status"] = "generating"
        scan["phase"] = "generating"
        scan["current_title"] = "Generating user manual..."
        output_dir = os.path.join(Config.OUTPUT_DIR, scan_id)
        builder = ManualBuilder(scan_id=scan_id, base_url=url, output_dir=output_dir)
        paths = builder.build(pages)

        scan["manual_html"] = paths["html"]
        scan["manual_md"] = paths["markdown"]
        scan["status"] = "done"
        scan["phase"] = "done"
        scan["current_title"] = "Complete!"
        logger.info("Scan %s complete. %d pages processed.", scan_id, len(pages))

    except Exception as exc:
        logger.exception("Scan %s failed: %s", scan_id, exc)
        scan["status"] = "error"
        scan["error"] = str(exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
