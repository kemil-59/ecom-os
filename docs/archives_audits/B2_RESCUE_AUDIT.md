# ECOM OS CLEAN B2 — récupération après coupure de LLM_Provider

## Statut exact

Ce dossier est un **prototype local REPLAY**, assemblé à partir de la B1 vérifiée et des modules B2 complets présents avant la coupure de la deuxième réponse LLM_Provider. **La réponse de LLM_Provider est tronquée au milieu de `ecom.py` et ne contient pas son script final, ses tests B2 ni son ZIP.** Nous avons reconstruit la fin de la CLI et les intégrations manquantes localement. **Ne pas attribuer à LLM_Provider l'exécution des tests ci-dessous**.

Les fonctions compilables trouvées dans la réponse interrompue ont été extraites dans des modules séparés ; les 142 sources de `reference_corpus/` proviennent de B1. Aucun ancien dépôt officiel n'a été modifié.

### Contrôles réellement exécutés
- 28 tests B1 préservés et réussis
- 16 tests indépendants B2 supplémentaires et réussis
- **44/44 tests réussis** (voir `logs/b2_rescue_44_tests.log`)
- `python3 ecom.py demo-b2` réussi avec données entièrement synthétiques (voir `logs/b2_rescue_demo.json`)
- `python3 ecom.py demo` réussi après récupération
- 142 fichiers historiques de référence, intacts en contenu, maintenus dans `reference_corpus/`

### Corrections faites après la réponse coupée
1. Réparation de la fin de `ecom.py` et de la commande `demo-b2` ; ajout de `a05 plan` hors ligne.
2. Correction de l'import `sys` et des champs `mission_id`/`plan_id` pour Higgsfield Studio.
3. Nouvelle mission de démo à chaque invocation, sans effacer les dossiers de mission existants.
4. Code Agent 03 générique, sans persona/produit/cuisine précablés et sans revendications produit non testées. Les sorties sont des **brouillons nécessitant examen humain**, pas du texte publicitaire prêt à publier.
5. Vérifications supplémentaires de l'intégrité de l'audit Agent 02, de la mission, du marché et de l'empreinte produit avant Agent 03.
6. Pour les données non synthétiques, rejet des preuves SHOP/SKU/AD revendiquées comme témoignages clients VoC ; anciens fixtures B1 autorisés uniquement en REPLAY pour assurer la parité des tests.
7. Une URL même domaine AD→SKU reste `SAME_DOMAIN_PRODUCT_URL_NOT_VERIFIED`, pas preuve vérifiée.
8. MarketPulse limité à REPLAY ou DISCONNECTED dans la CLI ; blocage LIVE jusqu'aux connecteurs authentifiés et approbations nécessaires.
9. Désactivation du cache Python dans le corpus pour conserver la vérification du nombre de sources.

### Limites importantes non résolues
- Aucune collecte Web/MarketPulse LIVE et aucun reçu provider authentifié. Un solde écrit dans un fichier REPLAY ne prouve pas une vraie connexion.
- Aucune génération d'image ou vidéo Higgsfield réelle : les assets de la démo sont des références fictives. L'étiquette de QA humaine de la démo est elle aussi **simulée**.
- `a03` crée des matrices et des briefs d'hypothèses, pas des publicités vérifiées ni un travail autonome d'enquête. Un LLM/éditeur humain doit rédiger et valider les variantes et contrôler les droits médias.
- Agent 04 = plan de boutique, pas site Shopify créé ou publié ; Agent 05 = plan Meta bloqué sans connecteur ; Agent 06 = règles de diagnostic depuis métriques injectées, pas de lectures Meta/Shopify.
- Le moteur Agent 05 historique est FR-only ; ne pas le présenter comme universel UAE.
- Les 44 tests démontrent uniquement les scénarios couverts. Ils ne prouvent ni l'absence de bugs ni une validation en production.
- Des garde-fous techniques n'authentifient pas les faits cités. Une URL ou un checksum n'est pas une preuve que la page a été consultée.

### Démarrage local ou LLM_Provider runtime (Python 3.11+)

Dans un terminal, se placer dans le dossier `ECOM_OS_CLEAN_B2` puis :

```bash
python3 ecom.py test
python3 ecom.py demo-b2
python3 ecom.py knowledge --query Smooche
```

On peut demander à LLM_Provider runtime d'ouvrir ce dossier et d'exécuter les commandes ; aucune des commandes ci-dessus n'utilise une API payante.

### Avant toute véritable mission commerciale

Mettre en place le chaînage de connecteurs live et une origine vérifiable des données, autorisations interactives G1/G2/G3, modèle économique local au marché, rapprochement SKU contrôlé, validation manuelle des preuves et des créatifs, intégrations Shopify/Meta avec permissions minimales. Laisser toutes les actions de paiement/publication **désactivées** jusqu'à preuve de ces contrôles.

---

## Mise à jour : réaudit supplémentaire Product-First (19/10/2026)

La suite initiale de 44 tests décrite plus haut reste archivée à titre historique. **La version transmise à LLM_Provider B3 ajoute 8 tests (52/52 réussis)** et quatre corrections ciblant la provenance des témoignages, le blocage direct de LIVE, la non-destruction d'une mission existante et l'idempotence des réservations MarketPulse REPLAY. Voir `docs/B2_PRODUCT_FIRST_REAUDIT.md` pour le diagnostic actuel et les limites critiques de sélection produit.
