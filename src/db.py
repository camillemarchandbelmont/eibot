"""Persistance de la configuration (Postgres, chez Supabase).

Le disque de Render est éphémère : la fourchette, l'heure et le template
réglés par commande doivent survivre aux redéploiements, d'où Postgres.

Le processus tourne chez Render, la base chez Supabase — celle de Render était
gratuite trente jours seulement. La chaîne de connexion est celle du **session
pooler** (port 5432) : la connexion directe de Supabase ne résout qu'en IPv6
depuis que l'IPv4 y est une option payante, et rien ne garantit que Render sorte
en IPv6. Le déménagement de l'état d'une base à l'autre est dans
`src/migration.py`.

Aucun bâtiment n'est stocké : le CSV reste la source de vérité, relu à
chaque exécution.

Sans `DATABASE_URL`, on retombe sur un stockage en mémoire pour pouvoir
lancer le bot en local sans base.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from random import Random
from typing import Any

from src import motdepasse, settings
from src.filiales import (
    Filiale,
    calculer,
    depuis_json,
    enregistrer,
    index_de,
    remettre_a_zero,
    retirer,
    retirer_plusieurs,
    valeurs_aleatoires,
    vers_json,
)
from src.promos import normaliser_type

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_state (
    cle    TEXT PRIMARY KEY,
    valeur JSONB NOT NULL,
    maj_le TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE bot_state ENABLE ROW LEVEL SECURITY;
"""

#: Cache de prepared statements d'asyncpg : désactivé.
#:
#: Supabase donne trois façons de se connecter, et deux d'entre elles reprennent
#: une connexion Postgres d'un client à l'autre (pooler en mode transaction,
#: port 6543). asyncpg prépare un statement par requête paramétrée et le
#: réutilise : sur une connexion reprise, le serveur ne le connaît plus et la
#: requête échoue — pas au démarrage, mais à la première lecture de config, donc
#: seulement en production.
#:
#: Zéro coûte une préparation par requête, soit quelques-unes par ping de cron :
#: rien de mesurable, contre une panne entière si la chaîne pointe le mauvais
#: port.
TAILLE_CACHE_STATEMENTS = 0

#: Nom donné à la fourchette issue d'une config plate (voir `Store.fourchettes`).
FOURCHETTE_MIGREE = "principale"

#: Champs de l'ancienne config à plat. Leur présence dans la config *enregistrée*
#: est la signature d'un bot à migrer, par opposition à un bot neuf.
_CHAMPS_PLATS = ("prix_min", "prix_max", "salons", "salon_id")

#: Préfixe du tiroir d'un serveur : `serveur:<id>:<clé habituelle>`.
#:
#: Une seule table, mais des clés préfixées, plutôt qu'une colonne `serveur_id` :
#: la table est un dictionnaire clé -> JSON, et tout `Store` sait déjà lire et
#: écrire une clé. Cloisonner devient donc une affaire de nom de clé, et les
#: soixante accesseurs restent tels quels.
PREFIXE_SERVEUR = "serveur"


def _cle_nom(nom: str) -> str:
    """Forme comparable d'un nom de fourchette.

    Insensible à la casse : `Grosses` et `grosses` seraient indistinguables à
    l'œil dans une liste, donc ils désignent la même fourchette.
    """
    return str(nom).strip().casefold()


def _salons_servis(base: dict[str, Any]) -> set[str]:
    """Ids des salons cités par une publication, n'importe où dans la base.

    Lit les données brutes plutôt que d'interroger les publications : `Store` ne
    connaît pas les modules, et n'a pas à les connaître pour reconnaître ses
    propres tiroirs. Deux conventions suffisent — un tiroir de publication finit
    par `:salons`, une config porte les siens sous `salons`, `salon_id`,
    `filiales_salons`, et dans chaque fourchette.

    Un tiroir d'une convention future serait ignoré, donc ses salons comptés
    orphelins : le nom d'un salon disparaîtrait du site jusqu'au post suivant,
    qui le remémorise. Faute cosmétique et réparable, contre le risque inverse
    — garder tout, et laisser la table grossir sans fin.
    """
    servis: set[str] = set()

    def ajouter(valeur: Any) -> None:
        if isinstance(valeur, list):
            servis.update(str(salon) for salon in valeur if salon)
        elif valeur:
            servis.add(str(valeur))

    for cle, valeur in base.items():
        if cle.endswith(":salons"):
            ajouter(valeur)
        elif (cle == "config" or cle.endswith(":config")) and isinstance(valeur, dict):
            ajouter(valeur.get("salons"))
            ajouter(valeur.get("salon_id"))
            ajouter(valeur.get("filiales_salons"))
            for fourchette in valeur.get("fourchettes") or []:
                if isinstance(fourchette, dict):
                    ajouter(fourchette.get("salons"))

    return servis


def _montant_ou_rien(brut: Any) -> Decimal | None:
    """Lit un montant facultatif ; `None` si absent ou illisible.

    Sert aux bornes tolérées, les seules qui ont le droit de ne pas être là. Une
    valeur illisible est traitée comme absente plutôt que levée : la config est
    du JSON retouchable à la main, et une faute de frappe doit coûter la zone de
    tolérance du jour, pas la publication.
    """
    texte = str(brut or "").strip()
    if not texte:
        return None
    try:
        return Decimal(texte)
    except InvalidOperation:
        return None


def _entier_ou_rien(brut: Any) -> int | None:
    """Un compte positif, ou `None` si la valeur n'en est pas un.

    Accepte le texte (`"5"`) parce que le JSON de la config a été écrit par
    plusieurs mains — le site, une version antérieure, un éditeur. Refuse zéro et
    les négatifs : un plafond de zéro promotion est une fourchette qui ne publie
    rien, indiscernable d'une panne.
    """
    try:
        nombre = int(str(brut).strip())
    except (TypeError, ValueError):
        return None
    return nombre if nombre >= 1 else None


def tranches_fourchette(fourchette: dict) -> list[tuple[Decimal, Decimal, int]]:
    """Plafonds par plage de prix, `(bas, haut, nombre)`, du plus bas au plus haut.

    Même rôle que `bornes_tolerees` et `plafond_fourchette` : une seule traduction
    pour la publication du soir, l'aperçu et l'API, plutôt qu'une lecture recopiée
    à trois endroits dont l'une oublierait un jour de plafonner.

    Défensive de bout en bout, la config étant du JSON retouchable à la main : une
    entrée dont il manque une borne ou le nombre est ignorée, jamais levée. Les
    bornes inversées sont remises dans l'ordre — `300 → 100` ne contiendrait
    jamais rien, la tranche serait inerte et rien à l'écran ne dirait pourquoi.

    Triées par borne basse : `/promos liste` les montre les unes sous les autres,
    et une liste qui se réordonne à chaque réglage ne se relit pas.
    """
    # Une liste, ou rien : un nombre à cette place lèverait un `TypeError` à
    # l'itération, et une chaîne s'y prêterait caractère par caractère.
    brutes = fourchette.get("tranches")
    if not isinstance(brutes, list):
        return []

    lues: list[tuple[Decimal, Decimal, int]] = []
    for brute in brutes:
        if not isinstance(brute, dict):
            continue
        bas = _montant_ou_rien(brute.get("min"))
        haut = _montant_ou_rien(brute.get("max"))
        nombre = _entier_ou_rien(brute.get("nombre"))
        if bas is None or haut is None or nombre is None:
            continue
        if bas > haut:
            bas, haut = haut, bas
        lues.append((bas, haut, nombre))
    return sorted(lues, key=lambda tranche: (tranche[0], tranche[1]))


def _normaliser_fourchette(brute: dict) -> dict:
    """Fourchette aux champs garantis, quelle que soit l'origine du JSON.

    La config peut avoir été écrite par une version antérieure ou retouchée à
    la main. Chaque champ absent vaut mieux qu'un `KeyError` dans la boucle de
    publication, qui ferait sauter le post du jour.

    La zone de tolérance est facultative et **des deux bornes ou d'aucune** :
    une seule ne décrirait pas une plage, et `find_promos` l'ignorerait de
    toute façon. Elle est aussi recadrée pour englober la fourchette idéale —
    une zone plus étroite exclurait une partie de ce que la passe idéale
    accepte, incohérence que rien ne signalerait à l'exécution.
    """
    prix_min = str(brute.get("prix_min", "0"))
    prix_max = str(brute.get("prix_max", "0"))

    tolere_min = _montant_ou_rien(brute.get("tolere_min"))
    tolere_max = _montant_ou_rien(brute.get("tolere_max"))
    if tolere_min is None or tolere_max is None:
        tolere_min = tolere_max = None
    else:
        if tolere_min > tolere_max:
            tolere_min, tolere_max = tolere_max, tolere_min
        tolere_min = min(tolere_min, _montant_ou_rien(prix_min) or Decimal(0))
        tolere_max = max(tolere_max, _montant_ou_rien(prix_max) or Decimal(0))

    # `0` plutôt qu'une clé absente pour « aucun plafond » : la forme des
    # fourchettes enregistrées reste la même d'une version à l'autre, ce qui
    # évite d'avoir à distinguer « pas plafonnée » de « écrite avant le
    # plafond » — les deux se lisent pareil.
    plafond = _entier_ou_rien(brute.get("plafond"))

    return {
        "nom": str(brute.get("nom", "")).strip(),
        "prix_min": prix_min,
        "prix_max": prix_max,
        "salons": [str(salon) for salon in brute.get("salons") or [] if salon],
        "tolere_min": "" if tolere_min is None else str(tolere_min),
        "tolere_max": "" if tolere_max is None else str(tolere_max),
        "plafond": 0 if plafond is None else plafond,
        # Réécrites depuis la lecture défensive plutôt que recopiées : ce qui est
        # enregistré est alors exactement ce que la publication appliquera, et une
        # entrée abîmée disparaît au premier réglage au lieu de rester à traîner.
        "tranches": [
            {"min": str(bas), "max": str(haut), "nombre": nombre}
            for bas, haut, nombre in tranches_fourchette(brute)
        ],
    }


def bornes_tolerees(fourchette: dict) -> tuple[Decimal | None, Decimal | None]:
    """Zone de tolérance d'une fourchette, `(None, None)` si elle n'en a pas.

    Le stockage garde des chaînes — y compris vides — et `find_promos` attend
    des `Decimal` ou rien. Cette traduction est faite ici plutôt qu'à chaque
    appel : elle est appelée depuis la boucle de publication, l'aperçu et
    l'API, et une conversion oubliée quelque part rendrait la zone silencieuse
    de ce côté-là seulement.
    """
    return _montant_ou_rien(fourchette.get("tolere_min")), _montant_ou_rien(
        fourchette.get("tolere_max")
    )


def plafond_fourchette(fourchette: dict) -> int | None:
    """Nombre maximum de promotions à publier, `None` si la fourchette n'en a pas.

    Même rôle que `bornes_tolerees` : une seule traduction pour la publication du
    soir, l'aperçu et l'API, plutôt qu'une lecture recopiée à trois endroits dont
    l'une oublierait un jour de plafonner.
    """
    return _entier_ou_rien(fourchette.get("plafond"))


class Store:
    """Dictionnaire persistant clé -> JSON."""

    def __init__(self, dsn: str = ""):
        self.dsn = dsn
        self._pool = None
        self._memoire: dict[str, Any] = {}

    @property
    def persistant(self) -> bool:
        return self._pool is not None

    async def connect(self) -> None:
        if not self.dsn:
            log.warning(
                "DATABASE_URL absent : configuration gardée en mémoire, "
                "elle sera perdue au redémarrage."
            )
            return

        import asyncpg

        # Render fournit parfois 'postgres://' ; asyncpg accepte les deux, mais
        # exige SSL sur les bases managées.
        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=1,
            max_size=3,
            statement_cache_size=TAILLE_CACHE_STATEMENTS,
        )
        async with self._pool.acquire() as connexion:
            await connexion.execute(SCHEMA)
        log.info("Base connectée.")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get(self, cle: str, defaut: Any = None) -> Any:
        if self._pool is None:
            return self._memoire.get(cle, defaut)
        async with self._pool.acquire() as connexion:
            ligne = await connexion.fetchrow(
                "SELECT valeur FROM bot_state WHERE cle = $1", cle
            )
        if ligne is None:
            return defaut
        return json.loads(ligne["valeur"])

    async def set(self, cle: str, valeur: Any) -> None:
        if self._pool is None:
            self._memoire[cle] = valeur
            return
        async with self._pool.acquire() as connexion:
            await connexion.execute(
                """
                INSERT INTO bot_state (cle, valeur, maj_le)
                VALUES ($1, $2::jsonb, now())
                ON CONFLICT (cle) DO UPDATE
                    SET valeur = EXCLUDED.valeur, maj_le = now()
                """,
                cle,
                json.dumps(valeur),
            )

    async def tout(self) -> dict[str, Any]:
        """Tout ce qui est enregistré, clé par clé.

        Sert au déménagement d'une base à l'autre : lire une liste de clés écrite
        en dur oublierait en silence celle ajoutée après, et le manque ne se
        verrait qu'une fois l'ancienne base éteinte.

        Ce qui est **enregistré**, sans les défauts d'usine : recopiés dans la
        base d'arrivée, ils lui inventeraient une config plate — que `Store` prend
        justement pour la signature d'un bot à migrer.
        """
        if self._pool is None:
            return dict(self._memoire)
        async with self._pool.acquire() as connexion:
            lignes = await connexion.fetch("SELECT cle, valeur FROM bot_state")
        return {ligne["cle"]: json.loads(ligne["valeur"]) for ligne in lignes}

    def pour(self, serveur_id: str | int) -> "VueServeur":
        """Le même stockage, vu par un seul serveur.

        Les mêmes accesseurs, mais chacun dans son tiroir : régler une fourchette
        dans le serveur d'une entreprise ne touche plus celles des autres.
        """
        return VueServeur(self, serveur_id)

    # --- Accès métier ------------------------------------------------------

    async def config(self) -> dict:
        """Config courante, complétée par les défauts pour les clés absentes."""
        defauts = settings.config_par_defaut()
        enregistree = await self.get("config", {}) or {}
        return {**defauts, **enregistree}

    async def _enregistree(self) -> dict:
        """Config telle qu'elle est **en base**, sans les défauts d'usine.

        Toute écriture part de là et non de `config()` : partir de la vue
        fusionnée recopierait les défauts en base, et `prix_min`/`prix_max`/
        `salons` y sont justement la signature d'une config plate à migrer. Un
        `/promos heure` sur un bot neuf lui inventerait donc une fourchette
        `principale`, aux bornes d'usine et dans le salon de `SALON_ID` — le bot
        se mettrait à publier de lui-même.
        """
        return dict(await self.get("config", {}) or {})

    # --- Salons de publication ---------------------------------------------

    async def salons(self) -> list[str]:
        """Salons où publier, dans l'ordre d'ajout.

        Migre à la lecture l'ancien `salon_id` unique (config d'avant le
        multi-salon) : sans ça, une mise à jour du bot ferait taire un salon
        déjà configuré. `salons` fait foi dès qu'elle existe.
        """
        config = await self.config()
        liste = [str(salon) for salon in config.get("salons") or [] if salon]
        if liste:
            return liste

        # Liste vide : soit rien n'est configuré, soit une config d'avant la
        # migration. `_ecrire_salons` efface `salon_id`, donc un salon retiré
        # ne peut pas ressusciter par ici.
        ancien = config.get("salon_id")
        return [str(ancien)] if ancien else []

    async def _ecrire_salons(self, liste: list[str]) -> None:
        """Enregistre la liste et retire l'ancien champ unique.

        Écrit directement plutôt que par `maj_config`, qui ignore les `None` et
        ne pourrait donc pas effacer `salon_id` — un salon retiré reviendrait
        au redémarrage.
        """
        config = await self._enregistree()
        config["salons"] = liste
        config.pop("salon_id", None)
        await self.set("config", config)

    async def ajouter_salon(self, salon_id: str) -> bool:
        """Ajoute un salon. Renvoie False s'il y était déjà."""
        liste = await self.salons()
        if str(salon_id) in liste:
            return False
        await self._ecrire_salons([*liste, str(salon_id)])
        return True

    async def retirer_salon(self, salon_id: str) -> bool:
        """Retire un salon. Renvoie False s'il n'y était pas."""
        liste = await self.salons()
        if str(salon_id) not in liste:
            return False
        await self._ecrire_salons([s for s in liste if s != str(salon_id)])
        return True

    # --- Fourchettes -------------------------------------------------------
    #
    # Une fourchette porte ses bornes **et** ses salons : c'est ce qui permet
    # d'envoyer « les grosses affaires » dans un salon et « les petits prix »
    # dans un autre. L'heure, la mention et le template restent globaux.

    async def fourchettes(self) -> list[dict]:
        """Fourchettes configurées, dans l'ordre d'ajout.

        Migre à la lecture une config plate (`prix_min`/`prix_max`/`salons` à la
        racine, l'état de la prod avant ce changement) en une fourchette unique
        nommée `principale`. Sans ça, une mise à jour du bot ferait taire un
        salon déjà configuré — et ça ne se remarquerait que le lendemain à
        l'heure du post.

        `fourchettes` fait foi **dès qu'elle existe**, liste vide comprise :
        c'est l'état après suppression de la dernière, et consulter la racine
        la ferait ressusciter au redémarrage.
        """
        # La décision porte sur ce qui est **réellement enregistré**, pas sur
        # `config()`, qui fusionne les défauts d'usine : ceux-ci contiennent
        # encore `prix_min`/`prix_max`, si bien qu'un bot neuf ressemblerait à
        # une config plate et hériterait d'une fourchette que personne n'a
        # demandée — avec des salons non choisis.
        enregistree = await self.get("config", {}) or {}

        if "fourchettes" in enregistree:
            liste = enregistree["fourchettes"] or []
            return [_normaliser_fourchette(f) for f in liste if isinstance(f, dict)]

        if not any(cle in enregistree for cle in _CHAMPS_PLATS):
            return []

        # Config plate d'avant les fourchettes multiples. Les valeurs viennent de
        # `config()` (les défauts complètent une clé manquante) et les salons de
        # `salons()`, qui applique lui-même la migration `salon_id`.
        config = await self.config()
        return [
            _normaliser_fourchette(
                {
                    "nom": FOURCHETTE_MIGREE,
                    "prix_min": config.get("prix_min", "0"),
                    "prix_max": config.get("prix_max", "0"),
                    "salons": await self.salons(),
                }
            )
        ]

    async def _ecrire_fourchettes(self, liste: list[dict]) -> None:
        """Enregistre la liste et retire les anciens champs plats.

        Écrit directement plutôt que par `maj_config`, qui ignore les valeurs
        vides et ne pourrait donc ni effacer `prix_min` ni enregistrer une liste
        vidée — une fourchette supprimée reviendrait au redémarrage.
        """
        config = await self._enregistree()
        config["fourchettes"] = liste
        for ancien in ("prix_min", "prix_max", "salons", "salon_id"):
            config.pop(ancien, None)
        await self.set("config", config)

    def _index(self, liste: list[dict], nom: str) -> int:
        """Position d'une fourchette par son nom, -1 si absente.

        Comparaison insensible à la casse : l'unicité l'ignore déjà, refuser
        `Grosses` pour `grosses` serait un piège.
        """
        cible = _cle_nom(nom)
        for index, fourchette in enumerate(liste):
            if _cle_nom(fourchette["nom"]) == cible:
                return index
        return -1

    async def ajouter_fourchette(
        self, nom: str, prix_min: Decimal, prix_max: Decimal
    ) -> dict:
        """Crée une fourchette sans salon. Lève `ValueError` si le nom est pris.

        Les bornes inversées sont remises dans l'ordre : une fourchette dont le
        minimum dépasse le maximum ne contiendrait jamais rien, et l'intention
        est évidente.
        """
        propre = nom.strip()
        if not propre:
            raise ValueError("Le nom de la fourchette ne peut pas être vide.")

        liste = await self.fourchettes()
        if self._index(liste, propre) >= 0:
            raise ValueError(f"Une fourchette nommée « {propre} » existe déjà.")

        if prix_min > prix_max:
            prix_min, prix_max = prix_max, prix_min

        fourchette = _normaliser_fourchette(
            {"nom": propre, "prix_min": str(prix_min), "prix_max": str(prix_max), "salons": []}
        )
        await self._ecrire_fourchettes([*liste, fourchette])
        return fourchette

    async def supprimer_fourchette(self, nom: str) -> bool:
        """Supprime une fourchette. Renvoie False si elle n'existait pas."""
        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0:
            return False
        await self._ecrire_fourchettes([*liste[:index], *liste[index + 1 :]])
        return True

    async def majprix_fourchette(
        self, nom: str, prix_min: Decimal, prix_max: Decimal
    ) -> bool:
        """Change les bornes en conservant les salons attachés."""
        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0:
            return False

        if prix_min > prix_max:
            prix_min, prix_max = prix_max, prix_min

        # `_normaliser_fourchette` repoussera les bornes tolérées si les
        # nouvelles bornes idéales les dépassent.
        liste[index] = _normaliser_fourchette(
            {**liste[index], "prix_min": str(prix_min), "prix_max": str(prix_max)}
        )
        await self._ecrire_fourchettes(liste)
        return True

    async def majtolerance_fourchette(
        self, nom: str, tolere_min: Decimal, tolere_max: Decimal
    ) -> bool:
        """Règle la zone de tolérance. False si la fourchette est inconnue.

        Lève `ValueError` si la zone est plus étroite que la fourchette : la
        tolérance n'a le droit que d'**ajouter** des candidats quand la
        fourchette est trop pauvre. Une zone plus étroite serait acceptée sans
        effet visible, alors qu'elle trahit une faute de saisie — typiquement
        les bornes idéales retapées à la place des tolérées.
        """
        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0:
            return False

        if tolere_min > tolere_max:
            tolere_min, tolere_max = tolere_max, tolere_min

        fourchette = liste[index]
        if tolere_min > Decimal(fourchette["prix_min"]) or tolere_max < Decimal(
            fourchette["prix_max"]
        ):
            raise ValueError(
                "La zone de tolérance doit être plus large que la fourchette "
                f"(« {fourchette['nom']} » va de {fourchette['prix_min']} à "
                f"{fourchette['prix_max']})."
            )

        liste[index] = {
            **fourchette,
            "tolere_min": str(tolere_min),
            "tolere_max": str(tolere_max),
        }
        await self._ecrire_fourchettes(liste)
        return True

    async def effacer_tolerance_fourchette(self, nom: str) -> bool:
        """Retire la zone de tolérance. False si la fourchette est inconnue ou
        n'en avait pas — pour que la commande n'annonce pas un effacement
        imaginaire."""
        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0 or not liste[index].get("tolere_min"):
            return False

        liste[index] = {**liste[index], "tolere_min": "", "tolere_max": ""}
        await self._ecrire_fourchettes(liste)
        return True

    async def regler_plafond_fourchette(self, nom: str, combien: int) -> bool:
        """Limite le nombre de promotions publiées. False si la fourchette est
        inconnue, `ValueError` si le nombre est inférieur à 1.

        Le nombre est vérifié **avant** le nom : à saisie doublement fautive,
        c'est le nombre qu'il faut signaler, parce qu'un nom de fourchette
        s'autocomplète et qu'un plafond de zéro ne se rattrape pas — la
        fourchette cesserait de publier sans que rien ne l'annonce.
        """
        if int(combien) < 1:
            raise ValueError(
                "Le plafond doit valoir au moins 1 promotion. Pour qu'une "
                "fourchette ne publie plus, retire-lui ses salons."
            )

        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0:
            return False

        liste[index] = {**liste[index], "plafond": int(combien)}
        await self._ecrire_fourchettes(liste)
        return True

    async def effacer_plafond_fourchette(self, nom: str) -> bool:
        """Retire le plafond. False si la fourchette est inconnue ou n'en avait
        pas — pour que la commande n'annonce pas un effacement imaginaire."""
        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0 or not plafond_fourchette(liste[index]):
            return False

        liste[index] = {**liste[index], "plafond": 0}
        await self._ecrire_fourchettes(liste)
        return True

    async def plafond_de_recherche(self) -> int | None:
        """Plafond à appliquer à une recherche, qui couvre **l'union** des
        fourchettes : le plus large, et seulement si toutes en ont un.

        Une recherche ne porte sur aucune fourchette en particulier. Y poser le
        plafond de l'une cacherait des promotions qu'une autre publie bel et
        bien : `/promos chercher` montrerait alors moins que ce qui sort le soir,
        c'est-à-dire l'inverse de ce qu'on lui demande. Il faut donc que
        *chaque* fourchette soit plafonnée pour que la recherche le soit, et
        alors au plus permissif des plafonds.

        Sans aucune fourchette, aucun plafond : « toutes plafonnées » ne doit pas
        être vrai d'un ensemble vide, sinon un serveur neuf verrait sa recherche
        bornée à rien.
        """
        plafonds = [plafond_fourchette(f) for f in await self.fourchettes()]
        if not plafonds or None in plafonds:
            return None
        return max(plafonds)

    async def regler_tranche_fourchette(
        self, nom: str, bas: Decimal, haut: Decimal, combien: int
    ) -> bool:
        """Plafonne une plage de prix de la fourchette. False si elle est inconnue,
        `ValueError` si le nombre est inférieur à 1.

        Une plage déjà réglée est **remplacée** : sans ça, chaque correction
        empilerait une tranche de plus sur les mêmes bornes, la plus stricte
        gagnerait pour toujours et la commande aurait confirmé un nombre que le
        post ne respecte pas.

        Le nombre est vérifié **avant** le nom, comme pour le plafond : à saisie
        doublement fautive, c'est le nombre qu'il faut signaler, parce qu'un nom de
        fourchette s'autocomplète.
        """
        if int(combien) < 1:
            raise ValueError(
                "Une tranche doit accepter au moins 1 promotion. Pour qu'une "
                "plage de prix ne sorte jamais, resserre les bornes de la "
                "fourchette."
            )

        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0:
            return False

        if bas > haut:
            bas, haut = haut, bas

        autres = [
            tranche
            for tranche in tranches_fourchette(liste[index])
            if (tranche[0], tranche[1]) != (bas, haut)
        ]
        liste[index] = _normaliser_fourchette(
            {
                **liste[index],
                "tranches": [
                    *(
                        {"min": str(a), "max": str(b), "nombre": n}
                        for a, b, n in autres
                    ),
                    {"min": str(bas), "max": str(haut), "nombre": int(combien)},
                ],
            }
        )
        await self._ecrire_fourchettes(liste)
        return True

    async def effacer_tranche_fourchette(
        self, nom: str, bas: Decimal, haut: Decimal
    ) -> bool:
        """Retire une tranche par ses bornes. False si la fourchette est inconnue
        ou n'avait pas cette plage — pour que la commande n'annonce pas un
        effacement imaginaire, cas d'une borne mal retapée.

        Par ses bornes et non par un numéro : un numéro de tranche changerait de
        sens dès qu'une autre est ajoutée, les tranches étant rangées par prix.
        """
        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0:
            return False

        if bas > haut:
            bas, haut = haut, bas

        avant = tranches_fourchette(liste[index])
        restantes = [t for t in avant if (t[0], t[1]) != (bas, haut)]
        if len(restantes) == len(avant):
            return False

        liste[index] = _normaliser_fourchette(
            {
                **liste[index],
                "tranches": [
                    {"min": str(a), "max": str(b), "nombre": n}
                    for a, b, n in restantes
                ],
            }
        )
        await self._ecrire_fourchettes(liste)
        return True

    async def tranches_de_recherche(self) -> list[tuple[Decimal, Decimal, int]]:
        """Tranches à appliquer à une recherche, qui couvre **l'union** des
        fourchettes : les plages réglées dans *toutes*, au plus permissif.

        Même raisonnement que `plafond_de_recherche`. Une recherche ne porte sur
        aucune fourchette en particulier ; y appliquer une tranche que l'une seule
        connaît cacherait des promotions qu'une autre publie bel et bien, et
        `/promos chercher` montrerait moins que ce qui sort le soir. Une plage doit
        donc être plafonnée partout pour l'être ici, et alors au plus grand des
        nombres.

        Sans aucune fourchette, aucune tranche : « réglée partout » ne doit pas
        être vrai d'un ensemble vide.
        """
        fourchettes = await self.fourchettes()
        if not fourchettes:
            return []

        par_plage: list[dict[tuple[Decimal, Decimal], int]] = [
            {(bas, haut): nombre for bas, haut, nombre in tranches_fourchette(f)}
            for f in fourchettes
        ]
        communes = set(par_plage[0])
        for table in par_plage[1:]:
            communes &= set(table)

        return sorted(
            (bas, haut, max(table[(bas, haut)] for table in par_plage))
            for bas, haut in communes
        )

    async def ajouter_salon_fourchette(self, nom: str, salon_id: str) -> bool:
        """Attache un salon. False si la fourchette est inconnue ou l'a déjà."""
        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0 or str(salon_id) in liste[index]["salons"]:
            return False

        liste[index] = {
            **liste[index],
            "salons": [*liste[index]["salons"], str(salon_id)],
        }
        await self._ecrire_fourchettes(liste)
        return True

    async def retirer_salon_fourchette(self, nom: str, salon_id: str) -> bool:
        """Détache un salon. False si la fourchette est inconnue ou ne l'a pas."""
        liste = await self.fourchettes()
        index = self._index(liste, nom)
        if index < 0 or str(salon_id) not in liste[index]["salons"]:
            return False

        liste[index] = {
            **liste[index],
            "salons": [s for s in liste[index]["salons"] if s != str(salon_id)],
        }
        await self._ecrire_fourchettes(liste)
        return True

    # --- Salon de logs -----------------------------------------------------

    async def salon_logs(self) -> str | None:
        salon = (await self.config()).get("logs_salon_id")
        return str(salon) if salon else None

    async def desactiver_logs(self) -> None:
        """`maj_config` ignore les None : il faut écrire la config entière."""
        config = await self._enregistree()
        config["logs_salon_id"] = None
        await self.set("config", config)

    # --- Mention : un rôle par serveur -------------------------------------

    async def roles(self) -> dict[str, str]:
        """Rôle à mentionner par serveur, `{}` si aucun n'est réglé.

        Pas de défaut d'usine pour cette clé : un dict vide serait
        indistinguable de « jamais réglé », et le matérialiser en base est ce
        que `_enregistree` évite partout ailleurs.
        """
        table = (await self.config()).get("roles") or {}
        return {str(serveur): str(role) for serveur, role in table.items() if role}

    async def role_du_serveur(self, serveur_id: str | int | None) -> str | None:
        """Rôle à mentionner dans ce serveur, ou None.

        `role_id` (config d'avant le multi-serveurs) sert de **repli**, il n'est
        pas converti : savoir à quel serveur appartient un rôle demanderait de
        résoudre un salon, donc un accès à Discord que `Store` n'a pas.

        Le repli ne joue que si `roles` est vide. Sinon un rôle qu'on croit
        remplacé continuerait d'être mentionné dans les serveurs non réglés.
        """
        table = await self.roles()
        if table:
            return table.get(str(serveur_id)) if serveur_id else None

        ancien = (await self.config()).get("role_id")
        return str(ancien) if ancien else None

    async def _ecrire_roles(self, table: dict[str, str]) -> None:
        """Écrit la table et retire `role_id`, devenu ambigu.

        Écriture directe et non `maj_config` : celui-ci ignore les valeurs
        vides, donc une table vidée ne serait jamais enregistrée et le rôle
        reviendrait au redémarrage.
        """
        config = await self._enregistree()
        config["roles"] = table
        config.pop("role_id", None)
        await self.set("config", config)

    async def definir_role(self, serveur_id: str | int, role_id: str | int) -> None:
        await self._ecrire_roles({**await self.roles(), str(serveur_id): str(role_id)})

    async def effacer_role(self, serveur_id: str | int) -> bool:
        """Retire le rôle d'un serveur. False s'il n'en avait pas."""
        table = await self.roles()
        config = await self.config()

        # Cas mixte : le serveur a un rôle dans `roles`
        if str(serveur_id) in table:
            await self._ecrire_roles(
                {s: r for s, r in table.items() if s != str(serveur_id)}
            )
            return True

        # Repli plat : `role_id` existe et `roles` est vide
        # On l'efface même si on ne connaît pas son serveur d'origine
        if not table and config.get("role_id"):
            await self._ecrire_roles({})
            return True

        return False

    # --- Noms de salons et de serveurs, pour le site -----------------------

    async def salons_connus(self) -> dict[str, dict]:
        """`{id_salon: {"nom": …, "serveur": …}}`, pour l'affichage du site.

        Deux tables plates (celle-ci et `serveurs`) plutôt qu'un objet par salon
        dans chaque fourchette : un salon servant deux fourchettes a son nom
        stocké **une seule fois**, et `fourchette["salons"]` reste une liste
        d'ids — ce dont dépendent la boucle de publication et le site.
        """
        table = (await self.config()).get("salons_connus") or {}
        return {
            str(salon): {
                "nom": str(details.get("nom", "")),
                "serveur": str(details.get("serveur", "")),
            }
            for salon, details in table.items()
            if isinstance(details, dict)
        }

    async def serveurs(self) -> dict[str, str]:
        """`{id_serveur: nom}` des serveurs dont un salon est connu."""
        table = (await self.config()).get("serveurs") or {}
        return {str(serveur): str(nom) for serveur, nom in table.items() if nom}

    async def memoriser_salon(
        self,
        salon_id: str | int,
        nom: str,
        serveur_id: str | int,
        serveur_nom: str,
    ) -> None:
        """Retient le nom d'un salon et de son serveur.

        Appelé au réglage **et** à chaque résolution : un salon renommé garde
        sinon son ancien nom indéfiniment. Les noms se corrigent donc d'eux-mêmes
        au premier post.
        """
        config = await self._enregistree()
        salons = dict(config.get("salons_connus") or {})
        salons[str(salon_id)] = {"nom": str(nom), "serveur": str(serveur_id)}
        config["salons_connus"] = salons

        noms = dict(config.get("serveurs") or {})
        noms[str(serveur_id)] = str(serveur_nom)
        config["serveurs"] = noms

        await self.set("config", config)

    async def oublier_salons_orphelins(self) -> int:
        """Efface les salons qu'aucune publication ne sert. Renvoie le compte.

        Sans ça la table grossit indéfiniment avec des salons dont plus personne
        ne parle. Un serveur dont plus aucun salon ne dépend disparaît aussi.

        Le balayage porte sur **toute la base**, et non sur la config d'un seul
        serveur : le cache des noms est commun — un nom de salon ne dépend pas de
        qui le regarde — donc un ménage décidé depuis un serveur effacerait les
        noms des salons de tous les autres, absents de ses propres réglages.

        Et sur **toutes** les publications, pas seulement les fourchettes : le
        salon du tableau des frais était compté orphelin à chaque passage, et le
        site reperdait son nom aussitôt mémorisé.
        """
        servis = _salons_servis(await self.tout())
        connus = await self.salons_connus()
        gardes = {
            salon: details for salon, details in connus.items() if salon in servis
        }
        if len(gardes) == len(connus):
            return 0

        config = await self._enregistree()
        config["salons_connus"] = gardes
        utiles = {details["serveur"] for details in gardes.values()}
        config["serveurs"] = {
            serveur: nom
            for serveur, nom in (await self.serveurs()).items()
            if serveur in utiles
        }
        await self.set("config", config)
        return len(connus) - len(gardes)

    # --- Membres autorisés à utiliser les commandes ------------------------

    async def autorises(self) -> list[str]:
        """Ids des membres autorisés en plus des administrateurs."""
        config = await self.config()
        return [str(membre) for membre in config.get("autorises") or [] if membre]

    async def _ecrire_autorises(self, liste: list[str]) -> None:
        """Écrit la liste telle quelle, vidée comprise : sauter une liste vide
        ferait revenir au redémarrage le membre qu'on vient de retirer."""
        config = await self._enregistree()
        config["autorises"] = liste
        await self.set("config", config)

    async def autoriser(self, membre_id: str) -> bool:
        """Autorise un membre. Renvoie False s'il l'était déjà."""
        liste = await self.autorises()
        if str(membre_id) in liste:
            return False
        await self._ecrire_autorises([*liste, str(membre_id)])
        return True

    async def retirer_autorise(self, membre_id: str) -> bool:
        """Retire un membre. Renvoie False s'il n'était pas autorisé."""
        liste = await self.autorises()
        if str(membre_id) not in liste:
            return False
        await self._ecrire_autorises([m for m in liste if m != str(membre_id)])
        return True

    # --- Modules allumés dans ce serveur -----------------------------------
    #
    # Ce sont les **éteints** qui sont retenus, et non les allumés. Tout est
    # allumé par défaut, donc un serveur neuf n'a rien à écrire ; et un module
    # posé par un déploiement est allumé partout d'office, alors que la liste des
    # allumés obligerait à passer dans chaque serveur pour l'y ajouter — sans que
    # rien ne dise qu'il faut le faire.

    async def modules_eteints(self) -> list[str]:
        """Noms des modules éteints dans ce serveur, triés."""
        config = await self.config()
        return sorted({str(nom) for nom in config.get("modules_eteints") or [] if nom})

    async def module_actif(self, nom: str) -> bool:
        return nom not in await self.modules_eteints()

    async def eteindre_module(self, nom: str) -> bool:
        """Éteint un module. Renvoie False s'il l'était déjà."""
        eteints = await self.modules_eteints()
        if nom in eteints:
            return False
        await self.maj_config(modules_eteints=sorted([*eteints, nom]))
        return True

    async def rallumer_module(self, nom: str) -> bool:
        """Rallume un module. Renvoie False s'il était déjà allumé.

        La liste vidée est écrite telle quelle — `maj_config` n'écarte que `None`.
        Sauter une liste vide rallumerait le module jusqu'au redémarrage, et il
        s'éteindrait tout seul là où personne ne regarde.
        """
        eteints = await self.modules_eteints()
        if nom not in eteints:
            return False
        await self.maj_config(modules_eteints=[n for n in eteints if n != nom])
        return True

    # --- Types de bâtiments écartés dans ce serveur -------------------------
    #
    # Un goût d'acheteur, pas une propriété du monde : une entreprise n'achète
    # jamais de transport, une autre ne vit que de ça. La liste est donc rangée
    # par serveur, comme les modules éteints, et pour la même raison ce sont les
    # **écartés** qui sont retenus : rien d'enregistré vaut « tout est proposé »,
    # donc ni un serveur neuf ni un déploiement ne filtrent quoi que ce soit.

    async def types_exclus(self) -> list[str]:
        """Types de bâtiments à ne jamais proposer dans ce serveur, triés."""
        config = await self.config()
        return sorted(
            {
                str(nom).strip()
                for nom in config.get("types_exclus") or []
                if str(nom).strip()
            }
        )

    async def exclure_type(self, nom: str) -> bool:
        """Écarte un type. Renvoie False s'il l'était déjà.

        La comparaison passe par `normaliser_type`, celle de la sélection : un
        « Transport » ajouté à côté de `transport` se lirait comme deux
        exclusions dans `/promos types liste`, et en remettre une laisserait
        l'autre filtrer sans que rien ne le dise.
        """
        propre = str(nom).strip()
        exclus = await self.types_exclus()
        if normaliser_type(propre) in {normaliser_type(t) for t in exclus}:
            return False
        await self.maj_config(types_exclus=sorted([*exclus, propre]))
        return True

    async def remettre_type(self, nom: str) -> bool:
        """Rend un type écarté. Renvoie False s'il ne l'était pas.

        La liste vidée est écrite telle quelle — `maj_config` n'écarte que les
        `None`. Sauter une liste vide garderait l'ancienne en base : le type
        semblerait remis jusqu'au redémarrage, puis se réexcluerait tout seul là
        où personne ne regarde.
        """
        cible = normaliser_type(nom)
        exclus = await self.types_exclus()
        restants = [t for t in exclus if normaliser_type(t) != cible]
        if len(restants) == len(exclus):
            return False
        await self.maj_config(types_exclus=restants)
        return True

    # --- Types connus, ceux du dernier export ------------------------------
    #
    # Communs à tous les serveurs : ils décrivent le monde M8 et non un serveur.
    # Ils ne servent qu'à proposer des noms justes sous le curseur — Discord
    # n'accorde que trois secondes à une frappe, ce qui exclut de charger
    # l'export à chaque lettre tapée.

    async def types_connus(self) -> list[str]:
        """Types vus dans le dernier export chargé, triés."""
        config = await self.config()
        return sorted(
            {
                str(nom).strip()
                for nom in config.get("types_connus") or []
                if str(nom).strip()
            }
        )

    async def memoriser_types(self, noms: Any) -> None:
        """Retient les types de l'export qui vient d'être lu.

        Appelé à chaque chargement, comme les noms de salons se corrigent au
        premier post : un type ajouté par le jeu devient proposable sans qu'on y
        touche. D'où les deux abandons avant écriture — un export vide ou
        illisible ne doit pas vider les propositions, et un export identique à la
        veille ne doit pas écrire en base à chaque commande tapée.
        """
        propres = sorted({str(nom).strip() for nom in noms or [] if str(nom).strip()})
        if not propres or propres == await self.types_connus():
            return
        config = await self._enregistree()
        config["types_connus"] = propres
        await self.set("config", config)

    async def maj_config(self, **champs: Any) -> dict:
        enregistree = await self._enregistree()
        enregistree.update(
            {cle: valeur for cle, valeur in champs.items() if valeur is not None}
        )
        await self.set("config", enregistree)
        # La vue fusionnée, pas ce qui vient d'être écrit : l'appelant attend une
        # config complète, avec les défauts pour les clés jamais réglées.
        return await self.config()

    async def template(self) -> dict:
        from src.template import TEMPLATE_DEFAUT

        return await self.get("template", None) or TEMPLATE_DEFAUT

    async def set_template(self, modele: dict) -> None:
        await self.set("template", modele)

    # --- Filiales : relevés des frais et réglages de leur tableau -----------
    #
    # Les relevés vivent sous leur **propre clé** et non dans `config` : celle-ci
    # porte encore les champs plats dont la présence déclenche la migration des
    # fourchettes (voir `fourchettes`), et y greffer une liste sans rapport
    # rendrait cette signature plus difficile à lire.
    #
    # L'heure et les salons du tableau, eux, sont des réglages comme les autres
    # et restent dans `config`, préfixés `filiales_` pour ne pas se confondre
    # avec ceux des promotions — deux posts, deux destinations, deux horaires.

    async def filiales(self) -> list[Filiale]:
        """Relevés enregistrés, dans l'ordre de première saisie."""
        return depuis_json(await self.get("filiales", []))

    async def enregistrer_filiale(
        self, nom: str, benefices: Decimal, date: str
    ) -> Filiale:
        """Calcule les frais et enregistre le relevé, en remplaçant le précédent.

        Renvoie le relevé calculé : la commande affiche ce qu'elle vient
        d'enregistrer, et le relire serait un aller-retour pour la même valeur.
        """
        filiale = calculer(nom, benefices, date)
        await self.set("filiales", vers_json(enregistrer(await self.filiales(), filiale)))
        return filiale

    async def enregistrer_filiales(self, releves: list[Filiale]) -> list[Filiale]:
        """Enregistre un lot de relevés en **une** écriture. Renvoie le lot.

        Ce que colle la page web : treize relevés d'un coup. Passer treize fois
        par `enregistrer_filiale` ferait treize lectures et treize écritures, et
        une panne au septième laisserait le tableau à moitié rempli sans rien
        dire.

        Un lot n'est pas un nouveau tableau : les filiales qu'il ne nomme pas
        restent, à leur place. Sinon, un tableau collé en deux fois effacerait sa
        première moitié, et une filiale vendue — qui se retire avec `/frais
        retirer` — disparaîtrait sans qu'on l'ait demandé.
        """
        if not releves:
            # Rien à écrire, et surtout pas une liste vide par-dessus les relevés
            # du jour : c'est le clic sur « Enregistrer » avant le collage.
            return []
        filiales = await self.filiales()
        for releve in releves:
            filiales = enregistrer(filiales, releve)
        await self.set("filiales", vers_json(filiales))
        return releves

    async def retirer_filiale(self, nom: str) -> bool:
        """Retire un relevé. Renvoie False s'il n'existait pas."""
        avant = await self.filiales()
        apres = retirer(avant, nom)
        if len(apres) == len(avant):
            return False
        await self.set("filiales", vers_json(apres))
        return True

    async def retirer_filiales(self, noms: list[str]) -> tuple[int, list[str]]:
        """Retire plusieurs relevés d'un coup. Renvoie (retirés, noms inconnus).

        Les inconnus sont rendus tels qu'ils ont été saisis : c'est ce qu'on
        relit pour trouver sa faute de frappe. Ils ne font pas échouer les
        retraits valides de la même saisie, mais ils sont dits — sinon on
        croirait une filiale supprimée alors qu'elle reste dans le tableau.
        """
        avant = await self.filiales()
        inconnus = [nom for nom in noms if index_de(avant, nom) < 0]
        apres = retirer_plusieurs(avant, noms)
        if len(apres) != len(avant):
            await self.set("filiales", vers_json(apres))
        return len(avant) - len(apres), inconnus

    async def remettre_a_zero_filiales(self, date: str) -> int:
        """Remet tous les bénéfices à zéro en gardant les noms. Renvoie combien.

        Les noms restent : ils sont la clé d'import du jeu et l'assise de
        l'autocomplétion, si bien qu'un nouveau cycle ne demande que de
        ressaisir les montants.
        """
        filiales = await self.filiales()
        if not filiales:
            return 0
        await self.set("filiales", vers_json(remettre_a_zero(filiales, date)))
        return len(filiales)

    async def valeurs_aleatoires_filiales(
        self, date: str, alea: Random, exposant: int | None = None
    ) -> int:
        """Remplace les montants par des chiffres au hasard. Renvoie combien.

        Le tirage porte sur les filiales **déjà enregistrées** : il sert à voir
        le tableau avec des montants d'ordres de grandeur variés, pas à inventer
        des filiales qu'il faudrait ensuite retirer une à une.

        `exposant` borne le tirage à un palier du jeu, pour voir le tableau dans
        une unité donnée ; sans lui, il couvre toute l'échelle.

        Le générateur est passé par l'appelant, pour qu'un test puisse rejouer
        un tirage.
        """
        filiales = await self.filiales()
        if not filiales:
            return 0
        await self.set(
            "filiales", vers_json(valeurs_aleatoires(filiales, date, alea, exposant))
        )
        return len(filiales)

    async def heure_filiales(self) -> str:
        """Heure du tableau des frais, distincte de celle des promotions."""
        return str(
            (await self.config()).get("filiales_heure")
            or settings.HEURE_FILIALES_DEFAUT
        )

    async def salons_filiales(self) -> list[str]:
        """Salons où publier le tableau des frais."""
        config = await self.config()
        return [str(salon) for salon in config.get("filiales_salons") or [] if salon]

    async def _ecrire_salons_filiales(self, liste: list[str]) -> None:
        """Écrit directement plutôt que par `maj_config`, qui ignore les valeurs
        vides : une liste vidée ne serait jamais enregistrée et le salon
        reviendrait au redémarrage."""
        config = await self._enregistree()
        config["filiales_salons"] = liste
        await self.set("config", config)

    async def ajouter_salon_filiales(self, salon_id: str) -> bool:
        """Ajoute un salon. Renvoie False s'il y était déjà."""
        liste = await self.salons_filiales()
        if str(salon_id) in liste:
            return False
        await self._ecrire_salons_filiales([*liste, str(salon_id)])
        return True

    async def retirer_salon_filiales(self, salon_id: str) -> bool:
        """Retire un salon. Renvoie False s'il n'y était pas."""
        liste = await self.salons_filiales()
        if str(salon_id) not in liste:
            return False
        await self._ecrire_salons_filiales([s for s in liste if s != str(salon_id)])
        return True

    async def derniere_publication_filiales(self) -> str | None:
        """Marque du jour propre au tableau.

        Distincte de celle des promotions : partagée, le premier post consommerait
        le quota du second et l'un des deux ne sortirait jamais.
        """
        return await self.get("derniere_publication_filiales", None)

    async def marquer_publie_filiales(self, date: str) -> None:
        await self.set("derniere_publication_filiales", date)

    async def oublier_publication_filiales(self) -> None:
        await self.set("derniere_publication_filiales", None)

    async def derniere_publication(self) -> str | None:
        return await self.get("derniere_publication", None)

    async def marquer_publie(self, date: str) -> None:
        await self.set("derniere_publication", date)

    async def oublier_publication(self) -> None:
        """Efface la marque du jour pour pouvoir retester le déclenchement."""
        await self.set("derniere_publication", None)

    # --- Mot de passe de la page des frais ----------------------------------
    #
    # Sous sa **propre clé** et non dans `config` : celle-ci est rendue telle
    # quelle au site de contrôle par `/api/config`, et l'empreinte n'a aucune
    # raison d'en sortir. Hérité par `VueServeur`, donc rangé par entreprise sans
    # une ligne de plus — et c'est l'essentiel : un mot de passe commun donnerait
    # à qui le tient l'écriture chez toutes, celles que la page propose justement
    # dans un menu déroulant.

    async def motdepasse_page(self) -> dict | None:
        """L'empreinte enregistrée, ou rien. Jamais le mot de passe : il n'est
        pas en base.

        Ce qui n'est pas un enregistrement est lu comme une absence : la base est
        du JSON qu'on peut retoucher à la main, et rendre la valeur telle quelle
        ferait échouer la signature du cookie — une panne de la page là où il ne
        devrait y avoir qu'un refus.
        """
        trace = await self.get("motdepasse_page", None)
        return trace if isinstance(trace, dict) else None

    async def definir_motdepasse_page(self) -> str:
        """Tire un mot de passe, n'enregistre que son empreinte, rend le clair.

        Le tirage vit ici et non chez l'appelant : c'est ce qui garantit qu'aucun
        chemin n'écrit un mot de passe lisible en base. Le clair rendu est le seul
        moment où il existe — la commande le montre, et personne ne peut le relire
        ensuite.

        Remplace le précédent, donc coupe les cookies déjà distribués : ils sont
        signés avec l'empreinte (voir `src/motdepasse.py`).
        """
        clair = motdepasse.nouveau()
        await self.set("motdepasse_page", motdepasse.empreinte(clair))
        return clair

    async def effacer_motdepasse_page(self) -> bool:
        """Referme la page en écriture. Renvoie False s'il n'y en avait pas.

        Le booléen évite un « ✅ retiré » sur une entreprise qui n'en avait pas :
        on croirait avoir refermé une page qui ne l'a jamais été.
        """
        if await self.motdepasse_page() is None:
            return False
        await self.set("motdepasse_page", None)
        return True


class VueServeur(Store):
    """Le magasin commun, vu par un seul serveur.

    Un `Store` à part entière : les soixante accesseurs sont hérités tels quels,
    et seules les deux portes qu'ils empruntent tous — `get` et `set` — sont
    détournées vers `serveur:<id>:<clé>`. Chaque serveur a donc ses fourchettes,
    ses filiales, son heure, son template, sa liste d'accès et sa trace de
    « déjà publié », sans une ligne d'accesseur réécrite.

    **Il n'y a pas de repli sur la configuration commune.** Un serveur neuf ne
    voit rien : `/reglages importer` est le pont, à taper une fois. Un repli
    serait pire qu'un vide — un serveur qui aurait délibérément supprimé ses
    fourchettes hériterait de celles du commun, et publierait ce qu'on venait de
    lui retirer.

    Deux choses restent délibérément communes, et sont déléguées plus bas :

    - le **cache des noms de salons et de serveurs**, cosmétique et destiné au
      site : un nom de salon ne dépend pas de qui le regarde, et le cloisonner
      le remplirait en double ;
    - la **table des rôles à mentionner**, déjà rangée par serveur avant ce
      changement et lue telle quelle par le site. La cloisonner la rangerait deux
      fois — `serveur:111:roles` ne contenant qu'une entrée `111`.

    La vue n'a **pas** d'attributs `_pool` ni `_memoire` : un accesseur ajouté
    plus tard qui parlerait à la base sans passer par `get`/`set` lèverait
    `AttributeError` au lieu de lire silencieusement la valeur commune sous le
    nez d'un serveur qui croit lire la sienne. Un test structurel
    (`tests/test_cloisonnement.py`) vérifie qu'il n'en existe aucun.
    """

    def __init__(self, commun: Store, serveur_id: str | int):
        # Volontairement sans `super().__init__` : voir le dernier paragraphe du
        # docstring. La vue n'a rien à elle, sinon le nom de son tiroir.
        self.commun = commun
        self.serveur_id = str(serveur_id)
        self.dsn = commun.dsn

    def __repr__(self) -> str:
        return f"VueServeur({self.serveur_id})"

    def _cle(self, cle: str) -> str:
        return f"{PREFIXE_SERVEUR}:{self.serveur_id}:{cle}"

    # --- Les portes vers la base -------------------------------------------

    @property
    def persistant(self) -> bool:
        """Celle du magasin commun : la vue n'a pas de base à elle.

        Répondre « en mémoire » ferait dire à `/reglages voir` que la
        configuration sera perdue au redémarrage.
        """
        return self.commun.persistant

    async def connect(self) -> None:
        raise RuntimeError(
            "Une vue de serveur n'ouvre pas de connexion : "
            "connecter le magasin commun, puis appeler `.pour(serveur_id)`."
        )

    async def close(self) -> None:
        raise RuntimeError(
            "Une vue de serveur ne ferme pas la connexion des autres : "
            "fermer le magasin commun."
        )

    async def get(self, cle: str, defaut: Any = None) -> Any:
        return await self.commun.get(self._cle(cle), defaut)

    async def set(self, cle: str, valeur: Any) -> None:
        await self.commun.set(self._cle(cle), valeur)

    async def tout(self) -> dict[str, Any]:
        """Toute la base, tiroirs des autres serveurs compris.

        `tout()` sert au déménagement d'une base à l'autre : cloisonné, il ne
        recopierait que le tiroir d'un serveur, et le manque ne se verrait
        qu'une fois l'ancienne base éteinte.
        """
        return await self.commun.tout()

    def pour(self, serveur_id: str | int) -> "VueServeur":
        raise RuntimeError(
            "Une vue de serveur ne se resserre pas : "
            f"`{self!r}.pour({serveur_id!r})` ne peut être qu'une confusion."
        )

    async def vierge(self) -> bool:
        """Vrai si rien n'a jamais été écrit pour ce serveur.

        Il n'y a pas de repli : un serveur vierge est muet — ses posts ne sortent
        pas, son journal se tait. Le dire demande de reconnaître cet état, et le
        silence lui ressemble trait pour trait à une panne du bot. C'est ce que
        lisent `/reglages voir` et le signalement du démarrage.

        La question porte sur le **tiroir entier** et non sur une clé en
        particulier : un serveur qui n'a réglé qu'une fourchette est réglé, même
        s'il n'a jamais touché à sa configuration. Le cache commun des noms de
        salons, lui, n'y est pas — il se remplit tout seul au premier post, et le
        compter ferait taire le signalement pour un serveur qui n'a rien.
        """
        prefixe = self._cle("")
        return not any(cle.startswith(prefixe) for cle in await self.commun.tout())

    # --- Ce qui reste commun -----------------------------------------------

    async def salons_connus(self) -> dict[str, dict]:
        return await self.commun.salons_connus()

    async def serveurs(self) -> dict[str, str]:
        return await self.commun.serveurs()

    async def memoriser_salon(
        self,
        salon_id: str | int,
        nom: str,
        serveur_id: str | int,
        serveur_nom: str,
    ) -> None:
        await self.commun.memoriser_salon(salon_id, nom, serveur_id, serveur_nom)

    async def oublier_salons_orphelins(self) -> int:
        """Le ménage du cache commun, donc décidé sur toute la base.

        Depuis la vue, il ne verrait que les réglages de ce serveur et effacerait
        les noms des salons de tous les autres.
        """
        return await self.commun.oublier_salons_orphelins()

    async def types_connus(self) -> list[str]:
        """Ceux du monde, donc du magasin commun.

        Lus dans le tiroir du serveur, ils seraient vides pour qui n'a encore
        rien publié — et la commande n'aurait rien à proposer alors que l'export
        est là. Les types **écartés**, eux, restent au serveur : c'est un goût.
        """
        return await self.commun.types_connus()

    async def memoriser_types(self, noms: Any) -> None:
        await self.commun.memoriser_types(noms)

    async def roles(self) -> dict[str, str]:
        return await self.commun.roles()

    async def role_du_serveur(self, serveur_id: str | int | None) -> str | None:
        return await self.commun.role_du_serveur(serveur_id)

    async def definir_role(self, serveur_id: str | int, role_id: str | int) -> None:
        await self.commun.definir_role(serveur_id, role_id)

    async def effacer_role(self, serveur_id: str | int) -> bool:
        return await self.commun.effacer_role(serveur_id)
