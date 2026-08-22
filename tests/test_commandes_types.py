"""`/promos types` : écarter un type de bâtiment, et le rendre.

Le cœur sait sélectionner sans un type (`tests/test_types_exclus.py`) et la base
sait le retenir par serveur (`tests/test_types_par_serveur.py`). Il manque la
porte : sans commande, la seule façon d'écarter un type serait d'écrire dans la
base à la main.

Trois commandes, et trois refus. `liste` montre ce qui est écarté **et ce qui
reste**, sans quoi elle ne répondrait pas à la question qu'on lui pose : qu'est-ce
qui va sortir ce soir ? `exclure` refuse un type que l'export ne contient pas —
accepté en silence, il donnerait un filtre qui ne filtre rien, et l'on chercherait
la panne du côté du bot. Et il refuse d'écarter le dernier type restant : un post
vide tous les soirs ressemble trait pour trait à une panne.

`remettre` ne charge pas l'export. Défaire un réglage ne doit pas dépendre de
l'API du jeu : le jour où elle tombe est précisément celui où l'on veut tout
remettre pour voir.
"""

from decimal import Decimal

from src.bot import EmpireBot
from src.db import Store
from src.promos import parse_csv
from src.source import SourceError

from tests.test_commandes_fourchettes import _commande
from tests.test_commandes_par_serveur import EMPIRE, VOISIN, _interaction, _propositions

#: Un export à quatre types, dont trois en promotion. Quatre parce que le refus
#: d'écarter le dernier type demande d'en écarter plusieurs avant, et que le
#: compte doit se lire dans le test plutôt que se déduire.
CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,500,0,0,0,17,0,0,0
transport,"Gare de fret",0,400,0,0,0,17,0,0,0
bureaux,"Local",0,300,0,0,0,17,0,0,0
commerciaux,"Boutique",0,200,0,0,0,0,0,0,0
"""

#: Les types de cet export, dans l'ordre où les propositions les rendent.
TYPES = ("bureaux", "commerciaux", "transport", "zones")


class SourcePleine:
    """La source du jeu, réduite à un export à quatre types."""

    async def fetch(self) -> str:
        return CSV


class SourceEnPanne:
    """L'API du jeu injoignable, telle que `src.source` la signale."""

    async def fetch(self) -> str:
        raise SourceError("API du jeu injoignable (503)")


async def _bot(source=None) -> EmpireBot:
    store = Store(dsn="")
    await store.connect()
    return EmpireBot(store, source or SourcePleine())


def _magasin(bot: EmpireBot, serveur_id: int = EMPIRE):
    return bot.store.pour(serveur_id)


def _lignes(embed) -> str:
    """Tout le texte de l'embed, champs compris.

    Le découpage en champs n'est qu'une question de place à l'écran : une
    assertion qui en dépendrait casserait au premier champ renommé.
    """
    return " ".join(
        [embed.title or "", embed.description or ""]
        + [f"{champ.name} {champ.value}" for champ in embed.fields]
    )


# --- /promos types liste ----------------------------------------------------


async def test_la_liste_cite_les_types_de_lexport():
    """La liste vient des données : un type ajouté par le jeu doit s'y voir sans
    qu'on touche au code."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types liste").callback(interaction)

    rendu = _lignes(interaction.embeds[0])
    for nom in TYPES:
        assert nom in rendu, rendu


async def test_la_liste_cite_les_types_hors_promotion():
    """`commerciaux` n'a aucune promotion dans cet export.

    Réduite aux types en promotion du jour, la liste ne proposerait un type que
    les jours où il s'en trouve un en promotion — donc pas le jour où l'on veut
    l'écarter.
    """
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types liste").callback(interaction)

    assert "commerciaux" in _lignes(interaction.embeds[0])


async def test_la_liste_dit_lequel_est_ecarte():
    """Sans l'état, la liste ne dirait pas ce qui va sortir ce soir."""
    bot = await _bot()
    await _magasin(bot).exclure_type("transport")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types liste").callback(interaction)

    lignes = _lignes(interaction.embeds[0]).splitlines()
    ecarte = next(ligne for ligne in lignes if "transport" in ligne)
    garde = next(ligne for ligne in lignes if "zones" in ligne)
    assert "écarté" in ecarte.casefold()
    assert "écarté" not in garde.casefold()


async def test_la_liste_dun_serveur_neuf_le_dit():
    """Un vide muet se lirait comme une liste qui n'a pas su se charger."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types liste").callback(interaction)

    rendu = _lignes(interaction.embeds[0]).casefold()
    assert "aucun" in rendu


async def test_la_liste_ignore_les_exclusions_du_voisin():
    """Montrer l'état d'un autre serveur ferait chercher ici la panne d'ailleurs.
    """
    bot = await _bot()
    await _magasin(bot, VOISIN).exclure_type("transport")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types liste").callback(interaction)

    # Sur la ligne du type, et non dans tout l'embed : le récapitulatif porte le
    # mot « écartés » dans son titre même quand il n'y en a aucun.
    lignes = _lignes(interaction.embeds[0]).splitlines()
    garde = next(ligne for ligne in lignes if "transport" in ligne)
    assert "écarté" not in garde.casefold()


async def test_la_liste_nomme_un_type_ecarte_disparu_de_lexport():
    """Le monde change, le goût non : le réglage reste, et se signale.

    Effacé, il reviendrait sans bruit si le jeu rendait le type ; caché, on
    croirait le post filtré par autre chose.
    """
    bot = await _bot()
    await _magasin(bot).exclure_type("aeroports")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types liste").callback(interaction)

    rendu = _lignes(interaction.embeds[0])
    assert "aeroports" in rendu
    assert "export" in rendu.casefold()


async def test_la_liste_se_rabat_sur_les_types_connus_si_lexport_est_illisible():
    """L'API du jeu tombe : la liste doit encore dire ce qui est écarté.

    Un ❌ sec laisserait croire que le réglage est perdu, alors qu'il est en base
    et continue de filtrer. Les types mémorisés au dernier chargement suffisent à
    répondre, et le message dit d'où ils viennent.
    """
    bot = await _bot(SourceEnPanne())
    await bot.store.memoriser_types(["zones", "transport"])
    await _magasin(bot).exclure_type("transport")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types liste").callback(interaction)

    rendu = _lignes(interaction.embeds[0])
    assert "transport" in rendu and "zones" in rendu
    assert "⚠️" in rendu


async def test_la_liste_memorise_les_types_de_lexport():
    """Ce qui remplit les propositions. Sans cette mémoire, un serveur neuf
    n'aurait rien sous le curseur et il faudrait taper les noms de mémoire."""
    bot = await _bot()

    await _commande(bot, "promos types liste").callback(_interaction(EMPIRE))

    assert await bot.store.types_connus() == list(TYPES)


# --- /promos types exclure --------------------------------------------------


async def test_exclure_ecarte_le_type_dans_ce_serveur_seulement():
    """Tout l'intérêt du réglage : une entreprise sans transport, sans l'enlever
    aux autres."""
    bot = await _bot()

    await _commande(bot, "promos types exclure").callback(
        _interaction(EMPIRE), type="transport"
    )

    assert await _magasin(bot).types_exclus() == ["transport"]
    assert await _magasin(bot, VOISIN).types_exclus() == []
    assert await bot.store.types_exclus() == []


async def test_exclure_previent_que_les_posts_maigrissent():
    """C'est la conséquence qu'on ne voit pas en tapant : le post du soir perd des
    promotions, et peut n'en avoir plus aucune. Un « ✅ » sec la laisserait
    découvrir le lendemain."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types exclure").callback(
        interaction, type="transport"
    )

    texte = " ".join(interaction.textes)
    assert "✅" in texte
    assert "transport" in texte
    assert "promo" in texte.casefold(), texte


async def test_exclure_un_type_deja_ecarte_le_dit():
    """Un « ✅ » ferait croire qu'on vient de changer quelque chose, et chercher
    ailleurs la raison d'un post inchangé."""
    bot = await _bot()
    await _magasin(bot).exclure_type("transport")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types exclure").callback(
        interaction, type="transport"
    )

    texte = " ".join(interaction.textes)
    assert "ℹ️" in texte and "déjà" in texte


async def test_exclure_un_type_inconnu_refuse_en_citant_les_vrais():
    """Le jeu écrit `zones` et `bureaux` au pluriel, et l'on tape le singulier.

    Accepté, `zone` n'écarterait rien : une exclusion silencieuse, donc le pire
    des cas — le post sort inchangé et rien ne dit pourquoi. Le refus doit citer
    les vrais noms, sans quoi il faudrait les deviner.
    """
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types exclure").callback(interaction, type="zone")

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    for nom in TYPES:
        assert nom in texte, texte
    assert await _magasin(bot).types_exclus() == []


async def test_exclure_accepte_la_casse_et_les_espaces():
    """Le nom arrive parfois d'un copier-coller. Refuser « Transport » en citant
    `transport` juste à côté serait une énigme."""
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types exclure").callback(
        interaction, type=" Transport "
    )

    assert "✅" in " ".join(interaction.textes)
    assert await _magasin(bot).types_exclus() == ["transport"]


async def test_exclure_refuse_decarter_le_dernier_type():
    """Le refus jumeau de celui des modules : un post vide tous les soirs ne se
    distingue pas d'une panne du bot, et rien à l'écran ne dirait que c'est un
    réglage."""
    bot = await _bot()
    magasin = _magasin(bot)
    for nom in TYPES:
        if nom != "zones":
            await magasin.exclure_type(nom)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types exclure").callback(interaction, type="zones")

    assert "❌" in " ".join(interaction.textes)
    assert "zones" not in await magasin.types_exclus()


async def test_le_dernier_type_se_compte_sur_lexport_et_non_sur_les_ecartes():
    """Un type écarté disparu du jeu pèse encore dans la liste des écartés.

    Compté là, il ferait refuser le réglage en annonçant un dernier type alors
    qu'il en reste deux — et le refus serait incompréhensible, puisque
    `/promos types liste` montre bien les types restants.
    """
    bot = await _bot()
    magasin = _magasin(bot)
    for nom in ("aeroports", "bureaux", "commerciaux"):
        await magasin.exclure_type(nom)
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types exclure").callback(
        interaction, type="transport"
    )

    assert "✅" in " ".join(interaction.textes)
    assert "transport" in await magasin.types_exclus()


async def test_exclure_valide_sur_les_types_connus_si_lexport_est_illisible():
    """L'API du jeu tombe, le réglage doit rester possible.

    Les types mémorisés au dernier chargement font foi : refuser tout net
    obligerait à attendre que le jeu revienne pour taire un type qui pollue les
    posts, ce qui est justement le moment où l'on veut le taire.
    """
    bot = await _bot(SourceEnPanne())
    await bot.store.memoriser_types(list(TYPES))
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types exclure").callback(
        interaction, type="transport"
    )

    assert "✅" in " ".join(interaction.textes)
    assert await _magasin(bot).types_exclus() == ["transport"]


async def test_exclure_refuse_quand_rien_ne_permet_de_valider():
    """Export illisible et aucun type mémorisé : il n'y a rien contre quoi
    vérifier le nom. Écrire quand même laisserait un filtre inerte dans la
    config, et le refus doit dire que c'est l'export qui manque."""
    bot = await _bot(SourceEnPanne())
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types exclure").callback(
        interaction, type="transport"
    )

    texte = " ".join(interaction.textes)
    assert "❌" in texte
    assert "export" in texte.casefold()
    assert await _magasin(bot).types_exclus() == []


# --- /promos types remettre -------------------------------------------------


async def test_remettre_rend_le_type_dans_ce_serveur():
    bot = await _bot()
    magasin = _magasin(bot)
    await magasin.exclure_type("transport")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types remettre").callback(
        interaction, type="transport"
    )

    assert "✅" in " ".join(interaction.textes)
    assert await magasin.types_exclus() == []


async def test_remettre_un_type_qui_nest_pas_ecarte_le_dit():
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types remettre").callback(
        interaction, type="transport"
    )

    texte = " ".join(interaction.textes)
    assert "ℹ️" in texte


async def test_remettre_marche_sans_lexport():
    """Défaire un réglage ne doit pas dépendre de l'API du jeu : le jour où elle
    tombe est celui où l'on veut tout remettre pour voir.

    C'est aussi le seul chemin qui rende un type disparu de l'export, qu'aucun
    chargement ne pourrait plus valider.
    """
    bot = await _bot(SourceEnPanne())
    magasin = _magasin(bot)
    await magasin.exclure_type("aeroports")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "promos types remettre").callback(
        interaction, type="aeroports"
    )

    assert "✅" in " ".join(interaction.textes)
    assert await magasin.types_exclus() == []


# --- Les propositions : celles que la commande accepte ----------------------


async def test_exclure_ne_propose_que_les_types_gardes():
    """Proposer un type déjà écarté ferait choisir un nom pour s'entendre
    répondre qu'il n'y avait rien à faire."""
    bot = await _bot()
    await _magasin(bot).exclure_type("transport")
    await bot.store.memoriser_types(list(TYPES))
    commande = _commande(bot, "promos types exclure")

    choix = await _propositions(commande, "type")(_interaction(EMPIRE), "")

    assert [c.value for c in choix] == ["bureaux", "commerciaux", "zones"]


async def test_remettre_ne_propose_que_les_types_ecartes():
    bot = await _bot()
    await _magasin(bot).exclure_type("transport")
    await bot.store.memoriser_types(list(TYPES))
    commande = _commande(bot, "promos types remettre")

    choix = await _propositions(commande, "type")(_interaction(EMPIRE), "")

    assert [c.value for c in choix] == ["transport"]


async def test_les_propositions_sont_celles_de_ce_serveur():
    """Lues dans la configuration commune, elles proposeraient à une entreprise
    de rendre ce que l'autre a écarté."""
    bot = await _bot()
    await _magasin(bot, VOISIN).exclure_type("transport")
    await bot.store.memoriser_types(list(TYPES))
    commande = _commande(bot, "promos types remettre")

    choix = await _propositions(commande, "type")(_interaction(EMPIRE), "")

    assert choix == []


async def test_les_propositions_nappellent_pas_lapi_du_jeu():
    """Discord n'accorde que trois secondes à une frappe, et l'export pèse
    plusieurs centaines de lignes : le charger à chaque lettre tapée ferait
    tomber l'autocomplétion, et multiplierait les appels à l'API du jeu."""
    bot = await _bot(SourceEnPanne())
    await bot.store.memoriser_types(list(TYPES))
    commande = _commande(bot, "promos types exclure")

    choix = await _propositions(commande, "type")(_interaction(EMPIRE), "")

    assert [c.value for c in choix] == list(TYPES)


async def test_les_propositions_filtrent_sur_la_saisie():
    """Quatre types tiennent à l'écran, mais le filtre est ce qui rend la liste
    utilisable quand le jeu en ajoutera — et il ignore la casse, comme le reste
    du réglage."""
    bot = await _bot()
    await bot.store.memoriser_types(list(TYPES))
    commande = _commande(bot, "promos types exclure")

    choix = await _propositions(commande, "type")(_interaction(EMPIRE), "TRANS")

    assert [c.value for c in choix] == ["transport"]


# --- Ce que l'exclusion change à l'écran ------------------------------------


async def test_la_recherche_nemontre_pas_un_type_ecarte():
    """Le réglage n'a de sens que là : ce qu'on voit en tapant doit être ce qui
    sortira ce soir. Filtré à la seule publication, l'aperçu mentirait."""
    bot = await _bot()
    magasin = _magasin(bot)
    await magasin.exclure_type("transport")

    embeds, _, _ = await bot.construire_publication(
        Decimal(0), Decimal("1e12"), magasin=magasin, donnees=parse_csv(CSV)
    )

    rendu = " ".join(str(embed) for embed in embeds)
    assert "Technopôle" in rendu
    assert "Gare de fret" not in rendu


async def test_le_voisin_voit_encore_le_type_ecarte_ici():
    """L'exclusion est propre au serveur, comme le reste de sa configuration."""
    bot = await _bot()
    await _magasin(bot).exclure_type("transport")

    embeds, _, _ = await bot.construire_publication(
        Decimal(0),
        Decimal("1e12"),
        magasin=_magasin(bot, VOISIN),
        donnees=parse_csv(CSV),
    )

    assert "Gare de fret" in " ".join(str(embed) for embed in embeds)


async def test_tout_ecarter_donne_le_message_de_repli():
    """Le choix assumé : pas de repêchage de ce qu'on a écarté.

    Le post ne sort donc pas, et c'est le message habituel qui explique pourquoi
    — celui qu'on lit déjà les jours sans promotion.
    """
    bot = await _bot()
    magasin = _magasin(bot)
    # Écrit d'un bloc : la commande refuse le dernier, et c'est justement l'état
    # qu'aucune commande ne peut produire qui doit rester lisible.
    await magasin.maj_config(types_exclus=list(TYPES))

    embeds, _, repli = await bot.construire_publication(
        Decimal(0), Decimal("1e12"), magasin=magasin, donnees=parse_csv(CSV)
    )

    assert embeds == []
    assert repli


async def test_la_configuration_commune_nen_ecarte_aucun():
    """Le site de contrôle lit le commun, faute de dire de quel serveur il parle.
    Une exclusion écrite là filtrerait pour tous les serveurs à la fois."""
    bot = await _bot()
    await _magasin(bot).exclure_type("transport")

    embeds, _, _ = await bot.construire_publication(
        Decimal(0), Decimal("1e12"), donnees=parse_csv(CSV)
    )

    assert "Gare de fret" in " ".join(str(embed) for embed in embeds)


# --- Ce que /reglages voir en dit -------------------------------------------


async def test_reglages_voir_nomme_les_types_ecartes():
    """La question qu'on lui pose est « pourquoi le post est-il si court ? ».

    Le filtre n'est visible que sous `/promos types liste`, un endroit où l'on ne
    va pas quand on cherche une panne : il faut qu'il se lise là où l'on relit
    toute la configuration du serveur.
    """
    bot = await _bot()
    await _magasin(bot).exclure_type("transport")
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages voir").callback(interaction)

    assert "transport" in _lignes(interaction.embeds[0])


async def test_reglages_voir_se_tait_quand_rien_nest_ecarte():
    """Rien d'écarté est le cas de tous les serveurs sauf ceux qui l'ont voulu.

    Un champ « *aucun* » de plus dans un embed déjà long ferait chercher un
    réglage là où il n'y a que le défaut.
    """
    bot = await _bot()
    interaction = _interaction(EMPIRE)

    await _commande(bot, "reglages voir").callback(interaction)

    assert "écart" not in _lignes(interaction.embeds[0]).casefold()


# --- Ce que le chargement mémorise ------------------------------------------


async def test_un_chargement_memorise_les_types():
    """Écrits à chaque chargement, comme les noms de salons se corrigent au
    premier post : c'est ce qui fait suivre un type ajouté par le jeu."""
    bot = await _bot()

    await bot.charger()

    assert await bot.store.types_connus() == list(TYPES)


async def test_un_export_illisible_ne_casse_pas_la_memoire():
    """La mémoire des types est un cache : elle ne doit jamais empêcher un post.

    Une base indisponible pendant la publication ferait sauter le post du soir
    pour une liste de propositions.
    """
    bot = await _bot()

    async def set_qui_tombe(*_args, **_kwargs):
        raise RuntimeError("base injoignable")

    bot.store.memoriser_types = set_qui_tombe

    _, batiments = await bot.charger()

    assert batiments
