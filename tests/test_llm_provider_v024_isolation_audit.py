"""Independent acceptance tests for LLM_Provider's two findings and Mac-readiness corpus integrity.

No external connector and no network required.
"""
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import ecom
from engine import corpus_extractor
from engine import product_research_pipeline as pipe


@unittest.skipUnless((__import__("pathlib").Path(__file__).resolve().parents[1] / "reference_corpus").exists(), "Requires external corpus")
class LLM_ProviderLegacyIsolationAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ecom.ROOT
        cls.corpus = cls.root / 'reference_corpus'
        cls.manifest = json.loads((cls.corpus / '_EXTRACTION_MANIFEST.json').read_text(encoding='utf-8'))

    def test_01_142_source_hashes_present_and_verified(self):
        m = self.manifest
        self.assertEqual(len(m['extracted_files']), 142)
        self.assertEqual(len(m['source_sha256_by_original_path']), 142)
        self.assertEqual(len(set(m['extracted_files'])), 142)
        for original, expected_sha in m['source_sha256_by_original_path'].items():
            actual = self.corpus / m['archived_instruction_path_map'].get(original, original)
            self.assertTrue(actual.is_file(), original)
            self.assertEqual(hashlib.sha256(actual.read_bytes()).hexdigest(), expected_sha, original)

    def test_02_seven_original_instruction_files_archived(self):
        archive = self.manifest['archived_instruction_path_map']
        self.assertEqual(len(archive), 7)
        for src, dst in archive.items():
            self.assertFalse((self.corpus / src).exists(), src)
            self.assertTrue((self.corpus / dst).is_file(), dst)
            self.assertTrue(dst.endswith('.txt'))
            self.assertNotIn('/.claude/', '/' + dst)

    def test_03_no_active_claude_or_agents_in_corpus(self):
        for p in self.corpus.rglob('*'):
            if not p.is_file():
                continue
            self.assertNotIn(p.name, {'CLAUDE.md', 'CLAUDE.local.md', 'AGENTS.md'})
            self.assertNotIn('.claude', p.parts)

    def test_04_only_four_project_skills_and_two_specialists(self):
        skills = list((self.root / '.claude/skills').glob('*/SKILL.md'))
        self.assertEqual(len(skills), 4)
        agents = list((self.root / '.claude/agents').glob('*.md'))
        self.assertEqual(len(agents), 2)
        self.assertTrue((self.root / 'CLAUDE.md').exists())

    def test_05_handoff_audits_archived_not_in_root(self):
        for name in ('LLM_PROVIDER_HANDOFF_B2.md', 'LLM_PROVIDER_HANDOFF_B3_PRODUCT_FIRST.md',
                     'B2_RESCUE_AUDIT.md', 'B2_PRODUCT_FIRST_REAUDIT.md', 'B3_RESCUE_AUDIT.md'):
            self.assertFalse((self.root / name).exists())
            self.assertTrue((self.root / 'docs/archives_audits' / name).is_file())

    def test_06_healthcheck_uses_source_hashes_not_only_file_count(self):
        with tempfile.TemporaryDirectory(prefix='ecom_manifest_tamper_') as td:
            root = Path(td)
            shutil.copytree(self.corpus, root / 'reference_corpus')
            f = root / 'reference_corpus' / 'LIMITES_CONNUES_V024.md'
            f.write_text(f.read_text(encoding='utf-8') + '\nmodified\n', encoding='utf-8')
            got = pipe.healthcheck(root)
            self.assertEqual(got['status'], 'BLOCKED_HEALTHCHECK')
            self.assertTrue(any('CORPUS_SOURCE_CHANGED' in x for x in got['issues']))

    def test_07_healthcheck_detects_legacy_instruction_reactivated(self):
        with tempfile.TemporaryDirectory(prefix='ecom_legacy_reactivation_') as td:
            root = Path(td)
            shutil.copytree(self.corpus, root / 'reference_corpus')
            p = root / 'reference_corpus/CLAUDE.md'
            p.write_text('legacy rules', encoding='utf-8')
            got = pipe.healthcheck(root)
            self.assertEqual(got['status'], 'BLOCKED_HEALTHCHECK')
            self.assertTrue(any('LEGACY_INSTRUCTION_ACTIVATED' in x for x in got['issues']))

    def test_08_mac_finder_ds_store_is_not_counted_as_corpus_source(self):
        with tempfile.TemporaryDirectory(prefix='ecom_dsstore_') as td:
            root = Path(td)
            shutil.copytree(self.corpus, root / 'reference_corpus')
            (root / 'reference_corpus/.DS_Store').write_bytes(b'Finder metadata')
            # The test project is intentionally not complete. It must still see exactly 142 sources.
            got = pipe.healthcheck(root)
            self.assertEqual(got['source_files'], 142)
            self.assertFalse(any('REFERENCE_FILES_COUNT' in x for x in got['issues']))

    def test_09_extractor_never_resurrects_old_router_paths(self):
        with tempfile.TemporaryDirectory(prefix='ecom_reextract_') as td:
            root = Path(td)
            source = root / corpus_extractor.V4_FILENAMES[0]
            source.write_text(
                '==================== FILE: CLAUDE.md ====================\nold system rules\n'
                '==================== FILE: .claude/skills/ecom-os/SKILL.md ====================\nold agent skill\n',
                encoding='utf-8')
            result = corpus_extractor.extract_v4_files([root], root / 'corpus')
            self.assertEqual(result['extracted_count'], 2)
            self.assertFalse((root / 'corpus/CLAUDE.md').exists())
            self.assertFalse((root / 'corpus/.claude').exists())
            self.assertEqual((root / 'corpus/_LEGACY_INSTRUCTION_ARCHIVE/root_CLAUDE_V024.txt').read_text().strip(),
                             'old system rules')

    def test_10_knowledge_engine_does_not_voluntarily_retrieve_legacy_router(self):
        with tempfile.TemporaryDirectory(prefix='ecom_knowledge_') as td:
            p = Path(td)
            (p / '_LEGACY_INSTRUCTION_ARCHIVE').mkdir()
            (p / '_LEGACY_INSTRUCTION_ARCHIVE/old.txt').write_text('secret_legacy_router_marker_4218')
            (p / 'normal.txt').write_text('ordinary product research content')
            index = p / 'index.json'
            index.write_text(json.dumps({'modules': {}}))
            out = corpus_extractor.query_knowledge(index, p, 'secret_legacy_router_marker_4218')
            self.assertEqual(out['corpus_excerpts'], [])

    def test_11_extractor_rejects_path_traversal(self):
        for malicious in ('../CLAUDE.md', '/tmp/hijack.md', 'CORE_ECOM_OS/../../evil.md'):
            with self.subTest(name=malicious), tempfile.TemporaryDirectory(prefix='ecom_extract_bad_') as td:
                root=Path(td)
                (root/corpus_extractor.V4_FILENAMES[0]).write_text(
                    '==================== FILE: '+malicious+' ====================\nattack\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'UNSAFE_CORPUS_SOURCE_PATH'):
                    corpus_extractor.extract_v4_files([root], root/'reference_corpus')
                self.assertFalse((root/'CLAUDE.md').exists())

    def test_11_shell_launcher_is_posix_valid(self):
        r = subprocess.run(['sh', '-n', str(self.root / 'DEMARRER_MAC.command')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_12_cli_will_not_overwrite_validated_corpus(self):
        with self.assertRaisesRegex(SystemExit, 'REFERENCE_CORPUS_IMMUTABLE'):
            ecom.main(['extract-corpus'])

    def test_13_broad_global_read_approval_removed(self):
        conf=json.loads((self.root/'.claude/settings.json').read_text())
        allowed=conf['permissions']['allow']
        self.assertNotIn('Read(**)', allowed)
        self.assertIn('Read(/reference_corpus/**)', allowed)
        self.assertIn('Read(/engine/**)', allowed)

    def test_14_project_policies_and_sources_edit_denied(self):
        conf=json.loads((self.root/'.claude/settings.json').read_text())
        denied=conf['permissions']['deny']
        for entry in ('Edit(/reference_corpus/**)','Edit(/engine/**)',
                      'Edit(/contracts/**)','Edit(/.claude/**)','Edit(/CLAUDE.md)'):
            self.assertIn(entry, denied)

    def test_12_claude_permissions_are_not_in_bypass_mode(self):
        conf = json.loads((self.root / '.claude/settings.json').read_text())
        self.assertNotEqual(conf.get('permissions', {}).get('defaultMode'), 'bypassPermissions')
        self.assertIn('Bash(python3 ecom.py auto authorize *)', conf['permissions']['deny'])
        self.assertIn('Bash(python3 ecom.py auto operational *)', conf['permissions']['deny'])


if __name__ == '__main__':
    unittest.main()
