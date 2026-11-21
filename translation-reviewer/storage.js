/**
 * IndexedDB autosave & Progress JSON import/export layer.
 */
(function (global) {
  'use strict';

  const DB_NAME = 'leapmotor_translation_reviewer';
  const DB_VERSION = 1;
  const STORE_NAME = 'sessions';
  const ACTIVE_KEY = 'active';
  const PROGRESS_FORMAT = 'translation-review-progress';
  const PROGRESS_VERSION = 1;

  let dbPromise = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      if (!global.indexedDB) {
        reject(new Error('IndexedDB is not supported in this browser.'));
        return;
      }
      const req = global.indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('Failed to open IndexedDB.'));
    });
    return dbPromise;
  }

  async function saveActiveSession(session) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite');
      const store = tx.objectStore(STORE_NAME);
      const payload = {
        ...session,
        savedAt: new Date().toISOString(),
      };
      store.put(payload, ACTIVE_KEY);
      tx.oncomplete = () => resolve(payload.savedAt);
      tx.onerror = () => reject(tx.error || new Error('Failed to save session to IndexedDB.'));
    });
  }

  async function loadActiveSession() {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readonly');
      const store = tx.objectStore(STORE_NAME);
      const req = store.get(ACTIVE_KEY);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error || new Error('Failed to read session from IndexedDB.'));
    });
  }

  async function clearActiveSession() {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite');
      const store = tx.objectStore(STORE_NAME);
      store.delete(ACTIVE_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error || new Error('Failed to clear IndexedDB session.'));
    });
  }

  function computeStats(items) {
    let approved = 0;
    let edited = 0;
    let missing = 0;
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.status === 'approved') approved++;
      if (it.currentHr !== it.originalHr) edited++;
      if (it.missingInHr) missing++;
    }
    const total = items.length;
    return {
      total,
      approved,
      pending: total - approved,
      edited,
      missing,
      percent: total > 0 ? Math.round((approved / total) * 1000) / 10 : 0,
    };
  }

  function buildProgressPayload(session) {
    return {
      format: PROGRESS_FORMAT,
      version: PROGRESS_VERSION,
      exportedAt: new Date().toISOString(),
      enFileName: session.enFileName || 'en.json',
      hrFileName: session.hrFileName || 'hr.json',
      currentIndex: session.currentIndex || 0,
      stats: computeStats(session.items || []),
      masterAst: session.masterAst,
      items: (session.items || []).map((item) => ({
        path: item.path,
        pathId: item.pathId,
        displayKey: item.displayKey,
        group: item.group,
        shortKey: item.shortKey,
        enValue: item.enValue,
        originalHr: item.originalHr,
        currentHr: item.currentHr,
        status: item.status === 'approved' ? 'approved' : 'pending',
        missingInHr: Boolean(item.missingInHr),
        updatedAt: item.updatedAt || null,
      })),
    };
  }

  function parseProgressPayload(rawText) {
    let data;
    try {
      data = JSON.parse(rawText);
    } catch (err) {
      throw new Error('Progress file is not valid JSON.');
    }
    if (!data || typeof data !== 'object' || !Array.isArray(data.items)) {
      throw new Error('Unrecognized progress file format (missing items array).');
    }
    if (data.format && data.format !== PROGRESS_FORMAT) {
      throw new Error(`Unsupported progress format "${data.format}".`);
    }
    return data;
  }

  global.ReviewStorage = {
    saveActiveSession,
    loadActiveSession,
    clearActiveSession,
    computeStats,
    buildProgressPayload,
    parseProgressPayload,
  };
})(window);
