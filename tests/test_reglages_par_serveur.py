"""`/reglages` règle **le serveur où il est tapé**, et le dit quand rien ne l'est.

`/reglages` n'est pas un module : c'est le noyau, là où se règlent le fuseau, le
journal, la mention et le template. Tant que ces commandes lisent la
configuration commune, un « ✅ » y répond à un réglage que la tournée de ce
serveur ne lira jamais — et qui déplace au passage celui du voisin.

Deux serveurs partout, et la même assertion : ce qui est réglé ici n'apparaît ni
chez le voisin, ni dans la configuration commune, celle que le site de contrôle
continue de lire faute de dire de quel serveur il parle.

Ce fichier porte aussi l'**annonce** de l'étape. Il n'y a pas de repli : un
serveur qui n'a rien réglé est muet, ses posts ne sortent plus et son journal se
tait. Le taire ferait chercher une panne pendant des jours — `/reglages voir` le
dit à qui regarde, et le journal le dit au démarrage à qui ne regarde pas.

Ce qui reste délibérément commun n'est pas remis en cause ici : la table des
rôles mentionnés (`tests/test_reglages_mention_multi_serveurs.py`), le cache des
noms de salons, et la liste d'accès — celle-ci suit son gardien, `ArbreProtege`.
"""

import json
from decimal import Decimal

from src.bot import EmpireBot
from src.db import Store
from src.promos import parse_csv
from src.schedule import maintenant_local

from tests.test_commandes_filiales import _fichiers, _octets
from tests.test_commandes_fourchettes import (
    SalonFactice,
    ServeurFactice,
    SourceFactice,
    _bot,
    _commande,
)
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction
from tests.test_commands import _champ

#: Un export où une promotion tombe dans n'importe quelle fourchette large : ce
#: qui est éprouvé est le **rendu** du template, pas la recherche de promotions.
CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
"""


class Piece:
    """Une pièce jointe Discord réduite à ce que la commande en fait : des octets."""

    def __init__(self, contenu: str):
        self._octets = contenu.encode("utf-8")

    async def read(self) -> bytes:
        return self._octets


class SourcePleine:
    """La source du jeu, réduite à un export qui contient une promotion.

    `SourceFactice` rend un export vide : l'aperçu d'un template n'aurait alors
    rien à rendre, et se contenterait de dire qu'il est impossible.
    """

    async def fetch(self) -> str:
        return CSV


class JournalFactice:
    """Le salon de logs, sans Discord : on lit ce qui y serait écrit."""

    def __init__(self):
        self.messages: list[str] = []

    async def erreur(self, message: str) -> None:
        self.messages.append(message)

    async def publication(self, promos, reussis, echecs) -> None:
        pass


class BoucleFactice:
    """La boucle asyncio que `discord.Client` n'a qu'une fois connecté.

    `on_ready` y lance la planification. La corotine est refermée aussitôt : non
    attendue, elle laisserait un avertissement à chaque exécution de la suite.
    """

    def create_task(self, corotine):
        corotine.close()
        return None


class BotAvecServeurs(EmpireBot):
    """`EmpireBot` dont on choisit les serveurs.

    `guilds` est une propriété en lecture seule de `discord.Client` : la
    redéfinir dans une sous-classe est le seul moyen de la garnir sans se
    connecter à Discord.
    """

    @property
    def guilds(self):
        return self._serveurs


def _embeds_envoyes(interaction) -> list:
    """Les embeds partis par `publish.envoyer`, qui les groupe sous `embeds=`.

    `InteractionFactice.embeds` ne relève que les `embed=` au singulier, ceux
    des réponses ordinaires : un aperçu de publication y serait invisible.
    """
    return [
        embed
        for message in interaction.followup.messages
        for embed in message.get("embeds", [])
    ]


async def _bot_avec(*serveurs: int) -> BotAvecServeurs:
    store = Store(dsn="")
    await store.connect()
    bot = BotAvecServeurs(store, SourceFactice())
    bot._serveurs = [ServeurFactice(serveur_id) for serveur_id in serveurs]
    bot.journal = JournalFactice()
    return bot


# --- /reglages voir : la configuration de ce serveur ------------------------


async def test_voir_montre_lheure_de_ce_serveur():
    """La première chose qu'on y lit, et la plus trompeuse : montrer l'heure du
    commun ferait attendre le post à un moment où il ne part pas."""
    bot = await _bot()
    await bot.store.maj_config(heure="06:07")
    await bot.store.pour(EMPIRE).maj_config(heure="21:37")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages voir").callback(interaction)

    assert _champ(interaction.embeds[0], "Heure").startswith("21:37")


async def test_voir_montre_les_fourchettes_de_ce_serveur():
    """Les fourchettes du commun ne publient plus rien : les afficher ici ferait
    croire ce serveur réglé, et cacherait qu'il n'a rien."""
    bot = await _bot()
    await bot.store.ajouter_fourchette("historique", Decimal("0"), Decimal("1e12"))
    await bot.store.pour(EMPIRE).ajouter_fourchette(
        "grosses", Decimal("1e14"), Decimal("6e15")
    )
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages voir").callback(interaction)

    rendu = repr(interaction.embeds[0].to_dict())
    assert "grosses" in rendu
    assert "historique" not in rendu


async def test_voir_montre_le_journal_de_ce_serveur():
    bot = await _bot()
    await bot.store.maj_config(logs_salon_id="1111")
    await bot.store.pour(EMPIRE).maj_config(logs_salon_id="2222")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages voir").callback(interaction)

    assert _champ(interaction.embeds[0], "Journal") == "<#2222>"


async def test_voir_montre_la_derniere_publication_de_ce_serveur():
    """Chaque serveur a sa trace du jour : celle du commun dirait « jamais » à un
    serveur qui a publié ce matin, ou l'inverse."""
    bot = await _bot()
    await bot.store.marquer_publie("2000-01-01")
    await bot.store.pour(EMPIRE).marquer_publie("2026-08-19")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages voir").callback(interaction)

    assert "2026-08-19" in interaction.embeds[0].footer.text


async def test_voir_montre_encore_le_role_dun_reglage_davant_le_multi_serveurs():
    """La mention reste dans la table commune, `role_id` plat compris.

    Cherché dans la configuration de ce serveur, l'ancien réglage plat
    disparaîtrait de l'affichage alors que le bot pingue toujours ce rôle.
    """
    bot = await _bot()
    await bot.store.set("config", {"role_id": "7"})
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages voir").callback(interaction)

    assert "<@&7>" in _champ(interaction.embeds[0], "Mention")


# --- /reglages voir : l'annonce de l'étape ----------------------------------


async def test_voir_dit_a_un_serveur_neuf_de_reprendre_la_configuration():
    """Sans repli, un serveur non réglé est muet. Le silence est le pire des
    signalements : il ressemble trait pour trait à une panne du bot."""
    bot = await _bot()
    await bot.store.ajouter_fourchette("historique", Decimal("0"), Decimal("1e12"))
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages voir").callback(interaction)

    description = interaction.embeds[0].description or ""
    assert "/reglages importer" in description


async def test_voir_ne_reclame_rien_a_un_serveur_deja_regle():
    """Un avertissement affiché toujours n'est plus lu, et celui-ci doit l'être."""
    bot = await _bot()
    await bot.store.pour(EMPIRE).ajouter_fourchette(
        "grosses", Decimal("1e14"), Decimal("6e15")
    )
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages voir").callback(interaction)

    assert "/reglages importer" not in (interaction.embeds[0].description or "")


async def test_les_serveurs_sans_configuration_sont_signales_au_demarrage():
    """Dit dans le journal commun, le seul salon réglé avant l'étape.

    Celui du serveur en cause est muet par définition : c'est justement sa
    configuration qui manque.
    """
    bot = await _bot_avec(EMPIRE, VOISIN)
    await bot.store.pour(VOISIN).maj_config(heure="21:37")

    await bot.signaler_les_serveurs_sans_configuration()

    message = " ".join(bot.journal.messages)
    assert "/reglages importer" in message
    assert f"Serveur {EMPIRE}" in message
    assert f"Serveur {VOISIN}" not in message


async def test_le_demarrage_reste_muet_quand_chaque_serveur_est_regle():
    """Un signalement à chaque démarrage apprendrait à ne plus lire ce salon, et
    le vrai passerait avec le reste."""
    bot = await _bot_avec(EMPIRE)
    await bot.store.pour(EMPIRE).maj_config(heure="21:37")

    await bot.signaler_les_serveurs_sans_configuration()

    assert bot.journal.messages == []


async def test_le_demarrage_dit_ce_qui_empeche_de_publier():
    """Les deux signalements sont branchés au seul endroit qui les déclenche.

    `on_ready` et non `setup_hook` : le salon de logs se résout par l'API, et la
    liste des serveurs n'est garnie qu'une fois la connexion établie. Une méthode
    éprouvée mais jamais appelée ne dirait jamais rien.
    """
    bot = await _bot_avec(EMPIRE)
    bot.loop = BoucleFactice()
    bot.modules_refuses = {"bonjour": "ImportError : pas de module nommé pandas"}

    await bot.on_ready()

    message = " ".join(bot.journal.messages)
    assert "bonjour" in message
    assert f"Serveur {EMPIRE}" in message


# --- /reglages fuseau -------------------------------------------------------


async def test_le_fuseau_est_regle_dans_le_serveur_ou_on_le_tape():
    """Deux entreprises peuvent vivre dans deux décalages : le fuseau est commun
    aux publications d'un serveur, pas aux serveurs entre eux."""
    bot = await _bot()

    await _commande(bot, "reglages fuseau").callback(
        _interaction(EMPIRE), fuseau="Pacific/Niue"
    )

    assert (await bot.store.pour(EMPIRE).config())["fuseau"] == "Pacific/Niue"
    assert (await bot.store.config())["fuseau"] != "Pacific/Niue"
    assert (await bot.store.pour(VOISIN).config())["fuseau"] != "Pacific/Niue"


async def test_le_fuseau_rappelle_les_heures_de_ce_serveur():
    """La réponse relit les deux heures pour rendre le décalage visible tout de
    suite : celles du commun feraient guetter les posts à côté."""
    bot = await _bot()
    await bot.store.maj_config(heure="06:07", filiales_heure="06:08")
    await bot.store.pour(EMPIRE).maj_config(heure="21:37", filiales_heure="22:47")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages fuseau").callback(
        interaction, fuseau="Pacific/Niue"
    )

    texte = " ".join(interaction.textes)
    assert "21:37" in texte and "22:47" in texte
    assert "06:07" not in texte and "06:08" not in texte


# --- /reglages logs ---------------------------------------------------------


async def test_le_journal_est_regle_dans_son_serveur():
    """Le compte rendu d'une tournée nomme des salons : raconté dans le journal
    d'une autre entreprise, il lui donnerait les ids de celle-ci."""
    bot = await _bot()
    await bot.store.maj_config(logs_salon_id="1111")

    await _commande(bot, "reglages logs").callback(
        _interaction(EMPIRE), SalonFactice(2222)
    )

    assert await bot.store.pour(EMPIRE).salon_logs() == "2222"
    assert await bot.store.salon_logs() == "1111"


async def test_desactiver_le_journal_ne_fait_taire_que_le_sien():
    bot = await _bot()
    await bot.store.maj_config(logs_salon_id="1111")
    await bot.store.pour(EMPIRE).maj_config(logs_salon_id="2222")

    await _commande(bot, "reglages logs").callback(_interaction(EMPIRE), None)

    assert await bot.store.pour(EMPIRE).salon_logs() is None
    assert await bot.store.salon_logs() == "1111"


# --- /reglages template -----------------------------------------------------


async def test_le_template_est_charge_dans_son_serveur():
    """Deux entreprises n'ont pas la même charte : c'est tout l'intérêt d'un
    template, et le charger pour tout le monde reprendrait celui du voisin."""
    bot = await _bot()
    await bot.store.set_template({"embeds": [{"title": "Commun"}]})

    await _commande(bot, "reglages template charger").callback(
        _interaction(EMPIRE),
        Piece(json.dumps({"embeds": [{"title": "Chez Empire"}]})),
    )

    magasin = bot.store.pour(EMPIRE)
    assert (await magasin.template())["embeds"][0]["title"] == "Chez Empire"
    assert (await bot.store.template())["embeds"][0]["title"] == "Commun"


async def test_lapercu_qui_suit_montre_le_template_quon_vient_de_charger():
    """C'est le seul retour qu'on ait sur un JSON qu'on vient d'envoyer.

    Rendu avec le template du commun, il confirmerait le réglage en montrant
    celui du voisin — et une charte fautive ne se verrait qu'au post du soir.
    """
    bot = await _bot()
    bot.source = SourcePleine()
    await bot.store.set_template({"embeds": [{"title": "Commun"}]})
    magasin = bot.store.pour(EMPIRE)
    await magasin.maj_config(prix_min="0", prix_max="6e15")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages template charger").callback(
        interaction,
        Piece(json.dumps({"embeds": [{"title": "Chez Empire — {nom}"}]})),
    )

    titres = [embed.title for embed in _embeds_envoyes(interaction)]
    assert titres and titres[0].startswith("Chez Empire")


async def test_le_template_affiche_est_celui_de_ce_serveur():
    """`/reglages template voir` est le seul moyen de relire ce qui est réglé :
    renvoyer celui du commun ferait repartir d'un fichier qui n'est pas le sien."""
    bot = await _bot()
    await bot.store.set_template({"embeds": [{"title": "Commun"}]})
    await bot.store.pour(EMPIRE).set_template({"embeds": [{"title": "Chez Empire"}]})
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages template voir").callback(interaction)

    contenu = _octets(_fichiers(interaction)[0]).decode("utf-8")
    assert "Chez Empire" in contenu
    assert "Commun" not in contenu


async def test_promos_repond_avec_le_template_de_son_serveur():
    """`/promos` est la répétition du post du soir : rendue avec la charte du
    commun, elle montrerait autre chose que ce qui sortira."""
    bot = await _bot()
    bot.source = SourcePleine()
    await bot.store.set_template({"embeds": [{"title": "Commun"}]})
    magasin = bot.store.pour(EMPIRE)
    await magasin.ajouter_fourchette("grosses", Decimal("0"), Decimal("6e15"))
    await magasin.set_template({"embeds": [{"title": "Chez Empire — {nom}"}]})
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos").callback(interaction)

    titres = [embed.title for embed in _embeds_envoyes(interaction)]
    assert titres and titres[0].startswith("Chez Empire")


async def test_la_publication_est_rendue_avec_le_template_de_son_serveur():
    """Le template ne sert qu'à une chose : rendre les posts. Réglé par serveur
    mais lu dans le commun, il ne changerait jamais rien à ce qui sort."""
    bot = await _bot()
    await bot.store.set_template({"embeds": [{"title": "Commun"}]})
    magasin = bot.store.pour(EMPIRE)
    await magasin.set_template({"embeds": [{"title": "Chez Empire — {nom}"}]})

    embeds, _, repli = await bot.construire_publication(
        Decimal("0"), Decimal("6e15"), magasin=magasin, donnees=parse_csv(CSV)
    )

    assert not repli
    assert embeds[0]["title"].startswith("Chez Empire")


async def test_la_date_dun_post_vient_du_fuseau_de_son_serveur():
    """`{date}` dans un template : datée d'ailleurs, la ligne se lirait « post
    d'hier » un jour sur deux dans un serveur qui n'a pas le même décalage."""
    bot = await _bot()
    await bot.store.maj_config(fuseau="Pacific/Kiritimati")
    magasin = bot.store.pour(EMPIRE)
    await magasin.maj_config(fuseau="Pacific/Niue")
    await magasin.set_template({"embeds": [{"title": "{date}"}]})

    embeds, _, _ = await bot.construire_publication(
        Decimal("0"), Decimal("6e15"), magasin=magasin, donnees=parse_csv(CSV)
    )

    # Les deux fuseaux sont à 25 heures l'un de l'autre : leurs dates ne
    # coïncident jamais, donc l'assertion ne dépend pas de l'heure du test.
    assert embeds[0]["title"] == maintenant_local("Pacific/Niue").strftime("%Y-%m-%d")
