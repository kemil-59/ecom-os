"""Independent B2 rescue tests. Offline/REPLAY only; no paid or external requests."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid

import ecom
from engine import marketpulse_adapter as tt, creative_center as cc, competitor_intel as ci, downstream_agents as ds, ledger, autopilot


class B2RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='b2_recovery_'))
        self.adapter = tt.MarketPulseAdapter(self.tmp/'TT', mode='REPLAY')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def configure_tt(self, max_credits=100):
        self.adapter.ingest_balance({'requestId':'balance-1','credits':{'totalRemaining':2000}})
        self.adapter.record_replay_approval(scope='TEST',max_credits=max_credits,owner_statement='REPLAY TEST APPROVAL ONLY')

    def plan(self, **kw):
        opts=dict(mission_id='M',scope='TEST',recipe_id='A01_S5_AD_SAMPLE_REACH',country='FR',stage=5,terms=['test'],locale='fr')
        opts.update(kw)
        return self.adapter.plan_call(**opts)

    def test_01_tt_disconnected_blocks(self):
        d=tt.MarketPulseAdapter(self.tmp/'Disc',mode='DISCONNECTED',connected=False)
        self.assertEqual(d.capabilities()['status'],'BLOCKED_CONNECTOR')

    def test_02_tt_missing_balance_blocks(self):
        self.assertEqual(self.adapter.capabilities()['status'],'BLOCKED_CONNECTOR')

    def test_03_balance_replay_never_claims_live_verified(self):
        self.configure_tt()
        a=self.adapter.capabilities()
        self.assertTrue(a['replay'])
        self.assertFalse(a['verified_at_runtime'])

    def test_04_tt_requires_scope_approval(self):
        self.adapter.ingest_balance({'requestId':'b','credits':{'totalRemaining':2000}})
        self.assertIn('NO_ACTIVE_CREDIT_APPROVAL',str(self.plan()['reasons']))

    def test_05_tt_us_localization_guard(self):
        self.configure_tt()
        r=self.plan(country='US',locale='fr',terms=['égouttoir'])
        self.assertEqual(r['status'],'BLOCKED')

    def test_06_tt_stage_gate_guard(self):
        self.configure_tt()
        r=self.plan(recipe_id='A01_S7_SOLUTION_PRODUCTS',stage=5,terms=['product'])
        self.assertEqual(r['status'],'BLOCKED')
        self.assertIn('STAGE_BELOW_7',str(r['reasons']))

    def test_07_tt_forbidden_write_guard(self):
        self.configure_tt()
        r=self.plan(recipe_id=None,tool='add_to_brandtracker')
        self.assertEqual(r['status'],'BLOCKED')

    def test_08_tt_ceiling_guard(self):
        self.configure_tt(max_credits=2)
        self.assertIn('SCOPE_CREDIT_CEILING_EXCEEDED',str(self.plan()['reasons']))

    def test_09_tt_receipt_reconcile_and_no_ad_sku_proof(self):
        self.configure_tt()
        r=self.plan()
        self.assertEqual(r['status'],'AWAITING_EXECUTION')
        response={'requestId':'ads-1','credits':{'costUnits':2,'creditsRemaining':1998},'data':[
            {'ad_id':'A','landing_url':'https://a.com/products/x','sku_url':'https://a.com/products/x','shop_domain':'a.com','ad_copy':'test'}]}
        ing=self.adapter.ingest_call(r['call_id'],response)
        self.assertFalse(ing['evidence'][0]['is_live'])
        self.assertNotEqual(ing['entity_links'][0]['link_status'],'VERIFIED_AD_TO_SKU')
        rec=self.adapter.reconcile_credits({'requestId':'b2','credits':{'totalRemaining':1998}})
        self.assertEqual(rec['status'],'RECONCILED')
        with self.assertRaises(KeyError): self.adapter.ingest_call(r['call_id'],response)

    def test_10_tt_overrun_freezes_model(self):
        self.configure_tt()
        r=self.plan(recipe_id='A01_S5_AD_DENSITY_PROBE')
        out=self.adapter.ingest_call(r['call_id'],{'requestId':'cost-breach','credits':{'costUnits':7,'creditsRemaining':1993},'data':[]})
        self.assertEqual(out['alert'],'COST_MODEL_VIOLATION_TOOL_FROZEN')
        self.assertIn('TOOL_FROZEN',str(self.plan()['reasons']))

    def test_11_counterexample_requires_real_refs_and_feature(self):
        g={'gap_id':'G','required_feature_keywords':['telescopique']}
        ev={'REF':{'observation':'fixed nozzle only'}}
        checks=[{'gap_id':'G','market_geo':'FR','counterexample_found':True,'source_refs':['REF'],'query':'q','surface':'shops'}]
        out=ci.falsify_home_gap(g,checks,'FR',{'REF'},ev)
        self.assertEqual(out['status'],'GAP_UNCONFIRMED_FEATURE_MISMATCH')
        checks[0]['source_refs']=['NONEXISTENT']
        out=ci.falsify_home_gap(g,checks,'FR',{'REF'},ev)
        self.assertNotEqual(out['status'],'COUNTEREXAMPLE_FOUND')

    def test_12_shop_is_not_live_customer_testimony(self):
        self.assertFalse(ci._is_valid_voc_evidence({'entity_scope':'SHOP','source_class':'DIRECT_OBSERVATION'}))
        self.assertFalse(ci._is_valid_voc_evidence({'entity_scope':'SHOP','source_class':'THIRD_PARTY_ESTIMATE'}))
        self.assertTrue(ci._is_valid_voc_evidence({'entity_scope':'REVIEW','source_class':'DIRECT_OBSERVATION'}))

    def test_13_agent03_blocks_unselected_candidate(self):
        res=cc.compile_creative_center_plan(root_dir=ecom.ROOT,mission_id='M',persona='P',target_market='AE',candidate_entry={'id':'C','status':'BACKLOG'},dossier={'solutions':[]},ledger_by_id={},agent02_report={},product_master={})
        self.assertEqual(res['status'],'BLOCKED_INTAKE')

    def test_14_agent03_requires_intact_a02_and_fingerprint(self):
        mdir=self.tmp/'MISSION_REPLAY_X'
        ecom.build_replay_fixture(mdir)
        r=autopilot.CandidateRegistry.load(mdir/'AGENT_01'/'candidate_registry.json',mdir.name)
        d=json.loads((mdir/'AGENT_01'/'dossiers.json').read_text())
        rep=json.loads((mdir/'AGENT_02'/'competitor_audit_report.json').read_text())
        cand=r.candidates['JTBD-DRAIN-01']
        product={'sku':'TEST-1','name':'Test product','solution_id':cand['selected_solution_id'],'material_fingerprint':cand['selected_material_fingerprint'],'immutable_attributes':['same product'],'reference_media':[{'view':'front','media_id':'local-id','rights':'OWNED'}]}
        rep['status']='APPROVED_BY_LLM'  # tamper digest
        out=cc.compile_creative_center_plan(root_dir=ecom.ROOT,mission_id=mdir.name,persona='PERSONA_UNVERIFIED',target_market='FR',candidate_entry=cand,dossier=d[cand['id']],ledger_by_id=ledger.load_by_id(mdir/'AGENT_01'/'problem_ledger.jsonl'),agent02_report=rep,product_master=product)
        self.assertEqual(out['status'],'BLOCKED_INTAKE')
        self.assertIn('AGENT02_DIGEST_INVALID',out['blockers'])

    @unittest.skipUnless((__import__("pathlib").Path(__file__).resolve().parents[1] / "reference_corpus").exists(), "Requires external corpus")
    def test_15_agent03_makes_only_sourced_generic_drafts(self):
        mdir=self.tmp/'MISSION_REPLAY_Y'
        ecom.build_replay_fixture(mdir)
        reg=autopilot.CandidateRegistry.load(mdir/'AGENT_01'/'candidate_registry.json',mdir.name)
        d=json.loads((mdir/'AGENT_01'/'dossiers.json').read_text())
        rep=json.loads((mdir/'AGENT_02'/'competitor_audit_report.json').read_text())
        cand=reg.candidates['JTBD-DRAIN-01']
        product={'sku':'TEST-1','name':'Test product','solution_id':cand['selected_solution_id'],'material_fingerprint':cand['selected_material_fingerprint'],'immutable_attributes':['same product'],'reference_media':[{'view':'front','media_id':'local-id','rights':'OWNED'}], 'sample_status':'SUPPLIER_SPEC_ONLY'}
        out=cc.compile_creative_center_plan(root_dir=ecom.ROOT,mission_id=mdir.name,persona='PERSONA_NOT_VALIDATED',target_market='FR',candidate_entry=cand,dossier=d[cand['id']],ledger_by_id=ledger.load_by_id(mdir/'AGENT_01'/'problem_ledger.jsonl'),agent02_report=rep,product_master=product)
        self.assertEqual(out['status'],'DRAFT_PLAN_READY_FOR_REVIEW')
        self.assertTrue(all('SEGMENT_DECLARE_NON_PROUVE' in a['avatar'] for a in out['angle_cards']))
        self.assertTrue(all('French kitchen' not in b['prompt_en'] for b in out['briefs']))
        self.assertTrue(all('REPLAY_DRAFT' in js['status'] for js in out['job_specs']))
        self.assertTrue(out['blocked_formats'])

    @unittest.skipUnless((__import__("pathlib").Path(__file__).resolve().parents[1] / "reference_corpus").exists(), "Requires external corpus")
    def test_16_downstream_unconnected_no_money_or_publishing(self):
        a05=ds.compile_agent05_meta_plan(root_dir=ecom.ROOT,packet={'mission_id':'M','market':'FR'},connector_capabilities={'connected':False})
        self.assertEqual(a05['meta_calls'],0)
        self.assertEqual(a05['money_spent'],0)
        self.assertIn('META_CONNECTOR_NOT_PROBED_OR_UNAVAILABLE',a05['issues'])
        self.assertEqual(a05['approval_request']['status'],'NOT_AUTHORIZED')
        a06=ds.diagnose_agent06_funnel_and_kahel_pillars({'refund_rate':0.4,'spend_eur':500,'orders':30})
        self.assertFalse(a06['scale_allowed'])
        self.assertEqual(a06['decision'],'STOP_AND_REPAIR_BACKEND_P0')

if __name__=='__main__': unittest.main()
