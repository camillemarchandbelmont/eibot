"""Le contrat de module : un fichier du dossier, une fonctionnalité.

Le bot balaie ce dossier au démarrage. Il n'y a donc **aucun registre à tenir à
jour ailleurs** : poser un fichier qui déclare un `MODULE` suffit à ajouter une
fonctionnalité, le retirer suffit à l'enlever. C'est toute la raison du balayage
plutôt que d'une liste écrite en dur, qui oublierait en silence le fichier ajouté
après elle.

Un module déclare ses commandes et, s'il en veut, une ou **plusieurs**
publications quotidiennes. Rien ne plafonne leur nombre : la mécanique d'envoi
est unique et paramétrée, si bien qu'un troisième post quotidien coûte une
déclaration et non une greffe dans le bot.

Ce qu'on ne fait pas : charger un module à chaud. Ce serait exécuter du code
arbitraire arrivé par Discord, et le disque de Render étant effacé à chaque
redémarrage, le fichier disparaîtrait de lui-même. Un module passe par le dépôt.
L'activation par serveur, elle, ne demande aucun redémarrage.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

#: Le paquet balayé au démarrage.
PAQUET = "src.modules"

#: Forme d'un nom de module ou d'une clé de publication.
#:
#: Minuscules, chiffres et traits d'union. Ce nom sert à deux choses qui
#: l'exigent : de clé de rangement en base, et de valeur de choix dans une
#: commande Discord — qui refuse les majuscules et les espaces. Un nom
#: inutilisable ne se verrait sinon qu'à la synchronisation des commandes, c'est
#: à dire au démarrage, en production.
FORME_DU_NOM = re.compile(r"^[a-z][a-z0-9-]*$")

#: Rang par défaut dans le menu, laissant de la place devant et derrière.
ORDRE_PAR_DEFAUT = 100

#: Heure de publication tant que rien n'a été réglé.
HEURE_PAR_DEFAUT = "09:00"

#: Forme d'une heure de publication, 'HH:MM'.
FORME_DE_L_HEURE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _valider_nom(nom: str, quoi: str) -> str:
    if not FORME_DU_NOM.match(nom or ""):
        raise ValueError(
            f"{quoi} inutilisable : {nom!r}. Attendu des minuscules, des chiffres "
            "et des traits d'union, commençant par une lettre (ex. « tableau-des-frais »)."
        )
    return nom


@dataclass(frozen=True)
class Envoi:
    """Un contenu, et les salons où il part.

    Une publication en produit autant qu'elle veut : les promotions en font un
    par fourchette, le tableau des frais un seul. C'est ce qui permet aux deux de
    partager la même mécanique d'envoi alors que l'une découpe son post et
    l'autre pas.
    """

    #: Ce que le journal affiche pour cet envoi — le nom de la fourchette, par
    #: exemple. Un même salon peut servir deux envois, et « <#111> a échoué »
    #: serait alors ambigu.
    etiquette: str
    salons: tuple[str, ...]
    #: `async (cible, ephemere=False) -> None`. C'est le module qui sait envoyer
    #: son contenu : un embed brut, un embed plus une mention de rôle, un fichier.
    #:
    #: `cible` n'est pas toujours un salon : l'aperçu passe le fil de réponse de
    #: la commande, et attend `ephemere=True`. Un contenu qui l'ignorerait
    #: partirait en clair dans le salon — un aperçu public vaut publication, soit
    #: l'inverse de ce qu'on demande. La commande d'aperçu force donc le drapeau
    #: de son côté ; le module reste libre de s'en servir pour taire une mention
    #: de rôle, qui n'a rien à faire dans une prévisualisation.
    envoyer: Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class Tournee:
    """Ce qu'une publication a à envoyer ce tour-ci.

    Une tournée vide n'est pas une panne : elle porte la raison, qui remonte
    telle quelle dans le compte rendu de `/tick`. « Rien » sans le pourquoi
    obligerait à deviner entre « pas l'heure », « aucun salon » et « rien à
    dire ».
    """

    envois: tuple[Envoi, ...] = ()
    #: Ce que la publication a compté — promotions trouvées, filiales listées.
    #: Le journal l'annonce ; il ne s'en sert pas pour décider.
    compte: int = 0
    #: Ce que le compte rendu ajoute au décompte des envois : « 3 fourchettes »,
    #: « 5 filiales ». Distinct de `compte`, qui dit ce qui a été trouvé et non
    #: ce qui a été servi — les promotions annoncent 12 promos dans 3 fourchettes.
    resume: str = ""
    raison: str = ""
    #: Ce qui ne partira pas, en `(étiquette, pourquoi)` — une fourchette sans
    #: salon, un contenu qu'on n'a pas su rendre. L'aperçu les nomme : sa question
    #: est « qu'est-ce que le bot va poster ? », donc aussi ce qu'il ne postera
    #: pas. Les taire laisserait croire que tout part.
    ecartes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Publication:
    """Un post quotidien : son heure, ses salons, son contenu.

    Un module peut en déclarer plusieurs, chacune indépendante — sa propre heure,
    sa propre liste de salons, sa propre trace de « déjà envoyé ». Une qui tombe
    en panne n'empêche pas les autres, comme un salon cassé ne prive pas les
    autres salons.
    """

    #: Range l'heure, les salons et la trace de passage. Deux publications qui la
    #: partageraient : la seconde ne partirait jamais, la première ayant déjà
    #: marqué la journée.
    cle: str
    #: Ce que le compte rendu nomme : « promotions », « tableau des frais ».
    titre: str
    #: `async (bot, magasin, maintenant) -> Tournee`. Peut lever : la panne
    #: remonte **avant** que la journée soit marquée, sinon l'export en panne à
    #: 09:00 annulerait la publication de toute la journée.
    preparer: Callable[..., Awaitable[Tournee]] | None
    #: L'heure tant que personne n'a rien réglé, au format 'HH:MM'. Un module
    #: neuf publie donc dès son premier jour : une première journée muette se
    #: lirait comme un module qui ne marche pas.
    heure_par_defaut: str = HEURE_PAR_DEFAUT
    #: Accès à l'heure, à la trace de passage et aux salons. Laissés vides, la
    #: publication utilise son tiroir générique. Les deux publications historiques
    #: les fournissent : leurs réglages vivent dans la config depuis avant les
    #: modules, et les déplacer demanderait une reprise de données que ce chantier
    #: n'a pas à faire.
    #:
    #: Lecteur et écrivain vont par paires, et `__post_init__` le vérifie : une
    #: publication qui lirait son heure dans la config pendant que la commande
    #: l'écrit dans le tiroir générique répondrait « ✅ » sans que l'heure change.
    lire_heure: Callable[..., Awaitable[str]] | None = None
    #: `async (magasin, heure) -> None`.
    ecrire_heure: Callable[..., Awaitable[None]] | None = None
    lire_derniere: Callable[..., Awaitable[str]] | None = None
    #: `async (magasin, date | None) -> None`. `None` efface la marque : régler
    #: l'heure oublie la journée, sinon un post déjà sorti bloquerait le nouvel
    #: horaire jusqu'au lendemain.
    marquer: Callable[..., Awaitable[None]] | None = None
    #: Salons de la publication. Une publication peut n'en avoir aucun en propre —
    #: les promotions attachent les leurs à une fourchette : elle ne déclare alors
    #: rien ici et n'expose pas les commandes `salon`.
    lire_salons: Callable[..., Awaitable[list]] | None = None
    #: `async (magasin, salon_id) -> bool`, faux si le salon y était déjà.
    ajouter_salon: Callable[..., Awaitable[bool]] | None = None
    #: `async (magasin, salon_id) -> bool`, faux si le salon n'y était pas.
    retirer_salon: Callable[..., Awaitable[bool]] | None = None

    def __post_init__(self) -> None:
        _valider_nom(self.cle, "Clé de publication")
        if not (self.titre or "").strip():
            raise ValueError(f"Publication « {self.cle} » sans titre.")
        self._verifier_les_paires()
        if not FORME_DE_L_HEURE.match(self.heure_par_defaut or ""):
            # Le planning retombe sur 09:00 devant une heure illisible, sans rien
            # dire : le module publierait à une heure qu'il n'a pas demandée.
            raise ValueError(
                f"Publication « {self.cle} » : heure par défaut illisible "
                f"({self.heure_par_defaut!r}). Attendu 'HH:MM' sur 24 h, "
                "avec le zéro de tête (ex. « 09:00 »)."
            )

    def _verifier_les_paires(self) -> None:
        """Refuse une publication qui lirait ici et écrirait ailleurs.

        Le tiroir générique est le défaut de chaque accès **séparément** : rien
        n'empêche donc, en théorie, de lire l'heure dans la config et de l'écrire
        dans le tiroir. La commande confirmerait, l'heure ne changerait pas, et ça
        ne se verrait que le lendemain à l'heure du post.
        """
        paires = (
            ("heure", ("lire_heure",), ("ecrire_heure",)),
            ("trace de passage", ("lire_derniere",), ("marquer",)),
            ("salons", ("lire_salons",), ("ajouter_salon", "retirer_salon")),
        )
        for quoi, lecteurs, ecrivains in paires:
            fournis = [
                nom for nom in (*lecteurs, *ecrivains) if getattr(self, nom) is not None
            ]
            if fournis and len(fournis) != len(lecteurs) + len(ecrivains):
                manquants = [
                    nom
                    for nom in (*lecteurs, *ecrivains)
                    if getattr(self, nom) is None
                ]
                raise ValueError(
                    f"Publication « {self.cle} » : {quoi} lue et écrite à deux "
                    f"endroits différents. Il manque {', '.join(manquants)} en "
                    f"regard de {', '.join(fournis)}."
                )


@dataclass(frozen=True)
class Module:
    """Ce qu'un fichier du dossier déclare.

    `nom` sert de clé : il range les réglages du module, et c'est lui qu'on tape
    dans `/reglages modules activer`. Il est donc contraint comme une clé, pas
    comme une phrase — le `titre` est là pour être lu.
    """

    nom: str
    titre: str
    description: str
    #: Rang dans le menu. À rang égal, le nom tranche : sans cela l'ordre
    #: dépendrait du système de fichiers et changerait d'un démarrage à l'autre.
    ordre: int = ORDRE_PAR_DEFAUT
    #: `(bot) -> None`, qui ajoute les commandes du module à l'arbre.
    enregistrer: Callable[..., None] | None = None
    publications: tuple[Publication, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _valider_nom(self.nom, "Nom de module")
        if not (self.titre or "").strip():
            raise ValueError(f"Module « {self.nom} » sans titre : la liste des "
                             "modules afficherait une ligne vide.")
        cles = [publication.cle for publication in self.publications]
        doublons = {cle for cle in cles if cles.count(cle) > 1}
        if doublons:
            raise ValueError(
                f"Module « {self.nom} » : deux publications de même clé "
                f"({', '.join(sorted(doublons))})."
            )


def noms_de_modules(paquet: str = PAQUET) -> list[str]:
    """Les fichiers du dossier, triés, hors ceux commençant par un blanc souligné.

    Le tri donne un point de départ stable ; c'est `ordre` qui décide ensuite du
    rang réel. Les fichiers en `_` sont exclus pour que le paquet lui-même et les
    brouillons n'apparaissent pas refusés à chaque démarrage.

    Un dossier vide rend une liste vide : c'est un état valide, pas une panne.
    """
    try:
        importe = importlib.import_module(paquet)
    except ModuleNotFoundError:
        return []
    return sorted(
        info.name
        for info in pkgutil.iter_modules(importe.__path__)
        if not info.name.startswith("_")
    )


def charger(
    noms: list[str], importer: Callable[[str], Any], paquet: str = PAQUET
) -> tuple[list[Module], dict[str, str]]:
    """Les modules chargés, et ceux qui ont refusé de l'être avec leur raison.

    Un module cassé est **écarté, pas fatal** : un fichier en cours d'écriture
    couperait sinon les publications de toutes les entreprises, et la panne ne se
    verrait que le lendemain. La raison est retenue pour être dite dans le salon
    de logs : « filiales n'a pas chargé » sans le pourquoi obligerait à aller
    lire les journaux du serveur.

    La fonction d'import est passée en argument pour que les tests puissent
    éprouver le refus sans écrire de fichier cassé sur le disque.
    """
    charges: list[Module] = []
    refuses: dict[str, str] = {}
    pris: dict[str, str] = {}

    for nom in noms:
        try:
            fichier = importer(f"{paquet}.{nom}")
        except Exception as erreur:
            refuses[nom] = f"{type(erreur).__name__} : {erreur}"
            continue

        module = getattr(fichier, "MODULE", None)
        if not isinstance(module, Module):
            refuses[nom] = (
                "pas de MODULE déclaré : le fichier doit exposer "
                "`MODULE = Module(nom=…, titre=…, description=…)`."
            )
            continue

        if module.nom in pris:
            # Deux modules de même nom partageraient leur tiroir de réglages :
            # l'un lirait l'heure de l'autre, et en éteindre un éteindrait les
            # deux. Le second est écarté plutôt que d'écraser le premier.
            refuses[nom] = (
                f"le nom « {module.nom} » est déjà pris par "
                f"{pris[module.nom]}.py."
            )
            continue

        pris[module.nom] = nom
        charges.append(module)

    charges.sort(key=lambda module: (module.ordre, module.nom))
    return charges, refuses


def decouvrir(paquet: str = PAQUET) -> tuple[list[Module], dict[str, str]]:
    """Ce que le bot appelle au démarrage : balayer le dossier, puis charger."""
    return charger(noms_de_modules(paquet), importlib.import_module, paquet)


def greffer(
    bot: Any, modules: list[Module]
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """Ajoute les commandes de chaque module à l'arbre.

    Rend deux choses : les modules qui ont échoué avec leur raison, et **ce que
    chaque module a posé à la racine** de l'arbre.

    Ce second relevé est pris ici parce que c'est le seul moment où l'on sait à
    qui attribuer une commande : le module vient de s'enregistrer, et ce qui est
    apparu est à lui. Une liste écrite à la main oublierait le module suivant, et
    ses commandes ne quitteraient jamais le menu d'un serveur qui l'éteint.

    Même garde que `charger`, et pour la même raison : ce qui est exécuté ici est
    du code de module, qui peut échouer tout autant — un nom de commande en
    doublon, une description trop longue pour Discord. Un module fautif est écarté
    et nommé, jamais fatal, sinon un fichier en cours d'écriture couperait les
    publications de toutes les entreprises.

    L'ordre est celui reçu, c'est à dire celui de `charger` : c'est lui qu'on
    retrouve dans le menu Discord.
    """
    refuses: dict[str, str] = {}
    commandes: dict[str, tuple[str, ...]] = {}
    for module in modules:
        if module.enregistrer is None:
            # Un module qui ne fait que publier est un cas ordinaire.
            continue
        avant = _noms_a_la_racine(bot)
        try:
            module.enregistrer(bot)
        except Exception as erreur:
            log.warning(
                "Les commandes du module « %s » n'ont pas pu être greffées.",
                module.nom,
                exc_info=True,
            )
            refuses[module.nom] = f"{type(erreur).__name__} : {erreur}"
        # Relevé même après un échec : un module qui a lâché à mi-chemin a pu
        # poser sa première commande, et non attribuée elle resterait dans le
        # menu de tous les serveurs sans que rien ne puisse l'en retirer — son
        # module est refusé, donc absent de la liste, et pourtant elle est là.
        posees = tuple(
            nom for nom in _noms_a_la_racine(bot) if nom not in avant
        )
        if posees:
            commandes[module.nom] = posees
    return refuses, commandes


def _noms_a_la_racine(bot: Any) -> tuple[str, ...]:
    """Les commandes de premier niveau de l'arbre, dans leur ordre d'ajout.

    L'ordre compte : c'est celui du menu Discord, donc celui qu'on a sous les
    yeux quand on cherche à quel module appartient une commande.
    """
    return tuple(commande.name for commande in bot.tree.get_commands())
