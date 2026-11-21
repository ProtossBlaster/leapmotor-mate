(function () {
  'use strict';

  const {
    parseOrderedJson,
    flattenMasterStrings,
    buildLeafLookup,
    serializeOrderedJson,
    extractPlaceholders,
    checkPlaceholders,
  } = window.OrderedJson;

  const {
    saveActiveSession,
    loadActiveSession,
    clearActiveSession,
    computeStats,
    buildProgressPayload,
    parseProgressPayload,
  } = window.ReviewStorage;

  // Application state
  const state = {
    session: null, // { enFileName, hrFileName, masterAst, currentIndex, items }
    filter: 'all', // 'all' | 'pending' | 'approved' | 'edited'
    searchQuery: '',
    visibleIndices: [],
    rowByIndex: new Map(),
    stagedOnboarding: { en: null, hr: null },
    stagedModal: { en: null, hr: null },
    saveTimer: null,
    isDirty: false,
  };

  // DOM refs
  const els = {
    filePairLabel: document.getElementById('filePairLabel'),
    topbarProgress: document.getElementById('topbarProgress'),
    progressSummaryText: document.getElementById('progressSummaryText'),
    progressPercentText: document.getElementById('progressPercentText'),
    progressTrack: document.querySelector('.progress-track'),
    progressFill: document.getElementById('progressFill'),
    editedCountBadge: document.getElementById('editedCountBadge'),
    autosaveBadge: document.getElementById('autosaveBadge'),
    autosaveText: document.getElementById('autosaveText'),

    openUploadBtn: document.getElementById('openUploadBtn'),
    importProgressBtn: document.getElementById('importProgressBtn'),
    exportProgressBtn: document.getElementById('exportProgressBtn'),
    exportHrBtn: document.getElementById('exportHrBtn'),

    enFileInput: document.getElementById('enFileInput'),
    hrFileInput: document.getElementById('hrFileInput'),
    progressFileInput: document.getElementById('progressFileInput'),

    onboardingView: document.getElementById('onboardingView'),
    onboardingDropArea: document.getElementById('onboardingDropArea'),
    enDropzone: document.getElementById('enDropzone'),
    hrDropzone: document.getElementById('hrDropzone'),
    enDropStatus: document.getElementById('enDropStatus'),
    hrDropStatus: document.getElementById('hrDropStatus'),
    enDropBadge: document.getElementById('enDropBadge'),
    hrDropBadge: document.getElementById('hrDropBadge'),
    uploadValidationBanner: document.getElementById('uploadValidationBanner'),
    onboardingImportBtn: document.getElementById('onboardingImportBtn'),
    startReviewBtn: document.getElementById('startReviewBtn'),

    workspaceView: document.getElementById('workspaceView'),
    sidebarVisibleCount: document.getElementById('sidebarVisibleCount'),
    searchInput: document.getElementById('searchInput'),
    clearSearchBtn: document.getElementById('clearSearchBtn'),
    filterPills: Array.from(document.querySelectorAll('.filter-pill')),
    jumpNextPendingBtn: document.getElementById('jumpNextPendingBtn'),
    stringList: document.getElementById('stringList'),
    stringListEmpty: document.getElementById('stringListEmpty'),

    itemPositionBadge: document.getElementById('itemPositionBadge'),
    itemGroupBadge: document.getElementById('itemGroupBadge'),
    itemStatusBadge: document.getElementById('itemStatusBadge'),
    itemMissingBadge: document.getElementById('itemMissingBadge'),
    itemKeyPath: document.getElementById('itemKeyPath'),
    copyKeyBtn: document.getElementById('copyKeyBtn'),
    prevBtn: document.getElementById('prevBtn'),
    nextBtn: document.getElementById('nextBtn'),

    enSource: document.getElementById('enSource'),
    enMetaCount: document.getElementById('enMetaCount'),
    enPlaceholders: document.getElementById('enPlaceholders'),
    copyEnBtn: document.getElementById('copyEnBtn'),

    hrEditorCard: document.getElementById('hrEditorCard'),
    hrEditor: document.getElementById('hrEditor'),
    hrMetaCount: document.getElementById('hrMetaCount'),
    editorFocusPill: document.getElementById('editorFocusPill'),
    placeholderValidation: document.getElementById('placeholderValidation'),
    revertEditBtn: document.getElementById('revertEditBtn'),

    unapproveBtn: document.getElementById('unapproveBtn'),
    approveNextBtn: document.getElementById('approveNextBtn'),

    uploadModal: document.getElementById('uploadModal'),
    closeUploadModalBtn: document.getElementById('closeUploadModalBtn'),
    cancelUploadModalBtn: document.getElementById('cancelUploadModalBtn'),
    modalEnDropzone: document.getElementById('modalEnDropzone'),
    modalHrDropzone: document.getElementById('modalHrDropzone'),
    modalEnStatus: document.getElementById('modalEnStatus'),
    modalHrStatus: document.getElementById('modalHrStatus'),
    modalEnBadge: document.getElementById('modalEnBadge'),
    modalHrBadge: document.getElementById('modalHrBadge'),
    preserveMatchesCheckbox: document.getElementById('preserveMatchesCheckbox'),
    resetSessionBtn: document.getElementById('resetSessionBtn'),
    applyModalUploadBtn: document.getElementById('applyModalUploadBtn'),

    exportModal: document.getElementById('exportModal'),
    closeExportModalBtn: document.getElementById('closeExportModalBtn'),
    cancelExportModalBtn: document.getElementById('cancelExportModalBtn'),
    exportModalBody: document.getElementById('exportModalBody'),
    exportModalStats: document.getElementById('exportModalStats'),
    confirmExportHrBtn: document.getElementById('confirmExportHrBtn'),

    toastContainer: document.getElementById('toastContainer'),
  };

  let filePickerTarget = { mode: 'onboarding', slot: 'en' };
  let pendingEditorFocusOnKeyup = false;

  function showToast(message, isError = false) {
    els.toastContainer.replaceChildren();
    const toast = document.createElement('div');
    toast.className = isError ? 'toast toast-error' : 'toast';
    toast.textContent = message;
    els.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity 180ms ease';
      setTimeout(() => toast.remove(), 200);
    }, 2200);
  }

  function formatCount(n, singular, plural) {
    return `${n} ${n === 1 ? singular : plural}`;
  }

  function formatTimeShort(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function downloadFile(filename, content, mimeType = 'application/json;charset=utf-8') {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function setAutosaveIndicator(statusText, saving = false) {
    els.autosaveBadge.classList.toggle('saving', saving);
    els.autosaveText.textContent = statusText;
  }

  function scheduleAutosave(immediate = false) {
    if (!state.session) return;
    state.isDirty = true;
    setAutosaveIndicator('Saving…', true);

    if (state.saveTimer) {
      clearTimeout(state.saveTimer);
      state.saveTimer = null;
    }

    const performSave = async () => {
      if (!state.session) return;
      try {
        const savedAt = await saveActiveSession(state.session);
        state.isDirty = false;
        setAutosaveIndicator(`Autosaved ${formatTimeShort(savedAt)}`, false);
      } catch (err) {
        console.error(err);
        setAutosaveIndicator('Autosave failed', true);
      }
    };

    if (immediate) {
      performSave();
    } else {
      state.saveTimer = setTimeout(performSave, 160);
    }
  }

  /**
   * Build a fresh session from parsed en.json and hr.json, keeping master key order.
   */
  function buildSessionFromFiles(enData, hrData, previousItemsByPathId = null) {
    const masterLeaves = flattenMasterStrings(enData.ast);
    if (masterLeaves.length === 0) {
      throw new Error('Master en.json contains no string entries.');
    }
    const hrLookup = buildLeafLookup(hrData.ast);

    const items = masterLeaves.map((leaf) => {
      const hasHr = hrLookup.has(leaf.pathId);
      const originalHr = hasHr ? hrLookup.get(leaf.pathId) : '';
      const prev = previousItemsByPathId ? previousItemsByPathId.get(leaf.pathId) : null;

      let currentHr = originalHr;
      let status = 'pending';
      let updatedAt = null;

      if (prev) {
        status = prev.status === 'approved' ? 'approved' : 'pending';
        updatedAt = prev.updatedAt || null;
        if (prev.currentHr !== prev.originalHr && typeof prev.currentHr === 'string') {
          currentHr = prev.currentHr;
        }
      }

      return {
        path: leaf.path,
        pathId: leaf.pathId,
        displayKey: leaf.displayKey,
        group: leaf.group,
        shortKey: leaf.shortKey,
        enValue: leaf.enValue,
        originalHr,
        currentHr,
        status,
        missingInHr: !hasHr,
        updatedAt,
      };
    });

    // Position cursor on first pending string if starting fresh or carrying over
    let startIdx = items.findIndex((it) => it.status !== 'approved');
    if (startIdx < 0) startIdx = 0;

    return {
      version: 1,
      enFileName: enData.fileName,
      hrFileName: hrData.fileName,
      masterAst: enData.ast,
      currentIndex: startIdx,
      items,
    };
  }

  async function readJsonUploadFile(file) {
    const text = await file.text();
    const ast = parseOrderedJson(text);
    const leaves = flattenMasterStrings(ast);
    return {
      fileName: file.name,
      ast,
      leafCount: leaves.length,
    };
  }

  function updateStagedUi(mode) {
    const staged = mode === 'onboarding' ? state.stagedOnboarding : state.stagedModal;
    const enStatus = mode === 'onboarding' ? els.enDropStatus : els.modalEnStatus;
    const hrStatus = mode === 'onboarding' ? els.hrDropStatus : els.modalHrStatus;
    const enBadge = mode === 'onboarding' ? els.enDropBadge : els.modalEnBadge;
    const hrBadge = mode === 'onboarding' ? els.hrDropBadge : els.modalHrBadge;
    const enZone = mode === 'onboarding' ? els.enDropzone : els.modalEnDropzone;
    const hrZone = mode === 'onboarding' ? els.hrDropzone : els.modalHrDropzone;
    const submitBtn = mode === 'onboarding' ? els.startReviewBtn : els.applyModalUploadBtn;

    if (staged.en) {
      enZone.classList.add('loaded');
      enStatus.textContent = `${staged.en.fileName} (${staged.en.leafCount.toLocaleString()} strings)`;
      enBadge.textContent = 'Loaded';
    } else {
      enZone.classList.remove('loaded');
      enStatus.textContent = mode === 'onboarding' ? 'Drop or click to browse' : 'Choose or drop en.json';
      enBadge.textContent = mode === 'onboarding' ? 'Required' : 'Pending';
    }

    if (staged.hr) {
      hrZone.classList.add('loaded');
      hrStatus.textContent = `${staged.hr.fileName} (${staged.hr.leafCount.toLocaleString()} strings)`;
      hrBadge.textContent = 'Loaded';
    } else {
      hrZone.classList.remove('loaded');
      hrStatus.textContent = mode === 'onboarding' ? 'Drop or click to browse' : 'Choose or drop hr.json';
      hrBadge.textContent = mode === 'onboarding' ? 'Required' : 'Pending';
    }

    submitBtn.disabled = !(staged.en && staged.hr);

    if (mode === 'onboarding') {
      if (staged.en && staged.hr) {
        const enLeaves = flattenMasterStrings(staged.en.ast);
        const hrLookup = buildLeafLookup(staged.hr.ast);
        const matched = enLeaves.filter((l) => hrLookup.has(l.pathId)).length;
        const missing = enLeaves.length - matched;
        els.uploadValidationBanner.classList.remove('hidden', 'error');
        els.uploadValidationBanner.textContent =
          missing === 0
            ? `Ready: ${enLeaves.length.toLocaleString()} master keys matched in exact en.json order.`
            : `Ready: ${enLeaves.length.toLocaleString()} master keys (${matched.toLocaleString()} matched, ${missing.toLocaleString()} missing in hr.json).`;
      } else {
        els.uploadValidationBanner.classList.add('hidden');
      }
    }
  }

  async function handleFileIntoSlot(file, mode, slot) {
    try {
      const parsed = await readJsonUploadFile(file);
      const staged = mode === 'onboarding' ? state.stagedOnboarding : state.stagedModal;
      staged[slot] = parsed;
      updateStagedUi(mode);
    } catch (err) {
      showToast(`Invalid JSON in ${file.name}: ${err.message}`, true);
      if (mode === 'onboarding') {
        els.uploadValidationBanner.classList.remove('hidden');
        els.uploadValidationBanner.classList.add('error');
        els.uploadValidationBanner.textContent = `Could not parse ${file.name}: ${err.message}`;
      }
    }
  }

  async function handleDroppedFiles(fileList, mode, preferredSlot = null) {
    const files = Array.from(fileList || []).filter((f) => f.name.toLowerCase().endsWith('.json') || f.type.includes('json'));
    if (files.length === 0) {
      showToast('Please drop .json files.', true);
      return;
    }

    if (files.length === 1 && preferredSlot) {
      await handleFileIntoSlot(files[0], mode, preferredSlot);
      return;
    }

    for (const file of files) {
      const lower = file.name.toLowerCase();
      if (lower.includes('hr')) {
        await handleFileIntoSlot(file, mode, 'hr');
      } else if (lower.includes('en')) {
        await handleFileIntoSlot(file, mode, 'en');
      } else if (preferredSlot) {
        await handleFileIntoSlot(file, mode, preferredSlot);
      } else {
        const staged = mode === 'onboarding' ? state.stagedOnboarding : state.stagedModal;
        await handleFileIntoSlot(file, mode, staged.en ? 'hr' : 'en');
      }
    }
  }

  function wireDropzone(zone, mode, slot) {
    zone.addEventListener('click', () => {
      filePickerTarget = { mode, slot };
      const input = slot === 'en' ? els.enFileInput : els.hrFileInput;
      input.value = '';
      input.click();
    });

    zone.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        zone.click();
      }
    });

    zone.addEventListener('dragover', (e) => {
      e.preventDefault();
      zone.classList.add('drag-over');
    });

    zone.addEventListener('dragleave', () => {
      zone.classList.remove('drag-over');
    });

    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.remove('drag-over');
      handleDroppedFiles(e.dataTransfer.files, mode, slot);
    });
  }

  function openWorkspaceWithSession(session, toastMsg = null) {
    state.session = session;
    state.filter = 'all';
    state.searchQuery = '';
    els.searchInput.value = '';
    els.clearSearchBtn.classList.add('hidden');
    els.filterPills.forEach((btn) => btn.classList.toggle('active', btn.dataset.filter === 'all'));

    els.onboardingView.classList.add('hidden');
    els.workspaceView.classList.remove('hidden');
    els.topbarProgress.classList.remove('hidden');
    els.openUploadBtn.classList.remove('hidden');
    els.exportProgressBtn.classList.remove('hidden');
    els.exportHrBtn.classList.remove('hidden');

    els.filePairLabel.textContent = `${session.enFileName} → ${session.hrFileName} · Master order`;

    rebuildStringList();
    renderProgressStats();
    selectIndex(Math.min(Math.max(0, session.currentIndex || 0), session.items.length - 1), {
      scrollSidebar: true,
      skipSave: true,
    });

    scheduleAutosave(true);
    if (toastMsg) showToast(toastMsg);
  }

  function itemMatchesFilterAndSearch(item, filter, query) {
    if (filter === 'pending' && item.status === 'approved') return false;
    if (filter === 'approved' && item.status !== 'approved') return false;
    if (filter === 'edited' && item.currentHr === item.originalHr) return false;

    if (query) {
      const q = query.toLowerCase();
      return (
        item.displayKey.toLowerCase().includes(q) ||
        item.enValue.toLowerCase().includes(q) ||
        item.currentHr.toLowerCase().includes(q)
      );
    }
    return true;
  }

  function getRowClasses(item, idx) {
    const classes = ['string-row'];
    if (idx === state.session.currentIndex) classes.push('active');
    if (item.status === 'approved') classes.push('is-approved');
    if (item.currentHr !== item.originalHr) classes.push('is-edited');
    return classes.join(' ');
  }

  function updateRowElement(idx) {
    const row = state.rowByIndex.get(idx);
    if (!row || !state.session) return;
    const item = state.session.items[idx];
    row.className = getRowClasses(item, idx);
    row.setAttribute('aria-selected', idx === state.session.currentIndex ? 'true' : 'false');
    const indicator = row.querySelector('.row-indicator');
    if (indicator) {
      if (item.status === 'approved') {
        indicator.textContent = '✓';
      } else if (item.currentHr !== item.originalHr) {
        indicator.textContent = '•';
      } else {
        indicator.textContent = '';
      }
    }
    const preview = row.querySelector('.row-preview');
    if (preview) {
      preview.textContent = item.currentHr || item.enValue || '—';
    }
  }

  function rebuildStringList() {
    if (!state.session) return;
    const items = state.session.items;
    const query = state.searchQuery.trim();
    const visible = [];

    for (let i = 0; i < items.length; i++) {
      if (itemMatchesFilterAndSearch(items[i], state.filter, query)) {
        visible.push(i);
      }
    }

    state.visibleIndices = visible;
    state.rowByIndex.clear();
    els.sidebarVisibleCount.textContent = visible.length.toLocaleString();

    if (visible.length === 0) {
      els.stringList.replaceChildren();
      els.stringListEmpty.classList.remove('hidden');
      return;
    }

    els.stringListEmpty.classList.add('hidden');
    const frag = document.createDocumentFragment();

    for (let v = 0; v < visible.length; v++) {
      const idx = visible[v];
      const item = items[idx];

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = getRowClasses(item, idx);
      btn.dataset.index = String(idx);
      btn.setAttribute('role', 'option');
      btn.setAttribute('aria-selected', idx === state.session.currentIndex ? 'true' : 'false');

      const indicator = document.createElement('span');
      indicator.className = 'row-indicator';
      indicator.textContent = item.status === 'approved' ? '✓' : item.currentHr !== item.originalHr ? '•' : '';

      const content = document.createElement('div');
      content.className = 'row-content';

      const top = document.createElement('div');
      top.className = 'row-top';

      const keySpan = document.createElement('span');
      keySpan.className = 'row-key';
      keySpan.textContent = item.shortKey;
      keySpan.title = item.displayKey;

      const badges = document.createElement('span');
      badges.className = 'row-badges';

      const editedDot = document.createElement('span');
      editedDot.className = 'row-edited-dot';
      editedDot.title = 'Edited';

      const idxSpan = document.createElement('span');
      idxSpan.className = 'row-index';
      idxSpan.textContent = `#${idx + 1}`;

      badges.append(editedDot, idxSpan);
      top.append(keySpan, badges);

      const preview = document.createElement('div');
      preview.className = 'row-preview';
      preview.textContent = item.currentHr || item.enValue || '—';

      content.append(top, preview);
      btn.append(indicator, content);
      frag.appendChild(btn);
      state.rowByIndex.set(idx, btn);
    }

    els.stringList.replaceChildren(frag);
  }

  function renderProgressStats() {
    if (!state.session) return;
    const stats = computeStats(state.session.items);
    els.progressSummaryText.textContent = `${stats.approved.toLocaleString()} / ${stats.total.toLocaleString()} approved`;
    els.progressPercentText.textContent = `${stats.percent}%`;
    els.progressFill.style.width = `${stats.percent}%`;
    els.progressTrack.setAttribute('aria-valuenow', String(stats.percent));
    els.editedCountBadge.textContent = `${stats.edited.toLocaleString()} edited`;
  }

  function renderPlaceholderHints(item) {
    const enTokens = extractPlaceholders(item.enValue);
    if (enTokens.length === 0) {
      els.enPlaceholders.innerHTML = '<span class="muted-hint">No template placeholders</span>';
    } else {
      els.enPlaceholders.replaceChildren();
      const label = document.createElement('span');
      label.className = 'muted-hint';
      label.textContent = 'Placeholders:';
      els.enPlaceholders.appendChild(label);
      enTokens.forEach((tok) => {
        const chip = document.createElement('span');
        chip.className = 'token-chip';
        chip.textContent = tok;
        els.enPlaceholders.appendChild(chip);
      });
    }

    const check = checkPlaceholders(item.enValue, item.currentHr);
    if (check.expected.length === 0) {
      els.placeholderValidation.innerHTML =
        item.currentHr !== item.originalHr
          ? '<span class="token-ok">Modified from original hr.json</span>'
          : '<span class="muted-hint">Unchanged from hr.json</span>';
    } else if (check.missing.length > 0) {
      els.placeholderValidation.innerHTML = `<span class="token-warn">Missing placeholder: ${check.missing.join(', ')}</span>`;
    } else {
      els.placeholderValidation.innerHTML = `<span class="token-ok">Placeholders match (${check.expected.join(', ')})</span>`;
    }
  }

  function renderActiveItem() {
    if (!state.session) return;
    const items = state.session.items;
    const idx = state.session.currentIndex;
    const item = items[idx];
    if (!item) return;

    const isEdited = item.currentHr !== item.originalHr;
    const isApproved = item.status === 'approved';
    const isLast = idx === items.length - 1;

    els.itemPositionBadge.textContent = `#${idx + 1} of ${items.length.toLocaleString()}`;
    els.itemGroupBadge.textContent = item.group;
    els.itemKeyPath.textContent = item.displayKey;

    if (isApproved && isEdited) {
      els.itemStatusBadge.className = 'status-pill status-approved';
      els.itemStatusBadge.textContent = 'Approved · Edited';
    } else if (isApproved) {
      els.itemStatusBadge.className = 'status-pill status-approved';
      els.itemStatusBadge.textContent = 'Approved';
    } else if (isEdited) {
      els.itemStatusBadge.className = 'status-pill status-edited';
      els.itemStatusBadge.textContent = 'Edited · Pending';
    } else {
      els.itemStatusBadge.className = 'status-pill status-pending';
      els.itemStatusBadge.textContent = 'Pending';
    }

    els.itemMissingBadge.classList.toggle('hidden', !item.missingInHr);
    els.unapproveBtn.classList.toggle('hidden', !isApproved);
    els.revertEditBtn.classList.toggle('hidden', !isEdited);

    els.enSource.value = item.enValue;
    els.hrEditor.value = item.currentHr;

    const enLines = item.enValue.split('\n').length;
    const hrLines = item.currentHr.split('\n').length;
    els.enMetaCount.textContent = `${item.enValue.length} chars${enLines > 1 ? ` · ${enLines} lines` : ''}`;
    els.hrMetaCount.textContent = `${item.currentHr.length} chars${hrLines > 1 ? ` · ${hrLines} lines` : ''}`;

    els.prevBtn.disabled = idx === 0;
    els.nextBtn.disabled = isLast;

    const btnLabel = els.approveNextBtn.querySelector('span');
    if (isLast) {
      btnLabel.textContent = isEdited ? 'Save & Approve Final' : 'Approve Final';
    } else {
      btnLabel.textContent = isEdited ? 'Save & Approve Next' : 'Approve & Next';
    }

    renderPlaceholderHints(item);
  }

  function selectIndex(newIdx, options = {}) {
    if (!state.session) return;
    const { scrollSidebar = true, skipSave = false } = options;
    const clamped = Math.min(Math.max(0, newIdx), state.session.items.length - 1);
    const prevIdx = state.session.currentIndex;

    state.session.currentIndex = clamped;
    renderActiveItem();

    if (prevIdx !== clamped) {
      updateRowElement(prevIdx);
    }
    updateRowElement(clamped);

    if (scrollSidebar) {
      const row = state.rowByIndex.get(clamped);
      if (row) {
        row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    }

    if (!skipSave) {
      scheduleAutosave(false);
    }
  }

  function getNextInLineIndex(fromIdx) {
    if (!state.session) return 0;
    const total = state.session.items.length;

    if (state.filter === 'all' && !state.searchQuery.trim()) {
      return fromIdx + 1 < total ? fromIdx + 1 : fromIdx;
    }

    // When a sidebar filter/search is active, advance to the next visible item after fromIdx
    for (let i = 0; i < state.visibleIndices.length; i++) {
      if (state.visibleIndices[i] > fromIdx) {
        return state.visibleIndices[i];
      }
    }
    // Fallback to sequential master order if at end of filtered list
    return fromIdx + 1 < total ? fromIdx + 1 : fromIdx;
  }

  function approveCurrentAndAdvance() {
    if (!state.session) return;
    const idx = state.session.currentIndex;
    const item = state.session.items[idx];
    if (!item) return;

    // Commit latest editor value
    item.currentHr = els.hrEditor.value;
    item.status = 'approved';
    item.updatedAt = new Date().toISOString();

    const wasLast = idx === state.session.items.length - 1;
    const nextIdx = getNextInLineIndex(idx);

    // Blur editor on advance so pressing 'E' on the next string focuses editor instead of typing 'e'
    if (document.activeElement === els.hrEditor) {
      els.hrEditor.blur();
    }

    renderProgressStats();

    if (state.filter !== 'all') {
      // Recompute filtered view since status changed
      rebuildStringList();
    } else {
      updateRowElement(idx);
    }

    if (wasLast || nextIdx === idx) {
      renderActiveItem();
      scheduleAutosave(true);
      const stats = computeStats(state.session.items);
      if (stats.pending === 0) {
        showToast('All strings approved! Ready to export hr.json.');
      } else {
        showToast(`Approved #${idx + 1}. (${stats.pending.toLocaleString()} pending earlier in file)`);
      }
      return;
    }

    selectIndex(nextIdx, { scrollSidebar: true, skipSave: true });
    scheduleAutosave(true);
  }

  function focusCroatianEditor() {
    if (!state.session) return;
    els.hrEditor.focus();
    const len = els.hrEditor.value.length;
    els.hrEditor.setSelectionRange(len, len);
  }

  function exportHrJsonFile() {
    if (!state.session) return;
    const valueMap = new Map();
    for (const item of state.session.items) {
      valueMap.set(item.pathId, item.currentHr);
    }
    const output = serializeOrderedJson(state.session.masterAst, valueMap, 2);
    const filename = state.session.hrFileName || 'hr.json';
    downloadFile(filename, output);
    showToast(`Exported ${filename} in exact master key order.`);
  }

  function openExportModalOrDownload() {
    if (!state.session) return;
    const stats = computeStats(state.session.items);
    if (stats.pending === 0) {
      exportHrJsonFile();
      return;
    }

    els.exportModalBody.textContent =
      `You still have ${stats.pending.toLocaleString()} pending string${stats.pending === 1 ? '' : 's'}. You can export hr.json right now (preserving exact master key order from ${state.session.enFileName}) or keep reviewing.`;

    els.exportModalStats.innerHTML = `
      <div class="stat-tile">
        <div class="stat-tile-value">${stats.approved.toLocaleString()}</div>
        <div class="stat-tile-label">Approved</div>
      </div>
      <div class="stat-tile">
        <div class="stat-tile-value">${stats.pending.toLocaleString()}</div>
        <div class="stat-tile-label">Pending</div>
      </div>
      <div class="stat-tile">
        <div class="stat-tile-value">${stats.edited.toLocaleString()}</div>
        <div class="stat-tile-label">Edited</div>
      </div>
    `;
    els.exportModal.classList.remove('hidden');
  }

  function exportProgressFile() {
    if (!state.session) return;
    const payload = buildProgressPayload(state.session);
    const dateSlug = new Date().toISOString().slice(0, 10);
    const filename = `hr-review-progress-${dateSlug}.json`;
    downloadFile(filename, JSON.stringify(payload, null, 2) + '\n');
    showToast(`Exported progress (${payload.stats.approved}/${payload.stats.total} approved).`);
  }

  async function importProgressFile(file) {
    try {
      const text = await file.text();
      const data = parseProgressPayload(text);

      // If no active session OR progress has masterAst and user has no session yet, restore directly
      if (!state.session && data.masterAst) {
        const restored = {
          version: 1,
          enFileName: data.enFileName || 'en.json',
          hrFileName: data.hrFileName || 'hr.json',
          masterAst: data.masterAst,
          currentIndex: data.currentIndex || 0,
          items: data.items.map((it) => ({
            path: it.path,
            pathId: it.pathId,
            displayKey: it.displayKey,
            group: it.group,
            shortKey: it.shortKey,
            enValue: it.enValue,
            originalHr: it.originalHr ?? '',
            currentHr: it.currentHr ?? '',
            status: it.status === 'approved' ? 'approved' : 'pending',
            missingInHr: Boolean(it.missingInHr),
            updatedAt: it.updatedAt || null,
          })),
        };
        const stats = computeStats(restored.items);
        openWorkspaceWithSession(restored, `Restored progress: ${stats.approved}/${stats.total} approved.`);
        return;
      }

      if (!state.session && !data.masterAst) {
        showToast('Upload en.json & hr.json first, or import a full progress snapshot.', true);
        return;
      }

      // Active session exists: check if master key sequences match identically
      const importedByPathId = new Map();
      for (const it of data.items) {
        if (it.pathId) importedByPathId.set(it.pathId, it);
      }

      let matched = 0;
      let approvedApplied = 0;
      let editedApplied = 0;

      for (const item of state.session.items) {
        const imp = importedByPathId.get(item.pathId);
        if (!imp) continue;
        matched++;
        if (typeof imp.currentHr === 'string') {
          item.currentHr = imp.currentHr;
          if (item.currentHr !== item.originalHr) editedApplied++;
        }
        item.status = imp.status === 'approved' ? 'approved' : 'pending';
        if (item.status === 'approved') approvedApplied++;
        item.updatedAt = imp.updatedAt || item.updatedAt;
      }

      if (typeof data.currentIndex === 'number' && data.currentIndex >= 0 && data.currentIndex < state.session.items.length) {
        state.session.currentIndex = data.currentIndex;
      }

      rebuildStringList();
      renderProgressStats();
      renderActiveItem();
      scheduleAutosave(true);
      showToast(
        `Imported progress: ${matched.toLocaleString()} keys matched (${approvedApplied.toLocaleString()} approved, ${editedApplied.toLocaleString()} edited).`
      );
    } catch (err) {
      showToast(`Import failed: ${err.message}`, true);
    }
  }

  function closeModals() {
    els.uploadModal.classList.add('hidden');
    els.exportModal.classList.add('hidden');
  }

  function wireEventListeners() {
    // Hidden file input handlers
    els.enFileInput.addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      if (file) handleFileIntoSlot(file, filePickerTarget.mode, 'en');
    });

    els.hrFileInput.addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      if (file) handleFileIntoSlot(file, filePickerTarget.mode, 'hr');
    });

    els.progressFileInput.addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      if (file) importProgressFile(file);
    });

    // Dropzones
    wireDropzone(els.enDropzone, 'onboarding', 'en');
    wireDropzone(els.hrDropzone, 'onboarding', 'hr');
    wireDropzone(els.modalEnDropzone, 'modal', 'en');
    wireDropzone(els.modalHrDropzone, 'modal', 'hr');

    // Multi-file drop directly onto onboarding card
    els.onboardingDropArea.addEventListener('dragover', (e) => e.preventDefault());
    els.onboardingDropArea.addEventListener('drop', (e) => {
      e.preventDefault();
      handleDroppedFiles(e.dataTransfer.files, 'onboarding', null);
    });

    // Start review from onboarding
    els.startReviewBtn.addEventListener('click', () => {
      if (!state.stagedOnboarding.en || !state.stagedOnboarding.hr) return;
      try {
        const session = buildSessionFromFiles(state.stagedOnboarding.en, state.stagedOnboarding.hr);
        openWorkspaceWithSession(session, `Loaded ${session.items.length.toLocaleString()} strings in master order.`);
      } catch (err) {
        showToast(err.message, true);
      }
    });

    // Topbar buttons
    els.openUploadBtn.addEventListener('click', () => {
      els.openUploadBtn.blur();
      if (!state.session) return;
      state.stagedModal = { en: null, hr: null };
      updateStagedUi('modal');
      els.uploadModal.classList.remove('hidden');
    });

    const triggerProgressImport = () => {
      els.progressFileInput.value = '';
      els.progressFileInput.click();
    };
    els.importProgressBtn.addEventListener('click', () => {
      els.importProgressBtn.blur();
      triggerProgressImport();
    });
    els.onboardingImportBtn.addEventListener('click', triggerProgressImport);

    els.exportProgressBtn.addEventListener('click', () => {
      els.exportProgressBtn.blur();
      exportProgressFile();
    });

    els.exportHrBtn.addEventListener('click', () => {
      els.exportHrBtn.blur();
      openExportModalOrDownload();
    });

    // Sidebar click delegation (fast for 1,600+ rows)
    els.stringList.addEventListener('click', (e) => {
      const row = e.target.closest('.string-row');
      if (!row) return;
      const idx = Number(row.dataset.index);
      if (!Number.isNaN(idx)) {
        row.blur();
        selectIndex(idx, { scrollSidebar: false });
      }
    });

    // Search & Filters
    els.searchInput.addEventListener('input', () => {
      state.searchQuery = els.searchInput.value;
      els.clearSearchBtn.classList.toggle('hidden', !state.searchQuery);
      rebuildStringList();
    });

    els.searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (state.visibleIndices.length > 0) {
          selectIndex(state.visibleIndices[0], { scrollSidebar: true });
        }
        els.searchInput.blur();
      }
    });

    els.clearSearchBtn.addEventListener('click', () => {
      state.searchQuery = '';
      els.searchInput.value = '';
      els.clearSearchBtn.classList.add('hidden');
      rebuildStringList();
      els.searchInput.focus();
    });

    els.filterPills.forEach((pill) => {
      pill.addEventListener('click', () => {
        pill.blur();
        state.filter = pill.dataset.filter;
        els.filterPills.forEach((p) => p.classList.toggle('active', p === pill));
        rebuildStringList();
      });
    });

    els.jumpNextPendingBtn.addEventListener('click', () => {
      els.jumpNextPendingBtn.blur();
      if (!state.session) return;
      const items = state.session.items;
      const start = state.session.currentIndex;
      for (let offset = 1; offset <= items.length; offset++) {
        const idx = (start + offset) % items.length;
        if (items[idx].status !== 'approved') {
          selectIndex(idx, { scrollSidebar: true });
          return;
        }
      }
      showToast('Every string is already approved!');
    });

    // Stage Navigation & Actions
    els.prevBtn.addEventListener('click', () => {
      els.prevBtn.blur();
      if (state.session && state.session.currentIndex > 0) {
        selectIndex(state.session.currentIndex - 1);
      }
    });

    els.nextBtn.addEventListener('click', () => {
      els.nextBtn.blur();
      if (state.session && state.session.currentIndex < state.session.items.length - 1) {
        selectIndex(state.session.currentIndex + 1);
      }
    });

    els.copyKeyBtn.addEventListener('click', async () => {
      els.copyKeyBtn.blur();
      if (!state.session) return;
      const key = state.session.items[state.session.currentIndex].displayKey;
      try {
        await navigator.clipboard.writeText(key);
        showToast(`Copied key: ${key}`);
      } catch {
        showToast('Clipboard permission unavailable.', true);
      }
    });

    els.copyEnBtn.addEventListener('click', async () => {
      els.copyEnBtn.blur();
      if (!state.session) return;
      const text = state.session.items[state.session.currentIndex].enValue;
      try {
        await navigator.clipboard.writeText(text);
        showToast('Copied English source text.');
      } catch {
        showToast('Clipboard permission unavailable.', true);
      }
    });

    // Croatian Editor Focus & Live Edit Tracking
    els.hrEditor.addEventListener('focus', () => {
      els.hrEditorCard.classList.add('is-focused');
      els.editorFocusPill.textContent = 'Editing';
    });

    els.hrEditor.addEventListener('blur', () => {
      els.hrEditorCard.classList.remove('is-focused');
      els.editorFocusPill.innerHTML = 'Press <kbd>E</kbd> to edit';
    });

    els.hrEditor.addEventListener('input', () => {
      if (!state.session) return;
      const idx = state.session.currentIndex;
      const item = state.session.items[idx];
      if (!item) return;

      item.currentHr = els.hrEditor.value;
      item.updatedAt = new Date().toISOString();

      const lines = item.currentHr.split('\n').length;
      els.hrMetaCount.textContent = `${item.currentHr.length} chars${lines > 1 ? ` · ${lines} lines` : ''}`;
      els.revertEditBtn.classList.toggle('hidden', item.currentHr === item.originalHr);

      renderPlaceholderHints(item);
      renderProgressStats();
      updateRowElement(idx);
      scheduleAutosave(false);
    });

    els.revertEditBtn.addEventListener('click', () => {
      els.revertEditBtn.blur();
      if (!state.session) return;
      const idx = state.session.currentIndex;
      const item = state.session.items[idx];
      item.currentHr = item.originalHr;
      item.updatedAt = new Date().toISOString();
      renderActiveItem();
      renderProgressStats();
      updateRowElement(idx);
      scheduleAutosave(true);
      showToast('Reverted translation to original hr.json.');
    });

    els.unapproveBtn.addEventListener('click', () => {
      els.unapproveBtn.blur();
      if (!state.session) return;
      const idx = state.session.currentIndex;
      const item = state.session.items[idx];
      item.status = 'pending';
      item.updatedAt = new Date().toISOString();
      renderActiveItem();
      renderProgressStats();
      if (state.filter !== 'all') {
        rebuildStringList();
      } else {
        updateRowElement(idx);
      }
      scheduleAutosave(true);
    });

    els.approveNextBtn.addEventListener('click', () => {
      els.approveNextBtn.blur();
      approveCurrentAndAdvance();
    });

    // Upload Modal Controls
    els.closeUploadModalBtn.addEventListener('click', closeModals);
    els.cancelUploadModalBtn.addEventListener('click', closeModals);
    els.uploadModal.addEventListener('click', (e) => {
      if (e.target === els.uploadModal) closeModals();
    });

    els.applyModalUploadBtn.addEventListener('click', () => {
      if (!state.stagedModal.en || !state.stagedModal.hr) return;
      try {
        let prevMap = null;
        if (els.preserveMatchesCheckbox.checked && state.session) {
          prevMap = new Map();
          for (const item of state.session.items) {
            prevMap.set(item.pathId, item);
          }
        }
        const session = buildSessionFromFiles(state.stagedModal.en, state.stagedModal.hr, prevMap);
        closeModals();
        openWorkspaceWithSession(session, `Loaded ${session.items.length.toLocaleString()} strings.`);
      } catch (err) {
        showToast(err.message, true);
      }
    });

    els.resetSessionBtn.addEventListener('click', async () => {
      await clearActiveSession();
      state.session = null;
      state.stagedOnboarding = { en: null, hr: null };
      updateStagedUi('onboarding');
      closeModals();
      els.workspaceView.classList.add('hidden');
      els.topbarProgress.classList.add('hidden');
      els.openUploadBtn.classList.add('hidden');
      els.exportProgressBtn.classList.add('hidden');
      els.exportHrBtn.classList.add('hidden');
      els.onboardingView.classList.remove('hidden');
      els.filePairLabel.textContent = 'EN → HR · Upload locale files to begin';
      showToast('Cleared saved browser session.');
    });

    // Export Modal Controls
    els.closeExportModalBtn.addEventListener('click', closeModals);
    els.cancelExportModalBtn.addEventListener('click', closeModals);
    els.exportModal.addEventListener('click', (e) => {
      if (e.target === els.exportModal) closeModals();
    });
    els.confirmExportHrBtn.addEventListener('click', () => {
      closeModals();
      exportHrJsonFile();
    });

    // Global Keyboard Shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.isComposing) return;

      const uploadOpen = !els.uploadModal.classList.contains('hidden');
      const exportOpen = !els.exportModal.classList.contains('hidden');

      if (e.key === 'Escape') {
        if (uploadOpen || exportOpen) {
          e.preventDefault();
          closeModals();
          return;
        }
        if (document.activeElement === els.hrEditor || document.activeElement === els.searchInput) {
          e.preventDefault();
          document.activeElement.blur();
        }
        return;
      }

      if (uploadOpen || exportOpen) return;

      // Onboarding Enter shortcut when both files are loaded
      if (!state.session) {
        if (e.key === 'Enter' && !e.shiftKey && !els.startReviewBtn.disabled) {
          e.preventDefault();
          els.startReviewBtn.click();
        }
        return;
      }

      const activeEl = document.activeElement;
      const isInEditor = activeEl === els.hrEditor;
      const isInSearch = activeEl === els.searchInput;
      const isInAnyTextControl =
        activeEl &&
        (activeEl.tagName === 'TEXTAREA' || activeEl.tagName === 'INPUT' || activeEl.isContentEditable);

      // Inside Croatian editor:
      // - Shift+Enter => allow default newline insertion
      // - Plain Enter => approve current string and move to next
      if (isInEditor) {
        if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          approveCurrentAndAdvance();
        }
        return;
      }

      if (isInSearch || (isInAnyTextControl && activeEl !== els.enSource)) {
        return;
      }

      // E => arm focus on Croatian editor for keyup so keydown/keypress/char finishes before editor receives focus
      if ((e.key === 'e' || e.key === 'E') && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        e.stopPropagation();
        pendingEditorFocusOnKeyup = true;
        return;
      }

      // Plain Enter => approve current string and move onto the next one in line
      if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        // Only let explicit non-row toolbar buttons handle Enter themselves if focused via Tab
        const isToolbarButton =
          activeEl &&
          activeEl.tagName === 'BUTTON' &&
          !activeEl.classList.contains('string-row') &&
          activeEl !== els.approveNextBtn;
        if (!isToolbarButton) {
          e.preventDefault();
          approveCurrentAndAdvance();
        }
        return;
      }

      // Arrow navigation without changing approval state
      if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
        if (activeEl !== els.enSource) {
          e.preventDefault();
          if (state.session.currentIndex < state.session.items.length - 1) {
            selectIndex(state.session.currentIndex + 1);
          }
        }
      } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
        if (activeEl !== els.enSource) {
          e.preventDefault();
          if (state.session.currentIndex > 0) {
            selectIndex(state.session.currentIndex - 1);
          }
        }
      }
    });

    document.addEventListener('keyup', (e) => {
      if (pendingEditorFocusOnKeyup && (e.key === 'e' || e.key === 'E')) {
        pendingEditorFocusOnKeyup = false;
        focusCroatianEditor();
      }
    });

    // Flush unsaved edits on page leave
    window.addEventListener('beforeunload', () => {
      if (state.isDirty && state.session) {
        saveActiveSession(state.session).catch(() => {});
      }
    });
  }

  async function init() {
    wireEventListeners();
    try {
      const saved = await loadActiveSession();
      if (saved && Array.isArray(saved.items) && saved.items.length > 0 && saved.masterAst) {
        openWorkspaceWithSession(saved, `Restored session (${saved.items.length.toLocaleString()} strings) from IndexedDB.`);
      } else {
        setAutosaveIndicator('IndexedDB ready', false);
      }
    } catch (err) {
      console.warn('IndexedDB restore unavailable:', err);
      setAutosaveIndicator('Storage unavailable', true);
    }
  }

  init();
})();
