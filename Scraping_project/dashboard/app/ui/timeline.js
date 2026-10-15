// A URL's journey through the stages, as a vertical timeline.
import { html } from '../lib.js';
import { STAGE_BY_ID } from '../meta.js';
import { formatValue, relativeTime, clockTime } from '../format.js';
import { icons } from './icons.js';

const STATUS_TEXT = { done: 'Done', waiting: 'Waiting', skipped: 'Skipped' };

function Field({ field }) {
  const text = formatValue(field.value, field.format);
  const block = field.format === 'text' && text.length > 44;
  return block
    ? html`<div class="field block"><dt>${field.label}</dt><dd>${text}</dd></div>`
    : html`<div class="field"><dt>${field.label}</dt><dd>${text}</dd></div>`;
}

export function Timeline({ steps, now }) {
  return html`<ol class="timeline">
    ${steps.map((step) => {
      const tint = STAGE_BY_ID[step.stage]?.tint || 'gray';
      const marker = step.status === 'done' ? icons.check() : step.status === 'skipped' ? icons.skipped() : icons.waiting();
      return html`<li class=${`step ${step.status} tint-${tint}`}>
        <span class="step-marker" aria-hidden="true">${marker}</span>
        <div class="step-body">
          <div class="step-head">
            <span class="step-title">${step.title}</span>
            ${step.t != null
              ? html`<time datetime=${new Date(step.t * 1000).toISOString()} title=${clockTime(step.t)}>${relativeTime(step.t, now)}</time>`
              : html`<span class="step-status">${step.note || STATUS_TEXT[step.status]}</span>`}
          </div>
          <span class="sr-only">${STATUS_TEXT[step.status]}.</span>
          ${step.status === 'done' && step.fields.length > 0 && html`<dl class="fields">${step.fields.map((f) => html`<${Field} field=${f} />`)}</dl>`}
        </div>
      </li>`;
    })}
  </ol>`;
}
