"""Independent additional adversarial checks, built after reviewing LLM_Provider B1."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import ecom
from engine import autopilot, competitor_intel, ledger

class IndependentB1Review(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self):
        mdir = self.root/'R'
        res = ecom.build_replay_fixture(mdir)
        return mdir,res

    def test_01_demo_returns_real_selection(self):
        _,r=self.fixture()
        self.assertEqual(r['selected_solution'], 'SOL-DIATOMITE-SPOUT')
        self.assertTrue(str(r['selected_material_fingerprint']).startswith('MAT-'))

    def test_02_forged_audit_without_digest_fails(self):
        p=self.root/'audit.json'
        p.write_text(json.dumps({'status':'READY_FOR_AGENT01_REVIEW_NOT_APPROVED', 'provenance_mode':'REPLAY',
            'evidence_count':1,'validated_gap_count':1,'solution_audits':{'S1':{'agent02_status':'AUDITED_READY'}}}))
        self.assertEqual(autopilot.load_agent02_audits(p), {})

    def test_03_modified_legitimate_audit_rejected(self):
        mdir,_=self.fixture()
        p=mdir/'AGENT_02'/'competitor_audit_report.json'
        self.assertTrue(autopilot.load_agent02_audits(p))
        x=json.loads(p.read_text());x['solution_audits']['SOL-DIATOMITE-SPOUT']['agent02_status']='AUDITED_READY'
        p.write_text(json.dumps(x))
        self.assertEqual(autopilot.load_agent02_audits(p),{})

    def test_04_supplier_cost_change_revokes_a02_audit(self):
        mdir,_=self.fixture()
        s=json.loads((mdir/'AGENT_01'/'dossiers.json').read_text())['JTBD-DRAIN-01']['solutions'][0]
        revised=copy.deepcopy(s);revised['supplier']='OTHER'; revised['economics']['landed_cost_eur']=87
        self.assertNotEqual(competitor_intel.solution_fingerprint(s),competitor_intel.solution_fingerprint(revised))

    def test_05_replay_cannot_set_live_or_internal_fields(self):
        p=self.root/'obs.jsonl';raw={'territory_id':'T','probleme':'p','phrase_exacte':'text','source_type':'FORUM_FR',
              'source_url':'https://forum.example.com/t/1','candidate_ids':['C']}
        with self.assertRaises(ValueError):
            ledger.append(p,[dict(raw,is_live=True)],mission_id='M',provenance_mode='REPLAY')
        with self.assertRaises(ValueError):
            ledger.append(p,[dict(raw,provenance_mode='LIVE')],mission_id='M',provenance_mode='REPLAY')
        with self.assertRaises(ValueError):
            ledger.append(p,[raw],mission_id='M',provenance_mode='LIVE')
        self.assertFalse(p.exists() and p.stat().st_size)

    def test_06_missing_selected_solution_revokes_selection(self):
        mdir,res=self.fixture()
        reg=autopilot.CandidateRegistry.load(mdir/'AGENT_01'/'candidate_registry.json')
        dossiers=json.loads((mdir/'AGENT_01'/'dossiers.json').read_text())
        dossiers[res['selected_candidate']]['solutions'] = []
        msg=reg.check_material_integrity(res['selected_candidate'], dossiers[res['selected_candidate']])
        self.assertEqual(msg,'STALE_PASS_INVALIDATED_MATERIAL_CHANGE')

    def test_07_llm_forged_score_and_gate2b_do_not_advance(self):
        mdir,res=self.fixture()
        reg=autopilot.CandidateRegistry(mission_id='R')
        reg.add('JTBD-DRAIN-01','Fake',stage=5)
        fake={'JTBD-DRAIN-01': {'candidate_id':'JTBD-DRAIN-01','stage':5,
              'score':{'status':'SCORED_WITH_EVIDENCE','score':40},
              'gate2b':{'status':'PASS'}, 'economic_screen':{'status':'OK'}, 'red_flags':[],'info_needed':[]}}
        out=autopilot.step(reg,fake,max_decisions=1,ledger=ledger.load_by_id(mdir/'AGENT_01'/'problem_ledger.jsonl'),
                     provenance_mode='REPLAY',target_market='FR')
        self.assertNotEqual(out['decisions'][0]['verdict'],'ADVANCE')

    def test_08_live_audit_must_not_load_in_b1(self):
        mdir,_=self.fixture()
        p=mdir/'AGENT_02'/'competitor_audit_report.json'
        x=json.loads(p.read_text());x['provenance_mode']='LIVE'
        body={k:v for k,v in x.items() if k != 'audit_digest'}
        import hashlib
        x['audit_digest']=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:20]
        p.write_text(json.dumps(x))
        self.assertEqual(autopilot.load_agent02_audits(p),{})

    def test_09_spoofed_scope_key_cannot_reuse_other_provider_grant(self):
        item_a={"id":"quote", "provider":"A", "solution_id":"S1", "cost_class":"PAID_MARKETPULSE", "max_amount":20, "decisive":True}
        item_b={**item_a,"provider":"B"}
        scope_a=autopilot.compute_scope_key("M","C",item_a)
        spoof_b={**item_b,"scope_key":scope_a}
        with self.assertRaises(ValueError):
            autopilot.plan_information([spoof_b],mission_id="M",candidate_id="C",granted_scopes={scope_a})

    @unittest.skipUnless((__import__("pathlib").Path(__file__).resolve().parents[1] / "reference_corpus").exists(), "Requires external corpus")
    def test_10_complete_corpus_extracted(self):
        corpus=ecom.ROOT/'reference_corpus'
        manifest=json.loads((corpus/'_EXTRACTION_MANIFEST.json').read_text())
        self.assertEqual(manifest['extracted_count'],142)
        self.assertEqual(len([p for p in corpus.rglob('*') if p.is_file()])-1,142)

if __name__=='__main__':unittest.main()
