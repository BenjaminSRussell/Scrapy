// One live event as a list row: what happened, to which URL, how long ago.
import { html } from '../lib.js';
import { EVENT_KINDS } from '../meta.js';
import { relativeTime, splitUrl } from '../format.js';
import { format, push } from '../router.js';
import { Row, IconTile, StageTile } from './components.js';

const DETAIL = {
  discovered: (f) => (f.depth != null ? `depth ${f.depth}` : null),
  analyzed: (f) => (f.routed === 'large_docs' ? 'large document' : f.word_count != null ? `${f.word_count.toLocaleString('en-US')} words` : null),
  error: (f) => (f.error_type ? f.error_type.replace(/_/g, ' ') : null),
};

export function EventRow({ event, route, now }) {
  const kind = EVENT_KINDS[event.kind] || { label: event.kind, stage: event.stage };
  const { host, path } = splitUrl(event.url);
  const extra = DETAIL[event.kind]?.(event.fields || {});
  const sub = [kind.label, extra].filter(Boolean).join(' · ');
  const icon = event.kind === 'error'
    ? html`<${IconTile} name="warning" tint="red" />`
    : html`<${StageTile} stageId=${kind.stage} />`;
  const when = relativeTime(event.t, now);
  const title = host ? `${host}${path === '/' ? '' : path}` : event.url;
  const linkable = host && event.kind !== 'error';
  return html`<${Row} icon=${icon} title=${title} sub=${sub} value=${when}
    href=${linkable ? format(push(route, { kind: 'url', value: event.url })) : undefined}
    label=${`${sub}: ${event.url}, ${when}`} />`;
}
