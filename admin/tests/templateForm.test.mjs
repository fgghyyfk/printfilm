import test from 'node:test';
import assert from 'node:assert/strict';
import { validateTemplateDraft } from '../src/lib/templateForm.ts';

const valid = { id: 'film', name: '电影风格', shot_duration_min: 3, shot_duration_max: 8, sort_order: 0 };
test('template identity is required for creation and name is required for every save', () => {
  assert.ok(validateTemplateDraft({ ...valid, id: ' ', name: ' ' }, true).id);
  assert.ok(validateTemplateDraft({ ...valid, name: ' ' }, false).name);
  assert.equal(validateTemplateDraft({ ...valid, id: '' }, false).id, undefined);
});
test('template durations reject fractional, zero, nonfinite and inverted ranges', () => {
  for (const value of [0, -1, 1.5, NaN, Infinity]) assert.ok(validateTemplateDraft({ ...valid, shot_duration_min: value }, true).shot_duration_min);
  assert.ok(validateTemplateDraft({ ...valid, shot_duration_max: 2 }, true).shot_duration_max);
  assert.deepEqual(validateTemplateDraft({ ...valid, shot_duration_min: 8 }, true), {});
});
test('template limits and sort values are validated without rejecting valid negative sort order', () => {
  assert.ok(validateTemplateDraft({ ...valid, id: 'x'.repeat(65) }, true).id);
  assert.ok(validateTemplateDraft({ ...valid, name: 'x'.repeat(129) }, true).name);
  assert.ok(validateTemplateDraft({ ...valid, sort_order: 0.5 }, true).sort_order);
  assert.deepEqual(validateTemplateDraft({ ...valid, sort_order: -2 }, true), {});
});
