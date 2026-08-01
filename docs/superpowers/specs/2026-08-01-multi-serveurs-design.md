# Support multi-serveurs

**Date** : 2026-08-01
**Dépôts** : `D:\bot` (bot Discord), `D:\eiweb` (site de contrôle)

## Le besoin

Les salons où publier les promotions sont répartis sur **deux serveurs Discord**
appartenant à la même entreprise. Le bot doit y publier indifféremment, et le
site doit montrer clairement quel salon vit sur quel serveur.

Deux serveurs, pas « N serveurs pour N clients » : la config reste **globale**,
il n'y a pas de cloisonnement par serveur à construire. C'est ce qui garde le
changement petit.

## Ce qui marche déjà, et ne change pas

`fourchette["salons"]` stocke des **ids de salons nus**, sans serveur.
`EmpireBot.resoudre_salon` (`src/bot.py:171`) fait `get_channel` puis
`fetch_channel` : deux appels qui traversent tous les serveurs où le bot est
présent.

**La publication n'a donc besoin d'aucun changement.** Une fois le bot invité
sur le second serveur, il sait y publier. Une fourchette peut mélanger des
salons des deux serveurs — cohérent avec l'usage : même entreprise, mêmes
promotions.

Corollaire à préserver : `fourchette["salons"]` **reste une liste d'ids**. Y
glisser des objets casserait la boucle de publication, `salonsUniques`,
`nombreEnvois` et une bonne partie des 45 mutations, pour un gain purement
cosmétique.

## Décisions arrêtées

| Sujet | Choix | Conséquence acceptée |
|---|---|---|
| Qui commande | **Chaque admin de chaque serveur** | Un admin d'un serveur déclaré peut tout changer, pour tous les serveurs |
| Désigner un salon | **Taper la commande dans son serveur** | Le sélecteur Discord reste tel quel, pas de saisie d'id |
| Mention de rôle | **Un rôle par serveur** | `/config mention` vaut pour le serveur courant |
| Noms de salons | **Stockés en base au réglage** | Un nom peut être périmé jusqu'au prochain rafraîchissement |

## 1. `GUILD_IDS` : les commandes sur plusieurs serveurs

`settings.GUILD_ID` synchronise les commandes sur **un seul** serveur
(`src/bot.py:126`). Sur le second, aucune commande n'apparaîtrait.

```python
GUILD_IDS = [id.strip() for id in os.getenv("GUILD_IDS", GUILD_ID).split(",") if id.strip()]
```

`setup_hook` boucle sur la liste. `GUILD_ID` reste lu en repli, pour ne pas
casser le `.env` local ni la variable déjà définie sur Render.

Pourquoi une liste explicite plutôt que la synchronisation globale
(`tree.sync()` sans serveur) :

- **immédiat** — la propagation globale met jusqu'à une heure ; par serveur,
  c'est instantané ;
- **les commandes n'existent que sur les serveurs déclarés** — si le bot est un
  jour invité ailleurs, aucune commande n'y apparaît ;
- `GUILD_IDS` vide retombe sur la synchro globale, donc un déploiement qui ne
  déclare rien continue de fonctionner.

**Piège au déploiement** : des commandes déjà synchronisées globalement
subsistent **en plus** de celles par serveur, et apparaissent en double dans le
sélecteur. À constater au déploiement, pas à coder à l'aveugle — noté dans la
checklist du README.

## 2. Un rôle par serveur

Un rôle appartient à un serveur. `config["role_id"]` est global, donc le bot
enverrait `<@&123>` dans le second serveur où ce rôle n'existe pas : Discord
affiche `@deleted-role`, visible seulement en lisant le post.

```python
config["roles"] = {"<id serveur>": "<id rôle>"}
```

- `/config mention` enregistre le rôle pour `interaction.guild.id` ;
- `/config mention` sans argument efface celui du serveur courant, pas les
  autres ;
- à la publication, le rôle est résolu depuis `salon.guild.id` — un salon du
  serveur B mentionne le rôle de B, ou rien si B n'en a pas ;
- `publish.envoyer` garde sa signature `role_id: str | None`, l'appelant résout.
  Le module ne doit pas apprendre ce qu'est un serveur.

**Migration : `role_id` sert de repli, il n'est pas converti.**

La conversion « ce rôle appartient au serveur X » demanderait de résoudre un
salon pour connaître son serveur — donc un accès à Discord. Or `Store` ne parle
qu'à Postgres, et lui donner un client Discord pour ça mélangerait deux
responsabilités qui sont séparées dans tout le reste du code.

La règle est donc plus simple, et sans I/O :

- `roles` non vide → on y cherche le serveur du salon, et **on ignore**
  `role_id` ;
- `roles` vide et `role_id` présent → `role_id` s'applique à tous les salons.
  C'est exactement le comportement actuel, et avec un seul serveur il n'y a
  qu'une réponse possible ;
- au premier `/config mention`, `roles` est écrit et `role_id` effacé.

Un test doit couvrir le cas mixte — `roles` réglé pour le serveur A, `role_id`
encore en base : le serveur B ne doit **pas** hériter du vieux `role_id`, sinon
un rôle qu'on croit remplacé continuerait d'être mentionné ailleurs.

## 3. Noms de salons et de serveurs

Le site n'a pas accès à Discord : il ne connaît ni les noms de salons ni ceux des
serveurs. Avec un seul serveur, afficher `123456` était austère ; avec deux,
c'est ambigu — rien ne dit d'où vient le salon.

Le bot connaît le salon **et** son serveur au moment du réglage. Il les écrit :

```python
config["serveurs"] = {"999": "Empire Immo"}
config["salons_connus"] = {"123": {"nom": "promos", "serveur": "999"}}
```

Deux tables plates, et non un objet par salon dans chaque fourchette : un salon
servant deux fourchettes a son nom stocké **une seule fois**. Deux copies
finiraient par diverger et afficheraient deux noms pour un même salon.

Ainsi `/api/config` ne dépend **pas** de l'état de la connexion Discord : les
noms viennent de la base, pas d'une résolution en direct.

**Les noms peuvent être périmés** — un salon renommé garde son ancien nom en
base. Le bot les rafraîchit chaque fois qu'il résout un salon : à la publication
quotidienne et à `/fourchette liste`. Ils se corrigent donc au premier post. Le
site affiche un nom, jamais une garantie.

**Nettoyage** : un salon retiré de toutes les fourchettes voit son entrée
effacée, sinon la table grossit indéfiniment.

**Salon sans nom connu** (ajouté avant cette mise à jour) : affiché en id nu.
L'absence de nom n'est pas une erreur.

`logs_salon_id` reste global : un salon unique, sans ambiguïté de serveur.

### Les trois nouvelles clés ne vont pas dans `config_par_defaut()`

`settings.config_par_defaut()` ne doit **pas** déclarer `roles`, `serveurs` ni
`salons_connus`. Deux raisons, l'une déjà documentée dans le code :

- `_CHAMPS_PLATS` (`src/db.py:37`) traite la présence de certains champs comme la
  signature d'un bot à migrer ; toute écriture part donc de
  `Store._enregistree()`, pas de `config()`. Matérialiser des défauts en base est
  précisément ce que le code évite.
- Un dictionnaire vide comme défaut est indistinguable de « jamais réglé ». Les
  lecteurs (`Store.roles()`, `Store.salons_connus()`) renvoient `{}` d'eux-mêmes
  quand la clé est absente.

`_CHAMPS_PLATS` **reste inchangé** : les nouvelles clés ne sont pas des champs
plats à migrer, leur absence est un état normal.

## 4. Le site

`lib/bot.ts` — le contrat change :

```typescript
roles: Record<string, string>;        // remplace role_id: string | null
serveurs: Record<string, string>;
salons_connus: Record<string, { nom: string; serveur: string }>;
```

Nouvelle fonction pure dans `lib/fourchettes.ts`, tests écrits d'abord :

```typescript
grouperParServeur(fourchettes, salonsConnus, serveurs): ServeurAffiche[]
```

Elle regroupe les salons par serveur pour l'affichage. Un salon inconnu tombe
dans un groupe « serveur inconnu » plutôt que de disparaître : perdre
silencieusement un salon de la liste ferait croire qu'il n'est pas configuré.

Pages :

- **Fourchettes** (`app/page.tsx`, `app/reglages/page.tsx`) : salons groupés par
  serveur, `Empire Immo — #promos, #grosses-affaires`.
- **Mention** : une ligne par serveur, « aucune » explicite pour un serveur sans
  rôle. Une seule valeur laisserait croire que les deux serveurs sont pingués.
- **Vignette « Salons servis »** : inchangée, plus le nombre de serveurs.

`PATCH /api/config` n'accepte toujours que `heure` et `fuseau` : `roles`,
`serveurs` et `salons_connus` désignent des objets Discord, donc restent réglés
par commande. Une clé de plus refusée en 400.

## Sécurité : inviter le bot, c'est en donner les clés

Conséquence directe du choix « chaque admin de chaque serveur » : `est_admin`
vient de `guild_permissions.administrator` **du serveur où la commande est
tapée** (`src/bot.py:83`), et la config est globale. Un administrateur d'un
serveur déclaré dans `GUILD_IDS` peut donc changer les prix, l'heure, le
template, et s'ajouter à la liste d'accès — pour tous les serveurs.

Sur des serveurs appartenant à la même entreprise, c'est sans risque réel et
c'est le comportement voulu. Mais ça doit être **écrit à côté du lien
d'invitation** dans le README, plutôt que découvert.

Ce que `GUILD_IDS` limite : un serveur non déclaré n'a **aucune** commande. Le
bot peut y publier si on lui donne un salon, mais personne n'y a de prise sur la
configuration.

## Tests

Bot — écrits avant le code :

- `resoudre_salon` sur un salon d'un autre serveur (non couvert aujourd'hui) ;
- publication vers deux salons de deux serveurs, chacun mentionnant **son** rôle ;
- un serveur sans rôle configuré → post sans mention, pas de mention vide ;
- `role_id` seul en base → s'applique partout (comportement actuel préservé) ;
- `roles` réglé pour A **et** `role_id` encore en base → B n'hérite de rien ;
- `/config mention` sur le serveur B ne touche pas le rôle de A ;
- `/config mention` sans argument sur B n'efface que le rôle de B ;
- nom de salon enregistré au réglage, rafraîchi quand il a changé ;
- salon retiré de toutes les fourchettes → entrée effacée ;
- salon sans nom connu → affiché en id, sans erreur ;
- `setup_hook` synchronise sur chacun des ids de `GUILD_IDS`.

Site — tests d'abord, `node --test` :

- `grouperParServeur` : deux serveurs, salon inconnu, serveur inconnu, liste vide ;
- contrat `test_contrat_*` côté Python figeant `roles`, `serveurs`,
  `salons_connus`.

Mutations à ajouter :

- `roles` ignoré à la publication (mention envoyée partout) ;
- rôle d'un autre serveur utilisé ;
- `GUILD_IDS` tronqué au premier id ;
- nom de salon jamais rafraîchi ;
- `salons_connus` non nettoyé ;
- `grouperParServeur` perdant les salons inconnus.

## Ce que cette spec ne fait pas

- **Pas de config par serveur.** Prix, heure, fuseau, template et liste d'accès
  restent globaux. Deux serveurs de la même entreprise veulent les mêmes
  promotions.
- **Pas de cloisonnement des droits.** Décision explicite ci-dessus.
- **Pas de saisie d'id de salon à la main.** Les commandes se tapent dans le
  serveur concerné, le sélecteur Discord reste tel quel.
