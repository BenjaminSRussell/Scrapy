// Explore what has been discovered: host, then path, as a treemap and a list.
import { html, useState } from '../lib.js';
import { explorePath } from '../api.js';
import { full, compact, plural } from '../format.js';
import { format, parent, push } from '../router.js';
import { Section, List, Row, IconTile, SearchField, Loading, ErrorCard } from '../ui/components.js';
import { Treemap, PALETTE } from '../ui/treemap.js';
import { BarList } from '../ui/bars.js';
import { useApi, useThrottled, useMedia } from '../ui/hooks.js';

function currentPath(route) {
  const top = route.layers[route.layers.length - 1];
  return top && top.kind === 'path' ? top.value.split('/').filter(Boolean) : [];
}

// A stable color per host, so a host keeps its color as you drill in.
function hostTint(host) {
  let h = 0x811c9dc5; // FNV-1a, then a mix step so similar names spread across colors
  for (const ch of host || '') h = Math.imul(h ^ ch.charCodeAt(0), 0x01000193) >>> 0;
  h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d) >>> 0;
  return PALETTE[(h ^ (h >>> 12)) % PALETTE.length];
}

export function ExploreView({ state, route, now }) {
  const path = currentPath(route);
  const discovery = state.stages.find((s) => s.id === 'discovery');
  const refreshKey = useThrottled(discovery?.version ?? null, 15000);
  const { data, error, loading, retry } = useApi(explorePath(path), refreshKey);
  const [query, setQuery] = useState('');
  const wide = useMedia('(min-width: 700px)');

  if (error && !data) {
    const empty = error.status === 404 && !path.length;
    return empty
      ? html`<div class="card"><div class="empty"><strong>Nothing discovered yet</strong><span>The map fills in as Discovery writes URLs.</span></div></div>`
      : html`<${ErrorCard} error=${error} onRetry=${retry} backHref=${path.length ? format(parent(route)) : undefined} />`;
  }
  if (!data) return html`<${Loading} label="Loading the crawl map" />`;
  if (!data.total && !path.length) {
    return html`<div class="card"><div class="empty"><strong>Nothing discovered yet</strong><span>The map fills in as Discovery writes URLs.</span></div></div>`;
  }

  const q = query.trim().toLowerCase();
  const children = q ? data.children.filter((c) => c.segment.toLowerCase().includes(q)) : data.children;
  const hrefFor = (c) => format(push(route, { kind: 'path', value: [...path, c.segment].join('/') }));
  const tintFor = (c, n) => (path.length ? hostTint(path[0]) : hostTint(c.segment));
  const crumbs = path.length ? path.join(' › ') : 'Every host the crawl has reached';

  return html`
    <p class="subtitle">${crumbs} · ${plural(data.total, 'URL')}</p>
    ${data.children.length > 0 && html`
      <${SearchField} value=${query} onInput=${setQuery} placeholder=${path.length ? 'Filter sections' : 'Filter hosts'} />
      ${children.length > 0 && html`<div class="card treemap-card"><${Treemap} items=${children} hrefFor=${hrefFor} tintFor=${tintFor} height=${wide ? 320 : 230} /></div>`}
      <${Section} title=${path.length ? 'Sections' : 'Hosts'}
        footer=${data.other ? `Plus ${plural(data.other, 'URL')} in smaller sections not listed.` : undefined}>
        ${children.length
          ? html`<${List}>${children.map((c) => html`<${Row} key=${c.segment}
              icon=${html`<${IconTile} name=${path.length ? 'folder' : 'globe'} tint=${tintFor(c)} />`}
              title=${c.segment} value=${compact(c.count)} href=${hrefFor(c)}
              label=${`${c.segment}, ${full(c.count)} URLs`} />`)}</${List}>`
          : html`<div class="card"><div class="empty"><strong>No matches</strong><span>Nothing here contains “${query}”.</span></div></div>`}
      </${Section}>`}

    ${data.urls.length > 0 && html`<${Section} title=${path.length ? 'Pages here' : 'Pages'}>
      <${List}>${data.urls.map((u) => html`<${Row} key=${u.url} title=${u.url.replace(/^https?:\/\//, '')} sub=${u.depth != null ? `Depth ${u.depth}` : undefined}
        href=${format(push(route, { kind: 'url', value: u.url }))} />`)}</${List}>
    </${Section}>`}

    ${data.depths && data.depths.length > 0 && html`<${Section} title="Crawl depth"
        footer="How many links away from a seed each page was found.">
      <div class="card"><${BarList} items=${data.depths.map((d) => ({ label: `Depth ${d.label}`, value: d.value }))} tint="blue" label="URLs by crawl depth" /></div>
    </${Section}>`}`;
}
