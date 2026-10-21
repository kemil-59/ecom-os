"""Additional independent Agent 01 / 02 + mission safety tests, REPLAY only."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from argparse import Namespace
from engine import ledger, autopilot, marketpulse_adapter
import ecom

class ProductResearchHardeningTests(unittest.TestCase):
    def test_same_author_three_pages_not_three_independent_people(self):
        obs=[]
        for i, source in enumerate(['AMAZON_FR_REVIEW', 'FORUM_FR','YOUTUBE_COMMENT'],1):
            obs.append({'observation_id':f'OBS-{i}','candidate_ids':['C'], 'market_geo':'FR','kind':'FRUSTRATION',
                        'source_type':source,'source_url':f'https://source.example/{i}', 'author_key':'SameUser'})
        r=ledger.gate_2b(obs,'C',target_market='FR')
        self.assertEqual(r['status'],'FAIL')
        self.assertEqual(r['independent_persons'],1)

    def test_unknown_authors_cannot_invent_person_independence(self):
        obs=[]
        for i, source in enumerate(['AMAZON_FR_REVIEW', 'FORUM_FR','YOUTUBE_COMMENT'],1):
            obs.append({'observation_id':f'OBS-{i}','candidate_ids':['C'], 'market_geo':'FR','kind':'FRUSTRATION',
                        'source_type':source,'source_url':f'https://source.example/{i}'})
        r=ledger.gate_2b(obs,'C',target_market='FR')
        self.assertEqual(r['status'],'EN_ATTENTE')
        self.assertEqual(r['independent_persons'],0)
        self.assertEqual(len(r['independence_unverified_observations']),3)

    def test_two_independent_authors_and_three_families_still_pass(self):
        obs=[]
        for i,(source,author) in enumerate(zip(['AMAZON_FR_REVIEW','FORUM_FR','YOUTUBE_COMMENT'],['A','B','A']),1):
            obs.append({'observation_id':f'OBS-{i}', 'candidate_ids':['C'], 'market_geo':'FR', 'kind':'FRUSTRATION',
                        'source_type':source,'source_url':f'https://source.example/{i}','author_key':author})
        r=ledger.gate_2b(obs,'C',target_market='FR')
        self.assertEqual(r['status'],'PASS')
        self.assertEqual(r['independent_persons'],2)

    def test_live_autopilot_rejected_even_when_called_as_python_library(self):
        with self.assertRaisesRegex(ValueError,'LIVE_AUTOPILOT_NOT_SUPPORTED'):
            autopilot.step(autopilot.CandidateRegistry(mission_id='X'),{}, provenance_mode='LIVE')

    def test_repeat_init_same_profile_does_not_overwrite(self):
        import uuid, shutil
        mission='HARDEN_'+uuid.uuid4().hex[:12]
        d=ecom.ROOT/'runs'/mission
        args=Namespace(mission=mission,market='FR',persona='Persona_A',foreign_markets=['US','GB','CA','AU'],mode='REPLAY')
        try:
            ecom.cmd_init(args)
            path=d/'mission.json'
            first=path.read_bytes()
            ecom.cmd_init(args)
            self.assertEqual(path.read_bytes(), first)
        finally:
            shutil.rmtree(d,ignore_errors=True)

    def test_conflicting_init_does_not_change_previous_mission(self):
        import uuid, shutil
        mission='HARDEN_'+uuid.uuid4().hex[:12]
        d=ecom.ROOT/'runs'/mission
        args=Namespace(mission=mission,market='FR',persona='Persona_A',foreign_markets=['US'],mode='REPLAY')
        try:
            ecom.cmd_init(args)
            before=(d/'mission.json').read_bytes()
            args.market='AE'
            with self.assertRaisesRegex(SystemExit,'CONFIG_CONFLICT'):
                ecom.cmd_init(args)
            self.assertEqual(before,(d/'mission.json').read_bytes())
        finally:
            shutil.rmtree(d,ignore_errors=True)

    def test_tt_duplicate_pending_call_reuses_call_id_and_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            t=marketpulse_adapter.MarketPulseAdapter(Path(temp),mode='REPLAY')
            t.ingest_balance({'requestId':'balance','credits':{'totalRemaining':1000}})
            t.record_replay_approval(scope='S',max_credits=16,owner_statement='I authorize offline replay only')
            args=dict(mission_id='M',scope='S',recipe_id='A01_S5_AD_SAMPLE_REACH',country='FR',stage=5,terms=['test'],locale='fr')
            r1=t.plan_call(**args)
            r2=t.plan_call(**args)
            self.assertEqual(r2['status'],'AWAITING_EXECUTION')
            self.assertEqual(r1['call_id'],r2['call_id'])
            self.assertTrue(r2['idempotent_reuse'])
            reservations=json.loads((Path(temp)/'STATE'/'reservations.json').read_text())
            self.assertEqual(len(reservations),1)

    def test_tt_distinct_queries_create_distinct_pending_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            t=marketpulse_adapter.MarketPulseAdapter(Path(temp),mode='REPLAY')
            t.ingest_balance({'requestId':'balance','credits':{'totalRemaining':2000}})
            t.record_replay_approval(scope='S',max_credits=40,owner_statement='I authorize offline replay only')
            args=dict(mission_id='M',scope='S',recipe_id='A01_S5_AD_SAMPLE_REACH',country='FR',stage=5,terms=['test'],locale='fr')
            a=t.plan_call(**args)
            b=t.plan_call(**{**args,'terms':['distinct']})
            self.assertNotEqual(a['call_id'],b['call_id'])
            self.assertEqual(len(json.loads((Path(temp)/'STATE'/'reservations.json').read_text())),2)

if __name__=='__main__':unittest.main()
