"""Read-only publication audit. Python 3.9+, Git, Gitleaks 8.19+.

Only temporary snapshots/reports are written. Exit: 0 pass, 1 blocked/review,
2 incomplete. A pass is NOT authorization, identity verification, or a visual QA.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_DEPTH = 3
MAX_COMMITS = 1000
IMAGES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.ico', '.svg', '.avif'}
UNSUPPORTED_ARCHIVES = {'.7z', '.rar', '.bz2', '.xz', '.zst', '.iso'}
PROCESS_DIRS = {'.superpowers', '__pycache__', '.pytest_cache', '.mypy_cache',
                '.ruff_cache', 'node_modules', '.venv', 'venv', '.github-publish-work'}
PROCESS_NAMES = {'implementation_plan.md', 'task_plan.md', 'progress.md',
                 'walkthrough.md', 'session.jsonl', 'history.jsonl', 'transcript.jsonl',
                 'conversation.jsonl', '.ds_store', 'thumbs.db'}
AMBIGUOUS_NAMES = {'agents.md', 'claude.md', 'gemini.md', 'tasks.md', 'todo.md',
                   'plan.md', 'notes.md'}
EMAIL = re.compile(r'[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})', re.I)
PRIVATE_KEY = re.compile(r'^-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----', re.M)
TOKEN = re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{50,255}|sk-[A-Za-z0-9_-]{32,})\b')
LOCAL_PATH = re.compile(r'(?:[A-Z]:[\\/](?:Users|home)[\\/][^\s<>"\']+|/(?:Users|home)/[^\s<>"\']+)', re.I)
PHONE = re.compile(r'(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)')
REVIEWABLE = {'personal-data-review', 'local-path-review', 'ambiguous-process-file',
              'agent-config-review', 'image-review-required', 'binary-review-required',
              'conversation-review'}


class AuditError(Exception):
    """Fixed rule identifiers only; never raw subprocess output or file content."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_display(value):
    # Paths may themselves contain a credential or a private email.
    value = TOKEN.sub('[redacted-token]', str(value))
    value = EMAIL.sub('[redacted-email]', value)
    value = PHONE.sub('[redacted-phone]', value)
    return ''.join(c if c >= ' ' and c != '\x7f' else '?' for c in value)


def valid_relative(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        return False
    p = PurePosixPath(name)
    return not p.is_absolute() and all(x not in ('', '.', '..') for x in name.split('/')) and ':' not in name


def git(root, *args, allowed=(0,)):
    try:
        env = dict(os.environ)
        env['GIT_NO_REPLACE_OBJECTS'] = '1'
        r = subprocess.run(['git', '-C', str(root), *args], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise AuditError('git-unavailable')
    if r.returncode not in allowed:
        raise AuditError('git-read-failed')
    return r


def decode(data):
    try:
        if data.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
            return data.decode('utf-32')
        if data.startswith((b'\xff\xfe', b'\xfe\xff')):
            return data.decode('utf-16')
        if b'\0' in data:
            return None
        return data.decode('utf-8-sig')
    except UnicodeError:
        return None


def printable_text(data):
    """Expose text metadata without binary MIME auto-skipping by scanners."""
    return b'\n'.join(re.findall(rb'[\x20-\x7e]{4,}', data)).decode('ascii')


def path_rule(path):
    parts = PurePosixPath(path.lower()).parts
    name = parts[-1]
    if any(x in PROCESS_DIRS or x == '.git' for x in parts):
        return 'process-artifact', 'block'
    if 'docs/superpowers/' in path.lower() or 'docs/plans/' in path.lower():
        return 'process-artifact', 'block'
    if name in PROCESS_NAMES or name.endswith(('.log', '.bak', '.swp', '.tmp')):
        return 'process-artifact', 'block'
    if any(x in ('sessions', 'conversations', 'transcripts', 'chat-history') for x in parts):
        return 'conversation-review', 'review'
    example = name.endswith(('.example', '.sample', '.template'))
    if (name == '.env' or name.startswith('.env.')) and not example:
        return 'environment-file', 'block'
    if name in {'id_rsa', 'id_ed25519', 'id_ecdsa', '.npmrc', '.pypirc', '.netrc',
                'credentials.json', 'credentials', 'auth.json'} or name.endswith(('.p12', '.pfx', '.key')):
        return 'credential-file', 'block'
    if name in AMBIGUOUS_NAMES:
        return 'ambiguous-process-file', 'review'
    if any(x in ('.codex', '.claude', '.cursor', '.agents') for x in parts):
        if name.endswith('.jsonl'):
            return 'process-artifact', 'block'
        return 'agent-config-review', 'review'
    return None


class Auditor:
    def __init__(self, root, scope, manifest, base, head, scanner, review):
        self.root = Path(root).resolve()
        self.scope, self.manifest = scope, manifest
        self.base, self.head, self.scanner = base, head, scanner
        self.review = {'reviews': []} if review is None else review
        self.findings, self.inventory, self.scans = [], [], []
        self.total = 0
        self.refs = {}
        self.seen = set()
        self.reviewed = []
        self.redacted_paths = set()
        self.scan_path_entries = set()

    def finding(self, rule, severity, path='', digest='', revision='', line=None):
        item = {'rule': rule, 'severity': severity, 'path': safe_display(path)}
        if digest:
            item['sha256'] = digest
        if revision:
            item['revision'] = revision
        if line is not None:
            item['line'] = line
        if severity == 'review' and rule in REVIEWABLE:
            for entry in self.review.get('reviews', []):
                if (entry.get('path') == path and entry.get('sha256') == digest
                        and rule in entry.get('rules', []) and entry.get('reason', '').strip()):
                    self.reviewed.append(item)
                    return
        self.findings.append(item)

    def gather_files(self):
        if self.manifest is None:
            names = []
            def walk_error(_):
                self.finding('directory-unreadable', 'incomplete')
            for parent, dirs, files in os.walk(self.root, followlinks=False, onerror=walk_error):
                for directory in list(dirs):
                    p = Path(parent) / directory
                    if directory == '.git':
                        dirs.remove(directory)
                    elif p.is_symlink() or getattr(p.lstat(), 'st_file_attributes', 0) & 0x400:
                        self.finding('symlink-not-inspected', 'incomplete', p.relative_to(self.root).as_posix())
                        dirs.remove(directory)
                names.extend((Path(parent) / n).relative_to(self.root).as_posix() for n in files if n != '.git')
        else:
            if not isinstance(self.manifest, list) or not all(isinstance(n, str) for n in self.manifest):
                raise AuditError('invalid-manifest')
            names = self.manifest
        for name in sorted(set(names)):
            if not valid_relative(name):
                self.finding('unsafe-path', 'incomplete')
                continue
            p = self.root / name
            try:
                if not p.resolve().is_relative_to(self.root):
                    raise AuditError('unsafe-path')
                if any(x.is_symlink() or (x.exists() and getattr(x.lstat(), 'st_file_attributes', 0) & 0x400)
                       for x in [p, *list(p.parents)[:len(PurePosixPath(name).parts) - 1]]):
                    raise AuditError('symlink-not-inspected')
                if not p.is_file():
                    raise AuditError('file-unavailable')
                if p.stat().st_size > MAX_FILE:
                    raise AuditError('file-too-large')
                yield name, p.read_bytes(), ''
            except AuditError as e:
                self.finding(str(e), 'incomplete', name)
            except OSError:
                self.finding('file-unavailable', 'incomplete', name)

    def read_blob(self, mode, oid, name, revision):
        if mode not in ('100644', '100755'):
            self.finding('git-link-not-inspected', 'incomplete', name, revision=revision)
            return None
        if int(git(self.root, 'cat-file', '-s', oid).stdout) > MAX_FILE:
            self.finding('file-too-large', 'incomplete', name, revision=revision)
            return None
        return name, git(self.root, 'cat-file', 'blob', oid).stdout, revision

    def gather_git(self):
        top = Path(git(self.root, 'rev-parse', '--show-toplevel').stdout.decode().strip()).resolve()
        if top != self.root:
            raise AuditError('root-must-be-repository-root')
        if git(self.root, 'rev-parse', '--is-shallow-repository').stdout.strip() == b'true':
            raise AuditError('shallow-history')
        graft = git(self.root, 'rev-parse', '--git-path', 'info/grafts').stdout.decode().strip()
        graft_path = Path(graft) if Path(graft).is_absolute() else self.root / graft
        if graft_path.exists() and graft_path.stat().st_size:
            raise AuditError('grafted-history')
        if self.scope == 'staged':
            rows = git(self.root, 'ls-files', '--stage', '-z').stdout.split(b'\0')
            for row in rows:
                if not row:
                    continue
                meta, name_raw = row.split(b'\t', 1)
                mode, oid, stage = meta.decode('ascii').split()
                name = name_raw.decode('utf-8')
                if stage != '0':
                    raise AuditError('unmerged-index')
                entry = self.read_blob(mode, oid, name, 'INDEX')
                if entry:
                    yield entry
            return
        if not self.base:
            raise AuditError('explicit-base-required')
        head = git(self.root, 'rev-parse', '--verify', '--end-of-options', str(self.head) + '^{commit}').stdout.decode().strip()
        self.refs['head'] = head
        if self.base == 'EMPTY':
            revisions = git(self.root, 'rev-list', head).stdout.decode().splitlines()
            self.refs['base'] = 'EMPTY'
        else:
            base = git(self.root, 'rev-parse', '--verify', '--end-of-options', str(self.base) + '^{commit}').stdout.decode().strip()
            self.refs['base'] = base
            if git(self.root, 'merge-base', '--is-ancestor', base, head, allowed=(0, 1)).returncode:
                raise AuditError('divergent-range')
            revisions = git(self.root, 'rev-list', base + '..' + head).stdout.decode().splitlines()
        if len(revisions) > MAX_COMMITS:
            raise AuditError('commit-limit-exceeded')
        # Include final tree even on an empty range; also inspect every intermediate
        # tree, including merge parents. Dedupe exact path+blob only, never net diff.
        for rev in dict.fromkeys([head, *revisions]):
            rows = git(self.root, 'ls-tree', '-r', '-z', rev).stdout.split(b'\0')
            for row in rows:
                if not row:
                    continue
                meta, name_raw = row.split(b'\t', 1)
                mode, _, oid = meta.decode('ascii').split()
                name = name_raw.decode('utf-8')
                if (name, oid) in self.seen:
                    continue
                self.seen.add((name, oid))
                entry = self.read_blob(mode, oid, name, rev)
                if entry:
                    yield entry
            # Commit messages can leak secrets too. Do not print author email/message.
            message = git(self.root, 'show', '-s', '--format=%B', rev).stdout
            self.scans.append(('[commit-message]', message, rev, sha(message)))
            text = decode(message)
            if text is None:
                self.finding('commit-message-unreadable', 'incomplete', '[commit-message]', revision=rev)
            else:
                self.inspect_text('[commit-message]', text, sha(message), rev)

    def inspect_archive(self, name, data, revision, depth):
        if depth >= MAX_DEPTH:
            self.finding('archive-depth-exceeded', 'incomplete', name, revision=revision)
            return
        try:
            if zipfile.is_zipfile(io.BytesIO(data)):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    members = archive.infolist()
                    if len(members) > MAX_ARCHIVE_ENTRIES:
                        raise AuditError('archive-limit-exceeded')
                    for member in members:
                        child = name + '!' + member.filename
                        if not valid_relative(member.filename.rstrip('/')) or stat.S_ISLNK(member.external_attr >> 16):
                            self.finding('unsafe-archive-member', 'incomplete', child)
                            continue
                        if member.is_dir():
                            continue
                        if member.flag_bits & 1:
                            self.finding('encrypted-archive-member', 'incomplete', child)
                        elif member.file_size > MAX_FILE or self.total + member.file_size > MAX_TOTAL:
                            self.finding('archive-limit-exceeded', 'incomplete', child)
                        else:
                            self.inspect(child, archive.read(member), revision, depth + 1,
                                         classification_path=member.filename)
                return
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
                for i, member in enumerate(archive):
                    if i >= MAX_ARCHIVE_ENTRIES:
                        raise AuditError('archive-limit-exceeded')
                    child = name + '!' + member.name
                    if not valid_relative(member.name.rstrip('/')) or not (member.isfile() or member.isdir()):
                        self.finding('unsafe-archive-member', 'incomplete', child)
                    elif member.isdir():
                        continue
                    elif member.size > MAX_FILE or self.total + member.size > MAX_TOTAL:
                        self.finding('archive-limit-exceeded', 'incomplete', child)
                    else:
                        self.inspect(child, archive.extractfile(member).read(), revision, depth + 1,
                                     classification_path=member.name)
        except tarfile.ReadError:
            if data.startswith(b'\x1f\x8b'):
                try:
                    with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                        unpacked = compressed.read(MAX_FILE + 1)
                    if len(unpacked) > MAX_FILE:
                        raise AuditError('archive-limit-exceeded')
                    self.inspect(name + '!payload', unpacked, revision, depth + 1)
                except (OSError, EOFError, AuditError):
                    self.finding('archive-unreadable', 'incomplete', name)
            else:
                self.finding('archive-unreadable', 'incomplete', name)
        except (OSError, ValueError, EOFError, RuntimeError, zipfile.BadZipFile, AuditError):
            self.finding('archive-unreadable', 'incomplete', name)

    def inspect(self, name, data, revision='', depth=0, classification_path=None):
        self.total += len(data)
        if len(data) > MAX_FILE or self.total > MAX_TOTAL:
            self.finding('scan-limit-exceeded', 'incomplete', name)
            return
        digest = sha(data)
        self.inventory.append({'path': safe_display(name), 'sha256': digest,
                               'path_sha256': sha(name.encode('utf-8')),
                               'bytes': len(data), 'revision': revision})
        self.inspect_text(name, name, digest, revision)
        self.scan_path_entries.add(len(self.scans))
        self.scans.append((name, name.encode('utf-8'), revision, digest))
        classified = classification_path or name
        rule = path_rule(classified)
        if rule:
            self.finding(rule[0], rule[1], name, digest, revision)
        suffix = PurePosixPath(classified.lower()).suffix
        is_archive = (suffix in {'.zip', '.tar', '.tgz', '.gz', '.whl', '.jar'}
                      or data.startswith((b'PK\x03\x04', b'PK\x05\x06', b'\x1f\x8b'))
                      or data[257:262] == b'ustar')
        if is_archive:
            # Containers may contain comments/extra fields/trailing bytes. Scan
            # them as well as decompressed contents, never members alone.
            self.scans.append((name, data, revision, digest))
            metadata = printable_text(data)
            self.scans.append((name, metadata.encode('utf-8'), revision, digest))
            self.inspect_text(name, metadata, digest, revision)
            self.inspect_archive(name, data, revision, depth)
            return
        if suffix in UNSUPPORTED_ARCHIVES or data.startswith((b'7z\xbc\xaf\x27\x1c', b'Rar!')):
            self.finding('unsupported-archive', 'incomplete', name, digest, revision)
            return
        # Gitleaks sees every inspected blob, using original bytes, independent of
        # repository ignore/config files. Text transcodes cover UTF-16/32 too.
        content = decode(data)
        scan_data = content.encode('utf-8') if content is not None else data
        self.scans.append((name, scan_data, revision, digest))
        if suffix in IMAGES or data.startswith((b'\x89PNG', b'\xff\xd8\xff', b'GIF8')):
            self.finding('image-review-required', 'review', name, digest, revision)
            # Raw bytes are still scanned, but metadata/visible text need external
            # tools and viewing; no false claim of automatic image privacy checking.
            return
        if content is None:
            self.finding('binary-review-required', 'review', name, digest, revision)
            return
        if content.startswith('version https://git-lfs.github.com/spec/v1'):
            self.finding('lfs-object-not-inspected', 'incomplete', name, digest, revision)
        self.inspect_text(name, content, digest, revision)

    def inspect_text(self, name, content, digest, revision):
        for pattern, rule_id, severity in [(PRIVATE_KEY, 'secret', 'block'), (TOKEN, 'secret', 'block'),
                                           (LOCAL_PATH, 'local-path-review', 'review'),
                                           (PHONE, 'personal-data-review', 'review')]:
            for match in pattern.finditer(content):
                self.finding(rule_id, severity, name, digest, revision, content.count('\n', 0, match.start()) + 1)
        for match in EMAIL.finditer(content):
            domain = match.group(1).lower()
            if domain not in {'example.com', 'example.org', 'example.net', 'users.noreply.github.com'}:
                self.finding('personal-data-review', 'review', name, digest, revision,
                             content.count('\n', 0, match.start()) + 1)
        # Structured role/message logs are ambiguous: training examples may be product.
        if re.search(r'"role"\s*:\s*"(?:user|assistant)"', content) and re.search(r'"(?:content|message)"\s*:', content):
            self.finding('conversation-review', 'review', name, digest, revision)

    def scan_secrets(self):
        binary = shutil.which(str(self.scanner))
        if not binary:
            self.finding('scanner-unavailable', 'incomplete')
            return
        try:
            with tempfile.TemporaryDirectory(prefix='github-publish-audit-') as tmp:
                work = Path(tmp)
                snapshots = work / 'snapshots'
                snapshots.mkdir()
                mapping = {}
                for i, entry in enumerate(self.scans):
                    filename = '{:08d}.txt'.format(i)
                    (snapshots / filename).write_bytes(entry[1])
                    mapping[filename] = (entry, i in self.scan_path_entries)
                config = work / 'rules.toml'
                config.write_text('[extend]\nuseDefault = true\n', encoding='utf-8')
                output = work / 'findings.json'
                env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GITLEAKS_')}
                r = subprocess.run([binary, 'dir', str(snapshots), '--config', str(config),
                                    '--redact=100', '--no-banner', '--no-color', '--ignore-gitleaks-allow',
                                    '--report-format', 'json', '--report-path', str(output)],
                                   cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
                if r.returncode not in (0, 1) or not output.is_file():
                    raise AuditError('scanner-failed')
                found = json.loads(output.read_text(encoding='utf-8-sig'))
                if not isinstance(found, list) or (r.returncode == 1 and not found):
                    raise AuditError('scanner-invalid-report')
                for finding in found:
                    filename = str(finding['File']).replace('\\', '/').rsplit('/', 1)[-1]
                    if filename not in mapping:
                        raise AuditError('scanner-invalid-report')
                    entry, is_path = mapping[filename]
                    name, _, revision, digest = entry
                    if is_path:
                        self.redacted_paths.add(safe_display(name))
                    self.finding('secret', 'block', name, digest, revision, int(finding['StartLine']))
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired, AuditError):
            self.finding('scanner-failed', 'incomplete')

    def run(self):
        try:
            if not self.root.is_dir():
                raise AuditError('root-unavailable')
            if self.scope not in {'files', 'staged', 'commits'}:
                raise AuditError('invalid-scope')
            if self.scope != 'files' and self.manifest is not None:
                raise AuditError('manifest-only-for-files')
            if not isinstance(self.review, dict) or not isinstance(self.review.get('reviews'), list):
                raise AuditError('invalid-review')
            for item in self.review['reviews']:
                if (not isinstance(item, dict) or not isinstance(item.get('path'), str)
                        or not isinstance(item.get('sha256'), str)
                        or not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', ''))
                        or not isinstance(item.get('rules'), list)
                        or not all(isinstance(r, str) for r in item.get('rules', []))
                        or not isinstance(item.get('reason'), str)):
                    raise AuditError('invalid-review')
            entries = self.gather_files() if self.scope == 'files' else self.gather_git()
            for name, data, revision in entries:
                self.inspect(name, data, revision)
            if not self.inventory:
                self.finding('empty-snapshot', 'incomplete')
        except AuditError as e:
            self.finding(str(e), 'incomplete')
        except (UnicodeError, OSError, ValueError, TypeError):
            self.finding('input-incomplete', 'incomplete')
        self.scan_secrets()
        for collection in (self.findings, self.inventory, self.reviewed):
            for item in collection:
                if item['path'] in self.redacted_paths:
                    item['path'] = '[redacted-path:' + sha(item['path'].encode())[:12] + ']'
        levels = {x['severity'] for x in self.findings}
        status = ('incomplete' if 'incomplete' in levels else 'blocked' if 'block' in levels
                  else 'review_required' if 'review' in levels else 'pass')
        canonical = json.dumps({'inventory': self.inventory, 'refs': self.refs}, sort_keys=True).encode('utf-8')
        return {'version': 1, 'scope': self.scope, 'status': status,
                'fingerprint': sha(canonical), 'refs': self.refs,
                'inventory': self.inventory, 'findings': self.findings, 'reviewed': self.reviewed,
                'limits': {'file_bytes': MAX_FILE, 'total_bytes': MAX_TOTAL, 'archive_depth': MAX_DEPTH},
                'notice': 'Automated content checks only; identity, publication scope, and presentation must also be verified.'}


def audit(root, scope='files', manifest=None, base=None, head='HEAD', gitleaks='gitleaks', review=None):
    return Auditor(root, scope, manifest, base, head, gitleaks, review).run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', choices=['files', 'staged', 'commits'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--manifest', help='UTF-8 JSON array of exact relative file paths; files scope only')
    parser.add_argument('--review', help='Content-hash-bound manual review records, stored outside project')
    parser.add_argument('--base', help='Fetched remote commit SHA; EMPTY only for a new remote branch')
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--gitleaks', default='gitleaks', help='Executable name or trusted absolute path')
    parser.add_argument('--expect-fingerprint', help='Fail if audited content/refs changed since prior run')
    args = parser.parse_args()
    try:
        def read_json(path):
            return json.loads(Path(path).read_text(encoding='utf-8-sig')) if path else None
        result = audit(args.root, args.scope, read_json(args.manifest), args.base, args.head,
                       args.gitleaks, read_json(args.review))
        if args.expect_fingerprint and args.expect_fingerprint != result['fingerprint']:
            result['findings'].append({'rule': 'snapshot-changed', 'severity': 'incomplete', 'path': ''})
            result['status'] = 'incomplete'
    except (OSError, ValueError):
        result = {'version': 1, 'status': 'incomplete', 'findings': [{'rule': 'invalid-input', 'severity': 'incomplete', 'path': ''}]}
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'pass' else 2 if result['status'] == 'incomplete' else 1


if __name__ == '__main__':
    sys.exit(main())
