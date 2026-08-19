"""Tests des commandes slash, exécutées sans se connecter à Discord.

`EmpireBot` s'instancie hors ligne : on peut donc appeler le callback d'une
commande avec une interaction factice et inspecter l'embed produit. C'est ce
qui vérifie réellement ce que voit l'utilisateur — `diagnostiquer` seul ne dit
rien du rendu.
"""

import pytest

from src.bot import EmpireBot
from src.db import Store
from src.source import ApiSource, CsvFileSource, SourceError

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-28 08:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
industriels,"Entrepôt",0,302620,0,0,283,17,611961,87354,62063
zones,"Zone portuaire",0,124467906332,0,0,0,17,0,0,0
bureaux,"Tour sans promo",0,500000,0,0,0,0,0,0,0
"""


class Permissions:
    def __init__(self, administrator: bool):
        self.administrator = administrator


class Utilisateur:
    def __init__(self, admin: bool = True, membre_id: int = 1):
        self.id = membre_id
        self.guild_permissions = Permissions(admin)


class Reponse:
    """Capture `interaction.response.*`."""

    def __init__(self):
        self.messages: list[dict] = []
        self.differee = False

    async def defer(self, ephemeral: bool = False) -> None:
        self.differee = True

    async def send_message(self, contenu=None, **options) -> None:
        self.messages.append({"contenu": contenu, **options})


class Followup:
    """Capture `interaction.followup.send`."""

    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, contenu=None, **options) -> None:
        self.messages.append({"contenu": contenu, **options})


class InteractionFactice:
    def __init__(self, admin: bool = True, membre_id: int = 1):
        self.user = Utilisateur(admin, membre_id)
        self.response = Reponse()
        self.followup = Followup()

    @property
    def embeds(self) -> list:
        """Embeds envoyés, quelle que soit la voie (réponse ou followup)."""
        return [
            message["embed"]
            for message in [*self.response.messages, *self.followup.messages]
            if message.get("embed")
        ]

    @property
    def textes(self) -> list[str]:
        return [
            message["contenu"]
            for message in [*self.response.messages, *self.followup.messages]
            if isinstance(message.get("contenu"), str)
        ]


def _commande(bot: EmpireBot, nom: str):
    for commande in bot.tree.walk_commands():
        if commande.qualified_name == nom:
            return commande
    raise AssertionError(f"commande introuvable : {nom}")


async def _bot(source) -> EmpireBot:
    store = Store(dsn="")
    await store.connect()
    return EmpireBot(store, source)


def _champ(embed, nom: str) -> str:
    for champ in embed.fields:
        if champ.name == nom:
            return champ.value
    raise AssertionError(f"champ introuvable : {nom} (parmi {[c.name for c in embed.fields]})")


# --- /source tester ---------------------------------------------------------

async def test_source_tester_rapporte_le_succes(tmp_path):
    chemin = tmp_path / "export.csv"
    chemin.write_text(CSV, encoding="utf-8")
    bot = await _bot(CsvFileSource(chemin))
    interaction = InteractionFactice()

    await _commande(bot, "source tester").callback(interaction)

    assert interaction.response.differee  # sinon Discord expire avant l'API
    embed = interaction.embeds[0]
    assert "✅" in embed.title
    assert _champ(embed, "Bâtiments") == "3"
    assert _champ(embed, "En promotion") == "2"
    assert "Entrepôt" in _champ(embed, "Promotions trouvées")
    assert "Tour sans promo" not in _champ(embed, "Promotions trouvées")
    assert "2026-07-28 08:00:07" in _champ(embed, "Export")


async def test_source_tester_rapporte_lechec_sans_planter(tmp_path):
    """Une source en panne doit produire un embed rouge, pas une exception."""
    bot = await _bot(CsvFileSource(tmp_path / "absent.csv"))
    interaction = InteractionFactice()

    await _commande(bot, "source tester").callback(interaction)

    embed = interaction.embeds[0]
    assert "❌" in embed.title
    assert "absent.csv" in _champ(embed, "Erreur")


async def test_source_tester_ne_revele_pas_la_cle():
    bot = await _bot(ApiSource("http://127.0.0.1:1/x.csv?key={api_key}", cle="SECRET42"))
    interaction = InteractionFactice()

    await _commande(bot, "source tester").callback(interaction)

    embed = interaction.embeds[0]
    rendu = repr(embed.to_dict())
    assert "SECRET42" not in rendu
    assert "***" in rendu
    # L'aide au dépannage n'a de sens que pour l'API.
    assert "EMPIRE_API_KEY" in _champ(embed, "À vérifier")


async def test_source_tester_signale_zero_promotion_sans_echouer(tmp_path):
    chemin = tmp_path / "export.csv"
    chemin.write_text(CSV.replace(",17,", ",0,"), encoding="utf-8")
    bot = await _bot(CsvFileSource(chemin))
    interaction = InteractionFactice()

    await _commande(bot, "source tester").callback(interaction)

    embed = interaction.embeds[0]
    assert "✅" in embed.title  # la source marche, c'est ce qu'on teste
    assert _champ(embed, "En promotion") == "aucune aujourd'hui"


async def test_source_tester_reserve(tmp_path):
    """Le rapport expose l'URL de l'API : pas pour tout le serveur.

    Le refus vient du `CommandTree` (voir `tests/test_acces.py`) : on vérifie
    ici que `source tester` passe bien par lui, et donc qu'un membre lambda
    n'obtient aucun embed.
    """
    chemin = tmp_path / "export.csv"
    chemin.write_text(CSV, encoding="utf-8")
    bot = await _bot(CsvFileSource(chemin))
    interaction = InteractionFactice(admin=False)

    assert await bot.tree.autorisation(interaction) is False
    assert not interaction.embeds
    assert "Réservé" in interaction.textes[0]


async def test_source_tester_borne_la_liste_des_promotions(tmp_path):
    """116 bâtiments peuvent tous être en promo : l'embed ne doit pas exploser.

    Les noms du jeu sont longs (« Mégapôle millenium désaffecté ») : sans
    borne, 116 d'entre eux dépassent les 1024 caractères d'une valeur de champ
    et Discord rejette le message entier avec un 400.
    """
    lignes = [
        f'zones,"Mégapôle millenium désaffecté {index}",0,{1000 + index},0,0,0,17,0,0,0'
        for index in range(116)
    ]
    chemin = tmp_path / "export.csv"
    chemin.write_text(
        "\n".join([*CSV.splitlines()[:3], *lignes]), encoding="utf-8"
    )
    bot = await _bot(CsvFileSource(chemin))
    interaction = InteractionFactice()

    await _commande(bot, "source tester").callback(interaction)

    embed = interaction.embeds[0]
    assert _champ(embed, "En promotion") == "116"   # le compte reste exact
    valeur = _champ(embed, "Promotions trouvées")
    assert "+106" in valeur                          # le reste est résumé
    assert len(valeur) <= 1024                       # limite Discord


# --- /source voir -----------------------------------------------------------

async def test_source_voir_decrit_le_fichier(tmp_path):
    bot = await _bot(CsvFileSource(tmp_path / "export.csv"))
    interaction = InteractionFactice()

    await _commande(bot, "source voir").callback(interaction)

    embed = interaction.embeds[0]
    assert "fichier" in embed.description
    assert "EMPIRE_API_KEY" in _champ(embed, "Bascule")


async def test_source_voir_masque_la_cle():
    bot = await _bot(ApiSource("https://monde8.example/x.csv?key={api_key}", cle="SECRET42"))
    interaction = InteractionFactice()

    await _commande(bot, "source voir").callback(interaction)

    embed = interaction.embeds[0]
    assert "SECRET42" not in repr(embed.to_dict())
    assert "configurée" in _champ(embed, "Clé d'API")


# --- La panne de source reste lisible dans les autres commandes -------------

class SourceEnPanne:
    async def fetch(self):
        raise SourceError("API injoignable (ClientConnectorError).")


async def test_apercu_affiche_lerreur_de_source():
    """Une fourchette **avec un salon** est nécessaire : sans elle, l'aperçu
    refuse avant même de charger l'export, et ce n'est pas ce cas-là qu'on teste
    ici."""
    from decimal import Decimal

    bot = await _bot(SourceEnPanne())
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "111")
    interaction = InteractionFactice()

    await _commande(bot, "fourchette apercu").callback(interaction)

    assert "API injoignable" in interaction.textes[0]


# --- Salons : voir `tests/test_commandes_fourchettes.py` --------------------
#
# `/config salon ajouter|retirer|liste` n'existe plus : un salon s'attache
# désormais à une fourchette nommée (`/fourchette salon ajouter`). Les tests
# d'attachement, de permissions et de listage ont suivi la commande.

class SalonDiscordFactice:
    """Remplace `discord.TextChannel` pour les arguments de commande."""

    def __init__(
        self,
        salon_id: int,
        nom: str = "promos",
        peut_ecrire: bool = True,
        peut_integrer: bool | None = None,
    ):
        self.id = salon_id
        self.name = nom
        self.mention = f"<#{salon_id}>"
        self._peut_ecrire = peut_ecrire
        # Les deux permissions sont distinctes : un salon peut autoriser les
        # messages mais interdire les embeds, ce qui casserait le post.
        self._peut_integrer = peut_ecrire if peut_integrer is None else peut_integrer

    def permissions_for(self, _membre):
        class Permissions:
            send_messages = self._peut_ecrire
            embed_links = self._peut_integrer

        return Permissions()


async def _bot_fichier(tmp_path) -> EmpireBot:
    chemin = tmp_path / "export.csv"
    chemin.write_text(CSV, encoding="utf-8")
    return await _bot(CsvFileSource(chemin))


async def test_salon_commandes_reservees(tmp_path):
    """Un membre lambda est arrêté avant le callback, par le `CommandTree`."""
    bot = await _bot_fichier(tmp_path)
    interaction = InteractionFactice(admin=False)

    assert await bot.tree.autorisation(interaction) is False
    assert await bot.store.salons() == []
    assert "Réservé" in interaction.textes[0]


# --- /config logs -----------------------------------------------------------

async def test_logs_definir_le_salon(tmp_path):
    bot = await _bot_fichier(tmp_path)
    interaction = InteractionFactice()

    await _commande(bot, "config logs").callback(interaction, SalonDiscordFactice(999))

    assert await bot.store.salon_logs() == "999"
    assert "✅" in interaction.textes[0]


async def test_logs_sans_argument_desactive(tmp_path):
    bot = await _bot_fichier(tmp_path)
    await bot.store.maj_config(logs_salon_id="999")
    interaction = InteractionFactice()

    await _commande(bot, "config logs").callback(interaction, None)

    assert await bot.store.salon_logs() is None
    assert "désactiv" in interaction.textes[0].lower()


async def test_logs_reserve(tmp_path):
    """Le journal peut relayer des erreurs : pas configurable par n'importe qui."""
    bot = await _bot_fichier(tmp_path)
    interaction = InteractionFactice(admin=False)

    assert await bot.tree.autorisation(interaction) is False
    assert await bot.store.salon_logs() is None


# --- /config voir : refléter les fourchettes --------------------------------

async def test_config_voir_affiche_chaque_fourchette_et_ses_salons(tmp_path):
    """Une seule ligne « fourchette » ne dirait plus quel salon reçoit quoi."""
    from decimal import Decimal

    bot = await _bot_fichier(tmp_path)
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "111")
    await bot.store.ajouter_fourchette("petits", Decimal("0"), Decimal("1e12"))
    await bot.store.ajouter_salon_fourchette("petits", "222")
    await bot.store.maj_config(logs_salon_id="999")
    interaction = InteractionFactice()

    await _commande(bot, "config voir").callback(interaction)

    rendu = repr(interaction.embeds[0].to_dict())
    assert "grosses" in rendu and "petits" in rendu
    assert "111" in rendu and "222" in rendu
    assert "999" in rendu


async def test_config_voir_sans_fourchette(tmp_path):
    bot = await _bot_fichier(tmp_path)
    interaction = InteractionFactice()

    await _commande(bot, "config voir").callback(interaction)

    rendu = repr(interaction.embeds[0].to_dict())
    assert "non défini" in rendu or "aucun" in rendu.lower()


# --- /config fuseau ---------------------------------------------------------
#
# Le fuseau ne peut pas voyager avec l'heure d'une publication : il est commun
# aux deux, si bien que le régler depuis `/fourchette heure` déplacerait aussi le
# tableau des frais. Il lui faut donc sa propre commande.

async def test_config_fuseau_change_le_fuseau_sans_deplacer_les_heures(tmp_path):
    """Chaque publication garde l'heure qu'on lui a réglée.

    Le fuseau est le seul réglage partagé par les deux : le confondre avec une
    heure ferait sortir le tableau du soir à un autre moment que celui affiché.
    """
    bot = await _bot_fichier(tmp_path)
    await bot.store.maj_config(heure="09:00", filiales_heure="21:00")
    interaction = InteractionFactice()

    await _commande(bot, "config fuseau").callback(interaction, "America/New_York")

    config = await bot.store.config()
    assert config["fuseau"] == "America/New_York"
    assert config["heure"] == "09:00"
    assert await bot.store.heure_filiales() == "21:00"
    assert "✅" in interaction.textes[0]


async def test_config_fuseau_dit_l_heure_qu_il_est_pour_reperer_une_erreur(tmp_path):
    """Un fuseau valide mais faux ne se voit qu'à l'horloge.

    « ✅ America/New_York » n'apprend rien ; « il est 04:12 » se remarque tout de
    suite, au lieu d'attendre le post du lendemain.
    """
    from src.schedule import maintenant_local

    bot = await _bot_fichier(tmp_path)
    interaction = InteractionFactice()

    await _commande(bot, "config fuseau").callback(interaction, "Asia/Tokyo")

    assert maintenant_local("Asia/Tokyo").strftime("%H:%M") in interaction.textes[0]


async def test_config_fuseau_refuse_un_fuseau_inconnu(tmp_path):
    """Écrit tel quel, il ferait échouer chaque lecture de l'heure ensuite."""
    bot = await _bot_fichier(tmp_path)
    avant = (await bot.store.config())["fuseau"]
    interaction = InteractionFactice()

    await _commande(bot, "config fuseau").callback(interaction, "Mars/Olympus")

    assert (await bot.store.config())["fuseau"] == avant
    assert "❌" in interaction.textes[0]
    # Un exemple, sinon rien ne dit à quoi ressemble un nom accepté.
    assert "Europe/Paris" in interaction.textes[0]


async def test_config_fuseau_ne_consomme_pas_la_journee_des_publications(tmp_path):
    """Corriger l'horloge n'est pas demander un nouveau post.

    Effacer les marques ferait repartir les deux publications dans la minute —
    et il n'y aurait aucune raison d'en choisir une plutôt que l'autre.
    """
    bot = await _bot_fichier(tmp_path)
    await bot.store.marquer_publie("2026-08-19")
    await bot.store.marquer_publie_filiales("2026-08-19")
    interaction = InteractionFactice()

    await _commande(bot, "config fuseau").callback(interaction, "Europe/Lisbon")

    assert await bot.store.derniere_publication() == "2026-08-19"
    assert await bot.store.derniere_publication_filiales() == "2026-08-19"


# --- /template champs -------------------------------------------------------

async def test_template_champs_ne_propose_plus_les_marqueurs(tmp_path):
    """Proposer `{hors_fourchette}` serait proposer un placeholder qui ne rend
    plus rien."""
    bot = await _bot_fichier(tmp_path)
    interaction = InteractionFactice()

    await _commande(bot, "template champs").callback(interaction)

    rendu = repr(interaction.embeds[0].to_dict())
    assert "hors_fourchette" not in rendu
    assert "dans_fourchette" not in rendu
    assert "{ecart}" in rendu   # celui-ci reste utile
