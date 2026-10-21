"""Extra isolation, boundary and packaging regressions for B3 Mac+Agent final."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import ecom
from engine import research_queue, autopilot, scoring, competitor_intel


class MacAgentFinalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='ecom_b3_mac_'))
        self.mid = 'TEST_MAC_B3'
        self.mdir = self.tmp / self.mid
        self.mdir.mkdir()
        self.cfg={'mission_id':self.mid,'target_sale_market':'FR','persona':'Persona test','mode':'REPLAY'}
        (self.mdir/'mission.json').write_text(json.dumps(self.cfg),encoding='utf8')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_mission_path_rejects_traversal_and_invalid(self):
        for name in ('..','../tmp','a/b','\\tmp','.', '', 'a b', '🚫', 'A'*65, 'a\nX'):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                ecom._mission_dir(name)
        self.assertTrue(str(ecom._mission_dir('A_safe-02')).endswith('A_safe-02'))

    def test_02_mission_initialization_does_not_overwrite(self):
        (self.mdir/'mission.json').unlink()
        with patch.object(ecom,'_runs_dir',return_value=self.tmp), contextlib.redirect_stdout(io.StringIO()):
            r=ecom.main(['init','--mission',self.mid,'--market','FR','--persona','Persona test','--foreign-markets','US','--mode','REPLAY'])
            self.assertEqual(r,0)
            p=self.mdir/'mission.json'
            original=p.read_bytes()
            with self.assertRaises(SystemExit):
                ecom.main(['init','--mission',self.mid,'--market','AE','--persona','Persona test'])
            self.assertEqual(p.read_bytes(),original)

    def test_03_research_kit_generated_offline(self):
        out=research_queue.make_research_kit(self.mdir,'AE','Persona libre')
        self.assertEqual(out['status'],'RESEARCH_KIT_READY')
        brief=json.loads(Path(out['brief_file']).read_text())
        self.assertEqual(brief['market'],'AE')
        self.assertEqual(brief['provenance'],'UNVERIFIED_RESEARCH_NOT_LIVE_EVIDENCE')
        self.assertEqual(len(brief['pipeline']),6)

    def test_04_research_kit_idempotent_preserves_user_changes(self):
        out=research_queue.make_research_kit(self.mdir,'FR','x')
        p=Path(out['brief_file']);p.write_text('edited by user',encoding='utf8')
        research_queue.make_research_kit(self.mdir,'FR','x')
        self.assertEqual(p.read_text(),'edited by user')

    def good(self):
        return {'url':'https://example.org/products/important-review','exact_observation':'Le produit mouille la table chaque soir.',
                'market_geo':'FR', 'source_class':'DIRECT_OBSERVATION','entity_scope':'REVIEW',
                'capture_method':'LLM_Provider runtime WebFetch'}

    def test_05_untrusted_live_cannot_be_staged(self):
        for patchdata in ({'verified_live':True},{'provenance_mode':'LIVE'}, {'synthetic':False,'verified_live':True}):
            row={**self.good(),**patchdata}
            out=research_queue.stage_unverified_capture(self.mdir,row)
            self.assertEqual(out['status'],'REJECTED_CAPTURE')

    def test_06_public_https_only(self):
        for url in ('http://example.org/x','https://127.0.0.1/x','https://[::1]/x',
                    'https://192.168.1.1/x','https://localhost/x','https://internal.local/x',
                    'https://user:pass@example.org/x','file:///etc/passwd'):
            with self.subTest(url=url):
                row=self.good();row['url']=url
                self.assertEqual(research_queue.stage_unverified_capture(self.mdir,row)['status'],'REJECTED_CAPTURE')

    def test_07_capture_is_quarantined_not_gate_approved(self):
        out=research_queue.stage_unverified_capture(self.mdir,self.good())
        self.assertEqual(out['status'],'CAPTURE_STAGED_UNVERIFIED')
        cap=json.loads(Path(out['file']).read_text())
        self.assertEqual(cap['provenance_mode'],'UNVERIFIED')
        self.assertIs(cap['approved_for_gate'],False)
        self.assertIs(cap['verified_live'],False)
        self.assertNotIn('observation_id',cap)

    def test_08_duplicate_staging_is_idempotent(self):
        first=research_queue.stage_unverified_capture(self.mdir,self.good())
        next_row={**self.good(),'observed_at':'2031-01-01T00:00:00+00:00'}
        second=research_queue.stage_unverified_capture(self.mdir,next_row)
        self.assertEqual(second['status'],'CAPTURE_ALREADY_STAGED')
        self.assertEqual(first['file'],second['file'])

    def test_09_separate_missions_do_not_share_queue(self):
        other=self.tmp/'ANOTHER_MISSION';other.mkdir()
        x=research_queue.stage_unverified_capture(self.mdir,self.good())
        y=research_queue.stage_unverified_capture(other,self.good())
        self.assertNotEqual(x['file'],y['file'])

    def test_10_unknown_or_empty_claim_fails(self):
        for row in ({}, {'exact_observation':'tiny'}, {'market_geo':''}, {'capture_method':''}):
            res=research_queue.stage_unverified_capture(self.mdir,{**self.good(),**row})
            if row=={}:
                self.assertEqual(res['status'],'CAPTURE_STAGED_UNVERIFIED')
            else:
                self.assertEqual(res['status'],'REJECTED_CAPTURE')

    def test_11_filter_ref_requires_real_reference_universe(self):
        answer={'q1':{'answer':'YES','evidence_refs':['FAKE']}}
        self.assertEqual(autopilot.evaluate_product_filter_14(answer,set())['yes_count'],0)

    def test_12_score_cannot_be_approved_without_registry(self):
        axes={a:{'score':5,'evidence_refs':['OBS-FAKE'],'justification':'at least one evidence'} for a in scoring.EIGHT_AXES}
        self.assertEqual(scoring.score_with_evidence(axes)['status'],'REJECTED_UNSUPPORTED_SCORE')

    def test_13_no_vox_customer_from_shop(self):
        self.assertFalse(competitor_intel._is_valid_voc_evidence({'entity_scope':'SHOP','source_class':'DIRECT_OBSERVATION'}))
        self.assertTrue(competitor_intel._is_valid_voc_evidence({'entity_scope':'REVIEW','source_class':'DIRECT_OBSERVATION'}))

    def test_14_audit_cli_rejects_other_mission(self):
        with patch.object(ecom,'_runs_dir',return_value=self.tmp),contextlib.redirect_stdout(io.StringIO()):
            entries={'handoff':{'mission_id':'OTHER','target_sale_market':'FR'}, 'evidence':[], 'gaps':[], 'negatives':[], 'scores':{}}
            paths={}
            for k,v in entries.items():
                p=self.tmp/(k+'.json');p.write_text(json.dumps(v),encoding='utf8');paths[k]=str(p)
            args=['a02','audit','--mission',self.mid,'--handoff',paths['handoff'],'--evidence',paths['evidence'],
                  '--gaps',paths['gaps'],'--negatives',paths['negatives'],'--scores',paths['scores']]
            with self.assertRaisesRegex(SystemExit,'AGENT02_HANDOFF_MISSION_OR_MARKET_MISMATCH'):
                ecom.main(args)

    def test_15_research_stage_cli_replay_only(self):
        (self.mdir/'mission.json').write_text(json.dumps({**self.cfg,'mode':'LIVE'}),encoding='utf8')
        with patch.object(ecom,'_runs_dir',return_value=self.tmp):
            with self.assertRaisesRegex(SystemExit,'MISSION_NOT_REPLAY'):
                ecom.main(['research-kit','--mission',self.mid])

    def test_16_research_cli_requires_initialization(self):
        with patch.object(ecom,'_runs_dir',return_value=self.tmp):
            with self.assertRaisesRegex(SystemExit,'MISSION_NOT_INITIALIZED'):
                ecom.main(['research-kit','--mission','NEW_MISSION'])

    def test_17_claude_settings_no_auto_spend(self):
        config=json.loads((ecom.ROOT/'.claude/settings.json').read_text())
        allows='\n'.join(config['permissions']['allow'])
        self.assertNotIn('bypassPermissions',str(config))
        self.assertNotIn('Bash(python3 ecom.py auto authorize *)',allows)
        self.assertIn('Bash(python3 ecom.py auto authorize *)', config['permissions']['deny'])

    def test_18_config_files_and_docs_exist(self):
        for rel in ('INSTALL_MAC.md','DEMARRER_MAC.command','CLAUDE.md','MODULE_MAP.md',
                    '.claude/skills/ecom-os/SKILL.md','.claude/agents/voc-miner.md',
                    '.claude/agents/competitor-scout.md','docs/VALIDATION_AND_LIMITS.md'):
            self.assertTrue((ecom.ROOT/rel).is_file(),rel)

    def test_19_shell_entrypoint_parses(self):
        r=subprocess.run(['sh','-n',str(ecom.ROOT/'DEMARRER_MAC.command')],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_20_shell_option_exit_zero_no_work(self):
        r=subprocess.run(['sh',str(ecom.ROOT/'DEMARRER_MAC.command')],input='4\n',capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('Aucune opération',r.stdout)

    def test_21_mission_kit_main_no_network(self):
        with patch.object(ecom,'_runs_dir',return_value=self.tmp),contextlib.redirect_stdout(io.StringIO()) as out:
            code=ecom.main(['research-kit','--mission',self.mid])
            self.assertEqual(code,0)
            self.assertIn('RESEARCH_KIT_READY',out.getvalue())

    def test_22_capture_large_or_empty_rejected(self):
        for text in ('', 'x'*3001):
            row=self.good();row['exact_observation']=text
            self.assertEqual(research_queue.stage_unverified_capture(self.mdir,row)['status'],'REJECTED_CAPTURE')

    def test_23_all_new_python_compiles(self):
        for rel in ('ecom.py','engine/research_queue.py','engine/autopilot.py','engine/competitor_intel.py'):
            compile((ecom.ROOT/rel).read_text(encoding='utf8'),rel,'exec')

    @unittest.skipUnless((__import__("pathlib").Path(__file__).resolve().parents[1] / "reference_corpus").exists(), "Requires external corpus")
    def test_24_healthcheck_verifies_claude_and_python(self):
        from engine import product_research_pipeline as pipe
        h=pipe.healthcheck(ecom.ROOT)
        self.assertEqual(h['status'],'REPLAY_READY')
        self.assertGreaterEqual(h['required_files_present'],15)

    def test_25_real_data_claim_cannot_bypass_live_disabled(self):
        with self.assertRaisesRegex(ValueError,'LIVE_AUTOPILOT_NOT_SUPPORTED'):
            autopilot.step(autopilot.CandidateRegistry(mission_id='M'),{},provenance_mode='LIVE')


if __name__ == '__main__':
    unittest.main()
