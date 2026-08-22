# Bot Discord — Promotions Empire Immo

Poste chaque jour dans Discord les bâtiments **en promotion** du monde M8 dont
le prix tombe dans une fourchette configurable, avec un embed que tu dessines
toi-même sur [Discohook](https://discohook.org).

Plusieurs fourchettes, chacune avec **ses propres salons** : les grosses affaires
dans un salon, les petits prix dans un autre. Un bot neuf n'en a aucune et ne
publie donc rien — `/promos ajouter` puis `/promos salon ajouter`.

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
fourchette, il cherche ailleurs, dans cet ordre :

1. **la zone de tolérance**, si tu en as réglé une (voir ci-dessous) ;
2. **le repêchage** : les promotions dont le prix est le plus proche d'un des
   bords de la fourchette — à écart égal, la plus chère.

Le repêchage est **par fourchette** : chacune complète le sien, indépendamment
des autres. Un même bâtiment peut donc apparaître dans deux posts, ce qui est
préférable à un post vide dans un salon qui attend sa liste.

### La zone de tolérance

Le repêchage prend le plus proche, quel qu'il soit. Sur une fourchette
`100T → 6P`, un bâtiment à 10 MØ est dix millions de fois trop petit mais reste
« proche » de 100 TØ à l'échelle des prix du jeu, et il sera choisi.

La zone de tolérance sert à dire ce que tu accepterais vraiment :

```
/promos tolerance fourchette:grosses min:50T max:8P
```

Désormais, quand « grosses » n'a pas ses deux promotions, le bot cherche
**d'abord entre 50 TØ et 8 PØ** — les plus proches de la fourchette idéale
d'abord. Il ne repêche au-delà que si la zone ne suffit pas.

La zone doit être **plus large que la fourchette** : elle n'a le droit que
d'ajouter des candidats, jamais d'en retirer. Une zone plus étroite est refusée,
parce que c'est presque toujours les bornes idéales retapées par erreur. Tu
peux l'élargir d'un seul côté (`min:100T max:8P` : accepter de payer plus cher,
sans accepter d'acheter plus petit).

Sans bornes, la commande efface la zone :

```
/promos tolerance fourchette:grosses
```

Si `/promos prix` élargit ensuite les bornes idéales au-delà de la zone,
celle-ci est élargie d'autant et la commande le signale — une zone qui exclut
une partie de sa propre fourchette n'aurait aucun sens.

Ces promos repêchées **ne sont pas signalées** : elles apparaissent comme les
autres. Le placeholder `{ecart}` reste disponible si tu veux malgré tout
afficher la distance à la fourchette (`0 Ø` pour une promo dans le budget).

S'il n'y a aucune promotion du tout dans l'export, le bot poste un simple
message le disant, pour que tu saches qu'il a bien tourné.

### Écarter des types de bâtiments

Le jeu range chaque bâtiment sous un type — `zones`, `bureaux`, `transport`,
`industriels`, `commerciaux`. Une entreprise qui n'achète jamais de transport
peut les faire taire :

```
/promos types liste          ce qui est écarté, et ce qui reste
/promos types exclure type:transport
/promos types remettre type:transport
```

Les noms viennent de l'export lui-même : ils sont proposés sous le curseur, et
un nom que l'export ne contient pas est refusé — accepté, il donnerait un filtre
qui ne filtre rien, et le post inchangé ne dirait pas pourquoi.

Le réglage est **par serveur** : deux entreprises n'ont pas les mêmes goûts.
Il vaut partout à la fois — le post du soir, `/promos apercu` et
`/promos chercher` — sinon l'aperçu montrerait des promotions qui ne sortiront
pas.

**Il n'y a pas de repêchage de ce qu'on a écarté.** Un type écarté ne revient
pas par la zone de tolérance ni par le repêchage, même le jour où il n'y a rien
d'autre : c'est ce qui distingue une exclusion d'une préférence. Un post peut
donc être plus court, voire ne pas sortir du tout — et c'est alors le message
habituel des jours sans promotion qui part. Le bot refuse en revanche d'écarter
le **dernier** type restant : un post vide tous les soirs ressemblerait trait
pour trait à une panne.

`/reglages voir` rappelle les types écartés, mais seulement s'il y en a — c'est
là qu'on relit la configuration quand on cherche pourquoi un post a maigri.
`/promos types remettre` ne charge pas l'export : défaire un réglage ne doit pas
dépendre de l'API du jeu, et c'est le seul chemin qui rende un type disparu de
l'export.

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

Le séparateur décimal affiché est le **point**, comme dans le jeu (`4.58P`) :
les deux tableaux se recoupent ainsi à l'œil, sans traduction mentale.

La saisie, elle, est tolérante : `6P`, `6 P`, `50 6P`, `12.25M`, `1,5G`,
`2.71 PØ`, `840`. Le point et la virgule sont acceptés tous deux comme
séparateur décimal, la casse est indifférente, le `Ø` final optionnel, et un
montant recopié depuis un message du bot est réutilisable tel quel.

### Deux calculatrices

Le bot affiche toujours un montant dans le plus grand palier qui tient
(`2.71 PØ`). Le jeu, lui, en choisit parfois un autre pour le même montant, et
recouper les deux à la main est fastidieux — d'où deux calculatrices, rangées
sous `/convertir` parce qu'elles font la même chose : rendre un montant à partir
d'un autre, sans toucher à aucun réglage.

`/convertir montant` impose le palier d'arrivée par sa case `vers` : `2.71P` vers
`T` donne `2 710.57 TØ`. Le palier se choisit dans un menu déroulant — les symboles ne
suivent pas les préfixes SI, donc personne ne les tape de mémoire. La mantisse
peut dépasser 1 000 ou tomber sous 1 : c'est le but, et le bot ne rebascule pas
sur un autre symbole comme il le fait ailleurs.

`/convertir frais` calcule les frais de gestion, **7 % sans décimales** — le jeu
ne facture pas de fraction d'Ø. L'arrondi est au plus proche, comme partout dans
le bot. Elle ne fait que calculer : c'est `/frais releve` qui enregistre.

Les deux calculatrices rappellent le montant de départ tel qu'elles l'ont compris
(la seule façon de vérifier que `50 6P` a bien été lu comme 506 PØ), donnent le
résultat en notation courte **et** en chiffres complets (on ne paie pas
« 189.70 TØ »), et répondent en privé.

Le mot « frais » sert donc deux fois dans le menu, et sans ambiguïté :
`/convertir frais` **calcule** sur un montant qu'on lui donne, `/frais` **liste et
publie** ce qu'il y a à payer. Le premier est un calcul, le second un sujet — et
c'est pour libérer ce nom-là que les deux calculatrices sont sous un seul mot.

## Les frais par filiale

`/frais releve filiale:ARMEE DE TERRE montant:2,71P` comprend le montant
comme les **bénéfices** de cette filiale : il calcule les 7 %, enregistre le
relevé et annonce le total de toutes les filiales. Les deux cases sont
obligatoires — un relevé sans montant, ou sans nom, n'est pas un relevé.

C'était longtemps la calculatrice, alors nommée `/frais`, avec une case `filiale`
facultative : la même commande écrivait en base ou pas selon qu'on l'avait
remplie, et rien dans son nom ne prévenait celui qui la tapait. Deux commandes,
donc, dont une seule laisse quelque chose derrière elle.

Le nom est celui du jeu, conservé caractère pour caractère (doubles espaces
compris) : c'est la clé d'import. Il se complète tout seul dès la deuxième
saisie ; retapé de mémoire, une faute de frappe créerait une **seconde** filiale
au lieu de mettre à jour la première. Ressaisir une filiale remplace son relevé
et le dit.

Une filiale qui ne gagne rien ne paie rien : le jeu ne rembourse pas, donc des
bénéfices nuls ou négatifs donnent 0 Ø, et la ligne est marquée « en perte » —
un 0 Ø muet se lirait comme une saisie oubliée.

Chaque jour à l'heure réglée par `/frais heure`, le bot publie un tableau dans
les salons de `/frais salon ajouter` : une ligne par filiale des frais les plus
lourds aux plus légers, chaque montant en notation courte **et** en chiffres
complets, puis le total. Les relevés qui ne datent pas du jour portent leur date,
pour repérer ceux qu'on a oublié de mettre à jour.

```
🏢 Frais de gestion des filiales
🥇 ARMEE  DE TERRE — `189 740 105 419 196 Ø` · 189.74 TØ
▫️ MARINE NATIONALE — `8 712 753 443 Ø` · 8.71 GØ
⏳ LOGISTIQUE — `68 600 Ø` · 68.60 KØ · relevé du 9 août
🔻 CHANTIERS — en perte, rien à payer
🧾 Total · 4 filiales
`189 748 818 241 239 Ø` · 189.75 TØ
mardi 11 août 2026
```

Les emojis y disent un **état** et rien d'autre : le poste le plus lourd (🥇),
une filiale en perte (🔻), un relevé qui n'a pas été remis à jour (⏳). Dans une
liste de vingt lignes, un pictogramme identique partout ne servirait à rien.

Le montant à payer est en `code` parce qu'un appui long le copie alors seul dans
Discord ; sinon il faudrait sélectionner vingt-un chiffres à la main. Le nombre
de lignes est plafonné à 40, mais c'est un **budget de caractères** qui tranche
en dernier ressort : une description d'embed plafonne à 4096, comptés en UTF-16
où un emoji pèse deux, et un dépassement ferait refuser le tableau en entier.
Les filiales non affichées sont comptées sous la liste, et le total les inclut.

### Recommencer un cycle, vider le tableau

`/frais vider` remet tous les bénéfices à 0 Ø **en gardant les noms**. Ils sont
la clé d'import du jeu et l'assise de l'autocomplétion : les garder fait qu'un
nouveau cycle ne demande que de ressaisir les montants, un `/frais releve` à la
fois. Les filiales restent donc listées, marquées « en perte » — ce qui est exact,
il n'y a rien à prélever sur zéro. C'est bien les **montants** qu'elle vide, pas
la liste ; pour perdre les noms aussi, c'est `/frais retirer filiales:tout`.

`/frais retirer` accepte un nom, un lot, ou `tout`. Discord n'offre pas de
champ répétable, donc les noms arrivent dans une chaîne, séparés par des virgules
ou collés d'une liste, un par ligne. Les espaces **internes** des noms survivent
au découpage, doubles compris, et les noms introuvables sont dits plutôt que
fatals : sinon on croirait une filiale supprimée alors qu'elle reviendrait dans le
tableau du soir.

La case `confirmer` n'est exigée que pour un lot ou pour `tout` — c'est là que le
geste devient irrattrapable de mémoire, personne ne se souvenant des montants de
cinq filiales. Sur un nom unique, une cérémonie apprendrait à cocher sans lire, et
la case ne protégerait plus ce qu'elle est là pour protéger. Et **il n'y a pas de
base d'essai** : l'écriture va dans la base courante, production comprise.

### Renvoyer les frais dans le jeu

`/frais export` rend un `.txt` au format d'import du jeu, une filiale par
ligne :

```
MEGAPOLE→189740105419196␍␊
ARMEE  DE TERRE→0␍␊
```

**Pourquoi une pièce jointe et non un bloc de code**, alors qu'on pourrait le
copier depuis le message : parce que le format ne survivrait pas au message. La
tabulation ne se saisit pas dans Discord — la touche sert à l'autocomplétion et
n'insère rien — et Discord normalise les fins de ligne du contenu d'un message,
donc aucun bloc ne peut porter le CRLF que le jeu attend. Les octets d'une pièce
jointe, eux, arrivent tels qu'ils ont été écrits.

Les montants sont les **frais**, en chiffres seuls : le format réclame ce qu'on
doit, et `format_money` arrondirait à `189,74 TØ`, soit un montant que personne
ne paie. Les filiales en perte y sont, à `0` — une ligne par filiale, et zéro est
exact. L'ordre est celui de l'enregistrement, pas un tri : le fichier est une
entrée machine, et trié, deux exports des mêmes filiales différeraient dès qu'un
montant bouge. Les noms sortent caractère pour caractère, doubles espaces
compris, parce que c'est la clé d'import du jeu ; une tabulation ou un retour à la
ligne **collés** dans un nom sont remplacés par un espace, sans quoi la ligne
porterait deux colonnes et le jeu la refuserait — et la réponse dit lesquels, car
rien d'autre n'expliquerait ensuite l'échec de l'import.

Ce tableau est une publication **indépendante** de celle des promotions : sa
propre heure, ses propres salons, sa propre marque du jour. Les deux ne peuvent
donc pas se voler leur quota quotidien, et la panne de l'export du jeu — dont le
tableau ne dépend pas, ses données étant saisies à la main — ne le fait pas taire.

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
   
   **⚠️ Inviter le bot sur un serveur revient à en donner les clés — celles de ce
   serveur-là.** `est_admin` vient des permissions du serveur où la commande est
   tapée, et chaque serveur a sa propre configuration : un administrateur ne
   change les prix, les heures, le template et la liste d'accès que **chez lui**.
   Restent communs à tous : la source des données (donc la clé d'API du jeu, que
   `/reglages source tester` fait appeler) et ce que lit le site de contrôle. Un
   serveur **non** déclaré dans `GUILD_IDS` n'a aucune commande du bot.
   
4. Renseigne `GUILD_IDS` avec les IDs de tes serveurs (séparés par des virgules) :
   les commandes y apparaissent immédiatement au lieu d'attendre la propagation
   globale. `GUILD_ID` (singulier) reste accepté en repli pour un seul serveur.

## Commandes

| Commande | Effet |
|---|---|
| `/convertir montant` (`montant`, `vers`) | Exprime un montant dans un autre palier (`2.71P` → `2 710.57 TØ`) |
| `/convertir frais montant` | Frais de gestion (7 %, sans décimales) — calcule seulement |
| `/promos chercher [min] [max]` | Promotions à la demande ; sans argument, l'**union** des fourchettes |
| `/promos liste` | Les fourchettes, leurs bornes et leurs salons |
| `/promos ajouter fourchette min max` | Crée une fourchette (ex : `fourchette:grosses min:100T max:6P`) |
| `/promos prix fourchette min max` | Modifie ses bornes, en gardant ses salons |
| `/promos tolerance fourchette [min] [max]` | Zone acceptée quand la fourchette est trop pauvre ; sans bornes, l'efface |
| `/promos supprimer fourchette` | Supprime une fourchette et ses salons |
| `/promos salon ajouter fourchette salon` | Publie **cette** fourchette dans ce salon |
| `/promos salon retirer fourchette salon` | Cesse de l'y publier |
| `/promos types liste` | Types de bâtiments écartés dans ce serveur, et ceux qui restent |
| `/promos types exclure type` | N'affiche plus jamais les promotions de ce type |
| `/promos types remettre type` | Les affiche de nouveau |
| `/promos heure [heure]` | Heure des promotions (`HH:MM`), distincte de celle du tableau ; sans argument, l'affiche |
| `/promos apercu` | Prévisualise les posts du jour, un par fourchette, sans publier |
| `/promos publier` | Publie les promotions maintenant, à la place de celles de l'heure prévue |
| `/frais liste` | Les filiales enregistrées, leurs frais et le total |
| `/frais releve filiale montant` | Enregistre les bénéfices d'une filiale pour le tableau du jour |
| `/frais export` | Le tableau en `.txt` au format d'import du jeu (`nom`+tab+`frais`, CRLF) |
| `/frais retirer filiales [confirmer]` | Oublie une filiale, un lot (noms séparés par des virgules), ou `tout` |
| `/frais vider confirmer` | Remet tous les bénéfices à 0 Ø en gardant les noms — nouveau cycle |
| `/frais salon ajouter salon` | Publie le tableau des frais dans ce salon |
| `/frais salon retirer salon` | Cesse de l'y publier |
| `/frais heure [heure]` | Heure du tableau des frais (`HH:MM`), distincte de celle des promotions ; sans argument, l'affiche |
| `/frais apercu` | Prévisualise le tableau sans publier ni consommer le post du jour |
| `/frais publier` | Publie le tableau maintenant, à la place de celui de l'heure prévue |
| `/reglages voir` | Affiche la configuration de ce serveur |
| `/reglages importer` | Reprend dans ce serveur l'ancienne configuration commune, ses salons seulement |
| `/reglages fuseau fuseau` | Fuseau horaire des publications de ce serveur (ex : `Europe/Paris`) |
| `/reglages mention [role]` | Rôle mentionné dans le post ; sans argument, aucune mention |
| `/reglages logs [salon]` | Salon de journal ; sans argument, journal désactivé |
| `/reglages modules liste` | Les modules trouvés et leur état dans ce serveur |
| `/reglages modules activer module` | Rallume un module dans ce serveur |
| `/reglages modules desactiver module` | L'éteint : ses commandes quittent le menu et ses publications se taisent |
| `/reglages acces ajouter membre` | Autorise un membre à utiliser les commandes |
| `/reglages acces retirer membre` | Lui retire cet accès |
| `/reglages acces liste` | Qui peut utiliser les commandes |
| `/reglages source tester` | Teste la récupération des données **maintenant** et rend un compte rendu |
| `/reglages source voir` | Affiche la source active (API ou fichier) |
| `/reglages template charger fichier` | Charge ton export Discohook `.json` |
| `/reglages template voir` | Renvoie le template actuel |
| `/reglages template champs` | Liste tous les placeholders disponibles |

## Un fichier par module

Tout ce que fait le bot, sauf `/reglages`, vient d'un fichier de `src/modules/`.
Le dossier est **balayé au démarrage** : il n'y a aucune liste à tenir à jour
ailleurs. Ajouter une fonctionnalité, c'est poser un fichier et déployer ; la
retirer, c'est enlever le fichier.

| Module | Ce qu'il apporte | Publications |
|---|---|---|
| `conversion` | `/convertir` | aucune |
| `promos` | `/promos` | une : les promotions |
| `frais` | `/frais` | une : le tableau des frais |

Chaque fichier déclare un `Module` — son `nom` (la clé qu'on tape dans
`/reglages modules`), son `titre`, sa `description`, son `ordre` dans le menu, la
fonction qui greffe ses commandes, et ses `publications`. Une publication tient
en une déclaration : son heure, ses salons, ce qu'elle envoie. La mécanique
commune (compte à rebours, « déjà publié aujourd'hui ? », boucle sur les salons,
pannes salon par salon) est écrite **une fois**, dans `src/tournee.py`, et elle
donne à chaque publication ses `heure`, `apercu` et `publier` — les mêmes mots
pour toutes. Le `salon ajouter|retirer` est greffé de même, sauf si le module le
décline : les salons des promotions appartiennent aux **fourchettes**, pas à la
publication, d'où `/promos salon ajouter fourchette salon`.

Il n'y a **pas de nombre maximum** de publications : un module peut en déclarer
deux, un récapitulatif le matin et une alerte le soir, chacune avec son heure,
ses salons et sa propre marque de passage. Une qui échoue n'empêche pas les
autres.

**Un module cassé ne casse pas le bot.** Un fichier qui refuse de se charger est
ignoré, le démarrage continue, et le bot le nomme dans le salon de logs — sans
quoi un module en cours d'écriture couperait les publications de tous les
serveurs.

**On ne dépose pas un module sur le bot en marche** : ce serait exécuter du code
arbitraire arrivé par Discord, et le disque de Render étant effacé à chaque
redémarrage, le fichier disparaîtrait de lui-même. Un module passe par le dépôt
et un déploiement. L'activation par serveur, elle, est immédiate.

`/reglages` n'est pas un module : c'est le noyau, toujours présent. Le bot refuse
d'éteindre le dernier module actif, et `/reglages` reste dans le menu quoi qu'il
arrive — sans lui, un serveur qui a tout éteint ne pourrait plus rien rallumer.
Une commande éteinte est refusée à **deux** endroits : elle quitte le menu du
serveur, et le gardien de l'arbre la rejette à l'exécution. Le second verrou est
nécessaire parce que Discord garde le menu en cache chez le client.

## Qui peut utiliser les commandes

**Toutes** les commandes, `/promos` comprise, sont réservées :

- aux **administrateurs** du serveur, toujours ;
- aux membres ajoutés par `/reglages acces ajouter`.

Tout autre membre reçoit un refus visible de lui seul. Le contrôle est fait une
fois pour tout l'arbre des commandes (`ArbreProtege` dans `src/bot.py`), pas
commande par commande : une commande ajoutée plus tard est protégée d'office.
La version précédente vérifiait au cas par cas, et sept commandes étaient
restées ouvertes à tout le serveur.

Deux conséquences à connaître :

- **Gérer le serveur ne suffit plus.** Avant, cette permission ouvrait la
  configuration. Désormais il faut être administrateur ou figurer dans la liste.
- **Gérer la liste est réservé aux administrateurs.** Un membre autorisé peut
  tout faire *sauf* `/reglages acces ajouter|retirer` — sinon il pourrait
  s'ajouter des complices ou retirer celui qui l'a nommé. `/reglages acces liste`
  reste consultable par les membres autorisés.

Un administrateur ne peut pas se verrouiller dehors : son accès ne vient pas de
la liste. En revanche, s'il compte perdre son rôle d'admin plus tard, il doit
s'ajouter explicitement à la liste avant.

Les commandes restent visibles dans le menu Discord pour tous les membres :
les masquer passerait par `default_member_permissions`, qui les cacherait aussi
aux membres autorisés non-administrateurs.

## Plusieurs fourchettes, plusieurs salons

Une fourchette porte **ses bornes et ses salons** :

```
/promos ajouter fourchette:grosses min:100T max:6P
/promos salon ajouter fourchette:grosses salon:#affaires
/promos salon ajouter fourchette:grosses salon:#général

/promos ajouter fourchette:petits min:100K max:1G
/promos salon ajouter fourchette:petits salon:#débutants
```

À l'heure dite, **un seul passage** publie tout : un post par fourchette et par
salon, chacun avec sa propre recherche et son propre repêchage. Ici, trois posts.
L'heure, le fuseau, la mention de rôle, le salon de logs et le template sont
communs à toutes les fourchettes **de ce serveur** — ce qui change d'un salon à
l'autre, ce sont les prix.

Les noms sont insensibles à la casse (`Grosses` et `grosses` désignent la même) et
proposés en autocomplétion, pour ne pas régler une fourchette jamais créée.

À l'ajout d'un salon, le bot vérifie tout de suite qu'il a **Envoyer des
messages** et **Intégrer des liens**, et refuse sinon : une permission manquante
découverte à l'heure du post serait un post perdu.

Une fourchette **sans salon est muette** : le bot la saute. `/promos liste` et
le site la signalent, faute de quoi ça ne se remarquerait que le lendemain.

L'isolation des pannes est à **deux niveaux** : une fourchette dont le rendu
échoue n'empêche pas les suivantes, et un salon cassé ne prive pas les autres
salons de sa fourchette. La journée est marquée publiée dès qu'un envoi a réussi —
sinon le passage suivant reposterait là où ça avait marché. Si **tous** les envois
échouent, rien n'est marqué et le prochain passage réessaie.

Un même salon peut servir deux fourchettes : il reçoit alors deux posts. C'est
pourquoi le compte rendu parle d'**envois** et non de salons.

## Une configuration par serveur

Chaque serveur a **sa** configuration, complète et séparée : ses fourchettes, ses
filiales, ses heures, son fuseau, son template, son rôle mentionné, son salon de
logs, sa liste d'accès et ses modules allumés. Un réglage fait dans un serveur ne
touche plus les autres. Deux bénéfices au passage : chaque serveur a sa propre
marque de « déjà publié aujourd'hui », donc une panne chez l'un n'annule plus la
journée des autres ; et les données du jeu ne sont téléchargées **qu'une fois**
pour tout le monde.

Aucune commande ne lit la configuration commune. Toutes passent par
`pour_ce_serveur` (`src/commandes.py`), qui rend la vue du serveur où la commande
est tapée ; `tests/test_cloisonnement.py` le vérifie commande par commande, et la
vue lève plutôt que de répondre si on la contourne.

Avant chaque envoi, le bot vérifie que le salon appartient bien au serveur dont
il lit la configuration, et signale dans le journal celui qui ne colle pas plutôt
que d'y publier. C'était le vrai risque du cloisonnement : une seule liste de
salons couvrait les deux serveurs, et mal fait, chacun aurait publié dans les
salons de tous les autres — deux posts par salon au lieu d'un.

`/reglages importer` reprend l'ancienne configuration commune, **à taper une fois
dans chaque serveur**. Elle ne garde que les salons de ce serveur-là, dit ce
qu'elle a repris et ce qu'elle a écarté, et ne touche pas à l'existant : si le
résultat ne va pas, rien n'est perdu. Il n'y a **pas** de reprise automatique — un
serveur qui ne la tape pas part d'une configuration neuve, donc sans fourchette,
donc muet.

Les salons peuvent vivre sur plusieurs serveurs Discord ; le bot résout les IDs
à travers tous ceux où il est présent. `/promos salon ajouter` doit malgré tout
être tapé **dans le serveur du salon** : Discord ne propose en autocomplétion que
les salons du serveur courant.

Le site affiche **quel salon vit sur quel serveur** en se basant sur les noms
mémorisés au moment du réglage : le site n'a pas accès à Discord et ne peut pas
résoudre les IDs lui-même. Il lit encore la configuration **commune** — le
raccorder aux configurations par serveur, et lui montrer les modules, est un
chantier à part.

### Migration depuis la fourchette unique

Une configuration d'avant ce changement (`prix_min`/`prix_max`/`salons` à la
racine) est convertie **à la lecture** en une fourchette nommée `principale`, avec
ses bornes et ses salons. Rien à lancer : la conversion a lieu au premier accès et
la racine est nettoyée à la première écriture. Sans elle, une mise à jour du bot
aurait fait taire un salon déjà configuré, et ça ne se serait vu que le lendemain
à l'heure du post.

## Salon de journal

`/reglages logs #salon` fait raconter au bot ce qu'il fait, là où tu le
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

`/reglages logs` sans argument désactive le journal.

## Personnaliser l'embed

Compose ton message sur Discohook, exporte le JSON, puis envoie-le avec
`/reglages template charger`. Le template décrit **un seul bâtiment** : le bot le
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

**Placeholders** (`/reglages template champs` les rappelle dans Discord) :

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
| `{prix}` | `302.62 KØ` |
| `{prix_long}` | `302 620 Ø` |
| `{prix_brut}` | `302620` |

Un placeholder mal orthographié est laissé tel quel et signalé au chargement.

## Déploiement sur Render

Le processus tourne chez Render, la base chez **Supabase** (voir plus bas).
Le dépôt contient `render.yaml` : **New → Blueprint** crée le web service.

1. Renseigne `DISCORD_TOKEN`, `GUILD_IDS`, `EMPIRE_API_KEY` et `DATABASE_URL`
   dans le dashboard.
2. Récupère les valeurs générées de `TICK_TOKEN` et `API_SECRET`. La seconde se
   recopie dans le projet Vercel du site.
3. Sur [cron-job.org](https://cron-job.org), crée un job **toutes les
   5 minutes** vers :
   ```
   https://<ton-service>.onrender.com/tick?token=<TICK_TOKEN>
   ```

**Note :** Après un déploiement, les commandes précédemment synchronisées
**globalement** subsistent **en plus** de celles par serveur et apparaissent en
double dans le sélecteur de Discord. Ce n'est pas une erreur — simplement quelque
chose à remarquer après le déploiement. Les commandes globales se propagent
lentement et finissent par disparaître.

### La base : Supabase, pas Render

La base gratuite de Render expire au bout de **trente jours**. L'état du bot vit
donc chez Supabase depuis le 18 août 2026, le processus restant chez Render.

Dans Supabase : **New project**, région **Frankfurt (eu-central-1)** pour être à
côté du service Render, puis **Connect → onglet « Session pooler »**.

**Prendre cette chaîne-là et pas « Direct connection ».** Depuis que l'IPv4 est
une option payante chez Supabase, la connexion directe (`db.<ref>.supabase.co`)
ne résout qu'en IPv6, et rien ne garantit que Render sorte en IPv6 : le bot
démarrerait sans base, en gardant sa config en mémoire — donc sans erreur
visible, jusqu'au premier redémarrage.

La chaîne du pooler en mode transaction (port 6543) marcherait aussi : le cache
de prepared statements d'asyncpg est désactivé côté code (`TAILLE_CACHE_STATEMENTS`
dans `src/db.py`), précisément parce qu'une connexion reprise d'un client à
l'autre ne connaît pas les statements préparés par le précédent. Le mode session
donne une connexion propre à chaque client et n'a rien à y gagner.

La table est créée avec **RLS activée et aucune politique**. Supabase publie
chaque table de `public` en HTTPS avec la clé anonyme du projet, qui est publique
par conception : sans RLS, les salons, les membres autorisés et le template
seraient lisibles par quiconque a l'URL. Le propriétaire de la table échappe à
RLS, donc le bot continue de lire et d'écrire sans politique à écrire.

Le blueprint ne déclare **aucune base** : en laisser une ferait recréer une base
Render vide à la prochaine application, dont le `fromDatabase` écraserait
`DATABASE_URL` et ferait repartir le bot d'une config d'usine, sans erreur.

Un projet Supabase gratuit se met en pause après ~7 jours sans activité. Le cron
de 5 minutes qui empêche Render de s'endormir l'empêche aussi de s'endormir.

#### Déménager l'état d'une base à l'autre

```bash
python -m src.migration          # ajouter --forcer pour écraser une cible non vide
```

Les deux chaînes de connexion sont demandées à l'écran, **masquées à la saisie** :
mises dans `.env` ou passées en argument, elles resteraient dans le fichier ou
dans l'historique du shell. Rien n'est enregistré, et aucun message n'affiche de
mot de passe.

La commande **recopie**, elle ne déplace pas : la base de départ reste intacte,
seul recours si celle d'arrivée se révèle inutilisable. Elle lit **toutes** les
clés trouvées plutôt qu'une liste écrite en dur — une clé ajoutée depuis serait
sinon laissée derrière, et le manque ne se verrait qu'une fois l'ancienne base
éteinte. Puis elle **relit** la cible et nomme les clés qui n'y sont pas
arrivées : une base peut accepter une écriture et n'en rien garder, et le bot
redémarrerait alors sur une config d'usine.

Deux saisies sont refusées avant toute écriture, parce que toutes deux
rendraient un rapport de réussite : la **même chaîne deux fois** (la base
copiée sur elle-même, puis éteinte avec les données dedans) et une **chaîne
vide** (`Store` retombe en mémoire sans lever, et tout disparaît à la fin du
processus).

Une fois le bot redéployé et vérifié sur la nouvelle base, la base Render peut
être supprimée.

### Une seconde instance de test

Créer le service de test avec **New → Web Service** (et non Blueprint) : le
blueprint est fait pour la prod, et un `render.yaml` partagé finirait par faire
converger les deux déploiements.

| Réglage | Test |
|---|---|
| Build / Start | `pip install -r requirements.txt` / `python -m src.main` |
| Health check | `/health` |
| `DISCORD_TOKEN` | **une autre application Discord** (voir ci-dessous) |
| `GUILD_IDS` | l'ID d'un serveur Discord de test |
| `API_SECRET` | une valeur **différente** de la prod |
| `DATABASE_URL` | **laissé vide** |
| `EMPIRE_API_KEY` | vide → le bot lit le CSV du dépôt, sans toucher à l'API du jeu |

Trois pièges, dans l'ordre de gravité :

- **Jamais le même `DISCORD_TOKEN` que la prod.** Deux processus connectés au
  même token se déconnectent mutuellement en boucle, et la prod cesse de poster.
  Il faut une seconde application sur le portail Discord.
- **Jamais le `DATABASE_URL` de la prod.** La configuration est cloisonnée par
  serveur, mais elle vit dans la **même** table : le bot de test écrirait dans la
  base de la prod, et sur un serveur commun aux deux `GUILD_IDS` il en écraserait
  les fourchettes, l'heure et le template.
- **`GUILD_IDS` sur un serveur de test.** Sinon les commandes du bot de test
  apparaissent en double dans le serveur réel.

Sans `DATABASE_URL`, la configuration vit en mémoire et repart des valeurs par
défaut à chaque réveil. Pour une instance de test c'est un avantage — chaque
essai part d'un état connu — et le site l'annonce par un bandeau « réglages non
persistants » plutôt que de le laisser deviner.

Ce job unique remplit deux rôles : il empêche le service gratuit de
s'endormir, et il déclenche la publication. L'heure exacte du post reste
réglée par `/promos heure` et `/frais heure` — le bot publie au premier
ping suivant l'heure prévue, et une seule fois par jour et par publication.

Le bot possède **aussi une boucle interne** qui vérifie l'heure chaque minute :
en local, ou sur un hébergement qui ne s'endort pas, l'heure réglée suffit
sans cron externe. Les deux mécanismes coexistent sans doubler les posts.

### Fenêtre de rattrapage

Un post manqué est rattrapé pendant **60 minutes** (`FENETRE_RATTRAPAGE` dans
`src/schedule.py`), puis abandonné pour la journée. Sans cette borne, un bot
démarré à 16 h avec `heure = 09:00` publierait aussitôt « en retard de 7 h »,
consommant le quota du jour et empêchant la publication réellement voulue.

Régler l'heure d'une publication **oublie automatiquement** son post du jour :
le nouvel horaire s'applique tout de suite, sans attendre demain. Chaque
publication a sa propre marque, donc régler l'une ne fait pas repartir l'autre.
`/reglages fuseau`, lui, n'efface aucune marque : corriger l'horloge n'est pas
demander un nouveau post.

`GET /health` répond `ok` : c'est aussi la cible du health check Render.

## Le site web

Un panneau de contrôle Next.js (dépôt séparé, `../eiweb`) remplace la plupart des
commandes : promotions du jour, fourchettes, heure, template, publication à la
demande. Il se connecte au bot par ces routes :

| Route | Effet |
|---|---|
| `GET /api/etat` | Bot connecté, type de stockage, dernière publication |
| `GET /api/promos[?min=&max=]` | Promotions du jour ; sans bornes, l'**union** des fourchettes |
| `GET /api/config` | Configuration courante, dont la liste des fourchettes |
| `PATCH /api/config` | Écrit `heure` et `fuseau` — rien d'autre |
| `GET /api/template` | Template et liste des placeholders |
| `PUT /api/template` | Remplace le template |
| `POST /api/apercu` | Rend le post sans publier ni enregistrer |
| `POST /api/publier` | Publie immédiatement (`forcer=True`) |

### Activer l'API

Une seule variable : `API_SECRET`, la même valeur que dans le projet Vercel.

```bash
openssl rand -base64 32
```

Sans elle, **toutes** les routes `/api/*` répondent 401 — y compris les lectures.
C'est délibéré : un `API_SECRET` vide comparé à un en-tête absent donnerait
`"" == ""`, et l'API serait grande ouverte.

`API_SECRET` est **distinct de `TICK_TOKEN`** pour qu'une fuite de l'un ne donne
pas l'autre : le jeton du cron circule dans une query string, donc dans les
journaux d'accès. `API_SECRET`, lui, voyage dans l'en-tête `X-Api-Secret`, qui
n'y apparaît jamais.

### Ce qui reste dans Discord

`PATCH /api/config` n'accepte que deux champs, `heure` et `fuseau`
(`CHAMPS_MODIFIABLES` dans `src/api.py`). Les **salons**, la **mention**, le
**salon de logs** et la **liste d'accès** désignent des objets Discord dont le
site ne peut vérifier ni l'existence, ni les permissions du bot, ni
l'appartenance d'un membre au serveur. Ils restent réglés par commande, où
Discord fait la vérification lui-même. Toute autre clé est refusée en 400 avec le
nom du champ fautif.

Les **bornes** suivent leurs salons : une fourchette porte les deux ensemble, et
régler un prix depuis le site sans pouvoir en régler les salons donnerait une
fourchette à moitié modifiable. `prix_min` et `prix_max` sont donc refusés comme
les autres — le site les **affiche**, `/promos prix` les change.

### Sans bornes, `/api/promos` renvoie l'union

Le site liste les promotions de toutes les fourchettes à la fois : la borne basse
vient de la plus basse, la borne haute de la plus haute. Un prix compris dans
cette union peut donc n'appartenir à **aucune** fourchette — la page dit ce qui
est surveillé, pas ce que recevra un salon donné. `/api/apercu`, lui, rend un
bloc par fourchette, dans l'ordre de publication.

Les deux routes appliquent les **types écartés** de la configuration commune,
celle dont le site parle faute de dire de quel serveur il s'agit. Sans ce filtre,
la page listerait des promotions que le bot ne publie nulle part, et l'on
croirait à une panne.

### Les montants ne passent jamais en nombre JSON

Un nombre JSON est un double IEEE 754 : `19013724539281000000` en ressortirait à
`19013724539280998400`, et le site afficherait un prix faux sans que rien ne le
signale. Chaque montant traverse donc le JSON **en texte**, sous trois formes
(`prix`, `prix_long`, `prix_brut`), produites par `src/serialisation.py` — les
mêmes rendus que les placeholders du template, pour que le site et Discord
n'affichent jamais deux montants différents.

`tests/test_serialisation.py` fige ces noms de champs (`test_contrat_*`) :
renommer un champ ici ne casserait rien côté Python, le site afficherait
simplement une colonne vide.

### Les erreurs ne disent que le type

Une exception inattendue sur `/api/*` renvoie `Erreur inattendue (RuntimeError)`,
jamais le message. Le détail pourrait contenir l'URL de l'API du jeu, donc la clé
`EMPIRE_API_KEY`. Un test (`test_erreur_inattendue_ne_fuit_pas_la_cle_dapi`)
plante volontairement la clé dans un message d'exception et vérifie qu'elle
n'atteint pas la réponse.

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

### Vérifier que ça marche : `/reglages source tester`

Après avoir renseigné une clé, `/reglages source tester` fait un vrai appel et rend le
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
qu'il sortira, utilise `/promos apercu`. `/reglages source voir` rappelle la source active
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

1090 tests couvrent la notation monétaire, le parsing du CSV (entiers de
21 chiffres, notation scientifique), le calcul des remises, le repêchage hors
fourchette, le rendu du template, les limites Discord, le planning (fenêtre
de rattrapage, idempotence quotidienne), l'API du jeu (construction de l'URL,
erreurs 401/5xx, non-fuite de la clé, réponses non-CSV), les fourchettes
multiples (chaque salon ne reçoit que les promotions de sa fourchette, migration
d'une config plate, unicité des noms insensible à la casse), la publication
multi-salon (échec partiel, échec total, salon supprimé), le salon de journal
(qui ne doit jamais échouer), le contrôle d'accès (toutes les commandes
protégées, admin jamais verrouillé dehors, liste non modifiable par un membre
autorisé), les commandes slash (exécutées hors ligne, embeds inspectés), les
endpoints HTTP et l'API du site (secret partagé, validation des écritures,
non-fuite de la clé d'API dans les erreurs), le contrat de module (un fichier
cassé écarté et nommé, publications multiples, ordre du menu), le cloisonnement
par serveur (aucune commande ne lit la configuration commune, un envoi ne sort
pas de son serveur, `/reglages importer`) et l'extinction par serveur (le menu
d'un serveur, le refus à l'exécution, `/reglages` inextinguible).

### Deux exports, deux usages

`buildings_batiments_entreprise.csv`, à la racine, est **remplacé** à chaque
nouvel export du jeu. Un test qui y épinglerait un nom de bâtiment ou un montant
casserait au remplacement suivant sans qu'aucun bug n'existe. Un seul test le
lit donc, `test_csv_vivant_reste_lisible`, et ne vérifie que le **format** — ce
qui attrape justement ce qui ferait publier « 0 bâtiment » demain matin : fichier
tronqué, ré-encodé, colonnes renommées.

Les tests qui ont besoin de valeurs stables lisent
`tests/fixtures/export_2026-07-28.csv`, un export **figé** (116 bâtiments,
4 promotions à −17 %). Nouvel export à figer : le copier sous un nouveau nom
daté, sans écraser l'ancien.

### Vérifier que les tests mordent

```bash
python -m tests.mutations          # les 363 mutations
python -m tests.mutations acces    # celles dont le nom contient « acces »
```

Une suite verte prouve que le code passe les tests, pas que les tests
vérifieraient quoi que ce soit. `tests/mutations.py` introduit une à une
363 fautes plausibles dans `src/` (inverser une comparaison, ôter une garde,
supprimer un masquage de secret, renommer une commande sans renommer son module)
et exige que la suite échoue à chaque fois. Un **survivant** est un trou de
couverture, pas un faux positif.

Le fichier muté est restauré dans un `finally`, donc rien ne reste modifié même
sur Ctrl-C. Un passage interrompu par un délai dépassé, en revanche, peut laisser
`src/` muté — et la suite serait alors rouge pour de mauvaises raisons, ce qui
ferait passer les motifs suivants pour couverts. Vérification de sûreté après
chaque passage : `git status --short -- src/`.

## Structure

```
src/modules/           un fichier par fonctionnalité, balayé au démarrage
  __init__.py          le contrat (Module, Publication), le balayage, la greffe
  conversion.py        /convertir
  promos.py            /promos et la publication des promotions
  frais.py             /frais et la publication du tableau
src/tournee.py   la mécanique de publication commune à tous les modules
src/commandes.py l'outillage commun des commandes, et celles greffées d'office
src/reglages.py  le noyau /reglages, qui n'appartient à aucun module
src/money.py     échelle Ø du jeu (format + saisie)
src/promos.py    parsing CSV, calcul des remises, filtre et tri
src/source.py    provenance des données (API du jeu ou fichier local)
src/template.py  substitution des {placeholders} Discohook
src/publish.py   assemblage des embeds et limites Discord
src/filiales.py  relevés de frais par filiale (cœur pur, sans Discord ni base)
src/publish_filiales.py  le tableau des frais en un embed
src/bot.py       client Discord, menu par serveur, gardien de l'arbre
src/journal.py   compte rendu dans le salon de logs
src/schedule.py  « est-ce l'heure de publier ? »
src/db.py        configuration persistante, cloisonnée par serveur (Postgres)
src/importation.py  reprise de l'ancienne configuration commune
src/migration.py déménagement de l'état d'une base Postgres à une autre
src/acces.py     qui a le droit d'utiliser les commandes
src/web.py       /health et /tick
src/api.py       routes /api/* consommées par le site web
src/serialisation.py  objets métier → JSON (montants en texte)
src/main.py      point d'entrée
```
