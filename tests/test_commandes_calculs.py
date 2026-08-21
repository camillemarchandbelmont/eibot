"""Tests des deux calculatrices, `/convertir montant` et `/convertir frais`.

Rassemblées sous un seul mot, et non deux commandes à la racine : elles font la
même chose — rendre un montant à partir d'un autre — et le nom `/frais` va au
tableau des frais, qui est un sujet et non un calcul. Deux « frais » à la racine
pour deux choses différentes, c'est le désordre qu'on vient de défaire.

Ces commandes n'écrivent rien : leur seule sortie est le message. Ce qui peut
donc mal tourner, c'est le message lui-même — un montant mal formaté, un symbole
refusé sans dire lesquels sont valides, ou une réponse publique là où elle
devrait rester privée.

Le calcul lui-même est couvert par `tests/test_money.py` ; on vérifie ici que la
commande appelle bien ce calcul et rend son résultat lisible.
"""

import pytest

from src.money import ECHELLE
from tests.test_commandes_fourchettes import InteractionFactice, _bot, _commande


# --- Les deux sous un seul mot ----------------------------------------------


async def test_les_deux_calculatrices_sont_sous_un_seul_mot():
    """À la racine, « frais » est le tableau et non un calcul.

    Les deux calculatrices rendent un montant à partir d'un autre : un seul mot
    suffit pour les deux. C'est ce qui libère `frais` pour le tableau, qui est un
    sujet entier — et deux `frais` à la racine, l'un calculant, l'autre listant,
    reproduiraient dans le nouveau menu ce qu'on reprochait à l'ancien.
    """
    bot = await _bot()

    sous_convertir = {
        commande.name for commande in _commande(bot, "convertir").walk_commands()
    }

    assert sous_convertir == {"montant", "frais"}
    # Le `frais` de la racine est un groupe, celui du tableau : une calculatrice
    # nue y répondrait au même mot sans rien lister.
    assert _commande(bot, "frais").name == "frais"
    assert "frais liste" in {
        commande.qualified_name for commande in bot.tree.walk_commands()
    }


# --- /convertir montant -----------------------------------------------------


async def test_convertir_affiche_le_montant_dans_le_palier_demande():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir montant").callback(interaction, montant="1P", vers="T")

    texte = " ".join(interaction.textes)
    assert "1 000.00" in texte.replace(" ", " ")
    assert "TØ" in texte


async def test_convertir_rappelle_le_montant_de_depart():
    """Sans lui, on ne peut pas vérifier que `50 6P` a été lu comme 506 PØ —
    c'est justement la saisie tolérante qui rend ce rappel nécessaire."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir montant").callback(interaction, montant="50 6P", vers="T")

    texte = " ".join(interaction.textes).replace(" ", " ")
    assert "506.00 PØ" in texte
    assert "506 000.00 TØ" in texte


async def test_convertir_montant_illisible_refuse_avec_l_aide():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir montant").callback(interaction, montant="beaucoup", vers="T")

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "12.25M" in texte  # l'aide sur les formats


async def test_convertir_symbole_inconnu_liste_les_valides():
    """`B` n'existe pas dans ce jeu et rien dans Discord ne dit lesquels si."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir montant").callback(interaction, montant="1P", vers="B")

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "billion" in texte


async def test_convertir_reste_prive():
    """Un calcul personnel n'a pas à encombrer le salon."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir montant").callback(interaction, montant="1P", vers="T")

    assert all(
        message.get("ephemeral") for message in interaction.response.messages
    )


async def test_convertir_propose_tous_les_paliers_en_menu():
    """Les symboles ne suivent pas les préfixes SI (`G` est un milliard, `E`
    vaut 10^18) : personne ne les tape de mémoire, d'où le menu déroulant.

    Les 15 paliers de la table, plus l'unité que le jeu affiche en dur — soit
    16, sous le plafond de 25 choix de Discord.
    """
    bot = await _bot()
    parametre = next(
        p for p in _commande(bot, "convertir montant").parameters if p.name == "vers"
    )

    valeurs = [choix.value for choix in parametre.choices]
    assert valeurs == ["Ø", *[symbole for _, symbole in reversed(ECHELLE)]]
    # Le nom affiché lève l'ambiguïté que le symbole seul laisse entière.
    assert any("milliard" in choix.name for choix in parametre.choices)


# --- /convertir frais -------------------------------------------------------


async def test_frais_affiche_le_montant_sans_decimales():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir frais").callback(interaction, montant="2,71P")

    texte = " ".join(interaction.textes).replace(" ", " ")
    # 7 % de 2 710 000 000 000 000 = 189 700 000 000 000
    assert "189.70 TØ" in texte


async def test_frais_donne_tous_les_chiffres():
    """Le montant exact est ce qu'on recopie dans le jeu ; la notation courte
    (`189.70 TØ`) ne suffit pas pour payer."""
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir frais").callback(interaction, montant="2 710 572 934 559 948")

    texte = " ".join(interaction.textes).replace(" ", " ")
    assert "189 740 105 419 196" in texte


async def test_frais_rappelle_le_taux():
    """Pour qu'on sache sur quoi le calcul repose sans ouvrir le code.

    Le taux est cherché sous sa forme « 7 % » et non comme un simple `7` : les
    montants du message en contiennent déjà, et l'assertion passerait même si le
    taux avait disparu.
    """
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir frais").callback(interaction, montant="1000")

    assert "7 %" in " ".join(interaction.textes)


async def test_frais_rappelle_le_montant_de_depart():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir frais").callback(interaction, montant="1P")

    texte = " ".join(interaction.textes).replace(" ", " ")
    assert "1.00 PØ" in texte


async def test_frais_montant_illisible_refuse_avec_l_aide():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir frais").callback(interaction, montant="beaucoup")

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "12.25M" in texte


async def test_frais_reste_prive():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "convertir frais").callback(interaction, montant="1P")

    assert all(
        message.get("ephemeral") for message in interaction.response.messages
    )
