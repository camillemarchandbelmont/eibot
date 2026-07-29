"""Salon de logs : le bot raconte dans Discord ce qu'il fait.

Sans ça, savoir pourquoi le post de 09:00 n'est pas sorti demande d'aller
lire `bot.log` sur Render. Le journal rend visibles les publications et les
pannes là où on les remarque : dans Discord.

Règle absolue de ce module : **il n'échoue jamais**. Un salon de logs
supprimé, renommé ou sans permissions ne doit pas casser la publication qu'il
est censé raconter — d'où les `except Exception` volontairement larges.

Il ne fabrique aucun message : il relaie ce qu'on lui donne. C'est ce qui
garantit qu'aucune clé d'API n'y transite, les messages d'erreur étant déjà
assainis par `source.SourceError`.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: Limite Discord pour le contenu d'un message.
MAX_CARACTERES = 2000


def _tronquer(texte: str) -> str:
    if len(texte) <= MAX_CARACTERES:
        return texte
    return texte[: MAX_CARACTERES - 1] + "…"


class Journal:
    """Écrit dans le salon de logs, si un salon est configuré."""

    def __init__(self, bot, store):
        self.bot = bot
        self.store = store

    async def _envoyer(self, contenu: str) -> None:
        """Tente l'envoi ; toute panne reste dans le log fichier.

        Ne journalise jamais son propre échec dans Discord : ce serait une
        cascade (échec d'écriture -> log -> échec d'écriture -> …).
        """
        try:
            salon_id = await self.store.salon_logs()
            if not salon_id:
                return
            salon = self.bot.get_channel(int(salon_id))
            if salon is None:
                salon = await self.bot.fetch_channel(int(salon_id))
            await salon.send(_tronquer(contenu))
        except Exception:
            log.warning(
                "Journal Discord indisponible (salon de logs inaccessible ?).",
                exc_info=True,
            )

    async def publication(
        self, promos: int, reussis: list[str], echecs: dict[str, str]
    ) -> None:
        """Compte rendu d'une publication quotidienne.

        `reussis` et `echecs` contiennent des salons déjà formatés (`<#id>` ou
        `#nom`) : le journal ne résout pas les salons lui-même.
        """
        if promos:
            sujet = f"{promos} promotion" + ("s" if promos > 1 else "")
        else:
            sujet = "aucune promotion"

        total = len(reussis) + len(echecs)

        if reussis and not echecs:
            entete = f"✅ **Publication** · {sujet} · {len(reussis)}/{total} salon"
            entete += "s" if len(reussis) > 1 else ""
            lignes = [f"{entete} : {', '.join(reussis)}"]
        elif reussis:
            lignes = [
                f"⚠️ **Publication partielle** · {sujet} · "
                f"{len(reussis)}/{total} salons : {', '.join(reussis)}"
            ]
        else:
            lignes = [f"❌ **Publication échouée** · {sujet}"]

        lignes += [f"-# ↳ {salon} : {raison}" for salon, raison in echecs.items()]
        await self._envoyer("\n".join(lignes))

    async def erreur(self, message: str) -> None:
        """Signale une panne. Le message doit déjà être assaini."""
        await self._envoyer(f"❌ **Échec** · {message}")
