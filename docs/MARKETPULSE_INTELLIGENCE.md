# MarketPulse — intelligence concurrentielle (moteur réutilisable + plan de mission)

Statut : implémenté et testé hors ligne (réponses fictives à la forme des calibrages réels du 2026-10-20) ; mécanisme
autonome vérifié de bout en bout sur LLM_Provider runtime 2.1.304 avec un faux serveur MCP local. Aucune recherche réelle conduite.
Références méthodologiques : `MARKETPULSE_TOOLKIT_V023.md`, `PLAYBOOK_MARKETPULSE.md` (archives non instructives).

## 1. Séparation moteur / mission
| Moteur (`engine/marketpulse_adapter.py`) | Mission (`runs/<M>/TT_INVESTIGATION_PLAN.json`) |
|---|---|
| Recettes, paramètres MCP exacts, coût maximal, pagination contrôlée | Marchés, formulations, facettes (problème, mécanisme, avatar, promesse, synonyme) |
| Qualification (`qualify_row`), nouveauté inter-appels, rendement | Lexiques : termes problème, mécanisme, avatar, substituts, contextes alternatifs, ancres |
| Pilote `next_call` / règles d'arrêt / budgets par enveloppe | Lots S1–S4, vagues, conditions, budgets, exclusions de domaines |
| Gouvernance : approbation signée, hooks, réserve, réconciliation | Décisions APPROFONDIR / INCERTAIN / ÉCARTER (Agent 02 / propriétaire) |

Aucun marché n'est codé dans le moteur (test `test_recherche_07_plan_is_valid_and_within_500`).

## 2. Recettes LIVE (paramètres des schémas MCP réels uniquement)
| Recette | Stratégie | Paramètres ajoutés | Objectif analytique | Coût max |
|---|---|---|---|---|
| `A01_S5_AD_DENSITY_PROBE` / `A02_FR_COUNTER_PROBE` | S1 | `status=all`, `sort_by=relevance`, limit 1 | Compteur d'index + pertinence du 1er résultat par formulation | 2 (0 si vide) |
| `A01_S5_AD_SAMPLE_REACH` | S2 | `status=active`, `sort_by=reach`, `max_ads_per_brand=2` | Acteurs visibles, diversité des annonceurs | 16 |
| `A01_S5_AD_SAMPLE_LONGEST` | S2 | `sort_by=longestRunning`, ≤2/marque | Persistance (≠ profit) | 16 |
| `A01_S5_AD_SAMPLE_GROWTH` | S2 | `sort_by=reachDelta30d`, `reach_period=last30d`, `min_reach=1` (filtre implicite rendu explicite) | Croissance de portée observable | 16 |
| `A01_S5_AD_RECENT_LAUNCHES` | S3 | `sort_by=newest`, `created_after=J-90` | Nouveaux entrants au niveau annonce (premier vu ≠ date de lancement) | 16 |
| `A01_S5_NEW_ENTRANTS` / `A01_S5_ESTABLISHED_ADVERTISERS` | S3 | `search_in=ad_copy`, `sort_by=newest` / `activeAds` | Annonceurs récents / établis | 15 |
| `A01_S5_LINKED_SHOP` | S4 | `match_mode=exact`, aucun filtre pays | Boutique d'un domaine **issu d'une annonce qualifiée pertinente** | 4 |
| `A02_FR_BASELINE_SHOPS` | — | **désactivée en LIVE** | Recherche texte de boutiques hors cible au calibrage (TTC-eb5bb8514d) | — |

Pages : `page` seulement pour search_ads/search_shops (search_advertisers n'a pas de pagination), page suivante proposée
uniquement si la précédente (du même lot, ou du lot source pour un lot V2 `start_page>1`) est pleine et a apporté
≥ `page_min_new_relevant` nouveaux pertinents.

**Limites signalées (non contournées par un filtre supposé)** : sémantique de `country` différente selon l'outil
(audience principale UE pour search_ads, audience pour search_advertisers) ; `meta.activeOnly=true` malgré `status=all`
non élucidé (inclusion des inactives : UNKNOWN) ; search_advertisers ne renvoie aucun texte d'annonce (pertinence
seulement via une annonce pertinente déjà observée) ; listes (`mainLandingPages`, `topCountries`) non mappables.

## 3. Qualification (chaque résultat, consignée dans la preuve)
Verdicts : `RELEVANT`, `RELEVANT_VIA_LINKED_AD`, `AMBIGUOUS_NO_PROBLEM_TERM`, `AMBIGUOUS_ALTERNATIVE_CONTEXT` (terme
d'ancrage présent avec un autre avatar/usage, ex. « incontinence » + « bébé » : jamais confirmé), `NAME_MATCH_UNVERIFIED`,
`UNVERIFIED_NO_TEXT`, `UNLINKED_UNVERIFIED` ; **isolés** (exclus des signaux, `signal_weight=EXCLUDED_OFF_TOPIC`) :
`OFF_TOPIC_CANDIDATE`, `ALTERNATIVE_CONTEXT` (même mot, autre usage, sans ancrage), `GENERALIST_UNLINKED`,
`OUT_OF_MARKET_AUDIENCE` (audience d'annonce observée hors du marché ; jamais appliqué au pays d'origine d'une boutique).
Seuls `RELEVANT`/`RELEVANT_VIA_LINKED_AD` comptent comme nouveaux pertinents et ouvrent une recherche de boutique.
Aussi : correspondances par facette (mots entiers, sans accents), géographie observée (`audience_country` si signé) ou
`REQUEST_FILTER_ONLY`, spécialisation (`GENERALIST` ≥500 produits ou ≥400 annonces actives, `SPECIALIZED_CANDIDATE`),
chaîne de liens (annonce → annonceur → domaine → boutique → produit, maillon absent = `MISSING`/`NOT_LOOKED_UP`),
signaux contraires (substituts, contexte alternatif, audience ou origine hors marché), nouveauté (`NEW`,
`SEEN_BEFORE:<réf>`, `DUPLICATE_IN_CALL`) et classe de fiabilité de chaque champ (`field_classes`).

## 4. Pilotage adaptatif
`tt wave` : état de chaque lot (`READY`, `WAITING_FOR_LOT`, `DONE`, `STOPPED_DIMINISHING_RETURNS`, `STOPPED_OFF_TOPIC`,
`NO_QUALIFIED_QUERY`, `NO_LINKED_DOMAIN`, `CONDITION_NOT_MET`, `BUDGET_EXHAUSTED`) et rendement (appels, crédits,
nouveaux pertinents, isolés). `tt next` réserve l'appel suivant. Les lots S2/S3 prennent les formulations S1 dont le
premier résultat est pertinent (puis compteur) ; S4 ne cherche que les domaines d'annonces pertinentes non exclus ;
V2 seulement si la condition de productivité est atteinte. Arrêt : 2 appels consécutifs sans nouveau pertinent, ou
≥60 % d'isolés après 6 résultats. Réponse en cache : 0 crédit, rendement nul enregistré.

## 5. Mode autonome signé
1. Plan validé (`tt wave`) puis, **par le propriétaire dans son Terminal** :
   `python3 ecom.py tt approve-live --mission <M> --mode MCP_SESSION --scope <scope du plan> --max-credits <≥ somme des budgets, ≤ 500> --hours <≤ 48> --autonomous`
2. Boucle LLM_Provider : `tt next` → appel MCP aux paramètres exacts → `check_credits` → `tt ingest --call <id>` → `tt reconcile`.
3. Barrières : PreToolUse renvoie toujours `ask` (paramètres identiques à la réservation, solde frais, plafond, réserve
   ≥ 500, un seul appel ouvert) ; la règle `ask` des réglages est conservée ; seul le hook **PermissionRequest** lève la
   confirmation, pour la réservation AUTHORIZED identique, une fois, dans les 120 s, approbation autonome valide et plan
   inchangé (empreinte), moteur non gelé. Sinon aucune décision → confirmation humaine (en `-p` : refus).
4. Vérification E2E (2026-10-20, LLM_Provider runtime 2.1.304, faux serveur MCP `mcp__fakett__`, clé jetable) : approbation
   autonome → appel exécuté sans confirmation, journal `DISPATCH_AUTHORIZED(autonomous) → AUTONOMOUS_PERMISSION_GRANTED →
   RESPONSE_CAPTURED` ; approbation manuelle → refus en l'absence de confirmation ; paramètres de type différent → refus
   `NO_RESERVATION_WITH_IDENTICAL_PARAMS`.
5. Limite : un PreToolUse dépassant son délai (30 s) n'est pas bloquant selon la documentation LLM_Provider runtime ; dans ce cas la
   réservation reste PREPARED, PermissionRequest ne trouve rien d'AUTHORIZED → confirmation humaine ; une réponse sans
   réservation autorisée gèle le moteur (PostToolUse).

## 6. Correspondances de champs complémentaires (facultatives, à signer par le propriétaire)
Chemins observés dans les calibrages réels (valeurs scalaires vérifiées) ; les correspondances actuelles restent valides.
```
python3 ecom.py tt confirm-fields --mission RECHERCHE_07 --mode MCP_SESSION --tool search_ads --map total=pagination.total id=id copy=content.body landing=content.landingPageUrl domain=content.landingPageDomain advertiser=advertiser.name reach=metrics.reach active_ads=advertiser.liveAdsCount media=media.mediaUrl advertiser_id=advertiser.id audience_country=audience.mainCountry first_seen=firstSeenAt ad_status=status
python3 ecom.py tt confirm-fields --mission RECHERCHE_07 --mode MCP_SESSION --tool search_advertisers --map total=pagination.total id=id name=name active_ads=advertising.activeAds page_created=profile.createdAt
python3 ecom.py tt confirm-fields --mission RECHERCHE_07 --mode MCP_SESSION --tool search_shops --map total=pagination.total domain=domain name=name products=catalog.productsCount visits=traffic.monthlyVisits main_category=catalog.mainCategory shop_country=profile.countryCode
```
`advertiser_id` permet de relier un annonceur à ses annonces pertinentes par identifiant (sinon par nom) ;
`audience_country` remplace `REQUEST_FILTER_ONLY` par une géographie observée.

## 7. Livrables (`tt report`, dans `deliverables_dir` du plan)
`10_REGISTRE_APPELS_MOTEUR.md`, `11_CARTOGRAPHIE_ACTEURS.md`, `12_ANNONCES_ANGLES_MECANISMES.md`,
`13_LACUNES_SUBSTITUTS_CONTRE_PREUVES.md`, `14_RAPPORT_COMPARATIF_MARCHES.md`, `intel_summary.json`. Toute ligne cite
sa référence `TT-<appel>-NN` ; analyses et décisions par marché restent **À RÉDIGER / NON RENDUE** (aucun verdict forcé,
aucun prix, coût, marge, CAC, LTV).
