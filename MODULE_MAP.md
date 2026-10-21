# MODULE MAP — ECOM OS

| Composant | Propriétaire | État réel |
|---|---|---|
| CLI et missions | `ecom.py` | REPLAY, validation IDs et configuration non destructive |
| Recherche LLM_Provider runtime hors réseau CLI | `engine/research_queue.py` | briefs, captures en quarantaine `UNVERIFIED` |
| Collecte vérifiée par le moteur | `engine/web_capture.py` | vérification GÉNÉRIQUE de toute page publique (aucune liste de domaines) : HTTPS, hôte public (DNS), robots.txt, pas de redirection inter-hôtes, détection connexion/CAPTCHA ; rattachement auteur ADAPTER/STRUCTURED/PROXIMITY/UNBOUND ; prix et offres publics `PRICE_OBSERVED`/`OFFER_OBSERVED` ; traductions/syndications exclues |
| Recherche autonome (≤ GATE 4) | `engine/autonomous_research.py` | promotion, JTBD, candidats, dossiers, pilote, rapport, ordres de travail, rapport profondeur Stage 1/2 |
| Persona | `engine/persona.py`, `research/personas/PERSONA_A_FR.json` | mode DISCOVERED par défaut (segments émergents à partir d'attributs auto-déclarés vérifiés) ; mode PROFILE seulement sur demande explicite (`--persona PERSONA_A_FR`) avec GATE 2A persona et fit démontré |
| DISCOVERY | `engine/discovery.py` | registre `DISCOVERED_UNVERIFIED` de toute source vue (tout site), pages inaccessibles conservées avec la raison, GATE 2A : sources découvertes, ≥ 5 secteurs, ≥ 5 domaines |
| Adaptateurs facultatifs | `engine/source_registry.py`, `contracts/source_registry.json`, `research/source_proposals/` | extracteurs de haute confiance (niveau ADAPTER) pour quelques sites ; jamais une condition d'admission |
| Géographie FR | `engine/geo_fr.py` | 101 départements, régions, villes ; noms sans accents, codes « (35) » ; jamais la langue ou un domaine .fr |
| Profondeur de la demande | `engine/demand.py`, `contracts/research_thresholds.json` | 10 indicateurs (sites, pages, contributions, intervalle d'auteurs, unicité, répartition, concentration, persistance, contextes, contre-signaux) ; voies témoignages / statistique séparées ; niveaux HYPOTHESIS → SIGNIFICANT_MARKET ; GATE 2B = convergence |
| Enquête et verdicts | `engine/research_depth.py`, `research/RESEARCH_PLAYBOOK.md` | signature des micro-problèmes (rapprochement sans fusion), accumulation multi-sources, saturation et suivi persistant, Agent 02 par micro, 7 verdicts, ordres ADAPTIVE_DEEPEN, arrêt avant MarketPulse |
| Territoires et JTBD | `engine/product_research_pipeline.py` | validateurs REPLAY; dossier de décision et healthcheck |
| VoC / GATE 2B | `engine/ledger.py` | REPLAY, déduplication auteur / texte |
| Score /40 | `engine/scoring.py` | 8 dimensions du processus maître, notes plafonnées par le niveau de preuve ; legacy économique isolé en REPLAY |
| Agent 02 | `engine/competitor_intel.py` | contre-enquête (≥ 5 requêtes FR, ≥ 2 réfutations, avis positifs/négatifs), prix et offres publics vérifiés, saturation, verdicts ; audit REPLAY /25 conservé pour les fixtures |
| Automate stages / 14Q | `engine/autopilot.py` | REPLAY, permissions réelles désactivées |
| Adaptateur MarketPulse | `engine/marketpulse_adapter.py`, `engine/tt_live_guard.py` | REPLAY/OUTBOX; MCP_SESSION lecture seule sous approbation signée, hook de barrière MCP (Pre/Post/PostToolUseFailure/PermissionRequest), règlement par solde lu après la réponse, calibrage + correspondance des champs signée (chemins pointés) ; intelligence concurrentielle : recettes S1–S4, qualification par lexique de mission, nouveauté inter-appels, pilote de plan `tt next`/`tt wave` avec règles d'arrêt, livrables `tt report`, mode autonome signé lié au plan (`docs/MARKETPULSE_INTELLIGENCE.md`) |
| Agent 03 | `engine/creative_center.py` | brouillons et Higgsfield REPLAY |
| Agents 04–06 | `engine/downstream_agents.py` | non prioritaires / plans offline seulement |
| Sources documentaires | `reference_corpus/` | 142 fichiers inchangés |
| Nectar index | `knowledge/nectar_index.json` | recherche ciblée |
| Routage LLM_Provider runtime | `CLAUDE.md`, `.claude/skills/`, `.claude/agents/`, `.claude/rules/` | instructions contextualisées, accès appliqué par LLM_Provider runtime sur Mac |
| Démarrage Mac | `DEMARRER_MAC.command`, `INSTALL_MAC.md` | lancement POSIX sans installation, non validé macOS réel |

Mode LIVE, connecteurs payants et autorisations commerciales : **désactivés**.
