// Run: node --test Scraping_project/dashboard/tests/
import test from 'node:test';
import assert from 'node:assert/strict';
import { TABS, parse, format, push, parent, routeTitle, backLabel, navDirection } from '../app/router.js';

test('every tab and layer kind round-trips through the hash', () => {
  const routes = [
    { tab: 'pipeline', layers: [] },
    { tab: 'pipeline', layers: [{ kind: 'stage', value: 'discovery' }, { kind: 'url', value: 'https://a.edu/x?y=1#z' }] },
    { tab: 'explore', layers: [{ kind: 'path', value: 'a.edu' }, { kind: 'path', value: 'a.edu/news' }] },
    { tab: 'activity', layers: [{ kind: 'url', value: 'https://a.edu/é/日本' }] },
    { tab: 'health', layers: [] },
  ];
  for (const r of routes) assert.deepEqual(parse(format(r)), r);
  assert.deepEqual(TABS, ['pipeline', 'explore', 'activity', 'health']);
});

test('format encodes values so slashes and colons stay inside one layer', () => {
  assert.equal(format({ tab: 'pipeline', layers: [{ kind: 'url', value: 'https://a.edu/x' }] }),
    '#/pipeline/url:https%3A%2F%2Fa.edu%2Fx');
});

test('garbage falls back to the pipeline root, and a bad layer drops the rest', () => {
  for (const h of ['', '#', '#/', '#/nope', '#/<script>', 'javascript:alert(1)', '#/pipeline/%E0%A4%A']) {
    const r = parse(h);
    assert.equal(r.tab, 'pipeline', h);
  }
  assert.deepEqual(parse('#/explore/path:a.edu/bogus/url:https%3A%2F%2Fa.edu%2F'),
    { tab: 'explore', layers: [{ kind: 'path', value: 'a.edu' }] });
  assert.deepEqual(parse('#/pipeline/stage:'), { tab: 'pipeline', layers: [] });
});

test('push and parent move one layer', () => {
  const root = { tab: 'pipeline', layers: [] };
  const stage = push(root, { kind: 'stage', value: 'analysis' });
  assert.equal(stage.layers.length, 1);
  assert.deepEqual(parent(stage), root);
  assert.deepEqual(parent(root), root);
  assert.equal(root.layers.length, 0, 'push does not mutate');
});

test('titles name the top layer and the back button names its parent', () => {
  const r = { tab: 'pipeline', layers: [{ kind: 'stage', value: 'large_docs' }, { kind: 'url', value: 'https://News.A.edu/p' }] };
  assert.equal(routeTitle(r), 'news.a.edu');
  assert.equal(backLabel(r), 'Large Docs');
  assert.equal(backLabel(parent(r)), 'Pipeline');
  assert.equal(backLabel({ tab: 'health', layers: [] }), null);
  assert.equal(routeTitle({ tab: 'explore', layers: [{ kind: 'path', value: 'a.edu/news' }] }), 'news');
  assert.equal(routeTitle({ tab: 'activity', layers: [] }), 'Activity');
  assert.equal(routeTitle({ tab: 'pipeline', layers: [{ kind: 'url', value: 'not a url' }] }), 'not a url');
});

test('navDirection tells push, pop, tab switch and replace apart', () => {
  const a = { tab: 'pipeline', layers: [] };
  const b = push(a, { kind: 'stage', value: 'seeds' });
  assert.equal(navDirection(a, b), 'push');
  assert.equal(navDirection(b, a), 'pop');
  assert.equal(navDirection(a, { tab: 'health', layers: [] }), 'tab');
  assert.equal(navDirection(b, push(a, { kind: 'stage', value: 'analysis' })), 'replace');
});
