"""`/promos plafond` : combien de promotions au maximum, par fourchette.

Le cœur sait couper (`tests/test_plafond.py`) et la base sait retenir le nombre
(`tests/test_plafond_par_fourchette.py`). Il manque la porte, et surtout le
branchement : un plafond enregistré que la publication ne lit pas serait le pire
des cas — la commande confirme, et le post du soir sort inchangé.

D'où les trois effets vérifiés ici, un par façade : le post du soir, l'aperçu qui
doit le montrer tel quel, et `/promos chercher`. Cette dernière couvre l'union des
fourchettes : elle n'est plafonnée que si toutes le sont, sinon elle cacherait des
promotions qu'une fourchette non plafonnée publie bel et bien.
"""

from decimal import Decimal

from src.bot import EmpireBot
from src.db import Store, plafond_fourchette
from src.modules.promos import _preparer

from tests.test_commandes_fourchettes import _commande
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction, _propositions

#: Quatre promotions du même type, à quatre prix distincts, toutes dans la
#: fourchette montée ci-dessous. Quatre pour qu'un plafond de 2 se voie comme une
#: coupe et non comme une récolte pauvre.
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

    Le salon compte : sans lui la fourchette est écartée de la publication, et le
    plafond ne serait éprouvé que sur une tournée vide.
    """
    magasin = _magasin(bot, serveur_id)
    await magasin.ajouter_fourchette("grosses", Decimal(1), Decimal(1000))
    await magasin.ajouter_salon_fourchette("grosses", "42")
    return magasin


async def _plafond_enregistre(magasin, nom: str = "grosses") -> int | None:
    for fourchette in await magasin.fourchettes():
        if fourchette["nom"] == nom:
            return plafond_fourchette(fourchette)
    raise AssertionError(f"fourchette introuvable : {nom}")


def _titres(interaction) -> list[str]:
    """Titres des embeds envoyés, pour compter ce qui est réellement montré."""
    trouves = []
    for message in [*interaction.response.messages, *interaction.followup.messages]:
        for embed in message.get("embeds") or []:
            titre = getattr(embed, "title", None) or embed.to_dict().get("title")
            if titre:
                trouves.append(titre)
    return trouves


# --- La commande dans le menu -----------------------------------------------


async def test_le_plafond_se_regle_sous_promos():
    """Sous `/promos` avec les autres réglages de fourchette, et non à la racine :
    un plafond ne veut rien dire sans la fourchette qu'il plafonne."""
    bot = await _bot()

    assert _commande(bot, "promos plafond") is not None


async def test_la_commande_nomme_sa_fourchette_et_son_nombre():
    """`fourchette` comme ses sœurs — Discord n'accepte que trois niveaux, donc
    c'est le paramètre qui dit sur quoi l'on agit."""
    bot = await _bot()

    parametres = _commande(bot, "promos plafond")._params

    assert "fourchette" in parametres
    assert "nombre" in parametres


async def test_le_nom_de_fourchette_se_propose():
    """Retaper le nom exposerait à une faute de frappe qui ne se verrait qu'au
    message d'erreur."""
    bot = await _bot()

    commande = _commande(bot, "promos plafond")

    assert _propositions(commande, "fourchette") is not None


# --- Régler ------------------------------------------------------------------


async def test_regler_le_plafond_confirme_avec_le_nombre():
    """Le nombre dans la réponse : c'est la seule preuve que c'est bien celui-là
    qui a été retenu, la commande n'ayant aucun autre écho."""
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=3
    )

    texte = " ".join(interaction.textes)
    assert "✅" in texte
    assert "3" in texte
    assert "grosses" in texte


async def test_regler_le_plafond_lenregistre():
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=3
    )

    assert await _plafond_enregistre(magasin) == 3


async def test_un_plafond_de_un_previent_quil_passe_devant_le_repechage():
    """Le plancher de deux promotions complète une fourchette trop pauvre.

    Un plafond de 1 l'annule ; sans avertissement, on croirait le repêchage
    toujours actif et l'on chercherait pourquoi les jours creux ne donnent qu'une
    promotion là où le réglage de tolérance en promettait deux.
    """
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=1
    )

    assert "repêch" in " ".join(interaction.textes).casefold()


async def test_un_plafond_a_zero_est_refuse():
    """Une fourchette qui ne publie rien est indiscernable d'une panne."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="grosses", nombre=0
    )

    assert "❌" in " ".join(interaction.textes)
    assert await _plafond_enregistre(magasin) is None


async def test_une_fourchette_inconnue_est_refusee_en_citant_les_vraies():
    """Sans les noms, impossible de savoir si c'est une faute de frappe ou une
    fourchette jamais créée."""
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(
        interaction, fourchette="petits", nombre=3
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "grosses" in texte


async def test_le_plafond_ne_bouge_pas_chez_le_voisin():
    """Les fourchettes sont par serveur, le plafond est dedans : un réglage fait
    dans une entreprise ne doit pas raccourcir le post d'une autre."""
    bot = await _bot()
    await _avec_fourchette(bot)
    voisin = await _avec_fourchette(bot, VOISIN)

    await _commande(bot, "promos plafond").callback(
        _interaction(EMPIRE), fourchette="grosses", nombre=2
    )

    assert await _plafond_enregistre(voisin) is None


# --- Effacer -----------------------------------------------------------------


async def test_sans_nombre_le_plafond_est_efface():
    """Comme `/promos tolerance` : le même geste défait le réglage, plutôt qu'un
    mot de plus à retenir par réglage."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_plafond_fourchette("grosses", 2)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(interaction, fourchette="grosses")

    assert await _plafond_enregistre(magasin) is None
    assert "✅" in " ".join(interaction.textes)


async def test_effacer_un_plafond_absent_le_dit():
    """Un « ✅ » annoncerait un effacement imaginaire, et l'on croirait avoir
    changé quelque chose."""
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(interaction, fourchette="grosses")

    assert "ℹ️" in " ".join(interaction.textes)


async def test_effacer_sur_une_fourchette_inconnue_le_dit():
    """Le même refus que pour un réglage : « n'avait pas de plafond » sur un nom
    qui n'existe pas cacherait la faute de frappe."""
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos plafond").callback(interaction, fourchette="petits")

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "grosses" in texte


# --- Ce que ça change à l'écran ---------------------------------------------


async def test_le_post_du_soir_respecte_le_plafond():
    """Le branchement qui compte : sans lui, tout le reste ne serait qu'un
    réglage enregistré et jamais lu."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_plafond_fourchette("grosses", 2)

    # `maintenant` n'est pas consulté par les promotions : leur heure est déjà
    # lue par la tournée, qui décide d'appeler ou non cette préparation.
    tournee = await _preparer(bot, magasin, maintenant=None)

    assert tournee.compte == 2


async def test_sans_plafond_le_post_du_soir_prend_tout():
    """Le témoin de l'assertion précédente : quatre promotions récoltées, donc
    le 2 qu'elle constate est bien une coupe."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)

    tournee = await _preparer(bot, magasin, maintenant=None)

    assert tournee.compte == 4


async def test_lapercu_respecte_le_plafond():
    """Un aperçu non plafonné promettrait un post que le soir ne produira pas."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_plafond_fourchette("grosses", 2)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos apercu").callback(interaction)

    assert len(_titres(interaction)) == 2


async def test_la_recherche_est_plafonnee_quand_tout_lest():
    """`/promos chercher` sans bornes montre ce qui va sortir : le plafond des
    fourchettes doit s'y voir, sinon la commande promet plus que le post."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_plafond_fourchette("grosses", 2)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos chercher").callback(interaction)

    assert len(_titres(interaction)) == 2


async def test_une_recherche_a_bornes_donnees_nest_pas_plafonnee():
    """Des bornes tapées à la main posent une autre question que « qu'est-ce qui
    va sortir ce soir ? » : elles ignorent déjà les fourchettes, et couper le
    résultat cacherait des promotions qu'on vient de demander explicitement."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_plafond_fourchette("grosses", 2)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos chercher").callback(interaction, min="0", max="1000")

    assert len(_titres(interaction)) == 4


async def test_la_recherche_reste_libre_si_une_fourchette_nest_pas_plafonnee():
    """La recherche couvre l'**union** : plafonnée par l'une, elle cacherait des
    promotions que l'autre publie, et montrerait moins que le post du soir."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.ajouter_fourchette("petits", Decimal(1), Decimal(150))
    await magasin.regler_plafond_fourchette("grosses", 2)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos chercher").callback(interaction)

    assert len(_titres(interaction)) == 4


# --- Le relire ---------------------------------------------------------------


async def test_la_liste_montre_le_plafond():
    """Seul endroit où le relire : les posts ne le mentionnent pas, et un
    réglage invisible se re-règle au hasard."""
    bot = await _bot()
    magasin = await _avec_fourchette(bot)
    await magasin.regler_plafond_fourchette("grosses", 3)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos liste").callback(interaction)

    rendu = interaction.embeds[0].description
    assert "plafond" in rendu.casefold()
    assert "3" in rendu


async def test_la_liste_ne_parle_pas_de_plafond_sans_plafond():
    """Une mention « aucun » ferait chercher un réglage là où il n'y a qu'un
    défaut, comme pour la tolérance."""
    bot = await _bot()
    await _avec_fourchette(bot)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos liste").callback(interaction)

    assert "plafond" not in interaction.embeds[0].description.casefold()
