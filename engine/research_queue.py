"""Mission-specific non-LIVE research handoff, designed for LLM_Provider runtime's own approved Web tools.

This module does not fetch the web and its records NEVER imply verified observations.
"""
from __future__ import annotations
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime, timezone
import json
import hashlib
import ipaddress


def _safe_https(url: str) -> bool:
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or not p.hostname or p.username or p.password:
            return False
        host = p.hostname.casefold()
        if host == 'localhost' or host.endswith('.localhost') or host.endswith('.local'):
            return False
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_global
        except ValueError:
            return True
    except (ValueError, TypeError):
        return False


def make_research_kit(mdir: Path, market: str, persona: str) -> dict:
    mdir = Path(mdir)
    dst = mdir/'RESEARCH_INBOX'
    dst.mkdir(parents=True, exist_ok=True)
    manifest_path = dst/'RESEARCH_BRIEF.json'
    brief = {
        'schema': 'ECOM_OS_RESEARCH_BRIEF_V1', 'mission_id': mdir.name,
        'market': market, 'persona_hypothesis': persona,
        'status': 'COLLECTION_PENDING',
        'pipeline': [
            {'stage':'GATE_2A', 'objective':'Map 8 territories; microcontexts = time × place × friction; never start with SKU'},
            {'stage':'GATE_2B', 'objective':'Get authentic firsthand frustration + distinct authors and 3 source families on target market'},
            {'stage':'GATE_3', 'objective':'Shortlist 5–8 JTBD; document exclusions and costs of workarounds'},
            {'stage':'MARKET_SCORE_40', 'objective':'8 dimensions each with evidence; hypotheses remain hypotheses'},
            {'stage':'A02', 'objective':'Benchmark substitutes, direct competitors, foreign ideas and falsifying counterexamples'},
            {'stage':'SOLUTION_FILTER_14', 'objective':'No YES without specific verified material evidence; unknown is allowed'},
        ],
        'sources_to_try': ['marketplace reviews','relevant forums and communities','customer video comments','independent reviews','search demand indicators','competitor PDP and ads'],
        'minimum_source_record': ['url','observed_at','market_geo','source_class','entity_scope','exact_observation','author_handle_if_visible','capture_method','verification_state'],
        'tooling': 'LLM_Provider runtime may browse with approved tools. This Python CLI cannot verify web facts or spend credits.',
        'provenance': 'UNVERIFIED_RESEARCH_NOT_LIVE_EVIDENCE',
    }
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(brief,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    return {'status':'RESEARCH_KIT_READY', 'brief_file':str(manifest_path), 'market':market, 'mission_id':mdir.name,
            'warning':'No external sources fetched. Stage external captures for review; do not promote to LIVE.'}


def stage_unverified_capture(mdir: Path, row: dict) -> dict:
    """Quarantine a claimed web capture. No authority to call it LIVE or to pass gates."""
    if not isinstance(row, dict):
        return {'status':'REJECTED_CAPTURE', 'issues':['NOT_OBJECT']}
    issues=[]
    url=row.get('url')
    if not isinstance(url,str) or not _safe_https(url):
        issues.append('REQUIRES_PUBLIC_HTTPS_URL')
    quote=row.get('exact_observation')
    if not isinstance(quote,str) or len(quote.strip()) < 15 or len(quote) > 3000:
        issues.append('EXACT_OBSERVATION_INVALID')
    if not str(row.get('market_geo') or '').strip(): issues.append('MARKET_MISSING')
    if not str(row.get('capture_method') or '').strip(): issues.append('CAPTURE_METHOD_MISSING')
    if row.get('verified_live') is True or str(row.get('provenance_mode') or '').upper() == 'LIVE':
        issues.append('UNTRUSTED_LIVE_ASSERTION')
    if issues: return {'status':'REJECTED_CAPTURE', 'issues':issues}
    clean={
        'mission_id': Path(mdir).name,'url':url, 'exact_observation':quote.strip(),
        'market_geo':row['market_geo'], 'source_class':str(row.get('source_class') or 'UNKNOWN'),
        'entity_scope':str(row.get('entity_scope') or 'UNKNOWN'),
        'capture_method':row['capture_method'], 'author_handle_if_visible':row.get('author_handle_if_visible'),
        'observed_at':row.get('observed_at') or datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'provenance_mode':'UNVERIFIED', 'verified_live':False, 'approved_for_gate':False,
    }
    # Idempotent capture identity: same page + same quote, irrespective of capture timestamp.
    stable={'url': clean['url'].split('#')[0], 'observation':clean['exact_observation'].casefold()}
    cid=hashlib.sha256(json.dumps(stable,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    path=Path(mdir)/'RESEARCH_INBOX'/(cid+'.unverified.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists(): return {'status':'CAPTURE_ALREADY_STAGED','file':str(path),'approved_for_gate':False}
    path.write_text(json.dumps(clean,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    return {'status':'CAPTURE_STAGED_UNVERIFIED','file':str(path),'approved_for_gate':False}
