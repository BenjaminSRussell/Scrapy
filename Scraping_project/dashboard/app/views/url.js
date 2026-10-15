// One URL's journey: where it was found, what each stage made of it. Fills in live.
import { html, useState } from '../lib.js';
import { urlPath } from '../api.js';
import { isHttpUrl, splitUrl } from '../format.js';
import { format, parent, push } from '../router.js';
import { Section, List, Row, IconTile, Loading, ErrorCard } from '../ui/components.js';
import { Timeline } from '../ui/timeline.js';
import { icons } from '../ui/icons.js';
import { useApi, useThrottled, copyText } from '../ui/hooks.js';

export function UrlView({ state, route, now }) {
  const url = route.layers[route.layers.length - 1].value;
  const valid = isHttpUrl(url);
  const versions = state.stages.map((s) => s.version ?? '').join(',');
  const refreshKey = useThrottled(versions, 5000);
  const { data, error, loading, retry } = useApi(valid ? urlPath(url) : null, refreshKey);
  const [shared, setShared] = useState('');
  const { path } = splitUrl(url);

  if (!valid) {
    return html`<${ErrorCard} error=${new Error('This isn’t an http(s) web address, so there is nothing to look up.')} backHref=${format(parent(route))} />`;
  }

  async function share() {
    const link = location.href;
    if (navigator.share) {
      try {
        await navigator.share({ title: `${splitUrl(url).host} – Control Center`, url: link });
        return;
      } catch (e) {
        if (e.name === 'AbortError') return;
      }
    }
    setShared((await copyText(link)) ? 'Link copied' : 'Couldn’t copy the link');
    setTimeout(() => setShared(''), 2400);
  }

  return html`
    <p class="subtitle url-path" title=${path}>${path}</p>
    <div class="btn-row actions">
      <a class="btn filled" href=${url} target="_blank" rel="noopener noreferrer">${icons.external()}Open Page</a>
      <button type="button" class="btn" onClick=${share}>${icons.share()}Share</button>
      <span class="toast" role="status" aria-live="polite">${shared}</span>
    </div>

    ${error && !data && html`<${ErrorCard} error=${error} onRetry=${retry} backHref=${format(parent(route))} />`}
    ${loading && !data && !error && html`<${Loading} label="Loading this URL’s journey" />`}

    ${data && data.parent_url && html`<${Section} title="Found on">
      <${List}><${Row} icon=${html`<${IconTile} name="link" tint="blue" />`} title=${splitUrl(data.parent_url).host || data.parent_url}
        sub=${splitUrl(data.parent_url).path} href=${isHttpUrl(data.parent_url) ? format(push(route, { kind: 'url', value: data.parent_url })) : undefined} /></${List}>
    </${Section}>`}

    ${data && html`<${Section} title="Journey" big=${true}>
      <div class="card"><${Timeline} steps=${data.steps} now=${now} /></div>
    </${Section}>`}`;
}
