importScripts("config.js");

const TARGET_URL = "https://online.udvash-unmesh.com/";

// Track whether we've already sent "on" so we don't spam it
let isOn = false;

// ── Helper: send a signal to the VPS ─────────────────────────────────────────

async function sendSignal(signal) {
    try {
        await fetch(SERVER_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                user_id: USER_ID,
                signal:  signal,
                secret:  SECRET
            })
        });
        console.log(`[Udvash Signal] Sent: ${signal}`);
    } catch (err) {
        console.error(`[Udvash Signal] Failed to send '${signal}':`, err);
    }
}

// ── Check if the target site is open in ANY tab ───────────────────────────────

async function isTargetOpen() {
    const tabs = await chrome.tabs.query({});
    return tabs.some(tab => tab.url && tab.url.startsWith(TARGET_URL));
}

// ── Core logic: called whenever a tab changes ─────────────────────────────────

async function evaluate() {
    const open = await isTargetOpen();

    if (open && !isOn) {
        isOn = true;
        await sendSignal("off");
    } else if (!open && isOn) {
        isOn = false;
        await sendSignal("on");
    }
}

// ── Event listeners ───────────────────────────────────────────────────────────

// Tab opened or URL changed
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
    if (changeInfo.status === "complete") {
        await evaluate();
    }
});

// Tab closed
chrome.tabs.onRemoved.addListener(async () => {
    await evaluate();
});

// Tab navigated away (covers going to a different site in same tab)
chrome.tabs.onActivated.addListener(async () => {
    await evaluate();
});

// Window closed — service workers may not always catch this, so we also
// use a periodic alarm as a fallback heartbeat every 1 minute
chrome.alarms.create("heartbeat", { periodInMinutes: 1 });

chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name === "heartbeat") {
        await evaluate();
    }
});

// On extension startup (e.g. browser restart), re-evaluate state
chrome.runtime.onStartup.addListener(async () => {
    isOn = false; // reset state on browser start
    await evaluate();
});
