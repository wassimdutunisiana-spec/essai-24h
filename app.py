import os
import re
import secrets
import string
import threading
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

import requests
from flask import Flask, redirect, render_template_string, request

app = Flask(__name__)

# --- Configuration (le token vient des variables d'environnement de Render) ---
API_TOKEN = os.environ.get("PANEL_API_TOKEN")
BASE_URL = "https://api.mypanel.cx/api/auto"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
TEMPLATE_ID = 183703981670826   # template par défaut
PERIOD = 0                      # essai 24 h uniquement
DEVICE_TYPE = "m3u"             # m3u uniquement

# --- Anti-abus ---
CLICK_COOLDOWN = int(os.environ.get("CLICK_COOLDOWN", "300"))        # blocage du bouton : 5 min
WINDOW_SECONDS = 24 * 3600                                # fenêtre glissante de 24 h
MAX_TRIALS_PER_IP = int(os.environ.get("MAX_TRIALS_PER_IP", "5"))     # essais max par IP / 24 h
RESULT_TTL = 600                                          # un résultat reste consultable 10 min
MAX_TRIALS_PER_DAY = int(os.environ.get("MAX_TRIALS_PER_DAY", "50"))  # plafond global

_lock = threading.Lock()
_results = {}                  # jeton -> (creds, erreur, expiration) : résultat affiché après redirection
_last_click = {}               # ip -> timestamp du dernier clic pris en compte
_trials = {}                   # ip -> liste des timestamps des essais créés
_daily = {"day": date.today(), "count": 0}

PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0f1226">
<title>Essai gratuit 24 h</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%23ffcf5c'/%3E%3Cpath d='M24 18l22 14-22 14z' fill='%230f1226'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,800&display=swap" rel="stylesheet">
<style>
 :root{--bg:#0f1226;--card:#181d3a;--line:#2d3566;--field:#10142c;--text:#eef0fa;--muted:#a3abd0;--accent:#ffcf5c;--ink:#0f1226;--err:#ff8a8a}
 *{box-sizing:border-box}
 html{color-scheme:dark}
 body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font-family:'Bricolage Grotesque',system-ui,-apple-system,'Segoe UI',sans-serif;line-height:1.5;display:flex;justify-content:center;padding:clamp(24px,8vh,80px) 16px 48px}
 main{width:100%;max-width:460px}
 h1{font-size:clamp(2rem,8vw,2.75rem);line-height:1.08;font-weight:800;letter-spacing:-.02em;margin:0 0 .6rem}
 .lead{color:var(--muted);font-size:1.05rem;margin:0 0 1.8rem;max-width:34ch}
 .go{display:block;width:100%;font:inherit;font-weight:600;font-size:1.1rem;padding:1rem 1.4rem;border:0;border-radius:999px;background:var(--accent);color:var(--ink);cursor:pointer;transition:transform .12s,filter .12s}
 .go:hover:not(:disabled){filter:brightness(1.07)}
 .go:active:not(:disabled){transform:scale(.985)}
 .go:disabled{background:var(--card);color:var(--muted);border:1px solid var(--line);cursor:not-allowed}
 :focus-visible{outline:3px solid var(--accent);outline-offset:3px}
 .err{margin:1rem 0 0;color:var(--err);font-size:.98rem}
 .err[hidden]{display:none}
 .pass{margin-top:2rem;background:var(--card);border:1px solid var(--line);border-radius:22px;overflow:hidden;animation:in .5s ease-out}
 .pass h2{margin:0;padding:1.2rem 1.3rem .4rem;font-size:1.25rem;font-weight:600}
 .note{margin:0;padding:0 1.3rem 1rem;color:var(--accent);font-size:.93rem}
 .row{display:flex;align-items:center;gap:.8rem;margin:0 1rem .6rem;padding:.65rem .8rem;background:var(--field);border-radius:12px}
 .row>div{min-width:0;flex:1}
 .lab{display:block;color:var(--muted);font-size:.8rem}
 .val{display:block;font-size:1.05rem;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;user-select:all;font-variant-numeric:tabular-nums}
 .cp{flex:none;font:inherit;font-size:.9rem;font-weight:600;padding:.45rem .85rem;border-radius:999px;border:1px solid var(--line);background:transparent;color:var(--text);cursor:pointer;min-width:5.2rem}
 .cp:hover{border-color:var(--accent);color:var(--accent)}
 .stub{position:relative;margin-top:1rem;padding:1.1rem 1.3rem 1.2rem;border-top:2px dashed var(--line);display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}
 .stub::before,.stub::after{content:"";position:absolute;top:-13px;width:24px;height:24px;border-radius:50%;background:var(--bg);border:1px solid var(--line)}
 .stub::before{left:-13px}.stub::after{right:-13px}
 .exp{font-size:.92rem;color:var(--muted)}
 .exp b{color:var(--text)}
 .all{font:inherit;font-weight:600;font-size:.95rem;padding:.55rem 1.1rem;border-radius:999px;border:0;background:var(--accent);color:var(--ink);cursor:pointer}
 .help{margin:1.2rem 0 0;color:var(--muted);font-size:.93rem}
 @keyframes in{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
 @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
<main>
 <h1>Testez gratuitement pendant 24 h</h1>
 <p class="lead">Un clic et vos accès s'affichent ici. Sans inscription.</p>

 <form method="post" id="f">
  <button type="submit" class="go" id="go"{% if wait|int > 0 %} disabled{% endif %}>Obtenir mon essai</button>
 </form>

 {% if error %}<p class="err" id="err" role="alert">{{ error }}</p>{% endif %}

 {% if creds %}
 {% set everything %}{% if creds.server %}Serveur : {{ creds.server }}
{% endif %}Identifiant : {{ creds.username }}
Mot de passe : {{ creds.password }}{% if creds.url %}
Lien : {{ creds.url }}{% endif %}{% endset %}
 <section class="pass" aria-label="Vos accès">
  <h2>Vos accès</h2>
  <p class="note">Notez-les maintenant : cette page ne les affichera que quelques minutes.</p>
  {% if creds.server %}
  <div class="row"><div><span class="lab">Serveur</span><span class="val">{{ creds.server }}</span></div><button type="button" class="cp" data-copy="{{ creds.server }}">Copier</button></div>
  {% endif %}
  <div class="row"><div><span class="lab">Identifiant</span><span class="val">{{ creds.username }}</span></div><button type="button" class="cp" data-copy="{{ creds.username }}">Copier</button></div>
  <div class="row"><div><span class="lab">Mot de passe</span><span class="val">{{ creds.password }}</span></div><button type="button" class="cp" data-copy="{{ creds.password }}">Copier</button></div>
  {% if creds.url %}
  <div class="row"><div><span class="lab">Lien de la playlist</span><span class="val">{{ creds.url }}</span></div><button type="button" class="cp" data-copy="{{ creds.url }}">Copier</button></div>
  {% endif %}
  <div class="stub">
   <span class="exp">{% if creds.expires %}Expire le <b>{{ creds.expires }}</b>{% else %}Valable 24 h{% endif %}</span>
   <button type="button" class="all" data-copy="{{ everything }}">Tout copier</button>
  </div>
 </section>
 <p class="help">Collez le lien, ou le serveur avec l'identifiant et le mot de passe, dans votre application IPTV.</p>
 {% endif %}
</main>
<script>
 var btn = document.getElementById('go');
 var LABEL = 'Obtenir mon essai';
 document.getElementById('f').addEventListener('submit', function () {
   btn.disabled = true; btn.textContent = 'Création en cours...';
 });

 function copyText(t) {
   if (navigator.clipboard && window.isSecureContext) { return navigator.clipboard.writeText(t); }
   return new Promise(function (ok) {
     var a = document.createElement('textarea'); a.value = t; document.body.appendChild(a);
     a.select(); document.execCommand('copy'); document.body.removeChild(a); ok();
   });
 }
 document.querySelectorAll('[data-copy]').forEach(function (b) {
   b.addEventListener('click', function () {
     var old = b.textContent;
     copyText(b.dataset.copy).then(function () {
       b.textContent = 'Copié'; setTimeout(function () { b.textContent = old; }, 1500);
     });
   });
 });

 var left = {{ wait|int }};
 function fmt(s) { return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
 function tick() {
   if (left <= 0) {
     btn.disabled = false; btn.textContent = LABEL;
     var e = document.getElementById('err'); if (e) { e.hidden = true; }
     return;
   }
   btn.disabled = true; btn.textContent = 'Nouvel essai dans ' + fmt(left);
   left--; setTimeout(tick, 1000);
 }
 if (left > 0) { tick(); }
</script>
</body></html>"""


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or "unknown"


def rand_string(n):
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def find_value(obj, names):
    """Cherche (même dans des sous-objets) la première clé correspondant à `names`."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key.lower() in names and isinstance(val, (str, int)) and str(val).strip():
                return str(val)
        for val in obj.values():
            found = find_value(val, names)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_value(item, names)
            if found:
                return found
    return None


def create_trial():
    username, password = "u" + rand_string(9), rand_string(12)
    params = {
        "api_token": API_TOKEN,
        "template": TEMPLATE_ID,
        "device_type": DEVICE_TYPE,
        "period": PERIOD,
        "username": username,
        "password": password,
    }
    r = requests.get(f"{BASE_URL}/create", params=params, headers=HEADERS, timeout=30)
    if r.status_code == 429:
        return None, "Trop de demandes en ce moment, réessayez dans une minute."
    try:
        data = r.json()
    except ValueError:
        print("Réponse non-JSON :", r.status_code, r.text[:200])
        return None, "Service momentanément indisponible."
    if not data.get("success"):
        print("Erreur API :", r.status_code, data.get("message"))  # visible dans les logs Render
        return None, "Service momentanément indisponible."
    payload = data.get("data") or data
    print("Réponse du panel reçue, clés :", list(payload) if isinstance(payload, dict) else type(payload))

    m3u = find_value(payload, {"m3u_link", "m3u", "m3u_url", "url", "link", "playlist"})
    server = real_user = real_pass = None

    # 1) le plus fiable : lire le lien m3u (serveur, identifiant, mot de passe)
    if m3u:
        parsed = urlparse(m3u)
        qs = parse_qs(parsed.query)
        real_user = (qs.get("username") or [None])[0]
        real_pass = (qs.get("password") or [None])[0]
        if parsed.scheme and parsed.netloc:
            server = f"{parsed.scheme}://{parsed.netloc}"

    # 2) sinon : champs directs ou texte "xtream_details"
    real_user = real_user or find_value(payload, {"username", "user", "login"})
    real_pass = real_pass or find_value(payload, {"password", "pass", "pwd"})
    if not (real_user and real_pass):
        details = find_value(payload, {"xtream_details"}) or ""
        mu = re.search(r"Username\*?\s*=\s*(\S+)", details)
        mp = re.search(r"Password\*?\s*=\s*(\S+)", details)
        ms = re.search(r"Server address\*?\s*=\s*(\S+)", details)
        real_user = real_user or (mu.group(1) if mu else None)
        real_pass = real_pass or (mp.group(1) if mp else None)
        server = server or (ms.group(1) if ms else None)

    if not real_user or not real_pass:
        print("Identifiants introuvables dans la réponse du panel.")
        return None, "Compte créé, mais les identifiants n'ont pas pu être récupérés. Contactez le support."

    creds = {
        "server": server,
        "username": real_user,
        "password": real_pass,
        "url": m3u,
        "expires": find_value(payload, {"exp_date", "expires_at", "expiry", "expire", "expires"}),
    }
    return creds, None


COOLDOWN_MSG = "Code déjà généré, veuillez réessayer dans 5 minutes."


def cooldown_left(ip, now):
    """Secondes restantes avant que le bouton soit de nouveau actif (0 = actif)."""
    return max(0, int(CLICK_COOLDOWN - (now - _last_click.get(ip, 0)) + 0.999))


def finish(creds=None, error=None):
    """Mémorise le résultat et redirige vers une simple page GET (Post/Redirect/Get).
    Ainsi, recharger la page ou revenir en arrière ne renvoie JAMAIS le formulaire."""
    now = time.time()
    with _lock:
        for k in [k for k, v in _results.items() if v[2] < now]:
            del _results[k]
        token = secrets.token_urlsafe(16)
        _results[token] = (creds, error, now + RESULT_TTL)
    return redirect(f"/?r={token}", code=303)


@app.route("/", methods=["GET", "POST"])
def index():
    ip, now = client_ip(), time.time()

    # --- Consulter la page : n'a JAMAIS d'effet sur le panel ---
    if request.method == "GET":
        with _lock:
            wait = cooldown_left(ip, now)
            entry = _results.get(request.args.get("r", ""))
            if entry and entry[2] < now:
                entry = None
        if entry:
            creds, error, _ = entry
        else:
            creds, error = None, (COOLDOWN_MSG if wait else None)
        return render_template_string(PAGE, creds=creds, error=error, wait=wait)

    # --- Créer un essai : uniquement quand on clique sur le bouton (POST) ---
    if not API_TOKEN:
        return render_template_string(PAGE, creds=None, error="Service non configuré.", wait=0), 500

    with _lock:
        # 1) bouton bloqué 5 min après un clic
        if cooldown_left(ip, now):
            return redirect("/", code=303)
        # 2) plafond global journalier
        if _daily["day"] != date.today():
            _daily.update(day=date.today(), count=0)
        if _daily["count"] >= MAX_TRIALS_PER_DAY:
            quota_error = "Le quota d'essais du jour est atteint, revenez demain."
        else:
            quota_error = None
            # 3) limite par IP sur 24 h
            recent = [t for t in _trials.get(ip, []) if now - t < WINDOW_SECONDS]
            _trials[ip] = recent
            if len(recent) >= MAX_TRIALS_PER_IP:
                quota_error = f"Limite de {MAX_TRIALS_PER_IP} essais par 24 h atteinte depuis votre connexion."
            else:
                # on enregistre le clic tout de suite (évite les doubles clics simultanés)
                _last_click[ip] = now
    if quota_error:
        return finish(error=quota_error)

    try:
        creds, error = create_trial()
    except requests.RequestException as e:
        print("Erreur réseau :", e)
        creds, error = None, "Service momentanément indisponible."

    with _lock:
        if creds:
            _trials.setdefault(ip, []).append(now)
            _daily["count"] += 1
        elif error and "Compte créé" not in error:
            _last_click.pop(ip, None)   # échec : on ne pénalise pas le visiteur
    return finish(creds=creds, error=error)


@app.route("/healthz")
def healthz():
    return "ok"
