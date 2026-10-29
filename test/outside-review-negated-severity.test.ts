/**
 * A tagged clean phrasing on the allowlist is not a finding. sovereign-forge
 * PR #188, Codex run 2 (comment 28056) opened with "No [P1] or [P2] findings
 * at `7439afe`." and tagged nothing else; the bracket scan read it as a P1 and
 * the gate posted FAIL. The fixture is that run's output verbatim.
 * James's decision (2026-10-28): an exact allowlist, not a negation heuristic.
 * Any other phrasing keeps its tags, so near-misses fail closed.
 */
import { describe, expect, test } from 'bun:test';
import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { classifyOutsideReview, type OutsideGate } from '../lib/outside-review-result';

const ROOT = path.resolve(import.meta.dir, '..');
const LIB = path.join(ROOT, 'lib', 'outside-review-result.ts');
const SF188 = path.join(ROOT, 'test', 'fixtures', 'outside-review-severity', 'sf188-run2-negated.txt');
const REC = 'Recommendation: approve because the change keeps its data.';
const highest = (text: string, gate: OutsideGate = 'structured') => classifyOutsideReview({ text, gate }).findings.highest;

describe('an allowlisted clean phrasing is not a finding', () => {
  test('sovereign-forge #188 run 2: the CLI no longer prints VERDICT: findings, FINDINGS: P1', () => {
    const r = spawnSync(process.execPath, [LIB, '--verdict', 'structured', SF188], { encoding: 'utf8', timeout: 10000 });
    // Untagged and without the NO_FINDINGS line the structured gate asks for, so a human reads it.
    expect([r.status, r.stdout]).toEqual([4, 'VERDICT: unverified\nFINDINGS: none\nREASON: untagged_review\n']);
  });

  test('the same run with the NO_FINDINGS line reads clean', () => {
    const text = `${fs.readFileSync(SF188, 'utf8')}\nNO_FINDINGS\n`;
    const result = classifyOutsideReview({ text, gate: 'structured' });
    expect([result.verdict, result.findings.highest]).toEqual(['clean', null]);
  });

  for (const text of [
    'No [P1] or [P2] findings.',
    'No [P1] or [P2] findings',
    'no  [p1]   OR [P2]\tFINDINGS.',
    'No [P1] or [P2] findings at `7439afe`.',
    'No [P1] or [P2] findings at `7439afe`. The run-1 finding is fixed.',
    '[P2] none: no findings.',
    '[P2] none',
    '[P2] none.',
    // A bare line (no closing punctuation) counts only when a blank line or the end follows it.
    'No [P1] or [P2] findings\n\nThe diff only renames a helper.',
    '[P2] none\n',
  ]) {
    test(`allowlisted: ${text}`, () => {
      expect(highest(text)).toBeNull();
      // A blank line ends a bare allowlisted line; directly followed by text it could continue.
      expect(highest(`${text}\n\n${REC}`, 'review')).toBeNull();
    });
  }
});

describe('near-misses of the allowlist keep their tags (fail closed)', () => {
  for (const [text, level] of [
    ['There are no [P0], [P1] or [P2] issues in this diff.', 'P0'],
    ['I found zero [P1] findings.', 'P1'],
    ['The change ships without any [P0]/[P1] regressions.', 'P0'],
    ['Nothing here rises to a finding: no [P1] and no [P2].', 'P1'],
    ['Neither [P0] nor [P1] issues remain.', 'P0'],
    ['No [P1] findings except the auth bug.', 'P1'],
    ['No [P1] or [P2] findings except the auth bug.', 'P1'],
    ['No [P1] or [P2] findings at `7439afe` except the auth bug.', 'P1'],
    ['No [P1] or [P2] findings at `7439afe` `beef123`.', 'P1'],
    ['No [P1] or [P2] findings in `lib/auth.ts`.', 'P1'],
    ['No [P1] or [P2] findings:', 'P1'],
    ['- No [P1] or [P2] findings.', 'P1'],
    ['No [P1], [P2] or [P3] findings.', 'P1'],
    ['[P2] none: no findings, except naming.', 'P2'],
    ['[P2] none yet', 'P2'],
    ['No [P1] issues remain.\n- Other than style, the diff is fine.', 'P1'],
    // Codex P2, comment 28453: a line that may continue on the next one fails closed.
    ['No [P1] or [P2] findings\nexcept the missing authorization check.\n- [P3] Rename the helper.', 'P1'],
    ['No [P1] or [P2] findings\nexcept the missing authorization check', 'P1'],
    ['[P2] none: no findings\nexcept the naming drift.', 'P2'],
    ['[P2] none\nyet; one naming nit remains.', 'P2'],
    ['No [P1] or [P2] findings\nRecommendation: approve because the change keeps its data.', 'P1'],
  ] as const) {
    test(`${level}: ${text.split('\n')[0]}`, () => {
      expect(highest(text)).toBe(level);
    });
  }
});

describe('real tagged findings are still read', () => {
  for (const [text, level] of [
    ['[P1] data loss in the ledger writer', 'P1'],
    ['- [P1] No validation on the token before use', 'P1'],
    ['1. **[P0]** Nothing guards the delete path', 'P0'],
    ['The parser drops trailing rows [P1] when the file lacks a newline.', 'P1'],
    ['No [P1] findings, but one [P2] naming issue in `ledger.py`.', 'P1'],
    ['No [P0] issues; [P1] the cache never expires.', 'P0'],
    ['No new tests cover the retry path [P1].', 'P1'],
    ['- [P2] naming drift\nNo [P1] findings.', 'P1'],
    ['- [P1] the lock is never released\nNo [P1] or [P2] findings.', 'P1'],
    // Codex P2 on gstack #21: a removed inline-code span must not leave a bare "No " before the tag.
    ['- No `await` [P1]: the handler returns before the write completes.\n- [P2] naming', 'P1'],
    ['Without `fsync` [P0] the ledger loses its last row.', 'P0'],
    // Codex P2 on gstack #21 (exec probe): "zero" here is the defect, not a negation of findings.
    ['- Retry count is always zero [P1] — lib/retry.ts:42\n- [P2] naming', 'P1'],
    ['- Division by zero [P1]\n- [P2] naming', 'P1'],
    ['- [P2] naming\n- Fails with no [P1] when the cache is cold', 'P1'],
    // An exception keeps the tag: the negation does not cover it (Codex P2, exec review of #21).
    ['No [P1] issues other than the missing authorization check.\n- [P2] The error message names the wrong flag.', 'P1'],
    ['No [P0] findings except the dropped write in lib/store.ts:40.', 'P0'],
    ['No [P1] regressions apart from the race in the lock release.', 'P1'],
    ['No [P1] issues\nother than the missing authorization check.\n- [P2] Naming drift', 'P1'],
  ] as const) {
    test(`${level}: ${text.split('\n')[0]}`, () => {
      expect(highest(text)).toBe(level);
    });
  }

  test('a P1 beside an allowlisted line still blocks', () => {
    const result = classifyOutsideReview({ text: 'No [P1] or [P2] findings.\n- [P1] the lock is never released', gate: 'structured' });
    expect([result.verdict, result.findings.highest]).toEqual(['findings', 'P1']);
  });
});
