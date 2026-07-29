"""Décide si l'heure de publication quotidienne est arrivée.

Le service gratuit Render s'endort : on ne peut pas compter sur un réveil
`asyncio` pile à 09:00. cron-job.org appelle `/tick` régulièrement et cette
fonction tranche — en garantissant une seule publication par jour, même si le
ping arrive à 09:03 ou trois fois de suite.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

#: Intervalle de vérification de la boucle interne. Une minute suffit : c'est
#: la granularité de `heure` ('HH:MM'), et le coût d'un tour est nul quand ce
#: n'est pas l'heure.
INTERVALLE_DEFAUT = 60

#: Retard toléré pour rattraper un post manqué (Render endormi, service
#: redémarré). Au-delà, on renonce pour la journée plutôt que de publier à
#: contretemps et de bloquer la publication suivante.
FENETRE_RATTRAPAGE = 60


def maintenant_local(fuseau: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(fuseau))
    except Exception:
        return datetime.now(ZoneInfo("Europe/Paris"))


def doit_publier(
    maintenant: datetime,
    heure: str,
    derniere_publication: str | None,
    fenetre_minutes: int = FENETRE_RATTRAPAGE,
) -> bool:
    """Faut-il publier à cet instant ?

    `heure` est au format 'HH:MM' dans le fuseau de `maintenant`.
    `derniere_publication` est une date 'AAAA-MM-JJ' (ou None).

    On publie dès que l'heure prévue est atteinte, et que rien n'a encore été
    publié aujourd'hui. Le retard est toléré pendant `fenetre_minutes` : si
    Render dormait à 09:00, le ping de 09:20 rattrape le post.

    Passé cette fenêtre, on renonce pour la journée. Sans cette borne, régler
    l'heure à 16:36 alors qu'il est 16:32 et que la config valait encore 09:00
    déclencherait un post immédiat « en retard de 7 h », qui consommerait le
    quota du jour et empêcherait la publication réellement voulue.

    `fenetre_minutes=0` supprime la borne (rattrapage illimité).
    """
    try:
        heures, minutes = (int(part) for part in heure.split(":", 1))
    except (ValueError, AttributeError):
        heures, minutes = 9, 0

    aujourdhui = maintenant.strftime("%Y-%m-%d")
    if derniere_publication == aujourdhui:
        return False

    retard = (maintenant.hour * 60 + maintenant.minute) - (heures * 60 + minutes)
    if retard < 0:
        return False
    return fenetre_minutes <= 0 or retard <= fenetre_minutes


async def boucle_planning(
    publier: Callable[[], Awaitable[str]],
    arrete: Callable[[], bool],
    intervalle: int = INTERVALLE_DEFAUT,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Vérifie l'heure en boucle et publie le moment venu.

    Complète `/tick` sans le remplacer : sur Render le service s'endort et
    seul le cron externe peut le réveiller, mais partout ailleurs (local, ou
    un hébergement qui ne dort pas) cette boucle suffit à ce que
    `/config heure` fonctionne sans dépendance extérieure.

    `publier` est idempotente (voir `doit_publier`) : l'appeler à chaque tour
    ne produit qu'une publication par jour, et le cron et la boucle peuvent
    coexister sans doubler les posts.

    Une exception est journalisée puis avalée : un incident réseau un jour ne
    doit pas empêcher la publication du lendemain.
    """
    precedent = None
    while not arrete():
        try:
            resultat = await publier()
        except Exception:
            log.exception("Échec de la publication planifiée")
        else:
            # On ne journalise qu'un changement d'état : sinon un « aucun salon
            # configuré » reviendrait chaque minute et noierait le log.
            if resultat and resultat != "rien à faire" and resultat != precedent:
                log.info("Planning : %s", resultat)
            precedent = resultat
        await dormir(intervalle)
