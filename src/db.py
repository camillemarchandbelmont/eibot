"""Persistance de la configuration (Postgres free de Render).

Le disque de Render est éphémère : la fourchette, l'heure et le template
réglés par commande doivent survivre aux redéploiements, d'où Postgres.

Aucun bâtiment n'est stocké : le CSV reste la source de vérité, relu à
chaque exécution.

Sans `DATABASE_URL`, on retombe sur un stockage en mémoire pour pouvoir
lancer le bot en local sans base.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from src import settings

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_state (
    cle    TEXT PRIMARY KEY,
    valeur JSONB NOT NULL,
    maj_le TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

#: Nom donné à la fourchette issue d'une config plate (voir `Store.fourchettes`).
FOURCHETTE_MIGREE = "principale"

#: Champs de l'ancienne config à plat. Leur présence dans la config *enregistrée*
#: est la signature d'un bot à migrer, par opposition à un bot neuf.
_CHAMPS_PLATS = ("prix_min", "prix_max", "salons", "salon_id")


def _cle_nom(nom: str) -> str:
    """Forme comparable d'un nom de fourchette.

    Insensible à la casse : `Grosses` et `grosses` seraient indistinguables à
    l'œil dans une liste, donc ils désignent la même fourchette.
    """
    return str(nom).strip().casefold()


def _normaliser_fourchette(brute: dict) -> dict:
    """Fourchette aux champs garantis, quelle que soit l'origine du JSON.

    La config peut avoir été écrite par une version antérieure ou retouchée à
    la main. Chaque champ absent vaut mieux qu'un `KeyError` dans la boucle de
    publication, qui ferait sauter le post du jour.
    """
    return {
        "nom": str(brute.get("nom", "")).strip(),
        "prix_min": str(brute.get("prix_min", "0")),
        "prix_max": str(brute.get("prix_max", "0")),
        "salons": [str(salon) for salon in brute.get("salons") or [] if salon],
    }


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
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=3)
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
        `/config heure` sur un bot neuf lui inventerait donc une fourchette
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

        liste[index] = {**liste[index], "prix_min": str(prix_min), "prix_max": str(prix_max)}
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
        if str(serveur_id) not in table:
            return False
        await self._ecrire_roles(
            {s: r for s, r in table.items() if s != str(serveur_id)}
        )
        return True

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

    async def derniere_publication(self) -> str | None:
        return await self.get("derniere_publication", None)

    async def marquer_publie(self, date: str) -> None:
        await self.set("derniere_publication", date)

    async def oublier_publication(self) -> None:
        """Efface la marque du jour pour pouvoir retester le déclenchement."""
        await self.set("derniere_publication", None)
