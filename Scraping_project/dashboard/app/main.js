// Bootstrap: one store, hash routing with iOS-style push/pop transitions, render on change.
import { html, render } from './lib.js';
import { parse, format, parent, navDirection, routeTitle } from './router.js';
import { createStore } from './store.js';
import { App } from './app.js';

const root = document.getElementById('app');
const store = createStore();
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');

let route = parse(location.hash);
if (format(route) !== location.hash) history.replaceState(null, '', format(route));
const tabRoutes = { [route.tab]: route };
const scrolls = new Map();
const entries = [format(route)]; // mirrors this tab's history so Back can pop instead of push

const nav = {
  tabHref: (tab) => (tab === route.tab ? `#/${tab}` : format(tabRoutes[tab] || { tab, layers: [] })),
  onBack(e) {
    if (entries.length > 1 && entries[entries.length - 2] === format(parent(route))) {
      e.preventDefault();
      history.back();
    }
  },
};

let queued = false;
function draw() {
  queued = false;
  render(html`<${App} route=${route} state=${store.get()} store=${store} nav=${nav} now=${Date.now() / 1000} />`, root);
}
function schedule() {
  if (!queued) {
    queued = true;
    requestAnimationFrame(draw);
  }
}

function onHashChange() {
  const next = parse(location.hash);
  const hash = format(next);
  if (hash !== location.hash) history.replaceState(null, '', hash);
  if (hash === format(route)) return;
  if (entries.length > 1 && entries[entries.length - 2] === hash) entries.pop();
  else entries.push(hash);
  const dir = navDirection(route, next);
  scrolls.set(format(route), window.scrollY);
  const apply = () => {
    route = next;
    tabRoutes[next.tab] = next;
    draw();
    window.scrollTo(0, dir === 'push' ? 0 : scrolls.get(hash) || 0);
    document.title = `${routeTitle(next)} - Control Center`;
    document.querySelector('.large-title')?.focus({ preventScroll: true });
  };
  if (document.startViewTransition && !reduceMotion.matches && dir !== 'replace') {
    document.documentElement.dataset.nav = dir;
    document.startViewTransition(apply);
  } else {
    apply();
  }
}

window.addEventListener('hashchange', onHashChange);
store.subscribe(schedule);
setInterval(schedule, 5000); // keeps "2m ago" labels honest
document.title = `${routeTitle(route)} - Control Center`;
draw();
