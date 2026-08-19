"""Le tableau des frais de gestion par filiale, une fois par jour.

Ne touche **pas** à l'export du jeu : les relevés sont saisis à la main, et une
API en panne ne doit pas empêcher le tableau de sortir. C'est toute la raison
d'en faire une publication séparée plutôt qu'un embed de plus dans celle des
promotions.

Comme pour les promotions, l'heure et la trace de passage restent lues et écrites
là où elles vivaient avant les modules : le déménagement du mécanisme ne déplace
aucune donnée.
"""

from __future__ import annotations

from typing import Any

from src.modules import Envoi, Module, Publication, Tournee
from src.publish_filiales import embed_filiales


async def _preparer(bot: Any, magasin: Any, maintenant: Any) -> Tournee:
    """Un seul envoi : le tableau se lit d'un coup d'œil, il ne se découpe pas."""
    salons = await magasin.salons_filiales()
    if not salons:
        return Tournee(
            raison="aucun salon pour le tableau des frais (/filiales salon ajouter)"
        )

    # Publié même vide : l'absence de post ne se distinguerait pas d'une panne du
    # bot, et l'embed vide dit comment le remplir. C'est ce qui le distingue des
    # promotions, qui ne partent pas sans fourchette.
    filiales = await magasin.filiales()
    embed = embed_filiales(filiales, maintenant.strftime("%Y-%m-%d"))

    async def envoyer_dans(salon: Any) -> None:
        await salon.send(embed=embed)

    return Tournee(
        envois=(
            Envoi(etiquette="filiales", salons=tuple(salons), envoyer=envoyer_dans),
        ),
        compte=len(filiales),
        resume=f"{len(filiales)} filiale{'s' if len(filiales) > 1 else ''}",
    )


async def _lire_heure(magasin: Any) -> str:
    return await magasin.heure_filiales()


async def _lire_derniere(magasin: Any) -> str | None:
    return await magasin.derniere_publication_filiales()


async def _marquer(magasin: Any, date: str) -> None:
    await magasin.marquer_publie_filiales(date)


PUBLICATION = Publication(
    cle="filiales",
    titre="tableau des frais",
    preparer=_preparer,
    lire_heure=_lire_heure,
    lire_derniere=_lire_derniere,
    marquer=_marquer,
)

MODULE = Module(
    nom="filiales",
    titre="Tableau des frais",
    description="Relevés de frais de gestion par filiale, et tableau quotidien.",
    ordre=30,
    publications=(PUBLICATION,),
)
