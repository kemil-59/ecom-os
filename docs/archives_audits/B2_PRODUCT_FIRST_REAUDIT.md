# B2 — réaudit indépendant, priorité Agents 01–02 (19 octobre 2026)

## Résultat observable

- Projet B2 récupéré depuis la réponse LLM_Provider tronquée ; **retesté et corrigé dans une copie distincte**.
- Les **44 tests précédents restent réussis**. **8 tests indépendants supplémentaires** ont été ajoutés : **52/52 réussis** dans l'environnement Linux / Python 3.13.5 utilisé ici.
- `python3 ecom.py demo` et `python3 ecom.py demo-b2` s'exécutent en **REPLAY**, avec exclusivement des observations et des vendeurs fictifs.
- Les 142 sources historiques sont conservées sans modification d'octet ; toutes les 28 vérifications historiques B1 et les 16 tests B2 antérieurs restent présents.
- Les fonctions B2 sont des **prototypes testés**, pas une validation complète sous macOS ni une preuve d'existence d'une intégration MarketPulse LIVE.

## Corrections apportées dans cette version

1. **Agent 01 — vrais auteurs indépendants (GATE 2B)** : une personne (`author_key`) qui reposte sur Amazon, un forum et YouTube ne compte plus trois fois. Une preuve sans identifiant auteur explicite ne certifie plus la pluralité des personnes. Réduit les faux PASS et publie la liste `independence_unverified_observations`.
2. **Autopilot — chemin Python direct** : le mode LIVE est rejeté même si un autre module tente d'appeler `engine.autopilot.step(...)` hors de la CLI.
3. **Séparation des missions** : une deuxième commande `init` avec des paramètres différents ne peut plus écraser silencieusement le marché/persona d'une mission existante. La réinitialisation identique est idempotente.
4. **MarketPulse — idempotence en attente** : deux préparations identiques pour la même mission et portée renvoient le même `call_id`, sans seconde réservation. Testé même lorsque la première réservation atteint exactement le plafond autorisé.

## Ce que les tests ne prouvent PAS

- L'Agent 01 ne collecte **aucune** véritable observation client tout seul à ce stade : pas de source Web ni de reçu fournisseur authentifié, seulement des données transmises au moteur.
- L'Agent 02 n'a pas réalisé d'audit sur des concurrents actuels. La démo est explicitement fictive.
- Dans la démo B1 reprise, les **14 réponses YES sont des valeurs synthétiques, sans justification preuve par preuve**. Cela suffit pour éprouver le moteur REPLAY mais pas pour annoncer qu'un produit réel a passé le filtre.
- Le moteur Agent 02 calcule bien une scorecard `/25`, mais la prise en compte de son seuil et de la validité des références doit encore être verrouillée **au moment du PASS du GATE 6**. Ne pas se fier à ce seul statut comme à une validation commerciale réelle.
- La pertinence des contre-exemples sur les fonctionnalités doit être obligatoire plutôt qu'optionnelle lors des recherches non simulées.
- Une empreinte/checksum n'est ni une signature de source externe ni la preuve qu'une URL a été effectivement consultée.
- Aucune garantie à 100 % de fiabilité et **aucun test exécuté sur un Mac** n'est revendiqué.

## Priorité B3 recommandée

1. **Agent 01 : acquisition / provenance / score /40 / GATE 2B / JTBD** : pipeline réel de collecte de preuves Web avec URLs exactes, dates, auteur indépendant, captures ou extraits, déduplication et niveaux de confiance ; exigences explicites avant passage des gates. Soit le connecteur est effectivement utilisable, soit la tâche de recherche est bloquée et expliquée. Ne pas inventer des avis.
2. **Agent 02 : concurrence /25, 8 phases, contre-recherche** : attester que les contre-exemples portent sur les *mêmes caractéristiques*, distinguer témoignages VoC et fiches commerçants, résoudre les preuves étrangères et les liens AD→SKU, traiter les scorecards faibles et les références inconnues comme des gates non passés.
3. **Filtre 14Q** : preuve ou inconnue par question ; `YES` sans preuve interdite pour une mission commerciale réelle. Sortie cible : fiche produit candidate, preuves, objections, scores, risques, lacunes et action suivante.
4. **LLM_Provider runtime et Mac** : parcours d'ouverture, healthcheck, mode lecture/recherche strictement encadré, sans facture ni publication. Vérifier les conditions de compatibilité au lieu de prétendre avoir testé macOS.
5. **Agent 03** : conserver le prototype actuel sans y disperser la majorité du développement. **Agents 04/05/06** : préserver les interfaces déjà présentes, sans les développer davantage dans B3.

## Commandes vérifiées

```bash
python3 ecom.py test
python3 ecom.py demo
python3 ecom.py demo-b2
```

Aucun appel externe payant et aucune installation sur la machine de l'utilisateur pendant ce réaudit.
