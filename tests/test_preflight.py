"""Behavior tests. Fixtures are synthetic; no live credentials or network writes."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'preflight.py'
SCANNER = os.environ.get('GITHUB_PUBLISH_TEST_GITLEAKS', 'gitleaks')


def load_auditor():
    if not SCRIPT.exists():
        return None
    spec = importlib.util.spec_from_file_location('preflight', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='github-publish-test-')
        self.root = Path(self.tmp.name) / '中文 项目'
        self.root.mkdir()
        self.mod = load_auditor()
        self.assertIsNotNone(self.mod, 'The required preflight auditor is not implemented')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
        return p

    def audit(self, **kw):
        return self.mod.audit(self.root, gitleaks=SCANNER, **kw)

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, check=True)
        return result.stdout.decode('utf-8').strip()

    def init(self):
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Synthetic Fixture')
        self.git('config', 'user.email', 'fixture@example.com')
        self.git('config', 'commit.gpgsign', 'false')

    def commit(self):
        self.git('add', '--all')
        self.git('commit', '-m', 'Synthetic test fixture')
        return self.git('rev-parse', 'HEAD')

    def rules(self, report):
        return {f['rule'] for f in report['findings']}

    def test_safe_product_and_skill_files_are_retained_without_mutation(self):
        for name in ['README.md', 'docs/user-guide.md', 'SKILL.md', 'agents/openai.yaml',
                     'references/api.md', 'scripts/run.py']:
            self.write(name, '# Public product documentation\n')
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report = self.audit()
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(set(before), {x['path'] for x in report['inventory']})
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_process_material_and_real_env_are_blocked_but_template_allowed(self):
        self.write('docs/superpowers/plans/build.md', '# Implementation plan')
        self.write('.codex/session.jsonl', '{}')
        self.write('.env', 'PORT=8080')
        self.write('.env.example', 'API_KEY=YOUR_API_KEY')
        report = self.audit()
        self.assertEqual(report['status'], 'blocked')
        blocked = {f['path'] for f in report['findings'] if f['severity'] == 'block'}
        self.assertIn('docs/superpowers/plans/build.md', blocked)
        self.assertIn('.codex/session.jsonl', blocked)
        self.assertIn('.env', blocked)
        self.assertNotIn('.env.example', blocked)

    def test_staged_reads_index_not_clean_worktree_or_gitignore(self):
        self.init()
        self.write('docs/superpowers/specs/release.md', 'Internal planning')
        self.write('main.py', 'print(1)')
        self.git('add', '--all')
        self.write('.gitignore', 'docs/superpowers/\n')
        (self.root / 'docs/superpowers/specs/release.md').unlink()
        report = self.audit(scope='staged')
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('docs/superpowers/specs/release.md', {f['path'] for f in report['findings']})

    def test_deleted_historical_secret_and_plan_still_block_push(self):
        self.init()
        self.write('main.py', 'print(1)')
        base = self.commit()
        # Synthetic provider-shaped string, built rather than stored as a real credential.
        token = 'ghp_' + 'Ab3dEf7hIj9Lm2pQr5sTu8vWx1yZ4aBc6dEf0gHi'
        self.write('settings.txt', 'GITHUB_TOKEN=' + token)
        self.write('docs/superpowers/plans/temp.md', 'Private plan')
        self.commit()
        (self.root / 'settings.txt').unlink()
        (self.root / 'docs/superpowers/plans/temp.md').unlink()
        self.commit()
        report = self.audit(scope='commits', base=base, head='HEAD')
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('secret', self.rules(report))
        self.assertIn('process-artifact', self.rules(report))
        self.assertNotIn(token, json.dumps(report))

    def test_zip_content_scanned_without_extracting_into_project(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.writestr('docs/superpowers/plans/old.md', 'Internal plan')
        self.write('source.zip', stream.getvalue())
        report = self.audit()
        self.assertEqual(report['status'], 'blocked')
        self.assertTrue(any('source.zip!' in f['path'] for f in report['findings']))
        self.assertFalse((self.root / 'docs').exists())

    def test_zip_traversal_is_incomplete(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.writestr('../outside.txt', 'hello')
        self.write('source.zip', stream.getvalue())
        report = self.audit()
        self.assertEqual(report['status'], 'incomplete')
        self.assertIn('unsafe-archive-member', self.rules(report))

    def test_unknown_archive_cannot_pass(self):
        self.write('data.7z', b'not a supported archive')
        report = self.audit()
        self.assertEqual(report['status'], 'incomplete')

    def test_image_needs_visual_review_and_stale_hash_does_not_pass(self):
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (5, 5), 'white').save(stream, format='PNG')
        data = stream.getvalue()
        self.write('assets/demo.png', data)
        report = self.audit()
        self.assertIn('image-review-required', self.rules(report))
        self.assertEqual(report['status'], 'review_required')
        review = {'reviews': [{'path': 'assets/demo.png', 'sha256': hashlib.sha256(data).hexdigest(),
                              'rules': ['image-review-required'], 'reason': 'Viewed synthetic blank image; metadata checked'}]}
        self.assertEqual(self.audit(review=review)['status'], 'pass')
        self.write('assets/demo.png', data + b'changed')
        self.assertNotEqual(self.audit(review=review)['status'], 'pass')

    def test_missing_scanner_is_incomplete_and_never_pass(self):
        self.write('main.py', 'print(1)')
        report = self.mod.audit(self.root, gitleaks=str(self.root / 'absent-gitleaks'))
        self.assertEqual(report['status'], 'incomplete')
        self.assertIn('scanner-unavailable', self.rules(report))

    def test_manifest_selects_exact_files_and_cannot_escape_root(self):
        self.write('main.py', 'print(1)')
        self.write('.env', 'SECRET=private')
        self.assertEqual(self.audit(manifest=['main.py'])['status'], 'pass')
        self.assertEqual(self.audit(manifest=['../outside.txt'])['status'], 'incomplete')

    def test_fingerprint_changes_with_content(self):
        self.write('main.py', 'print(1)')
        a = self.audit()['fingerprint']
        self.write('main.py', 'print(2)')
        self.assertNotEqual(a, self.audit()['fingerprint'])

    def test_synthetic_personal_email_is_reported_without_value(self):
        private = 'someone' + '@private-mail.invalid'
        self.write('README.md', 'Contact: ' + private)
        report = self.audit()
        self.assertEqual(report['status'], 'review_required')
        self.assertIn('personal-data-review', self.rules(report))
        self.assertNotIn(private, json.dumps(report))

    def test_divergent_commit_range_is_incomplete(self):
        self.init()
        self.write('main.py', 'print(1)')
        self.commit()
        self.git('checkout', '-b', 'side')
        self.write('side.py', 'print(2)')
        base = self.commit()
        self.git('checkout', 'main')
        self.write('other.py', 'print(3)')
        self.commit()
        report = self.audit(scope='commits', base=base)
        self.assertEqual(report['status'], 'incomplete')

    def test_first_publish_scans_entire_reachable_history(self):
        self.init()
        self.write('docs/superpowers/plans/a.md', 'private plan')
        self.commit()
        (self.root / 'docs/superpowers/plans/a.md').unlink()
        self.write('main.py', 'print(1)')
        self.commit()
        self.assertEqual(self.audit(scope='commits', base='EMPTY')['status'], 'blocked')

    def test_empty_publish_is_not_a_success(self):
        self.assertEqual(self.audit()['status'], 'incomplete')

    def test_shallow_history_never_claims_complete_scan(self):
        self.init()
        self.write('main.py', 'print(1)')
        self.commit()
        self.write('main.py', 'print(2)')
        latest = self.commit()
        # Reproduce a shallow boundary without network access.
        self.write('.git/shallow', latest + '\n')
        self.assertEqual(self.audit(scope='commits', base='EMPTY')['status'], 'incomplete')

    def test_archive_detected_by_tar_magic_even_when_renamed(self):
        import tarfile
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            data = b'private plan'
            member = tarfile.TarInfo('docs/superpowers/plans/leak.md')
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        self.write('blob.dat', stream.getvalue())
        report = self.audit()
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('process-artifact', self.rules(report))

    def test_secret_cannot_be_waived_by_manual_review_or_inline_allow(self):
        token = 'ghp_' + 'Ab3dEf7hIj9Lm2pQr5sTu8vWx1yZ4aBc6dEf0gHi'
        data = ('token=' + token + ' # gitleaks:allow').encode()
        self.write('config.txt', data)
        review = {'reviews': [{'path': 'config.txt', 'sha256': hashlib.sha256(data).hexdigest(),
                              'rules': ['secret'], 'reason': 'Please ignore'}]}
        self.assertEqual(self.audit(review=review)['status'], 'blocked')

    def test_repo_scanner_config_does_not_suppress_detected_secret(self):
        self.write('.gitleaks.toml', '[allowlist]\npaths = [".*"]\n')
        token = 'ghp_' + 'Ab3dEf7hIj9Lm2pQr5sTu8vWx1yZ4aBc6dEf0gHi'
        self.write('config.txt', 'token=' + token)
        self.assertEqual(self.audit()['status'], 'blocked')

    def test_utf16_secret_is_scanned_and_never_printed(self):
        token = 'ghp_' + 'Ab3dEf7hIj9Lm2pQr5sTu8vWx1yZ4aBc6dEf0gHi'
        self.write('config.txt', ('token=' + token).encode('utf-16'))
        report = self.audit()
        self.assertEqual(report['status'], 'blocked')
        self.assertNotIn(token, json.dumps(report))

    def test_cli_fingerprint_detects_change_and_returns_nonzero(self):
        self.write('main.py', 'print(1)')
        prior = self.audit()['fingerprint']
        self.write('main.py', 'print(2)')
        r = subprocess.run([os.sys.executable, str(SCRIPT), 'files', '--root', str(self.root),
                            '--gitleaks', SCANNER, '--expect-fingerprint', prior], capture_output=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn('snapshot-changed', self.rules(json.loads(r.stdout)))

    def test_commit_message_secret_is_detected_without_printing(self):
        self.init()
        self.write('main.py', 'print(1)')
        self.git('add', '--all')
        token = 'ghp_' + 'Ab3dEf7hIj9Lm2pQr5sTu8vWx1yZ4aBc6dEf0gHi'
        self.git('commit', '-m', 'token=' + token)
        report = self.audit(scope='commits', base='EMPTY')
        self.assertEqual(report['status'], 'blocked')
        self.assertNotIn(token, json.dumps(report))

    def test_git_replace_does_not_hide_actual_push_objects(self):
        self.init()
        self.write('docs/plans/private.md', 'Private plan')
        original = self.commit()
        self.git('checkout', '--orphan', 'clean')
        self.git('rm', '-rf', '.')
        self.write('README.md', 'Public content')
        replacement = self.commit()
        self.git('replace', original, replacement)
        report = self.audit(scope='commits', base='EMPTY', head=original)
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('process-artifact', self.rules(report))

    def test_zip_comment_secret_and_unsafe_directory_are_not_ignored(self):
        token = 'ghp_' + 'Ab3dEf7hIj9Lm2pQr5sTu8vWx1yZ4aBc6dEf0gHi'
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('README.md', 'Public')
            archive.comment = ('token=' + token).encode()
        self.write('source.zip', stream.getvalue())
        report = self.audit()
        self.assertEqual(report['status'], 'blocked')
        self.assertNotIn(token, json.dumps(report))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('../unsafe/', b'')
        self.write('source.zip', stream.getvalue())
        self.assertEqual(self.audit()['status'], 'incomplete')

    def test_path_secrets_block_and_fingerprint_uses_original_path(self):
        token = 'ghp_' + 'Ab3dEf7hIj9Lm2pQr5sTu8vWx1yZ4aBc6dEf0gHi'
        self.write(token + '.txt', 'hello')
        a = self.audit()
        self.assertEqual(a['status'], 'blocked')
        self.assertNotIn(token, json.dumps(a))
        (self.root / (token + '.txt')).rename(self.root / (token[:-1] + 'J.txt'))
        self.assertNotEqual(a['fingerprint'], self.audit()['fingerprint'])

    def test_phone_in_filename_is_redacted_and_requires_review(self):
        phone = '139' + '12345678'
        self.write(phone + '.txt', 'hello')
        report = self.audit()
        self.assertEqual(report['status'], 'review_required')
        self.assertNotIn(phone, json.dumps(report))

    def test_commit_message_privacy_requires_review(self):
        self.init()
        self.write('README.md', 'Public')
        self.git('add', '--all')
        private = 'someone' + '@private-mail.invalid'
        self.git('commit', '-m', 'Contact ' + private)
        report = self.audit(scope='commits', base='EMPTY')
        self.assertEqual(report['status'], 'review_required')
        self.assertNotIn(private, json.dumps(report))

    def test_malformed_review_is_incomplete_not_traceback(self):
        self.write('README.md', 'Public')
        report = self.audit(review={'reviews': [{'path': 'README.md', 'sha256': 42, 'rules': [], 'reason': 'x'}]})
        self.assertEqual(report['status'], 'incomplete')

    def test_lfs_pointer_is_not_treated_as_actual_asset(self):
        self.write('assets/model.bin', 'version https://git-lfs.github.com/spec/v1\noid sha256:' + 'a' * 64 + '\nsize 12345\n')
        self.assertEqual(self.audit()['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
