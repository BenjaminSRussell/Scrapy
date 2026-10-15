// Run: node --test Scraping_project/dashboard/tests/
// A missing stage series is not a zero (#987).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { stageMetricReading, stageMetricNumber, stageMetricText, stageCompressionLabel } = require('../format-utils.js');

test('a missing key is not zero, and a present zero stays zero', () => {
    const metrics = {
        stage1_urls_discovered_total: 0,
        stage2_pages_analyzed_total: 12,
        stage4_avg_compression_ratio: 0,
    };
    const zero = stageMetricReading(metrics, 'stage1_urls_discovered_total');
    const present = stageMetricReading(metrics, 'stage2_pages_analyzed_total');
    const missing = stageMetricReading(metrics, 'stage2_quality_docs_total');
    const compressionZero = stageMetricReading(metrics, 'stage4_avg_compression_ratio');
    assert.strictEqual(zero.missing, false);
    assert.strictEqual(zero.value, 0);
    assert.strictEqual(stageMetricNumber(zero), 0);
    assert.strictEqual(stageMetricText(zero, '0'), '0');
    assert.strictEqual(present.missing, false);
    assert.strictEqual(stageMetricText(present, '12'), '12');
    assert.strictEqual(missing.missing, true);
    assert.strictEqual(missing.value, null);
    assert.strictEqual(stageMetricNumber(missing), 0);
    assert.strictEqual(stageMetricText(missing, '0'), '\u2014');
    assert.notStrictEqual(stageMetricText(missing, '0'), stageMetricText(zero, '0'));
    assert.strictEqual(stageMetricText(compressionZero, stageCompressionLabel(compressionZero.value)), '0x');
    assert.strictEqual(stageMetricText(missing, stageCompressionLabel(0)), '\u2014');
    assert.strictEqual(stageCompressionLabel(0.25), '4x');
    assert.strictEqual(stageMetricReading(null, 'stage1_urls_discovered_total').missing, true);
    assert.strictEqual(stageMetricReading({ stage2_avg_word_count: NaN }, 'stage2_avg_word_count').missing, true);
});

test('stage cards paint a warning for a missing series and a spinner while loading', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    for (const key of [
        'stage1_urls_discovered_total',
        'stage1_urls_queued_total',
        'stage2_pages_analyzed_total',
        'stage2_quality_docs_total',
        'stage2_massive_docs_total',
        'stage2_avg_word_count',
        'stage3_summaries_created_total',
        'stage3_documents_deduplicated_total',
        'stage4_large_doc_summaries_total',
        'stage4_avg_compression_ratio',
        'pipeline_scout_instances',
    ]) {
        assert.match(app, new RegExp(`stageMetricReading\\(metrics, '${key}'\\)`));
    }
    assert.match(app, /stage-metric-warn/);
    assert.match(app, /stage-card-warn/);
    assert.match(app, /stage-card-spinner/);
    assert.match(app, /is-partial/);
    assert.match(app, /is-card-loading/);
    assert.match(app, /syncStageCardStates\(\)/);
    assert.match(html, /\.stage-metric\.is-missing \.stage-metric-value/);
    assert.match(html, /\.stage-card\.is-partial/);
    assert.match(html, /\.stage-card-spinner/);
    assert.match(html, /@keyframes stageCardSpin/);
    assert.doesNotMatch(app, /stage2_quality_docs_total'\]\s*\|\|\s*0/);
    assert.doesNotMatch(app, /stage4_avg_compression_ratio'\]\s*\|\|\s*0/);
});
