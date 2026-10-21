"""Regression and negative-case checks for conservative B3 rescue. 100% offline."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import ecom
from engine import ledger, autopilot, competitor_intel, product_research_pipeline as pipe


class B3RescueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix='ecom_b3_safe_'))
        self.mid='B3_TEST'
        self.mdir=self.tmp/self.mid
    def tearDown(self):
        shutil.rmtree(self.tmp,ignore_errors=True)

    def territories(self):
        return [{'territory_id':f'T-{i}', 'name':f'Territory {i}',
                 'micro_contexts':[{'moment':'morning','place':'home','friction':'lost time'}]} for i in range(8)]

    @unittest.skipUnless((__import__("pathlib").Path(__file__).resolve().parents[1] / "reference_corpus").exists(), "Requires external corpus")
    def test_01_healthcheck_requires_all_sources(self):
        h=pipe.healthcheck(ecom.ROOT)
        self.assertEqual(h['status'],'REPLAY_READY')
        self.assertEqual(h['source_files'],142)
        self.assertEqual(h['live_mode'],'DISABLED_NO_VERIFIED_CONNECTOR')

    def test_02_territories_valid_and_persist(self):
        out=pipe.register_territories(self.mdir,self.territories())
        self.assertEqual(out['status'],'PASS_REPLAY_STRUCTURE_ONLY')
        self.assertTrue((self.mdir/'AGENT_01'/'territory_map.json').exists())

    def test_03_territories_reject_early_sku_without_overwrite(self):
        pipe.register_territories(self.mdir,self.territories())
        path=self.mdir/'AGENT_01'/'territory_map.json'
        old=path.read_bytes()
        bad=self.territories();bad[0]['supplier_sku']='BUY-THIS'
        out=pipe.register_territories(self.mdir,bad)
        self.assertEqual(out['status'],'GATE_2A_INCOMPLETE')
        self.assertTrue(any('PREMATURE_SKU' in i for i in out['issues']))
        self.assertEqual(old,path.read_bytes())

    def test_04_territories_reject_duplicate_name(self):
        bad=self.territories();bad[1]['name']=bad[0]['name']
        out=pipe.register_territories(self.mdir,bad)
        self.assertNotEqual(out['status'],'PASS_REPLAY_STRUCTURE_ONLY')

    def test_05_territories_reject_missing_microcontext_fields(self):
        bad=self.territories();bad[2]['micro_contexts']=[{'moment':'evening'}]
        out=pipe.register_territories(self.mdir,bad)
        self.assertTrue(any('INVALID_MICROCONTEXT' in i for i in out['issues']))

    def test_06_jtbd_cannot_invent_refs(self):
        jobs=[{'id':f'J{i}', 'title':'Dry kitchen', 'workaround_cost':'5 min', 'voc_refs':['OBS-FAKE']} for i in range(5)]
        out=pipe.register_jtbd_shortlist(self.mdir,jobs)
        self.assertEqual(out['status'],'GATE_3_INCOMPLETE')
        self.assertFalse((self.mdir/'AGENT_01'/'jtbd_ranked.json').exists())

    def test_07_jtbd_valid_replay_refs(self):
        fixture=ecom.build_replay_fixture(self.mdir)
        jobs=[{'id':f'J{i}', 'title':f'JTBD {i}', 'workaround_cost':'5 min/day', 'voc_refs':['OBS-0001']} for i in range(5)]
        out=pipe.register_jtbd_shortlist(self.mdir,jobs)
        self.assertEqual(out['status'],'PASS_REPLAY_STRUCTURE_ONLY')

    def test_08_audit_scorecard_25_requires_real_referenced_sources(self):
        good={'status':'TESTABLE_DIFFERENTIATION_HYPOTHESIS'}
        dims={k:4 for k in competitor_intel.SCORECARD_DIMS}; dims['source_refs']=['UNKNOWN']
        out=competitor_intel.score_transferability(good,dims,valid_ev_refs={'KNOWN'})
        self.assertEqual(out['status'],'INSUFFICIENT_VALIDATED_INPUTS')

    def test_09_no_bare_yes_in_normal_gate7(self):
        x=autopilot.evaluate_product_filter_14({f'q{i}':'YES' for i in range(1,15)}, {'OBS-1'})
        self.assertEqual(x['yes_count'],0)
        self.assertEqual(len(x['assumptions']),14)

    def test_10_no_fake_refs_when_known_set_empty(self):
        x=autopilot.evaluate_product_filter_14({'q1':{'answer':'YES','evidence_refs':['FAKE']}},set())
        self.assertEqual(x['yes_count'],0)
        self.assertIn('q1',x['assumptions'])

    def test_11_dossier_is_explicitly_not_commercial_proof(self):
        ecom.build_replay_fixture(self.mdir)
        out=pipe.generate_decision_dossier(self.mdir)
        self.assertEqual(out['status'],'DRAFT_REPLAY_NOT_COMMERCIAL_APPROVAL')
        data=json.loads(Path(out['json']).read_text())
        self.assertFalse(data['commercially_approved'])
        self.assertFalse(data['verified_live_evidence'])
        self.assertTrue(Path(out['markdown']).exists())

    def test_12_unsafe_and_unconnected_web_source_blocked(self):
        for url in ['http://example.com','https://localhost/private','https://user:pass@example.org/x']:
            self.assertEqual(pipe.collect_public_web_source(self.mdir,url=url,purpose='research')['status'],'BLOCKED_SOURCE')
        x=pipe.collect_public_web_source(self.mdir,url='https://www.example.org/reviews',purpose='collect review')
        self.assertEqual(x['status'],'BLOCKED_SOURCE')
        self.assertIn('collection_task',x)
        self.assertTrue(x['never_invent_live_result'])

    def test_13_bare_optout_in_dossier_cannot_bypass_step_gate(self):
        mid='MISSION_REPLAY_TAMPER'
        out=ecom.build_replay_fixture(self.mdir)
        reg=autopilot.CandidateRegistry.load(self.mdir/'AGENT_01'/'candidate_registry.json')
        dossiers=json.loads((self.mdir/'AGENT_01'/'dossiers.json').read_text())
        first=reg.candidates['JTBD-DRAIN-01']
        first['status']='ACTIVE';first['stage']=8;first['history']=[
            {'gate': 'GATE_4','verdict':'ADVANCE'},{'gate': 'GATE_5','verdict':'ADVANCE'},
            {'gate':'GATE_6','verdict':'ADVANCE'}]
        first['selected_solution_id']=None;first['selected_material_fingerprint']=None
        first['evidence_fingerprint']=None
        dossiers['JTBD-DRAIN-01']['require_filter_evidence']=False
        rep=autopilot.load_agent02_audits(self.mdir/'AGENT_02'/'competitor_audit_report.json')
        result=autopilot.step(reg,dossiers,ledger=ledger.load_by_id(self.mdir/'AGENT_01'/'problem_ledger.jsonl'),agent02_audits=rep,max_decisions=1,provenance_mode='REPLAY')
        self.assertNotEqual(result['decisions'][0]['verdict'],'PROPOSE_FOR_COMMERCIAL_SELECTION')

    def test_14_separate_missions_produce_separate_decisions(self):
        a,b=self.tmp/'R1',self.tmp/'R2'
        ecom.build_replay_fixture(a)
        ecom.build_replay_fixture(b)
        da=pipe.generate_decision_dossier(a)
        db=pipe.generate_decision_dossier(b)
        self.assertNotEqual(Path(da['json']),Path(db['json']))
        self.assertEqual(json.loads(Path(da['json']).read_text())['mission_id'],'R1')
        self.assertEqual(json.loads(Path(db['json']).read_text())['mission_id'],'R2')

    def test_15_nobody_can_claim_live_autopilot_from_library(self):
        reg=autopilot.CandidateRegistry(mission_id='X')
        with self.assertRaisesRegex(ValueError,'LIVE_AUTOPILOT_NOT_SUPPORTED'):
            autopilot.step(reg,{},provenance_mode='LIVE')

    def test_16_untouched_product_filter_legacy_only_fixture(self):
        # The demo may simulate a selection. A normal mission cannot use bare YES.
        out=ecom.build_replay_fixture(self.mdir)
        self.assertEqual(out['selected_solution'],'SOL-DIATOMITE-SPOUT')
        x=autopilot.evaluate_product_filter_14({f'q{i}':'YES' for i in range(1,15)}, {'OBS-0001'})
        self.assertEqual(x['yes_count'],0)


    def test_17_a02_scorecard_low_score_is_blocked(self):
        gap={'status':'TESTABLE_DIFFERENTIATION_HYPOTHESIS'}
        data={k:3 for k in competitor_intel.SCORECARD_DIMS}
        data['source_refs']=['EV-OK']
        out=competitor_intel.score_transferability(gap,data,valid_ev_refs={'EV-OK'})
        self.assertEqual(out['score_25'],15)
        self.assertEqual(out['status'],'LOW_PRIORITY_OR_WEAK_FIT')

    def test_18_synthetic_shop_never_becomes_customer_review(self):
        self.assertFalse(competitor_intel._is_valid_voc_evidence(
            {'source_class':'DIRECT_OBSERVATION','entity_scope':'SHOP','synthetic':True}))
        self.assertTrue(competitor_intel._is_valid_voc_evidence(
            {'source_class':'DIRECT_OBSERVATION','entity_scope':'REVIEW','synthetic':True}))

if __name__=='__main__': unittest.main()
