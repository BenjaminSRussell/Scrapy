// Placeholder until its task fills it in.
import { html } from '../lib.js';
import { Section, Loading } from '../ui/components.js';

export function HealthView() {
  return html`<${Section} label="Loading"><${Loading} /></${Section}>`;
}
