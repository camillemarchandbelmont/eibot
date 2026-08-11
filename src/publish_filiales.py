"""Le tableau des frais par filiale, en un embed Discord.

Un seul embed, pas un par filiale comme pour les promotions : le tableau se lit
d'un coup d'œil, et son intérêt est le **total** — ce qu'on va payer. Il n'y a
donc pas de template Discohook ici ; il n'y aurait rien à personnaliser sans
casser la lecture en colonnes.

Chaque montant apparaît sous ses deux formes : la courte pour lire, la longue
pour recopier dans le jeu — on ne paie pas « 189.70 TØ ».
"""

from __future__ import annotations

from src.filiales import Filiale, total_frais
from src.money import format_money, format_money_long

#: Bleu Discord, comme les autres embeds du bot.
COULEUR = 0x5865F2

#: Nombre de filiales listées. La description d'un embed plafonne à 4096
#: caractères et une ligne peut dépasser 90 : au-delà, Discord refuserait le
#: post entier. Les filiales en trop sont **comptées** sous la liste, jamais
#: tues — sinon le total se lirait comme portant seulement sur ce qui est
#: affiché.
LIMITE_LIGNES = 40


def lignes_tableau(filiales: list[Filiale], aujourdhui: str = "") -> list[str]:
    """Une ligne par filiale, des frais les plus lourds aux plus légers.

    Le plus gros poste en tête : c'est celui qu'on regarde.

    Une filiale dont le relevé ne date pas d'aujourd'hui porte sa date. Les
    dater toutes serait du bruit ; l'intérêt est de repérer celles qu'on a
    oublié de mettre à jour.
    """
    classees = sorted(filiales, key=lambda f: (-f.frais, f.nom.casefold()))

    lignes = []
    for filiale in classees:
        if filiale.en_perte:
            # Signalée, et non silencieusement à 0 Ø : sans la marque, on
            # croirait à une saisie oubliée.
            details = "*en perte, rien à payer*"
        else:
            # Parenthèses et non `-#` : ce préfixe ne réduit le texte qu'en
            # **début** de ligne, et s'afficherait tel quel au milieu.
            details = (
                f"**{format_money(filiale.frais)}** "
                f"({format_money_long(filiale.frais)})"
            )
        ligne = f"• {filiale.nom} — {details}"
        if aujourdhui and filiale.date != aujourdhui:
            ligne += f" · saisie le {filiale.date}"
        lignes.append(ligne)
    return lignes


def embed_filiales(filiales: list[Filiale], date: str):
    """Embed du tableau du jour.

    `date` est la date locale du post ('AAAA-MM-JJ'), pas celle des relevés.
    """
    import discord

    total = total_frais(filiales)

    embed = discord.Embed(
        title="Frais de gestion des filiales",
        color=COULEUR,
    )

    if not filiales:
        # Un embed vide se lirait comme une panne : on dit ce qui manque et la
        # commande qui y remédie.
        embed.description = (
            "*Aucune filiale enregistrée.*\n"
            "-# `/frais montant:… filiale:…` pour en ajouter une."
        )
        embed.set_footer(text=date)
        return embed

    lignes = lignes_tableau(filiales, aujourdhui=date)
    affichees = lignes[:LIMITE_LIGNES]
    restantes = len(lignes) - len(affichees)
    if restantes:
        affichees.append(f"-# … +{restantes} filiale(s) non affichée(s)")

    embed.description = "\n".join(affichees)
    # Le total porte sur **toutes** les filiales, affichées ou non : c'est ce
    # qu'on paie, pas ce qui tient dans l'embed.
    embed.add_field(
        name=f"Total ({len(filiales)} filiale{'s' if len(filiales) > 1 else ''})",
        value=f"**{format_money(total)}**\n-# {format_money_long(total)}",
        inline=False,
    )
    embed.set_footer(text=date)
    return embed
