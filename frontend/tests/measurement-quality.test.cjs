const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const ts = require('typescript');

// Exercise the actual TypeScript helper without adding a browser/test framework.
const source = readFileSync(path.join(__dirname, '../src/lib/measurementQuality.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const exportsObject = {};
new Function('exports', compiled.outputText)(exportsObject);
const { qualityStatus } = exportsObject;

test('missing and legacy quality is unknown, never valid', () => {
  for (const quality of [undefined, null, {}, { quality_issues: [] },
    { measurement_valid: false, quality_status: 'unknown' }]) {
    assert.equal(qualityStatus(quality), 'unknown');
  }
});

test('only explicit validity without errors is valid', () => {
  assert.equal(qualityStatus({ measurement_valid: true }), 'valid');
  for (const extra of [{ quality_issues: ['encoder_stale'] }, { encoder_error: 'timeout' },
    { encoder_error_message: 'Read failed' }, { quality_status: 'invalid' }]) {
    assert.equal(qualityStatus({ measurement_valid: true, ...extra }), 'invalid');
  }
  assert.equal(qualityStatus({ measurement_valid: false }), 'invalid');
});

test('recording and pending analysis are labelled pending, not invalid', () => {
  assert.equal(qualityStatus({ measurement_valid: false, recording: true }), 'pending');
  assert.equal(qualityStatus({ measurement_valid: false, analysis_pending: true }), 'pending');
  assert.equal(qualityStatus({ recording: true, quality_issues: ['encoder_stale'] }), 'invalid');
});
