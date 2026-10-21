# ECOM OS — Méthode de recherche active (Agents 01 et 02)

Date : 2026-10-21 · Copie de travail : `ECOM_OS_INTEL_V2_WORKCOPY` · Autorisation : consigne propriétaire « Refonte stratégique définitive des Agents 01 et 02 ». Une seule méthodologie active ; projet principal non modifié tant que le paquet n'est pas installé.

## 1. Anciennes restrictions supprimées du circuit d'exécution

| Ancienne règle | Où elle agissait | Remplacée par |
|---|---|---|
| Liste blanche de 7 domaines (`NOT_ALLOWLISTED`) | `web_capture.check_url` | Vérification générique de toute page publique ; les 7 extracteurs deviennent des adaptateurs facultatifs de haute confiance |
| Familles d'extracteurs pour le GATE 2B (2 personnes + 3 familles) | `ledger.gate_2b` (FETCH) | `demand.gate_2b_convergence` : auteurs distincts, domaines indépendants, types de sources |
| Persona imposé (OUVERT puis PERSONA_A_FR par défaut, `--open-persona-explicit`) | `ecom.py init`, skills, `CLAUDE.md` | Segments découverts par défaut ; profil seulement sur `--persona PERSONA_A_FR` |
| Récurrence d'achat obligatoire (rejet GATE 4, exclusion du classement et des solutions) | `autopilot.decide_gate4`, `exploratory_ranking`, Agent 02 | Avantage évalué (axe Gamme/LTV), jamais un veto |
| Interdiction de collecter les prix | `CLAUDE.md`, politique, test | Prix et offres publics `PRICE_OBSERVED`/`OFFER_OBSERVED`, vérifiés sur la page, lecture seule |
| Seuils 2–5 témoins, puis 15/40 | `research_depth` (V1), seuils INTEL_V2 | Niveaux de preuve multi-sources (§6) ; anciens fichiers de seuils refusés |
| `ready_for_owner_review` à 2 comptes | `research_depth` | Retiré du circuit de décision ; seul `owner_checkpoint.opportunities` compte |
| Plafond de score selon 2–30 personnes | `scoring` (INTEL_V2) | Plafond selon le niveau de preuve |
| Anti-biais « sites certifiables », zones de vie Persona_A imposées | GATE 2A INTEL_V2 | Sources découvertes sur tout site, au moins 5 secteurs et 5 domaines |

Le test `test_14_old_rules_cannot_regain_priority` scanne toutes les instructions actives (CLAUDE.md, skills, sous-agents, règles, playbook) et le moteur pour empêcher le retour de ces règles.

## 2. Architecture de collecte et de vérification Web

DISCOVERY (`engine/discovery.py`) → EVIDENCE (`engine/web_capture.py`) → VALIDATION (`engine/demand.py`, `engine/research_depth.py`, gates).
- Toute page publique : HTTPS, hôte public vérifié aussi par résolution DNS, robots.txt (règles génériques et agents LLM_Provider), aucune redirection vers un autre hôte, taille bornée, contenu texte, détection des connexions et CAPTCHA (jamais contournés).
- La citation doit figurer dans la page téléchargée (reçu sha256 + instantané). Un résumé de moteur de recherche n'est jamais une preuve.
- Rattachement de l'auteur : ADAPTER > STRUCTURED (schema.org JSON-LD : Review, Comment, Answer, DiscussionForumPosting…) > PROXIMITY (pseudo affiché avant la citation) > UNBOUND (contribution, jamais personne distincte).
- Conservés pour chaque observation : URL, citation exacte, empreintes d'auteur (tous sites / par site), date, type de source, domaine enregistrable, langue, niveau de confiance, géographie, attributs déclarés.
- Avis traduits ou syndiqués (« Publié à l'origine sur… », « Traduit de… ») et sections étrangères : exclus de la demande française.

## 3. Méthode de recherche multi-sources

Un micro-problème reçoit une signature (`signature_terms`). Seuls les témoignages qui l'expriment y sont rattachés ; les problèmes voisins ne sont jamais fusionnés. Il est ensuite élargi de façon adaptative, cycle après cycle (synonymes, autres communautés et types de sources, périodes, solutions achetées, satisfaits, statistiques).

Le moteur mesure 10 indicateurs séparés : sites explorés, pages pertinentes, contributions, intervalle d'auteurs distincts (borne basse fusionnée, borne haute), incertitude d'unicité, répartition (domaines, types, HHI), concentration (domaine, fil, produit), récence et persistance, contextes, contre-signaux.

La saturation (3 lots sans nouveaux auteurs ni domaines) est suivie dans `investigation_state.json` et conduit à INSUFFICIENT_EVIDENCE si le niveau n'est pas atteint. Les ordres `ADAPTIVE_DEEPEN` listent les manques mesurés. Objectif pour une piste prometteuse : au moins 1 000 auteurs distincts au cumul de nombreuses sources indépendantes, sans quota par site ni plafond.

## 4. Construction des personas

PROBLÈME → PERSONNES → SEGMENTS → COMPORTEMENTS D'ACHAT → PERSONA → OPPORTUNITÉ.
- `persona_claim` vérifié dans la page ; seuls l'âge (et sa tranche), le genre et les rôles **déclarés** sont extraits (parent, retraite, étudiant, aidant, enceinte, animal, télétravail, sportif).
- `segment_profile` : répartition par auteurs distincts, couverture des déclarations, segments émergents (au moins 10 auteurs et au moins 40 % des déclarants) toujours qualifiés d'HYPOTHÈSES tant qu'une statistique vérifiée ne les confirme pas.
- Mode PROFILE (`--persona PERSONA_A_FR`) : GATE 2A persona, rejet hors cible, fit démontré par déclarations **ou** statistique vérifiée applicable au segment.

## 5. Agents 01 et 02

**Agent 01** : découverte commerciale large, critères d'opportunité complets, rejet après enquête possible.

**Agent 02** : contre-enquête indépendante avec au moins 5 requêtes FR, avis positifs ET négatifs, substituts, au moins 2 tentatives de réfutation, et prix publics observés ou constat d'absence. Il produit la fourchette de prix vérifiée (source et date), les offres et la saturation, puis un verdict : INSUFFICIENT_COUNTER_RESEARCH, COMPETITION_UNKNOWN (jamais « marché vide »), INVALIDATED, ALREADY_WELL_SOLVED, NO_CREDIBLE_ADVANTAGE ou DIFFERENTIATION_PLAUSIBLE_NOT_PROVEN. L'international ne sert que d'inspiration.

## 6. Gates et critères de preuve effectivement modifiés

| Niveau | Condition moteur (paramètres `contracts/research_thresholds.json`) |
|---|---|
| HYPOTHESIS_DETECTED | en dessous de « corroboré » (plancher codé : moins de 6 auteurs) |
| PROBLEM_CORROBORATED | ≥ 10 auteurs (borne basse), ≥ 3 domaines, ≥ 2 types de sources |
| BROAD_DISTRIBUTED_SIGNAL | ≥ 100 auteurs, ≥ 10 domaines, ≥ 3 types, domaine dominant ≤ 35 %, ≥ 2 années, ≥ 3 contextes |
| SIGNIFICANT_MARKET_DOCUMENTED | voie témoignages (≥ 1 000 auteurs, ≥ 20 domaines indépendants, niveau large, ET ampleur FR : ≥ 300 auteurs ancrés FR dont ≥ 50 résidents déclarés, ≥ 10 domaines FR, ≥ 30 % du total) et/ou voie statistique (statistique FR vérifiée par le moteur + ≥ 10 auteurs ancrés FR), affichées séparément |
| COMMERCIAL_OPPORTUNITY_TO_DEEPEN | marché significatif + Agent 02 DIFFERENTIATION_PLAUSIBLE après contre-enquête complète (+ fit démontré si profil imposé) |
| INSUFFICIENT_EVIDENCE | enquête saturée sans marché significatif |
| REJECTED | Agent 02 : déjà bien résolu, invalidé ou aucun avantage ; ou hors cible imposée |

- **GATE 2B** = convergence (au moins PROBLEM_CORROBORATED).
- **GATE 3** = bloqué tant que le GATE 2A n'est pas franchi.
- **GATE 4** = SIGNIFICANT_MARKET_DOCUMENTED + score ≥ 28/40 + aucun red flag majeur. Les notes sont plafonnées à 1/2/3/4/5 selon le niveau ; un score ne contourne jamais la preuve.

Aucun changement supplémentaire de politique n'est à soumettre en dehors du cadre autorisé.

### Corrections avant installation (2026-10-21)

1. **Marché français** : 4 catégories séparées par auteurs distincts (`fr_market.authors_by_geography`) : FR_RESIDENCE_DECLARED, FR_MARKET_SITE_NO_RESIDENCE (adaptateur de site FR ou domaine .fr, résidence non prouvée), UNKNOWN_COUNTRY, INTERNATIONAL (conservé comme signal, exclu de la demande FR). `fr_qualified_authors` ne compte plus que les résidences déclarées. Statut `fr_market.status` : FR_SCALE_DEMONSTRATED_BY_TESTIMONY / _BY_STATISTIC / _BY_TESTIMONY_AND_STATISTIC / FR_SCALE_NOT_DEMONSTRATED, avec texte d'incertitude. Un grand signal sans ampleur FR reste BROAD_DISTRIBUTED_SIGNAL (biais LARGE_SIGNAL_BUT_FR_SCALE_NOT_DEMONSTRATED).
2. **Indépendance des publications** : une même publication sur plusieurs domaines (syndication, copie) ne compte qu'une fois ; les seuils utilisent les domaines indépendants (`independence.raw_domains` / `independent_domains`).
3. **Études** : `web_capture.verify_study` vérifie sur la page HTML la donnée chiffrée exacte, la date (page ou métadonnées), le pays, la population, le lien au micro-problème (termes de signature à moins de 1 500 caractères), la méthode et le dénominateur s'ils sont déclarés. VERIFIED seulement si tout est retrouvé ; PDF et autres formats : UNSUPPORTED_FORMAT(_PDF). Reçus `RESEARCH_INBOX/study_receipts.jsonl` ; seule une étude avec reçu moteur `STU-…`, VERIFIED, pays FR et portée micro/segment est applicable. Le champ `verification` proposé est ignoré.

### Clarification : le problème avant l'identité des internautes (2026-10-21)

1. **Identité non requise** : `author` est facultatif. Une citation vérifiée sans pseudo est une contribution non attribuée, utile pour mesurer le problème. Si un auteur est revendiqué mais que le rattachement est incertain ou faux (y compris sur un site à adaptateur), la citation est conservée NON attribuée (`author_claim_rejected`), jamais attribuée à tort.
2. **Volumes** : contributions, **témoignages distincts** (auteurs identifiés fusionnés + contributions anonymes indépendantes, copies retirées), discussions (pages), communautés (domaines indépendants) et auteurs identifiables (intervalle) sont séparés. Les niveaux de preuve et l'ancrage FR se calculent en témoignages distincts.
3. **Personas en 4 couches** (`segments.layers`) : 1 déclaré (texte vérifié, `persona_claim`), 2 inféré du contexte (accords grammaticaux, mentions de vie, avec confiance LOW/MEDIUM ; segments `INFERRED_SEGMENT_HYPOTHESIS_NOT_INDIVIDUAL_PROOF`), 3 établi par études vérifiées (portée segment), 4 inconnu. Le fit d'un profil imposé n'utilise jamais l'inféré.
4. **GATE 2B unique** : tout le pipeline réel (classement JTBD, autopilote, `a01 gate2b`, Agent 02, `ledger.gate_2b` en FETCH_VERIFIED) passe par `demand.gate_2b_convergence`. La règle historique ne subsiste que pour les simulations REPLAY.
5. **Installateur fiabilisé** : contrôle d'intégrité sha256 du paquet ; Python 3.11+ ; verrou contre les installations concurrentes ; détection « déjà installé » ; dérive refusée sauf `--force` ; sauvegarde vérifiée ; copie contrôlée par sha256 ; tests en nombre exact ; retour arrière automatique sur toute erreur. La restauration est contrôlée par les empreintes d'origine.

### Défauts reproduits sur le ZIP final, corrigés (2026-10-21)

1. **Paternité** : la fenêtre de proximité de 500 caractères est supprimée. Un auteur n'est rattaché que dans un bloc fiable : adaptateur ; données structurées (JSON-LD ou microdonnées schema.org) ; ou pseudo affiché dans le bloc même d'une contribution répétée (commentaire, avis, message). La paternité doit être unique : un même texte signé par deux auteurs est ambigu, donc UNBOUND. Sinon la citation reste UNBOUND (`author_claim_rejected` : NO_RELIABLE_AUTHOR_BLOCK, AUTHOR_NOT_IN_CONTRIBUTION_BLOCK, AMBIGUOUS_AUTHORSHIP…).
2. **Nature du contenu** (`contribution_nature`, adossée au reçu) : USER_CONTRIBUTION_ADAPTER / _STRUCTURED / _MARKUP, EDITORIAL_OR_MARKETING (fiche produit, article, FAQ du site, témoignage marketing, méta-description), UNCERTAIN_NATURE. Le `source_kind` proposé est conservé pour information (`source_kind_proposed`) mais ne détermine plus rien. Seules les contributions d'utilisateurs comptent comme témoignages et pour le GATE 2B ; le reste est compté à part (`market_information_not_testimonies`). Les contributions anonymes restent admissibles quand la page les présente comme contributions d'utilisateurs.
3. **Installateur** : SIGINT, SIGTERM et SIGHUP pendant la copie ou les tests arrêtent le processus en cours puis déclenchent le retour arrière vérifié, sans nouvelle interruption possible pendant la restauration. Une interruption avant toute écriture retire la sauvegarde partielle. Le verrou est toujours libéré.

## 7. Tests réalisés et résultats exacts

- Suite complète : **239 tests OK** (Python 3.13.16) = 210 tests historiques (dont 7 réécrits parce qu'ils encodaient les règles remplacées, voir ci-dessous) + 29 tests de la méthode active (`test_17` à `test_21` : marché FR et études ; `test_22` à `test_25` : témoignages anonymes, attribution jamais inventée, couches de persona, GATE 2B unique ; `test_26` à `test_28` : régressions Alice/Bob et 12 pages commerciales, contributions anonymes admises, FAQ éditoriale).
- Sous Python 3.9.6 : 237 OK. Les 2 échecs (healthcheck exigeant Python 3.11+) sont identiques sur le projet principal non modifié.
- Tests historiques réécrits (règle remplacée → nouvelle attente) :
  - `test_fetch_verified` : 02 (liste blanche → page inaccessible ; pseudo faux → contribution non attribuée au lieu d'un rejet), 06 (GATE 2B à 2 personnes → jamais PASS), 08 (récurrence ABSENT → plus de veto ; marché significatif exigé ; `PRICE_OBSERVED` autorisé), 09 (prix publics autorisés, coûts toujours interdits ; récurrence évaluée).
  - Toujours en `test_fetch_verified` et dans `test_attempt_counting` : ajout d'un GATE 2A valide via `tests/mission_helpers.py`.
- Couverture des 15 vérifications demandées : 1 → `test_01` ; 2 → `test_02` ; 3 → `test_03` ; 4 → `test_04` ; 5 → `test_05` ; 6 → `test_06` ; 7 → `test_07` ; 8 → `test_08` ; 9 → `test_09` (5 000 contributions, < 20 s) ; 10 → `test_10` ; 11 → `test_11` ; 12 → `test_12` ; 13 → `test_13`, `test_13b` ; 14 → `test_14` ; 15 → `test_15` plus les 77 tests MarketPulse historiques, inchangés et OK.
- De bout en bout (`test_16`) : les 27 pages réelles de RECHERCHE_08 produisent 69/69 reçus, GATE 2A incomplet, GATE 3 bloqué, 0 opportunité, tous les micro-problèmes en HYPOTHESIS_DETECTED.

### Essai réel limité (2026-10-21, sans mission, dossier temporaire)

| Cas | Statut moteur | Rattachement | Géographie |
|---|---|---|---|
| pharma-gdd.com, avis négatif (inconnu des anciennes listes) | QUOTE_VERIFIED | STRUCTURED, daté 2022-09-03 | LANGUAGE_ONLY |
| pharma-gdd.com, avis positif | QUOTE_VERIFIED | STRUCTURED, daté 2022-12-06 | LANGUAGE_ONLY |
| pharma-gdd.com, prix public « 7,19 € » | QUOTE_VERIFIED (7.19) | SITE_LEVEL | PUBLIC_PAGE |
| pharmashopi.com, prix public « À partir de 9,40 € » | QUOTE_VERIFIED (9.4) | SITE_LEVEL | PUBLIC_PAGE |
| notino.fr, avis affiché en français | QUOTE_VERIFIED | STRUCTURED | **SYNDICATED_OR_FOREIGN** (traduit de l'italien, exclu) |
| notino.fr, citation inventée | QUOTE_NOT_FOUND | — | — |
| newpharma.fr, atida.fr | BLOCKED_ROBOTS | — | — |
| vivelesrondes.com (page citée par un moteur de recherche) | redirection inter-hôtes, puis 404 sur l'hôte canonique | — | — |
| soin-et-nature.com, texte éditorial trop court | QUOTE_TOO_SHORT | — | — |
| forumconstruire.com (adaptateur historique) | QUOTE_VERIFIED | ADAPTER | RESIDENCE_DECLARED |

Aucun de ces domaines (hors forumconstruire) n'a été activé. Cet essai démontre l'absence de liste blanche bloquante. Il ne valide aucun marché.

## 8. Limites techniques réelles restantes

- **Plateformes inaccessibles à cet agent** : Reddit, Amazon, Trustpilot, Fnac, Darty, Leroy Merlin, Sephora, newpharma, atida (robots.txt ou 403) ; Marmiton et BabyCenter refusés par l'outil de recherche lui-même. Les commentaires TikTok, YouTube et Instagram ne figurent pas dans le HTML public et aucune API autorisée n'est configurée. Ces sources restent DISCOVERED_UNVERIFIED.
- **Pages rendues en JavaScript** : sans données structurées ni texte serveur, la citation est introuvable (QUOTE_NOT_FOUND), même si elle est visible dans un navigateur.
- **Rattachement PROXIMITY** : heuristique de confiance moyenne. Sans pseudo visible avant la citation, la contribution reste UNBOUND et la borne basse d'auteurs n'augmente pas.
- **Résidence** : la plupart des sites ne la donnent pas. Les domaines .fr comptent comme « marché FR sans résidence prouvée » ; un site .com francophone compte comme pays inconnu. La voie témoignages exige un ancrage FR convergent (≥ 300 auteurs ancrés FR, dont ≥ 50 résidents déclarés).
- **Études** : vérification HTML minimale seulement ; PDF, tableaux en images et pages rendues en JavaScript restent non vérifiés. Le lien au micro-problème repose sur des termes proches de la donnée, pas sur une lecture méthodologique complète de l'étude.
- **Volume** : atteindre 1 000 auteurs distincts suppose de nombreux cycles de collecte, coûteux en temps et en requêtes, et limités par l'accès réel aux plateformes. Le moteur dira honnêtement INSUFFICIENT_EVIDENCE si l'ampleur n'est pas atteignable.
- **Domaine enregistrable** : approximation sans liste publique des suffixes (quelques suffixes de second niveau seulement).
- **Conditions d'utilisation** : robots.txt et les contrôles d'accès sont respectés automatiquement ; les CGU spécifiques à chaque site ne sont pas interprétées par le moteur.

## 9. Une seule méthodologie active

- Toutes les missions FETCH_VERIFIED passent par `autonomous_research.run` → `research_depth._apply_v3` → `autopilot` (GATE 2B convergence, score plafonné, GATE 4 marché significatif).
- Les fonctions historiques encore présentes ne sont appelées que par les simulations REPLAY (fixtures B1–B3) et ne sont reliées à aucune mission réelle : écran économique, `ledger.gate_2b`, `score_with_evidence`, et l'analyse Stage 1/2 sans contexte moteur.
- Les sauvegardes et le corpus restent inertes.
