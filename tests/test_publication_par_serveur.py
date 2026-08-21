"""La tournée quotidienne, une fois par serveur.

Le bot publiait une fois pour tout le monde, en lisant une seule configuration.
Deux entreprises réglées séparément (voir `tests/test_cloisonnement.py`) ne
sortiraient donc jamais rien de différent : cloisonner le stockage ne sert à rien
tant que la boucle d'envoi n'en tient pas compte.

Le point de vigilance nommé dans le plan est ici : une seule liste de salons
couvrait les deux serveurs, si bien qu'un cloisonnement mal fait ferait publier
chaque serveur dans les salons de tous les autres — **deux messages par salon au
lieu d'un**. Compter les messages est le seul vrai juge, et c'est ce que font ces
tests.

La configuration commune garde son chemin : le site de contrôle ne dit pas de
quel serveur il parle, et `publier_si_lheure` continue de la lire. La garde ne
s'applique donc qu'aux magasins qui savent de quel serveur ils sont.
"""

from decimal import Decimal

from src.bot import EmpireBot
from src.db import Store
from src.journal import Journal
from src.modules import Envoi, Module, Publication, Tournee
from src.modules import filiales as module_filiales
from src.modules import promos as module_promos
from src.schedule import maintenant_local

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
"""


class ServeurFactice:
    def __init__(self, serveur_id: int, nom: str):
        self.id = serveur_id
        self.name = nom


class SalonFactice:
    def __init__(self, salon_id: int, serveur: ServeurFactice | None, nom="promos"):
        self.id = salon_id
        self.name = nom
        self.guild = serveur
        self.mention = f"<#{salon_id}>"
        self.envois: list[dict] = []

    async def send(self, contenu=None, **options):
        self.envois.append({"contenu": contenu, **options})


class SalonCasse(SalonFactice):
    """Salon dont l'envoi échoue : permissions retirées, par exemple."""

    async def send(self, contenu=None, **options):
        raise RuntimeError("Missing Permissions")


class SourceFactice:
    async def fetch(self) -> str:
        return CSV


class JournalFactice:
    async def publication(self, promos, reussis, echecs):
        pass

    async def erreur(self, message):
        pass


class BotDeTest(EmpireBot):
    """`EmpireBot` dont on choisit les serveurs.

    `guilds` est une propriété en lecture seule de `discord.Client` : la
    redéfinir dans une sous-classe est le seul moyen de la garnir sans se
    connecter à Discord. C'est aussi ce qui laisse la boucle de `publier_tout`
    sous test, au lieu de la remplacer par une doublure.
    """

    @property
    def guilds(self):
        return self._serveurs


#: Les deux entreprises de l'histoire, nommées pour être lues dans un compte
#: rendu — et sans leur id dans le nom, sinon on ne saurait pas lequel des deux
#: le bot a écrit.
EMPIRE = ServeurFactice(111, "Empire Immo")
FILIALE = ServeurFactice(222, "Groupe Nord")


async def _bot(
    serveurs: list[ServeurFactice],
    salons: dict[int, SalonFactice],
    modules: list[Module] | None = None,
) -> BotDeTest:
    store = Store(dsn="")
    await store.connect()

    bot = object.__new__(BotDeTest)
    bot.store = store
    bot.source = SourceFactice()
    bot.journal = JournalFactice()
    bot.get_channel = salons.get
    bot._serveurs = serveurs
    bot.modules = (
        modules if modules is not None
        else [module_promos.MODULE, module_filiales.MODULE]
    )
    return bot


async def _fourchette_dans(magasin, *salons: str) -> None:
    await magasin.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    for salon in salons:
        await magasin.ajouter_salon_fourchette("a", salon)


def _module_dessai(
    cles: tuple[str, ...],
    salons=("1",),
    casse: str | None = None,
    nom: str = "essai",
):
    """Un module jetable : une publication par clé, chacune dans `salons`.

    `casse` est l'id du serveur dont la préparation lève, pour éprouver qu'une
    panne chez l'un n'empêche pas les autres. `nom` sert à en monter deux dans le
    même bot, ce que l'activation par serveur demande.
    """
    envoyes: list[tuple[str, str]] = []

    def publication(cle: str) -> Publication:
        async def preparer(bot, magasin, maintenant):
            if casse is not None and magasin.serveur_id == casse:
                raise RuntimeError("export illisible")

            async def envoyer(cible, ephemere=False):
                envoyes.append((magasin.serveur_id, cle))

            return Tournee(
                envois=(Envoi(etiquette=cle, salons=salons, envoyer=envoyer),)
            )

        return Publication(cle=cle, titre=cle, preparer=preparer)

    module = Module(
        nom=nom,
        titre="Essai",
        description="Publications jetables",
        publications=tuple(publication(cle) for cle in cles),
    )
    return module, envoyes


# --- Un tour par serveur, chacun sur sa configuration -----------------------


async def test_chaque_serveur_publie_selon_sa_propre_configuration():
    """Le besoin de base : deux entreprises, deux salons, deux posts."""
    salons = {1: SalonFactice(1, EMPIRE), 2: SalonFactice(2, FILIALE)}
    bot = await _bot([EMPIRE, FILIALE], salons)
    await _fourchette_dans(bot.store.pour("111"), "1")
    await _fourchette_dans(bot.store.pour("222"), "2")

    await bot.publier_tout(forcer=True)

    assert len(salons[1].envois) == 1
    assert len(salons[2].envois) == 1


async def test_un_serveur_sans_rien_de_regle_ne_publie_nulle_part():
    """Sans repli sur la configuration commune, un serveur neuf reste muet.

    C'est le prix assumé de `/reglages importer`, et il vaut mieux qu'un post
    surprise dans un salon que personne n'a désigné pour lui.
    """
    salons = {1: SalonFactice(1, EMPIRE), 2: SalonFactice(2, FILIALE)}
    bot = await _bot([EMPIRE, FILIALE], salons)
    await _fourchette_dans(bot.store, "1", "2")  # la config commune, celle d'avant

    await bot.publier_tout(forcer=True)

    assert salons[1].envois == []
    assert salons[2].envois == []


async def test_le_post_du_jour_est_habille_par_le_template_de_son_serveur():
    """Deux entreprises n'ont pas la même charte, et c'est le seul usage du
    template : rendu depuis la configuration commune, régler le sien ne
    changerait jamais rien à ce qui sort."""
    salons = {1: SalonFactice(1, EMPIRE)}
    bot = await _bot([EMPIRE], salons)
    await bot.store.set_template({"embeds": [{"title": "Commun"}]})
    magasin = bot.store.pour("111")
    await _fourchette_dans(magasin, "1")
    await magasin.set_template({"embeds": [{"title": "Chez Empire — {nom}"}]})

    await bot.publier_tout(forcer=True)

    titres = [embed.title for envoi in salons[1].envois for embed in envoi["embeds"]]
    assert titres and titres[0].startswith("Chez Empire")


async def test_le_compte_rendu_nomme_chaque_serveur():
    """Sinon `/tick` répondrait deux fois « promotions : publié » sans dire où."""
    salons = {1: SalonFactice(1, EMPIRE)}
    bot = await _bot([EMPIRE, FILIALE], salons)
    await _fourchette_dans(bot.store.pour("111"), "1")

    rendu = await bot.publier_tout(forcer=True)

    assert "Empire Immo" in rendu
    # Y compris celui qui n'a rien publié : son silence est justement ce qu'on
    # cherche à comprendre en lisant le compte rendu.
    assert "Groupe Nord" in rendu


async def test_lheure_est_lue_dans_le_fuseau_du_serveur():
    """Deux entreprises peuvent vivre dans deux fuseaux.

    L'heure réglée dans un serveur n'a de sens que dans le fuseau de ce
    serveur : lue dans celui de la configuration commune, « 09:00 » désignerait
    un autre moment de la journée, et le post sortirait à côté — ou, la
    tolérance de rattrapage étant d'une heure, pas du tout.
    """
    salons = {1: SalonFactice(1, EMPIRE)}
    bot = await _bot([EMPIRE], salons)
    empire = bot.store.pour("111")
    await _fourchette_dans(empire, "1")
    # Kiritimati est douze heures devant Paris : l'heure réglée ici ne ressemble
    # à rien pour qui la lirait dans le fuseau commun.
    await empire.maj_config(
        fuseau="Pacific/Kiritimati",
        heure=maintenant_local("Pacific/Kiritimati").strftime("%H:%M"),
    )

    # Sans `forcer` : c'est justement le compte à rebours qui est en jeu.
    await bot.publier_tout()

    assert len(salons[1].envois) == 1


async def test_sans_aucun_serveur_le_tour_le_dit():
    """Un bot invité nulle part rend une phrase, pas une réponse vide.

    `/tick` répond au cron toutes les cinq minutes : une chaîne vide se lirait
    comme une panne du service.
    """
    bot = await _bot([], {})

    assert await bot.publier_tout(forcer=True) == "aucun serveur"


# --- Le vrai juge : compter les messages ------------------------------------


async def test_un_serveur_ne_publie_pas_dans_le_salon_dun_autre():
    """La garde du plan : le salon doit appartenir au serveur dont on lit la config.

    Un salon d'un autre serveur resté dans la liste — une reprise de réglages
    trop large, un id recopié à la main — donnerait deux messages dans ce salon
    au lieu d'un, et personne ne verrait d'erreur.
    """
    salons = {1: SalonFactice(1, EMPIRE), 2: SalonFactice(2, FILIALE)}
    bot = await _bot([EMPIRE, FILIALE], salons)
    # Le serveur 111 croit servir le salon 2, qui est chez 222.
    await _fourchette_dans(bot.store.pour("111"), "1", "2")
    await _fourchette_dans(bot.store.pour("222"), "2")

    await bot.publier_tout(forcer=True)

    assert len(salons[1].envois) == 1
    assert len(salons[2].envois) == 1


async def test_le_salon_dun_autre_serveur_est_signale_dans_le_journal():
    """Écarté **et** nommé : sans trace, un salon muet ressemble à une panne.

    Le plan le demande là : « signalera dans le journal celui qui ne colle pas
    plutôt que d'y publier ». Le salon fautif et le serveur auquel il appartient
    sont les deux seules informations qui permettent de corriger le réglage.
    """
    salons = {
        1: SalonFactice(1, EMPIRE),
        2: SalonFactice(2, FILIALE),
        8: SalonFactice(8, EMPIRE, "logs"),
    }
    bot = await _bot([EMPIRE], salons)
    bot.journal = Journal(bot, bot.store)
    empire = bot.store.pour("111")
    await _fourchette_dans(empire, "1", "2")
    await empire.maj_config(logs_salon_id="8")

    await bot.publier_tout(forcer=True)

    raconte = " ".join(envoi["contenu"] for envoi in salons[8].envois)
    assert "<#2>" in raconte, raconte
    assert "222" in raconte, raconte
    # Et le salon légitime reste annoncé comme réussi : un signalement qui les
    # confondrait ferait chercher une panne là où il n'y en a pas.
    assert "<#1>" in raconte


async def test_un_salon_etranger_seul_nempeche_pas_de_marquer_la_journee():
    """Sinon le bot réessaierait toutes les cinq minutes, sans espoir.

    Un salon d'un autre serveur n'est pas une panne passagère : réessayer ne le
    rapprochera pas, et chaque passage écrirait une ligne dans le salon de logs —
    288 par jour.
    """
    salons = {2: SalonFactice(2, FILIALE)}
    bot = await _bot([EMPIRE], salons)
    await _fourchette_dans(bot.store.pour("111"), "2")

    await bot.publier_tout(forcer=True)

    assert salons[2].envois == []
    assert await bot.store.pour("111").derniere_publication() is not None


async def test_une_panne_passagere_laisse_la_journee_a_faire():
    """L'inverse du test précédent : un salon cassé, lui, mérite un nouvel essai.

    C'est ce qui distingue les deux cas — sans quoi la garde pourrait marquer la
    journée dans les deux, et un salon aux permissions retirées pour une minute
    coûterait le post du jour.
    """
    salons = {1: SalonCasse(1, EMPIRE)}
    bot = await _bot([EMPIRE], salons)
    await _fourchette_dans(bot.store.pour("111"), "1")

    await bot.publier_tout(forcer=True)

    assert await bot.store.pour("111").derniere_publication() is None


async def test_le_site_publie_encore_depuis_la_configuration_commune():
    """Le site de contrôle ne dit pas de quel serveur il parle : pas de garde.

    Sa configuration couvre des salons de plusieurs serveurs, et l'appliquer là
    ferait tout écarter — le site cesserait de publier sans rien annoncer.
    """
    salons = {1: SalonFactice(1, EMPIRE), 2: SalonFactice(2, FILIALE)}
    bot = await _bot([EMPIRE, FILIALE], salons)
    await _fourchette_dans(bot.store, "1", "2")

    await bot.publier_si_lheure(forcer=True)

    assert len(salons[1].envois) == 1
    assert len(salons[2].envois) == 1


# --- Chaque serveur a sa journée, et ses pannes -----------------------------


async def test_une_panne_chez_un_serveur_ne_consomme_pas_la_journee_des_autres():
    """Le bénéfice annoncé : la trace « déjà publié » est par serveur."""
    salons = {1: SalonCasse(1, EMPIRE), 2: SalonFactice(2, FILIALE)}
    bot = await _bot([EMPIRE, FILIALE], salons)
    await _fourchette_dans(bot.store.pour("111"), "1")
    await _fourchette_dans(bot.store.pour("222"), "2")

    await bot.publier_tout(forcer=True)

    assert await bot.store.pour("111").derniere_publication() is None
    assert await bot.store.pour("222").derniere_publication() is not None


async def test_une_preparation_qui_leve_chez_un_serveur_nempeche_pas_les_autres():
    """Une entreprise dont l'export est illisible ne fait pas taire l'autre.

    La panne est isolée **par serveur** et non seulement par publication : sans
    ça, l'export en panne du premier serveur interromprait la boucle avant même
    d'atteindre le second.
    """
    module, envoyes = _module_dessai(("essai",), salons=("2",), casse="111")
    bot = await _bot([EMPIRE, FILIALE], {2: SalonFactice(2, FILIALE)}, [module])

    rendu = await bot.publier_tout(forcer=True)

    assert envoyes == [("222", "essai")]
    # La panne reste visible : avalée en silence, on croirait tout normal.
    assert "RuntimeError" in rendu


# --- Les publications viennent des modules ---------------------------------


async def test_une_troisieme_publication_part_sans_toucher_au_bot():
    """La promesse du contrat de module : rien n'est écrit en dur dans la boucle.

    Le bot appelait deux méthodes nommées, une par publication historique. Un
    module qui en déclare une autre n'aurait rien publié.
    """
    module, envoyes = _module_dessai(("bonjour",))
    bot = await _bot([EMPIRE], {1: SalonFactice(1, EMPIRE)}, [module])

    await bot.publier_tout(forcer=True)

    assert envoyes == [("111", "bonjour")]


async def test_un_module_peut_declarer_deux_publications():
    """L'épreuve du plan : deux posts dans un seul fichier, chacun indépendant."""
    module, envoyes = _module_dessai(("bonjour", "bonsoir"))
    bot = await _bot([EMPIRE], {1: SalonFactice(1, EMPIRE)}, [module])

    await bot.publier_tout(forcer=True)

    assert sorted(cle for _, cle in envoyes) == ["bonjour", "bonsoir"]


async def test_sans_aucun_module_le_serveur_le_dit():
    """Tous les modules refusés au démarrage : le compte rendu doit l'avouer.

    Un `Empire Immo — ` suivi de rien se lirait comme « tout va bien ».
    """
    bot = await _bot([EMPIRE], {}, [])

    assert "aucune publication" in await bot.publier_tout(forcer=True)


# --- Le journal parle du serveur dont il raconte la tournée -----------------


async def test_chaque_serveur_raconte_sa_tournee_dans_son_salon_de_logs():
    """Le salon de logs est un réglage comme les autres, donc par serveur.

    Raconter la tournée du second dans le salon de logs du premier mélangerait
    deux entreprises dans un même fil, et donnerait à chacune les ids de salons
    de l'autre.
    """
    salons = {
        1: SalonFactice(1, EMPIRE),
        2: SalonFactice(2, FILIALE),
        8: SalonFactice(8, EMPIRE, "logs-empire"),
        9: SalonFactice(9, FILIALE, "logs-nord"),
    }
    bot = await _bot([EMPIRE, FILIALE], salons)
    # Le vrai journal, et non la doublure : ce qui est en jeu est le magasin qu'il
    # interroge pour trouver son salon.
    bot.journal = Journal(bot, bot.store)

    empire = bot.store.pour("111")
    nord = bot.store.pour("222")
    await _fourchette_dans(empire, "1")
    await _fourchette_dans(nord, "2")
    await empire.maj_config(logs_salon_id="8")
    await nord.maj_config(logs_salon_id="9")

    await bot.publier_tout(forcer=True)

    assert len(salons[8].envois) == 1, salons[8].envois
    assert len(salons[9].envois) == 1, salons[9].envois
    assert "<#1>" in salons[8].envois[0]["contenu"]
    assert "<#2>" in salons[9].envois[0]["contenu"]
