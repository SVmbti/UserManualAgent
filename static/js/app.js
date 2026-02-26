// =====================================================================
// User Manual Agent — Frontend Logic
// =====================================================================

// ----- Index Page: Scan Form -----

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('scan-form');
    const authSelect = document.getElementById('auth_type');
    const maxPagesSlider = document.getElementById('max_pages');
    const maxPagesValue = document.getElementById('max-pages-value');

    // Auth type toggle
    if (authSelect) {
        authSelect.addEventListener('change', () => {
            document.querySelectorAll('.auth-fields').forEach(el => {
                el.style.display = 'none';
            });
            const selected = authSelect.value;
            if (selected !== 'none') {
                const target = document.getElementById(`auth-${selected}`);
                if (target) target.style.display = 'block';
            }
        });
    }

    // Range slider label
    if (maxPagesSlider && maxPagesValue) {
        maxPagesSlider.addEventListener('input', () => {
            maxPagesValue.textContent = maxPagesSlider.value;
        });
    }

    // Form submission
    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();

            const btn = document.getElementById('scan-btn');
            const btnText = btn.querySelector('.btn-text');
            const btnLoader = btn.querySelector('.btn-loader');
            btn.disabled = true;
            btnText.textContent = 'Starting...';
            btnLoader.style.display = 'inline-block';

            const formData = new FormData(form);
            const data = {};
            formData.forEach((val, key) => { data[key] = val; });

            // Map basic auth fields
            if (data.auth_type === 'basic') {
                data.username = data.basic_username || '';
                data.password = data.basic_password || '';
            }

            try {
                const res = await fetch('/scan', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data),
                });
                const result = await res.json();

                if (result.error) {
                    alert(result.error);
                    btn.disabled = false;
                    btnText.textContent = 'Start Scan';
                    btnLoader.style.display = 'none';
                    return;
                }

                // Redirect to progress page
                window.location.href = `/scan/${result.scan_id}/progress`;
            } catch (err) {
                alert('Failed to start scan: ' + err.message);
                btn.disabled = false;
                btnText.textContent = 'Start Scan';
                btnLoader.style.display = 'none';
            }
        });
    }
});


// ----- Progress Page -----

let pollInterval = null;

function initProgress(scanId) {
    pollInterval = setInterval(() => pollStatus(scanId), 2000);
    pollStatus(scanId); // immediate first poll
}

async function pollStatus(scanId) {
    try {
        const res = await fetch(`/scan/${scanId}/status`);
        const data = await res.json();

        if (data.error && data.status !== 'done') {
            updateError(data.error);
            clearInterval(pollInterval);
            return;
        }

        updateProgressUI(data, scanId);

        if (data.status === 'done' || data.status === 'error') {
            clearInterval(pollInterval);
        }
    } catch (err) {
        console.error('Poll error:', err);
    }
}

function updateProgressUI(data, scanId) {
    const phaseTitle = document.getElementById('phase-title');
    const statusBadge = document.getElementById('status-badge');
    const progressBar = document.getElementById('progress-bar');
    const pagesVisited = document.getElementById('pages-visited');
    const pagesQueued = document.getElementById('pages-queued');
    const phaseLabel = document.getElementById('phase-label');
    const currentUrl = document.getElementById('current-url');

    // Phase title
    const phaseTitles = {
        crawling: '🔍 Crawling pages...',
        analyzing: '🤖 Analyzing pages...',
        generating: '📄 Generating manual...',
        done: '✅ Manual Ready!',
    };
    if (phaseTitle) {
        phaseTitle.textContent = phaseTitles[data.phase] || data.phase || 'Working...';
    }

    // Status badge
    if (statusBadge) {
        statusBadge.textContent = data.status;
        statusBadge.className = 'status-badge';
        if (data.status === 'done') statusBadge.classList.add('done');
        if (data.status === 'error') statusBadge.classList.add('error');
    }

    // Progress bar
    if (progressBar) {
        let pct = 0;
        if (data.phase === 'crawling' && data.total_queued > 0) {
            pct = Math.min((data.visited / data.total_queued) * 70, 70);
        } else if (data.phase === 'analyzing') {
            pct = 70 + (data.page_count > 0 ? 20 : 0);
        } else if (data.phase === 'generating') {
            pct = 92;
        } else if (data.status === 'done') {
            pct = 100;
        }
        progressBar.style.width = pct + '%';
    }

    // Stats
    if (pagesVisited) pagesVisited.textContent = data.visited || 0;
    if (pagesQueued) pagesQueued.textContent = data.total_queued || 0;
    if (phaseLabel) {
        const labels = { crawling: 'Crawling', analyzing: 'Analyzing', generating: 'Building', done: 'Done' };
        phaseLabel.textContent = labels[data.phase] || data.phase || '—';
    }

    // Current URL
    if (currentUrl) {
        currentUrl.textContent = data.current_title || data.current_url || 'Working...';
    }

    // Done state
    if (data.status === 'done') {
        const doneActions = document.getElementById('done-actions');
        if (doneActions) {
            doneActions.style.display = 'flex';
            document.getElementById('view-manual-btn').href = `/scan/${scanId}/manual`;
            document.getElementById('download-html-btn').href = `/scan/${scanId}/download/html`;
            document.getElementById('download-md-btn').href = `/scan/${scanId}/download/md`;
        }
        // Stop shimmer animation
        if (progressBar) progressBar.style.setProperty('--shimmer', 'none');
    }

    // Error
    if (data.status === 'error') {
        updateError(data.error || 'An unknown error occurred.');
    }
}

function updateError(message) {
    const errBox = document.getElementById('error-message');
    if (errBox) {
        errBox.textContent = message;
        errBox.style.display = 'block';
    }

    const statusBadge = document.getElementById('status-badge');
    if (statusBadge) {
        statusBadge.textContent = 'Error';
        statusBadge.className = 'status-badge error';
    }
}
