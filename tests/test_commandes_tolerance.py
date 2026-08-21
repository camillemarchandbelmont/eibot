"""Tests de `/promos tolerance`, sans se connecter à Discord.

Ce qui se joue ici est le **message** : la zone est invisible dans Discord une
fois réglée (les posts ne la mentionnent pas), donc la confirmation de la
commande et `/promos liste` sont les deux seuls endroits où l'utilisateur
peut vérifier ce qu'il a saisi. Une commande qui écrit bien mais confirme mal
serait indétectable jusqu'au matin où un bâtiment inattendu apparaîtrait.
"""

from decimal import Decimal

import pytest

from tests.test_commandes_fourchettes import (
    InteractionFactice,
    _bot,
    _commande,
    _magasin,
)


# --- Réglage ----------------------------------------------------------------


async def test_regler_confirme_avec_les_bornes_formatees():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos tolerance").callback(
        interaction, fourchette="grosses", min="50T", max="8P"
    )

    texte = " ".join(interaction.textes)
    assert "grosses" in texte
    assert "50.00" in texte and "8.00" in texte

    fourchette = (await _magasin(bot).fourchettes())[0]
    assert Decimal(fourchette["tolere_min"]) == Decimal("5e13")
    assert Decimal(fourchette["tolere_max"]) == Decimal("8e15")


async def test_regler_montant_illisible_refuse_sans_rien_ecrire():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos tolerance").callback(
        interaction, fourchette="grosses", min="beaucoup", max="8P"
    )

    assert "❌" in " ".join(interaction.textes)
    assert (await _magasin(bot).fourchettes())[0]["tolere_min"] == ""


async def test_regler_sur_fourchette_inconnue_refuse_explicitement():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "promos tolerance").callback(
        interaction, fourchette="fantome", min="50T", max="8P"
    )

    assert "❌" in " ".join(interaction.textes)


async def test_zone_plus_etroite_refusee_avec_la_raison():
    """Le refus doit nommer la fourchette et ses bornes.

    Sans elles, le message serait une énigme : c'est justement quand on s'est
    trompé de bornes qu'on a besoin de lire lesquelles sont attendues.
    """
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos tolerance").callback(
        interaction, fourchette="grosses", min="200T", max="1P"
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte and "plus large" in texte
    assert (await _magasin(bot).fourchettes())[0]["tolere_min"] == ""


# --- Effacement -------------------------------------------------------------


async def test_sans_bornes_efface_la_zone():
    """La forme nue de la commande : `/promos tolerance fourchette:grosses`."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await _magasin(bot).majtolerance_fourchette(
        "grosses", Decimal("5e13"), Decimal("8e15")
    )
    interaction = InteractionFactice()

    await _commande(bot, "promos tolerance").callback(interaction, fourchette="grosses")

    assert "✅" in " ".join(interaction.textes)
    assert (await _magasin(bot).fourchettes())[0]["tolere_min"] == ""


async def test_effacer_une_zone_absente_le_dit():
    """Confirmer un effacement imaginaire ferait croire qu'une zone existait."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos tolerance").callback(interaction, fourchette="grosses")

    assert "ℹ️" in " ".join(interaction.textes)


async def test_une_seule_borne_refusee():
    """Une zone à moitié réglée serait ignorée par `find_promos` : autant le
    dire tout de suite plutôt que de confirmer un réglage sans effet."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos tolerance").callback(
        interaction, fourchette="grosses", min="50T"
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert (await _magasin(bot).fourchettes())[0]["tolere_min"] == ""


# --- Visibilité -------------------------------------------------------------


async def test_la_liste_montre_la_zone():
    """Seul endroit où relire la zone : les posts ne la mentionnent pas."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await _magasin(bot).majtolerance_fourchette(
        "grosses", Decimal("5e13"), Decimal("8e15")
    )
    interaction = InteractionFactice()

    await _commande(bot, "promos liste").callback(interaction)

    description = interaction.embeds[0].description
    assert "50.00" in description and "8.00" in description


async def test_la_liste_reste_sobre_sans_zone():
    """Sans zone réglée, aucune ligne à son sujet : la liste doit rester lisible
    quand on a dix fourchettes."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos liste").callback(interaction)

    assert "tolérance" not in interaction.embeds[0].description.lower()


async def test_prix_signale_la_zone_repoussee():
    """Élargir les bornes déplace la zone : le taire laisserait croire qu'elle
    est restée là où on l'avait mise."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await _magasin(bot).majtolerance_fourchette(
        "grosses", Decimal("5e13"), Decimal("8e15")
    )
    interaction = InteractionFactice()

    await _commande(bot, "promos prix").callback(
        interaction, fourchette="grosses", min="10T", max="9P"
    )

    texte = " ".join(interaction.textes)
    assert "tolérance" in texte.lower()


async def test_prix_ne_parle_pas_de_zone_quand_elle_ne_bouge_pas():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await _magasin(bot).majtolerance_fourchette(
        "grosses", Decimal("5e13"), Decimal("8e15")
    )
    interaction = InteractionFactice()

    await _commande(bot, "promos prix").callback(
        interaction, fourchette="grosses", min="200T", max="5P"
    )

    assert "tolérance" not in " ".join(interaction.textes).lower()
