# Bot Discord — Promotions Empire Immo

Poste chaque jour dans Discord les bâtiments **en promotion** du monde M8 dont
le prix tombe dans une fourchette configurable, avec un embed que tu dessines
toi-même sur [Discohook](https://discohook.org).

Fourchette par défaut : **100 TØ → 6 PØ**.

## Comment le bot lit les promotions

Dans l'export du jeu, la colonne `promotion` est un **pourcentage de remise**
(`17` = −17 %, `0` = pas de promo) et la colonne `valeur` est le **prix déjà
remisé** — ce que tu paies. Le bot en déduit :

```
prix_origine = valeur / (1 − promotion/100)
economie     = prix_origine − valeur
```

Les promotions retenues sont triées **du plus cher au moins cher**.

### Quand la fourchette est trop pauvre

Le bot vise **au moins 2 promotions** par post. S'il en trouve moins dans ta
fourchette, il complète avec celles dont le prix est **le plus proche d'un des
bords** — à écart égal, la plus chère.

Ces promos repêchées **ne sont pas signalées** : elles apparaissent comme les
autres. Le placeholder `{ecart}` reste disponible si tu veux malgré tout
afficher la distance à la fourchette (`0 Ø` pour une promo dans le budget).

S'il n'y a aucune promotion du tout dans l'export, le bot poste un simple
message le disant, pour que tu saches qu'il a bien tourné.

## Notation monétaire

Le jeu a ses propres symboles, qui ne suivent pas les préfixes SI. Le bot les
comprend à la saisie et les utilise à l'affichage.

| Symbole | Valeur | Nom | Symbole | Valeur | Nom |
|---|---|---|---|---|---|
| `K` | 10³ | mille | `R` | 10²⁷ | quadrilliard |
| `M` | 10⁶ | million | `Q` | 10³⁰ | quintillion |
| `G` | 10⁹ | milliard | `U` | 10³³ | quintilliard |
| `T` | 10¹² | billion | `S` | 10³⁶ | sextillion |
| `P` | 10¹⁵ | billiard | `X` | 10³⁹ | sextilliard |
| `E` | 10¹⁸ | trillion | `N` | 10⁴² | septillion |
| `Z` | 10²¹ | trilliard | `D` | 10⁴⁵ | septilliard |
| `Y` | 10²⁴ | quadrillion | | | |

La saisie est tolérante : `6P`, `6 P`, `50 6P`, `12,25M`, `1.5G`, `2,71 PØ`,
`840`. La casse est indifférente, le `Ø` final optionnel, et un montant
recopié depuis un message du bot est réutilisable tel quel.

## Installation

```bash
pip install -r requirements-dev.txt   # prod + pytest ; `requirements.txt` seul suffit à faire tourner le bot
cp .env.example .env                  # puis renseigne DISCORD_TOKEN
python -m src.main
```

Sans `DATABASE_URL`, la configuration reste **en mémoire** : pratique en
local, mais elle repart des valeurs de `.env` à chaque redémarrage.

### Créer l'application Discord

1. <https://discord.com/developers/applications> → **New Application**.
2. Onglet **Bot** → **Reset Token** → recopie le jeton dans `DISCORD_TOKEN`.
3. Onglet **OAuth2 → URL Generator** : scopes `bot` + `applications.commands`,
   permissions **Send Messages** et **Embed Links**. Ouvre l'URL générée pour
   inviter le bot.
4. Renseigne `GUILD_ID` avec l'ID de ton serveur : les commandes y
   apparaissent immédiatement au lieu d'attendre la propagation globale.

## Commandes

| Commande | Effet |
|---|---|
| `/promos [min] [max]` | Promotions à la demande ; sans argument, la fourchette configurée |
| `/config voir` | Affiche la configuration courante |
| `/config prix min max` | Définit la fourchette (ex : `min:100T max:6P`) |
| `/config heure heure [fuseau]` | Heure du post quotidien (`HH:MM`) |
| `/config retester` | Oublie la publication du jour pour retester le déclenchement |
| `/config salon ajouter salon` | Ajoute un salon de publication |
| `/config salon retirer salon` | Retire un salon de publication |
| `/config salon liste` | Liste les salons, en signalant ceux devenus inaccessibles |
| `/config mention [role]` | Rôle mentionné dans le post ; sans argument, aucune mention |
| `/config logs [salon]` | Salon de journal ; sans argument, journal désactivé |
| `/config acces ajouter membre` | Autorise un membre à utiliser les commandes |
| `/config acces retirer membre` | Lui retire cet accès |
| `/config acces liste` | Qui peut utiliser les commandes |
| `/source tester` | Teste la récupération des données **maintenant** et rend un compte rendu |
| `/source voir` | Affiche la source active (API ou fichier) |
| `/template charger fichier` | Charge ton export Discohook `.json` |
| `/template voir` | Renvoie le template actuel |
| `/template champs` | Liste tous les placeholders disponibles |
| `/apercu` | Prévisualise le post du jour sans publier |

## Qui peut utiliser les commandes

**Toutes** les commandes, `/promos` comprise, sont réservées :

- aux **administrateurs** du serveur, toujours ;
- aux membres ajoutés par `/config acces ajouter`.

Tout autre membre reçoit un refus visible de lui seul. Le contrôle est fait une
fois pour tout l'arbre des commandes (`ArbreProtege` dans `src/bot.py`), pas
commande par commande : une commande ajoutée plus tard est protégée d'office.
La version précédente vérifiait au cas par cas, et sept commandes étaient
restées ouvertes à tout le serveur.

Deux conséquences à connaître :

- **Gérer le serveur ne suffit plus.** Avant, cette permission ouvrait la
  configuration. Désormais il faut être administrateur ou figurer dans la liste.
- **Gérer la liste est réservé aux administrateurs.** Un membre autorisé peut
  tout faire *sauf* `/config acces ajouter|retirer` — sinon il pourrait
  s'ajouter des complices ou retirer celui qui l'a nommé. `/config acces liste`
  reste consultable par les membres autorisés.

Un administrateur ne peut pas se verrouiller dehors : son accès ne vient pas de
la liste. En revanche, s'il compte perdre son rôle d'admin plus tard, il doit
s'ajouter explicitement à la liste avant.

Les commandes restent visibles dans le menu Discord pour tous les membres :
les masquer passerait par `default_member_permissions`, qui les cacherait aussi
aux membres autorisés non-administrateurs.

## Publier dans plusieurs salons

`/config salon ajouter` autant de fois que nécessaire : le bot poste **le même
message dans tous les salons de la liste**. Une seule fourchette, une seule
recherche, une seule mention de rôle (`/config mention`) — les salons ne se
configurent pas séparément.

À l'ajout, le bot vérifie tout de suite qu'il a **Envoyer des messages** et
**Intégrer des liens** dans le salon, et refuse sinon : une permission
manquante découverte à l'heure du post serait un post perdu. `/config salon
liste` marque `⚠️ introuvable` un salon supprimé depuis.

**Si un salon échoue** (supprimé, permissions retirées entre-temps), les
autres reçoivent quand même le post, l'échec est signalé dans le journal, et
la journée est marquée publiée. Sans ça, le passage suivant reposterait dans
les salons déjà servis. En revanche, si **tous** les salons échouent, rien
n'est marqué et le prochain passage réessaie.

## Salon de journal

`/config logs #salon` fait raconter au bot ce qu'il fait, là où tu le
remarqueras :

```
✅ Publication · 4 promotions · 3/3 salons : #promos, #général, #annonces
⚠️ Publication partielle · 4 promotions · 2/3 salons : #promos, #général
-# ↳ #annonces : Forbidden: 403 Missing Permissions
❌ Échec · L'API a répondu 401 : Clé API invalide.
```

Le journal ne peut pas casser la publication : un salon de logs supprimé ou
sans permissions est signalé dans `bot.log` et rien d'autre. Il ne relaie que
des messages déjà assainis, donc la clé d'API n'y apparaît jamais.

`/config logs` sans argument désactive le journal.

## Personnaliser l'embed

Compose ton message sur Discohook, exporte le JSON, puis envoie-le avec
`/template charger`. Le template décrit **un seul bâtiment** : le bot le
duplique pour chaque promotion trouvée.

```json
{ "embeds": [{
    "title": "🏷️ {nom}",
    "description": "**{type}** · niveau {niveau}",
    "color": 3066993,
    "fields": [
      { "name": "Prix promo",   "value": "**{prix}**",              "inline": true },
      { "name": "Avant remise", "value": "~~{prix_origine}~~",      "inline": true },
      { "name": "Économie",     "value": "💰 {economie} (−{remise})", "inline": true }
    ],
    "footer": { "text": "{rang}/{total} • {monde} • MAJ {mise_a_jour}" }
}]}
```

**Placeholders** (`/template champs` les rappelle dans Discord) :

- Bâtiment : `{nom}` `{type}` `{niveau}` `{remise}` `{rang}` `{total}`
- Monde : `{monde}` `{taux_promoteur}` `{mise_a_jour}` `{date}`
- Montants : `{prix}` `{prix_origine}` `{economie}` `{loyer}` `{charge}`
  `{impot}` `{loyer_net}` `{construction}` `{embellissement}` `{reparation}`
  `{ecart}` (distance au bord de la fourchette, `0 Ø` si dedans)

`{hors_fourchette}` et `{dans_fourchette}` ont été retirés : un template qui
les contient encore reste valide, ils rendent simplement du vide.

Chaque montant accepte deux variantes :

| Forme | Rendu |
|---|---|
| `{prix}` | `302,62 KØ` |
| `{prix_long}` | `302 620 Ø` |
| `{prix_brut}` | `302620` |

Un placeholder mal orthographié est laissé tel quel et signalé au chargement.

## Déploiement sur Render

Le dépôt contient `render.yaml` : **New → Blueprint** crée le web service et
la base Postgres d'un coup.

1. Renseigne `DISCORD_TOKEN`, `GUILD_ID` et `EMPIRE_API_KEY` dans le dashboard.
2. Récupère la valeur générée de `TICK_TOKEN`.
3. Sur [cron-job.org](https://cron-job.org), crée un job **toutes les
   5 minutes** vers :
   ```
   https://<ton-service>.onrender.com/tick?token=<TICK_TOKEN>
   ```

Ce job unique remplit deux rôles : il empêche le service gratuit de
s'endormir, et il déclenche la publication. L'heure exacte du post reste
réglée par `/config heure` — le bot publie au premier ping suivant l'heure
prévue, et une seule fois par jour.

Le bot possède **aussi une boucle interne** qui vérifie l'heure chaque minute :
en local, ou sur un hébergement qui ne s'endort pas, `/config heure` suffit
sans cron externe. Les deux mécanismes coexistent sans doubler les posts.

### Fenêtre de rattrapage

Un post manqué est rattrapé pendant **60 minutes** (`FENETRE_RATTRAPAGE` dans
`src/schedule.py`), puis abandonné pour la journée. Sans cette borne, un bot
démarré à 16 h avec `heure = 09:00` publierait aussitôt « en retard de 7 h »,
consommant le quota du jour et empêchant la publication réellement voulue.

Régler `/config heure` **oublie automatiquement** la publication du jour : le
nouvel horaire s'applique tout de suite, sans attendre demain.

`GET /health` répond `ok` : c'est aussi la cible du health check Render.

## Source des données : API du jeu ou fichier local

Le bot lit l'export officiel du monde :

```
https://monde8.empireimmo.com/api/buildings_batiments_entreprise.csv?key=<ta clé>
```

Il suffit de renseigner **`EMPIRE_API_KEY`** dans les variables
d'environnement (dashboard Render, ou `.env` en local) : le bot bascule
aussitôt du fichier vers l'API, sans autre réglage. Sans clé, il lit
`buildings_batiments_entreprise.csv` du dépôt — pratique hors ligne et pour
les tests.

| Variable | Rôle |
|---|---|
| `EMPIRE_API_KEY` | Ta clé d'API. Sa présence suffit à activer l'API. |
| `EMPIRE_API_URL` | Optionnelle. Autre monde (`monde9…`) ou URL changée. `{api_key}` y est remplacé par la clé ; sans placeholder, `?key=…` est ajouté. |
| `CSV_PATH` | Fichier de secours quand aucune clé n'est fournie. |

**La clé n'apparaît jamais** — ni dans Discord, ni dans les logs, ni dans les
messages d'erreur : l'URL y est toujours masquée en `?key=***`.

### Vérifier que ça marche : `/source tester`

Après avoir renseigné une clé, `/source tester` fait un vrai appel et rend le
compte rendu :

```
✅ Données accessibles
🌐 API Empire Immo
-# https://monde8.empireimmo.com/api/…csv?key=***

Réponse : 412 ms · 24 318 caractères     Bâtiments : 116
En promotion : 4
Export : Empire Immo - M8 · mise à jour 2026-07-28 08:00:07
Promotions trouvées : Entrepôt inexploitable, Zone portuaire désaffectée, …
```

Si la clé est refusée, l'embed passe au rouge et reprend le message de l'API
plutôt qu'une erreur Discord opaque :

```
❌ Données inaccessibles
Erreur : L'API a répondu 401 : Clé API invalide ou révoquée. Vérifie EMPIRE_API_KEY.
```

La commande teste **la source, pas la fourchette** : « 0 promotion » n'est pas
un échec, c'est peut-être simplement le cas du jour. Pour voir le post tel
qu'il sortira, utilise `/apercu`. `/source voir` rappelle la source active
sans appel réseau.

Le bot distingue les pannes et les explique en clair, en reprenant le message
de l'API quand elle en fournit un :

| Situation | Message |
|---|---|
| Clé refusée (401/403) | `L'API a répondu 401 : Clé API invalide ou révoquée. Vérifie EMPIRE_API_KEY.` |
| Panne serveur (5xx) | `L'API a répondu 500 : … Réessai au prochain passage.` |
| Injoignable / trop lente | `API injoignable (…)` — délai maximal 30 s |
| Réponse qui n'est pas un CSV | Refusée explicitement, plutôt que parsée en « 0 bâtiment » |

Une panne à l'heure du post **ne consomme pas** la publication du jour : les
données sont chargées avant d'écrire quoi que ce soit, donc le prochain
passage réessaie.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

286 tests couvrent la notation monétaire, le parsing du CSV (entiers de
21 chiffres, notation scientifique), le calcul des remises, le repêchage hors
fourchette, le rendu du template, les limites Discord, le planning (fenêtre
de rattrapage, idempotence quotidienne), l'API du jeu (construction de l'URL,
erreurs 401/5xx, non-fuite de la clé, réponses non-CSV), la publication
multi-salon (échec partiel, échec total, salon supprimé), le salon de journal
(qui ne doit jamais échouer), le contrôle d'accès (toutes les commandes
protégées, admin jamais verrouillé dehors, liste non modifiable par un membre
autorisé), les commandes slash (exécutées hors ligne, embeds inspectés) et les
endpoints HTTP.

## Structure

```
src/money.py     échelle Ø du jeu (format + saisie)
src/promos.py    parsing CSV, calcul des remises, filtre et tri
src/source.py    provenance des données (API du jeu ou fichier local)
src/template.py  substitution des {placeholders} Discohook
src/publish.py   assemblage des embeds et limites Discord
src/bot.py       client Discord et commandes slash
src/journal.py   compte rendu dans le salon de logs
src/schedule.py  « est-ce l'heure de publier ? »
src/db.py        configuration persistante (Postgres)
src/web.py       /health et /tick
src/main.py      point d'entrée
```
