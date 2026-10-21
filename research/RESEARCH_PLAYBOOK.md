# ECOM OS — RESEARCH PLAYBOOK (méthode unique active)

Ce fichier est **le seul endroit à modifier pour améliorer la stratégie de recherche**. Il est lu à chaque mission via `.claude/skills/ecom-os/SKILL.md`. Il ne remplace jamais les règles de sécurité ni les niveaux de preuve calculés par le moteur.

**Principe :** parcourir largement les sources accessibles, détecter les problèmes dont parlent réellement de nombreux consommateurs, accumuler et confronter des preuves indépendantes, identifier les personnes concernées, mesurer la profondeur du marché, puis chercher les possibilités commerciales. Un petit échantillon n'est jamais la preuve d'un marché. Aucune piste n'est promue pour « produire un résultat ».

## A — DISCOVERY : tout le Web public accessible

- Aucune liste de sites. Forums généralistes et spécialisés, communautés (Doctissimo, aufeminin, Reddit…), marketplaces (Amazon, Cdiscount, Fnac…), boutiques DTC et avis publics, commentaires publics de vidéos, blogs et médias contenant des témoignages, comparateurs, moteurs de recherche, études sectorielles et institutionnelles, plateformes nouvelles découvertes en cours de route. Cette énumération n'est pas une liste blanche.
- Respecter robots.txt, connexions, CAPTCHA et conditions d'accès : aucun contournement. Une page inaccessible est notée (`BLOCKED_*`) et reste DISCOVERED_UNVERIFIED.
- Partir des situations vécues, motivations et dépenses des consommateurs, jamais d'un catalogue. Aucun secteur, produit ou persona présélectionné ; aucun privilège pour l'entretien, le bricolage ou les consommables.
- `territories.json` : 8 territoires, **au moins 5 secteurs** différents (`sector` parmi `engine/discovery.SECTORS`), au plus 2 par secteur, chacun avec `discovery_refs` (URL déclarées dans `discovery_N.json → sources`), au moins 5 domaines distincts au total.

## B — EVIDENCE : vérification générique de la page

- Toute page publique accessible peut fournir une preuve : le moteur télécharge la page et retrouve la citation exacte. **L'identité des internautes n'est jamais requise** : `author` est facultatif. Si un auteur est revendiqué, le moteur exige un rattachement fiable (adaptateur, données structurées JSON-LD, pseudo affiché juste avant la citation) ; sinon, ou s'il ne correspond pas, la citation reste une contribution non attribuée (`UNBOUND`), jamais attribuée à tort.
- Capture : `{url, quote, author, source_kind (FORUM|REVIEW|COMMENT|BLOG|QA|SOCIAL|NEWS|OTHER), kind, territory_id, micro_id, context, probleme, location_claim?, persona_claim?, incumbent?, dimension?, satisfied_dimension?}`.
- `persona_claim` (facultatif) = phrase exacte d'auto-déclaration. Le moteur extrait aussi lui-même, du texte vérifié, les caractéristiques déclarées et les caractéristiques probables (indices de contexte, confiance LOW/MEDIUM). Jamais rien depuis un prénom ou un pseudo.
- Agent 02 : `kind: PRICE_OBSERVED` (prix public tel qu'affiché, avec montant) et `OFFER_OBSERVED` (bundle, promotion, garantie). Vérifiés sur la page, avec date.

## C — ACCUMULATION multi-sources d'un même micro-problème (méthode centrale)

1. Une frustration précise est repérée dans quelques discussions.
2. Elle reçoit une **signature** (`signature_terms` : mots exacts des consommateurs et synonymes). Seuls les témoignages qui expriment réellement cette micro-friction y sont rattachés. Des problèmes voisins restent des micro-problèmes distincts, jamais fusionnés.
3. **Élargir de façon adaptative** : synonymes et formulations réelles ; autres communautés et types de sources ; discussions historiques et récentes ; avis sur les solutions achetées ; récurrence, intensité, conséquences, dépenses ; consommateurs **satisfaits** ; statistiques vérifiables sur le segment.
4. Chaque lot de captures porte `micro_id`. Le moteur mesure les **10 indicateurs** : sites explorés, pages pertinentes, contributions, intervalle d'auteurs distincts, doublons et incertitude d'unicité, répartition, concentration, récence et persistance, contextes, contre-signaux.
5. Continuer tant que le rendement marginal le justifie (nouveaux auteurs, nouveaux domaines). Le moteur détecte la **saturation** et conclut `INSUFFICIENT_EVIDENCE` si le niveau n'a pas été atteint. L'avancement est conservé d'un cycle à l'autre (`investigation_state.json`).
6. Viser, pour les pistes prometteuses, **au moins 1 000 témoignages distincts au cumul de nombreuses sources indépendantes** (copies et répétitions détectables retirées ; témoignages anonymes inclus), quand c'est accessible. Ce n'est ni un quota sur un seul site, ni un plafond. L'identification des auteurs n'est pas nécessaire ; le nombre d'auteurs identifiables est donné à part, en intervalle.
7. Voie statistique séparée : `studies.json` → `{id, url, figure_quote (donnée chiffrée exacte), date, country, population, method?, denominator?, scope (GENERAL_MARKET|CATEGORY|SEGMENT|MICRO_PROBLEM), applies_to, link_terms?}`. Le moteur vérifie sur la page HTML la donnée exacte, sa date, le pays, la population et le lien au micro-problème (termes de signature près de la donnée) ; tout statut « VERIFIED » proposé est ignoré. PDF et formats non HTML : non vérifiés (limite explicite). Une étude générale ne valide jamais un micro-problème.
8. **Ampleur FRANÇAISE** : le moteur sépare résidence FR déclarée, sites marchands FR / domaines .fr (résidence non prouvée), pays inconnu et international. Les auteurs de pays inconnu ou internationaux restent des signaux utiles mais ne démontrent jamais seuls l'ampleur française.

## D — PERSONAS : après les problèmes

PROBLÈME → PERSONNES CONCERNÉES → SEGMENTS → COMPORTEMENTS D'ACHAT → PERSONA → OPPORTUNITÉ. Le moteur construit les segments à partir des **témoignages** (identité non requise) en 4 couches séparées : 1 déclaré, 2 inféré du contexte (probable, avec confiance ; jamais une preuve individuelle), 3 établi par une étude vérifiée, 4 inconnu. Les segments émergents restent des hypothèses tant qu'une statistique vérifiée ne les confirme pas. PERSONA_A_FR (et tout profil) ne s'applique que si le propriétaire l'impose (`--persona PERSONA_A_FR`).

## E — Agent 02 : contre-enquête indépendante

`proposals/competitive_research.json`, par micro (`micro_id`) ou candidat :
- `queries` (au moins 5 FR, avec intents FR_COMPETITORS, SATISFACTION, NEGATIVE_REVIEWS, SUBSTITUTES, INVALIDATION ; plus INTERNATIONAL) ;
- `competitors` : DIRECT/INDIRECT/SUBSTITUTE/BEHAVIOR (alternatives gratuites incluses) avec mécanisme, positionnement, angles publicitaires, objections, segments servis, raisons de fidélité, URL et `evidence_refs` ;
- `invalidation_attempts` : au moins 2 ;
- prix publics via captures `PRICE_OBSERVED`, ou `no_public_price_found: true`.

Verdicts calculés par le moteur : INSUFFICIENT_COUNTER_RESEARCH, COMPETITION_UNKNOWN_SEARCH_INSUFFICIENT (jamais « marché vide »), INVALIDATED_BY_COUNTER_RESEARCH, ALREADY_WELL_SOLVED, NO_CREDIBLE_ADVANTAGE_FOUND, DIFFERENTIATION_PLAUSIBLE_NOT_PROVEN. L'étranger sert d'inspiration de mécanismes, jamais de preuve de demande française.

## F — Niveaux de preuve et décision

Seuils en **témoignages distincts** (auteurs identifiés fusionnés + contributions anonymes indépendantes, copies retirées ; discussions, communautés et auteurs identifiables sont aussi affichés à part). HYPOTHESIS_DETECTED · PROBLEM_CORROBORATED (≥ 10 témoignages, ≥ 3 domaines, ≥ 2 types de sources) · BROAD_DISTRIBUTED_SIGNAL (≥ 100 témoignages, ≥ 10 domaines, ≥ 3 types, domaine dominant ≤ 35 %, ≥ 2 années, ≥ 3 contextes) · SIGNIFICANT_MARKET_DOCUMENTED (voie témoignages : ≥ 1 000 témoignages sur ≥ 20 domaines indépendants ET ampleur française démontrée — ≥ 300 témoignages ancrés FR dont ≥ 50 avec résidence déclarée, sur ≥ 10 domaines FR, ≥ 30 % du total ; et/ou voie statistique : statistique française vérifiée par le moteur et ≥ 10 témoignages ancrés FR). Les domaines ne comptent que pour des publications indépendantes (avis syndiqués et copies fusionnés) · COMMERCIAL_OPPORTUNITY_TO_DEEPEN · INSUFFICIENT_EVIDENCE · REJECTED.

Ces seuils sont des paramètres de calibrage (`contracts/research_thresholds.json`), pas des validations commerciales. Une frustration fréquente n'est pas forcément un marché ; un grand marché n'est pas forcément une opportunité ; une forte concurrence prouve une demande mais peut fermer l'entrée.

## Score /40 (processus maître)

Fit (cible imposée ou segment découvert), Fréquence, Intensité, Dépense existante, Insatisfaction, Profondeur de marché, Démontrabilité publicitaire, Gamme/LTV. Chaque note est plafonnée par le niveau de preuve (1/5 pour une hypothèse, 5/5 seulement avec les deux voies). « Ce score ne prouve rien. »

## Notes personnelles et nouvelles sources

Écris ici les communautés, requêtes et formulations à essayer. Toute nouvelle source est explorable immédiatement ; seule la vérification moteur en fait une preuve.
