# Webtoon Downloader

A personal webtoon downloader with a dreamy blue/purple pixelated frontend and Python Selenium backend to download webtoons as PDF.

## Project Structure

```
webtoon-downloader/
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── script.js
├── backend/
│   ├── app.py
│   ├── requirements.txt
│   └── downloader.py
└── README.md
```

## Setup Instructions

### Backend Setup

1. Install Python dependencies:
```bash
cd backend
pip install -r requirements.txt
```

2. Download ChromeDriver:
   - Visit: https://chromedriver.chromium.org/
   - Download the version matching your Chrome browser
   - Place it in the `backend/` folder or add to PATH

3. Start the Flask server:
```bash
python app.py
```
The server will run on `http://localhost:5000`

### Frontend Setup

1. Open `frontend/index.html` in your browser
2. Or use a local server (optional but recommended):
```bash
cd frontend
python -m http.server 8000
```
Then visit `http://localhost:8000`

## Usage

1. Enter a webtoon URL
2. Click "Download as PDF"
3. Select chapters to download
4. Wait for the process to complete
5. Your PDF will be ready for download

## Notes

- This is a personal, private project
- Ensure you have the right to download content
- Requires Chrome/Chromium browser installed
- Python 3.8+ required
