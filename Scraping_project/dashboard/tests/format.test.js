import test from 'node:test';
import assert from 'node:assert/strict';
import {
  compact, full, rate, relativeTime, splitUrl, isHttpUrl, bytes, percent, ratio, freshnessLabel, formatValue, plural,
} from '../app/format.js';

test('compact numbers', () => {
  assert.equal(compact(null), '—');
  assert.equal(compact(0), '0');
  assert.equal(compact(999), '999');
  assert.equal(compact(1000), '1K');
  assert.equal(compact(1234), '1.2K');
  assert.equal(compact(99_950), '100K');
  assert.equal(compact(3_400_000), '3.4M');
  assert.equal(compact(2_000_000_000), '2B');
  assert.equal(full(12345), '12,345');
  assert.equal(full(undefined), '—');
});

test('rates per minute', () => {
  assert.equal(rate(null), '—');
  assert.equal(rate(0), '0/min');
  assert.equal(rate(0.44), '0.4/min');
  assert.equal(rate(84.4), '84/min');
  assert.equal(rate(1234), '1.2K/min');
});

test('relative time, including clock skew', () => {
  const now = 1_000_000;
  assert.equal(relativeTime(null, now), '—');
  assert.equal(relativeTime(now - 2, now), 'just now');
  assert.equal(relativeTime(now + 30, now), 'just now');
  assert.equal(relativeTime(now - 45, now), '45s ago');
  assert.equal(relativeTime(now - 180, now), '3m ago');
  assert.equal(relativeTime(now - 7200, now), '2h ago');
  assert.equal(relativeTime(now - 3 * 86400, now), '3d ago');
});

test('splitUrl and isHttpUrl only trust http(s)', () => {
  assert.deepEqual(splitUrl('https://News.UConn.edu/a/b?c=1'), { host: 'news.uconn.edu', path: '/a/b?c=1' });
  assert.deepEqual(splitUrl('https://uconn.edu'), { host: 'uconn.edu', path: '/' });
  assert.deepEqual(splitUrl('javascript:alert(1)'), { host: null, path: 'javascript:alert(1)' });
  assert.deepEqual(splitUrl('not a url'), { host: null, path: 'not a url' });
  assert.equal(isHttpUrl('https://a.edu/'), true);
  assert.equal(isHttpUrl('http://a.edu/'), true);
  for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'ftp://a.edu', '', null, '//a.edu']) {
    assert.equal(isHttpUrl(bad), false, String(bad));
  }
  const long = 'https://a.edu/' + 'x'.repeat(4000);
  assert.equal(splitUrl(long).path.length, 4001);
});

test('bytes, percent, ratio, freshness, plural', () => {
  assert.equal(bytes(512), '512 B');
  assert.equal(bytes(1536), '1.5 KB');
  assert.equal(bytes(3 * 1048576), '3 MB');
  assert.equal(bytes(null), '—');
  assert.equal(percent(25), '25%');
  assert.equal(percent(66.666), '67%');
  assert.equal(ratio(0.02), '50× smaller');
  assert.equal(ratio(0), '—');
  assert.equal(freshnessLabel('active'), 'Active');
  assert.equal(freshnessLabel('none'), 'No data');
  assert.equal(plural(1, 'event'), '1 event');
  assert.equal(plural(12, 'event'), '12 events');
  assert.equal(plural(1, 'query', 'queries'), '1 query');
  assert.equal(plural(2, 'query', 'queries'), '2 queries');
});

test('formatValue dispatches on the server format hint', () => {
  assert.equal(formatValue(4400.4, 'int'), '4,400');
  assert.equal(formatValue(true, 'bool'), 'Yes');
  assert.equal(formatValue(false, 'bool'), 'No');
  assert.equal(formatValue(2048, 'bytes'), '2 KB');
  assert.equal(formatValue(25, 'percent'), '25%');
  assert.equal(formatValue(0.01, 'ratio'), '100× smaller');
  assert.equal(formatValue('<b>x</b>', 'text'), '<b>x</b>');
  assert.equal(formatValue(null, 'int'), '—');
});
