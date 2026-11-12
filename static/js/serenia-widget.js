/**
 * Global Serenia AI floating assistant — free-floating, persistent across CRM pages.
 * Version bump forces UI upgrade without full hard-reload of the whole app.
 */
(function () {
  'use strict';

  var VERSION = '20261112b';
  var STORAGE_KEY = 'serenia_widget_state_v2';
  var POS_KEY = 'serenia_widget_pos_v1';
  var CHAT_URL = '/admin/ai/chat';
  var CONFIRM_URL = '/admin/ai/confirm';
  var UPLOAD_URL = '/admin/ai/upload';
  var PROMPTS_URL = '/admin/ai/prompts';
  var LOGO = '/static/images/serenia-logo.png?v=' + VERSION;

  // Tear down old instance if version changed (fixes "must refresh to see new UI")
  if (window.__sereniaWidgetVersion && window.__sereniaWidgetVersion !== VERSION) {
    try {
      var oldFab = document.getElementById('serenia-fab');
      var oldPanel = document.getElementById('serenia-panel');
      if (oldFab) oldFab.remove();
      if (oldPanel) oldPanel.remove();
    } catch (e) {}
    window.__sereniaWidget = false;
  }
  if (window.__sereniaWidget) return;
  window.__sereniaWidget = true;
  window.__sereniaWidgetVersion = VERSION;

  var state = {
    open: false,
    busy: false,
    chatId: null,
    history: [],
    pendingFiles: []
  };
  var recognition = null;
  var listening = false;
  var drag = { active: false, target: null, ox: 0, oy: 0, moved: false };

  function loadState() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      var s = JSON.parse(raw);
      if (s && typeof s === 'object') {
        state.chatId = s.chatId || null;
        state.history = Array.isArray(s.history) ? s.history.slice(-40) : [];
        state.open = !!s.open;
      }
    } catch (e) {}
  }

  function saveState() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        chatId: state.chatId,
        history: state.history.slice(-40),
        open: state.open
      }));
    } catch (e) {}
  }

  function loadPos() {
    try {
      return JSON.parse(localStorage.getItem(POS_KEY) || '{}') || {};
    } catch (e) { return {}; }
  }

  function savePos(patch) {
    try {
      var cur = loadPos();
      for (var k in patch) cur[k] = patch[k];
      localStorage.setItem(POS_KEY, JSON.stringify(cur));
    } catch (e) {}
  }

  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function simpleMarkdown(text) {
    var t = escapeHtml(text || '');
    t = t.replace(/```([\s\S]*?)```/g, function (_, code) {
      return '<pre style="overflow:auto;background:#f1f5f9;padding:8px;border-radius:8px;font-size:11.5px"><code>' + code.trim() + '</code></pre>';
    });
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/\n\n/g, '</p><p>');
    t = t.replace(/\n/g, '<br>');
    return '<p>' + t + '</p>';
  }

  function isAiPage() {
    return (location.pathname || '').replace(/\/$/, '') === '/admin/ai';
  }

  function clamp(n, min, max) {
    return Math.max(min, Math.min(max, n));
  }

  function applySavedPositions() {
    var pos = loadPos();
    var fab = document.getElementById('serenia-fab');
    var panel = document.getElementById('serenia-panel');
    if (fab && pos.fab) {
      fab.style.left = pos.fab.left;
      fab.style.top = pos.fab.top;
      fab.style.right = 'auto';
      fab.style.bottom = 'auto';
    }
    if (panel && pos.panel) {
      panel.style.left = pos.panel.left;
      panel.style.top = pos.panel.top;
      panel.style.right = 'auto';
      panel.style.bottom = 'auto';
    }
  }

  function updateVisibility() {
    var fab = document.getElementById('serenia-fab');
    var panel = document.getElementById('serenia-panel');
    if (!fab || !panel) return;
    if (isAiPage()) {
      document.body.classList.add('serenia-fullpage');
      fab.classList.add('hidden');
      panel.classList.add('hidden');
    } else {
      document.body.classList.remove('serenia-fullpage');
      fab.classList.remove('hidden');
      if (state.open) panel.classList.remove('hidden');
      else panel.classList.add('hidden');
    }
  }

  function makeDraggable(el, handle, kind) {
    function onDown(e) {
      if (e.button != null && e.button !== 0) return;
      // Don't start drag from interactive controls inside the panel
      var t = e.target;
      if (t.closest && t.closest('button, a, textarea, input, .sw-composer, .sw-msgs, .sw-prompt-menu')) {
        if (kind === 'panel' && !(t.closest && t.closest('.sw-drag-handle'))) return;
      }
      var pt = e.touches ? e.touches[0] : e;
      var rect = el.getBoundingClientRect();
      drag.active = true;
      drag.target = el;
      drag.kind = kind;
      drag.ox = pt.clientX - rect.left;
      drag.oy = pt.clientY - rect.top;
      drag.moved = false;
      el.classList.add('sw-dragging');
      e.preventDefault();
    }
    handle.addEventListener('mousedown', onDown);
    handle.addEventListener('touchstart', onDown, { passive: false });
  }

  function onMove(e) {
    if (!drag.active || !drag.target) return;
    var pt = e.touches ? e.touches[0] : e;
    var el = drag.target;
    var w = el.offsetWidth;
    var h = el.offsetHeight;
    var x = clamp(pt.clientX - drag.ox, 4, window.innerWidth - w - 4);
    var y = clamp(pt.clientY - drag.oy, 4, window.innerHeight - h - 4);
    el.style.left = x + 'px';
    el.style.top = y + 'px';
    el.style.right = 'auto';
    el.style.bottom = 'auto';
    drag.moved = true;
    e.preventDefault();
  }

  function onUp() {
    if (!drag.active || !drag.target) return;
    var el = drag.target;
    var kind = drag.kind;
    var moved = drag.moved;
    el.classList.remove('sw-dragging');
    savePos({
      [kind]: { left: el.style.left, top: el.style.top }
    });
    drag.active = false;
    drag.target = null;
    // If FAB was clicked without moving, toggle panel
    if (kind === 'fab' && !moved) {
      state.open = !state.open;
      saveState();
      // Position panel near fab if not previously placed
      var panel = document.getElementById('serenia-panel');
      var pos = loadPos();
      if (panel && state.open && !pos.panel) {
        var fr = el.getBoundingClientRect();
        var pw = panel.offsetWidth || 400;
        var ph = panel.offsetHeight || 520;
        var left = clamp(fr.right - pw, 8, window.innerWidth - pw - 8);
        var top = clamp(fr.top - ph - 12, 8, window.innerHeight - ph - 8);
        if (top < 8) top = clamp(fr.bottom + 12, 8, window.innerHeight - ph - 8);
        panel.style.left = left + 'px';
        panel.style.top = top + 'px';
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
      }
      updateVisibility();
      if (state.open) {
        var input = document.getElementById('serenia-input');
        if (input) setTimeout(function () { input.focus(); }, 40);
      }
    }
  }

  document.addEventListener('mousemove', onMove, { passive: false });
  document.addEventListener('touchmove', onMove, { passive: false });
  document.addEventListener('mouseup', onUp);
  document.addEventListener('touchend', onUp);

  function ensureDom() {
    if (document.getElementById('serenia-fab')) {
      applySavedPositions();
      return;
    }

    var fab = document.createElement('button');
    fab.id = 'serenia-fab';
    fab.type = 'button';
    fab.setAttribute('aria-label', 'Open Serenia AI');
    fab.title = 'Serenia AI — drag to move';
    fab.innerHTML =
      '<span class="pulse"></span>' +
      '<img src="' + LOGO + '" alt="Serenia AI" class="fab-logo" draggable="false">';

    var panel = document.createElement('div');
    panel.id = 'serenia-panel';
    panel.className = 'hidden';
    panel.innerHTML =
      '<div class="sw-head sw-drag-handle" title="Drag to move">' +
        '<img class="brand" src="' + LOGO + '" alt="" draggable="false">' +
        '<div class="titles"><strong>Serenia AI</strong><span>Unlock Possibilities With Serenia AI.</span></div>' +
        '<button type="button" id="serenia-new" title="New chat">＋</button>' +
        '<button type="button" id="serenia-close" title="Close">✕</button>' +
      '</div>' +
      '<div class="sw-msgs" id="serenia-msgs"></div>' +
      '<div class="sw-composer">' +
        '<div class="sw-attach-preview" id="serenia-attach-preview"></div>' +
        '<textarea id="serenia-input" rows="1" placeholder="Ask Serenia AI"></textarea>' +
        '<div class="sw-composer-bar">' +
          '<button type="button" class="sw-icon-btn" id="serenia-attach" title="Attach image">' +
            '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>' +
          '</button>' +
          '<button type="button" class="sw-icon-btn" id="serenia-voice" title="Voice input">' +
            '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>' +
          '</button>' +
          '<button type="button" class="sw-icon-btn" id="serenia-prompts" title="Prompt library">' +
            '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/><line x1="8" y1="7" x2="16" y2="7"/><line x1="8" y1="11" x2="14" y2="11"/></svg>' +
          '</button>' +
          '<a class="sw-open-full" href="/admin/ai" data-no-ajax>Full page</a>' +
          '<button type="button" class="sw-send" id="serenia-send">Send</button>' +
        '</div>' +
        '<input type="file" id="serenia-file" accept="image/*,video/*,.pdf,.txt,.md,.csv" multiple hidden>' +
      '</div>';

    // Append to body so AJAX content swaps never remove the widget
    document.body.appendChild(fab);
    document.body.appendChild(panel);

    makeDraggable(fab, fab, 'fab');
    makeDraggable(panel, panel.querySelector('.sw-drag-handle'), 'panel');
    applySavedPositions();

    document.getElementById('serenia-close').addEventListener('click', function (e) {
      e.stopPropagation();
      state.open = false;
      saveState();
      updateVisibility();
    });
    document.getElementById('serenia-new').addEventListener('click', function (e) {
      e.stopPropagation();
      state.chatId = null;
      state.history = [];
      state.pendingFiles = [];
      saveState();
      renderAttach();
      renderMessages();
    });
    document.getElementById('serenia-send').addEventListener('click', function () { sendMessage(); });
    document.getElementById('serenia-input').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    document.getElementById('serenia-attach').addEventListener('click', function () {
      document.getElementById('serenia-file').click();
    });
    document.getElementById('serenia-file').addEventListener('change', function () {
      var files = this.files;
      if (files && files.length) uploadFiles(files);
      this.value = '';
    });
    document.getElementById('serenia-voice').addEventListener('click', toggleVoice);
    document.getElementById('serenia-prompts').addEventListener('click', openPromptLibrary);
  }

  function renderAttach() {
    var box = document.getElementById('serenia-attach-preview');
    if (!box) return;
    box.innerHTML = '';
    state.pendingFiles.forEach(function (f, i) {
      var chip = document.createElement('div');
      chip.className = 'sw-chip-file';
      if (f.kind === 'image' && f.url) {
        chip.innerHTML = '<img src="' + escapeHtml(f.url) + '" alt="">' +
          '<span>' + escapeHtml(f.original || f.filename || 'file') + '</span>' +
          '<button type="button" data-i="' + i + '">×</button>';
      } else {
        chip.innerHTML = '<span>' + escapeHtml(f.original || f.filename || 'file') + '</span>' +
          '<button type="button" data-i="' + i + '">×</button>';
      }
      chip.querySelector('button').addEventListener('click', function () {
        state.pendingFiles.splice(parseInt(this.getAttribute('data-i'), 10), 1);
        renderAttach();
      });
      box.appendChild(chip);
    });
  }

  function uploadFiles(files) {
    Array.prototype.forEach.call(files, function (file) {
      var fd = new FormData();
      fd.append('file', file);
      fetch(UPLOAD_URL, { method: 'POST', body: fd, credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) {
            addBubble('assistant', data.error || 'Upload failed');
            return;
          }
          state.pendingFiles.push({
            filename: data.filename,
            url: data.url,
            original: data.original || file.name,
            kind: data.kind || 'image',
            extracted_text: data.extracted_text || ''
          });
          renderAttach();
        })
        .catch(function () { addBubble('assistant', 'Upload failed. Please try again.'); });
    });
  }

  function toggleVoice() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    var btn = document.getElementById('serenia-voice');
    if (!SR) {
      addBubble('assistant', 'Voice input is not supported in this browser.');
      return;
    }
    if (listening && recognition) {
      recognition.stop();
      listening = false;
      if (btn) btn.classList.remove('listening');
      return;
    }
    recognition = new SR();
    recognition.lang = 'en-IN';
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = function (ev) {
      var text = (ev.results[0][0].transcript || '').trim();
      var input = document.getElementById('serenia-input');
      if (input && text) {
        input.value = (input.value ? input.value + ' ' : '') + text;
        input.focus();
      }
    };
    recognition.onerror = function () { listening = false; if (btn) btn.classList.remove('listening'); };
    recognition.onend = function () { listening = false; if (btn) btn.classList.remove('listening'); };
    listening = true;
    if (btn) btn.classList.add('listening');
    recognition.start();
  }

  function openPromptLibrary() {
    fetch(PROMPTS_URL, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var prompts = (data && data.prompts) ? data.prompts : [];
        if (!prompts.length) {
          prompts = [
            { title: 'CRM summary', text: 'Analyse my CRM and give a clear summary' },
            { title: 'Available properties', text: 'Show available properties in a compact table' },
            { title: 'Open deals', text: 'List open deals with stage and value' },
            { title: 'Upcoming appointments', text: 'Show upcoming appointments' }
          ];
        }
        var existing = document.getElementById('serenia-prompt-menu');
        if (existing) existing.remove();
        var menu = document.createElement('div');
        menu.id = 'serenia-prompt-menu';
        menu.className = 'sw-prompt-menu';
        prompts.forEach(function (pr) {
          var b = document.createElement('button');
          b.type = 'button';
          b.innerHTML = '<strong>' + escapeHtml(pr.title || 'Prompt') + '</strong><span>' + escapeHtml(pr.text || '') + '</span>';
          b.onclick = function () { menu.remove(); sendMessage(pr.text || pr.title); };
          menu.appendChild(b);
        });
        document.getElementById('serenia-panel').appendChild(menu);
        setTimeout(function () {
          document.addEventListener('click', function closeMenu(e) {
            if (!menu.contains(e.target) && !(e.target.closest && e.target.closest('#serenia-prompts'))) {
              menu.remove();
              document.removeEventListener('click', closeMenu);
            }
          });
        }, 0);
      })
      .catch(function () { addBubble('assistant', 'Could not load prompts.'); });
  }

  function userGreeting() {
    var name = document.body.getAttribute('data-user-name') || '';
    if (name) {
      return 'What would you like to accomplish, <span class="sw-greet-name">' + escapeHtml(name.split(' ')[0]) + '</span>?';
    }
    return 'What would you like to accomplish, <span class="sw-greet-name">System</span>?';
  }

  function renderMessages() {
    var box = document.getElementById('serenia-msgs');
    if (!box) return;
    box.innerHTML = '';
    if (!state.history.length) {
      var empty = document.createElement('div');
      empty.className = 'sw-empty';
      empty.innerHTML =
        '<strong class="sw-greet">' + userGreeting() + '</strong>' +
        '<div class="sw-chips">' +
          '<button type="button" data-q="Show available properties">Available properties</button>' +
          '<button type="button" data-q="List open deals with stage and value">Open deals</button>' +
          '<button type="button" data-q="Summarise my pipeline this week">Pipeline summary</button>' +
          '<button type="button" data-q="Show upcoming appointments">Appointments</button>' +
        '</div>';
      box.appendChild(empty);
      empty.querySelectorAll('button[data-q]').forEach(function (btn) {
        btn.addEventListener('click', function () { sendMessage(btn.getAttribute('data-q')); });
      });
      return;
    }
    state.history.forEach(function (m) {
      if (m.role === 'user') addBubble('user', m.content, false);
      else if (m.role === 'assistant') addBubble('assistant', m.content, false);
      else if (m.role === 'confirm' && m.pending) addConfirm(m.pending, false);
    });
    box.scrollTop = box.scrollHeight;
  }

  function addBubble(role, content, persist) {
    var box = document.getElementById('serenia-msgs');
    if (!box) return null;
    var empty = box.querySelector('.sw-empty');
    if (empty) empty.remove();
    var row = document.createElement('div');
    row.className = 'sw-row ' + role;
    var avatar = document.createElement('div');
    avatar.className = 'sw-avatar';
    if (role === 'assistant') avatar.innerHTML = '<img src="' + LOGO + '" alt="">';
    else avatar.textContent = 'You';
    var bubble = document.createElement('div');
    bubble.className = 'sw-bubble' + (role === 'assistant' ? ' md' : '');
    if (role === 'assistant') bubble.innerHTML = simpleMarkdown(content);
    else bubble.textContent = content;
    row.appendChild(avatar);
    row.appendChild(bubble);
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
    if (persist !== false) {
      state.history.push({ role: role, content: content });
      saveState();
    }
    return row;
  }

  function addTyping() {
    var box = document.getElementById('serenia-msgs');
    if (!box) return;
    var row = document.createElement('div');
    row.className = 'sw-row assistant';
    row.id = 'serenia-typing';
    row.innerHTML =
      '<div class="sw-avatar"><img src="' + LOGO + '" alt=""></div>' +
      '<div class="sw-bubble"><span class="sw-typing"><span></span><span></span><span></span></span></div>';
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }

  function removeTyping() {
    var el = document.getElementById('serenia-typing');
    if (el) el.remove();
  }

  function addConfirm(pending, persist) {
    var box = document.getElementById('serenia-msgs');
    if (!box || !pending) return;
    var empty = box.querySelector('.sw-empty');
    if (empty) empty.remove();
    var wrap = document.createElement('div');
    wrap.className = 'sw-confirm';
    wrap.innerHTML =
      '<div class="tag">Confirmation required</div>' +
      '<div>' + escapeHtml(pending.summary || pending.tool_name || 'Destructive action') + '</div>' +
      '<div class="actions">' +
        '<button type="button" class="yes">Confirm</button>' +
        '<button type="button" class="no">Cancel</button>' +
      '</div>';
    box.appendChild(wrap);
    box.scrollTop = box.scrollHeight;
    wrap.querySelector('.yes').addEventListener('click', function () { confirmPending(pending.pending_id, true, wrap); });
    wrap.querySelector('.no').addEventListener('click', function () { confirmPending(pending.pending_id, false, wrap); });
    if (persist !== false) {
      state.history.push({ role: 'confirm', pending: pending });
      saveState();
    }
  }

  function confirmPending(pid, yes, row) {
    fetch(CONFIRM_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ pending_id: pid, confirm: !!yes })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (row) row.remove();
        addBubble('assistant', data.message || data.reply || (yes ? 'Confirmed.' : 'Cancelled.'));
      })
      .catch(function () { addBubble('assistant', 'Could not complete confirmation.'); });
  }

  function lastThreeMessages() {
    // Last 3 user/assistant turns for server context
    return state.history
      .filter(function (m) { return m.role === 'user' || m.role === 'assistant'; })
      .slice(-3)
      .map(function (m) { return { role: m.role, content: m.content }; });
  }

  function sendMessage(preset) {
    var input = document.getElementById('serenia-input');
    var text = (preset != null ? preset : (input && input.value) || '').trim();
    var attachments = state.pendingFiles.slice();
    if (!text && !attachments.length) return;
    if (!text && attachments.length) {
      text = 'Use the uploaded file(s) and create or update matching CRM records.';
    }
    if (state.busy) return;
    if (input) input.value = '';
    state.pendingFiles = [];
    renderAttach();
    state.busy = true;
    var sendBtn = document.getElementById('serenia-send');
    if (sendBtn) sendBtn.disabled = true;

    // Capture context BEFORE adding the new user bubble
    var hist = lastThreeMessages();

    addBubble('user', text + (attachments.length ? ' 📎 ' + attachments.length + ' file(s)' : ''));
    addTyping();

    fetch(CHAT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        message: text,
        chat_id: state.chatId,
        history: hist,
        attachments: attachments
      })
    })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, data: d }; }); })
      .then(function (res) {
        removeTyping();
        state.busy = false;
        if (sendBtn) sendBtn.disabled = false;
        var data = res.data || {};
        if (data.chat_id) { state.chatId = data.chat_id; saveState(); }
        if (!data.ok) {
          var err = data.error || 'Something went wrong.';
          if (data.need_key) err += ' Open full Serenia AI page to add your Groq API key.';
          addBubble('assistant', err);
          return;
        }
        addBubble('assistant', data.reply || data.message || data.content || 'Done.');
        if (data.pending) addConfirm(data.pending);
      })
      .catch(function () {
        removeTyping();
        state.busy = false;
        if (sendBtn) sendBtn.disabled = false;
        addBubble('assistant', 'Network error. Please try again.');
      });
  }

  function boot() {
    loadState();
    ensureDom();
    renderMessages();
    updateVisibility();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  // Keep alive across AJAX navigations
  function keepAlive() {
    setTimeout(function () {
      ensureDom();
      updateVisibility();
    }, 20);
  }
  window.addEventListener('popstate', keepAlive);
  var origPush = history.pushState;
  if (origPush) {
    history.pushState = function () {
      var r = origPush.apply(this, arguments);
      keepAlive();
      return r;
    };
  }
  var origReplace = history.replaceState;
  if (origReplace) {
    history.replaceState = function () {
      var r = origReplace.apply(this, arguments);
      keepAlive();
      return r;
    };
  }

  try {
    var obs = new MutationObserver(function () {
      if (!document.getElementById('serenia-fab')) {
        ensureDom();
        renderMessages();
      }
      updateVisibility();
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  } catch (e) {}
})();
