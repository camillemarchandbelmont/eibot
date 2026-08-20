"""Reprendre la configuration commune dans le tiroir d'un serveur.

Le cloisonnement (`Store.pour`) n'a **pas de repli** : un serveur qui n'a rien
réglé ne publie nulle part. C'est voulu — un post surprise dans un salon que
personne n'a désigné pour lui serait pire — mais il faut alors un pont, et
`/reglages importer` est ce pont, à taper une fois dans chaque serveur.

Le calcul est ici, à part de la commande : il ne connaît ni Discord ni la base,
seulement la forme du stockage. Ce qu'il reprend, ce qu'il écarte et ce qu'il
laisse tel quel se lit donc sur des dictionnaires nus, ce qui est la seule façon
d'éprouver les cas qui comptent — un salon d'un autre serveur, une clé déjà
réglée, le tiroir d'un module qui n'existe pas encore.

Deux règles gouvernent tout le fichier :

**Ne garder que les salons de ce serveur.** Une seule liste de salons couvrait
les deux serveurs. Tout recopier ferait publier chaque serveur dans les salons de
tous les autres ; la garde de `src/tournee.py` les écarterait à l'envoi, mais
chaque passage écrirait un signalement dans le journal et `/reglages voir`
montrerait des salons qui ne sont pas là.

**Ne rien écraser.** La commande peut être retapée, ou tapée après un premier
réglage à la main. Une clé déjà présente dans le tiroir du serveur est laissée
telle quelle et signalée ; ce qui manque est complété.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.db import PREFIXE_SERVEUR

#: Clés de la config qui restent **communes**, et ne sont donc pas recopiées.
#:
#: `salons_connus` et `serveurs` sont le cache des noms, que `VueServeur` lit
#: dans le commun : un nom de salon ne dépend pas de qui le regarde. `roles` est
#: déjà une table par serveur, lue dans le commun elle aussi. Recopiées dans le
#: tiroir, ces clés y dormiraient sans lecteur — et aucun ménage ne viendrait
#: jamais les y nettoyer. `role_id` suit `roles` : c'est son ancêtre plat.
CLES_COMMUNES = ("salons_connus", "serveurs", "roles", "role_id")

#: Champs d'une config qui désignent un salon unique, et non une liste.
_CHAMPS_SALON = ("salon_id", "logs_salon_id")

#: Champs d'une config qui portent une liste de salons.
_CHAMPS_SALONS = ("salons", "filiales_salons")


@dataclass(frozen=True)
class Importation:
    """Ce qu'un import écrirait, et ce qu'il laisserait de côté.

    Rien n'est écrit ici : la commande décide, et c'est ce qui permet d'annoncer
    le résultat avant de toucher à quoi que ce soit.
    """

    #: Clé (non préfixée) -> valeur à écrire dans le tiroir du serveur.
    a_ecrire: dict[str, Any]
    #: Ids des salons repris, triés.
    salons_gardes: tuple[str, ...]
    #: Ids des salons d'un autre serveur, écartés, triés et sans doublon.
    salons_ecartes: tuple[str, ...]
    #: Clés que le serveur avait déjà, laissées telles quelles.
    deja_reglees: tuple[str, ...]


def nommer(cle: str) -> str:
    """Nom lisible d'une clé de stockage, pour le compte rendu.

    Une clé inconnue est nommée telle quelle plutôt qu'omise : le tiroir d'un
    module à venir doit apparaître dans le compte rendu, sinon on ne pourrait
    pas constater qu'il est passé.
    """
    connues = {
        "config": "les réglages (heure, fourchettes, salons, accès)",
        "template": "le template des posts",
        "filiales": "les relevés des filiales",
        "derniere_publication": "la marque du jour des promotions",
        "derniere_publication_filiales": "la marque du jour du tableau des frais",
    }
    if cle in connues:
        return connues[cle]

    parties = cle.split(":")
    if len(parties) == 3 and parties[0] == "publication":
        publication, quoi = parties[1], parties[2]
        libelles = {
            "heure": "l'heure",
            "salons": "les salons",
            "derniere": "la marque du jour",
        }
        return f"{libelles.get(quoi, quoi)} de « {publication} »"

    return f"`{cle}`"


def preparer(
    base: dict[str, Any], serveur_id: str | int, salons_du_serveur: set[str]
) -> Importation:
    """Décide ce qu'un import reprendrait, sans rien écrire.

    `base` est toute la base (`Store.tout()`), `salons_du_serveur` les ids des
    salons que ce serveur possède — la seule chose que le calcul ne peut pas
    savoir seul.

    Les tiroirs des serveurs sont sautés en entier : celui du serveur visé, dont
    la présence d'une clé signifie « déjà réglé », et ceux des autres, qui lui
    donneraient les réglages du voisin et enfermeraient un tiroir dans un tiroir.
    """
    prefixe = f"{PREFIXE_SERVEUR}:{serveur_id}:"
    deja = {cle[len(prefixe):] for cle in base if cle.startswith(prefixe)}

    a_ecrire: dict[str, Any] = {}
    gardes: set[str] = set()
    ecartes: set[str] = set()
    laissees: list[str] = []

    def trier(salons: Any) -> list[str]:
        """Garde les salons de ce serveur, note les autres."""
        retenus = []
        for salon in salons or []:
            if not salon:
                continue
            if str(salon) in salons_du_serveur:
                gardes.add(str(salon))
                retenus.append(str(salon))
            else:
                ecartes.add(str(salon))
        return retenus

    for cle, valeur in sorted(base.items()):
        if cle.startswith(f"{PREFIXE_SERVEUR}:"):
            continue
        if cle in deja:
            laissees.append(cle)
            continue
        a_ecrire[cle] = _cloisonner(cle, valeur, trier)

    return Importation(
        a_ecrire=a_ecrire,
        salons_gardes=tuple(sorted(gardes)),
        salons_ecartes=tuple(sorted(ecartes)),
        deja_reglees=tuple(laissees),
    )


def _cloisonner(cle: str, valeur: Any, trier) -> Any:
    """La valeur d'une clé, réduite aux salons de ce serveur.

    Les deux conventions sont celles que `Store` connaît déjà (voir
    `_salons_servis`) : un tiroir de publication finit par `:salons`, une config
    porte ses salons sous `salons`, `salon_id`, `filiales_salons`, et dans chaque
    fourchette.
    """
    if cle.endswith(":salons"):
        return trier(valeur)

    if (cle == "config" or cle.endswith(":config")) and isinstance(valeur, dict):
        config = {c: v for c, v in valeur.items() if c not in CLES_COMMUNES}

        for champ in _CHAMPS_SALONS:
            if champ in config:
                config[champ] = trier(config[champ])

        for champ in _CHAMPS_SALON:
            # Retiré et non vidé : `logs_salon_id: None` désactive le journal, et
            # un `salon_id` vide serait quand même la signature d'une config
            # plate — le serveur se croirait à migrer.
            if config.get(champ) and not trier([config[champ]]):
                config.pop(champ)

        fourchettes = config.get("fourchettes")
        if isinstance(fourchettes, list):
            # La fourchette reste même sans salon : ses bornes sont un réglage,
            # et les reperdre obligerait à les ressaisir alors qu'il n'y a qu'un
            # salon à corriger. `/fourchette liste` la montrera sans salon.
            config["fourchettes"] = [
                {**f, "salons": trier(f.get("salons"))} if isinstance(f, dict) else f
                for f in fourchettes
            ]

        return config

    return valeur
