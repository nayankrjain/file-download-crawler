import os
import re
import time
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from dotenv import load_dotenv
import os

# Load environment variables from .env in the project root
load_dotenv()

# (Now your existing os.getenv("BASE_URL") etc. will work)

# ---------------------------
# Env / Config
# ---------------------------
BASE_URL                 = os.getenv("BASE_URL", "").rstrip("/")
LOGIN_URL                = os.getenv("LOGIN_URL", "").strip()
DOCS_URL                 = os.getenv("DOCS_URL", "").strip()

USERNAME                 = os.getenv("USERNAME", "")
PASSWORD                 = os.getenv("PASSWORD", "")

# CSS selectors (customize per site)
USER_SELECTOR            = os.getenv("USER_SELECTOR", "input[name='username']")
PASS_SELECTOR            = os.getenv("PASS_SELECTOR", "input[name='password']")
SUBMIT_SELECTOR          = os.getenv("SUBMIT_SELECTOR", "button[type='submit'],input[type='submit']")

# Where to find folders & files in the documents area
FOLDER_LINK_SELECTOR     = os.getenv("FOLDER_LINK_SELECTOR", "a.folder, a[role='treeitem'][data-type='folder']")
FILE_LINK_SELECTOR       = os.getenv("FILE_LINK_SELECTOR", "a.file, a[download], a[data-type='file']")

# Optional: a selector for the "current folder" name, useful for naming
CURRENT_FOLDER_SELECTOR  = os.getenv("CURRENT_FOLDER_SELECTOR", "")

# Optional: restrict crawling to same origin
RESTRICT_TO_DOMAIN       = os.getenv("RESTRICT_TO_DOMAIN", "true").lower() == "true"

# Rate limits / timing
NAV_TIMEOUT_MS           = int(os.getenv("NAV_TIMEOUT_MS", "30000"))
POST_LOGIN_WAIT_MS       = int(os.getenv("POST_LOGIN_WAIT_MS", "1500"))
CRAWL_DELAY_MS           = int(os.getenv("CRAWL_DELAY_MS", "300"))

# Download & state
DOWNLOAD_ROOT            = Path(os.getenv("DOWNLOAD_ROOT", "/downloads"))
STATE_FILE               = Path(os.getenv("STATE_FILE", "/state/downloaded.json"))

# --------------------------------
# Helpers
# --------------------------------
def sanitize_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)  # Windows-invalid chars + common bad ones
    name = re.sub(r"\s+", " ", name)
    return name[:200] if len(name) > 200 else name

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def same_origin(url: str, base: str) -> bool:
    if not RESTRICT_TO_DOMAIN:
        return True
    try:
        return urlparse(url).netloc == urlparse(base).netloc
    except Exception:
        return True

def load_state() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()

def save_state(done: set) -> None:
    ensure_dir(STATE_FILE.parent)
    STATE_FILE.write_text(json.dumps(sorted(done), indent=2))

# --------------------------------
# Core
# --------------------------------
def login(page):
    if not LOGIN_URL:
        return
    page.goto(LOGIN_URL, timeout=NAV_TIMEOUT_MS)
    page.wait_for_load_state("domcontentloaded")
    if USER_SELECTOR:
        page.fill(USER_SELECTOR, USERNAME)
    if PASS_SELECTOR:
        page.fill(PASS_SELECTOR, PASSWORD)
    if SUBMIT_SELECTOR:
        page.click(SUBMIT_SELECTOR)
    # small wait for redirects/messages
    page.wait_for_timeout(POST_LOGIN_WAIT_MS)

def collect_links(page, folder_selector, file_selector):
    folders = []
    files   = []
    # folders
    if folder_selector:
        for el in page.query_selector_all(folder_selector):
            href = el.get_attribute("href") or ""
            text = (el.inner_text() or "").strip()
            if href:
                folders.append((href, text))
    # files
    if file_selector:
        for el in page.query_selector_all(file_selector):
            href = el.get_attribute("href") or ""
            text = (el.inner_text() or "").strip()
            # some file links have explicit download attr; use text or download filename
            dl_name = el.get_attribute("download") or text
            if href:
                files.append((href, dl_name))
    return folders, files

def get_current_folder_name(page):
    if not CURRENT_FOLDER_SELECTOR:
        return ""
    el = page.query_selector(CURRENT_FOLDER_SELECTOR)
    if not el:
        return ""
    return (el.inner_text() or "").strip()

def crawl_documents(context):
    page = context.new_page()
    done = load_state()
    ensure_dir(DOWNLOAD_ROOT)

    # 1) Login
    login(page)

    # 2) Start at docs root
    start = DOCS_URL or BASE_URL
    if not start:
        raise SystemExit("Please set BASE_URL and DOCS_URL/LOGIN_URL env vars")

    # BFS style queue: list of (url, relative_path)
    queue = [(start, Path("."))]
    visited = set()

    while queue:
        url, rel_path = queue.pop(0)
        abs_url = url if url.startswith("http") else urljoin(BASE_URL + "/", url)

        if abs_url in visited:
            continue
        if RESTRICT_TO_DOMAIN and not same_origin(abs_url, BASE_URL or abs_url):
            continue

        print(f"[NAV] {abs_url}")
        try:
            page.goto(abs_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            print(f"[WARN] Timeout navigating to {abs_url}")
            continue

        visited.add(abs_url)
        time.sleep(CRAWL_DELAY_MS / 1000.0)

        # Determine current folder (optional)
        current_folder = get_current_folder_name(page)
        effective_rel = rel_path / sanitize_filename(current_folder) if current_folder else rel_path
        ensure_dir(DOWNLOAD_ROOT / effective_rel)

        # Collect subfolders & files
        folders, files = collect_links(page, FOLDER_LINK_SELECTOR, FILE_LINK_SELECTOR)

        # Enqueue subfolders
        for href, text in folders:
            next_url = href if href.startswith("http") else urljoin(abs_url, href)
            if RESTRICT_TO_DOMAIN and not same_origin(next_url, BASE_URL or abs_url):
                continue
            next_rel = effective_rel / sanitize_filename(text or "folder")
            queue.append((next_url, next_rel))

        # Download files
        for href, text in files:
            file_url = href if href.startswith("http") else urljoin(abs_url, href)
            if file_url in done:
                print(f"[SKIP] Already downloaded: {file_url}")
                continue

            print(f"[DOWNLOAD] {file_url}")
            # Use Playwright's download handling when clicking is required
            try:
                with page.expect_download(timeout=NAV_TIMEOUT_MS):
                    # Create a temporary clickable link in DOM (works even if original link is off-screen)
                    page.evaluate("""(u)=>{ const a=document.createElement('a'); a.href=u; a.target='_self'; document.body.appendChild(a); a.click(); a.remove(); }""", file_url)
                download = page.wait_for_event("download", timeout=NAV_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                # Some sites start download via navigation; try navigating directly
                try:
                    resp = page.goto(file_url, timeout=NAV_TIMEOUT_MS)
                    # If navigation returns a document instead of a download, try saving its content
                    if resp and resp.ok:
                        suggested = sanitize_filename(os.path.basename(urlparse(file_url).path) or (text or "file"))
                        out_path = (DOWNLOAD_ROOT / effective_rel / suggested)
                        ensure_dir(out_path.parent)
                        content = resp.body()
                        out_path.write_bytes(content)
                        print(f"[SAVED] {out_path}")
                        done.add(file_url)
                        save_state(done)
                    continue
                except Exception as e:
                    print(f"[ERROR] Direct fetch failed: {e}")
                    continue
            else:
                # Save with site-suggested filename
                suggested = sanitize_filename(download.suggested_filename or (text or "file"))
                out_path = (DOWNLOAD_ROOT / effective_rel / suggested)
                ensure_dir(out_path.parent)
                download.save_as(str(out_path))
                print(f"[SAVED] {out_path}")
                done.add(file_url)
                save_state(done)

    page.close()

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Constrain downloads to our root
        context = browser.new_context(accept_downloads=True)
        # Playwright saves downloads to a temp dir; we call save_as() to move into DOWNLOAD_ROOT
        try:
            crawl_documents(context)
        finally:
            context.close()
            browser.close()

if __name__ == "__main__":
    main()
