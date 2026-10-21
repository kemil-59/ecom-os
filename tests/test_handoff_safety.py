"""Independent safety checks before passing B1 as development input to LLM_Provider."""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path
from engine import competitor_intel, autopilot
ROOT=Path(__file__).resolve().parents[1]

class HandoffSecurityTests(unittest.TestCase):
    def test_cli_grant_is_not_possible_in_unattended_b1(self):
        with tempfile.TemporaryDirectory() as t:
            # Use a scratch copy of ecom.py? CLI B1 guards before reaching registry
            res=subprocess.run([sys.executable,str(ROOT/'ecom.py'),'auto','authorize',
                '--mission','UNDEFINED_MISSION','--candidate','C1','--item','Q1',
                '--scope-key','SCOPE-FAKE','--grant'],cwd=ROOT,capture_output=True,text=True)
            self.assertNotEqual(res.returncode,0)
            self.assertIn('DISABLED',res.stderr+res.stdout)
    def test_cli_operational_pass_not_possible_in_b1(self):
        res=subprocess.run([sys.executable,str(ROOT/'ecom.py'),'auto','operational',
            '--mission','UNDEFINED_MISSION','--candidate','C1','--solution','S1',
            '--material-fp','fake','--pass-gate','--reason','test','--refs','R1'],
            cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(res.returncode,0)
        self.assertIn('DISABLED',res.stderr+res.stdout)
    def test_counterexample_missing_ref_not_accepted(self):
        gap={'gap_id':'G1'}
        rows=[{'gap_id':'G1','market_geo':'FR','counterexample_found':True,
               'source_refs':['MADE_UP'],'query':'test','surface':'shops'}]
        result=competitor_intel.falsify_home_gap(gap,rows,'FR',{'VALID_FR_REF'})
        self.assertEqual(result['status'],'GAP_UNCONFIRMED')
    def test_counterexample_valid_fr_reference_supported(self):
        gap={'gap_id':'G1'}
        rows=[{'gap_id':'G1','market_geo':'FR','counterexample_found':True,
               'source_refs':['VALID_FR_REF'],'query':'test','surface':'shops'}]
        result=competitor_intel.falsify_home_gap(gap,rows,'FR',{'VALID_FR_REF'})
        self.assertEqual(result['status'],'COUNTEREXAMPLE_FOUND')
    def test_live_auto_step_disabled(self):
        with tempfile.TemporaryDirectory() as t:
            # Preconfigure isolated mission in the project test folder, clean after
            import uuid, shutil
            mid='CHECK_LIVE_'+uuid.uuid4().hex[:8]
            mdir=ROOT/'runs'/mid
            try:
                mdir.mkdir(parents=True,exist_ok=True)
                (mdir/'mission.json').write_text(json.dumps({'mode':'LIVE','target_sale_market':'FR'}))
                (mdir/'dossiers.json').write_text('{}')
                cp=subprocess.run([sys.executable,str(ROOT/'ecom.py'),'auto','step','--mission',mid,'--dossiers',str(mdir/'dossiers.json')],cwd=ROOT,capture_output=True,text=True)
                self.assertNotEqual(cp.returncode,0)
                self.assertIn('LIVE_AUTOPILOT_NOT_SUPPORTED_IN_B1',cp.stderr+cp.stdout)
            finally: shutil.rmtree(mdir,ignore_errors=True)

    def test_untrusted_seed_cannot_claim_selection(self):
        reg=autopilot.CandidateRegistry(mission_id="M-SAFE")
        with self.assertRaisesRegex(ValueError, "SEED_CANNOT_SET_VALIDATED_STATUS"):
            autopilot.seed_candidates(reg,[{"id":"X","title":"Fake","stage":5,
                "status":"PROPOSED_FOR_COMMERCIAL_SELECTION"}])
        self.assertNotIn("X",reg.candidates)

    def test_seed_cannot_begin_at_later_gate(self):
        reg=autopilot.CandidateRegistry(mission_id="M-SAFE")
        with self.assertRaisesRegex(ValueError, "SEED_CANNOT_SKIP_INITIAL_GATES"):
            autopilot.seed_candidates(reg,[{"id":"X","title":"Fake","stage":8,"status":"ACTIVE"}])

    def test_unverified_persisted_selection_cannot_skip_gate(self):
        reg=autopilot.CandidateRegistry(mission_id="M-SAFE")
        reg.add("X","Fake",stage=5,status="PROPOSED_FOR_COMMERCIAL_SELECTION")
        with self.assertRaisesRegex(ValueError, "PROPOSED_STATE_WITHOUT_ENGINE_GATE7_DECISION"):
            autopilot.step(reg,{"X":{"candidate_id":"X", "stage":5}},max_decisions=2)

    def test_negative_search_result_refs_must_exist(self):
        gap={"gap_id":"G1"}
        checks=[{"gap_id":"G1","market_geo":"FR","query":q,"surface":surf,
                 "result_evidence_ref":"FAKE"} for q,surf in
                 [("term1","shops"),("term2","marketplace"),("term3","marketplace")]]
        r=competitor_intel.falsify_home_gap(gap,checks,"FR",{"REAL"})
        self.assertEqual(r["status"],"GAP_UNCONFIRMED")

if __name__=='__main__':unittest.main()
