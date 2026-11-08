/** Decode the two Git path tokens in a `diff --git` header. */
function token(source: string, offset: number): { value: string; next: number } | undefined {
  if (source[offset] !== '"') {
    const end = source.indexOf(' ', offset),
      next = end < 0 ? source.length : end;
    if (next === offset) return;
    return { value: source.slice(offset, next), next };
  }
  const bytes: number[] = [];
  let at = offset + 1;
  const append = (value: string) => bytes.push(...new TextEncoder().encode(value));
  while (at < source.length) {
    const value = source[at++];
    if (value === '"')
      return { value: new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(bytes)), next: at };
    if (value !== '\\') {
      append(value);
      continue;
    }
    if (at >= source.length) return;
    const escaped = source[at++],
      mapped: { [key: string]: string } = {
        a: '\x07',
        b: '\b',
        f: '\f',
        n: '\n',
        r: '\r',
        t: '\t',
        v: '\v',
        '\\': '\\',
        '"': '"',
      };
    if (mapped[escaped] !== undefined) {
      append(mapped[escaped]);
      continue;
    }
    if (/[0-7]/.test(escaped) && /^[0-7]{2}/.test(source.slice(at, at + 2))) {
      bytes.push(Number.parseInt(escaped + source.slice(at, at + 2), 8));
      at += 2;
      continue;
    }
    return;
  }
}

export function gitDiffHeaderPaths(line: string): [string, string] | undefined {
  const prefix = 'diff --git ';
  if (!line.startsWith(prefix)) return;
  try {
    const left = token(line, prefix.length);
    if (!left || line[left.next] !== ' ') return;
    const right = token(line, left.next + 1);
    if (!right || right.next !== line.length) return;
    return [left.value, right.value];
  } catch {
    return;
  }
}

/** An extended header line saying either side of the section is a symlink (Git mode 120000). */
const SYMLINK_HEADER = /^(?:(?:old|new|deleted file|new file) mode 120000|index [0-9a-f]+\.\.[0-9a-f]+ 120000)$/;
/** The first line after a section's extended header. */
const HEADER_END = /^(?:--- |@@ |Binary files |GIT binary patch)/;

/**
 * Replace every diff section touching a path `withheld` names with one marker line, so a file the snapshot keeps
 * out of the readable tree cannot come back through retained history. An unparseable diff header is withheld too,
 * and so is any symlink section, whatever its path: its body is link text, which the snapshot never records.
 */
export function withholdHistory(raw: string, withheld: (path: string) => string | undefined): string {
  const output: string[] = [];
  let skipping = false,
    // A section's extended header is held back until it shows whether the section is a symlink.
    header: { lines: string[]; path: string } | undefined;
  const release = () => {
    if (header) output.push(...header.lines);
    header = undefined;
  };
  for (const line of raw.split('\n')) {
    if (line.startsWith('commit ')) {
      release();
      skipping = false;
    } else if (line.startsWith('diff --git ')) {
      release();
      const paths = gitDiffHeaderPaths(line)?.map((value) => value.replace(/^[ab]\//, '')),
        reason = paths ? paths.map(withheld).find(Boolean) : 'unparseable diff header';
      skipping = Boolean(reason);
      if (reason) {
        output.push(`diff withheld: ${paths?.[1] ?? 'unknown path'} (${reason})`);
        continue;
      }
      header = { lines: [line], path: paths?.[1] ?? 'unknown path' };
      continue;
    }
    if (skipping) continue;
    if (header) {
      if (SYMLINK_HEADER.test(line)) {
        output.push(`diff withheld: ${header.path} (symlink)`);
        header = undefined;
        skipping = true;
        continue;
      }
      if (!HEADER_END.test(line)) {
        header.lines.push(line);
        continue;
      }
      release();
    }
    output.push(line);
  }
  release();
  return output.join('\n');
}

/** Return only exact path hunks, keeping one commit preamble per matching commit. */
export function historyForPath(raw: string, path: string): string | undefined {
  const expected = new Set([`a/${path}`, `b/${path}`]),
    output: string[] = [],
    lines = raw.split('\n');
  let preamble: string[] = [],
    section: string[] | undefined,
    include = false,
    preambleEmitted = false;
  const flush = () => {
    if (section && include) {
      if (!preambleEmitted) {
        output.push(...preamble);
        preambleEmitted = true;
      }
      output.push(...section);
    }
    section = undefined;
    include = false;
  };
  for (const line of lines) {
    if (line.startsWith('commit ')) {
      flush();
      preamble = [line];
      preambleEmitted = false;
      continue;
    }
    if (line.startsWith('diff --git ')) {
      flush();
      section = [line];
      const paths = gitDiffHeaderPaths(line);
      include = Boolean(paths && (expected.has(paths[0]) || expected.has(paths[1])));
      continue;
    }
    if (section) section.push(line);
    else preamble.push(line);
  }
  flush();
  while (output.at(-1) === '') output.pop();
  return output.length ? output.join('\n') : undefined;
}
