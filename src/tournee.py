"""La mécanique d'envoi, une fois pour toutes les publications.

Le bot portait deux routines quasi identiques — une pour les promotions, une pour
le tableau des frais : même compte à rebours, même « déjà publié aujourd'hui ? »,
même boucle sur les salons, même isolation des pannes, même trace de passage. Tout
cela est ici, une seule fois. Ce qui change d'une publication à l'autre — l'heure,
les salons, le contenu — est déclaré par le module.

C'est ce qui fait qu'une publication de plus coûte une déclaration : rien à
brancher dans le bot, donc pas de plafond au nombre de posts quotidiens.

Une publication ne connaît ni le planning ni la façon de résoudre un salon : elle
rend une `Tournee`, et cette fonction s'occupe du reste.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .modules import Publication
from .schedule import doit_publier

log = logging.getLogger(__name__)

#: Préfixe du tiroir générique d'une publication. Chaque publication range son
#: heure et sa trace sous sa propre clé, si bien que deux modules ne peuvent pas
#: se marcher dessus et que supprimer un module n'abîme rien d'autre.
PREFIXE = "publication"


def cle_heure(cle: str) -> str:
    return f"{PREFIXE}:{cle}:heure"


def cle_derniere(cle: str) -> str:
    return f"{PREFIXE}:{cle}:derniere"


async def _heure(publication: Publication, magasin: Any) -> str:
    if publication.lire_heure is not None:
        return await publication.lire_heure(magasin)
    return await magasin.get(cle_heure(publication.cle)) or publication.heure_par_defaut


async def _derniere(publication: Publication, magasin: Any) -> str | None:
    if publication.lire_derniere is not None:
        return await publication.lire_derniere(magasin)
    return await magasin.get(cle_derniere(publication.cle))


async def _marquer(publication: Publication, magasin: Any, date: str) -> None:
    if publication.marquer is not None:
        await publication.marquer(magasin, date)
        return
    await magasin.set(cle_derniere(publication.cle), date)


async def faire_la_tournee(
    publication: Publication,
    bot: Any,
    magasin: Any,
    maintenant: datetime,
    forcer: bool = False,
) -> str:
    """Publie si c'est l'heure, et rend le compte rendu de ce qui s'est passé.

    Peut lever : la préparation d'une publication (charger l'export du jeu, par
    exemple) échoue avant que rien ne soit envoyé ni marqué. L'appelant isole la
    panne pour que les autres publications sortent quand même.
    """
    if not forcer:
        heure = await _heure(publication, magasin)
        derniere = await _derniere(publication, magasin)
        # Le compte à rebours **avant** la préparation : préparer coûte un appel à
        # l'API du jeu, et le cron passe toutes les cinq minutes.
        if not doit_publier(maintenant, heure, derniere):
            return "rien à faire"

    tournee = await publication.preparer(bot, magasin, maintenant)

    if not tournee.envois:
        # Rien à envoyer n'est pas une panne, et surtout ne marque pas la journée :
        # sinon régler le salon à 09:05 ne donnerait un post que le lendemain.
        return tournee.raison

    reussis: list[str] = []
    echecs: dict[str, str] = {}

    for envoi in tournee.envois:
        for salon_id in envoi.salons:
            # L'envoi est nommé : un même salon peut en servir deux, et
            # « <#111> a échoué » serait alors ambigu.
            ou = f"<#{salon_id}> ({envoi.etiquette})"
            try:
                salon = await bot.resoudre_salon(salon_id)
                await envoi.envoyer(salon)
            except Exception as erreur:
                # Un salon cassé — supprimé, permissions retirées — ne doit pas
                # priver les autres : on note et on continue.
                log.warning(
                    "%s impossible dans %s : %s", publication.titre, salon_id, erreur
                )
                echecs[ou] = f"{type(erreur).__name__}: {erreur}"
            else:
                reussis.append(ou)

    # Le journal passe par le bot, qui avale ses propres pannes : un observateur
    # ne doit jamais bloquer ce qu'il observe.
    await bot.journaliser_publication(tournee.compte, reussis, echecs)

    if not reussis:
        # Rien n'est parti : la journée reste à faire et le passage suivant
        # réessaiera.
        log.error("%s échouée dans les %d envois.", publication.titre, len(echecs))
        return f"{publication.titre} : échec dans les {len(echecs)} envoi(s)"

    # Marqué dès qu'un salon a reçu le post : sinon le passage suivant reposterait
    # là où ça avait marché.
    await _marquer(publication, magasin, maintenant.strftime("%Y-%m-%d"))

    total = sum(len(envoi.salons) for envoi in tournee.envois)
    log.info(
        "%s publiée (%d/%d envois, %s).",
        publication.titre, len(reussis), total, tournee.resume or "—",
    )
    # « Envois » et non « salons » : un salon servant deux envois en reçoit deux,
    # et le compter une fois annoncerait moins que ce qui est parti.
    resume = f", {tournee.resume}" if tournee.resume else ""
    return f"{publication.titre} : publié ({len(reussis)}/{total} envois{resume})"
