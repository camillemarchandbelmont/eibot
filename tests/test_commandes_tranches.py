"""`/promos plafond` avec des bornes : au plus tant de promotions entre tant et tant.

Le cœur sait couper par plage (`tests/test_tranches.py`) et la base sait retenir
les plages (`tests/test_tranches_par_fourchette.py`). Il manque la porte, et
surtout le branchement : une tranche enregistrée que la publication ne lit pas
serait le pire des cas — la commande confirme, et le post du soir sort inchangé.

Le même mot que le plafond de fourchette, avec deux bornes de plus : c'est le même
réglage à deux échelles, et un second mot obligerait à choisir lequel des deux on
veut avant même de savoir qu'ils existent. Sans bornes, `/promos plafond` continue
de plafonner la fourchette entière — les tests de `tests/test_commandes_plafond.py`
le figent, et rien ici ne doit le déranger.

D'où les effets vérifiés par façade : le post du soir, l'aperçu qui doit le montrer
tel quel, et `/promos chercher`. Cette dernière couvre l'union des fourchettes :
elle n'est tranchée que sur les plages réglées dans toutes, sinon elle cacherait
des promotions qu'une autre fourchette publie bel et bien.
"""

from decimal import Decimal

from src.bot import EmpireBot
from src.db import Store, plafond_fourchette, tranches_fourchette
from src.modules.promos import _preparer

from tests.test_commandes_fourchettes import _commande
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction, _propositions

#: Quatre promotions du même type, à quatre prix distincts, toutes dans la
#: fourchette montée ci-dessous. Deux par moitié de fourchette, pour qu'une tranche
#: sur le bas se voie sans toucher au haut.
CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Quatre cents",0,400,0,0,0,17,0,0,0
zones,"Trois cents",0,300,0,0,0,17,0,0,0
zones,"Deux cents",0,200,0,0,0,17,0,0,0
zones,"Cent",0,100,0,0,0,17,0,0,0
"""


class SourceFactice:
    async def fetch(self) -> str:
        return CSV


async def _bot() -> EmpireBot:
    store = Store(dsn="")
    await store.connect()
    return EmpireBot(store, SourceFactice())


def _magasin(bot: EmpireBot, serveur_id: int = EMPIRE):
    return bot.store.pour(serveur_id)


async def _avec_fourchette(bot: EmpireBot, serveur_id: int = EMPIRE):
    """Une fourchette qui contient les quatre promotions, et un salon.

    Le salon compte : sans lui la fourchette est écartée de la publication, et les
    tranches ne seraient éprouvées que sur une tournée vide.
    """
    magasin = _magasin(bot, serveur_id)
    await magasin.ajouter_fourchette("grosses", Decimal(1), Decimal(1000))
    await magasin.ajouter_salon_fourchette("grosses", "42")
    return magasin


async def _fourchette(magasin, nom: str = "grosses") -> dict:
    for fourchette in await magasin.fourchettes():
        if fourchette["nom"] == nom:
            return fourchette
    raise AssertionError(f"fourchette introuvable : {nom}")


async def _tranches_enregistrees(magasin, nom: str = "grosses") -> list:
    return tranches_fourchette(await _fourchette(magasin, nom))


def _titres(interaction) -> list[str]:
    """Titres des embeds envoyés, pour compter ce qui est réellement montré."""
    trouves = []
    for message in [*interaction.response.messages, *interaction.followup.messages]:
        for embed in message.get("embeds") or []:
            titre = getattr(embed, "title", None) or embed.to_dict().get("title")
            if titre:
                trouves.append(titre)
    return trouves


#: La plage basse de la fourchette : « Deux cents » et « Cent » y sont.
BAS = {"min": "1", "max": "250"}


# --- La commande dans le menu -----------------------------------------------


async def test_la_commande_accepte_des_bornes():
    """Les mêmes noms que partout ailleurs (`min`, `max`) : c'est ce que Discord
    montre, et un troisième vocabulaire pour désigner deux prix serait à
    apprendre pour rien."""
    bot = await _bot()

    parametres = _commande(bot, "promos plafond")._params

    assert "min" in parametres
    assert "max" in parametres


async def test_le_nom_de_fourchette_se_propose_toujours():
    """Ajouter des bornes ne doit pas coûter l'autocomplétion du nom."""
    bot = await _bot()

    commande = _commande(bot, "promos plafond")

    assert _propositions(commande, "fourchette") is not None


# --- Régler une tranche ------------------------------------------------------


async def test_regler_une_tranche_confirme_avec_le_nombre_et_les_bornes():
    """Les trois valeurs dans la réponse : c'est la seule preuve que ce sont bien
    celles-là qui ont été retenues, la commande n'ayant aucun autre écho."""
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=1, **BAS
    )

    texte = " ".join(interaction.textes)
    assert "✅" in texte
    assert "grosses" in texte
    assert "1" in texte
    # Les bornes reformatées dans la notation du jeu, comme partout ailleurs.
    assert "250" in texte


async def test_regler_une_tranche_lenregistre():
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=1, **BAS
    )

    assert await _tranches_enregistrees(magasin) == [
        (Decimal(1), Decimal(250), 1)
    ]


async def test_une_tranche_a_zero_est_refusee():
    """Interdire une plage n'est pas la plafonner : la fourchette a ses bornes
    pour ça, et le mot « plafond » ne l'annonce nulle part."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=0, **BAS
    )

    assert "❌" in " ".join(interaction.textes)
    assert await _tranches_enregistrees(magasin) == []


async def test_des_bornes_illisibles_sont_refusees_avec_laide():
    """Sans le rappel des formats, `1,5 milliard` renvoie à la documentation —
    que personne n'ouvre pour corriger une saisie."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=2, min="abc", max="250"
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "K M G T P" in texte
    assert await _tranches_enregistrees(magasin) == []


async def test_une_seule_borne_est_refusee():
    """Une borne seule ne décrit pas une plage. Acceptée en complétant l'autre au
    hasard, elle donnerait une tranche que personne n'a réglée ; ignorée, elle
    plafonnerait la fourchette entière alors qu'on visait une plage.
    """
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=2, min="1"
    )

    assert "❌" in " ".join(interaction.textes)
    assert await _tranches_enregistrees(magasin) == []
    assert plafond_fourchette(await _fourchette(magasin)) is None


async def test_une_tranche_sur_une_fourchette_inconnue_est_refusee():
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="petits", nombre=2, **BAS
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "grosses" in texte


async def test_une_tranche_hors_de_la_fourchette_previent():
    """Réglée mais inerte : elle ne rencontrera jamais une promotion.

    Le cas d'une borne tapée d'un palier à côté. Sans avertissement, le réglage
    est confirmé, le post ne change pas, et il n'y a rien à l'écran pour faire le
    lien entre les deux.
    """
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=2, min="5000", max="9000"
    )

    assert "⚠️" in " ".join(interaction.textes)


async def test_une_tranche_dans_la_fourchette_ne_previent_pas():
    """Le témoin de l'avertissement précédent : un ⚠️ sur chaque réglage valide
    apprendrait à ne plus le lire."""
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=2, **BAS
    )

    assert "⚠️" not in " ".join(interaction.textes)


async def test_une_tranche_ne_touche_pas_le_plafond_de_la_fourchette():
    """Les deux réglages se composent : régler l'un en effaçant l'autre ferait
    d'une commande à quatre paramètres un piège."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_plafond_fourchette("grosses", 3)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=1, **BAS
    )

    assert plafond_fourchette(await _fourchette(magasin)) == 3


async def test_le_plafond_de_la_fourchette_ne_touche_pas_les_tranches():
    """L'inverse du précédent : `/promos plafond` sans bornes ne parle que de la
    fourchette entière, et ne doit pas emporter les plages réglées."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 1)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=3
    )

    assert await _tranches_enregistrees(magasin) == [(Decimal(1), Decimal(250), 1)]


async def test_la_tranche_ne_bouge_pas_chez_le_voisin():
    """Les fourchettes sont par serveur, les tranches sont dedans : un réglage
    fait dans une entreprise ne doit pas raccourcir le post d'une autre."""
    bot = await _bot()
    await _avec_fourchette(bot)
    voisin = await _avec_fourchette(bot, VOISIN)

    await _commande(bot, "promos plafond").callback(
        _interaction(EMPIRE), fourchette="grosses", nombre=1, **BAS
    )

    assert await _tranches_enregistrees(voisin) == []


# --- Effacer une tranche -----------------------------------------------------


async def test_avec_des_bornes_et_sans_nombre_la_tranche_est_effacee():
    """Le même geste que pour le plafond et la tolérance : le réglage sans sa
    valeur le défait, plutôt qu'un mot de plus à retenir par réglage."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 1)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", **BAS
    )

    assert await _tranches_enregistrees(magasin) == []
    assert "✅" in " ".join(interaction.textes)


async def test_effacer_une_tranche_absente_le_dit():
    """Un « ✅ » annoncerait un effacement imaginaire. Le cas typique est la borne
    mal retapée : on croit corriger la tranche qu'on vient de régler, et l'on en
    laisse deux."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 1)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", min="1", max="300"
    )

    assert "ℹ️" in " ".join(interaction.textes)
    assert await _tranches_enregistrees(magasin) == [(Decimal(1), Decimal(250), 1)]


async def test_effacer_une_tranche_garde_le_plafond_de_la_fourchette():
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 1)
    await magasin.regler_plafond_fourchette("grosses", 3)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", **BAS
    )

    assert plafond_fourchette(await _fourchette(magasin)) == 3


async def test_effacer_une_tranche_sur_une_fourchette_inconnue_le_dit():
    """Le même refus que pour un réglage : « pas de tranche » sur un nom qui
    n'existe pas cacherait la faute de frappe."""
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="petits", **BAS
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "grosses" in texte


# --- Ce que ça change à l'écran ---------------------------------------------


async def test_le_post_du_soir_respecte_les_tranches():
    """Le branchement qui compte : sans lui, tout le reste ne serait qu'un réglage
    enregistré et jamais lu."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 1)

    # `maintenant` n'est pas consulté par les promotions : leur heure est déjà lue
    # par la tournée, qui décide d'appeler ou non cette préparation.
    tournee = await _preparer(bot, magasin, maintenant=None)

    assert tournee.compte == 3


async def test_sans_tranche_le_post_du_soir_prend_tout():
    """Le témoin de l'assertion précédente : quatre promotions récoltées, donc le
    3 qu'elle constate est bien une coupe."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)

    tournee = await _preparer(bot, magasin, maintenant=None)

    assert tournee.compte == 4


async def test_lapercu_respecte_les_tranches():
    """Un aperçu non tranché promettrait un post que le soir ne produira pas."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 1)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos apercu").callback(interaction)

    assert len(_titres(interaction)) == 3


async def test_la_recherche_est_tranchee_quand_la_plage_est_reglee_partout():
    """`/promos chercher` sans bornes montre ce qui va sortir : les tranches
    doivent s'y voir, sinon la commande promet plus que le post."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 1)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos chercher").callback(interaction)

    assert len(_titres(interaction)) == 3


async def test_une_recherche_a_bornes_donnees_nest_pas_tranchee():
    """Des bornes tapées à la main posent une autre question que « qu'est-ce qui va
    sortir ce soir ? » : elles ignorent déjà les fourchettes, et couper le résultat
    cacherait des promotions qu'on vient de demander explicitement."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 1)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos chercher").callback(interaction, min="0", max="1000")

    assert len(_titres(interaction)) == 4


async def test_la_recherche_reste_libre_si_une_fourchette_na_pas_la_plage():
    """La recherche couvre l'**union** : tranchée par l'une, elle cacherait des
    promotions que l'autre publie, et montrerait moins que le post du soir."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.ajouter_fourchette("petits", Decimal(1), Decimal(150))
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 1)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos chercher").callback(interaction)

    assert len(_titres(interaction)) == 4


# --- Les relire --------------------------------------------------------------


async def test_la_liste_montre_les_tranches():
    """Seul endroit où les relire : les posts ne les mentionnent pas, et un
    réglage invisible se re-règle au hasard."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 2)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos liste").callback(interaction)

    rendu = interaction.embeds[0].description
    assert "250" in rendu
    assert "2" in rendu


async def test_la_liste_montre_chaque_tranche():
    """Une seule ligne pour deux plages obligerait à deviner laquelle est
    laquelle, et l'on en effacerait une au hasard."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_tranche_fourchette("grosses", Decimal(1), Decimal(250), 2)
    await magasin.regler_tranche_fourchette("grosses", Decimal(300), Decimal(500), 1)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos liste").callback(interaction)

    rendu = interaction.embeds[0].description
    assert "250" in rendu
    assert "500" in rendu


async def test_la_liste_ne_parle_pas_de_tranche_sans_tranche():
    """Une mention « aucune » ferait chercher un réglage là où il n'y a qu'un
    défaut, comme pour la tolérance et le plafond."""
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos liste").callback(interaction)

    assert "tranche" not in interaction.embeds[0].description.casefold()
