"""Offline Product-First intake and review outputs for ECOM OS B3.

No network access, no real-provider claims, no payment, and no permission grants.
Real-world collection must be carried out by a suitably permissioned external tool,
then provenance reviewed by a human before LIVE ingestion is enabled in a future version.
"""
from __future__ import annotations

import datetime as dt
import json
import hashlib
from pathlib import Path
import platform
import sys
from urllib.parse import urlsplit

from . import autopilot, ledger

FORBIDDEN_SKU_KEYS = frozenset({
    'selected_sku', 'recommended_product', 'winning_product', 'chosen_product_id',
    'sku_shortlist', 'supplier_sku',
})


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def register_territories(mdir: Path, territories: list[dict], provenance_mode: str = 'REPLAY') -> dict:
    """Conservatively validate GATE 2A: 8 distinct territories, micro-contexts, no early SKU."""
    if not isinstance(territories, list):
        raise ValueError('TERRITORIES_MUST_BE_LIST')
    issues, seen_ids, seen_names, clean = [], set(), set(), []
    for idx, t in enumerate(territories):
        if not isinstance(t, dict):
            issues.append(f'TERRITORY_{idx}_NOT_OBJECT')
            continue
        tid, name = str(t.get('territory_id') or '').strip(), str(t.get('name') or '').strip()
        contexts = t.get('micro_contexts')
        if FORBIDDEN_SKU_KEYS.intersection(t):
            issues.append(f'PREMATURE_SKU_IN_TERRITORY:{tid or idx}')
        if not tid or not name or not isinstance(contexts, list) or not contexts:
            issues.append(f'TERRITORY_INVALID:{tid or idx}')
            continue
        if tid.casefold() in seen_ids or name.casefold() in seen_names:
            issues.append(f'DUPLICATE_TERRITORY:{tid}')
            continue
        seen_ids.add(tid.casefold())
        seen_names.add(name.casefold())
        for j, ctx in enumerate(contexts):
            if not isinstance(ctx, dict) or not all(str(ctx.get(k) or '').strip() for k in ('moment', 'place', 'friction')):
                issues.append(f'INVALID_MICROCONTEXT:{tid}:{j}')
            if isinstance(ctx, dict) and FORBIDDEN_SKU_KEYS.intersection(ctx):
                issues.append(f'PREMATURE_SKU_IN_MICROCONTEXT:{tid}:{j}')
        clean.append({'territory_id': tid, 'name': name, 'micro_contexts': contexts,
                      'regular_activities': t.get('regular_activities', []),
                      'frictions': t.get('frictions', []),
                      'existing_spend_areas': t.get('existing_spend_areas', [])})
    if len(clean) < 8:
        issues.append(f'TERRITORIES_UNDER_EIGHT:{len(clean)}/8')
    out = {'gate': 'GATE_2A', 'status': f'PASS_{provenance_mode}_STRUCTURE_ONLY' if not issues else 'GATE_2A_INCOMPLETE',
           'issues': sorted(set(issues)), 'distinct_territories_count': len(clean),
           'territories': clean, 'provenance_mode': provenance_mode, 'updated_at': _now()}
    # Never overwrite a previously passing file on failed validation.
    if not issues:
        dest = Path(mdir)/'AGENT_01'/'territory_map.json'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return out


def register_jtbd_shortlist(mdir: Path, jobs: list[dict], not_retained: list[dict]|None=None, provenance_mode: str = 'REPLAY') -> dict:
    """Structure 5–8 JTBD and auditable exclusions; no SKU before stage 7."""
    if not isinstance(jobs, list) or not isinstance(not_retained or [], list):
        raise ValueError('JTBD_PAYLOAD_MUST_CONTAIN_LISTS')
    idx = ledger.load_by_id(Path(mdir)/'AGENT_01'/'problem_ledger.jsonl')
    issues, clean, excluded, ids = [], [], [], set()
    if not 5 <= len(jobs) <= 8:
        issues.append(f'JTBD_COUNT_OUTSIDE_5_TO_8:{len(jobs)}')
    for j in jobs:
        if not isinstance(j, dict):
            issues.append('JTBD_NOT_OBJECT')
            continue
        jid, title = str(j.get('id') or '').strip(), str(j.get('title') or '').strip()
        if FORBIDDEN_SKU_KEYS.intersection(j):
            issues.append(f'PREMATURE_SKU_SELECTION_AT_STEP_4:{jid}')
        if not jid or not title or jid in ids:
            issues.append(f'JTBD_ID_INVALID_OR_DUPLICATE:{jid}')
        ids.add(jid)
        refs = j.get('voc_refs') or []
        if not isinstance(refs, list) or not refs or not set(refs).issubset(idx):
            issues.append(f'JTBD_WITHOUT_VALID_LEDGER_REFS:{jid}')
        if not str(j.get('workaround_cost') or '').strip():
            issues.append(f'JTBD_MISSING_WORKAROUND_COST:{jid}')
        clean.append(dict(j))
    for j in not_retained or []:
        if not isinstance(j, dict):
            issues.append('EXCLUDED_JTBD_NOT_OBJECT')
            continue
        jid = str(j.get('id') or '').strip()
        refs = j.get('closed_refs') or []
        if not jid or not str(j.get('closed_reason') or '').strip() or not isinstance(refs, list) or not refs or not set(refs).issubset(idx):
            issues.append(f'EXCLUDED_JTBD_MISSING_REASON_OR_REFS:{jid}')
        excluded.append(dict(j))
    out = {'gate':'GATE_3', 'status':f'PASS_{provenance_mode}_STRUCTURE_ONLY' if not issues else 'GATE_3_INCOMPLETE',
           'issues':sorted(set(issues)), 'jobs':clean, 'not_retained_at_step4':excluded,
           'provenance_mode':provenance_mode, 'updated_at':_now()}
    if not issues:
        dest = Path(mdir)/'AGENT_01'/'jtbd_ranked.json'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return out


def collect_public_web_source(mdir: Path, *, url: str, purpose: str) -> dict:
    """Return an explicit read-only research handoff; does NOT fetch, scrape or mint LIVE evidence."""
    p = urlsplit(str(url or '').strip())
    host = p.hostname or ''
    if p.scheme != 'https' or not host or p.username or p.password or host.casefold() in ('localhost','127.0.0.1','0.0.0.0','::1'):
        return {'status':'BLOCKED_SOURCE','reason':'UNSAFE_OR_NON_HTTPS_URL','url':url}
    return {'status':'BLOCKED_SOURCE','reason':'READ_ONLY_WEB_CONNECTOR_NOT_IMPLEMENTED',
            'url':url, 'collection_task':{'purpose':purpose,'url':url,
            'required_fields':['source_url','observed_at','author_key','phrase_exacte','source_type','market_geo'],
            'instructions':'LLM_Provider runtime may use a separately authorized browser tool. Do not claim that this CLI collected or validated the page.'},
            'provenance_mode':'UNVERIFIED','never_invent_live_result':True}


def generate_decision_dossier(mdir: Path, candidate_id: str|None=None) -> dict:
    """Create a readable *review dossier*, not a commercial approval. Preserves all source files."""
    mdir=Path(mdir)
    conf_path=mdir/'mission.json'
    if not conf_path.exists():
        return {'status':'BLOCKED_MISSION_NOT_INITIALIZED'}
    conf=json.loads(conf_path.read_text(encoding='utf-8'))
    reg=autopilot.CandidateRegistry.load(mdir/'AGENT_01'/'candidate_registry.json',conf['mission_id'])
    selected=next((c for c in reg.candidates.values() if c.get('status')=='PROPOSED_FOR_COMMERCIAL_SELECTION'),None)
    if candidate_id:
        cand=reg.candidates.get(candidate_id)
    else:
        cand=selected or next(iter(reg.candidates.values()),None)
    dossiers_path=mdir/'AGENT_01'/'dossiers.json'
    dossiers=json.loads(dossiers_path.read_text(encoding='utf-8')) if dossiers_path.exists() else {}
    cid=cand.get('id') if cand else None
    dos=dossiers.get(cid,{})
    led_path=mdir/'AGENT_01'/'problem_ledger.jsonl'
    obs=ledger.load(led_path)
    a02_path=mdir/'AGENT_02'/'competitor_audit_report.json'
    a02=json.loads(a02_path.read_text(encoding='utf-8')) if a02_path.exists() else {}
    filt=dos.get('product_filter') or {}
    select_id=cand.get('selected_solution_id') if cand else None
    decision={'schema':'ECOM_OS_B3_DECISION_DOSSIER','mission_id':conf['mission_id'],
              'target_market':conf.get('target_sale_market'), 'persona':conf.get('persona'),
              'provenance_mode':conf.get('mode','UNKNOWN'),'verified_live_evidence':False,
              'candidate_id':cid,'candidate_status':cand.get('status') if cand else None,
              'selected_solution_id':select_id,'score_40':dos.get('score'),
              'gate2b':dos.get('gate2b'), 'economic_screen':dos.get('economic_screen'),
              'solution_candidates':dos.get('solutions',[]),
              'product_filter_raw':filt.get(select_id,{}) if select_id else {},
              'agent02_status':a02.get('status'),
              'agent02_gaps':a02.get('evaluated_gaps',[]),
              'agent02_solution_audits':a02.get('solution_audits',{}),
              'evidence_refs':[r.get('observation_id') for r in obs],
              'evidence_count':len(obs),'risks':['REPLAY_SIMULATIONS_ARE_NOT_VERIFIED_MARKET_PROOF']
                     if conf.get('mode')=='REPLAY' else (['FETCH_VERIFIED_PROVISIONAL_NOT_LIVE_CERTIFIED',
                     'AUTHOR_IDENTITY_AND_RESIDENCE_NOT_VERIFIED'] if conf.get('mode')=='FETCH_VERIFIED'
                     else ['LIVE_VERIFICATION_NOT_IMPLEMENTED']),
              'next_action':'COLLECT_AND_VERIFY_REAL_EVIDENCE_WITH_AUTHORIZED_TOOLS',
              'commercially_approved':False, 'generated_at':_now()}
    folder=mdir/'DECISION'; folder.mkdir(parents=True,exist_ok=True)
    (folder/'DECISION_DOSSIER.json').write_text(json.dumps(decision,indent=2,ensure_ascii=False,default=str)+'\n',encoding='utf-8')
    lines=['# Dossier de décision — ECOM OS CLEAN B3','',
           f"**Mission :** {conf['mission_id']} | **Marché :** {conf.get('target_sale_market','?')}",
           f"**Mode :** {conf.get('mode','UNKNOWN')} — données LIVE non certifiées",
           f"**JTBD :** {cid or 'Aucun candidat'}",
           f"**État du candidat :** {decision['candidate_status'] or 'Aucun'}",
           f"**Solution proposée :** {select_id or 'Aucune'}",
           f"**Observations enregistrées :** {len(obs)} (non vérifiées en externe)",
           f"**Agent 02 :** {a02.get('status','Pas encore audité')}",
           '', '## Décision','',
           'Il ne s’agit **PAS** d’une approbation commerciale ni d’une recherche LIVE validée.',
           'La sélection éventuellement présentée provient du scénario REPLAY.',
           '', '## Risques et prochaine étape','',
           '- Vérifier les témoignages, prix, concurrents et fournisseurs auprès des sources originales.',
           '- Ne pas passer de commande ni dépenser de crédits depuis ce dossier.',
           '', 'Voir `DECISION_DOSSIER.json` pour les références et scores complets.','']
    (folder/'DECISION_DOSSIER.md').write_text('\n'.join(lines), encoding='utf-8')
    return {'status':'DRAFT_REPLAY_NOT_COMMERCIAL_APPROVAL','json':str(folder/'DECISION_DOSSIER.json'),
            'markdown':str(folder/'DECISION_DOSSIER.md'), 'candidate_id':cid}


def healthcheck(root:Path) -> dict:
    root=Path(root)
    # Finder creates .DS_Store / AppleDouble files; bytecode caches are not source documents.
    refs=[p for p in (root/'reference_corpus').rglob('*')
          if p.is_file() and p.name != '_EXTRACTION_MANIFEST.json'
          and p.name not in ('.DS_Store','Thumbs.db') and not p.name.startswith('._')
          and '__pycache__' not in p.parts and '__MACOSX' not in p.parts
          and p.suffix not in ('.pyc', '.pyo')]
    required=['ecom.py','CLAUDE.md','MODULE_MAP.md','engine/autopilot.py','engine/ledger.py',
              'engine/scoring.py','engine/competitor_intel.py','engine/creative_center.py',
              'engine/marketpulse_adapter.py','.claude/skills/agent01-research/SKILL.md',
              '.claude/skills/agent02-competitor/SKILL.md',
              '.claude/skills/ecom-os/SKILL.md', 'engine/research_queue.py',
              'DEMARRER_MAC.command', 'INSTALL_MAC.md',
              # INTEL_V2
              'engine/geo_fr.py', 'engine/persona.py', 'engine/demand.py', 'engine/discovery.py',
              'engine/source_registry.py', 'contracts/source_registry.json', 'contracts/research_thresholds.json',
              'contracts/research_policy.json', 'research/personas/PERSONA_A_FR.json']
    missing=[f for f in required if not (root/f).is_file()]
    v=sys.version_info
    version_ok=(3,11)<=v[:2]<(4,0)
    issues=[]
    if not missing:
        try:
            from . import demand, persona, source_registry
            source_registry.load_active(root/'contracts/source_registry.json')
            demand.load_thresholds(root/'contracts/research_thresholds.json')
            persona.load_profile('PERSONA_A_FR')
        except (OSError, ValueError, KeyError) as exc:
            issues.append(f'INTEL_V2_CONTRACT_INVALID:{exc}')
    if missing:issues.append(f'MISSING_FILES:{missing}')
    if len(refs)!=142:issues.append(f'REFERENCE_FILES_COUNT:{len(refs)}_EXPECTED_142')
    # Corpus integrity and nested instruction isolation: a file count alone is insufficient.
    manifest_path = root/'reference_corpus/_EXTRACTION_MANIFEST.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        original_paths = manifest['extracted_files']
        relocated = manifest['archived_instruction_path_map']
        checksums = manifest['source_sha256_by_original_path']
        if len(original_paths) != 142 or len(set(original_paths)) != 142 or set(checksums) != set(original_paths):
            issues.append('CORPUS_MANIFEST_INCONSISTENT')
        for original in original_paths:
            real_rel = relocated.get(original, original)
            p = root/'reference_corpus'/real_rel
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != checksums.get(original):
                issues.append(f'CORPUS_SOURCE_CHANGED:{original}')
        for old in relocated:
            if (root/'reference_corpus'/old).exists():
                issues.append(f'LEGACY_INSTRUCTION_ACTIVATED:{old}')
        for fp in (root/'reference_corpus').rglob('*'):
            if fp.is_file() and fp.name in ('CLAUDE.md','AGENTS.md','CLAUDE.local.md'):
                issues.append(f'NESTED_INSTRUCTION_ACTIVE:{fp.relative_to(root)}')
            if fp.is_file() and '.claude' in fp.parts:
                issues.append(f'NESTED_AGENT_CONFIG_ACTIVE:{fp.relative_to(root)}')
    except (OSError,ValueError,TypeError,KeyError) as exc:
        issues.append(f'CORPUS_MANIFEST_INVALID:{type(exc).__name__}')

    if not version_ok:issues.append('PYTHON_3_11_PLUS_REQUIRED')
    try:
        conf=json.loads((root/'.claude/settings.json').read_text(encoding='utf-8'))
        if not isinstance(conf.get('permissions',{}).get('allow',[]),list):
            issues.append('AGENT_SETTINGS_MALFORMED')
    except (OSError,ValueError,TypeError):
        issues.append('AGENT_SETTINGS_MISSING_OR_INVALID')
    try:
        index=json.loads((root/'knowledge/nectar_index.json').read_text(encoding='utf-8'))
        if not index:
            issues.append('NECTAR_INDEX_EMPTY')
    except (OSError,ValueError):
        issues.append('NECTAR_INDEX_MISSING_OR_INVALID')
    return {'status':'REPLAY_READY' if not issues else 'BLOCKED_HEALTHCHECK','python':sys.version.split()[0],
            'os':platform.platform(), 'source_files':len(refs),'required_files_present':len(required)-len(missing),
            'issues':issues,'live_mode':'DISABLED_NO_VERIFIED_CONNECTOR','macos_native_test':'NOT_EXECUTED_IN_THIS_ENVIRONMENT'}
