"""Le tableau des frais par filiale, en un embed Discord.

Un seul embed, pas un par filiale comme pour les promotions : le tableau se lit
d'un coup d'œil, et son intérêt est le **total** — ce qu'on va payer. Il n'y a
donc pas de template Discohook ici ; il n'y aurait rien à personnaliser sans
casser la lecture en colonnes.

Chaque montant apparaît sous ses deux formes : la courte pour lire, la longue
pour recopier dans le jeu — on ne paie pas « 189.70 TØ ». La longue est en
`code inline` : un appui long dans Discord la copie seule, alors qu'il faudrait
sinon sélectionner vingt-un chiffres à la main.

Les emojis y disent un **état** — poste principal, filiale en perte, relevé
périmé — et jamais rien d'autre : dans une liste de vingt lignes, un
pictogramme décoratif ferait le même bruit sur chacune.
"""

from __future__ import annotations

from datetime import date as Date

from src.filiales import Filiale, total_frais
from src.money import format_money, format_money_long

#: Bleu Discord, comme les autres embeds du bot.
COULEUR = 0x5865F2

#: Le poste le plus lourd, celui qu'on regarde en premier.
EMOJI_TETE = "🥇"
#: Les autres filiales qui paient.
EMOJI_PAYANTE = "▫️"
#: Rien à payer, et ce n'est pas une saisie oubliée.
EMOJI_PERTE = "🔻"
#: Relevé qui ne date pas d'aujourd'hui : à ressaisir.
EMOJI_PERIME = "⏳"
#: Le total, en pied de tableau.
EMOJI_TOTAL = "🧾"
#: Aucune filiale : le tableau est vide, le bot n'est pas en panne.
EMOJI_VIDE = "📭"

#: Nombre de filiales listées, quand la place le permet. Ce plafond ne suffit
#: pas seul : un nom est libre, un montant monte à 21 chiffres et un relevé
#: oublié porte sa date, si bien que quarante lignes peuvent dépasser les 4096
#: caractères d'une description — auquel cas Discord refuse le post **entier**.
#: `BUDGET_DESCRIPTION` tranche donc en dernier ressort.
LIMITE_LIGNES = 40

#: Place tenue par la description, en unités UTF-16.
#:
#: Discord plafonne à 4096 et compte en UTF-16, où un emoji hors du BMP pèse
#: deux : `len()` de Python en verrait un et laisserait passer un embed refusé.
#: La marge couvre la ligne « +N non affichées », ajoutée après la mesure.
BUDGET_DESCRIPTION = 3900

#: Les jours et les mois en français.
#:
#: `strftime('%A')` suit la locale du processus, donc « Tuesday » sur Render où
#: rien ne garantit `fr_FR` — et `locale.setlocale` est un réglage global, qui
#: déborderait sur le reste du bot. Une table est plus longue mais sûre.
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


def _longueur(texte: str) -> int:
    """La longueur telle que Discord la compte : en unités UTF-16."""
    return len(texte.encode("utf-16-le")) // 2


def _jour(iso: str) -> Date | None:
    """La date d'un relevé, ou None si elle est illisible.

    Une date qu'on ne sait pas lire ne doit pas faire échouer le post entier :
    un relevé écrit par une version antérieure vaut mieux affiché brut que pas
    affiché du tout.
    """
    try:
        return Date.fromisoformat(iso)
    except ValueError:
        return None


def date_longue(iso: str) -> str:
    """« mardi 11 août 2026 » — l'en-tête du tableau.

    Sans zéro devant le jour : « 09 août » se lit comme une référence.
    """
    jour = _jour(iso)
    if jour is None:
        return iso
    return f"{JOURS[jour.weekday()]} {jour.day} {MOIS[jour.month - 1]} {jour.year}"


def date_courte(iso: str) -> str:
    """« 9 août » — pour dater un relevé oublié en bout de ligne.

    Ni année ni jour de la semaine : ils n'apprendraient rien sur un relevé de
    la semaine, et allongeraient une ligne déjà chargée.
    """
    jour = _jour(iso)
    if jour is None:
        return iso
    return f"{jour.day} {MOIS[jour.month - 1]}"


def lignes_tableau(filiales: list[Filiale], aujourdhui: str = "") -> list[str]:
    """Une ligne par filiale, des frais les plus lourds aux plus légers.

    Le plus gros poste en tête : c'est celui qu'on regarde.

    Une filiale dont le relevé ne date pas d'aujourd'hui porte sa date. Les
    dater toutes serait du bruit ; l'intérêt est de repérer celles qu'on a
    oublié de mettre à jour.
    """
    classees = sorted(filiales, key=lambda f: (-f.frais, f.nom.casefold()))

    lignes = []
    for rang, filiale in enumerate(classees):
        perime = bool(aujourdhui) and filiale.date != aujourdhui

        if filiale.en_perte:
            # Signalée, et non silencieusement à 0 Ø : sans la marque, on
            # croirait à une saisie oubliée.
            marque = EMOJI_PERTE
            details = "*en perte, rien à payer*"
        else:
            # La tête de liste porte sa propre marque : le poste principal doit
            # se voir sans comparer les montants soi-même.
            marque = EMOJI_TETE if rang == 0 else EMOJI_PAYANTE
            # Le montant long en `code` : copiable d'un appui long dans Discord.
            # Et non en `-#`, qui ne réduit le texte qu'en **début** de ligne et
            # s'afficherait tel quel au milieu.
            details = (
                f"`{format_money_long(filiale.frais)}`"
                f" · {format_money(filiale.frais)}"
            )
        if perime:
            # L'emoji plutôt que la date seule : en bout d'une liste de vingt
            # lignes, « 9 août » se remarque mal.
            marque = EMOJI_PERIME

        ligne = f"{marque} **{filiale.nom}** — {details}"
        if perime:
            ligne += f" · relevé du {date_courte(filiale.date)}"
        lignes.append(ligne)
    return lignes


def embed_filiales(filiales: list[Filiale], date: str):
    """Embed du tableau du jour.

    `date` est la date locale du post ('AAAA-MM-JJ'), pas celle des relevés.
    """
    import discord

    total = total_frais(filiales)
    jour = date_longue(date)

    embed = discord.Embed(
        title="🏢 Frais de gestion des filiales",
        color=COULEUR,
    )

    if not filiales:
        # Un embed vide se lirait comme une panne : on dit ce qui manque et la
        # commande qui y remédie.
        embed.description = (
            f"{EMOJI_VIDE} *Aucune filiale enregistrée.*\n"
            "-# `/filiales releve` pour en ajouter une."
        )
        embed.set_footer(text=jour)
        return embed

    lignes = lignes_tableau(filiales, aujourdhui=date)

    # Autant de lignes que la place en autorise, les plus lourdes d'abord —
    # elles sont déjà en tête. Le plafond de lignes évite un mur de texte ; le
    # budget en caractères évite un post refusé.
    affichees: list[str] = []
    place = BUDGET_DESCRIPTION
    for ligne in lignes[:LIMITE_LIGNES]:
        cout = _longueur(ligne) + 1  # +1 : le saut de ligne qui la précède
        if cout > place:
            break
        affichees.append(ligne)
        place -= cout

    restantes = len(lignes) - len(affichees)
    if restantes:
        # Comptées, jamais tues : sinon le total se lirait comme ne portant que
        # sur ce qui est affiché.
        affichees.append(f"-# … +{restantes} filiale(s) non affichée(s)")

    embed.description = "\n".join(affichees)
    # Le total porte sur **toutes** les filiales, affichées ou non : c'est ce
    # qu'on paie, pas ce qui tient dans l'embed.
    combien = f"{len(filiales)} filiale{'s' if len(filiales) > 1 else ''}"
    embed.add_field(
        name=f"{EMOJI_TOTAL} Total · {combien}",
        value=f"`{format_money_long(total)}` · **{format_money(total)}**",
        inline=False,
    )
    embed.set_footer(text=jour)
    return embed
