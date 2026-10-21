"""Collecte Web vérifiée par le moteur (niveau FETCH_VERIFIED) — GÉNÉRIQUE, sans liste blanche de domaines.

LLM_Provider propose (URL, citation exacte, pseudo, lieu/persona revendiqués) ; ce module télécharge lui-même la page et
vérifie ce qui a réellement été consulté. L'admissibilité dépend de la qualité de la vérification, pas du nom de domaine :
- protections conservées : HTTPS seul, hôtes publics seulement (y compris après résolution DNS), robots.txt (règles
  génériques ET agents LLM_Provider/Anthropic, la plus restrictive l'emporte), aucune redirection vers un autre hôte,
  taille bornée, contenu texte seulement, détection des murs de connexion/CAPTCHA (jamais contournés) ;
- la citation doit figurer dans la page téléchargée (un résumé de moteur de recherche n'est jamais une preuve) ;
- nature du contenu établie par la STRUCTURE de la page (jamais par le type proposé) : contribution d'utilisateur
  (adaptateur, schema.org JSON-LD/microdonnées, blocs répétés de commentaires/avis/messages), contenu éditorial ou
  marketing, ou nature incertaine ; seules les contributions d'utilisateurs comptent comme témoignages ;
- rattachement à l'auteur (facultatif) seulement dans un bloc fiable : ADAPTER, STRUCTURED (auteur déclaré dans les
  données structurées), COMMENT_BLOCK (pseudo affiché DANS le bloc de la contribution) ; paternité unique exigée ;
  sinon UNBOUND (citation vérifiée, jamais attribuée). Une simple proximité textuelle ne prouve jamais la paternité.

Ce que prouve un reçu QUOTE_VERIFIED : « ce texte figurait sur cette URL, à cette date de téléchargement », avec le
niveau de rattachement indiqué. Il ne prouve ni l'identité, ni l'authenticité, ni la résidence : jamais LIVE.
Le pseudo n'est jamais stocké : seules des empreintes propres à la mission sont conservées.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import html
import ipaddress
import json
import re
import socket
import ssl
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

USER_AGENT = "ECOM-OS-Research/1.0 (lecture seule; agent pilote par LLM_Provider runtime)"
ROBOT_AGENTS = ("ECOM-OS-Research", "ClaudeBot", "Claude-User", "anthropic-ai", "*")
MAX_BYTES = 4_000_000
FOREIGN_MARKERS = ("Avis d’autres pays", "Avis d'autres pays")
# Avis sollicités/incités : jamais spontanés (exclus du GATE 2B, conservés comme contexte).
INCENTIVE_MARKERS = ("recueilli en réponse à une offre", "en échange d'un produit", "produit offert", "produit gratuit en échange")

# Adaptateurs éprouvés (contracts/source_registry.json) : extracteurs d'en-tête de haute confiance pour quelques sites.
# Ce n'est PAS une liste d'admission : tout site public accessible passe par la vérification générique.
from . import geo_fr, source_registry  # noqa: E402
DOMAINS = source_registry.load_active()
ADAPTERS = DOMAINS
SYNDICATION_MARKERS = ("Publié à l'origine sur", "Traduit de l'", "Cet avis a été traduit", "Originally posted on")
BARRIER_MARKERS = {"BLOCKED_CAPTCHA": ("captcha", "cf-challenge", "just a moment...", "vérifiez que vous êtes humain",
                                       "are you a robot", "verify you are human"),
                   "BLOCKED_LOGIN_WALL": ("connectez-vous pour voir", "connectez-vous pour lire", "log in to continue",
                                          "sign in to continue", "inscrivez-vous pour voir")}
SOURCE_KIND_TYPES = {"FORUM": "WEB_FORUM", "REVIEW": "WEB_REVIEW", "COMMENT": "WEB_COMMENT", "BLOG": "WEB_BLOG",
                     "QA": "WEB_QA", "SOCIAL": "WEB_SOCIAL", "NEWS": "WEB_NEWS", "OTHER": "WEB_OTHER"}
# Types schema.org de CONTRIBUTIONS D'UTILISATEURS. Article/BlogPosting/Product/FAQ = contenu du site (éditorial/marketing).
JSONLD_KINDS = {"review": "REVIEW", "comment": "COMMENT", "answer": "QA", "question": "QA",
                "discussionforumposting": "FORUM", "socialmediaposting": "SOCIAL"}
JSONLD_EDITORIAL = {"product", "article", "newsarticle", "blogposting", "webpage", "organization", "howto", "faqpage",
                    "itempage", "collectionpage", "offer", "brand", "medicalwebpage"}
UGC_TOKENS = re.compile(r"(?:^|[-_ ])(comments?|commentaires?|reviews?|avis|posts?|messages?|replies|reply|answers?|reponses?|ugc|topic-post|forum-post)(?:$|[-_ ])", re.I)
TESTIMONIAL_TOKENS = re.compile(r"testimonial|temoignage-client|témoignage|customer-story|success-story", re.I)
AUTHOR_TOKENS = re.compile(r"(?:^|[-_ ])(author|auteur|username|user-name|pseudo|nickname|screen-name|reviewer|commenter|poster)(?:$|[-_ ])", re.I)
MICRODATA_UGC = re.compile(r"schema\.org/(Review|Comment|Answer|Question|DiscussionForumPosting|SocialMediaPosting)\b", re.I)
MICRODATA_EDITORIAL = re.compile(r"schema\.org/(Product|Article|NewsArticle|BlogPosting|WebPage|FAQPage|Offer)\b", re.I)
_SECOND_LEVEL = {"co.uk", "org.uk", "com.au", "co.nz", "gouv.fr", "asso.fr", "com.br", "co.jp", "com.mx"}

FR_PLACES = {x.casefold().replace("-", " ") for x in (
    "France,Paris,Lyon,Marseille,Toulouse,Nice,Nantes,Strasbourg,Montpellier,Bordeaux,Lille,Rennes,Reims,"
    "Toulon,Grenoble,Dijon,Angers,Nîmes,Brest,Limoges,Tours,Amiens,Metz,Perpignan,Besançon,Orléans,Rouen,Caen,"
    "Nancy,Clermont-Ferrand,Le Havre,Le Mans,Ile-de-France,Île-de-France,Bretagne,Normandie,Occitanie,"
    "Nouvelle-Aquitaine,Auvergne-Rhône-Alpes,Grand Est,Hauts-de-France,Bourgogne-Franche-Comté,Centre-Val de Loire,"
    "Pays de la Loire,Provence-Alpes-Côte d'Azur,PACA,Corse,Ain,Aisne,Allier,Alpes-de-Haute-Provence,Hautes-Alpes,"
    "Alpes-Maritimes,Ardèche,Ardennes,Ariège,Aube,Aude,Aveyron,Bouches-du-Rhône,Calvados,Cantal,Charente,"
    "Charente-Maritime,Cher,Corrèze,Côte-d'Or,Côtes-d'Armor,Creuse,Dordogne,Doubs,Drôme,Eure,Eure-et-Loir,Finistère,"
    "Gard,Haute-Garonne,Gers,Gironde,Hérault,Ille-et-Vilaine,Indre,Indre-et-Loire,Isère,Jura,Landes,Loir-et-Cher,"
    "Loire,Haute-Loire,Loire-Atlantique,Loiret,Lot,Lot-et-Garonne,Lozère,Maine-et-Loire,Manche,Marne,Haute-Marne,"
    "Mayenne,Meurthe-et-Moselle,Meuse,Morbihan,Moselle,Nièvre,Nord,Oise,Orne,Pas-de-Calais,Puy-de-Dôme,"
    "Pyrénées-Atlantiques,Hautes-Pyrénées,Pyrénées-Orientales,Bas-Rhin,Haut-Rhin,Rhône,Haute-Saône,Saône-et-Loire,"
    "Sarthe,Savoie,Haute-Savoie,Seine-Maritime,Seine-et-Marne,Yvelines,Deux-Sèvres,Somme,Tarn,Tarn-et-Garonne,Var,"
    "Vaucluse,Vendée,Vienne,Haute-Vienne,Vosges,Yonne,Territoire de Belfort,Essonne,Hauts-de-Seine,Seine-Saint-Denis,"
    "Val-de-Marne,Val-d'Oise,Guadeloupe,Martinique,Guyane,La Réunion,Mayotte").split(",")}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def norm(text: str) -> str:
    t = (text or "").replace("\xa0", " ").replace(" ", " ").replace("’", "'").replace("‘", "'")
    return " ".join(t.split())


def html_to_text(raw: bytes) -> str:
    try:
        t = raw.decode("utf-8")
    except UnicodeDecodeError:
        t = raw.decode("cp1252", errors="replace")
    t = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = html.unescape(re.sub(r"(?s)<[^>]+>", " ", t))
    return norm(t)


def author_key(mission_id: str, handle: str) -> str:
    """Pseudonymisation : même pseudo (normalisé) = même compte, quel que soit le site (fusion prudente)."""
    return "acct:" + hashlib.sha256(f"{mission_id}:{norm(handle).casefold()}".encode()).hexdigest()[:16]


def check_url(url: str) -> str | None:
    """Sécurité uniquement (aucune liste de domaines) : HTTPS, port standard, sans identifiants, hôte public nommé."""
    p = urlsplit(str(url or "").strip())
    host = (p.hostname or "").casefold()
    if p.scheme != "https" or not host or p.username or p.password or p.port not in (None, 443):
        return "UNSAFE_URL"
    if re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host) or host in ("localhost",) or "." not in host \
            or host.endswith((".local", ".internal", ".lan", ".home", ".localhost")):
        return "UNSAFE_URL"
    return None


def registrable_domain(host: str) -> str:
    """Domaine enregistrable approché (indépendance des sources) : forum.doctissimo.fr → doctissimo.fr."""
    labels = (host or "").casefold().strip(".").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return bool(infos)


class _NoCrossHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (urlsplit(newurl).hostname or "").casefold() != (urlsplit(req.full_url).hostname or "").casefold() \
                or urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(newurl, 399, "CROSS_HOST_REDIRECT_BLOCKED", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _tls_context() -> ssl.SSLContext:
    """Vérification TLS toujours active. Python python.org sur macOS n'a pas de magasin CA : repli sur
    le magasin système /etc/ssl/cert.pem (jamais de désactivation de la vérification)."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and Path("/etc/ssl/cert.pem").exists():
        ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ctx


def http_fetch(url: str) -> tuple[int, str, bytes]:
    if check_url(url) or not _host_is_public(urlsplit(url).hostname or ""):
        return 0, url, b""                       # hôte non public (résolution DNS) : jamais téléchargé
    opener = urllib.request.build_opener(_NoCrossHost, urllib.request.HTTPSHandler(context=_tls_context()))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"})
    try:
        with opener.open(req, timeout=25) as r:
            return r.status, r.geturl(), r.read(MAX_BYTES + 1)[:MAX_BYTES]
    except urllib.error.HTTPError as e:
        return e.code, url, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, url, b""


def robots_allows(url: str, fetcher=http_fetch, _cache: dict | None = None) -> bool:
    p = urlsplit(url)
    root = f"https://{p.hostname}/robots.txt"
    cache = _cache if _cache is not None else {}
    if root not in cache:
        status, _, body = fetcher(root)
        rp = urllib.robotparser.RobotFileParser()
        if status in (401, 403) or status == 0 or status >= 500:
            rp.disallow_all = True            # robots inaccessible : prudence
        elif status >= 400:
            rp.allow_all = True               # pas de robots.txt (404) : pas de restriction déclarée
        else:
            rp.parse(body.decode("utf-8", errors="replace").splitlines())
        cache[root] = rp
    return all(cache[root].can_fetch(a, url) for a in ROBOT_AGENTS)


def _place(x: str) -> str:
    """Forme canonique d'un lieu : casse, accents, tirets et apostrophes ignorés (engine/geo_fr.norm_place)."""
    return geo_fr.norm_place(x)


def _resolve_geo(conf: dict, block: str, location_claim: str | None, foreign: bool, extra: str | None,
                 profile_zone: str = "") -> tuple[str, str]:
    if foreign:
        return "FOREIGN", "FOREIGN_SECTION"
    if location_claim:
        # Résidence déclarée = champ de profil de l'en-tête (nom ou code de département entre parenthèses),
        # ou phrase explicite de l'auteur ; jamais un mot isolé du texte, la langue ou le domaine .fr.
        if geo_fr.is_fr_place(location_claim) and (geo_fr.claim_in_profile(location_claim, profile_zone)
                                                   or geo_fr.stated_residence(location_claim, block)):
            return "FR", "RESIDENCE_DECLARED"
    if conf["geo"] == "MARKET_SITE":
        if conf.get("market_requires_extra") and not extra:
            return "UNKNOWN", "LANGUAGE_ONLY"
        if extra and conf["source_type"] == "RETAILER_REVIEW_FR" and "auchan" not in extra.casefold() \
                and "propriétaire" not in extra.casefold():
            return "UNKNOWN", "SYNDICATED_ORIGIN_UNKNOWN"
        return "FR", "FR_MARKET_SITE"
    return "UNKNOWN", "LANGUAGE_ONLY"


_DATE_PATTERNS = (r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", r"\b(\d{4}-\d{2}-\d{2})\b",
                  r"\b(\d{1,2} (?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre) \d{4})\b",
                  r"\b(il y a \d+ (?:jours?|semaines?|mois|ans?))\b")
_AMOUNT = re.compile(r"(?:(\d{1,5}(?:[.,]\d{1,2})?)\s?(€|eur\b|euros?\b))|(?:€\s?(\d{1,5}(?:[.,]\d{1,2})?))", re.I)


def author_site_key(mission_id: str, host: str, handle: str) -> str:
    """Empreinte par site : borne haute (le même pseudo sur deux sites peut être deux personnes)."""
    return "acct-site:" + hashlib.sha256(f"{mission_id}:{registrable_domain(host)}:{norm(handle).casefold()}".encode()).hexdigest()[:16]


def _page_lang(raw: bytes) -> str | None:
    m = re.search(rb"<html[^>]*\blang=[\"']?([a-zA-Z-]{2,8})", raw[:5000])
    return m.group(1).decode().lower() if m else None


def _jsonld_items(raw: bytes, include_editorial: bool = False) -> list[dict]:
    """Objets schema.org publiés par la page. Par défaut : contributions d'utilisateurs seulement (Review, Comment,
    Answer/Question hors FAQ du site, DiscussionForumPosting, SocialMediaPosting). include_editorial : ajoute les
    contenus du site (Product, Article, BlogPosting, FAQPage…) avec kind EDITORIAL."""
    out = []
    for m in re.finditer(rb'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw):
        try:
            doc = json.loads(html.unescape(m.group(1).decode("utf-8", errors="replace")))
        except ValueError:
            continue
        stack = [(doc, False)]
        while stack:
            x, in_faq = stack.pop()                       # in_faq : un ancêtre est une FAQPage (contenu du site)
            if isinstance(x, list):
                stack.extend((v, in_faq) for v in x)
            elif isinstance(x, dict):
                t = x.get("@type")
                t = str((t[0] if isinstance(t, list) and t else t) or "").casefold()
                body = x.get("reviewBody") or x.get("text") or x.get("articleBody") or x.get("description")
                a = x.get("author")
                a = a[0] if isinstance(a, list) and a else a
                name = a.get("name") if isinstance(a, dict) else a if isinstance(a, str) else None
                in_faq = in_faq or t == "faqpage"
                if isinstance(body, str):
                    if t in JSONLD_KINDS and not in_faq:
                        out.append({"kind": JSONLD_KINDS[t], "author": name, "body": norm(html.unescape(body)),
                                    "date": x.get("datePublished") or x.get("dateCreated")})
                    elif include_editorial and (t in JSONLD_EDITORIAL or in_faq or t in JSONLD_KINDS):
                        out.append({"kind": "EDITORIAL", "author": None, "body": norm(html.unescape(body)), "date": None})
                stack.extend((v, in_faq) for v in x.values() if isinstance(v, (dict, list)))
    return out


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


def _contribution_blocks(raw: bytes) -> list[dict]:
    """Blocs de la page : contributions d'utilisateurs (microdonnées schema.org ou conteneurs répétés de type
    commentaire/avis/message), témoignages marketing, contenu éditorial balisé. Pour chaque bloc : texte normalisé,
    pseudos affichés DANS le bloc (élément auteur/pseudo), type de bloc. Aucune proximité textuelle."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.blocks, self.skip = [], [], 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self.skip += 1
            if tag in _VOID:
                return
            a = dict(attrs)
            label = " ".join(str(a.get(k) or "") for k in ("class", "id", "itemprop", "data-testid", "role"))
            itype = str(a.get("itemtype") or "")
            block = None
            if MICRODATA_UGC.search(itype):
                block = {"type": "MICRODATA_UGC", "group": "microdata"}
            elif MICRODATA_EDITORIAL.search(itype):
                block = {"type": "EDITORIAL", "group": "microdata-editorial"}
            elif TESTIMONIAL_TOKENS.search(label):
                block = {"type": "MARKETING_TESTIMONIAL", "group": "testimonial"}
            elif UGC_TOKENS.search(label):
                m = UGC_TOKENS.search(label)
                block = {"type": "MARKUP_UGC", "group": (tag + ":" + m.group(1).casefold())}
            is_author = bool(AUTHOR_TOKENS.search(label)) or a.get("itemprop") == "author"
            entry = {"tag": tag, "block": None, "author": is_author, "buf": [] if is_author else None}
            if block is not None:
                block.update(text=[], authors=[])
                self.blocks.append(block)
                entry["block"] = block
            self.stack.append(entry)

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript") and self.skip:
                self.skip -= 1
            if tag in _VOID:
                return
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i]["tag"] == tag:
                    for e in self.stack[i:][::-1]:
                        self._close(e)
                    del self.stack[i:]
                    break

        def _close(self, e):
            if e["author"] and e["buf"] is not None:
                name = norm(" ".join(e["buf"]))
                inner = next((x["block"] for x in reversed(self.stack) if x["block"] is not None and x is not e), None)
                if name and inner is not None and len(name) <= 60:
                    inner["authors"].append(name)

        def handle_data(self, data):
            if self.skip:
                return
            for e in self.stack:
                if e["block"] is not None:
                    e["block"]["text"].append(data)
                if e["author"] and e["buf"] is not None:
                    e["buf"].append(data)

    pr = P()
    try:
        pr.feed(raw.decode("utf-8", errors="replace"))
        pr.close()
    except Exception:  # HTML illisible : aucun bloc, jamais d'attribution
        return []
    for e in pr.stack[::-1]:
        pr._close(e)
    groups: dict[str, int] = {}
    for b in pr.blocks:
        b["text"] = norm(" ".join(b["text"]))
        groups[b["group"]] = groups.get(b["group"], 0) + 1
    for b in pr.blocks:
        b["repeated"] = groups[b["group"]] >= 2
    return pr.blocks


def _date_near(segment: str) -> str | None:
    for pat in _DATE_PATTERNS:
        m = re.search(pat, segment, re.I)
        if m:
            return m.group(1)
    return None


def verify(mdir: Path, *, url: str, quote: str, author: str | None, location_claim: str | None = None,
           count_capture: bool = False, fetcher=http_fetch, robots_cache: dict | None = None,
           persona_claim: str | None = None, persona_profile: dict | None = None,
           capture_kind: str | None = None, source_kind: str | None = None) -> dict:
    """Télécharge et vérifie une capture proposée sur N'IMPORTE QUEL site public accessible. Écrit toujours un reçu.
    capture_kind PRICE_OBSERVED / OFFER_OBSERVED : prix ou offre publics observés (lecture seule, source et date)."""
    mdir = Path(mdir)
    mission_id = mdir.name
    rid = hashlib.sha256(f"{url}|{norm(quote)}|{norm(author or '')}".encode()).hexdigest()[:16]
    rc = {"receipt_id": rid, "url": url, "quote": norm(quote), "checked_at": _now(), "engine": "web_capture/2"}
    bad = check_url(url)
    if bad:
        return _save(mdir, {**rc, "status": bad})
    site_level = count_capture or capture_kind in ("PRICE_OBSERVED", "OFFER_OBSERVED")
    # L'auteur est FACULTATIF : sans pseudo, la citation vérifiée devient une contribution non attribuée (UNBOUND).
    author = author if (author and norm(author)) else None
    if len(norm(quote)) < (4 if site_level else 15):
        return _save(mdir, {**rc, "status": "QUOTE_TOO_SHORT"})
    if not robots_allows(url, fetcher, robots_cache):
        return _save(mdir, {**rc, "status": "BLOCKED_ROBOTS"})
    status, final_url, raw = fetcher(url)
    if status == 399:
        return _save(mdir, {**rc, "status": "BLOCKED_CROSS_HOST_REDIRECT"})   # l'URL canonique doit être proposée
    if status != 200 or not raw:
        return _save(mdir, {**rc, "status": f"BLOCKED_HTTP_{status}"})
    if b"\x00" in raw[:2048]:
        return _save(mdir, {**rc, "status": "NON_TEXT_CONTENT"})
    sha = hashlib.sha256(raw).hexdigest()
    snap = mdir / "RESEARCH_INBOX" / "SNAPSHOTS" / f"{sha}.html.gz"
    snap.parent.mkdir(parents=True, exist_ok=True)
    if not snap.exists():
        snap.write_bytes(gzip.compress(raw))
    host = urlsplit(url).hostname.casefold()
    conf = ADAPTERS.get(host)
    text = html_to_text(raw)
    q = norm(quote)
    pos = text.find(q)
    jsonld = _jsonld_items(raw) if conf is None else []
    in_jsonld = [j for j in jsonld if q in j["body"]]
    nature, kind_detected, blocks_q = _nature(raw, q, conf, in_jsonld)
    stype = conf["source_type"] if conf else (SOURCE_KIND_TYPES.get(kind_detected, "WEB_OTHER")
                                              if nature.startswith("USER_CONTRIBUTION") else
                                              "WEB_NEWS" if nature == "EDITORIAL_OR_MARKETING" else "WEB_OTHER")
    rc.update(final_url=final_url, http_status=status, sha256=sha, source_type=stype, host=host,
              registrable_domain=registrable_domain(host), page_lang=_page_lang(raw), contribution_nature=nature,
              source_kind_proposed=(source_kind or None),
              source_kind_basis="ADAPTER" if conf else ("PAGE_STRUCTURE" if nature != "UNCERTAIN_NATURE" else "UNDETERMINED"))
    in_site_content = [] if (pos >= 0 or in_jsonld) else [
        j for j in _jsonld_items(raw, include_editorial=True) if j["kind"] == "EDITORIAL" and q in j["body"]]
    if pos < 0 and not in_jsonld and not in_site_content:
        low = text.casefold()
        for st, marks in BARRIER_MARKERS.items():
            if any(mk in low for mk in marks):
                return _save(mdir, {**rc, "status": st})     # jamais contourné, jamais une preuve
        return _save(mdir, {**rc, "status": "QUOTE_NOT_FOUND"})
    if count_capture:
        m = re.search(r"\((\d[\d ]*)\)|(\d[\d ]*) (?:avis|réponses|messages|commentaires|notes|évaluations)", q)
        if not m:
            return _save(mdir, {**rc, "status": "COUNT_NOT_PARSED"})
        n = int((m.group(1) or m.group(2)).replace(" ", ""))
        geo = ("FR", "FR_MARKET_SITE") if conf and conf.get("geo") == "MARKET_SITE" else ("UNKNOWN", "PUBLIC_PAGE")
        return _save(mdir, {**rc, "status": "QUOTE_VERIFIED", "kind_hint": "INDEX_COUNT", "count": n,
                            "author_key": f"site:{host}", "market_geo": geo[0], "geo_basis": geo[1],
                            "solicited": False, "testimony_date": None, "binding": "SITE_LEVEL"})
    if capture_kind in ("PRICE_OBSERVED", "OFFER_OBSERVED"):
        extra = {}
        if capture_kind == "PRICE_OBSERVED":
            m = _AMOUNT.search(q)
            if not m:
                return _save(mdir, {**rc, "status": "PRICE_NOT_PARSED"})
            extra = {"amount": float((m.group(1) or m.group(3)).replace(",", ".")), "currency": "EUR"}
        return _save(mdir, {**rc, "status": "QUOTE_VERIFIED", "kind_hint": capture_kind, **extra,
                            "author_key": f"site:{host}", "market_geo": "UNKNOWN", "geo_basis": "PUBLIC_PAGE",
                            "solicited": False, "testimony_date": None, "observed_at": rc["checked_at"],
                            "binding": "SITE_LEVEL"})
    from . import persona as persona_mod
    rejected_claim = None
    if conf is not None and author:
        res = _verify_adapter(rc, conf, text, q, pos, author, location_claim, mission_id)
        if res.get("status") == "QUOTE_VERIFIED":
            block = res.pop("_block")
            pq = persona_mod.qualify_claim(persona_claim, block, persona_profile) if persona_claim else None
            res.update({"author_site_key": author_site_key(mission_id, host, author), "binding": "ADAPTER",
                        "evidence_confidence": "HIGH", **({"persona": pq} if pq else {})})
            return _save(mdir, res)
        if pos < 0:
            return _save(mdir, res)
        # Rattachement incertain : jamais inventé. La citation (présente sur la page) reste une contribution non attribuée.
        rejected_claim, author = res["status"], None
    # --- vérification générique (tout site) : attribution UNIQUEMENT par bloc structuré ou bloc de contribution ---
    ca = norm(author).casefold() if author else None
    binding, date, profile_zone, block = None, None, "", None
    if in_jsonld:
        authors_q = {norm(j["author"]).casefold() for j in in_jsonld if j["author"]}
        if ca and authors_q == {ca}:                      # une seule paternité possible pour cette citation
            j = next(j for j in in_jsonld if j["author"] and norm(j["author"]).casefold() == ca)
            binding, date, block = "STRUCTURED", j["date"], j["body"]
        elif ca and authors_q and ca not in authors_q:
            rejected_claim = rejected_claim or "AUTHOR_NOT_IN_STRUCTURED_DATA"
        elif ca and len(authors_q) > 1:
            rejected_claim = rejected_claim or "AMBIGUOUS_AUTHORSHIP"
        if binding is None:
            block, date = in_jsonld[0]["body"], in_jsonld[0]["date"]
    ugc_q = [b for b in blocks_q if b["type"] == "MICRODATA_UGC" or (b["type"] == "MARKUP_UGC" and b["repeated"])]
    if binding is None and ugc_q:
        inner = min(ugc_q, key=lambda b: len(b["text"]))
        names = {n.casefold() for b in ugc_q for n in b["authors"]}
        if ca and ca in {n.casefold() for n in inner["authors"]} and names == {ca}:
            binding, block = ("STRUCTURED" if inner["type"] == "MICRODATA_UGC" else "COMMENT_BLOCK"), inner["text"]
            date = _date_near(inner["text"])
        elif ca:
            rejected_claim = rejected_claim or ("AMBIGUOUS_AUTHORSHIP" if len(names) > 1 else "AUTHOR_NOT_IN_CONTRIBUTION_BLOCK")
        if block is None:
            block = inner["text"]
    if binding is None and ca and not rejected_claim:
        rejected_claim = "NO_RELIABLE_AUTHOR_BLOCK"        # un pseudo voisin n'est jamais une preuve de paternité
    author = author if binding else None
    if block is None:
        block = text[max(0, pos - 800): pos + len(q) + 800] if pos >= 0 else (in_site_content[0]["body"] if in_site_content else q)
    if binding is None:
        binding = "UNBOUND"
    if date is None and pos >= 0:
        date = _date_near(text[max(0, pos - 300): pos])
    before = text[max(0, pos - 800): pos] if pos >= 0 else ""
    # Mentions de traduction/syndication placées juste après l'avis (ex. « Publié à l'origine sur … Traduit de … »).
    after = text[pos + len(q): pos + len(q) + 250] if pos >= 0 else ""
    foreign = any(mk in before for mk in FOREIGN_MARKERS) or any(mk in block for mk in SYNDICATION_MARKERS) \
        or any(mk in after for mk in SYNDICATION_MARKERS)
    generic_conf = {"geo": "RESIDENCE_ONLY", "source_type": stype}
    market_geo, basis = _resolve_geo(generic_conf, block, location_claim, foreign, None, profile_zone=profile_zone)
    if foreign:
        basis = "SYNDICATED_OR_FOREIGN"
    pq = persona_mod.qualify_claim(persona_claim, block, persona_profile) if persona_claim else None
    bound = binding != "UNBOUND"
    return _save(mdir, {**rc, "status": "QUOTE_VERIFIED", "binding": binding,
                        **({"author_claim_rejected": rejected_claim} if rejected_claim else {}),
                        "evidence_confidence": {"STRUCTURED": "HIGH", "COMMENT_BLOCK": "MEDIUM"}.get(binding, "LOW"),
                        "author_key": author_key(mission_id, author) if bound else f"unbound:{rid}",
                        "author_site_key": author_site_key(mission_id, host, author) if bound else f"unbound:{rid}",
                        "author_bound": bound, "testimony_date": date, "market_geo": market_geo, "geo_basis": basis,
                        "solicited": any(m in block.casefold() for m in INCENTIVE_MARKERS),
                        **({"persona": pq} if pq else {})})


def _nature(raw: bytes, q: str, conf: dict | None, in_jsonld: list[dict]) -> tuple[str, str, list[dict]]:
    """Nature de la citation, établie par la STRUCTURE DE LA PAGE (jamais par le source_kind proposé) :
    USER_CONTRIBUTION_ADAPTER / _STRUCTURED / _MARKUP (blocs répétés de commentaires, avis, messages),
    EDITORIAL_OR_MARKETING (fiche produit, article, FAQ du site, témoignage marketing), UNCERTAIN_NATURE."""
    if conf is not None:
        return "USER_CONTRIBUTION_ADAPTER", "ADAPTER", []
    blocks_q = [b for b in _contribution_blocks(raw) if q in b["text"]]
    if in_jsonld:
        return "USER_CONTRIBUTION_STRUCTURED", in_jsonld[0]["kind"], blocks_q
    if any(b["type"] == "MICRODATA_UGC" for b in blocks_q):
        return "USER_CONTRIBUTION_STRUCTURED", "REVIEW", blocks_q
    ugc = [b for b in blocks_q if b["type"] == "MARKUP_UGC" and b["repeated"]]
    editorial = [b for b in blocks_q if b["type"] in ("EDITORIAL", "MARKETING_TESTIMONIAL")]
    if ugc and not (editorial and min(len(b["text"]) for b in editorial) < min(len(b["text"]) for b in ugc)):
        g = min(ugc, key=lambda b: len(b["text"]))["group"].split(":")[1]
        return "USER_CONTRIBUTION_MARKUP", ("REVIEW" if g.startswith(("review", "avis")) else "COMMENT" if g.startswith(("comment", "commentaire"))
                                            else "QA" if g.startswith(("answer", "reponse")) else "FORUM"), blocks_q
    if editorial or any(q in j["body"] for j in _jsonld_items(raw, include_editorial=True) if j["kind"] == "EDITORIAL"):
        return "EDITORIAL_OR_MARKETING", "NEWS", blocks_q
    m = re.search(rb'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)', raw, re.I)
    if m and q in norm(html.unescape(m.group(1).decode("utf-8", errors="replace"))):
        return "EDITORIAL_OR_MARKETING", "NEWS", blocks_q
    return "UNCERTAIN_NATURE", "OTHER", blocks_q


def _verify_adapter(rc: dict, conf: dict, text: str, q: str, pos: int, author: str, location_claim, mission_id) -> dict:
    """Extracteur d'en-tête éprouvé (ancien mécanisme, conservé comme niveau de confiance ADAPTER)."""
    if pos < 0:
        return {**rc, "status": "QUOTE_NOT_FOUND"}
    found, why = None, "AUTHOR_HEADER_NOT_FOUND"
    while pos >= 0 and found is None:
        heads = [m for m in re.finditer(conf["header"], text) if m.start() < pos]
        if heads:
            h = heads[-1]
            nxt = re.search(conf["header"], text[h.end():])
            block = text[h.start(): h.end() + (nxt.start() if nxt else 6000)]
            ha, ca = norm(h.group("author")).casefold(), norm(author).casefold()
            # zooplus : la variante produit suit le pseudo dans l'en-tête (« Yvorel Camille L 140 x l 76 cm »).
            if ha != ca and not (conf.get("author_prefix") and ha.startswith(ca + " ")):
                why = "AUTHOR_MISMATCH"
            elif pos + len(q) > h.start() + len(block):
                why = "QUOTE_OUTSIDE_AUTHOR_BLOCK"
            else:
                found = (h, block)
                break
        pos = text.find(q, pos + 1)
    if found is None:
        return {**rc, "status": why}
    h, block = found
    foreign = any(0 <= text.find(mk) < pos for mk in FOREIGN_MARKERS) \
        or any(mk in block for mk in conf.get("foreign_markers") or [])
    extra = h.groupdict().get("extra")
    market_geo, basis = _resolve_geo(conf, block, location_claim, foreign, extra,
                                     profile_zone=text[h.start(): min(h.end() + 120, pos)])
    return {**rc, "status": "QUOTE_VERIFIED", "author_key": author_key(mission_id, author), "author_bound": True,
            "contribution_nature": "USER_CONTRIBUTION_ADAPTER",
            "testimony_date": h.groupdict().get("date"), "market_geo": market_geo, "geo_basis": basis,
            "solicited": bool(conf.get("solicited")) or any(m in block.casefold() for m in INCENTIVE_MARKERS),
            "syndicated_from": extra if basis == "SYNDICATED_ORIGIN_UNKNOWN" else None, "_block": block}


def _save(mdir: Path, rc: dict) -> dict:
    p = Path(mdir) / "RESEARCH_INBOX" / "receipts.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rc, ensure_ascii=False) + "\n")
    return rc


# --- Études publiques : vérification générique minimale (pages HTML accessibles) ---
def _fold(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", norm(t).casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


FR_COUNTRY_WORDS = ("france", "francais", "francaise", "francaises", "metropole", "metropolitaine")


def verify_study(mdir: Path, *, url: str, figure_quote: str, date_claim: str | None, country_claim: str | None,
                 population_claim: str | None, link_terms: list[str] | None, method_claim: str | None = None,
                 denominator_claim: str | None = None, fetcher=http_fetch, robots_cache: dict | None = None) -> dict:
    """Vérifie, sur la page HTML réellement téléchargée, la donnée chiffrée exacte et son contexte : date, pays, population,
    lien avec le micro-problème (au moins un terme de signature près de la donnée), méthode/dénominateur s'ils sont déclarés.
    VERIFIED seulement si tous les éléments obligatoires sont retrouvés près de la donnée. PDF et formats non pris en charge :
    jamais vérifiés (limite explicite). Le moteur ignore tout statut de vérification proposé par LLM_Provider."""
    mdir = Path(mdir)
    rid = "STU-" + hashlib.sha256(f"{url}|{norm(figure_quote)}".encode()).hexdigest()[:16]
    rc = {"receipt_id": rid, "url": url, "figure_quote": norm(figure_quote), "checked_at": _now(), "engine": "web_capture/study-1"}
    bad = check_url(url)
    if bad:
        return _save_study(mdir, {**rc, "status": bad})
    if urlsplit(url).path.casefold().endswith(".pdf"):
        return _save_study(mdir, {**rc, "status": "UNSUPPORTED_FORMAT_PDF", "limit": "PDF non vérifiable par le moteur"})
    if len(norm(figure_quote)) < 8 or not re.search(r"\d", figure_quote):
        return _save_study(mdir, {**rc, "status": "FIGURE_WITHOUT_NUMBER"})
    if not robots_allows(url, fetcher, robots_cache):
        return _save_study(mdir, {**rc, "status": "BLOCKED_ROBOTS"})
    status, final_url, raw = fetcher(url)
    if status == 399:
        return _save_study(mdir, {**rc, "status": "BLOCKED_CROSS_HOST_REDIRECT"})
    if status != 200 or not raw:
        return _save_study(mdir, {**rc, "status": f"BLOCKED_HTTP_{status}"})
    if raw[:5] == b"%PDF-":
        return _save_study(mdir, {**rc, "status": "UNSUPPORTED_FORMAT_PDF", "limit": "PDF non vérifiable par le moteur"})
    if b"\x00" in raw[:2048] or b"<" not in raw[:4096]:
        return _save_study(mdir, {**rc, "status": "UNSUPPORTED_FORMAT", "limit": "seules les pages HTML sont vérifiées"})
    sha = hashlib.sha256(raw).hexdigest()
    snap = mdir / "RESEARCH_INBOX" / "SNAPSHOTS" / f"{sha}.html.gz"
    snap.parent.mkdir(parents=True, exist_ok=True)
    if not snap.exists():
        snap.write_bytes(gzip.compress(raw))
    text = html_to_text(raw)
    pos = text.find(norm(figure_quote))
    rc.update(sha256=sha, final_url=final_url, host=urlsplit(url).hostname.casefold(),
              registrable_domain=registrable_domain(urlsplit(url).hostname))
    if pos < 0:
        return _save_study(mdir, {**rc, "status": "FIGURE_NOT_FOUND"})
    win = _fold(text[max(0, pos - 1500): pos + len(figure_quote) + 1500])
    page = _fold(text)
    meta_dates = " ".join(str(j.get("date") or "") for j in _jsonld_items(raw))
    checks = {
        "date": bool(date_claim) and (_fold(date_claim) in win or _fold(date_claim) in _fold(meta_dates)),
        "country": bool(country_claim) and _fold(country_claim) in win,
        "population": bool(population_claim) and _fold(population_claim) in win,
        "micro_link": any(_fold(t) and _fold(t) in win for t in (link_terms or [])),
    }
    optional = {k: _fold(v) in page for k, v in (("method", method_claim), ("denominator", denominator_claim)) if v}
    missing = [k for k, ok in checks.items() if not ok] + [k for k, ok in optional.items() if not ok]
    is_fr = bool(country_claim) and any(w in _fold(country_claim) for w in FR_COUNTRY_WORDS)
    return _save_study(mdir, {**rc, "status": "VERIFIED" if not missing else "PARTIALLY_VERIFIED",
                              "checks": checks, "optional_checks": optional, "missing": missing,
                              "country": "FR" if (is_fr and checks["country"]) else "UNKNOWN_OR_NON_FR"})


def _save_study(mdir: Path, rc: dict) -> dict:
    p = Path(mdir) / "RESEARCH_INBOX" / "study_receipts.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rc, ensure_ascii=False) + "\n")
    return rc
