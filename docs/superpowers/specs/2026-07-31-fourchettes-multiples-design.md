# Fourchettes multiples, chacune avec ses salons

## Le problème

Aujourd'hui la configuration est plate : **une** fourchette (`prix_min`,
`prix_max`), **une** liste de salons, et `publier_si_lheure` construit un post
qu'il diffuse partout. Impossible d'envoyer « les grosses affaires » dans un
salon et « les petits prix » dans un autre.

## Ce que ça devient

Une fourchette est un objet nommé qui porte ses prix **et** ses salons :

```json
{
  "fourchettes": [
    {"nom": "grosses-affaires", "prix_min": "1e14", "prix_max": "6e15", "salons": ["111", "222"]},
    {"nom": "petits-prix",      "prix_min": "0",    "prix_max": "1e12", "salons": ["333"]}
  ],
  "heure": "09:00", "fuseau": "Europe/Paris",
  "role_id": null, "logs_salon_id": null, "autorises": []
}
```

`prix_min`, `prix_max` et `salons` quittent la racine.

### Portée d'une fourchette : prix et salons, rien de plus

L'heure, le fuseau, la mention de rôle, le salon de logs et le template restent
**globaux**. Un seul passage quotidien publie toutes les fourchettes.

Décision délibérée contre l'alternative « chaque fourchette a son heure » :
l'idempotence quotidienne devrait alors être suivie par fourchette, et `/tick`
deviendrait difficile à raisonner — pour un besoin qui n'existe pas.

## Migration : le point le plus risqué

La prod tourne avec une config plate en Postgres. `Store.salons()` gère déjà une
migration antérieure (`salon_id` unique → `salons`). On ajoute un étage à cette
lecture, sans script de migration :

> Une config sans clé `fourchettes` est **lue** comme une fourchette unique
> nommée `principale`, avec les `prix_min`/`prix_max` de la racine et le résultat
> de `salons()` (donc la migration `salon_id` s'applique d'abord).

Écrite seulement au premier changement, comme le fait déjà `_ecrire_salons`.
Lire-puis-migrer plutôt qu'un script : un script devrait tourner exactement une
fois, sur une base dont on ne contrôle pas le cycle de redéploiement.

Trois variantes d'ancienne config doivent donc fonctionner :

| Config trouvée | Lue comme |
|---|---|
| `{prix_min, prix_max, salons: [a, b]}` | `principale` avec `[a, b]` |
| `{prix_min, prix_max, salon_id: a}` (pré-multi-salon) | `principale` avec `[a]` |
| `{prix_min, prix_max}` sans salon | `principale` sans salon |
| `{}` (bot neuf) | aucune fourchette |

Sans fourchette configurée, le bot ne publie rien et le dit — même comportement
qu'aujourd'hui sans salon.

## Publication

`publier_si_lheure` passe de « construire un post → diffuser partout » à « pour
chaque fourchette : construire son post → diffuser dans ses salons ». Trois
points qui comptent :

- **Le CSV est lu une seule fois.** `charger()` remonte avant la boucle et
  `(meta, batiments)` est passé à `construire_publication`. Sinon N fourchettes
  = N appels à l'API du jeu, pour des données identiques.
- **Isolation à deux niveaux.** Une fourchette dont tous les salons échouent ne
  doit pas empêcher les suivantes. Un salon cassé ne doit pas priver les autres
  salons de sa fourchette (comportement actuel, conservé).
- **`derniere_publication` reste globale**, marquée dès qu'un salon d'une
  fourchette quelconque a reçu le post. Même règle qu'aujourd'hui : sinon le
  passage suivant reposterait là où ça avait marché.

Les erreurs de chargement (source en panne, CSV corrompu) sont levées **avant**
toute publication et avant `marquer_publie` — sinon une panne à 09:00 annulerait
la journée entière.

### Repêchage : inchangé, par fourchette

`find_promos` complète une fourchette de moins de `CIBLE_MINIMUM` (2) promos avec
les plus proches, hors bornes, marquées `dans_fourchette=False` et porteuses de
leur `ecart`.

Chaque fourchette repêche indépendamment. **Un bâtiment peut donc apparaître dans
deux publications** — avec seulement 4 promotions dans l'export courant, ce n'est
pas théorique. Assumé : le badge « hors fourchette » et l'écart rendent le cas
lisible, alors qu'un dédoublonnage inter-fourchettes rendrait le résultat d'une
fourchette dépendant de l'ordre des autres.

## Commandes Discord

`/config prix` et `/config salon` ne peuvent plus rien signifier sans dire *de
quelle* fourchette. Ils sont **retirés**, pas conservés pour compatibilité : les
laisser agir sur une fourchette implicite est exactement l'ambiguïté qui fait
poster au mauvais endroit.

| Commande | Effet |
|---|---|
| `/fourchette ajouter nom min max` | Crée une fourchette, sans salon |
| `/fourchette supprimer nom` | La supprime |
| `/fourchette prix nom min max` | Modifie ses bornes |
| `/fourchette salon ajouter nom salon` | Attache un salon |
| `/fourchette salon retirer nom salon` | Le détache |
| `/fourchette liste` | Toutes les fourchettes, bornes et salons |

Le paramètre `nom` reçoit l'autocomplétion Discord alimentée par les fourchettes
existantes : il n'est jamais retapé à la main.

`/config voir` liste les fourchettes au lieu d'une ligne unique. `/promos min
max` est inchangé — c'est une recherche ponctuelle, indépendante de la config.

Règles sur les noms : non vide, unique (insensible à la casse pour la comparaison
d'unicité, afin que `Petits` et `petits` ne coexistent pas), longueur bornée à ce
qu'un nom de commande Discord affiche lisiblement. Un nom inconnu dans
`/fourchette prix` ou `/fourchette salon` est refusé avec la liste des noms
valides, jamais créé silencieusement.

Une fourchette sans salon est ignorée à la publication et affichée avec un ⚠️
dans `/fourchette liste`.

## Site et API

`CHAMPS_MODIFIABLES` devient `("heure", "fuseau")`.

Les fourchettes suivent la règle déjà en place pour les salons : **réglées dans
Discord**, parce qu'elles désignent des salons dont le site ne peut vérifier ni
l'existence, ni les permissions du bot. Le site les **affiche** — nom, bornes,
salons — avec les montants en trois formes (`prix_min`, `prix_min_long`,
`prix_min_brut`) comme partout ailleurs.

L'écran « réglages » perd son formulaire de fourchette et ne garde que l'heure et
le fuseau. La page `/promos` garde sa simulation libre, qui n'a jamais touché à
la config.

`config_en_json` expose `fourchettes` en liste d'objets et **retire** les
`prix_min`/`prix_max` de la racine. Le contrat est verrouillé côté Python
(`tests/test_serialisation.py`), sans quoi un renommage ne produirait côté site
qu'un affichage vide, sans erreur.

## Tests

TDD, comme le reste du projet. Les cas qui comptent :

- **Migration** : les quatre variantes du tableau ci-dessus, plus le fait qu'une
  fourchette supprimée ne ressuscite pas au redémarrage (le piège que
  `_ecrire_salons` corrigeait déjà pour `salon_id`).
- **Le CSV est lu une seule fois** pour N fourchettes — vérifié par un compteur
  d'appels sur une source factice, pas par lecture du code.
- **Isolation** : une fourchette dont le salon échoue n'empêche pas la suivante ;
  `derniere_publication` est marquée si au moins un salon a reçu le post, et ne
  l'est pas si aucun ne l'a reçu.
- **Bornes par fourchette** : `find_promos` reçoit les bornes de *sa* fourchette.
- **Noms** : vide refusé, doublon refusé (casse comprise), inconnu refusé avec la
  liste des valides.
- **Sérialisation** : contrats de champs du site mis à jour.

Puis `tests/mutations.py` gagne des mutations sur la migration et sur la boucle
de publication — c'est là que les fautes seraient silencieuses. Une mutation qui
survit est un trou de couverture, pas un détail.

## Hors périmètre

- Limite au nombre de fourchettes : inutile, un seul administrateur configure.
- Heure, mention ou template par fourchette : voir « Portée » ci-dessus.
- Dédoublonnage inter-fourchettes : voir « Repêchage ».
- Réglage des fourchettes depuis le site : voir « Site et API ».
