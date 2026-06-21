from flask import Flask, request, jsonify, send_file, Response, stream_with_context
from flask_cors import CORS
import os
import tempfile
import threading
import queue
import uuid
import json
import time
from downloader import WebtoonDownloader, WebnovelDownloader

app = Flask(__name__)
CORS(app)

DOWNLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'webtoon-downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

fetch_jobs = {}
download_jobs = {}

_SENTINEL = object()  # signals the PDF writer that scraping is done


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'message': 'Webtoon Downloader API is running'})


# ── FETCH ─────────────────────────────────────

@app.route('/api/fetch-webtoon', methods=['POST'])
def fetch_webtoon():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'success': False, 'message': 'URL is required'}), 400

    job_id = str(uuid.uuid4())
    fetch_jobs[job_id] = {'logs': [], 'result': None, 'done': False, 'error': None}

    def run():
        def on_log(msg):
            fetch_jobs[job_id]['logs'].append(msg)
        try:
            downloader = WebtoonDownloader(progress_callback=on_log)
            result = downloader.get_webtoon_info(url)
            fetch_jobs[job_id]['result'] = result
        except Exception as e:
            fetch_jobs[job_id]['error'] = str(e)
        finally:
            fetch_jobs[job_id]['done'] = True
            try:
                downloader.cleanup()
            except:
                pass

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id})


@app.route('/api/fetch-progress/<job_id>', methods=['GET'])
def fetch_progress(job_id):
    def generate():
        sent_count = 0
        while True:
            job = fetch_jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
            logs = job['logs']
            while sent_count < len(logs):
                yield f"data: {json.dumps({'log': logs[sent_count]})}\n\n"
                sent_count += 1
            if job['done']:
                if job['error']:
                    yield f"data: {json.dumps({'error': job['error']})}\n\n"
                else:
                    yield f"data: {json.dumps({'done': True, 'webtoon': job['result']})}\n\n"
                fetch_jobs.pop(job_id, None)
                break
            time.sleep(0.3)

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ── DOWNLOAD (pipelined) ───────────────────────────────────

@app.route('/api/download', methods=['POST'])
def download():
    data = request.json
    chapters = data.get('chapters', [])
    single_pdf = data.get('single_pdf', True)
    high_quality = data.get('high_quality', False)

    if not chapters:
        return jsonify({'success': False, 'message': 'No chapters selected'}), 400

    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        'progress': 0, 'status': 'Starting...', 'log': [],
        'file_paths': [], 'error': None, 'done': False,
        'paused': False, 'cancelled': False,
        'pdf_ready_indices': []   # ← tracks which chapter indices have a finished PDF
    }

    def is_cancelled():
        return download_jobs[job_id]['cancelled']

    def wait_if_paused():
        while download_jobs[job_id]['paused']:
            time.sleep(0.5)
            if is_cancelled():
                return False
        return True

    def log(msg):
        download_jobs[job_id]['log'].append(msg)

    def set_progress(p):
        download_jobs[job_id]['progress'] = p

    # ── Producer: scrapes chapters one by one, pushes images to queue ──
    def scraper(img_queue):
        downloader = WebtoonDownloader()
        total = len(chapters)
        try:
            for idx, chapter in enumerate(chapters):
                if is_cancelled():
                    break
                if not wait_if_paused():
                    break

                chapter_url   = chapter.get('url', '') if isinstance(chapter, dict) else chapter
                chapter_title = chapter.get('title', f'Episode {idx+1}') if isinstance(chapter, dict) else f'Episode {idx+1}'

                chapter_start = int(idx / total * 90)
                chapter_end   = int((idx + 1) / total * 90)

                msg = f"Scraping {chapter_title} ({idx+1}/{total})..."
                download_jobs[job_id]['status'] = msg
                log(msg)
                set_progress(chapter_start)

                def make_cb(start, end):
                    def cb(done_count, total_count):
                        if total_count > 0:
                            set_progress(int(start + (done_count / total_count) * (end - start)))
                    return cb

                try:
                    images = downloader.download_chapter(
                        chapter_url, high_quality,
                        progress_cb=make_cb(chapter_start, chapter_end)
                    )
                    set_progress(chapter_end)
                    log(f"Done scraping {chapter_title} — {len(images)} images")
                    img_queue.put({'title': chapter_title, 'images': images, 'idx': idx})
                except Exception as e:
                    log(f"Failed: {chapter_title} — {str(e)}")

        finally:
            img_queue.put(_SENTINEL)
            try:
                downloader.cleanup()
            except:
                pass

    # ── Consumer: receives chapters from queue, writes PDFs immediately ──
    def pdf_writer(img_queue):
        file_paths = []
        all_images_combined = []  # used only in single_pdf mode
        total = len(chapters)
        received = 0

        try:
            downloader = WebtoonDownloader()  # used only for create_pdf

            while True:
                item = img_queue.get()

                if item is _SENTINEL:
                    break

                chapter_title = item['title']
                images = item['images']
                received += 1

                if not images:
                    continue

                if single_pdf:
                    all_images_combined.extend(images)
                    log(f"Buffered {chapter_title} into combined PDF ({received}/{total})")
                else:
                    # Write this chapter's PDF immediately
                    log(f"Writing PDF for {chapter_title}...")
                    safe = "".join(c for c in chapter_title if c.isalnum() or c in ' -_').rstrip()[:50]
                    pdf_path = os.path.join(DOWNLOAD_FOLDER, f'{safe}_{job_id}.pdf')
                    try:
                        downloader.create_pdf(images, pdf_path, 'high' if high_quality else 'standard')
                        file_paths.append(pdf_path)
                        log(f"PDF ready: {chapter_title}")
                        download_jobs[job_id]['file_paths'] = list(file_paths)
                        download_jobs[job_id]['pdf_ready_indices'].append({
                            'index': len(file_paths) - 1,
                            'title': chapter_title   # original title from Webtoons
                        })
                    except Exception as e:
                        log(f"PDF failed for {chapter_title}: {str(e)}")

            # Single PDF: write everything at the end
            if single_pdf and all_images_combined:
                log("Creating combined PDF...")
                set_progress(95)
                pdf_path = os.path.join(DOWNLOAD_FOLDER, f'webtoon_{job_id}.pdf')
                try:
                    downloader.create_pdf(all_images_combined, pdf_path, 'high' if high_quality else 'standard')
                    file_paths.append(pdf_path)
                except Exception as e:
                    log(f"Combined PDF failed: {str(e)}")

            if not file_paths:
                download_jobs[job_id]['error'] = 'No PDFs were created'
            else:
                download_jobs[job_id]['file_paths'] = file_paths
                log(f"All done! {len(file_paths)} PDF(s) ready.")

        except Exception as e:
            download_jobs[job_id]['error'] = str(e)
            log(f"Error: {str(e)}")
        finally:
            set_progress(100)
            download_jobs[job_id]['status'] = 'completed'
            download_jobs[job_id]['done'] = True

    def run():
        img_queue = queue.Queue(maxsize=2)  # backpressure: don't let scraper run too far ahead
        t_scraper = threading.Thread(target=scraper, args=(img_queue,), daemon=True)
        t_writer  = threading.Thread(target=pdf_writer,  args=(img_queue,), daemon=True)
        t_scraper.start()
        t_writer.start()
        t_scraper.join()
        t_writer.join()

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id})


@app.route('/api/download-progress/<job_id>', methods=['GET'])
def download_progress(job_id):
    def generate():
        sent_log_count = 0
        sent_pdf_indices = set()   # track which pdf_ready events we've already sent
        while True:
            job = download_jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break

            # Send any new pdf_ready events
            for entry in job.get('pdf_ready_indices', []):
                idx = entry['index']
                if idx not in sent_pdf_indices:
                    yield f"data: {json.dumps({'pdf_ready': idx, 'pdf_title': entry['title']})}\n\n"
                    sent_pdf_indices.add(idx)

            logs = job.get('log', [])
            while sent_log_count < len(logs):
                yield f"data: {json.dumps({'log': logs[sent_log_count], 'progress': job['progress']})}\n\n"
                sent_log_count += 1

            if job['done']:
                if job['error']:
                    yield f"data: {json.dumps({'error': job['error']})}\n\n"
                else:
                    yield f"data: {json.dumps({'done': True, 'progress': 100, 'job_id': job_id, 'file_count': len(job.get('file_paths', []))})}\n\n"
                break

            yield f"data: {json.dumps({'progress': job['progress'], 'status': job['status']})}\n\n"
            time.sleep(0.3)

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/pause-download/<job_id>', methods=['POST'])
def pause_download(job_id):
    if job_id not in download_jobs:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    download_jobs[job_id]['paused'] = True
    download_jobs[job_id]['log'].append('Download paused')
    return jsonify({'success': True})


@app.route('/api/resume-download/<job_id>', methods=['POST'])
def resume_download(job_id):
    if job_id not in download_jobs:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    download_jobs[job_id]['paused'] = False
    download_jobs[job_id]['log'].append('Download resumed')
    return jsonify({'success': True})


@app.route('/api/cancel-download/<job_id>', methods=['POST'])
def cancel_download(job_id):
    if job_id not in download_jobs:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    download_jobs[job_id]['cancelled'] = True
    download_jobs[job_id]['log'].append('Download cancelled')
    return jsonify({'success': True})


@app.route('/api/download-file/<job_id>/<int:file_index>', methods=['GET'])
def download_file(job_id, file_index):
    job = download_jobs.get(job_id)
    if not job or not job.get('file_paths'):
        return jsonify({'error': 'File not found'}), 404
    file_paths = job['file_paths']
    if file_index < 0 or file_index >= len(file_paths):
        return jsonify({'error': 'Invalid file index'}), 404
    pdf_path = file_paths[file_index]
    if not os.path.exists(pdf_path):
        return jsonify({'error': 'File missing on disk'}), 404
    filename = os.path.basename(pdf_path).replace(f'_{job_id}', '')
    return send_file(pdf_path, mimetype='application/pdf', as_attachment=True, download_name=filename)


# ── WEBNOVEL ──────────────────────────────────────────────────

webnovel_fetch_jobs = {}
webnovel_download_jobs = {}


@app.route('/api/fetch-webnovel', methods=['POST'])
def fetch_webnovel():
    data = request.json
    url = (data.get('url') or '').strip()
    if not url or 'webnovel.com' not in url:
        return jsonify({'success': False, 'message': 'A valid webnovel.com URL is required'}), 400

    job_id = str(uuid.uuid4())
    webnovel_fetch_jobs[job_id] = {
        'logs': [], 'result': None, 'done': False, 'error': None,
        'chapters': [],   # chapters emitted one-by-one as they are found
        'info': None,     # book metadata (sent once, before chapters start)
    }

    def run():
        def on_log(msg):
            webnovel_fetch_jobs[job_id]['logs'].append(msg)

        def on_chapter(ch):
            webnovel_fetch_jobs[job_id]['chapters'].append(ch)

        downloader = WebnovelDownloader(progress_callback=on_log)
        try:
            result = downloader.get_book_info(url, chapter_callback=on_chapter)
            # Store the result without chapters (already streamed)
            meta = {k: v for k, v in result.items() if k != 'chapters'}
            webnovel_fetch_jobs[job_id]['info'] = meta
            webnovel_fetch_jobs[job_id]['result'] = result
        except Exception as e:
            webnovel_fetch_jobs[job_id]['error'] = str(e)
        finally:
            webnovel_fetch_jobs[job_id]['done'] = True
            try:
                downloader.cleanup()
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id})


@app.route('/api/fetch-webnovel-progress/<job_id>', methods=['GET'])
def fetch_webnovel_progress(job_id):
    def generate():
        sent_logs = 0
        sent_chapters = 0
        info_sent = False
        while True:
            job = webnovel_fetch_jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break

            # Send book metadata once as soon as it arrives
            if not info_sent and job.get('info'):
                yield f"data: {json.dumps({'info': job['info']})}\n\n"
                info_sent = True

            # Stream each new chapter individually
            chapters = job.get('chapters', [])
            while sent_chapters < len(chapters):
                yield f"data: {json.dumps({'chapter': chapters[sent_chapters]})}\n\n"
                sent_chapters += 1

            # Stream logs
            logs = job['logs']
            while sent_logs < len(logs):
                yield f"data: {json.dumps({'log': logs[sent_logs]})}\n\n"
                sent_logs += 1

            if job['done']:
                if job['error']:
                    yield f"data: {json.dumps({'error': job['error']})}\n\n"
                else:
                    yield f"data: {json.dumps({'done': True, 'total': sent_chapters})}\n\n"
                webnovel_fetch_jobs.pop(job_id, None)
                break
            time.sleep(0.2)

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/download-webnovel', methods=['POST'])
def download_webnovel():
    data = request.json
    book_id     = data.get('book_id', '')
    book_info   = data.get('book_info', {})
    chapters    = data.get('chapters', [])
    fmt         = data.get('format', 'txt')   # 'txt', 'pdf', or 'epub'
    if not book_id or not chapters:
        return jsonify({'success': False, 'message': 'book_id and chapters are required'}), 400

    job_id = str(uuid.uuid4())
    webnovel_download_jobs[job_id] = {
        'progress': 0, 'status': 'Starting...', 'log': [],
        'file_path': None, 'format_used': fmt, 'error': None, 'done': False,
        'paused': False, 'cancelled': False,
    }

    def is_cancelled():
        return webnovel_download_jobs[job_id]['cancelled']

    def wait_if_paused():
        while webnovel_download_jobs[job_id]['paused']:
            time.sleep(0.5)
            if is_cancelled():
                return False
        return True

    def log(msg):
        webnovel_download_jobs[job_id]['log'].append(msg)

    def run():
        downloader = WebnovelDownloader()
        chapters_data = []
        total = len(chapters)

        try:
            for idx, ch in enumerate(chapters):
                if is_cancelled():
                    log("Download cancelled.")
                    break
                if not wait_if_paused():
                    log("Download cancelled during pause.")
                    break

                chapter_id = ch.get('chapter_id', '')
                # Use a numbered fallback label so the chapter list shows "Ch. 2", "Ch. 3" etc.
                ch_title   = ch.get('title') or f'Ch. {idx + 1}'
                webnovel_download_jobs[job_id]['progress'] = int((idx / total) * 90)
                webnovel_download_jobs[job_id]['status'] = f"Downloading {ch_title} ({idx+1}/{total})..."
                log(f"Downloading {ch_title} ({idx+1}/{total})...")

                try:
                    # Pass ch_title as fallback so download_chapter_text uses it when
                    # scraping the page title fails (e.g. behind login wall).
                    result = downloader.download_chapter_text(book_id, chapter_id,
                                                              fallback_title=ch_title)
                    if not result['title']:
                        result['title'] = ch_title
                    chapters_data.append(result)
                    log(f"Done: {result['title']} — {len(result['paragraphs'])} paragraphs")
                except Exception as e:
                    log(f"Failed: {ch_title} — {e}")
                    chapters_data.append({'title': ch_title,
                                          'paragraphs': [f"[Error downloading this chapter: {e}]"]})

            if is_cancelled() and not chapters_data:
                webnovel_download_jobs[job_id]['error'] = 'Download was cancelled before any chapters were saved'
                return

            webnovel_download_jobs[job_id]['progress'] = 95
            log("Building output file...")

            book_title = book_info.get('title', 'webnovel')
            safe_book  = "".join(c for c in book_title if c.isalnum() or c in ' -_').rstrip()[:50]

            # Build a chapter label for the filename (only when downloading a single chapter)
            if len(chapters_data) == 1:
                ch_label = chapters_data[0].get('title', '')
                safe_ch  = "".join(c for c in ch_label if c.isalnum() or c in ' -_').rstrip()[:40]
                safe_title = f"{safe_book} - {safe_ch}" if safe_ch else safe_book
            else:
                safe_title = safe_book

            if fmt == 'pdf':
                fmt_used = 'pdf'
                file_path = os.path.join(DOWNLOAD_FOLDER, f'{safe_title}_{job_id}.pdf')
                log("Generating PDF...")
                WebnovelDownloader.build_pdf(book_info, chapters_data, file_path)
            elif fmt == 'epub':
                file_path = os.path.join(DOWNLOAD_FOLDER, f'{safe_title}_{job_id}.epub')
                ok = WebnovelDownloader.build_epub(book_info, chapters_data, file_path)
                if not ok:
                    log("ebooklib not installed — falling back to PDF")
                    fmt_used = 'pdf'
                    file_path = os.path.join(DOWNLOAD_FOLDER, f'{safe_title}_{job_id}.pdf')
                    WebnovelDownloader.build_pdf(book_info, chapters_data, file_path)
                else:
                    fmt_used = 'epub'
            else:
                fmt_used = 'txt'
                file_path = os.path.join(DOWNLOAD_FOLDER, f'{safe_title}_{job_id}.txt')
                txt = WebnovelDownloader.build_txt(book_info, chapters_data)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(txt)

            webnovel_download_jobs[job_id]['file_path'] = file_path
            webnovel_download_jobs[job_id]['format_used'] = fmt_used
            log(f"Done! {len(chapters_data)} chapters saved as {fmt_used.upper()}.")

        except Exception as e:
            webnovel_download_jobs[job_id]['error'] = str(e)
            log(f"Fatal error: {e}")
        finally:
            webnovel_download_jobs[job_id]['progress'] = 100
            webnovel_download_jobs[job_id]['status'] = 'completed'
            webnovel_download_jobs[job_id]['done'] = True
            try:
                downloader.cleanup()
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id})


@app.route('/api/webnovel-download-progress/<job_id>', methods=['GET'])
def webnovel_download_progress(job_id):
    def generate():
        sent = 0
        while True:
            job = webnovel_download_jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
            logs = job.get('log', [])
            while sent < len(logs):
                yield f"data: {json.dumps({'log': logs[sent], 'progress': job['progress']})}\n\n"
                sent += 1
            if job['done']:
                if job['error']:
                    yield f"data: {json.dumps({'error': job['error']})}\n\n"
                else:
                    yield f"data: {json.dumps({'done': True, 'progress': 100, 'job_id': job_id, 'format': job.get('format_used', 'txt')})}\n\n"
                break
            yield f"data: {json.dumps({'progress': job['progress'], 'status': job['status']})}\n\n"
            time.sleep(0.3)

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/pause-webnovel-download/<job_id>', methods=['POST'])
def pause_webnovel_download(job_id):
    if job_id not in webnovel_download_jobs:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    webnovel_download_jobs[job_id]['paused'] = True
    webnovel_download_jobs[job_id]['log'].append('Download paused')
    return jsonify({'success': True})


@app.route('/api/resume-webnovel-download/<job_id>', methods=['POST'])
def resume_webnovel_download(job_id):
    if job_id not in webnovel_download_jobs:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    webnovel_download_jobs[job_id]['paused'] = False
    webnovel_download_jobs[job_id]['log'].append('Download resumed')
    return jsonify({'success': True})


@app.route('/api/cancel-webnovel-download/<job_id>', methods=['POST'])
def cancel_webnovel_download(job_id):
    if job_id not in webnovel_download_jobs:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    webnovel_download_jobs[job_id]['cancelled'] = True
    webnovel_download_jobs[job_id]['log'].append('Download cancelled')
    return jsonify({'success': True})


@app.route('/api/download-webnovel-file/<job_id>', methods=['GET'])
def download_webnovel_file(job_id):
    job = webnovel_download_jobs.get(job_id)
    if not job or not job.get('file_path'):
        return jsonify({'error': 'File not found'}), 404
    file_path = job['file_path']
    if not os.path.exists(file_path):
        return jsonify({'error': 'File missing on disk'}), 404
    fmt = job.get('format_used', 'txt')
    filename = os.path.basename(file_path).replace(f'_{job_id}', '')
    mimetypes = {'epub': 'application/epub+zip', 'pdf': 'application/pdf', 'txt': 'text/plain'}
    mimetype = mimetypes.get(fmt, 'text/plain')
    return send_file(file_path, mimetype=mimetype, as_attachment=True, download_name=filename)


if __name__ == '__main__':
    print("Webtoon Downloader API Starting...")
    print("Server running on http://localhost:5000")
    app.run(debug=False, port=5000, threaded=True)
