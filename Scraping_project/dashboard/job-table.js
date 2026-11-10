// Sortable job-history table (#1023).
// Headers use a button inside <th> (not a clickable header cell).
// Keyboard: Tab to a sort button; Enter or Space cycles ascending ->
// descending -> unsorted; ArrowLeft/ArrowRight move focus between those
// buttons; Home/End jump to the first or last. aria-sort lives on the <th>.
// Zebra striping is optional (class job-history-table--zebra). An empty
// table keeps the header and shows JOB_HISTORY_EMPTY_MESSAGE.
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else {
        Object.assign(root, api);
        if (!root.document) return;
        const boot = function () { api.mountJobTables(root.document); };
        if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', boot);
        else boot();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    const JOB_HISTORY_EMPTY_MESSAGE = 'No job history yet.';
    const KEY_RE = /^[A-Za-z][A-Za-z0-9_]*$/;
    const originals = typeof WeakMap === 'function' ? new WeakMap() : null;

    function isEmptyCell(value) {
        return value == null || value === '';
    }

    function compareJobCell(a, b) {
        const aEmpty = isEmptyCell(a);
        const bEmpty = isEmptyCell(b);
        if (aEmpty || bEmpty) {
            if (aEmpty && bEmpty) return 0;
            return aEmpty ? 1 : -1;
        }
        const aNum = typeof a === 'number' ? a : (/^-?\d+(\.\d+)?$/.test(String(a)) ? Number(a) : NaN);
        const bNum = typeof b === 'number' ? b : (/^-?\d+(\.\d+)?$/.test(String(b)) ? Number(b) : NaN);
        if (Number.isFinite(aNum) && Number.isFinite(bNum)) {
            if (aNum === bNum) return 0;
            return aNum < bNum ? -1 : 1;
        }
        return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
    }

    // Stable. Empty cells stay last in both directions. Input is not mutated.
    function sortJobRows(rows, key, direction) {
        const list = Array.isArray(rows) ? rows : [];
        if (!key || (direction !== 'ascending' && direction !== 'descending')) return list.slice();
        const dir = direction === 'descending' ? -1 : 1;
        return list
            .map(function (row, index) { return { row: row, index: index }; })
            .sort(function (a, b) {
                const av = a.row ? a.row[key] : undefined;
                const bv = b.row ? b.row[key] : undefined;
                if (isEmptyCell(av) || isEmptyCell(bv)) {
                    const emptyCmp = compareJobCell(av, bv);
                    if (emptyCmp !== 0) return emptyCmp;
                }
                const cmp = compareJobCell(av, bv);
                return cmp === 0 ? a.index - b.index : cmp * dir;
            })
            .map(function (item) { return item.row; });
    }

    // First activation sorts ascending, second descending, third clears.
    // A different column starts at ascending.
    function nextJobSort(current, key) {
        const k = typeof key === 'string' ? key : '';
        if (!KEY_RE.test(k)) return { key: null, direction: 'none' };
        const same = current && current.key === k ? current.direction : 'none';
        if (same === 'ascending') return { key: k, direction: 'descending' };
        if (same === 'descending') return { key: null, direction: 'none' };
        return { key: k, direction: 'ascending' };
    }

    // Index of the sort button to focus, or null if this key does not move.
    // Does not wrap: ArrowRight on the last header stays put.
    function jobHeaderKeyTarget(key, index, count) {
        const n = Math.floor(Number(count) || 0);
        if (n <= 0) return null;
        let i = Math.floor(Number(index) || 0);
        if (i < 0 || i >= n) i = Math.min(Math.max(i, 0), n - 1);
        switch (key) {
            case 'ArrowRight':
            case 'Right':
                return i + 1 < n ? i + 1 : null;
            case 'ArrowLeft':
            case 'Left':
                return i > 0 ? i - 1 : null;
            case 'Home':
                return 0;
            case 'End':
                return n - 1;
            default:
                return null;
        }
    }

    function jobSortButtonLabel(columnLabel, direction) {
        const label = columnLabel == null || columnLabel === '' ? 'Column' : String(columnLabel);
        if (direction === 'ascending') return label + ', sorted ascending. Activate to sort descending.';
        if (direction === 'descending') return label + ', sorted descending. Activate to clear sort.';
        return label + ', not sorted. Activate to sort ascending.';
    }

    function sortIndicator(direction) {
        if (direction === 'ascending') return '\u25B2';
        if (direction === 'descending') return '\u25BC';
        return '\u2195';
    }

    function jobTableState(rows, sort, emptyMessage) {
        const list = Array.isArray(rows) ? rows.slice() : [];
        const key = sort && sort.key ? sort.key : null;
        const direction = sort && sort.direction ? sort.direction : 'none';
        const active = !!(key && (direction === 'ascending' || direction === 'descending'));
        const message = emptyMessage == null || emptyMessage === ''
            ? JOB_HISTORY_EMPTY_MESSAGE
            : String(emptyMessage);
        return {
            empty: list.length === 0,
            emptyMessage: message,
            rows: active ? sortJobRows(list, key, direction) : list.slice(),
            sort: active ? { key: key, direction: direction } : { key: null, direction: 'none' },
        };
    }

    function jobTableModel(rows, columns, sort, emptyMessage) {
        const state = jobTableState(rows, sort, emptyMessage);
        const cols = Array.isArray(columns) ? columns : [];
        return {
            empty: state.empty,
            emptyMessage: state.emptyMessage,
            sort: state.sort,
            rows: state.rows,
            columns: cols.map(function (col) {
                const key = col && col.key;
                const label = col && col.label ? col.label : key;
                const ariaSort = state.sort.key === key ? state.sort.direction : 'none';
                return {
                    key: key,
                    label: label,
                    ariaSort: ariaSort,
                    buttonLabel: jobSortButtonLabel(label, ariaSort),
                    indicator: sortIndicator(ariaSort),
                };
            }),
        };
    }

    function cellString(value) {
        if (typeof value === 'number' && Number.isFinite(value)) return String(value);
        if (typeof value === 'string') return value;
        return '';
    }

    function normalizeRows(rows, columns) {
        if (!Array.isArray(rows)) return [];
        const keys = (columns || []).map(function (col) { return col.key; }).filter(function (key) {
            return KEY_RE.test(key || '');
        });
        return rows.filter(function (row) { return row && typeof row === 'object'; }).map(function (row) {
            const out = {};
            const use = keys.length ? keys : Object.keys(row).filter(function (key) { return KEY_RE.test(key); });
            for (let i = 0; i < use.length; i++) {
                out[use[i]] = cellString(row[use[i]]);
            }
            return out;
        });
    }

    function readColumns(table) {
        const buttons = table.querySelectorAll('thead button.job-history-sort');
        const cols = [];
        for (let i = 0; i < buttons.length; i++) {
            const button = buttons[i];
            const key = button.getAttribute('data-sort-key') || '';
            if (!KEY_RE.test(key)) continue;
            const fromAttr = button.getAttribute('data-label');
            const labelEl = button.querySelector('.job-history-sort-label');
            const label = fromAttr || (labelEl ? labelEl.textContent : '') || key;
            cols.push({ key: key, label: label });
        }
        return cols;
    }

    function readJobRowsFromDom(table, columns) {
        const bodyRows = table.querySelectorAll('tbody tr[data-job-row]');
        const rows = [];
        for (let r = 0; r < bodyRows.length; r++) {
            const tr = bodyRows[r];
            const row = {};
            for (let c = 0; c < columns.length; c++) {
                const key = columns[c].key;
                const raw = tr.getAttribute('data-' + key);
                row[key] = raw == null ? '' : raw;
            }
            rows.push(row);
        }
        return rows;
    }

    function currentSort(table) {
        const th = table.querySelector('thead th[aria-sort="ascending"], thead th[aria-sort="descending"]');
        if (!th) return { key: null, direction: 'none' };
        const button = th.querySelector('button.job-history-sort');
        const key = button ? button.getAttribute('data-sort-key') : null;
        if (!key) return { key: null, direction: 'none' };
        return { key: key, direction: th.getAttribute('aria-sort') };
    }

    function paintButton(button, meta) {
        let label = button.querySelector('.job-history-sort-label');
        let ind = button.querySelector('.job-history-sort-ind');
        if (!label || !ind) {
            while (button.firstChild) button.removeChild(button.firstChild);
            label = button.ownerDocument.createElement('span');
            label.className = 'job-history-sort-label';
            ind = button.ownerDocument.createElement('span');
            ind.className = 'job-history-sort-ind';
            ind.setAttribute('aria-hidden', 'true');
            button.appendChild(label);
            button.appendChild(ind);
        }
        label.textContent = meta.label;
        ind.textContent = meta.indicator;
        button.setAttribute('aria-label', meta.buttonLabel);
    }

    function applyJobTable(table, rows, sort) {
        const columns = readColumns(table);
        const message = table.getAttribute('data-empty-message');
        const model = jobTableModel(rows, columns, sort, message);
        const headerCells = table.querySelectorAll('thead th');
        for (let i = 0; i < model.columns.length && i < headerCells.length; i++) {
            const th = headerCells[i];
            const meta = model.columns[i];
            th.setAttribute('aria-sort', meta.ariaSort);
            const button = th.querySelector('button.job-history-sort');
            if (button) paintButton(button, meta);
        }
        const tbody = table.querySelector('tbody');
        if (!tbody) return model;
        while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
        const doc = table.ownerDocument;
        if (model.empty) {
            const tr = doc.createElement('tr');
            tr.className = 'job-history-empty';
            const td = doc.createElement('td');
            td.colSpan = model.columns.length || 1;
            td.textContent = model.emptyMessage;
            tr.appendChild(td);
            tbody.appendChild(tr);
            return model;
        }
        for (let r = 0; r < model.rows.length; r++) {
            const row = model.rows[r];
            const tr = doc.createElement('tr');
            tr.setAttribute('data-job-row', '');
            for (let c = 0; c < model.columns.length; c++) {
                const key = model.columns[c].key;
                const value = row[key] == null ? '' : String(row[key]);
                tr.setAttribute('data-' + key, value);
                const td = doc.createElement('td');
                td.textContent = value;
                tr.appendChild(td);
            }
            tbody.appendChild(tr);
        }
        return model;
    }

    function mountOne(table) {
        if (!table || !table.querySelector || table.getAttribute('data-job-table-ready') === '1') return false;
        if (!table.querySelector('thead') || !table.querySelector('tbody')) return false;
        table.setAttribute('data-job-table-ready', '1');
        const columns = readColumns(table);
        const initial = normalizeRows(readJobRowsFromDom(table, columns), columns);
        if (originals) originals.set(table, initial);
        applyJobTable(table, initial, currentSort(table));
        const thead = table.querySelector('thead');
        thead.addEventListener('click', function (event) {
            const target = event.target;
            const button = target && target.closest ? target.closest('button.job-history-sort') : null;
            if (!button || !table.contains(button)) return;
            const source = originals ? originals.get(table) : readJobRowsFromDom(table, readColumns(table));
            const next = nextJobSort(currentSort(table), button.getAttribute('data-sort-key'));
            applyJobTable(table, source || [], next);
        });
        thead.addEventListener('keydown', function (event) {
            const buttons = table.querySelectorAll('thead button.job-history-sort');
            let index = -1;
            for (let i = 0; i < buttons.length; i++) {
                if (buttons[i] === event.target) { index = i; break; }
            }
            if (index < 0) return;
            const next = jobHeaderKeyTarget(event.key, index, buttons.length);
            if (next == null) return;
            event.preventDefault();
            buttons[next].focus();
        });
        return true;
    }

    function mountJobTables(root) {
        if (!root || !root.querySelectorAll) return 0;
        const tables = root.querySelectorAll('[data-job-table] table.job-history-table');
        let n = 0;
        for (let i = 0; i < tables.length; i++) {
            if (mountOne(tables[i])) n += 1;
        }
        return n;
    }

    function setJobTableRows(table, rows) {
        if (!table || !table.querySelector) return null;
        if (table.getAttribute('data-job-table-ready') !== '1') mountOne(table);
        const columns = readColumns(table);
        const copy = normalizeRows(rows, columns);
        if (originals) originals.set(table, copy);
        return applyJobTable(table, copy, currentSort(table));
    }

    return {
        JOB_HISTORY_EMPTY_MESSAGE: JOB_HISTORY_EMPTY_MESSAGE,
        sortJobRows: sortJobRows,
        nextJobSort: nextJobSort,
        jobHeaderKeyTarget: jobHeaderKeyTarget,
        jobSortButtonLabel: jobSortButtonLabel,
        sortIndicator: sortIndicator,
        jobTableState: jobTableState,
        jobTableModel: jobTableModel,
        mountJobTables: mountJobTables,
        setJobTableRows: setJobTableRows,
    };
});
