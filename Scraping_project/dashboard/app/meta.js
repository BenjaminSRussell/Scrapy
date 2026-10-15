// Stage and event metadata shared by every view. Pure: no DOM.

export const STAGES = [
  { id: 'seeds', title: 'Seeds', tint: 'indigo', glyph: 'seed', blurb: 'Starting points for the crawl' },
  { id: 'discovery', title: 'Discovery', tint: 'blue', glyph: 'search', blurb: 'Pages found by following links' },
  { id: 'analysis', title: 'Analysis', tint: 'purple', glyph: 'doc', blurb: 'Text extracted and scored' },
  { id: 'summaries', title: 'Summaries', tint: 'green', glyph: 'quote', blurb: 'Short summaries of quality pages' },
  { id: 'large_docs', title: 'Large Docs', tint: 'orange', glyph: 'stack', blurb: 'Long documents condensed in parts' },
];
export const STAGE_BY_ID = Object.fromEntries(STAGES.map((s) => [s.id, s]));
export const STAGE_TITLES = Object.fromEntries(STAGES.map((s) => [s.id, s.title]));

export const EVENT_KINDS = {
  seeded: { label: 'Seeded', stage: 'seeds' },
  discovered: { label: 'Found', stage: 'discovery' },
  analyzed: { label: 'Analyzed', stage: 'analysis' },
  summarized: { label: 'Summarized', stage: 'summaries' },
  large_doc: { label: 'Condensed', stage: 'large_docs' },
  error: { label: 'Error', stage: 'discovery' },
};

// Activity filter groups.
export const FILTERS = [
  { id: 'all', label: 'All', kinds: null },
  { id: 'found', label: 'Found', kinds: ['discovered', 'seeded'] },
  { id: 'analyzed', label: 'Analyzed', kinds: ['analyzed'] },
  { id: 'summarized', label: 'Summaries', kinds: ['summarized', 'large_doc'] },
  { id: 'errors', label: 'Errors', kinds: ['error'] },
];

// The pipeline edge an event travels along (null: no edge to animate).
export const EDGE_FOR_KIND = {
  discovered: ['seeds', 'discovery'],
  analyzed: ['discovery', 'analysis'],
  summarized: ['analysis', 'summaries'],
  large_doc: ['analysis', 'large_docs'],
};

export const SERVICE_ORDER = ['redis', 'delta', 'metrics'];
