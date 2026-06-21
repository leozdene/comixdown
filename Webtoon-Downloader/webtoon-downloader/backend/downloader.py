import os
import time
import io
import base64
import ssl
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from selenium import webdriver

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image
from urllib.parse import urlparse, parse_qs


class WebtoonSSLAdapter(HTTPAdapter):
    """Allows older TLS ciphers — needed for webtoon-phinf.pstatic.net CDN."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs['ssl_context'] = ctx
        super().init_poolmanager(*args, **kwargs)


def _make_session():
    s = requests.Session()
    s.mount('https://', WebtoonSSLAdapter())
    return s


class WebtoonDownloader:
    def __init__(self, progress_callback=None):
        self.driver = None
        self.images = []
        self.progress_callback = progress_callback

    def _log(self, msg):
        print(msg)
        if self.progress_callback:
            self.progress_callback(msg)

    def setup_driver(self):
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        try:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
        except Exception as e:
            raise Exception(f"Failed to initialize ChromeDriver: {str(e)}")

    def normalize_to_list_url(self, url):
        if '/list?' in url:
            return url
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        title_no = params.get('title_no', [None])[0]
        if title_no:
            path_parts = [p for p in parsed.path.rstrip('/').split('/')
                          if not p.startswith('episode') and p != 'viewer' and p != '']
            clean_path = '/' + '/'.join(path_parts)
            return f"https://www.webtoons.com{clean_path}/list?title_no={title_no}"
        return url

    def get_max_page(self, list_url):
        try:
            sep = '&' if '?' in list_url else '?'
            self.driver.get(f"{list_url}{sep}page=9999")
            time.sleep(2)
            for sel in [".paginate strong", ".paginate .on", ".paging .on", "[class*='paginate'] strong"]:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    num = int(el.text.strip())
                    if num > 0:
                        return num
                except:
                    continue
            pager = self.driver.find_elements(By.CSS_SELECTOR,
                ".paginate a, .paginate strong, .paging a, .paging strong")
            nums = []
            for el in pager:
                try:
                    nums.append(int(el.text.strip()))
                except:
                    continue
            return max(nums) if nums else 1
        except:
            return 1

    def get_episodes_on_page(self):
        episodes = []
        seen = set()
        try:
            items = self.driver.find_elements(By.CSS_SELECTOR, "#_listUl li, .detail_lst li")
            for item in items:
                try:
                    link = item.find_element(By.TAG_NAME, "a")
                    ep_url = link.get_attribute('href')
                    if not ep_url or ep_url in seen:
                        continue
                    try:
                        title = item.find_element(By.CSS_SELECTOR, ".subj span").text.strip()
                    except:
                        try:
                            title = item.find_element(By.CSS_SELECTOR, ".subj").text.strip()
                        except:
                            title = ''
                    try:
                        ep_no = item.find_element(By.CSS_SELECTOR, ".tx").text.strip().replace('#', '')
                    except:
                        ep_no = str(len(episodes) + 1)
                    try:
                        thumb = item.find_element(By.CSS_SELECTOR, "img")
                        thumb_url = thumb.get_attribute('src') or thumb.get_attribute('data-src') or ''
                    except:
                        thumb_url = ''
                    try:
                        date = item.find_element(By.CSS_SELECTOR, ".date").text.strip()
                    except:
                        date = ''
                    if not title:
                        title = f"Episode {ep_no}"
                    episodes.append({'title': title, 'url': ep_url, 'episode_no': ep_no,
                                     'thumbnail': thumb_url, 'date': date})
                    seen.add(ep_url)
                except:
                    continue
        except:
            pass
        return episodes

    def get_webtoon_info(self, url):
        try:
            # Detect URL type BEFORE normalising
            parsed_orig = urlparse(url)
            orig_params = parse_qs(parsed_orig.query)
            is_chapter_url = '/viewer?' in url or 'episode_no' in orig_params
            pasted_episode_no = orig_params.get('episode_no', [None])[0]
            pasted_episode_url = url if is_chapter_url else None

            list_url = self.normalize_to_list_url(url)
            self._log(f"Fetching webtoon info from: {list_url}")
            self.setup_driver()
            self.driver.get(list_url)
            WebDriverWait(self.driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(3)

            info = {'title': 'Unknown', 'author': 'Unknown', 'cover_url': '', 'chapters': [],
                    'url_type': 'chapter' if is_chapter_url else 'book'}

            for sel in [".info .subj", "h1.subj", "h1", "[class*='title']"]:
                try:
                    t = self.driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if t:
                        info['title'] = t
                        break
                except:
                    continue

            for sel in [".author_area", ".author", "[class*='author']"]:
                try:
                    a = self.driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if a:
                        info['author'] = a
                        break
                except:
                    continue

            for sel in ["img.cover_thumbnail", "img[class*='cover']", ".detail_header img"]:
                try:
                    src = self.driver.find_element(By.CSS_SELECTOR, sel).get_attribute('src')
                    if src:
                        info['cover_url'] = src
                        break
                except:
                    continue

            max_page = self.get_max_page(list_url)
            self._log(f"Found {max_page} pages of episodes")

            self.driver.get(list_url)
            time.sleep(2)
            try:
                WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "#_listUl li, .detail_lst li")))
            except:
                pass

            all_episodes = self.get_episodes_on_page()
            self._log(f"Page 1/{max_page}: found {len(all_episodes)} episodes")

            for page in range(2, max_page + 1):
                sep = '&' if '?' in list_url else '?'
                self.driver.get(f"{list_url}{sep}page={page}")
                time.sleep(2)
                try:
                    WebDriverWait(self.driver, 8).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "#_listUl li, .detail_lst li")))
                except:
                    self._log(f"Page {page}: no episodes, stopping")
                    break
                episodes = self.get_episodes_on_page()
                if not episodes:
                    break
                all_episodes.extend(episodes)
                self._log(f"Page {page}/{max_page}: found {len(episodes)} episodes (total: {len(all_episodes)})")

            all_episodes.reverse()
            self._log(f"Done! Total episodes: {len(all_episodes)}")

            if is_chapter_url and pasted_episode_url:
                # User pasted a specific episode link — only expose that one
                ep_no = pasted_episode_no or '?'
                matched = [e for e in all_episodes if e.get('episode_no') == ep_no]
                if matched:
                    info['chapters'] = matched
                    self._log(f"Chapter URL pasted — returning episode {ep_no} only")
                else:
                    # Couldn't match by number; construct entry from the pasted URL
                    info['chapters'] = [{'title': f'Episode {ep_no}', 'url': pasted_episode_url,
                                         'episode_no': ep_no, 'thumbnail': '', 'date': ''}]
                    self._log(f"Chapter URL pasted — using pasted episode URL directly")
            else:
                info['chapters'] = all_episodes
                if not info['chapters']:
                    info['chapters'] = [{'title': 'Episode 1', 'url': list_url,
                                         'episode_no': '1', 'thumbnail': '', 'date': ''}]
            return info

        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None

    def download_chapter(self, url, high_quality=False, progress_cb=None):
        """Download images from an episode viewer.
        progress_cb(done_count, total_count) is called after each image download.
        """
        if not self.driver:
            self.setup_driver()

        try:
            self.driver.get(url)
            WebDriverWait(self.driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "img")))
            time.sleep(2)

            last_height = self.driver.execute_script("return document.body.scrollHeight")
            while True:
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = self.driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            image_urls = []
            seen = set()

            all_imgs = self.driver.find_elements(By.CSS_SELECTOR,
                "#_imageList img, .viewer_lst img, .viewer_wrap img")
            if not all_imgs:
                all_imgs = self.driver.find_elements(By.TAG_NAME, "img")

            for img in all_imgs:
                src = (img.get_attribute('data-url') or
                       img.get_attribute('data-src') or
                       img.get_attribute('src') or '')
                if src and src not in seen and src.startswith('http'):
                    skip_patterns = ['thumbnail', 'profile', 'icon', 'logo', 'banner', 'btn_', 'bg_']
                    if not any(p in src.lower() for p in skip_patterns):
                        image_urls.append(src)
                        seen.add(src)

            self._log(f"Found {len(image_urls)} panel images")

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://www.webtoons.com/'
            }

            session = _make_session()
            downloaded = []
            total_imgs = len(image_urls)
            for i, img_url in enumerate(image_urls):
                try:
                    resp = session.get(img_url, headers=headers, timeout=15, verify=False)
                    if resp.status_code == 200:
                        downloaded.append(resp.content)
                except Exception as e:
                    print(f"Failed to download image: {e}")
                # Fire progress callback after every image
                if progress_cb:
                    progress_cb(i + 1, total_imgs)

            return downloaded

        except Exception as e:
            raise Exception(f"Failed to download chapter: {str(e)}")

    def create_pdf(self, images, filename, quality='standard'):
        try:
            pil_images = []
            for img_data in images:
                try:
                    if isinstance(img_data, bytes):
                        img = Image.open(io.BytesIO(img_data))
                    elif isinstance(img_data, str) and img_data.startswith('data:'):
                        _, encoded = img_data.split(',', 1)
                        img = Image.open(io.BytesIO(base64.b64decode(encoded)))
                    else:
                        continue
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    if quality != 'high':
                        img.thumbnail((1200, 1800), Image.Resampling.LANCZOS)
                    pil_images.append(img)
                except Exception as e:
                    print(f"Failed to process image: {e}")

            if not pil_images:
                raise Exception("No valid images to create PDF")

            pil_images[0].save(filename, format='PDF', save_all=True, append_images=pil_images[1:])
            return filename

        except Exception as e:
            raise Exception(f"Failed to create PDF: {str(e)}")

    def cleanup(self):
        if self.driver:
            self.driver.quit()
            self.driver = None


# ─────────────────────────────────────────────────────────────
#  Webnovel downloader  (webnovel.com)
# ─────────────────────────────────────────────────────────────

class WebnovelDownloader:
    """Scrapes book info and chapter text from webnovel.com."""

    BASE = "https://www.webnovel.com"

    def __init__(self, progress_callback=None):
        self.driver = None
        self.progress_callback = progress_callback

    def _log(self, msg):
        print(msg)
        if self.progress_callback:
            self.progress_callback(msg)

    # ── URL helpers ────────────────────────────────────────────

    @staticmethod
    def parse_url(url):
        """Return (book_id, chapter_id_or_None) from any webnovel.com book/chapter URL."""
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split('/') if p]
        # /book/<book_id>  or  /book/<book_id>/<chapter_id>
        book_id = None
        chapter_id = None
        if 'book' in parts:
            idx = parts.index('book')
            if idx + 1 < len(parts):
                book_id = parts[idx + 1]
            if idx + 2 < len(parts):
                chapter_id = parts[idx + 2]
        return book_id, chapter_id

    def book_list_url(self, book_id):
        return f"{self.BASE}/book/{book_id}"

    def chapter_url(self, book_id, chapter_id):
        return f"{self.BASE}/book/{book_id}/{chapter_id}"

    # ── Driver ─────────────────────────────────────────────────

    def setup_driver(self):
        opts = Options()
        opts.add_argument('--headless')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--disable-blink-features=AutomationControlled')
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option('useAutomationExtension', False)
        opts.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=opts)
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def _get(self, url, wait_css=None, sleep=3):
        self.driver.get(url)
        if wait_css:
            try:
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_css)))
            except Exception:
                pass
        time.sleep(sleep)

    # ── Book info + chapter list ───────────────────────────────

    def get_book_info(self, url, chapter_callback=None):
        """
        Return dict with title, author, cover_url, synopsis, chapters list.
        If chapter_callback(chapter_dict) is provided, it is called for each
        chapter as soon as it is discovered so the caller can stream it live.
        """
        book_id, chapter_id_from_url = self.parse_url(url)
        if not book_id:
            raise ValueError(f"Cannot parse book ID from URL: {url}")

        is_chapter_url = chapter_id_from_url is not None

        if not self.driver:
            self.setup_driver()

        list_url = self.book_list_url(book_id)
        self._log(f"Fetching book info from: {list_url}")
        self._get(list_url, wait_css="body", sleep=4)

        info = {
            'book_id': book_id,
            'url_type': 'chapter' if is_chapter_url else 'book',
            'title': 'Unknown',
            'author': 'Unknown',
            'cover_url': '',
            'synopsis': '',
            'chapters': [],
        }

        # Title
        for sel in ["h1.pt4", "h1", ".book-info h1", "[class*='bookName']"]:
            try:
                t = self.driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                if t:
                    info['title'] = t
                    break
            except Exception:
                pass

        # Author
        for sel in ["[class*='author']", ".author span", "address"]:
            try:
                a = self.driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                if a:
                    info['author'] = a
                    break
            except Exception:
                pass

        # Cover
        for sel in ["[class*='cover'] img", ".book-img img", "._cover img"]:
            try:
                src = self.driver.find_element(By.CSS_SELECTOR, sel).get_attribute('src')
                if src:
                    info['cover_url'] = src
                    break
            except Exception:
                pass

        # Synopsis
        for sel in ["[class*='synopsis'] p", "[class*='intro'] p", ".j_synopsis p", "p.fs16"]:
            try:
                paras = self.driver.find_elements(By.CSS_SELECTOR, sel)
                txt = ' '.join(p.text.strip() for p in paras if p.text.strip())
                if txt:
                    info['synopsis'] = txt
                    break
            except Exception:
                pass

        # ── Chapter list via catalog page (streamed one-by-one) ────
        catalog_url = f"{list_url}/catalog"
        self._log("Fetching chapter catalog...")
        self._get(catalog_url, wait_css="body", sleep=5)

        # When a chapter URL is pasted we must slice BEFORE firing callbacks,
        # so suppress the callback during scraping and fire it manually afterwards.
        suppress_cb = is_chapter_url and chapter_id_from_url is not None
        chapters = self._scrape_catalog_streaming(book_id, None if suppress_cb else chapter_callback)

        # Fallback: ToC links on the main book page
        if not chapters:
            self._log("Catalog empty, trying ToC fallback...")
            self._get(list_url, wait_css="body", sleep=3)
            chapters = self._scrape_toc_links(book_id, None if suppress_cb else chapter_callback)

        self._log(f"Found {len(chapters)} chapters total")

        if is_chapter_url and chapter_id_from_url:
            # User pasted a specific chapter link — return all chapters from that point onwards
            matched_idx = next(
                (i for i, c in enumerate(chapters) if c.get('chapter_id') == chapter_id_from_url),
                None
            )
            if matched_idx is not None:
                sliced = chapters[matched_idx:]
                info['chapters'] = sliced
                self._log(f"Chapter URL pasted — returning {len(sliced)} chapters from chapter {chapter_id_from_url} onwards")
                # Now fire callbacks only for the sliced subset
                if chapter_callback:
                    for ch in sliced:
                        chapter_callback(ch)
            else:
                # Couldn't match in scraped list — build a placeholder with the pasted ID.
                placeholder = {'title': '', 'chapter_id': chapter_id_from_url, 'index': 0}
                info['chapters'] = [placeholder]
                if chapter_callback:
                    chapter_callback(placeholder)
                self._log("Chapter URL pasted — title will be resolved when downloading")
        else:
            info['chapters'] = chapters

        return info

    def _scrape_catalog_streaming(self, book_id, chapter_callback=None):
        """
        Scroll the catalog page, emitting each newly-found chapter immediately
        via chapter_callback so the frontend can show them one by one.
        Returns the full list when done.
        """
        chapters = []
        seen = set()

        self._log("Scrolling catalog to load all chapters...")

        stale_rounds = 0
        last_count = 0

        while stale_rounds < 12:
            # Scrape whatever is currently visible
            try:
                selector_groups = [
                    "[class*='chapter-item'] a, [class*='chapterItem'] a",
                    "[class*='catalog'] li a, ol.chapter-list li a",
                    "li.chapter-item a",
                    "a[href*='/book/']",
                ]
                items = []
                for sel in selector_groups:
                    items = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    if len(items) > 1:
                        break

                for el in items:
                    try:
                        href = el.get_attribute('href') or ''
                        if '/book/' not in href:
                            continue
                        bk_id, ch_id = self.parse_url(href)
                        if not ch_id or ch_id == bk_id or ch_id in seen:
                            continue
                        title = (el.text.strip()
                                 or el.get_attribute('title')
                                 or el.get_attribute('aria-label')
                                 or f"Chapter {len(chapters)+1}")
                        if len(title) > 100:
                            title = title[:100]
                        ch = {'title': title, 'chapter_id': ch_id, 'index': len(chapters)}
                        chapters.append(ch)
                        seen.add(ch_id)
                        # ← emit immediately so the caller can stream it
                        if chapter_callback:
                            chapter_callback(ch)
                    except Exception:
                        continue
            except Exception as e:
                print(f"Catalog scrape error: {e}")

            current_count = len(chapters)
            if current_count == last_count:
                stale_rounds += 1
            else:
                stale_rounds = 0
                self._log(f"Loaded {current_count} chapters so far...")
            last_count = current_count

            # Scroll further — try multiple techniques for virtualized lists
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.8)
            self.driver.execute_script("window.scrollBy(0, -200);")
            time.sleep(0.3)
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            # Try clicking any "load more" button
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR,
                    "button[class*='load'], button[class*='more'], [class*='load-more']")
                self.driver.execute_script("arguments[0].click();", btn)
            except Exception:
                pass
            time.sleep(1.5)

        self._log(f"Catalog fully loaded: {len(chapters)} chapters")
        return chapters

    def _scrape_toc_links(self, book_id, chapter_callback=None):
        chapters = []
        seen = set()
        try:
            links = self.driver.find_elements(By.CSS_SELECTOR, f"a[href*='/book/{book_id}/']")
            for el in links:
                href = el.get_attribute('href') or ''
                _, ch_id = self.parse_url(href)
                if not ch_id or ch_id == book_id or ch_id in seen:
                    continue
                title = el.text.strip() or f"Chapter {len(chapters)+1}"
                ch = {'title': title, 'chapter_id': ch_id, 'index': len(chapters)}
                chapters.append(ch)
                seen.add(ch_id)
                if chapter_callback:
                    chapter_callback(ch)
        except Exception as e:
            print(f"ToC scrape error: {e}")
        return chapters

    # ── Chapter text ───────────────────────────────────────────

    def download_chapter_text(self, book_id, chapter_id, progress_cb=None, fallback_title=''):
        """Scroll through the full chapter page, extracting ALL paragraphs as they load.
        Returns {'title': str, 'paragraphs': [str]}.
        """
        if not self.driver:
            self.setup_driver()

        url = self.chapter_url(book_id, chapter_id)
        self._get(url, wait_css='body', sleep=4)

        # Use the catalog title — avoids "Chapter 1: Chapter 1" from page h1
        title = fallback_title
        if not title:
            for sel in ['.chapter-title', "[class*='chapterTitle']", "[class*='chapter-name']", 'h1']:
                try:
                    t = self.driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if t:
                        title = t
                        break
                except Exception:
                    pass

        # ── Incremental scroll to force all lazy content to load ──
        # webnovel.com reveals paragraphs as you scroll; one big jump misses them.
        SCROLL_STEP = 500
        PAUSE       = 0.35
        stale       = 0
        while stale < 5:
            self.driver.execute_script(f'window.scrollBy(0, {SCROLL_STEP});')
            time.sleep(PAUSE)
            scroll_y = self.driver.execute_script('return window.scrollY + window.innerHeight')
            total    = self.driver.execute_script('return document.body.scrollHeight')
            if scroll_y >= total - 20:
                time.sleep(1.2)
                stale += 1
            else:
                stale = 0

        # ── Extract paragraphs ────────────────────────────────────
        paragraphs = []
        for sel in [
            "[class*='chapter-content'] p",
            "[class*='cha-content'] p",
            '#chapter-content p',
            '.cha-words p',
            'article p',
        ]:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    paragraphs = [e.text.strip() for e in els if e.text.strip()]
                    break
            except Exception:
                pass

        # Fallback: all <p> longer than 40 chars (filters nav/UI noise)
        if not paragraphs:
            try:
                all_p = self.driver.find_elements(By.TAG_NAME, 'p')
                paragraphs = [e.text.strip() for e in all_p if len(e.text.strip()) > 40]
            except Exception:
                pass

        if progress_cb:
            progress_cb()

        return {'title': title, 'paragraphs': paragraphs}


    # ── Output builders ────────────────────────────────────────

    @staticmethod
    def build_txt(book_info, chapters_data):
        """chapters_data: list of {'title', 'paragraphs'}"""
        lines = []
        lines.append(book_info.get('title', 'Unknown'))
        lines.append(f"Author: {book_info.get('author', 'Unknown')}")
        if book_info.get('synopsis'):
            lines.append('')
            lines.append('Synopsis:')
            lines.append(book_info['synopsis'])
        lines.append('')
        lines.append('=' * 60)
        for ch in chapters_data:
            lines.append('')
            lines.append(ch['title'])
            lines.append('-' * 40)
            for para in ch['paragraphs']:
                lines.append(para)
            lines.append('')
        return '\n'.join(lines)

    @staticmethod
    def build_pdf(book_info, chapters_data, output_path):
        """One combined PDF.  Each chapter begins on its own page with a large
        chapter-heading block so it is immediately obvious where chapters start.
        """
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        PageBreak, HRFlowable, KeepTogether)

        W, H = A4

        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=2.8*cm, rightMargin=2.8*cm,
            topMargin=2.8*cm, bottomMargin=2.8*cm,
        )

        styles = getSampleStyleSheet()

        def safe(t):
            return (t or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

        # ── Cover ───────────────────────────────────────────────
        cover_title = ParagraphStyle('CoverTitle', parent=styles['Title'],
            fontSize=30, leading=36, alignment=1,
            textColor=colors.HexColor('#111111'), spaceAfter=10)
        cover_sub   = ParagraphStyle('CoverSub', parent=styles['Normal'],
            fontSize=13, leading=18, alignment=1,
            textColor=colors.HexColor('#555555'), spaceAfter=6)
        cover_meta  = ParagraphStyle('CoverMeta', parent=styles['Normal'],
            fontSize=11, leading=16, alignment=1,
            textColor=colors.HexColor('#999999'), spaceAfter=4)
        synopsis_st = ParagraphStyle('Synopsis', parent=styles['Normal'],
            fontSize=11, leading=17, leftIndent=14, rightIndent=14,
            textColor=colors.HexColor('#444444'), spaceAfter=0)

        story = []
        story.append(Spacer(1, 3*cm))
        story.append(Paragraph(safe(book_info.get('title') or 'Unknown'), cover_title))
        story.append(Paragraph(f"by {safe(book_info.get('author') or 'Unknown')}", cover_sub))

        if chapters_data:
            n = len(chapters_data)
            first = chapters_data[0].get('title') or ''
            last  = chapters_data[-1].get('title') or '' if n > 1 else ''
            span  = f"{safe(first)} — {safe(last)}" if last and last != first else safe(first)
            if span:
                story.append(Paragraph(span, cover_meta))
            story.append(Paragraph(f"{n} chapter{'s' if n != 1 else ''}", cover_meta))

        if book_info.get('synopsis'):
            story.append(Spacer(1, 1*cm))
            story.append(HRFlowable(width='50%', thickness=0.5,
                                    color=colors.HexColor('#cccccc'), spaceAfter=10))
            story.append(Paragraph(safe(book_info['synopsis']), synopsis_st))

        story.append(PageBreak())

        # ── Chapter heading — one big bold line, impossible to miss ───────
        ch_heading_st = ParagraphStyle('ChHeading', parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=28, leading=36,
            textColor=colors.HexColor('#111111'),
            spaceAfter=6)

        # ── Body ────────────────────────────────────────────────
        body_st = ParagraphStyle('Body', parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=11, leading=20,
            spaceAfter=10,
            textColor=colors.HexColor('#1a1a1a'),
            firstLineIndent=0)

        # ── Chapters — each starts on its own page ───────────────
        for idx, ch in enumerate(chapters_data):
            raw_title = ch.get('title') or f'Chapter {idx + 1}'

            # Chapter heading block — heading + rule kept together at top of page
            header = KeepTogether([
                Paragraph(safe(raw_title), ch_heading_st),
                HRFlowable(width='100%', thickness=1.5,
                           color=colors.HexColor('#333333'), spaceAfter=20),
            ])
            story.append(header)

            for para in ch.get('paragraphs', []):
                if para.strip():
                    story.append(Paragraph(safe(para), body_st))

            story.append(PageBreak())

        doc.build(story)

    @staticmethod
    def build_epub(book_info, chapters_data, output_path):
        """Build a simple EPUB file. Falls back gracefully if ebooklib absent."""
        try:
            from ebooklib import epub

            book = epub.EpubBook()
            book.set_title(book_info.get('title', 'Unknown'))
            book.add_author(book_info.get('author', 'Unknown'))
            book.set_language('en')

            epub_chapters = []
            for i, ch in enumerate(chapters_data):
                c = epub.EpubHtml(title=ch['title'], file_name=f'chapter_{i:04d}.xhtml', lang='en')
                body = f"<h2>{ch['title']}</h2>\n"
                body += '\n'.join(f"<p>{p}</p>" for p in ch['paragraphs'])
                c.content = f"<html><body>{body}</body></html>"
                book.add_item(c)
                epub_chapters.append(c)

            book.toc = tuple(epub_chapters)
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())
            book.spine = ['nav'] + epub_chapters
            epub.write_epub(output_path, book)
            return True
        except ImportError:
            return False

    def cleanup(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
