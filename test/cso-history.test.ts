import { describe,expect,test } from 'bun:test';
import { gitDiffHeaderPaths,historyForPath,withholdHistory } from '../lib/cso/history';

describe('CSO retained Git history path filtering',()=>{
  test('matches exact paths without leaking a prefix sibling hunk',()=>{
    const raw=`commit ${'a'.repeat(40)}
Author: Fixture
diff --git a/foo b/foo
--- a/foo
+++ b/foo
+wanted
diff --git a/foo-extra b/foo-extra
--- a/foo-extra
+++ b/foo-extra
+must-not-leak
`;
    const selected=historyForPath(raw,'foo')!;
    expect(selected).toContain('+wanted');
    expect(selected).not.toContain('must-not-leak');
  });

  test('decodes Git C-quoted UTF-8 and space-bearing header paths',()=>{
    expect(gitDiffHeaderPaths('diff --git "a/caf\\303\\251 file.ts" "b/caf\\303\\251 file.ts"')).toEqual(['a/café file.ts','b/café file.ts']);
    const raw=`commit ${'b'.repeat(40)}\nSubject: Unicode\ndiff --git "a/caf\\303\\251 file.ts" "b/caf\\303\\251 file.ts"\n+unicode-only\n`;
    expect(historyForPath(raw,'café file.ts')).toContain('+unicode-only');
  });

  test('withholds every diff section touching an excluded path, keeping commit preambles and other files',()=>{
    const raw=`commit ${'c'.repeat(40)}
Subject: add config
diff --git a/.env b/.env
--- /dev/null
+++ b/.env
+PLANTED_VALUE=excluded-file-body
diff --git a/app.ts b/app.ts
--- a/app.ts
+++ b/app.ts
+kept-line
commit ${'d'.repeat(40)}
Subject: rename
diff --git a/config/old.txt b/config/master.key
rename from config/old.txt
rename to config/master.key
+renamed-into-excluded
diff --git "a/foo b/foo
+unparseable-body
`;
    const kept=withholdHistory(raw,(path)=>/(^|\/)(\.env|master\.key)$/.test(path)?'credential or execution configuration':undefined);
    expect(kept).not.toContain('excluded-file-body');
    expect(kept).not.toContain('renamed-into-excluded');
    expect(kept).not.toContain('unparseable-body');
    expect(kept).toContain('diff withheld: .env (credential or execution configuration)');
    expect(kept).toContain('diff withheld: config/master.key (credential or execution configuration)');
    expect(kept).toContain('diff withheld: unknown path (unparseable diff header)');
    expect(kept).toContain('+kept-line');
    expect(kept).toContain('Subject: add config');
    expect(kept).toContain('Subject: rename');
  });

  test('withholds every symlink diff section, whose body is link text, whatever the path',()=>{
    const raw=`commit ${'e'.repeat(40)}
Subject: add links
diff --git a/AGENTS.md b/AGENTS.md
new file mode 120000
index 0000000..1111111
--- /dev/null
+++ b/AGENTS.md
@@ -0,0 +1 @@
+NEW-LINK-TEXT
\\ No newline at end of file
diff --git a/app.ts b/app.ts
index 2222222..3333333 100644
--- a/app.ts
+++ b/app.ts
@@ -1 +1 @@
+kept-line
commit ${'f'.repeat(40)}
Subject: retarget, typechange and removal
diff --git a/docs/guide.md b/docs/guide.md
index 4444444..5555555 120000
--- a/docs/guide.md
+++ b/docs/guide.md
@@ -1 +1 @@
-OLD-LINK-TEXT
+RETARGETED-LINK-TEXT
diff --git a/tool.md b/tool.md
old mode 100644
new mode 120000
--- a/tool.md
+++ b/tool.md
@@ -1 +1 @@
+TYPECHANGE-LINK-TEXT
diff --git a/gone.md b/gone.md
deleted file mode 120000
index 6666666..0000000
--- a/gone.md
+++ /dev/null
@@ -1 +0,0 @@
-DELETED-LINK-TEXT
`;
    const kept=withholdHistory(raw,()=>undefined);
    for(const text of ['NEW-LINK-TEXT','OLD-LINK-TEXT','RETARGETED-LINK-TEXT','TYPECHANGE-LINK-TEXT','DELETED-LINK-TEXT'])
      expect(kept).not.toContain(text);
    for(const path of ['AGENTS.md','docs/guide.md','tool.md','gone.md'])
      expect(kept).toContain(`diff withheld: ${path} (symlink)`);
    expect(kept).toContain('+kept-line');
    expect(kept).toContain('Subject: add links');
    expect(kept).toContain('Subject: retarget, typechange and removal');
  });

  test('rejects malformed quoted headers instead of broadening the match',()=>{
    expect(gitDiffHeaderPaths('diff --git "a/foo b/foo')).toBeUndefined();
    expect(historyForPath('diff --git "a/foo b/foo\n+secret\n','foo')).toBeUndefined();
  });
});
