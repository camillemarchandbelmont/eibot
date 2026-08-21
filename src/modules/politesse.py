"""Un bonjour le matin, un bonsoir le soir. Module **jetable**.

Il n'a aucune utilité dans le jeu : c'est l'épreuve finale du contrat de module,
telle que le plan la demande. Deux publications dans un seul fichier, sans toucher
à quoi que ce soit d'autre — ni au bot, ni au moteur d'envoi, ni au menu.

Ce qu'il éprouve, et qu'aucun module historique ne peut éprouver :

- **Deux publications pour un module.** Les trois autres en déclarent zéro ou une.
- **Le tiroir générique, seul.** Ce fichier ne déclare aucun accès à l'heure, aux
  salons ni à la trace de passage : tout va dans `publication:<clé>:…`, cloisonné
  par serveur sans une ligne de plomberie. Les publications historiques, elles,
  fournissent leurs accès, parce que leurs données existaient avant les modules.
- **Deux commandes à la racine.** `/bonjour` et `/bonsoir`, dont le vocabulaire —
  `heure`, `apercu`, `publier`, `salon ajouter|retirer` — n'est pas réécrit ici.

À retirer par `git revert` une fois la vérification faite dans Discord. Le retirer
est d'ailleurs la dernière moitié de l'épreuve : un module s'en va comme il est
venu, en enlevant son fichier.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from src.commandes import ajouter_les_commandes_de_publication
from src.modules import Envoi, Module, Publication, Tournee
from src.tournee import salons_de


def _salutation(cle: str, mot: str, heure: str) -> Publication:
    """Une publication complète, sans un seul accès déclaré.

    La publication se referme sur elle-même : `preparer` lit ses salons par
    `salons_de`, qui a besoin de l'objet. Il n'existe pas encore quand la fonction
    est écrite, mais il existe quand elle est appelée — et c'est l'accès public
    qu'un module doit prendre, plutôt que de reconstruire la clé du tiroir à la
    main.
    """

    async def preparer(bot: Any, magasin: Any, maintenant: Any) -> Tournee:
        salons = await salons_de(publication, magasin)
        if not salons:
            # La raison, et non un envoi vide : elle remonte telle quelle dans le
            # compte rendu, et dit quoi taper pour que le post sorte.
            return Tournee(raison=f"aucun salon pour le {mot} (/{cle} salon ajouter)")

        embed = discord.Embed(
            title=f"{mot.capitalize()} !",
            description=f"Il est {maintenant.strftime('%H:%M')}.",
        )

        async def envoyer(cible: Any, ephemere: bool = False) -> None:
            # `ephemeral` seulement quand il est demandé : un salon ordinaire ne
            # connaît pas cet argument et refuserait l'envoi.
            options: dict[str, Any] = {"embed": embed}
            if ephemere:
                options["ephemeral"] = True
            await cible.send(**options)

        return Tournee(
            envois=(Envoi(etiquette=mot, salons=tuple(salons), envoyer=envoyer),)
        )

    publication = Publication(
        cle=cle, titre=f"le {mot}", preparer=preparer, heure_par_defaut=heure
    )
    return publication


MATIN = _salutation("bonjour", "bonjour", "08:00")
SOIR = _salutation("bonsoir", "bonsoir", "20:00")


def enregistrer(bot: Any) -> None:
    """Un groupe par publication, et pas une commande propre.

    Tout le vocabulaire vient de `ajouter_les_commandes_de_publication` : c'est la
    moitié du contrat qui fait qu'un post de plus coûte une déclaration. Un module
    qui devrait réécrire `heure` et `apercu` n'hériterait de rien.
    """
    for publication in (MATIN, SOIR):
        groupe = app_commands.Group(
            name=publication.cle,
            description=f"{publication.titre.capitalize()} du jour",
        )
        ajouter_les_commandes_de_publication(groupe, bot, publication)
        bot.tree.add_command(groupe)


MODULE = Module(
    nom="politesse",
    titre="Bonjour et bonsoir",
    description="Deux publications dans un seul fichier — module d'épreuve, jetable.",
    ordre=90,
    enregistrer=enregistrer,
    publications=(MATIN, SOIR),
)
