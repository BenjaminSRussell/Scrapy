/*! Control Center a11y helpers (#153) */
(function () {
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function injectStyles() {
    if (qs('#cc-a11y-styles')) return;
    const style = document.createElement('style');
    style.id = 'cc-a11y-styles';
    style.textContent = `
.tab-button:focus { outline: none; }
.tab-button:focus-visible { outline: 3px solid var(--primary, #2563eb); outline-offset: -3px; z-index: 1; }
@media (prefers-reduced-motion: reduce) {
  .tab-button, .card, .stage-card, .status-dot { transition: none !important; animation: none !important; }
  .card:hover { transform: none; }
}`;
    document.head.appendChild(style);
  }

  function enhanceStatus() {
    const top = qs('#topbar-status');
    if (top && !qs('.status-text', top)) {
      const raw = (top.textContent || '').trim();
      top.setAttribute('role', 'status');
      top.setAttribute('aria-live', 'polite');
      top.textContent = '';
      const emoji = document.createElement('span');
      emoji.className = 'status-emoji';
      emoji.setAttribute('aria-hidden', 'true');
      const text = document.createElement('span');
      text.className = 'status-text';
      const online = /online|\uD83D\uDFE2/i.test(raw);
      const offline = /offline|\uD83D\uDD34/i.test(raw);
      emoji.textContent = online ? '\uD83D\uDFE2' : (offline ? '\uD83D\uDD34' : '\u25CF');
      text.textContent = online ? 'Online' : (offline ? 'Offline' : (raw || 'Unknown'));
      top.append(emoji, document.createTextNode(' '), text);
      top.setAttribute('aria-label', 'Pipeline status: ' + text.textContent);
    }

    const sys = qs('#system-status');
    if (sys) {
      const label = (qs('.status-text', sys) || sys).textContent.trim() || 'Online';
      sys.setAttribute('role', 'status');
      sys.setAttribute('aria-label', 'System status: ' + label);
      const dot = qs('.status-dot', sys);
      if (dot) dot.setAttribute('aria-hidden', 'true');
    }

    qsa('.health-item').forEach(item => {
      const icon = qs('.health-icon', item);
      if (icon) icon.setAttribute('aria-hidden', 'true');
      const name = (qs('.health-label', item)?.textContent || '').trim() || 'Health';
      const state = (qs('.health-value', item)?.textContent || '').trim() || 'Unknown';
      item.setAttribute('aria-label', name + ': ' + state);
    });
  }

  function activateTab(tabButton, { focus } = {}) {
    const tabs = qsa('.tab-button');
    const name = tabButton.getAttribute('data-tab');
    tabs.forEach(btn => {
      const selected = btn === tabButton;
      btn.classList.toggle('active', selected);
      btn.setAttribute('aria-selected', selected ? 'true' : 'false');
      btn.tabIndex = selected ? 0 : -1;
      btn.setAttribute('role', 'tab');
    });
    qsa('.tab-content').forEach(panel => {
      const active = panel.id === 'tab-' + name;
      panel.classList.toggle('active', active);
      panel.setAttribute('role', 'tabpanel');
      if (active) panel.removeAttribute('hidden');
      else panel.setAttribute('hidden', '');
    });
    if (focus) tabButton.focus();
  }

  function enhanceTabs() {
    const list = qs('.tab-buttons');
    if (!list) return;
    list.setAttribute('role', 'tablist');
    list.setAttribute('aria-label', 'Control Center sections');
    const tabs = qsa('.tab-button', list);
    tabs.forEach(btn => {
      const name = btn.getAttribute('data-tab');
      const id = 'tab-btn-' + name;
      btn.id = id;
      btn.setAttribute('role', 'tab');
      btn.setAttribute('aria-controls', 'tab-' + name);
      const selected = btn.classList.contains('active');
      btn.setAttribute('aria-selected', selected ? 'true' : 'false');
      btn.tabIndex = selected ? 0 : -1;
      if (btn.childNodes.length === 1 && btn.firstChild.nodeType === 3) {
        const raw = btn.textContent;
        const m = raw.match(/^(\S+)\s+(.*)$/);
        if (m) {
          btn.textContent = '';
          const em = document.createElement('span');
          em.setAttribute('aria-hidden', 'true');
          em.textContent = m[1] + ' ';
          btn.append(em, document.createTextNode(m[2]));
        }
      }
      const panel = qs('#tab-' + name);
      if (panel) {
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', id);
        panel.tabIndex = 0;
        if (!panel.classList.contains('active')) panel.setAttribute('hidden', '');
        else panel.removeAttribute('hidden');
      }
      btn.addEventListener('click', () => activateTab(btn));
      btn.addEventListener('keydown', (event) => {
        const i = tabs.indexOf(btn);
        let n = null;
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') n = (i + 1) % tabs.length;
        else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') n = (i - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') n = 0;
        else if (event.key === 'End') n = tabs.length - 1;
        else return;
        event.preventDefault();
        activateTab(tabs[n], { focus: true });
      });
    });

    const top = qs('#topbar-status');
    if (top) {
      const obs = new MutationObserver(() => {
        const t = top.textContent;
        if (top.querySelector('.status-text')) {
          const online = /ONLINE|Online|\uD83D\uDFE2/.test(t);
          const offline = /OFFLINE|Offline|\uD83D\uDD34/.test(t);
          if (online || offline) {
            const emoji = top.querySelector('.status-emoji');
            const text = top.querySelector('.status-text');
            if (emoji) emoji.textContent = online ? '\uD83D\uDFE2' : '\uD83D\uDD34';
            if (text) text.textContent = online ? 'Online' : 'Offline';
            top.setAttribute('aria-label', 'Pipeline status: ' + (online ? 'Online' : 'Offline'));
          }
          return;
        }
        enhanceStatus();
      });
      obs.observe(top, { childList: true, characterData: true, subtree: true });
    }
  }

  function boot() {
    injectStyles();
    enhanceTabs();
    enhanceStatus();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
