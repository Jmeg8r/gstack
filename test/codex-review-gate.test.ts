// Drives lib/codex-review-gate.ts as the skill does (exit code + stdout file +
// stderr file + CODEX_HOME rollout) and asserts BOTH the process exit code and
// the printed verdict, so a failing case is proven to block, not just to print.
import { afterAll, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const GATE = join(import.meta.dir, '..', 'lib', 'codex-review-gate.ts');
const SPAWN_TIMEOUT_MS = 30_000;
const root = mkdtempSync(join(tmpdir(), 'codex-gate-test-'));
afterAll(() => rmSync(root, { recursive: true, force: true }));

// Verbatim clean output from claude-config forge PR 40, round 2 (a0c79f0).
const CLEAN_PROSE =
  'No actionable defects were found in the changes relative to the supplied merge base. The pointer-integrity check passed, both new skill frontmatter blocks parsed successfully, and git diff --check reported no issues. End-to-end skill execution was not exercised.';
const CLEAN_VERDICT = { findings: [], overall_correctness: 'patch is correct', overall_explanation: CLEAN_PROSE, overall_confidence_score: 1.0 };
const finding = (priority: number) => ({ title: `[P${priority}] t`, body: 'b', confidence_score: 0.9, priority, code_location: { absolute_file_path: '/x', line_range: { start: 1, end: 1 } } });

let n = 0;
function rolloutLine(verdict: unknown): string {
  // Shape observed in codex-cli 0.156.1 rollouts: event_msg / item_completed / item.review_output.
  return JSON.stringify({ type: 'event_msg', payload: { type: 'item_completed', item: { type: 'exited_review_mode', review_output: verdict } } });
}

function run(opts: { exit?: number | string; out: string; verdictLines?: string[]; stderr?: string; noSession?: boolean }) {
  const dir = join(root, `case-${n++}`);
  const home = join(dir, 'codex-home');
  const id = `01a0dfe1-24ca-70b0-b90f-${String(n).padStart(12, '0')}`;
  mkdirSync(join(home, 'sessions', '2026', '09', '26'), { recursive: true });
  if (opts.verdictLines) {
    const lines = [JSON.stringify({ type: 'session_meta', payload: { id } }), ...opts.verdictLines];
    writeFileSync(join(home, 'sessions', '2026', '09', '26', `rollout-2026-09-26T18-40-17-${id}.jsonl`), lines.join('\n') + '\n');
  }
  const stderr = opts.stderr ?? (opts.noSession ? 'model: gpt-6-astra\n' : `workdir: /repo\nmodel: gpt-6-astra\nsession id: ${id}\n`);
  writeFileSync(join(dir, 'out'), opts.out);
  writeFileSync(join(dir, 'err'), stderr);
  const p = Bun.spawnSync(['bun', GATE, String(opts.exit ?? 0), join(dir, 'out'), join(dir, 'err')], { env: { ...process.env, CODEX_HOME: home }, timeout: SPAWN_TIMEOUT_MS });
  return { code: p.exitCode, stdout: p.stdout.toString() };
}

function expectFail(r: { code: number; stdout: string }, why: string) {
  expect(r.code).toBe(1);
  expect(r.stdout).not.toContain('GATE: PASS');
  expect(r.stdout).toContain(`GATE: FAIL (${why}`);
}

function expectPass(r: { code: number; stdout: string }, source: string) {
  expect(r.code).toBe(0);
  expect(r.stdout).toContain('GATE: PASS');
  expect(r.stdout).toContain(`GATE_SOURCE: ${source}`);
}

describe('codex review gate — required failing cases stay FAIL', () => {
  test('non-zero exit fails even with a clean structured verdict', () => {
    expectFail(run({ exit: 1, out: CLEAN_PROSE, verdictLines: [rolloutLine(CLEAN_VERDICT)] }), 'fail-closed: codex exited 1');
  });
  test('timeout exit 124 fails', () => {
    expectFail(run({ exit: 124, out: '', verdictLines: [rolloutLine(CLEAN_VERDICT)] }), 'fail-closed: codex exited 124');
  });
  test('empty output fails even with a clean structured verdict', () => {
    expectFail(run({ out: '', verdictLines: [rolloutLine(CLEAN_VERDICT)] }), 'fail-closed: empty output');
  });
  test('whitespace-only output fails', () => {
    expectFail(run({ out: '  \n\t\n', verdictLines: [rolloutLine(CLEAN_VERDICT)] }), 'fail-closed: empty output');
  });
  test('garbage untagged output with no structured verdict fails', () => {
    expectFail(run({ out: 'lorem ipsum stack trace?? something happened', noSession: true }), 'fail-closed: untagged output');
  });
  test('clean prose alone is NOT a pass — phrasing is never trusted without the structured verdict', () => {
    expectFail(run({ out: CLEAN_PROSE, noSession: true }), 'fail-closed: untagged output');
  });
  test('session id present but rollout missing falls back to text rules and fails untagged', () => {
    expectFail(run({ out: CLEAN_PROSE }), 'fail-closed: untagged output');
  });
});

describe('codex review gate — structured verdict', () => {
  test('clean phrasing with a "patch is correct" verdict PASSES', () => {
    const r = run({ out: CLEAN_PROSE, verdictLines: [rolloutLine(CLEAN_VERDICT)] });
    expectPass(r, 'structured');
    expect(r.stdout).toContain('clean review');
  });
  test('P1 finding fails', () => {
    expectFail(run({ out: '- [P1] x', verdictLines: [rolloutLine({ ...CLEAN_VERDICT, overall_correctness: 'patch is incorrect', findings: [finding(1), finding(2)] })] }), '1 critical findings');
  });
  test('P0 finding fails', () => {
    expectFail(run({ out: '- [P0] x', verdictLines: [rolloutLine({ ...CLEAN_VERDICT, overall_correctness: 'patch is incorrect', findings: [finding(0)] })] }), '1 critical findings');
  });
  test('P2/P3-only findings pass (advisory)', () => {
    expectPass(run({ out: '- [P2] x\n- [P3] y', verdictLines: [rolloutLine({ ...CLEAN_VERDICT, overall_correctness: 'patch is incorrect', findings: [finding(2), finding(3)] })] }), 'structured');
  });
  test('P2 finding whose prose quotes "[P1]" still passes — structured priority is authoritative', () => {
    expectPass(run({ out: '- [P2] the gate greps for [P1] markers', verdictLines: [rolloutLine({ ...CLEAN_VERDICT, overall_correctness: 'patch is incorrect', findings: [finding(2)] })] }), 'structured');
  });
  test('no findings but "patch is incorrect" fails as inconsistent', () => {
    expectFail(run({ out: CLEAN_PROSE, verdictLines: [rolloutLine({ ...CLEAN_VERDICT, overall_correctness: 'patch is incorrect' })] }), 'fail-closed: no findings but verdict');
  });
  test('unknown overall_correctness value fails as malformed', () => {
    expectFail(run({ out: CLEAN_PROSE, verdictLines: [rolloutLine({ ...CLEAN_VERDICT, overall_correctness: 'looks fine' })] }), 'fail-closed: malformed structured verdict');
  });
  test('non-integer / out-of-range priority fails as malformed', () => {
    for (const priority of ['1', 1.5, 4, -1, null]) {
      expectFail(run({ out: 'x', verdictLines: [rolloutLine({ ...CLEAN_VERDICT, findings: [{ priority }] })] }), 'fail-closed: malformed structured verdict');
    }
  });
  test('findings not an array fails as malformed', () => {
    expectFail(run({ out: 'x', verdictLines: [rolloutLine({ ...CLEAN_VERDICT, findings: null })] }), 'fail-closed: malformed structured verdict');
  });
  test('two different verdicts in one rollout fail as ambiguous', () => {
    const bad = { ...CLEAN_VERDICT, overall_correctness: 'patch is incorrect', findings: [finding(1)] };
    expectFail(run({ out: CLEAN_PROSE, verdictLines: [rolloutLine(CLEAN_VERDICT), rolloutLine(bad)] }), 'fail-closed: ambiguous structured verdict');
  });
  test('the same verdict repeated in two events is not ambiguous', () => {
    expectPass(run({ out: CLEAN_PROSE, verdictLines: [rolloutLine(CLEAN_VERDICT), rolloutLine(CLEAN_VERDICT)] }), 'structured');
  });
  test('a truncated verdict line fails instead of being skipped', () => {
    expectFail(run({ out: CLEAN_PROSE, verdictLines: [rolloutLine(CLEAN_VERDICT).slice(0, 60) + '"overall_correctness"'] }), 'fail-closed: malformed structured verdict');
  });
  test('two different session ids on stderr → no structured lookup → untagged FAIL', () => {
    const r = run({ out: CLEAN_PROSE, verdictLines: [rolloutLine(CLEAN_VERDICT)], stderr: 'session id: 01a0dfe1-24ca-70b0-b90f-000000000001\nsession id: 01a0dfe1-24ca-70b0-b90f-000000000002\n' });
    expectFail(r, 'fail-closed: untagged output');
  });
});

describe('codex review gate — text fallback (custom-instructions codex exec path)', () => {
  test('[P1] fails', () => {
    expectFail(run({ out: '[P1] sql injection at a.rb:1', noSession: true }), '1 critical findings');
  });
  test('native P0: label fails', () => {
    expectFail(run({ out: '- P0: data loss', noSession: true }), '1 critical findings');
  });
  test('[P2]-only passes', () => {
    expectPass(run({ out: '[P2] naming nit', noSession: true }), 'text');
  });
  test('NO_FINDINGS sentinel on its own line passes', () => {
    expectPass(run({ out: 'Reviewed the diff.\nNO_FINDINGS\n', noSession: true }), 'text');
  });
  test('NO_FINDINGS embedded mid-sentence does not pass', () => {
    expectFail(run({ out: 'I would normally say NO_FINDINGS but here it is unclear', noSession: true }), 'fail-closed: untagged output');
  });
  test('unknown severity tag [P7] fails', () => {
    expectFail(run({ out: '[P7] ???', noSession: true }), 'fail-closed: unknown severity tag');
  });
});

describe('codex review gate — CLI contract', () => {
  test('bad arguments exit 2 and never print PASS', () => {
    const p = Bun.spawnSync(['bun', GATE, 'zero', '/nonexistent', '/nonexistent'], { timeout: SPAWN_TIMEOUT_MS });
    expect(p.exitCode).toBe(2);
    expect(p.stdout.toString()).not.toContain('GATE: PASS');
  });
  test('unreadable output file fails closed', () => {
    const p = Bun.spawnSync(['bun', GATE, '0', join(root, 'missing-out'), join(root, 'missing-err')], { timeout: SPAWN_TIMEOUT_MS });
    expect(p.exitCode).toBe(1);
    expect(p.stdout.toString()).toContain('GATE: FAIL (fail-closed: gate error');
  });
});
