"""Deux calculatrices : convertir un montant, et en prendre 7 %.

Aucune publication, aucune donnée : ce module ne lit ni n'écrit rien. C'est le
cas le plus simple du contrat, et à ce titre le meilleur exemple de ce qu'un
module peut être — un fichier, deux commandes, rien à ranger.

`/frais` ne s'occupe **que** du calcul. Elle a longtemps enregistré aussi, selon
qu'une case facultative était remplie : rien dans son nom ne prévenait celui qui
la tapait, et la moitié qui écrivait en base était invisible jusqu'au tableau du
soir. La saisie d'un relevé est désormais `/filiales releve`, chez le module qui
tient le tableau — c'est-à-dire chez celui qui possède les données.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from src.commandes import aide_montants, choix_symboles
from src.modules import Module
from src.money import (
    TAUX_GESTION,
    MoneyError,
    convertir,
    format_money,
    format_money_long,
    frais_de_gestion,
    parse_money,
)


def enregistrer(bot: Any) -> None:
    """Greffe `/convertir` et `/frais` sur l'arbre du bot.

    Deux commandes à la racine et non un groupe : elles ne partagent ni données
    ni réglages, et `/conversion frais` ferait taper deux mots pour un calcul de
    trois secondes.
    """
    tree = bot.tree

    @tree.command(
        name="convertir",
        description="Exprime un montant dans un autre palier (P → T, Z → M…)",
    )
    @app_commands.describe(
        montant="Montant de départ (ex: 2,71P, 50 6P, 840)",
        vers="Palier d'arrivée",
    )
    @app_commands.choices(vers=choix_symboles())
    async def convertir_commande(
        interaction: discord.Interaction, montant: str, vers: str
    ) -> None:
        try:
            valeur = parse_money(montant)
        except MoneyError as erreur:
            await interaction.response.send_message(
                f"❌ {erreur}\n{aide_montants()}", ephemeral=True
            )
            return

        try:
            rendu = convertir(valeur, vers)
        except MoneyError as erreur:
            await interaction.response.send_message(f"❌ {erreur}", ephemeral=True)
            return

        # Le montant de départ est rappelé sous sa forme comprise : c'est le
        # seul moyen de vérifier que `50 6P` a bien été lu comme 506 PØ.
        await interaction.response.send_message(
            f"**{format_money(valeur)}** = **{rendu}**\n"
            f"-# {format_money_long(valeur)}",
            ephemeral=True,
        )

    @tree.command(
        name="frais",
        description="Frais de gestion sur un montant (7 %, sans décimales)",
    )
    @app_commands.describe(montant="Montant sur lequel calculer (ex: 2,71P, 100T)")
    async def frais_commande(
        interaction: discord.Interaction, montant: str
    ) -> None:
        """Le calcul, et rien d'autre : elle ne laisse rien derrière elle.

        Pas de case `filiale` ici, même facultative : tant qu'elle existe, elle
        est proposée dans le menu, et celui qui la remplit attend un
        enregistrement. C'est `/filiales releve` qui écrit.
        """
        try:
            valeur = parse_money(montant)
        except MoneyError as erreur:
            await interaction.response.send_message(
                f"❌ {erreur}\n{aide_montants()}", ephemeral=True
            )
            return

        frais = frais_de_gestion(valeur)
        # Les deux formes : la courte pour lire, la longue pour recopier dans le
        # jeu — on ne paie pas « 189,70 TØ ».
        await interaction.response.send_message(
            f"Frais de gestion sur **{format_money(valeur)}** "
            f"({TAUX_GESTION.normalize():f} %) :\n"
            f"**{format_money(frais)}**\n"
            f"-# {format_money_long(frais)}",
            ephemeral=True,
        )


MODULE = Module(
    nom="conversion",
    titre="Conversion",
    description="Convertit un montant d'un palier à l'autre, et calcule les frais de gestion.",
    # Devant les autres : ce sont les commandes les plus tapées, et les seules
    # qui ne demandent aucun réglage préalable.
    ordre=10,
    enregistrer=enregistrer,
)
