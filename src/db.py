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

from src import settings
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

    return {
        "nom": str(brute.get("nom", "")).strip(),
        "prix_min": prix_min,
        "prix_max": prix_max,
        "salons": [str(salon) for salon in brute.get("salons") or [] if salon],
        "tolere_min": "" if tolere_min is None else str(tolere_min),
        "tolere_max": "" if tolere_max is None else str(tolere_max),
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
        `/fourchette heure` sur un bot neuf lui inventerait donc une fourchette
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
        """`maj_config` ignore les valeurs vides : une liste vidée ne serait
        jamais enregistrée, et un membre retiré reviendrait au redémarrage."""
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

    async def roles(self) -> dict[str, str]:
        return await self.commun.roles()

    async def role_du_serveur(self, serveur_id: str | int | None) -> str | None:
        return await self.commun.role_du_serveur(serveur_id)

    async def definir_role(self, serveur_id: str | int, role_id: str | int) -> None:
        await self.commun.definir_role(serveur_id, role_id)

    async def effacer_role(self, serveur_id: str | int) -> bool:
        return await self.commun.effacer_role(serveur_id)
