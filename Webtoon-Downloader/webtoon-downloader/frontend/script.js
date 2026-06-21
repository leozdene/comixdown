// API Configuration
// 🔧 STEP: After deploying backend to Render, replace this with your Render URL
// e.g. const API_BASE_URL = 'https://comixdown-backend.onrender.com/api';
const API_BASE_URL = 'http://localhost:5000/api';

// DOM Elements
const webtoonUrlInput = document.getElementById('webtoonUrl');
const fetchBtn = document.getElementById('fetchBtn');
const loadingState = document.getElementById('loadingState');
const infoSection = document.getElementById('infoSection');
const chapterSection = document.getElementById('chapterSection');
const downloadSection = document.getElementById('downloadSection');
const progressSection = document.getElementById('progressSection');
const statusMessage = document.getElementById('statusMessage');

// Chapter Selection
const chapterList = document.getElementById('chapterList');
const selectAllBtn = document.getElementById('selectAllBtn');
const clearAllBtn = document.getElementById('clearAllBtn');
const downloadBtn = document.getElementById('downloadBtn');

// Progress Elements
const progressBar = document.getElementById('progressBar');
const progressText = document.getElementById('progressText');
const progressLog = document.getElementById('progressLog');

// State
let currentWebtoon = null;
let selectedChapters = [];

// Event Listeners
fetchBtn.addEventListener('click', handleFetchWebtoon);
webtoonUrlInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') handleFetchWebtoon();
});

selectAllBtn.addEventListener('click', selectAllChapters);
clearAllBtn.addEventListener('click', clearAllChapters);
downloadBtn.addEventListener('click', handleDownload);

// Functions
async function handleFetchWebtoon() {
    const url = webtoonUrlInput.value.trim();
    
    if (!url) {
        showStatus('Please enter a webtoon URL', 'error');
        return;
    }

    showLoading(true);
    hideAllSections();

    try {
        const response = await fetch(`${API_BASE_URL}/fetch-webtoon`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url: url })
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.success) {
            currentWebtoon = data.webtoon;
            displayWebtoonInfo(data.webtoon);
            displayChapters(data.webtoon.chapters);
            showStatus('Webtoon loaded successfully!', 'success');
        } else {
            throw new Error(data.message || 'Failed to fetch webtoon');
        }
    } catch (error) {
        console.error('Error fetching webtoon:', error);
        showStatus(`Error: ${error.message}`, 'error');
    } finally {
        showLoading(false);
    }
}

function displayWebtoonInfo(webtoon) {
    document.getElementById('coverImage').src = webtoon.cover_url || 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="120" height="160"%3E%3Crect fill="%23999" width="120" height="160"/%3E%3C/svg%3E';
    document.getElementById('webtoonTitle').textContent = webtoon.title || 'Unknown';
    document.getElementById('webtoonAuthor').textContent = `By ${webtoon.author || 'Unknown Author'}`;
    document.getElementById('chapterCount').textContent = `${webtoon.chapters.length} chapters available`;
    
    infoSection.classList.remove('hidden');
}

function displayChapters(chapters) {
    chapterList.innerHTML = '';
    selectedChapters = [];

    chapters.forEach((chapter, index) => {
        const div = document.createElement('div');
        div.className = 'chapter-item';
        div.innerHTML = `
            <input type="checkbox" id="chapter-${index}" value="${chapter.url}" checked>
            <label for="chapter-${index}">${chapter.title || `Chapter ${index + 1}`}</label>
        `;

        const checkbox = div.querySelector('input');
        checkbox.addEventListener('change', updateSelectedChapters);

        chapterList.appendChild(div);
        selectedChapters.push({
            index: index,
            url: chapter.url,
            title: chapter.title || `Chapter ${index + 1}`
        });
    });

    chapterSection.classList.remove('hidden');
    downloadSection.classList.remove('hidden');
}

function updateSelectedChapters() {
    selectedChapters = [];
    document.querySelectorAll('#chapterList input[type="checkbox"]:checked').forEach((checkbox) => {
        const index = parseInt(checkbox.id.split('-')[1]);
        selectedChapters.push({
            index: index,
            url: checkbox.value,
            title: checkbox.nextElementSibling.textContent
        });
    });
}

function selectAllChapters() {
    document.querySelectorAll('#chapterList input[type="checkbox"]').forEach(checkbox => {
        checkbox.checked = true;
    });
    updateSelectedChapters();
}

function clearAllChapters() {
    document.querySelectorAll('#chapterList input[type="checkbox"]').forEach(checkbox => {
        checkbox.checked = false;
    });
    updateSelectedChapters();
}

async function handleDownload() {
    updateSelectedChapters();

    if (selectedChapters.length === 0) {
        showStatus('Please select at least one chapter', 'error');
        return;
    }

    if (!currentWebtoon) {
        showStatus('No webtoon loaded', 'error');
        return;
    }

    const singlePdf = document.getElementById('singlePdf').checked;
    const highQuality = document.getElementById('highQuality').checked;

    downloadBtn.disabled = true;
    progressSection.classList.remove('hidden');
    progressLog.innerHTML = '';
    updateProgress(0);

    try {
        const response = await fetch(`${API_BASE_URL}/download`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                webtoon_url: webtoonUrlInput.value.trim(),
                chapters: selectedChapters.map(ch => ch.url),
                single_pdf: singlePdf,
                high_quality: highQuality
            })
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }

        const blob = await response.blob();
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = `${currentWebtoon.title || 'webtoon'}.pdf`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(downloadUrl);
        document.body.removeChild(a);

        addLogEntry('✓ Download completed successfully!', 'success');
        showStatus('Download completed! Check your downloads folder.', 'success');
    } catch (error) {
        console.error('Download error:', error);
        addLogEntry(`✗ Error: ${error.message}`, 'error');
        showStatus(`Download failed: ${error.message}`, 'error');
    } finally {
        downloadBtn.disabled = false;
    }
}

function updateProgress(percent) {
    progressBar.style.width = percent + '%';
    progressText.textContent = Math.round(percent) + '%';
}

function addLogEntry(message, type = 'info') {
    const entry = document.createElement('div');
    entry.className = `log-entry log-${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    progressLog.appendChild(entry);
    progressLog.scrollTop = progressLog.scrollHeight;
}

function showStatus(message, type) {
    statusMessage.textContent = message;
    statusMessage.className = `status-message ${type}`;
    statusMessage.classList.remove('hidden');

    if (type === 'success') {
        setTimeout(() => {
            statusMessage.classList.add('hidden');
        }, 4000);
    }
}

function showLoading(show) {
    if (show) {
        loadingState.classList.remove('hidden');
    } else {
        loadingState.classList.add('hidden');
    }
}

function hideAllSections() {
    infoSection.classList.add('hidden');
    chapterSection.classList.add('hidden');
    downloadSection.classList.add('hidden');
    progressSection.classList.add('hidden');
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    console.log('Webtoon Downloader ready!');
    // Check if backend is available
    fetch(`${API_BASE_URL}/health`)
        .catch(err => {
            console.error('Backend not available:', err);
            showStatus('⚠️ Backend server not running. Start it with: python backend/app.py', 'info');
        });
});
