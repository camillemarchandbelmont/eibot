"""Le mot de passe de la page, rangé par entreprise et tiré par Discord.

`tests/test_motdepasse.py` éprouve le calcul : empreinte salée, cookie signé.
Restent les deux bouts qui le relient au reste du bot :

- **le tiroir**, `Store.motdepasse_page` et compagnie. Rangé dans le commun, un
  mot de passe vaudrait pour toutes les entreprises à la fois — celles dont la
  page propose la liste dans un menu déroulant ;
- **la commande**, `/reglages motdepasse`. Le mot de passe est tiré par le bot et
  rendu dans une réponse **éphémère** : passé en argument de commande, Discord
  l'afficherait à tout le salon, et il serait à changer aussitôt que lu.

Le mot de passe en clair ne doit exister qu'entre le tirage et cette réponse :
nulle part en base, et donc nulle part dans une sauvegarde de la base.
"""

import re

from src.db import Store
from src.motdepasse import verifie
from src.page_frais import CHEMIN

from tests.test_commandes_fourchettes import (
    InteractionFactice,
    ServeurFactice,
    _bot,
    _commande,
)
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction

#: Un mot de passe tiré, tel qu'il apparaît dans une réponse : quatre groupes de
#: quatre caractères. Sert à le relire dans le message, faute de pouvoir le
#: demander à la base — qui n'en garde que l'empreinte.
MOTIF = re.compile(r"[a-z2-9]{4}(?:-[a-z2-9]{4}){3}")


async def _magasin() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


def _lire_le_mot_de_passe(interaction: InteractionFactice) -> str:
    """Le mot de passe tel qu'il est montré, extrait de la réponse."""
    trouve = MOTIF.search(" ".join(interaction.textes))
    assert trouve, f"aucun mot de passe dans {interaction.textes}"
    return trouve.group(0)


def _non_admin(serveur_id: int) -> InteractionFactice:
    interaction = InteractionFactice(admin=False)
    interaction.guild = ServeurFactice(serveur_id)
    return interaction


# --- Le tiroir --------------------------------------------------------------


async def test_une_entreprise_neuve_na_pas_de_mot_de_passe():
    """Et sa page est donc fermée en écriture : c'est `verifie` qui le tient,
    mais encore faut-il que l'absence se lise comme une absence."""
    store = await _magasin()

    assert await store.motdepasse_page() is None


async def test_definir_rend_le_mot_de_passe_et_nen_garde_que_lempreinte():
    """Le seul moment où le clair existe : il est rendu, jamais relu."""
    store = await _magasin()

    mdp = await store.definir_motdepasse_page()

    assert verifie(await store.motdepasse_page(), mdp) is True
    assert mdp not in repr(await store.tout())


async def test_le_redefinir_invalide_lancien():
    """C'est ce qu'on attend d'un mot de passe changé, et le seul moyen de couper
    quelqu'un à qui on l'avait donné."""
    store = await _magasin()

    ancien = await store.definir_motdepasse_page()
    nouveau = await store.definir_motdepasse_page()

    assert ancien != nouveau
    assert verifie(await store.motdepasse_page(), ancien) is False
    assert verifie(await store.motdepasse_page(), nouveau) is True


async def test_effacer_referme_la_page():
    """Retirer le mot de passe doit fermer l'écriture, pas l'ouvrir à tous."""
    store = await _magasin()
    mdp = await store.definir_motdepasse_page()

    assert await store.effacer_motdepasse_page() is True
    assert await store.motdepasse_page() is None
    assert verifie(await store.motdepasse_page(), mdp) is False


async def test_effacer_sans_mot_de_passe_ne_dit_pas_le_contraire():
    """La commande répond d'après ce booléen : « ✅ retiré » sur une entreprise
    qui n'en avait pas ferait croire une page refermée qui ne l'était jamais."""
    store = await _magasin()

    assert await store.effacer_motdepasse_page() is False


async def test_une_trace_abimee_se_lit_comme_absente():
    """La base est du JSON qu'on peut retoucher à la main.

    Rendue telle quelle, une valeur qui n'est pas un enregistrement ferait
    échouer la signature du cookie — une panne de la page là où il ne devrait y
    avoir qu'un refus.
    """
    store = await _magasin()
    await store.set("motdepasse_page", "coucou")

    assert await store.motdepasse_page() is None


async def test_chaque_entreprise_a_son_mot_de_passe():
    """Le cœur du réglage : la page propose la liste des entreprises, et un mot
    de passe commun donnerait à chacun l'écriture chez tous."""
    store = await _magasin()

    ici = await store.pour(EMPIRE).definir_motdepasse_page()

    assert await store.pour(VOISIN).motdepasse_page() is None
    assert await store.motdepasse_page() is None
    assert verifie(await store.pour(EMPIRE).motdepasse_page(), ici) is True


async def test_le_mot_de_passe_dune_entreprise_ne_vaut_pas_chez_lautre():
    store = await _magasin()

    ici = await store.pour(EMPIRE).definir_motdepasse_page()
    await store.pour(VOISIN).definir_motdepasse_page()

    assert verifie(await store.pour(VOISIN).motdepasse_page(), ici) is False


# --- /reglages motdepasse ---------------------------------------------------


async def test_la_commande_montre_un_mot_de_passe_qui_marche():
    """Montré une fois : il n'est pas relisible ensuite, seule son empreinte
    reste. Un message qui ne le contiendrait pas le rendrait inutilisable."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction)

    trace = await bot.store.pour(EMPIRE).motdepasse_page()
    assert verifie(trace, _lire_le_mot_de_passe(interaction)) is True


async def test_le_mot_de_passe_nest_montre_qua_qui_le_demande():
    """Éphémère, sans quoi il resterait dans l'historique du salon — lisible par
    tout le serveur, et par tout nouveau membre."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction)

    messages = [*interaction.response.messages, *interaction.followup.messages]
    assert messages
    assert all(message.get("ephemeral") for message in messages)


async def test_la_commande_regle_le_serveur_ou_elle_est_tapee():
    """Écrit dans le commun, le mot de passe n'ouvrirait aucune entreprise : la
    page lit celui de l'entreprise choisie dans son menu."""
    bot = await _bot()

    await _commande(bot, "reglages motdepasse").callback(_interaction(EMPIRE))

    assert await bot.store.pour(EMPIRE).motdepasse_page() is not None
    assert await bot.store.pour(VOISIN).motdepasse_page() is None
    assert await bot.store.motdepasse_page() is None


async def test_la_commande_dit_ou_taper_le_mot_de_passe():
    """Un mot de passe sans l'adresse de la page ne sert à rien."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction)

    assert CHEMIN in " ".join(interaction.textes)


async def test_le_nouveau_mot_de_passe_previent_quil_coupe_les_navigateurs():
    """Le cookie est signé avec l'empreinte : le retirer coupe les navigateurs
    déjà identifiés. Retaper la commande pour relire le mot de passe oublié
    déconnecte donc les autres postes, et il faut le savoir avant."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction)

    assert "navigateur" in " ".join(interaction.textes).casefold()


async def test_retirer_referme_la_page():
    bot = await _bot()
    await bot.store.pour(EMPIRE).definir_motdepasse_page()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction, retirer=True)

    assert await bot.store.pour(EMPIRE).motdepasse_page() is None
    assert not MOTIF.search(" ".join(interaction.textes))


async def test_retirer_sans_mot_de_passe_le_dit():
    """« ✅ retiré » ferait croire une page refermée qui ne l'a jamais été."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction, retirer=True)

    assert "aucun" in " ".join(interaction.textes).casefold()


async def test_seul_un_administrateur_tire_le_mot_de_passe():
    """Le mot de passe s'emporte hors de Discord : le donner revient à ajouter
    quelqu'un à la liste d'accès en écriture, et cela ne s'accorde pas soi-même.
    """
    bot = await _bot()
    interaction = _non_admin(EMPIRE)

    await _commande(bot, "reglages motdepasse").callback(interaction)

    assert await bot.store.pour(EMPIRE).motdepasse_page() is None
    assert not MOTIF.search(" ".join(interaction.textes))
    assert "administrateur" in " ".join(interaction.textes).casefold()


async def test_un_non_administrateur_ne_referme_pas_la_page():
    """Le refus doit porter sur la commande, pas seulement sur le tirage : sinon
    un membre autorisé couperait la page de tout le monde."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).definir_motdepasse_page()

    await _commande(bot, "reglages motdepasse").callback(
        _non_admin(EMPIRE), retirer=True
    )

    assert await bot.store.pour(EMPIRE).motdepasse_page() is not None
