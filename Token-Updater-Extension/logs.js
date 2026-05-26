// logs.js - Log viewer page script

function formatTime(isoString) {
    const date = new Date(isoString);
    const now = new Date();

    if (date.toDateString() === now.toDateString()) {
        return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
        return 'Yesterday ' + date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    }

    return date.toLocaleString('en-US', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function renderLogs(logs) {
    const container = document.getElementById('logsContainer');

    if (!logs || logs.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📝</div>
                <div>No log entries yet</div>
            </div>
        `;
        return;
    }

    container.innerHTML = logs.map(log => {
        const detailsHtml = log.details
            ? `<div class="log-details">${JSON.stringify(log.details, null, 2)}</div>`
            : '';

        return `
            <div class="log-entry ${log.level}">
                <div class="log-header">
                    <span class="log-level ${log.level}">${log.level}</span>
                    <span class="log-time">${formatTime(log.timestamp)}</span>
                </div>
                <div class="log-message">${log.message}</div>
                ${detailsHtml}
            </div>
        `;
    }).join('');
}

async function loadLogs() {
    chrome.runtime.sendMessage({ action: 'getLogs' }, (response) => {
        if (response && response.success) {
            renderLogs(response.logs);
        } else {
            document.getElementById('logsContainer').innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">❌</div>
                    <div>Failed to load logs</div>
                </div>
            `;
        }
    });
}

async function clearLogs() {
    if (!confirm('Clear all logs?')) {
        return;
    }

    chrome.runtime.sendMessage({ action: 'clearLogs' }, (response) => {
        if (response && response.success) {
            loadLogs();
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    loadLogs();

    document.getElementById('refreshBtn').addEventListener('click', loadLogs);
    document.getElementById('clearBtn').addEventListener('click', clearLogs);
    document.getElementById('backBtn').addEventListener('click', () => {
        window.location.href = 'popup.html';
    });

    // Auto-refresh every 5 seconds
    setInterval(loadLogs, 5000);
});
