"""Tests des fourchettes sous `/promos`, sans se connecter à Discord.

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


#: Le serveur où les commandes de ces tests sont tapées. Nommé plutôt qu'écrit
#: deux fois : chaque serveur a sa configuration, et une assertion qui irait
#: chercher un autre id lirait un tiroir vide en croyant constater une panne.
SERVEUR = 999


class ServeurFactice:
    def __init__(self, serveur_id: int = SERVEUR):
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


def _magasin(bot: EmpireBot):
    """La configuration du serveur où `InteractionFactice` tape ses commandes.

    Le montage et les assertions passent par elle, comme les commandes : réglé
    dans la configuration commune, rien ne serait lu.
    """
    return bot.store.pour(SERVEUR)


# --- Un seul mot pour les promotions ----------------------------------------


async def test_tout_ce_qui_touche_aux_promotions_est_sous_un_seul_mot():
    """`/fourchette` ne vit plus à côté de `/promos`.

    Une fourchette n'existe que pour découper les promotions : deux mots à la
    racine laissaient croire à deux sujets, et rien ne disait lequel réglait
    lequel. La recherche immédiate descend donc en `/promos chercher`, et tout le
    reste la rejoint.

    L'égalité est stricte : une sous-commande oubliée en route ne se remarquerait
    pas dans un « contient ».
    """
    bot = await _bot()

    racine = {commande.name for commande in bot.tree.get_commands()}
    sous_promos = {
        commande.qualified_name.removeprefix("promos ")
        for commande in _commande(bot, "promos").walk_commands()
    }

    assert "fourchette" not in racine
    assert sous_promos == {
        # La recherche, seule réponse publique du groupe.
        "chercher",
        # Les fourchettes elles-mêmes.
        "liste",
        "ajouter",
        "supprimer",
        "prix",
        "tolerance",
        # Combien de promotions au maximum, par fourchette.
        "plafond",
        "salon",
        "salon ajouter",
        "salon retirer",
        # Les types de bâtiments qu'on ne veut jamais voir sortir.
        "types",
        "types liste",
        "types exclure",
        "types remettre",
        # Le vocabulaire commun à toutes les publications.
        "heure",
        "apercu",
        "publier",
    }


async def test_chercher_dit_de_quoi_il_est_question():
    """Sous `/promos`, un mot nu ne dirait plus ce qu'il cherche.

    `/promos chercher` sans sa description se lirait « chercher quoi ? » — les
    fourchettes, les promotions, un salon. Elle doit nommer les fourchettes,
    puisque ce sont elles qui bornent la recherche quand on ne donne rien.
    """
    bot = await _bot()

    description = _commande(bot, "promos chercher").description

    assert "fourchette" in description.casefold()


async def test_les_commandes_de_fourchette_nomment_leur_cible():
    """Le paramètre s'appelle `fourchette` et non `nom`.

    Discord n'accepte que trois niveaux : `/promos fourchette salon ajouter` est
    impossible, donc les commandes restent à plat sous `/promos`. C'est alors le
    paramètre qui doit dire sur quoi on agit — `nom:grosses` sous `/promos` ne
    disait plus le nom de quoi.
    """
    bot = await _bot()

    for nom in ("ajouter", "supprimer", "prix", "tolerance", "plafond",
                "salon ajouter", "salon retirer"):
        parametres = _commande(bot, f"promos {nom}")._params
        assert "fourchette" in parametres, nom
        assert "nom" not in parametres, nom


# --- /promos ajouter --------------------------------------------------------


async def test_ajouter_confirme_avec_les_bornes_formatees():
    """Les montants sont relus dans la notation du jeu, pas en chiffres bruts.

    C'est ce qui permet de vérifier d'un coup d'œil que `100T` a bien été
    comprise comme 100 TØ.
    """
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "promos ajouter").callback(
        interaction, fourchette="grosses", min="100T", max="6P"
    )

    texte = " ".join(interaction.textes)
    assert "grosses" in texte
    assert "100.00" in texte and "6.00" in texte
    assert [f["nom"] for f in await _magasin(bot).fourchettes()] == ["grosses"]


async def test_ajouter_montant_illisible_refuse_sans_rien_creer():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "promos ajouter").callback(
        interaction, fourchette="grosses", min="beaucoup", max="6P"
    )

    assert "❌" in " ".join(interaction.textes)
    assert await _magasin(bot).fourchettes() == []


async def test_ajouter_nom_duplique_refuse_explicitement():
    """Le message doit dire *pourquoi*, sinon on croit à un bug."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos ajouter").callback(
        interaction, fourchette="grosses", min="0", max="1T"
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte and "existe déjà" in texte
    assert len(await _magasin(bot).fourchettes()) == 1


async def test_ajouter_nom_vide_refuse():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "promos ajouter").callback(
        interaction, fourchette="   ", min="0", max="1T"
    )

    assert "❌" in " ".join(interaction.textes)
    assert await _magasin(bot).fourchettes() == []


# --- /promos supprimer ------------------------------------------------------


async def test_supprimer_confirme():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos supprimer").callback(interaction, fourchette="grosses")

    assert "✅" in " ".join(interaction.textes)
    assert await _magasin(bot).fourchettes() == []


async def test_supprimer_inconnue_refuse_et_liste_les_noms():
    """Sans la liste, on ne saurait pas si c'est la casse ou une faute de frappe."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos supprimer").callback(interaction, fourchette="fantome")

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "grosses" in texte


# --- /promos prix -----------------------------------------------------------


async def test_prix_modifie_les_bornes():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos prix").callback(
        interaction, fourchette="grosses", min="0", max="1T"
    )

    assert "✅" in " ".join(interaction.textes)
    fourchette = (await _magasin(bot).fourchettes())[0]
    assert Decimal(fourchette["prix_max"]) == Decimal("1e12")


async def test_prix_sur_fourchette_inconnue_refuse():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "promos prix").callback(
        interaction, fourchette="fantome", min="0", max="1T"
    )

    assert "❌" in " ".join(interaction.textes)


# --- /promos salon ----------------------------------------------------------


async def test_salon_ajouter_confirme_et_attache():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos salon ajouter").callback(
        interaction, fourchette="grosses", salon=SalonFactice(111)
    )

    assert "✅" in " ".join(interaction.textes)
    assert (await _magasin(bot).fourchettes())[0]["salons"] == ["111"]


async def test_salon_ajouter_deux_fois_le_dit():
    """Un « ✅ » sur un ajout sans effet laisserait croire à un doublon créé."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await _magasin(bot).ajouter_salon_fourchette("grosses", "111")
    interaction = InteractionFactice()

    await _commande(bot, "promos salon ajouter").callback(
        interaction, fourchette="grosses", salon=SalonFactice(111)
    )

    texte = " ".join(interaction.textes)
    assert "déjà" in texte
    assert (await _magasin(bot).fourchettes())[0]["salons"] == ["111"]


async def test_salon_ajouter_sur_fourchette_inconnue_refuse():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "promos salon ajouter").callback(
        interaction, fourchette="fantome", salon=SalonFactice(111)
    )

    assert "❌" in " ".join(interaction.textes)


async def test_salon_ajouter_refuse_sans_permission_decrire():
    """Sinon l'erreur n'apparaîtrait qu'à 09:00 le lendemain."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos salon ajouter").callback(
        interaction,
        fourchette="grosses",
        salon=SalonFactice(111, peut_ecrire=False, peut_integrer=True),
    )

    assert "Envoyer des messages" in " ".join(interaction.textes)
    assert (await _magasin(bot).fourchettes())[0]["salons"] == []


async def test_salon_ajouter_refuse_sans_permission_dintegrer():
    """Le post est fait d'embeds : « Envoyer des messages » ne suffit pas.

    Testé à part : avec un salon qui refuse les deux, supprimer l'une des deux
    vérifications passerait inaperçu.
    """
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos salon ajouter").callback(
        interaction,
        fourchette="grosses",
        salon=SalonFactice(111, peut_ecrire=True, peut_integrer=False),
    )

    assert "Intégrer des liens" in " ".join(interaction.textes)
    assert (await _magasin(bot).fourchettes())[0]["salons"] == []


async def test_salon_retirer_detache():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await _magasin(bot).ajouter_salon_fourchette("grosses", "111")
    interaction = InteractionFactice()

    await _commande(bot, "promos salon retirer").callback(
        interaction, fourchette="grosses", salon=SalonFactice(111)
    )

    assert "✅" in " ".join(interaction.textes)
    assert (await _magasin(bot).fourchettes())[0]["salons"] == []


async def test_salon_retirer_absent_le_dit():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos salon retirer").callback(
        interaction, fourchette="grosses", salon=SalonFactice(111)
    )

    assert "❌" in " ".join(interaction.textes)


# --- /promos liste ----------------------------------------------------------


async def test_liste_montre_bornes_et_salons():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("grosses", Decimal("1e14"), Decimal("6e15"))
    await _magasin(bot).ajouter_salon_fourchette("grosses", "111")
    interaction = InteractionFactice()

    await _commande(bot, "promos liste").callback(interaction)

    embed = interaction.embeds[0]
    rendu = embed.description or "".join(c.value for c in embed.fields)
    assert "grosses" in rendu
    assert "100.00" in rendu
    assert "<#111>" in rendu


async def test_liste_signale_une_fourchette_sans_salon():
    """Elle ne publiera rien : ça doit se voir sans avoir à le déduire."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette("orpheline", Decimal("0"), Decimal("6e15"))
    interaction = InteractionFactice()

    await _commande(bot, "promos liste").callback(interaction)

    embed = interaction.embeds[0]
    rendu = embed.description or "".join(c.value for c in embed.fields)
    assert "⚠️" in rendu


async def test_liste_vide_explique_quoi_faire():
    bot = await _bot()
    interaction = InteractionFactice()

    await _commande(bot, "promos liste").callback(interaction)

    embed = interaction.embeds[0]
    rendu = embed.description or ""
    assert "/promos ajouter" in rendu


# --- Autocomplétion ---------------------------------------------------------


async def test_autocompletion_propose_les_fourchettes_existantes():
    """Sans elle, le nom serait retapé à la main à chaque commande."""
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette(
        "grosses-affaires", Decimal("1e14"), Decimal("6e15")
    )
    await _magasin(bot).ajouter_fourchette("petits-prix", Decimal("0"), Decimal("1e12"))

    commande = _commande(bot, "promos prix")
    choix = await commande._params["fourchette"].autocomplete(InteractionFactice(), "")

    assert {c.value for c in choix} == {"grosses-affaires", "petits-prix"}


async def test_autocompletion_filtre_sur_la_saisie():
    bot = await _bot()
    await _magasin(bot).ajouter_fourchette(
        "grosses-affaires", Decimal("1e14"), Decimal("6e15")
    )
    await _magasin(bot).ajouter_fourchette("petits-prix", Decimal("0"), Decimal("1e12"))

    commande = _commande(bot, "promos prix")
    choix = await commande._params["fourchette"].autocomplete(InteractionFactice(), "pet")

    assert [c.value for c in choix] == ["petits-prix"]


# --- Le vocabulaire commun greffé sur /promos -------------------------------


async def test_fourchette_recoit_les_mots_communs_aux_publications():
    """Les mêmes qu'ailleurs : `/frais heure` et `/promos heure` s'écrivent
    pareil, et le module qui ajoutera une troisième publication héritera de ces
    mots sans en inventer.

    Ce que ces commandes *font* est éprouvé dans
    `tests/test_commandes_publication.py`, sur une publication d'essai. Ici on
    vérifie seulement qu'elles sont bien greffées sur les promotions.
    """
    bot = await _bot()
    noms = {commande.qualified_name for commande in bot.tree.walk_commands()}

    assert "promos heure" in noms
    assert "promos apercu" in noms
    assert "promos publier" in noms


async def test_les_salons_d_une_fourchette_restent_ceux_de_la_fourchette():
    """Le `salon ajouter` générique ne doit pas s'installer sur `/promos`.

    Les salons des promotions appartiennent à une fourchette **nommée**, pas à la
    publication : greffé ici, le générique porterait le même nom en écrivant dans
    une autre liste, et la fourchette ne partirait nulle part malgré son « ✅ ».
    Le nom de la fourchette est donc obligatoire — c'est ce qui distingue les
    deux commandes.
    """
    bot = await _bot()

    parametres = _commande(bot, "promos salon ajouter")._params
    assert "fourchette" in parametres
    assert parametres["fourchette"].required


# --- Les anciennes commandes ont disparu ------------------------------------


async def test_apercu_n_est_plus_une_commande_a_part():
    """Prévisualiser les promotions se dit maintenant `/promos apercu`.

    Un `/apercu` nu ne pourrait plus dire de quelle publication il parle, alors
    que le bot en a deux et pourra en avoir plus.
    """
    bot = await _bot()
    noms = {commande.qualified_name for commande in bot.tree.walk_commands()}

    assert "apercu" not in noms


async def test_config_heure_a_disparu_au_profit_de_fourchette_heure():
    """Elle réglait l'heure d'**une** des deux publications sous un nom qui ne le
    disait pas — celle des promotions, sans jamais nommer les promotions."""
    bot = await _bot()
    noms = {commande.qualified_name for commande in bot.tree.walk_commands()}

    assert "config heure" not in noms


async def test_config_retester_a_disparu():
    """Doublon de `/source tester`, et son nom promettait autre chose.

    Elle effaçait la marque du jour des promotions seules : sur un bot à deux
    publications, « retester » ne peut plus désigner l'une sans le dire.
    """
    bot = await _bot()
    noms = {commande.qualified_name for commande in bot.tree.walk_commands()}

    assert "config retester" not in noms


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
