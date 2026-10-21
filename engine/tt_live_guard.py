"""Barrière MCP du gouverneur MarketPulse : hook LLM_Provider runtime PreToolUse / PostToolUse.

Exécuté par LLM_Provider runtime (pas par LLM_Provider) avant et après chaque outil `mcp__*` :
- PreToolUse : refus par défaut. Seuls `check_credits` / `usage_get` passent librement. Les trois outils payants
  ne passent qu'en `ask` (confirmation humaine dans l'interface) et seulement si une réservation du moteur porte
  exactement les mêmes paramètres, sous approbation signée par la clé épinglée. Tout autre outil MCP est refusé.
- PostToolUse : capture le solde réel (outils gratuits) et la réponse réelle (outils payants) dans la mission.
- PostToolUseFailure : marque l'appel payant FAILED (facturation indéterminée, réservation conservée).
- PermissionRequest (après PreToolUse `ask`) : `allow` seulement pour une réservation AUTHORIZED identique sous
  approbation signée `autonomous` liée au plan inchangé, une seule fois ; sinon aucune décision (confirmation humaine).
  Erreur, délai ou sortie non nulle : aucune décision (vérifié sur LLM_Provider runtime 2.1.304 : délai dépassé = pas d'allow).
Un refus manuel dans LLM_Provider runtime ne déclenche aucun hook (PermissionDenied ne couvre que le mode auto, v2.1.304) :
l'appel reste AUTHORIZED et bloque toute dépense jusqu'à une résolution signée par le propriétaire.
Toute exception en PreToolUse produit un refus; la commande du hook ajoute `|| exit 2` si Python échoue.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from engine import marketpulse_adapter as tta  # noqa: E402

FREE_MCP_TOOLS = frozenset({"check_credits", "usage_get"})


def _decision(kind: str, reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind,
                                   "permissionDecisionReason": f"ECOM OS: {reason}"}}


def _live_missions(runs: Path):
    for cfg_path in sorted(runs.glob("*/mission.json")):
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        tdir = cfg_path.parent / "MARKETPULSE"
        if cfg.get("mode") == "FETCH_VERIFIED" and (tdir / "STATE" / "live_approvals").is_dir():
            yield tdir


def _adapter(tdir: Path, pubkey: Path, pin: str) -> tta.MarketPulseAdapter:
    return tta.MarketPulseAdapter(tdir, mode=tta.LIVE_MODE, owner_pubkey=pubkey, owner_pubkey_sha256=pin)


def pre_tool_use(event: dict, runs: Path, pubkey: Path, pin: str) -> dict | None:
    name = str(event.get("tool_name") or "")
    if not name.startswith("mcp__"):
        return None
    if not name.startswith(tta.MCP_TOOL_PREFIX):
        return _decision("deny", f"MCP_TOOL_NOT_ALLOWLISTED:{name}")
    tool = name[len(tta.MCP_TOOL_PREFIX):]
    if tool in FREE_MCP_TOOLS:
        for tdir in _live_missions(runs):
            _adapter(tdir, pubkey, pin).note_balance_request(str(event.get("tool_use_id") or ""))
        return _decision("allow", f"FREE_USAGE_READ:{tool}")
    if tool not in tta.LIVE_TOOLS:
        return _decision("deny", f"MARKETPULSE_TOOL_NOT_ALLOWLISTED:{tool}")
    if not pin or not pubkey.is_file() or tta.sha256_file(pubkey) != pin:
        return _decision("deny", "OWNER_PUBKEY_MISSING_OR_PIN_MISMATCH")
    reasons = []
    for tdir in _live_missions(runs):
        r = _adapter(tdir, pubkey, pin).authorize_dispatch(tool, event.get("tool_input") or {}, event.get("tool_use_id"))
        if r["decision"] == "ask":
            return _decision("ask", r["reason"])
        if r["decision"] == "deny":
            reasons.append(f"{tdir.parent.name}:{r['reason']}")
    return _decision("deny", "; ".join(reasons) or "NO_RESERVATION_WITH_IDENTICAL_PARAMS")


def post_tool_use(event: dict, runs: Path, pubkey: Path, pin: str) -> dict:
    name = str(event.get("tool_name") or "")
    if not name.startswith(tta.MCP_TOOL_PREFIX):
        return {"status": "IGNORED"}
    tool = name[len(tta.MCP_TOOL_PREFIX):]
    missions = list(_live_missions(runs))
    if tool in FREE_MCP_TOOLS:
        payload = tta.extract_mcp_payload(event.get("tool_response"))
        captured = 0
        for tdir in missions:
            try:
                _adapter(tdir, pubkey, pin).capture_hook_balance(payload or {}, event.get("tool_use_id"))
                captured += 1
            except Exception:
                pass
        return {"status": "BALANCE_CAPTURED", "missions": captured}
    if tool in tta.LIVE_TOOLS:
        for tdir in missions:
            r = _adapter(tdir, pubkey, pin).record_response(tool, event.get("tool_input") or {}, event.get("tool_response"),
                                                            event.get("tool_use_id"))
            if r["status"] == "RECORDED":
                return r
        for tdir in missions:
            _adapter(tdir, pubkey, pin).halt_unmatched(tool)
        return {"status": "HALTED_UNMATCHED"}
    return {"status": "IGNORED"}


def post_tool_use_failure(event: dict, runs: Path, pubkey: Path, pin: str) -> dict:
    name = str(event.get("tool_name") or "")
    tool = name[len(tta.MCP_TOOL_PREFIX):] if name.startswith(tta.MCP_TOOL_PREFIX) else ""
    if tool not in tta.LIVE_TOOLS:
        return {"status": "IGNORED"}
    for tdir in _live_missions(runs):
        r = _adapter(tdir, pubkey, pin).record_failure(tool, event.get("tool_input") or {}, event.get("tool_use_id"),
                                                       event.get("error") or "", event.get("is_interrupt"))
        if r["status"] == "RECORDED_FAILURE":
            return r
    return {"status": "NO_MATCH"}


def permission_request(event: dict, runs: Path, pubkey: Path, pin: str) -> dict | None:
    name = str(event.get("tool_name") or "")
    if not name.startswith(tta.MCP_TOOL_PREFIX):
        return None
    tool = name[len(tta.MCP_TOOL_PREFIX):]
    if tool not in tta.LIVE_TOOLS or not pin or not pubkey.is_file() or tta.sha256_file(pubkey) != pin:
        return None
    for tdir in _live_missions(runs):
        r = _adapter(tdir, pubkey, pin).permission_request(tool, event.get("tool_input") or {})
        if r.get("decision") == "allow":
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}
    return None


def main(argv: list[str] | None = None, stdin=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("event", choices=["pre", "post", "failure", "permission"])
    ap.add_argument("--pubkey", type=Path, default=ROOT / ".claude" / "ecom_owner_pubkey.pem")
    ap.add_argument("--pubkey-sha256", default="")
    ap.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    args = ap.parse_args(argv)
    stdin = stdin or sys.stdin
    if args.event == "permission":
        try:
            out = permission_request(json.loads(stdin.read()), args.runs_dir, args.pubkey, args.pubkey_sha256)
        except Exception:  # aucune décision : la confirmation humaine reste affichée
            out = None
        if out is not None:
            print(json.dumps(out, ensure_ascii=False))
        return 0
    if args.event == "pre":
        try:
            out = pre_tool_use(json.loads(stdin.read()), args.runs_dir, args.pubkey, args.pubkey_sha256)
        except Exception as exc:  # fail-closed
            out = _decision("deny", f"GUARD_ERROR:{type(exc).__name__}")
        if out is not None:
            print(json.dumps(out, ensure_ascii=False))
        return 0
    if args.event == "failure":
        try:
            post_tool_use_failure(json.loads(stdin.read()), args.runs_dir, args.pubkey, args.pubkey_sha256)
        except Exception as exc:
            print(f"ECOM OS guard failure-hook error: {type(exc).__name__}", file=sys.stderr)
            return 2
        return 0
    try:
        res = post_tool_use(json.loads(stdin.read()), args.runs_dir, args.pubkey, args.pubkey_sha256)
    except Exception as exc:
        print(f"ECOM OS guard post error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if res.get("status") == "HALTED_UNMATCHED":
        print("ECOM OS: appel payant sans réservation envoyée; moteur MarketPulse gelé.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
