"""Tests du groupe `/fourchette`, exécutés sans se connecter à Discord.

Ce qui se vérifie ici n'est pas le stockage (couvert par
`tests/test_fourchettes.py`) mais **ce que voit l'utilisateur** : le message de
confirmation, le refus explicite d'un nom inconnu, et la liste. Une commande qui
écrit correctement mais répond « ✅ » sur une fourchette inexistante serait le
pire des deux mondes.
"""

from decimal import Decimal

import pytest

from src.bot import EmpireBot
from src.db import Store


class Permissions:
    def __init__(self, administrator: bool):
        self.administrator = administrator


class Utilisateur:
    def __init__(self, admin: bool = True, membre_id: int = 1):
        self.id = membre_id
        self.guild_permissions = Permissions(admin)


class Reponse:
    def __init__(self):
        self.messages: list[dict] = []
        self.differee = False

    async def defer(self, ephemeral: bool = False) -> None:
        self.differee = True

    async def send_message(self, contenu=None, **options) -> None:
        self.messages.append({"contenu": contenu, **options})


class Followup:
    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, contenu=None, **options) -> None:
        self.messages.append({"contenu": contenu, **options})


class SalonFactice:
    """Un salon Discord vu par une commande : un id, une mention, des droits.

    `permissions_for` fait partie de l'interface de `discord.TextChannel` : sans
    elle, la vérification faite à l'attachement ne serait jamais exercée ici.
    """

    def __init__(
        self,
        salon_id: int,
        peut_ecrire: bool = True,
        peut_integrer: bool | None = None,
    ):
        self.id = salon_id
        self.name = f"salon-{salon_id}"
        self.mention = f"<#{salon_id}>"
        self._peut_ecrire = peut_ecrire
        self._peut_integrer = peut_ecrire if peut_integrer is None else peut_integrer

    def permissions_for(self, _membre):
        class Permissions:
            send_messages = self._peut_ecrire
            embed_links = self._peut_integrer

        return Permissions()


class ServeurFactice:
    def __init__(self, serveur_id: int = 999):
        self.id = serveur_id
        self.name = f"Serveur {serveur_id}"


class InteractionFactice:
    def __init__(self, admin: bool = True, membre_id: int = 1):
        self.user = Utilisateur(admin, membre_id)
        self.response = Reponse()
        self.followup = Followup()
        self.guild = ServeurFactice()

    @property
    def embeds(self) -> list:
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


class SourceFactice:
    async def fetch(self) -> str:
        return ""


def _commande(bot: EmpireBot, nom: str):
    for commande in bot.tree.walk_commands():
        if commande.qualified_name == nom:
            return commande
    raise AssertionError(f"commande introuvable : {nom}")


async def _bot() -> EmpireBot:
    store = Store(dsn="")
    await store.connect()
    return EmpireBot(store, SourceFactice())


# --- /fourchette ajouter ----------------------------------------------------


async def test_ajouter_confirme_avec_les_bornes_formatees():
    """Les montants sont relus dans la notation du jeu, pas en chiffres bruts.

    C'est ce qui permet de vérifier d'un coup d'œil que `100T` a bien été
    comprise comme 100 TØ.
    """
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "fourchette ajouter").callback(
        interaction, nom="grosses", min="100T", max="6P"
    )

    texte = " ".join(interaction.textes)
    assert "grosses" in texte
    assert "100.00" in texte and "6.00" in texte
    assert [f["nom"] for f in await bot.store.fourchettes()] == ["grosses"]


async def test_ajouter_montant_illisible_refuse_sans_rien_creer():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "fourchette ajouter").callback(
        interaction, nom="grosses", min="beaucoup", max="6P"
    )

    assert "❌" in " ".join(interaction.textes)
    assert await bot.store.fourchettes() == []


async def test_ajouter_nom_duplique_refuse_explicitement():
    """Le message doit dire *pourquoi*, sinon on croit à un bug."""
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "fourchette ajouter").callback(
        interaction, nom="grosses", min="0", max="1T"
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte and "existe déjà" in texte
    assert len(await bot.store.fourchettes()) == 1


async def test_ajouter_nom_vide_refuse():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "fourchette ajouter").callback(
        interaction, nom="   ", min="0", max="1T"
    )

    assert "❌" in " ".join(interaction.textes)
    assert await bot.store.fourchettes() == []


# --- /fourchette supprimer --------------------------------------------------


async def test_supprimer_confirme():
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "fourchette supprimer").callback(interaction, nom="grosses")

    assert "✅" in " ".join(interaction.textes)
    assert await bot.store.fourchettes() == []


async def test_supprimer_inconnue_refuse_et_liste_les_noms():
    """Sans la liste, on ne saurait pas si c'est la casse ou une faute de frappe."""
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "fourchette supprimer").callback(interaction, nom="fantome")

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "grosses" in texte


# --- /fourchette prix -------------------------------------------------------


async def test_prix_modifie_les_bornes():
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "fourchette prix").callback(
        interaction, nom="grosses", min="0", max="1T"
    )

    assert "✅" in " ".join(interaction.textes)
    fourchette = (await bot.store.fourchettes())[0]
    assert Decimal(fourchette["prix_max"]) == Decimal("1e12")


async def test_prix_sur_fourchette_inconnue_refuse():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "fourchette prix").callback(
        interaction, nom="fantome", min="0", max="1T"
    )

    assert "❌" in " ".join(interaction.textes)


# --- /fourchette salon ------------------------------------------------------


async def test_salon_ajouter_confirme_et_attache():
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "fourchette salon ajouter").callback(
        interaction, nom="grosses", salon=SalonFactice(111)
    )

    assert "✅" in " ".join(interaction.textes)
    assert (await bot.store.fourchettes())[0]["salons"] == ["111"]


async def test_salon_ajouter_deux_fois_le_dit():
    """Un « ✅ » sur un ajout sans effet laisserait croire à un doublon créé."""
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "111")
    interaction = InteractionFactice()

    await _commande(bot, "fourchette salon ajouter").callback(
        interaction, nom="grosses", salon=SalonFactice(111)
    )

    texte = " ".join(interaction.textes)
    assert "déjà" in texte
    assert (await bot.store.fourchettes())[0]["salons"] == ["111"]


async def test_salon_ajouter_sur_fourchette_inconnue_refuse():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "fourchette salon ajouter").callback(
        interaction, nom="fantome", salon=SalonFactice(111)
    )

    assert "❌" in " ".join(interaction.textes)


async def test_salon_ajouter_refuse_sans_permission_decrire():
    """Sinon l'erreur n'apparaîtrait qu'à 09:00 le lendemain."""
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "fourchette salon ajouter").callback(
        interaction,
        nom="grosses",
        salon=SalonFactice(111, peut_ecrire=False, peut_integrer=True),
    )

    assert "Envoyer des messages" in " ".join(interaction.textes)
    assert (await bot.store.fourchettes())[0]["salons"] == []


async def test_salon_ajouter_refuse_sans_permission_dintegrer():
    """Le post est fait d'embeds : « Envoyer des messages » ne suffit pas.

    Testé à part : avec un salon qui refuse les deux, supprimer l'une des deux
    vérifications passerait inaperçu.
    """
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "fourchette salon ajouter").callback(
        interaction,
        nom="grosses",
        salon=SalonFactice(111, peut_ecrire=True, peut_integrer=False),
    )

    assert "Intégrer des liens" in " ".join(interaction.textes)
    assert (await bot.store.fourchettes())[0]["salons"] == []


async def test_salon_retirer_detache():
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "111")
    interaction = InteractionFactice()

    await _commande(bot, "fourchette salon retirer").callback(
        interaction, nom="grosses", salon=SalonFactice(111)
    )

    assert "✅" in " ".join(interaction.textes)
    assert (await bot.store.fourchettes())[0]["salons"] == []


async def test_salon_retirer_absent_le_dit():
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "fourchette salon retirer").callback(
        interaction, nom="grosses", salon=SalonFactice(111)
    )

    assert "❌" in " ".join(interaction.textes)


# --- /fourchette liste ------------------------------------------------------


async def test_liste_montre_bornes_et_salons():
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("grosses", "111")
    interaction = InteractionFactice()

    await _commande(bot, "fourchette liste").callback(interaction)

    embed = interaction.embeds[0]
    rendu = embed.description or "".join(c.value for c in embed.fields)
    assert "grosses" in rendu
    assert "100.00" in rendu
    assert "<#111>" in rendu


async def test_liste_signale_une_fourchette_sans_salon():
    """Elle ne publiera rien : ça doit se voir sans avoir à le déduire."""
    bot = await _bot()
    await bot.store.ajouter_fourchette("orpheline", Decimal("0"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "fourchette liste").callback(interaction)

    embed = interaction.embeds[0]
    rendu = embed.description or "".join(c.value for c in embed.fields)
    assert "⚠️" in rendu


async def test_liste_vide_explique_quoi_faire():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "fourchette liste").callback(interaction)

    embed = interaction.embeds[0]
    rendu = embed.description or ""
    assert "/fourchette ajouter" in rendu


# --- Autocomplétion ---------------------------------------------------------


async def test_autocompletion_propose_les_fourchettes_existantes():
    """Sans elle, le nom serait retapé à la main à chaque commande."""
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses-affaires", Decimal("1e14"), Decimal("6e15"))
    await bot.store.ajouter_fourchette("petits-prix", Decimal("0"), Decimal("1e12"))

    commande = _commande(bot, "fourchette prix")
    choix = await commande._params["nom"].autocomplete(InteractionFactice(), "")

    assert {c.value for c in choix} == {"grosses-affaires", "petits-prix"}


async def test_autocompletion_filtre_sur_la_saisie():
    bot = await _bot()
    await bot.store.ajouter_fourchette("grosses-affaires", Decimal("1e14"), Decimal("6e15"))
    await bot.store.ajouter_fourchette("petits-prix", Decimal("0"), Decimal("1e12"))

    commande = _commande(bot, "fourchette prix")
    choix = await commande._params["nom"].autocomplete(InteractionFactice(), "pet")

    assert [c.value for c in choix] == ["petits-prix"]


# --- Les anciennes commandes ont disparu ------------------------------------


async def test_config_prix_et_config_salon_ont_disparu():
    """Les garder agirait sur une fourchette implicite.

    C'est exactement l'ambiguïté qui fait publier au mauvais endroit : mieux
    vaut une commande absente qu'une commande dont la cible se devine.
    """
    bot = await _bot()
    noms = {commande.qualified_name for commande in bot.tree.walk_commands()}

    assert "config prix" not in noms
    assert "config salon ajouter" not in noms
    assert "config salon retirer" not in noms
