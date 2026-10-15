// Everything the pipeline wrote, as it happens. Filter, search, pause.
import { html, useEffect, useRef, useState } from '../lib.js';
import { FILTERS } from '../meta.js';
import { filterEvents } from '../state.js';
import { plural } from '../format.js';
import { Section, List, Segmented, SearchField } from '../ui/components.js';
import { EventRow } from '../ui/events.js';
import { icons } from '../ui/icons.js';
import { ConnectionBanners } from './pipeline.js';

const ANNOUNCE_EVERY = 10000;

export function ActivityView({ state, route, now }) {
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [frozen, setFrozen] = useState(null); // the list as it was when paused
  const [announcement, setAnnouncement] = useState('');
  const pending = useRef(0);
  const lastTop = useRef(state.events[0]?.id);

  // Count arrivals; tell screen readers at most once every 10 s.
  useEffect(() => {
    const top = state.events[0]?.id;
    if (top && top !== lastTop.current) {
      const idx = state.events.findIndex((e) => e.id === lastTop.current);
      pending.current += idx < 0 ? 1 : idx;
      lastTop.current = top;
    }
  }, [state.events]);
  useEffect(() => {
    const timer = setInterval(() => {
      if (pending.current && !frozen) setAnnouncement(`${plural(pending.current, 'new event')}`);
      pending.current = 0;
    }, ANNOUNCE_EVERY);
    return () => clearInterval(timer);
  }, [frozen]);

  const source = frozen || state.events;
  const shown = filterEvents(source, filter, query);
  const seen = frozen ? new Set(frozen.map((e) => e.id)) : null;
  const newCount = frozen ? state.events.filter((e) => !seen.has(e.id)).length : 0;
  const resume = () => {
    setFrozen(null);
    window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  };

  return html`
    <p class="subtitle">Every row the pipeline writes, newest first.</p>
    <${ConnectionBanners} state=${state} now=${now} />
    <div class="toolbar">
      <${SearchField} value=${query} onInput=${setQuery} placeholder="Search URLs" />
      <button type="button" class="icon-btn" onClick=${() => (frozen ? resume() : setFrozen(state.events))}
        aria-label=${frozen ? 'Resume live updates' : 'Pause live updates'} aria-pressed=${String(Boolean(frozen))}>
        ${frozen ? icons.play() : icons.pause()}</button>
    </div>
    <${Segmented} label="Show" options=${FILTERS} value=${filter} onChange=${setFilter} />
    ${frozen && html`<button type="button" class="new-pill" onClick=${resume}>
      ${newCount ? `${plural(newCount, 'new event')} · Resume` : 'Paused · Resume'}</button>`}
    <div class="sr-only" aria-live="polite">${announcement}</div>

    <${Section} label="Events" footer=${`Showing ${plural(shown.length, 'event')} of the newest ${source.length}.`}>
      ${shown.length
        ? html`<${List} label="Events">${shown.map((e) => html`<${EventRow} key=${e.id} event=${e} route=${route} now=${now} />`)}</${List}>`
        : html`<div class="card"><div class="empty"><strong>${source.length ? 'No matches' : 'Nothing yet'}</strong>
            <span>${source.length ? 'Try another filter or search.' : 'Rows appear here as the pipeline writes them.'}</span></div></div>`}
    </${Section}>`;
}
