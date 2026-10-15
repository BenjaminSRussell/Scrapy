// App frame: sidebar (wide) or tab bar (phone), and the current layer.
import { html } from './lib.js';
import { TABS, TAB_TITLES, format, parent, routeTitle, backLabel } from './router.js';
import { icons } from './ui/icons.js';
import { Layer, ConnPill } from './ui/components.js';
import { PipelineView } from './views/pipeline.js';
import { StageView } from './views/stage.js';
import { UrlView } from './views/url.js';
import { ExploreView } from './views/explore.js';
import { ActivityView } from './views/activity.js';
import { HealthView } from './views/health.js';

const ROOT_VIEWS = { pipeline: PipelineView, explore: ExploreView, activity: ActivityView, health: HealthView };
const LAYER_VIEWS = { stage: StageView, path: ExploreView, url: UrlView };

function viewFor(route) {
  const top = route.layers[route.layers.length - 1];
  return top ? LAYER_VIEWS[top.kind] : ROOT_VIEWS[route.tab];
}

function TabBar({ route, nav }) {
  return html`<nav class="tabbar" aria-label="Sections">
    ${TABS.map((tab) => html`<a class="tab" href=${nav.tabHref(tab)} aria-current=${tab === route.tab ? 'page' : undefined}>
      ${icons[tab]()}<span>${TAB_TITLES[tab]}</span></a>`)}
  </nav>`;
}

function Sidebar({ route, state, nav }) {
  const version = typeof window !== 'undefined' ? window.__CC_VERSION__ : null;
  return html`<aside class="sidebar" aria-label="Sections">
    <div class="sidebar-brand"><img src="icons/icon.svg" alt="" width="32" height="32" />Control Center</div>
    <nav aria-label="Sections">
      ${TABS.map((tab) => html`<a class="side-item" href=${nav.tabHref(tab)} aria-current=${tab === route.tab ? 'page' : undefined}>
        ${icons[tab]()}<span>${TAB_TITLES[tab]}</span></a>`)}
    </nav>
    <div class="sidebar-foot">
      <${ConnPill} state=${state} />
      ${version && html`<div>Version ${version}</div>`}
    </div>
  </aside>`;
}

export function App(props) {
  const { route, state, nav } = props;
  const View = viewFor(route);
  const back = route.layers.length
    ? { href: format(parent(route)), label: backLabel(route), onClick: nav.onBack }
    : null;
  return html`<div class="app">
    <${Sidebar} route=${route} state=${state} nav=${nav} />
    <main class="main" id="main">
      <${Layer} key=${format(route)} title=${routeTitle(route)} back=${back} trailing=${html`<${ConnPill} state=${state} />`}>
        <${View} ...${props} />
      </${Layer}>
    </main>
    <${TabBar} route=${route} nav=${nav} />
  </div>`;
}
