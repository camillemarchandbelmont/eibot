"""`/reglages modules` : chaque serveur allume ce qu'il veut.

Le stockage est en place (`tests/test_modules_par_serveur.py`) et la tournée
saute déjà les modules éteints. Il manque la porte : sans commande, la seule
façon d'éteindre un module serait d'écrire dans la base à la main.

Trois commandes, et un refus. `liste` montre **tous** les modules trouvés dans le
dossier avec leur état dans ce serveur — y compris ceux qui ont refusé de se
charger, sans quoi un module absent se lirait comme un module jamais déployé.
`activer` et `desactiver` font le reste, et le bot refuse d'éteindre le dernier :
un serveur sans aucun module ressemblerait trait pour trait à une panne du bot.

Le serveur où la commande est tapée est le seul concerné. La même assertion
partout : ce qui est éteint ici reste allumé chez le voisin, qui a ses propres
entreprises à publier.
"""

from tests.test_commandes_fourchettes import _bot, _commande
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction, _propositions

#: Les modules réels du dossier, ceux que le plan demande de citer. Nommés ici
#: pour que l'ajout d'un module casse ce fichier plutôt que de laisser une
#: assertion muette sur ce qui compte. `politesse` est le module d'épreuve,
#: jetable : il s'en va avec son fichier.
LES_MODULES = ("conversion", "promos", "filiales", "politesse")


def _lignes(embed) -> str:
    """Tout le texte de l'embed, champs compris.

    La liste tient dans un champ aujourd'hui ; l'assertion ne doit pas dépendre
    de ce découpage, qui n'est qu'une question de place à l'écran.
    """
    return " ".join(
        [embed.title or "", embed.description or ""]
        + [f"{champ.name} {champ.value}" for champ in embed.fields]
    )


# --- /reglages modules liste -------------------------------------------------


async def test_la_liste_cite_tous_les_modules_du_dossier():
    """L'épreuve du plan. Un module trouvé mais absent de la liste ne pourrait
    ni s'allumer ni s'éteindre, et rien ne dirait qu'il est là."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules liste").callback(interaction)

    rendu = _lignes(interaction.embeds[0])
    for nom in LES_MODULES:
        assert nom in rendu, rendu


async def test_la_liste_dit_lequel_est_eteint_dans_ce_serveur():
    """Sans l'état, la liste ne répondrait pas à la seule question qu'on lui
    pose : est-ce que le tableau du soir va sortir ce soir ?"""
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("filiales")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules liste").callback(interaction)

    lignes = _lignes(interaction.embeds[0]).splitlines()
    eteinte = next(ligne for ligne in lignes if "filiales" in ligne)
    allumee = next(ligne for ligne in lignes if "promos" in ligne)
    assert "éteint" in eteinte.casefold()
    assert "éteint" not in allumee.casefold()


async def test_la_liste_ignore_les_extinctions_du_voisin():
    """Montrer l'état d'un autre serveur ferait chercher ici la panne d'ailleurs.
    """
    bot = await _bot()
    await bot.store.pour(VOISIN).eteindre_module("filiales")

    interaction = _interaction(EMPIRE)
    await _commande(bot, "reglages modules liste").callback(interaction)

    assert "éteint" not in _lignes(interaction.embeds[0]).casefold()


async def test_la_liste_nomme_les_modules_refuses():
    """Un module cassé est écarté au démarrage, pas fatal — mais il doit se voir.

    Absent de la liste sans un mot, il se lirait comme un module jamais déployé,
    et on chercherait la panne dans le dépôt plutôt que dans le fichier.
    """
    bot = await _bot()
    bot.modules_refuses = {"courtoisie": "ImportError : pas de module nommé pandas"}
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules liste").callback(interaction)

    rendu = _lignes(interaction.embeds[0])
    assert "courtoisie" in rendu
    assert "ImportError" in rendu, rendu


# --- /reglages modules desactiver -------------------------------------------


async def test_desactiver_eteint_le_module_dans_ce_serveur_seulement():
    """Tout l'intérêt de l'étape : une entreprise sans tableau des frais, sans
    l'enlever aux autres."""
    bot = await _bot()

    await _commande(bot, "reglages modules desactiver").callback(
        _interaction(EMPIRE), module="filiales"
    )

    assert await bot.store.pour(EMPIRE).module_actif("filiales") is False
    assert await bot.store.pour(VOISIN).module_actif("filiales") is True
    assert await bot.store.module_actif("filiales") is True


async def test_desactiver_previent_que_la_publication_sarrete():
    """C'est la conséquence qu'on ne voit pas en tapant : le post du soir
    disparaît. Un « ✅ » sec laisserait l'attendre."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules desactiver").callback(
        interaction, module="filiales"
    )

    texte = " ".join(interaction.textes)
    assert "✅" in texte
    assert "publi" in texte.casefold(), texte


async def test_desactiver_un_module_deja_eteint_le_dit():
    """Un « ✅ » ferait croire qu'on vient de changer quelque chose, et chercher
    ailleurs la raison d'un post qui sort encore."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("filiales")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules desactiver").callback(
        interaction, module="filiales"
    )

    texte = " ".join(interaction.textes)
    assert "ℹ️" in texte and "déjà" in texte


async def test_desactiver_refuse_deteindre_le_dernier_module():
    """Le refus demandé par le plan : un serveur muet ressemble à une panne.

    Le bot répondrait aux commandes de `/reglages`, mais plus rien d'autre — et
    rien à l'écran ne dirait que c'est un réglage et non un incident.
    """
    bot = await _bot()
    magasin = bot.store.pour(EMPIRE)
    for nom in LES_MODULES:
        if nom != "filiales":
            await magasin.eteindre_module(nom)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules desactiver").callback(
        interaction, module="filiales"
    )

    assert "❌" in " ".join(interaction.textes)
    assert await magasin.module_actif("filiales") is True


async def test_desactiver_un_module_inconnu_refuse_en_listant_les_noms():
    """Un nom mal tapé écrirait sinon un module qui n'existe pas dans la liste
    des éteints, où il resterait sans que rien ne le rallume.

    La faute de frappe ne contient aucun nom réel : « filialess » aurait rendu
    l'assertion vraie sans que le refus cite quoi que ce soit.
    """
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules desactiver").callback(
        interaction, module="filaiales"
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    for nom in LES_MODULES:
        assert nom in texte, texte
    assert await bot.store.pour(EMPIRE).modules_eteints() == []


# --- /reglages modules activer ----------------------------------------------


async def test_activer_rallume_le_module_dans_ce_serveur():
    bot = await _bot()
    magasin = bot.store.pour(EMPIRE)
    await magasin.eteindre_module("filiales")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules activer").callback(
        interaction, module="filiales"
    )

    assert "✅" in " ".join(interaction.textes)
    assert await magasin.module_actif("filiales") is True


async def test_activer_un_module_deja_allume_le_dit():
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules activer").callback(
        interaction, module="filiales"
    )

    texte = " ".join(interaction.textes)
    assert "ℹ️" in texte and "déjà" in texte


async def test_activer_un_module_inconnu_refuse():
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages modules activer").callback(
        interaction, module="courtoisie"
    )

    assert "❌" in " ".join(interaction.textes)


# --- Les propositions : celles que la commande accepte ----------------------


async def test_desactiver_ne_propose_que_les_modules_allumes():
    """Proposer un module déjà éteint ferait choisir un nom pour s'entendre
    répondre qu'il n'y avait rien à faire."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("filiales")
    commande = _commande(bot, "reglages modules desactiver")

    choix = await _propositions(commande, "module")(_interaction(EMPIRE), "")

    assert [c.value for c in choix] == ["conversion", "promos", "politesse"]


async def test_activer_ne_propose_que_les_modules_eteints():
    bot = await _bot()
    await bot.store.pour(EMPIRE).eteindre_module("filiales")
    commande = _commande(bot, "reglages modules activer")

    choix = await _propositions(commande, "module")(_interaction(EMPIRE), "")

    assert [c.value for c in choix] == ["filiales"]


async def test_les_propositions_sont_celles_de_ce_serveur():
    """Lues dans la configuration commune, elles proposeraient à une entreprise
    de rallumer ce que l'autre a éteint."""
    bot = await _bot()
    await bot.store.pour(VOISIN).eteindre_module("filiales")
    commande = _commande(bot, "reglages modules activer")

    choix = await _propositions(commande, "module")(_interaction(EMPIRE), "")

    assert choix == []
