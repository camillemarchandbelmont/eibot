"""La page web des frais de gestion : coller le tableau du jeu, récupérer les frais.

Le tour de main que la page remplace : ouvrir le jeu, lire treize filiales, taper
treize `/filiales releve` dans Discord, puis `/filiales export`. Ici, on
sélectionne le tableau du jeu, on le colle, et on repart avec les deux colonnes
que l'import du jeu réclame.

Ce que ces tests tiennent :

- la page est **ouverte** : convertir ne demande rien et n'écrit rien. Une page
  qui écrirait en base sans mot de passe laisserait quiconque connaît l'URL
  remplacer les relevés du jour ;
- ce qu'on colle est du texte quelconque, donc **échappé** : le collage revient
  dans la page, et un `<script>` recopié tel quel s'y exécuterait ;
- le bloc à copier porte les **tabulations** et les chiffres exacts. C'est une
  entrée machine : un arrondi ou un espace de milliers, et le jeu refuse la ligne.
"""

from decimal import Decimal
from http.cookies import SimpleCookie

from aiohttp.test_utils import TestClient, TestServer

from src.db import Store
from src.web import creer_app

from tests.test_collage import COLLAGE

#: Les deux entreprises, comme dans les autres tests multi-serveurs.
EMPIRE = (111, "Empire Immo")
VOISIN = (222, "Groupe Nord")


class ServeurFactice:
    def __init__(self, serveur_id: int, nom: str):
        self.id = serveur_id
        self.name = nom


class BotFactice:
    """Le strict nécessaire : les serveurs du bot et son magasin."""

    def __init__(self, store, serveurs=(EMPIRE, VOISIN)):
        self.store = store
        self.guilds = [ServeurFactice(*serveur) for serveur in serveurs]

    def is_ready(self) -> bool:
        return True


async def _client(serveurs=(EMPIRE, VOISIN)) -> tuple[TestClient, BotFactice]:
    store = Store(dsn="")
    await store.connect()
    bot = BotFactice(store, serveurs)
    client = TestClient(TestServer(creer_app(bot)))
    await client.start_server()
    return client, bot


async def _convertir(client, collage: str, serveur: int = EMPIRE[0]) -> str:
    reponse = await client.post(
        "/frais", data={"collage": collage, "serveur": str(serveur)}
    )
    assert reponse.status == 200
    return await reponse.text()


async def _enregistrer(
    client,
    collage: str,
    serveur: int = EMPIRE[0],
    motdepasse: str = "",
    cookie: str = "",
):
    """Un clic sur « Enregistrer », mot de passe ou cookie à l'appui.

    Le cookie est passé à la main : le bocal du client de test refuse ceux d'un
    hôte en adresse IP, et le navigateur, lui, les renvoie.
    """
    return await client.post(
        "/frais/enregistrer",
        data={
            "collage": collage,
            "serveur": str(serveur),
            "motdepasse": motdepasse,
        },
        headers={"Cookie": cookie} if cookie else {},
    )


def _cookie(reponse) -> str:
    """Ce que le navigateur renverrait au coup d'après."""
    bocal = SimpleCookie()
    for entete in reponse.headers.getall("Set-Cookie", []):
        bocal.load(entete)
    return "; ".join(f"{nom}={morceau.value}" for nom, morceau in bocal.items())


# --- La page ----------------------------------------------------------------


async def test_la_page_repond_en_html():
    client, _ = await _client()
    reponse = await client.get("/frais")

    assert reponse.status == 200
    assert "text/html" in reponse.headers["Content-Type"]
    assert "utf-8" in reponse.headers["Content-Type"].casefold()
    await client.close()


async def test_la_page_offre_une_zone_de_collage():
    client, _ = await _client()
    page = await (await client.get("/frais")).text()

    assert 'name="collage"' in page
    assert "<textarea" in page
    await client.close()


async def test_le_menu_deroulant_liste_les_serveurs_du_bot():
    """Le nom pour choisir, l'id pour écrire.

    Sans les ids, l'enregistrement irait dans la configuration commune, que plus
    aucun tableau ne lit : les frais seraient saisis et le post du soir muet.
    """
    client, _ = await _client()
    page = await (await client.get("/frais")).text()

    assert 'name="serveur"' in page
    assert 'value="111"' in page
    assert "Empire Immo" in page
    assert 'value="222"' in page
    assert "Groupe Nord" in page
    await client.close()


async def test_sans_serveur_le_bot_le_dit_plutot_quun_menu_vide():
    """Le bot pas encore connecté, ou invité nulle part.

    Un menu vide se lirait comme une panne de la page ; le dire renvoie à la
    vraie cause.
    """
    client, _ = await _client(serveurs=())
    page = await (await client.get("/frais")).text()

    assert "aucun serveur" in page.casefold()
    await client.close()


async def test_la_page_nest_pas_mise_en_cache():
    """Elle affiche les relevés du jour : un cache la montrerait périmée."""
    client, _ = await _client()
    reponse = await client.get("/frais")

    assert "no-store" in reponse.headers.get("Cache-Control", "")
    await client.close()


# --- La conversion ----------------------------------------------------------


async def test_le_collage_du_jeu_ressort_pret_a_copier():
    """Le format d'import du jeu : nom, tabulation, frais.

    Les chiffres exacts et non la notation courte : `24.12 PØ` importé
    demanderait quatre-vingts fois trop peu, sans que la ligne ait l'air fausse.
    """
    client, _ = await _client()
    page = await _convertir(client, COLLAGE)

    # 7 % de 344 582 317 616 911 946, sans décimale — le jeu ne facture pas de
    # fraction d'Ø. Et 7 % de 213 491 272 791 433 636 tombe sur ,52 : arrondi au
    # plus proche, donc vers le haut.
    assert "ARMEE  DE LAIR ET DE L ESPACE\t24120762233183836" in page
    assert "MARINE  NATIONALE\t14944389095400355" in page
    await client.close()


async def test_les_treize_filiales_sont_toutes_la():
    client, _ = await _client()
    page = await _convertir(client, COLLAGE)

    for nom in ("EMF AZOU 1", "EMF AZOU 2", "EMF AZOU 3", "BASE NAVALE BREST"):
        assert nom in page
    await client.close()


async def test_le_total_des_frais_est_affiche():
    """Le seul chiffre qu'on recoupe à l'œil avec le jeu."""
    client, _ = await _client()
    page = await _convertir(client, COLLAGE)

    assert "485.61" in page
    await client.close()


async def test_le_collage_reste_dans_la_zone_de_texte():
    """Sinon corriger une ligne refusée obligerait à tout recoller.

    C'est aussi ce qui permet d'enregistrer après avoir converti : le bouton
    d'enregistrement renvoie le contenu de cette zone.
    """
    client, _ = await _client()
    page = await _convertir(client, "MEGAPOLE\t1000")

    assert "MEGAPOLE\t1000" in page
    await client.close()


async def test_le_serveur_choisi_reste_selectionne():
    """Choisi avant de convertir, il doit l'être encore pour enregistrer.

    Revenu au premier de la liste, un clic sur « Enregistrer » écrirait les
    relevés d'une entreprise dans une autre.
    """
    client, _ = await _client()
    page = await _convertir(client, COLLAGE, serveur=222)

    assert 'value="222" selected' in page
    assert 'value="111" selected' not in page
    await client.close()


async def test_une_ligne_illisible_est_montree_avec_son_numero():
    """Ce que la page doit dire pour qu'on puisse corriger.

    Sautée en silence, une filiale manquerait au tableau du soir sans que rien
    ne l'annonce — et c'est le montant du jour qui serait faux.
    """
    client, _ = await _client()
    page = await _convertir(client, "MEGAPOLE\t1000\nABIMEE 500\n")

    assert "ABIMEE 500" in page
    assert "ligne 2" in page.casefold()
    assert "tabulation" in page.casefold()
    await client.close()


async def test_un_collage_vide_le_dit_plutot_que_de_montrer_un_tableau_vide():
    """Le cas du clic avant le collage.

    Un tableau vide et un total de 0 Ø se liraient comme un vrai résultat, et
    l'on croirait que le jeu ne rapporte rien.
    """
    client, _ = await _client()
    page = await _convertir(client, "   \n")

    assert "rien" in page.casefold()
    await client.close()


async def test_le_collage_nest_pas_execute_par_le_navigateur():
    """Ce qui entre est du texte quelconque, et il ressort dans la page.

    Recopié tel quel, un `<script>` s'exécuterait chez celui qui colle — et la
    page étant ouverte à tous, un lien piégé le ferait coller par un autre.
    """
    client, _ = await _client()
    page = await _convertir(client, "<script>alert(1)</script>\t1000")

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    await client.close()


async def test_convertir_nenregistre_rien():
    """La page est ouverte : convertir ne doit pas écrire en base.

    Sinon l'URL suffirait à remplacer les relevés du jour de n'importe quelle
    entreprise, sans mot de passe et sans trace.
    """
    client, bot = await _client()
    await _convertir(client, COLLAGE)

    assert await bot.store.pour(111).filiales() == []
    assert await bot.store.filiales() == []
    await client.close()


async def test_les_frais_dune_perte_sont_nuls():
    """Le jeu ne rembourse pas : la colonne s'appelle « Bénéfices ou pertes »."""
    client, _ = await _client()
    page = await _convertir(client, "MEGAPOLE\t-1000")

    assert "MEGAPOLE\t0" in page
    await client.close()


# --- L'enregistrement -------------------------------------------------------
#
# Convertir est ouvert, enregistrer est fermé : sans mot de passe, l'URL suffirait
# à remplacer les relevés du jour de n'importe quelle entreprise. Le mot de passe
# est réglé par `/reglages motdepasse`, entreprise par entreprise, et le cookie
# évite de le retaper à chaque tableau.


async def test_enregistrer_avec_le_mot_de_passe_ecrit_les_releves():
    """Ce que la page remplace : treize `/frais releve` tapés à la main."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(client, COLLAGE, motdepasse=mdp)

    assert reponse.status == 200
    filiales = await bot.store.pour(111).filiales()
    assert len(filiales) == 13
    assert all(filiale.frais > 0 for filiale in filiales)
    await client.close()


async def test_les_releves_vont_dans_lentreprise_choisie():
    """Écrits dans le commun ou chez le voisin, ils seraient saisis pour rien :
    le tableau du soir lit le tiroir de son serveur, et rien d'autre."""
    client, bot = await _client()
    mdp = await bot.store.pour(222).definir_motdepasse_page()

    await _enregistrer(client, "MEGAPOLE\t1000", serveur=222, motdepasse=mdp)

    assert len(await bot.store.pour(222).filiales()) == 1
    assert await bot.store.pour(111).filiales() == []
    assert await bot.store.filiales() == []
    await client.close()


async def test_sans_le_bon_mot_de_passe_rien_nest_ecrit():
    """La seule chose qui ferme la page. Un refus qui écrirait quand même
    laisserait n'importe qui remplacer les relevés du jour."""
    client, bot = await _client()
    await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(client, COLLAGE, motdepasse="pas-le-bon")

    assert reponse.status == 403
    assert await bot.store.pour(111).filiales() == []
    page = (await reponse.text()).casefold()
    assert "rien n'a été enregistré" in page
    assert "mot de passe refusé" in page
    await client.close()


async def test_une_entreprise_sans_mot_de_passe_ne_senregistre_pas():
    """Sans mot de passe réglé, la page est fermée — et non ouverte à tous.

    C'est le cas de toute entreprise neuve : accepter faute d'empreinte les
    ouvrirait toutes. Le message nomme la commande, sinon on chercherait la panne
    dans la page.
    """
    client, bot = await _client()

    reponse = await _enregistrer(client, COLLAGE, motdepasse="ce-quon-veut")

    assert reponse.status == 403
    assert await bot.store.pour(111).filiales() == []
    texte = await reponse.text()
    assert "/reglages motdepasse" in texte
    # Et non « mot de passe refusé » : on chercherait une faute de frappe dans un
    # mot de passe qui n'a jamais existé.
    assert "aucun mot de passe" in texte.casefold()
    await client.close()


async def test_le_mot_de_passe_dune_entreprise_ne_vaut_pas_chez_lautre():
    """La page propose la liste des entreprises : un mot de passe qui vaudrait
    partout donnerait à qui le tient l'écriture chez toutes."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()
    await bot.store.pour(222).definir_motdepasse_page()

    reponse = await _enregistrer(client, COLLAGE, serveur=222, motdepasse=mdp)

    assert reponse.status == 403
    assert await bot.store.pour(222).filiales() == []
    await client.close()


async def test_une_entreprise_inconnue_est_refusee():
    """Le menu ne propose que les serveurs du bot, mais le formulaire s'envoie à
    la main : un id quelconque écrirait dans un tiroir que personne ne publie."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(client, COLLAGE, serveur=999, motdepasse=mdp)

    assert reponse.status == 403
    assert await bot.store.pour(999).filiales() == []
    # Le refus doit nommer la cause : envoyer régler un mot de passe dans une
    # entreprise où le bot n'est pas ferait chercher pendant un moment.
    assert "choisis une" in (await reponse.text()).casefold()
    await client.close()


async def test_un_collage_vide_neffacerait_pas_les_releves_du_jour():
    """Le clic sur « Enregistrer » avant le collage.

    Rien n'est écrit et le tableau reste : « enregistré : 0 filiale » se lirait
    comme un succès, et le tableau du soir serait vide sans qu'on l'ait voulu.
    """
    client, bot = await _client()
    magasin = bot.store.pour(111)
    mdp = await magasin.definir_motdepasse_page()
    await magasin.enregistrer_filiale("MEGAPOLE", Decimal("1000"), "2026-08-31")

    reponse = await _enregistrer(client, "   \n", motdepasse=mdp)

    assert len(await magasin.filiales()) == 1
    assert "rien" in (await reponse.text()).casefold()
    await client.close()


async def test_enregistrer_ne_touche_pas_aux_filiales_absentes_du_collage():
    """C'est un lot de `/frais releve`, pas un remplacement du tableau.

    Une filiale vendue reste à retirer avec `/frais retirer` ; l'effacer ici
    ferait disparaître du tableau celle dont on aurait collé le tableau en deux
    fois.
    """
    client, bot = await _client()
    magasin = bot.store.pour(111)
    mdp = await magasin.definir_motdepasse_page()
    await magasin.enregistrer_filiale("MEGAPOLE", Decimal("1000"), "2026-08-31")

    await _enregistrer(client, "AUTRE\t2000", motdepasse=mdp)

    assert [filiale.nom for filiale in await magasin.filiales()] == [
        "MEGAPOLE",
        "AUTRE",
    ]
    await client.close()


async def test_le_succes_dit_ce_qui_a_ete_enregistre_et_pour_qui():
    """Deux entreprises dans le menu : un « ✅ » muet laisserait douter de
    laquelle vient d'être remplie."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(client, COLLAGE, motdepasse=mdp)
    page = await reponse.text()

    assert "13" in page
    assert "Empire Immo" in page
    await client.close()


async def test_les_lignes_illisibles_sont_montrees_meme_apres_enregistrement():
    """Le reste est enregistré, mais le manque doit se voir : c'est le montant du
    jour qui serait faux, et personne ne recompte treize lignes."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(client, "MEGAPOLE\t1000\nABIMEE 500\n", motdepasse=mdp)
    page = await reponse.text()

    assert len(await bot.store.pour(111).filiales()) == 1
    assert "ligne 2" in page.casefold()
    await client.close()


async def test_le_mot_de_passe_ne_revient_pas_dans_la_page():
    """Le collage revient dans la zone de texte ; le mot de passe, jamais.

    Réaffiché dans la valeur d'un champ, il se lirait dans la source de la page
    et resterait dans l'historique du navigateur.
    """
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(client, COLLAGE, motdepasse=mdp)

    assert mdp not in await reponse.text()
    await client.close()


# --- Le cookie --------------------------------------------------------------


async def test_enregistrer_identifie_le_navigateur():
    """Le tableau se colle tous les jours : retaper le mot de passe à chaque fois
    ferait garder le mot de passe sous la main, donc à portée de tout le monde."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()

    premiere = await _enregistrer(client, "MEGAPOLE\t1000", motdepasse=mdp)
    seconde = await _enregistrer(client, "AUTRE\t2000", cookie=_cookie(premiere))

    assert seconde.status == 200
    assert len(await bot.store.pour(111).filiales()) == 2
    await client.close()


async def test_le_cookie_porte_le_nom_de_son_entreprise():
    """Un seul navigateur peut suivre deux entreprises : un nom unique ferait
    remplacer le cookie de l'une par celui de l'autre à chaque enregistrement."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(client, "MEGAPOLE\t1000", motdepasse=mdp)

    assert "eibot_frais_111=" in " ".join(reponse.headers.getall("Set-Cookie"))
    await client.close()


async def test_le_cookie_est_hors_de_portee_du_javascript():
    """Volé, il vaudrait mot de passe pour trente jours."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(client, "MEGAPOLE\t1000", motdepasse=mdp)
    entetes = " ".join(reponse.headers.getall("Set-Cookie")).casefold()

    assert "httponly" in entetes
    assert "samesite=lax" in entetes
    assert "secure" in entetes
    # Trente jours, et une fin : sans durée, le cookie tiendrait le temps du
    # navigateur, c'est-à-dire indéfiniment.
    assert "max-age=2592000" in entetes
    await client.close()


async def test_le_cookie_ne_vaut_que_pour_son_entreprise():
    """Le nom de l'entreprise est dans la signature, pas seulement dans celui du
    cookie : renvoyé sur une autre entreprise, il ne doit rien ouvrir."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()
    await bot.store.pour(222).definir_motdepasse_page()
    premiere = await _enregistrer(client, "MEGAPOLE\t1000", motdepasse=mdp)

    reponse = await _enregistrer(
        client, "AUTRE\t2000", serveur=222, cookie=_cookie(premiere)
    )

    assert reponse.status == 403
    assert await bot.store.pour(222).filiales() == []
    await client.close()


async def test_le_cookie_nest_pas_prolonge_a_chaque_enregistrement():
    """Trente jours à partir du mot de passe tapé, pas du dernier collage.

    Reposé à chaque enregistrement, le cookie ferait d'un mois glissant un accès
    sans fin sur un navigateur qui colle tous les jours — alors que sa raison
    d'être est qu'un navigateur oublié finisse par perdre la main.
    """
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()
    premiere = await _enregistrer(client, "MEGAPOLE\t1000", motdepasse=mdp)

    seconde = await _enregistrer(client, "AUTRE\t2000", cookie=_cookie(premiere))

    assert seconde.status == 200
    assert seconde.headers.getall("Set-Cookie", []) == []
    await client.close()


async def test_changer_le_mot_de_passe_coupe_les_navigateurs():
    """C'est ce qu'on attend d'un mot de passe changé, et le seul moyen de couper
    un navigateur qu'on ne tient plus."""
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()
    premiere = await _enregistrer(client, "MEGAPOLE\t1000", motdepasse=mdp)
    await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(client, "AUTRE\t2000", cookie=_cookie(premiere))

    assert reponse.status == 403
    assert len(await bot.store.pour(111).filiales()) == 1
    await client.close()


async def test_retirer_le_mot_de_passe_referme_la_page():
    client, bot = await _client()
    mdp = await bot.store.pour(111).definir_motdepasse_page()
    premiere = await _enregistrer(client, "MEGAPOLE\t1000", motdepasse=mdp)
    await bot.store.pour(111).effacer_motdepasse_page()

    reponse = await _enregistrer(client, "AUTRE\t2000", cookie=_cookie(premiere))

    assert reponse.status == 403
    assert len(await bot.store.pour(111).filiales()) == 1
    await client.close()


async def test_un_cookie_fabrique_ne_vaut_rien():
    """Il se lit et se retouche dans les outils du navigateur."""
    client, bot = await _client()
    await bot.store.pour(111).definir_motdepasse_page()

    reponse = await _enregistrer(
        client, "MEGAPOLE\t1000", cookie="eibot_frais_111=9999999999.abcdef"
    )

    assert reponse.status == 403
    assert await bot.store.pour(111).filiales() == []
    await client.close()


# --- Le formulaire ----------------------------------------------------------


async def test_la_page_offre_denregistrer_avec_un_mot_de_passe():
    client, _ = await _client()
    page = await (await client.get("/frais")).text()

    assert 'formaction="/frais/enregistrer"' in page
    assert 'name="motdepasse"' in page
    assert 'type="password"' in page
    await client.close()


async def test_le_mot_de_passe_ne_passe_pas_par_ladresse():
    """En `get`, il finirait dans l'URL, donc dans le journal d'accès de Render et
    dans l'historique du navigateur."""
    client, _ = await _client()
    page = await (await client.get("/frais")).text()

    assert '<form method="post"' in page
    assert 'method="get"' not in page
    await client.close()
