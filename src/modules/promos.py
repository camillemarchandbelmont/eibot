"""Les promotions du jeu : un post par fourchette de prix, chaque jour.

Ce fichier ne contient plus de mécanique d'envoi — pas de compte à rebours, pas
de boucle sur les salons, pas de « déjà publié aujourd'hui ? ». Tout cela est
dans `src.tournee`, une fois pour toutes les publications. Ce qui reste ici est
ce qui n'appartient qu'aux promotions : charger l'export du jeu, découper les
promotions par fourchette, et mentionner le rôle.

L'heure et la trace de passage restent lues et écrites **là où elles vivaient
avant les modules**. Le tiroir générique des publications est le défaut, pas une
obligation : les y déménager demanderait une reprise de données, et un
déploiement où la reprise manquerait ferait republier tout un jour à 09:00.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from src.db import bornes_tolerees
from src.modules import Envoi, Module, Publication, Tournee
from src.publish import envoyer
from src.source import SourceError

log = logging.getLogger(__name__)


async def _preparer(bot: Any, magasin: Any, maintenant: Any) -> Tournee:
    """Un envoi par fourchette servie, prêt à partir.

    Peut lever : l'export du jeu est chargé ici, donc **avant** que quoi que ce
    soit soit envoyé ou marqué. Sinon la panne de 09:00 annulerait la publication
    de toute la journée.
    """
    fourchettes = await magasin.fourchettes()
    if not fourchettes:
        return Tournee(raison="aucune fourchette configurée (/fourchette ajouter)")

    servies = [f for f in fourchettes if f["salons"]]
    if not servies:
        # Le message parle de salon et non de fourchette : celles-ci existent.
        return Tournee(raison="aucun salon configuré (/fourchette salon ajouter)")

    # L'export une seule fois pour toutes les fourchettes : recharger à chaque
    # tour multiplierait les appels à l'API du jeu pour des données identiques.
    try:
        donnees = await bot.charger()
    except SourceError as erreur:
        # Son message est déjà une phrase lisible, clé d'API masquée : la préfixer
        # du nom de la classe n'ajouterait que du bruit dans le salon de logs.
        await bot.journaliser_erreur(str(erreur))
        raise
    except Exception as erreur:
        # Panne non prévue (CSV corrompu) : elle doit rester visible dans Discord,
        # pas seulement dans les logs du serveur.
        await bot.journaliser_erreur(f"{type(erreur).__name__} : {erreur}")
        raise

    envois: list[Envoi] = []
    promos = 0

    for fourchette in servies:
        try:
            tolere_min, tolere_max = bornes_tolerees(fourchette)
            embeds, contenu, repli = await bot.construire_publication(
                Decimal(fourchette["prix_min"]),
                Decimal(fourchette["prix_max"]),
                donnees=donnees,
                tolere_min=tolere_min,
                tolere_max=tolere_max,
            )
        except Exception as erreur:
            # Rendu impossible pour *cette* fourchette (template appliqué à des
            # valeurs inattendues) : les autres doivent quand même partir, alors
            # qu'une panne de l'export les condamnait toutes.
            log.warning("Rendu impossible pour « %s » : %s", fourchette["nom"], erreur)
            await bot.journaliser_erreur(
                f"Fourchette « {fourchette['nom']} » : "
                f"{type(erreur).__name__} : {erreur}"
            )
            continue

        promos += 0 if repli else len(embeds)
        envois.append(
            Envoi(
                etiquette=fourchette["nom"],
                salons=tuple(fourchette["salons"]),
                envoyer=_envoyeur(magasin, embeds, contenu, repli),
            )
        )

    if not envois:
        # Toutes les fourchettes ont échoué au rendu : rien à envoyer, et surtout
        # rien à marquer — le passage suivant réessaiera.
        return Tournee(
            raison=f"rendu impossible pour les {len(servies)} fourchette(s)"
        )

    return Tournee(
        envois=tuple(envois),
        compte=promos,
        resume=f"{len(envois)} fourchette{'s' if len(envois) > 1 else ''}",
    )


def _envoyeur(magasin: Any, embeds: list[dict], contenu: str, repli: str):
    """Ce qui part dans un salon donné, une fois le contenu rendu."""

    async def envoyer_dans(salon: Any) -> None:
        if repli:
            await salon.send(repli)
            return
        # Le rôle du serveur **du salon**, et non un rôle global : un rôle n'existe
        # que dans son serveur, et `<@&123>` envoyé ailleurs s'affiche en
        # `@deleted-role`.
        serveur = getattr(salon, "guild", None)
        role_id = await magasin.role_du_serveur(getattr(serveur, "id", None))
        await envoyer(salon, embeds, contenu, role_id)

    return envoyer_dans


async def _lire_heure(magasin: Any) -> str:
    return (await magasin.config())["heure"]


async def _lire_derniere(magasin: Any) -> str | None:
    return await magasin.derniere_publication()


async def _marquer(magasin: Any, date: str) -> None:
    await magasin.marquer_publie(date)


PUBLICATION = Publication(
    cle="promos",
    titre="promotions",
    preparer=_preparer,
    lire_heure=_lire_heure,
    lire_derniere=_lire_derniere,
    marquer=_marquer,
)

MODULE = Module(
    nom="promos",
    titre="Promotions",
    description="Les promotions du jeu, par fourchette de prix.",
    ordre=20,
    publications=(PUBLICATION,),
)
