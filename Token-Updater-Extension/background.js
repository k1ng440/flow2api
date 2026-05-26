// background.js - Chrome extension service worker

const ALARM_NAME = 'tokenRefresh';

const Logger = {
    async log(level, message, details = null) {
        const timestamp = new Date().toISOString();
        const logEntry = {
            timestamp,
            level,
            message,
            details
        };

        console.log(`[${level}] ${message}`, details || '');

        // Store in chrome.storage.local (persists across sessions)
        const { logs = [] } = await chrome.storage.local.get(['logs']);
        logs.unshift(logEntry); // newest first

        // Keep only the last 50 entries
        if (logs.length > 50) {
            logs.splice(50);
        }

        await chrome.storage.local.set({ logs });
    },

    info(message, details) {
        return this.log('INFO', message, details);
    },

    error(message, details) {
        return this.log('ERROR', message, details);
    },

    success(message, details) {
        return this.log('SUCCESS', message, details);
    },

    async getLogs() {
        const { logs = [] } = await chrome.storage.local.get(['logs']);
        return logs;
    },

    async clearLogs() {
        await chrome.storage.local.set({ logs: [] });
    }
};

// Initialize: set up alarm on install
chrome.runtime.onInstalled.addListener(async () => {
    await Logger.info('Flow2API Token Updater installed');
    await setupAlarm();
});

// Listen for messages from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'updateConfig') {
        setupAlarm().then(async () => {
            await Logger.info('Config updated, alarm reset');
        });
    } else if (request.action === 'testNow') {
        extractAndSendToken().then((result) => {
            sendResponse(result);
        }).catch((error) => {
            sendResponse({ success: false, error: error.message });
        });
        return true; // keep message channel open
    } else if (request.action === 'getLogs') {
        Logger.getLogs().then((logs) => {
            sendResponse({ success: true, logs });
        });
        return true;
    } else if (request.action === 'clearLogs') {
        Logger.clearLogs().then(() => {
            sendResponse({ success: true });
        });
        return true;
    }
});

// Handle alarm firing
chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name === ALARM_NAME) {
        await Logger.info('Alarm triggered, extracting token...');
        const result = await extractAndSendToken();

        if (result.success) {
            const title = result.action === 'updated' ? '✅ Token updated' : '✅ Token added';
            const message = result.displayMessage || result.message || 'Token successfully synced to Flow2API';

            chrome.notifications.create({
                type: 'basic',
                iconUrl: 'icon48.png',
                title: title,
                message: message
            });
        } else {
            chrome.notifications.create({
                type: 'basic',
                iconUrl: 'icon48.png',
                title: '❌ Token sync failed',
                message: result.error || 'Unknown error'
            });
        }
    }
});

// Set up the repeating alarm
async function setupAlarm() {
    await chrome.alarms.clear(ALARM_NAME);

    const config = await chrome.storage.sync.get(['refreshInterval']);
    const intervalMinutes = config.refreshInterval || 60;

    chrome.alarms.create(ALARM_NAME, {
        periodInMinutes: intervalMinutes
    });

    await Logger.info(`Alarm set to ${intervalMinutes} minutes`);
}

// Extract cookie and send to server
async function extractAndSendToken() {
    let tab = null;

    try {
        await Logger.info('Starting token extraction...');

        const config = await chrome.storage.sync.get(['apiUrl', 'connectionToken']);

        if (!config.apiUrl || !config.connectionToken) {
            await Logger.error('Config not set');
            return { success: false, error: 'Config not set' };
        }

        await Logger.info('Config loaded', { apiUrl: config.apiUrl });

        // 1. Open Google Labs page in background
        await Logger.info('Opening Google Labs page...');
        tab = await chrome.tabs.create({
            url: 'https://labs.google/fx/vi/tools/flow',
            active: false
        });

        await Logger.info('Tab created, waiting for load...', { tabId: tab.id });

        // Wait for page to fully load
        await new Promise((resolve) => {
            const listener = (tabId, changeInfo) => {
                if (tabId === tab.id && changeInfo.status === 'complete') {
                    chrome.tabs.onUpdated.removeListener(listener);
                    resolve();
                }
            };
            chrome.tabs.onUpdated.addListener(listener);
        });

        await Logger.info('Page loaded, waiting for JavaScript to execute...');

        // Wait 5 seconds to ensure JS has fully executed
        await new Promise(resolve => setTimeout(resolve, 5000));

        await Logger.info('Extracting cookies...');

        // 2. Get session-token
        let sessionToken = null;
        let allCookiesFound = [];

        try {
            // Method 1: get cookies by tab URL
            const tabCookies = await chrome.cookies.getAll({ url: 'https://labs.google/fx/vi/tools/flow' });
            allCookiesFound.push(...tabCookies);
            await Logger.info(`Found ${tabCookies.length} cookies from tab URL`);

            // Method 2: get all cookies for labs.google domain
            const labsCookies = await chrome.cookies.getAll({ domain: 'labs.google' });
            allCookiesFound.push(...labsCookies);
            await Logger.info(`Found ${labsCookies.length} cookies from labs.google domain`);

            // Method 3: get all cookies for .google.com domain
            const googleCookies = await chrome.cookies.getAll({ domain: '.google.com' });
            allCookiesFound.push(...googleCookies);
            await Logger.info(`Found ${googleCookies.length} cookies from .google.com domain`);

        } catch (err) {
            await Logger.error('Failed to get cookies', { error: err.message });
        }

        // Deduplicate
        const uniqueCookies = Array.from(
            new Map(allCookiesFound.map(c => [c.name + c.domain, c])).values()
        );

        await Logger.info(`Found ${uniqueCookies.length} unique cookies total`, {
            cookieNames: uniqueCookies.map(c => ({ name: c.name, domain: c.domain }))
        });

        // Find session-token
        for (const cookie of uniqueCookies) {
            if (cookie.name === '__Secure-next-auth.session-token' && !sessionToken) {
                sessionToken = cookie.value;
                await Logger.success('Found session-token', {
                    domain: cookie.domain,
                    path: cookie.path,
                    length: sessionToken.length
                });
                break;
            }
        }

        // Close tab
        if (tab) {
            await chrome.tabs.remove(tab.id);
            await Logger.info('Tab closed');
        }

        if (!sessionToken) {
            await Logger.error('session-token not found', {
                foundCookies: uniqueCookies.map(c => ({
                    name: c.name,
                    domain: c.domain
                }))
            });

            return {
                success: false,
                error: 'session-token not found. Make sure you are logged in to Google Labs.'
            };
        }

        await Logger.info('Session-token extracted', { tokenLength: sessionToken.length });

        // 4. Send to server
        await Logger.info('Sending to server...');

        const response = await fetch(config.apiUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${config.connectionToken}`
            },
            body: JSON.stringify({
                session_token: sessionToken
            })
        });

        if (!response.ok) {
            const errorText = await response.text();
            await Logger.error('Server error', {
                status: response.status,
                error: errorText
            });
            return { success: false, error: `Server error: ${response.status}` };
        }

        const result = await response.json();

        if (result.action === 'updated') {
            await Logger.success('✅ Token updated upstream', {
                action: 'Updated existing token',
                message: result.message
            });
        } else if (result.action === 'added') {
            await Logger.success('✅ Token added upstream', {
                action: 'Added new token',
                message: result.message
            });
        } else {
            await Logger.success('✅ Token synced upstream', result);
        }

        return {
            success: true,
            message: result.message || 'Token updated successfully',
            action: result.action,
            displayMessage: result.action === 'updated'
                ? `✅ Successfully updated upstream\n${result.message}`
                : `✅ Successfully added upstream\n${result.message}`
        };

    } catch (error) {
        await Logger.error('Extraction failed', {
            error: error.message,
            stack: error.stack
        });

        // Ensure tab is closed on error
        if (tab) {
            try {
                await chrome.tabs.remove(tab.id);
            } catch (e) {
                // ignore tab close errors
            }
        }

        return { success: false, error: error.message };
    }
}
