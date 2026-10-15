// Run: node --test Scraping_project/dashboard/tests/
// Job history table (#1023): sort buttons, sticky header, optional zebra, empty state.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
    JOB_HISTORY_EMPTY_MESSAGE,
    sortJobRows,
    nextJobSort,
    jobHeaderKeyTarget,
    jobSortButtonLabel,
    sortIndicator,
    jobTableState,
    jobTableModel,
    mountJobTables,
} = require('../job-table.js');

const root = path.join(__dirname, '..');
const css = fs.readFileSync(path.join(root, 'job-table.css'), 'utf8');
const js = fs.readFileSync(path.join(root, 'job-table.js'), 'utf8');
const readme = fs.readFileSync(path.join(root, 'README.md'), 'utf8');
const checklist = fs.readFileSync(path.join(root, 'A11Y_CHECKLIST.md'), 'utf8');
const guide = fs.readFileSync(path.join(root, 'styleguide.html'), 'utf8');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

const rows = [
    { id: 'b', status: 'running', started: '2026-03-02T00:00:00Z', urls: '10' },
    { id: 'a', status: 'failed', started: '2026-03-01T00:00:00Z', urls: '2' },
    { id: 'c', status: 'running', started: '2026-03-03T00:00:00Z', urls: '' },
];

test('sort is numeric, stable, and keeps empty cells last in both directions', () => {
    const byUrls = sortJobRows(rows, 'urls', 'ascending').map(r => r.id);
    assert.deepStrictEqual(byUrls, ['a', 'b', 'c']);
    const desc = sortJobRows(rows, 'urls', 'descending').map(r => r.id);
    assert.deepStrictEqual(desc, ['b', 'a', 'c']);
    const byStatus = sortJobRows(rows, 'status', 'ascending').map(r => r.id);
    assert.deepStrictEqual(byStatus, ['a', 'b', 'c'], 'equal status keeps input order');
    assert.deepStrictEqual(rows.map(r => r.id), ['b', 'a', 'c'], 'input not mutated');
    assert.deepStrictEqual(sortJobRows(rows, 'started', 'none').map(r => r.id), ['b', 'a', 'c']);
});

test('sort button cycles ascending, descending, then unsorted', () => {
    const asc = nextJobSort({ key: null, direction: 'none' }, 'started');
    assert.deepStrictEqual(asc, { key: 'started', direction: 'ascending' });
    const desc = nextJobSort(asc, 'started');
    assert.deepStrictEqual(desc, { key: 'started', direction: 'descending' });
    assert.deepStrictEqual(nextJobSort(desc, 'started'), { key: null, direction: 'none' });
    assert.deepStrictEqual(nextJobSort(asc, 'status'), { key: 'status', direction: 'ascending' });
    assert.deepStrictEqual(nextJobSort(asc, 'bad key'), { key: null, direction: 'none' });
});

test('arrow keys move between header buttons without wrapping', () => {
    assert.strictEqual(jobHeaderKeyTarget('ArrowRight', 0, 4), 1);
    assert.strictEqual(jobHeaderKeyTarget('ArrowRight', 3, 4), null);
    assert.strictEqual(jobHeaderKeyTarget('ArrowLeft', 0, 4), null);
    assert.strictEqual(jobHeaderKeyTarget('ArrowLeft', 2, 4), 1);
    assert.strictEqual(jobHeaderKeyTarget('Home', 3, 4), 0);
    assert.strictEqual(jobHeaderKeyTarget('End', 0, 4), 3);
    assert.strictEqual(jobHeaderKeyTarget('Enter', 1, 4), null);
    assert.strictEqual(jobHeaderKeyTarget(' ', 1, 4), null);
    assert.strictEqual(jobHeaderKeyTarget('ArrowRight', 0, 0), null);
});

test('button name states the column and the next sort action', () => {
    assert.match(jobSortButtonLabel('Started', 'none'), /Started, not sorted\. Activate to sort ascending\./);
    assert.match(jobSortButtonLabel('Started', 'ascending'), /sorted ascending\. Activate to sort descending\./);
    assert.match(jobSortButtonLabel('Started', 'descending'), /sorted descending\. Activate to clear sort\./);
    assert.strictEqual(sortIndicator('ascending'), '\u25B2');
    assert.strictEqual(sortIndicator('descending'), '\u25BC');
    assert.strictEqual(sortIndicator('none'), '\u2195');
});

test('empty table keeps no invented rows and a single empty message', () => {
    const state = jobTableState([], { key: 'id', direction: 'ascending' });
    assert.strictEqual(state.empty, true);
    assert.deepStrictEqual(state.rows, []);
    assert.strictEqual(state.emptyMessage, JOB_HISTORY_EMPTY_MESSAGE);
    assert.strictEqual(state.emptyMessage, 'No job history yet.');
    const model = jobTableModel([], [{ key: 'id', label: 'Job' }], { key: null, direction: 'none' });
    assert.strictEqual(model.columns[0].ariaSort, 'none');
    assert.match(model.columns[0].buttonLabel, /not sorted/);
});

test('model marks only the active header sorted', () => {
    const model = jobTableModel(rows, [
        { key: 'id', label: 'Job' },
        { key: 'urls', label: 'URLs' },
    ], { key: 'urls', direction: 'ascending' });
    assert.deepStrictEqual(model.rows.map(r => r.id), ['a', 'b', 'c']);
    assert.strictEqual(model.columns[0].ariaSort, 'none');
    assert.strictEqual(model.columns[1].ariaSort, 'ascending');
    assert.strictEqual(model.empty, false);
});

test('styles: sticky header, optional zebra, empty row, no outline removed', () => {
    assert.match(css, /\.job-history-table thead th\s*\{[^}]*position:\s*sticky/);
    assert.match(css, /\.job-history-table thead th\s*\{[^}]*top:\s*0/);
    assert.match(css, /\.job-history-table--zebra tbody tr:nth-child\(even\)/);
    assert.match(css, /\.job-history-empty td/);
    assert.match(css, /@media \(forced-colors: active\)/);
    assert.match(css, /min-height:\s*44px/);
    assert.ok(!/outline:\s*(none|0)\b/.test(css));
    assert.ok(!/innerHTML/.test(js));
});

test('keyboard sortable headers are documented', () => {
    assert.match(readme, /Enter or Space/);
    assert.match(readme, /Arrow Left \/ Arrow Right/);
    assert.match(readme, /Home \/ End/);
    assert.match(readme, /aria-sort/);
    assert.match(readme, /job-history-table--zebra/);
    assert.match(checklist, /#1023/);
    assert.match(checklist, /Enter or Space/);
});

test('styleguide shows a zebra sample and an empty table; dashboard links the asset', () => {
    assert.match(guide, /class="job-history-table job-history-table--zebra"/);
    assert.match(guide, /class="job-history-empty"/);
    assert.match(guide, /No job history yet\./);
    assert.match(guide, /href="job-table\.css"/);
    assert.match(guide, /src="job-table\.js"/);
    const emptyTable = guide.match(/<table[^>]*id="job-history-empty"[^>]*>/);
    assert.ok(emptyTable, 'empty specimen table');
    assert.ok(!/zebra/.test(emptyTable[0]), 'empty specimen is not zebra');
    assert.match(index, /href="job-table\.css"/);
    assert.match(index, /src="job-table\.js"/);
    assert.strictEqual(mountJobTables(null), 0);
    assert.strictEqual(mountJobTables({ querySelectorAll: () => [] }), 0);
});
