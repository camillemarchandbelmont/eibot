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

from src.modules import Publication
from src.schedule import doit_publier

log = logging.getLogger(__name__)

#: Préfixe du tiroir générique d'une publication. Chaque publication range son
#: heure et sa trace sous sa propre clé, si bien que deux modules ne peuvent pas
#: se marcher dessus et que supprimer un module n'abîme rien d'autre.
PREFIXE = "publication"


def cle_heure(cle: str) -> str:
    return f"{PREFIXE}:{cle}:heure"


def cle_derniere(cle: str) -> str:
    return f"{PREFIXE}:{cle}:derniere"


def cle_salons(cle: str) -> str:
    return f"{PREFIXE}:{cle}:salons"


# Les six accès qui suivent sont publics : les commandes `heure`, `apercu`,
# `publier` et `salon` passent par eux, et c'est ce qui leur permet d'être écrites
# une seule fois pour toutes les publications. Chacune redirige vers ce que le
# module a déclaré, ou retombe sur son tiroir générique.


async def heure_de(publication: Publication, magasin: Any) -> str:
    if publication.lire_heure is not None:
        return await publication.lire_heure(magasin)
    return await magasin.get(cle_heure(publication.cle)) or publication.heure_par_defaut


async def ecrire_l_heure(publication: Publication, magasin: Any, heure: str) -> None:
    if publication.ecrire_heure is not None:
        await publication.ecrire_heure(magasin, heure)
        return
    await magasin.set(cle_heure(publication.cle), heure)


async def derniere_de(publication: Publication, magasin: Any) -> str | None:
    if publication.lire_derniere is not None:
        return await publication.lire_derniere(magasin)
    return await magasin.get(cle_derniere(publication.cle))


async def marquer_le_jour(
    publication: Publication, magasin: Any, date: str | None
) -> None:
    """Pose la trace de passage, ou l'efface si `date` vaut None."""
    if publication.marquer is not None:
        await publication.marquer(magasin, date)
        return
    await magasin.set(cle_derniere(publication.cle), date)


async def salons_de(publication: Publication, magasin: Any) -> list[str]:
    if publication.lire_salons is not None:
        return [str(salon) for salon in await publication.lire_salons(magasin)]
    return [str(salon) for salon in await magasin.get(cle_salons(publication.cle), [])]


async def ajouter_un_salon(
    publication: Publication, magasin: Any, salon_id: str
) -> bool:
    """Vrai si le salon a été ajouté, faux s'il y était déjà.

    Le doublon est refusé et non ignoré : un salon compté deux fois recevrait
    deux fois le même post.
    """
    if publication.ajouter_salon is not None:
        return await publication.ajouter_salon(magasin, salon_id)
    salons = await salons_de(publication, magasin)
    if str(salon_id) in salons:
        return False
    await magasin.set(cle_salons(publication.cle), [*salons, str(salon_id)])
    return True


async def retirer_un_salon(
    publication: Publication, magasin: Any, salon_id: str
) -> bool:
    """Vrai si le salon a été retiré, faux s'il n'y était pas."""
    if publication.retirer_salon is not None:
        return await publication.retirer_salon(magasin, salon_id)
    salons = await salons_de(publication, magasin)
    if str(salon_id) not in salons:
        return False
    await magasin.set(
        cle_salons(publication.cle),
        [salon for salon in salons if salon != str(salon_id)],
    )
    return True


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
        heure = await heure_de(publication, magasin)
        derniere = await derniere_de(publication, magasin)
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
    await marquer_le_jour(publication, magasin, maintenant.strftime("%Y-%m-%d"))

    total = sum(len(envoi.salons) for envoi in tournee.envois)
    log.info(
        "%s publiée (%d/%d envois, %s).",
        publication.titre, len(reussis), total, tournee.resume or "—",
    )
    # « Envois » et non « salons » : un salon servant deux envois en reçoit deux,
    # et le compter une fois annoncerait moins que ce qui est parti.
    resume = f", {tournee.resume}" if tournee.resume else ""
    return f"{publication.titre} : publié ({len(reussis)}/{total} envois{resume})"
