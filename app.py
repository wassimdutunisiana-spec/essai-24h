import os
import secrets
import string
import threading
import time
from datetime import date

import requests
from flask import Flask, render_template_string, request

app = Flask(__name__)

# --- Configuration (le token vient des variables d'environnement de Render) ---
API_TOKEN = os.environ.get("PANEL_API_TOKEN")
BASE_URL = "https://api.mypanel.cx/api/auto"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
TEMPLATE_ID = 183703981670826   # template par défaut
PERIOD = 0                      # essai 24 h uniquement
DEVICE_TYPE = "m3u"             # m3u uniquement

# --- Anti-abus ---
WINDOW_SECONDS = 24 * 3600                                # fenêtre glissante de 24 h
MAX_TRIALS_PER_IP = int(os.environ.get("MAX_TRIALS_PER_IP", "5"))     # essais max par IP / 24 h
MAX_TRIALS_PER_DAY = int(os.environ.get("MAX_TRIALS_PER_DAY", "50"))  # plafond global

_lock = threading.Lock()
_trials = {}                   # ip -> liste des timestamps des essais créés
_daily = {"day": date.today(), "count": 0}

PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Essai gratuit 24 h</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:480px;margin:10vh auto;padding:0 16px;text-align:center}
 button{font-size:1.1rem;padding:.8rem 1.6rem;border:0;border-radius:8px;background:#2563eb;color:#fff;cursor:pointer}
 .box{background:#f3f4f6;border-radius:8px;padding:1rem;margin-top:1.5rem;text-align:left;word-break:break-all}
 .err{color:#b91c1c;margin-top:1.5rem}
 @media (prefers-color-scheme: dark){body{background:#111;color:#eee}.box{background:#222}}
</style></head><body>
<h1>Essai gratuit 24 h</h1>
<p>Obtenez vos identifiants de test en un clic.</p>
<form method="post"><button type="submit">Obtenir mon essai</button></form>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
{% if creds %}
<div class="box">
 <p><b>Identifiant :</b> {{ creds.username }}</p>
 <p><b>Mot de passe :</b> {{ creds.password }}</p>
 {% if creds.expires %}<p><b>Expire le :</b> {{ creds.expires }}</p>{% endif %}
 <p>Notez-les maintenant : ils ne seront pas réaffichés.</p>
</div>
{% endif %}
</body></html>"""


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or "unknown"


def rand_string(n):
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


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
    expires = (data.get("data") or {}).get("expires_at")
    return {"username": username, "password": password, "expires": expires}, None


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template_string(PAGE, creds=None, error=None)

    if not API_TOKEN:
        return render_template_string(PAGE, creds=None, error="Service non configuré."), 500

    ip, now = client_ip(), time.time()
    with _lock:
        if _daily["day"] != date.today():
            _daily.update(day=date.today(), count=0)
        if _daily["count"] >= MAX_TRIALS_PER_DAY:
            return render_template_string(PAGE, creds=None,
                error="Le quota d'essais du jour est atteint, revenez demain."), 429
        recent = [t for t in _trials.get(ip, []) if now - t < WINDOW_SECONDS]
        _trials[ip] = recent
        if len(recent) >= MAX_TRIALS_PER_IP:
            return render_template_string(PAGE, creds=None,
                error=f"Limite de {MAX_TRIALS_PER_IP} essais par 24 h atteinte depuis votre connexion."), 429

    try:
        creds, error = create_trial()
    except requests.RequestException as e:
        print("Erreur réseau :", e)
        creds, error = None, "Service momentanément indisponible."

    if creds:
        with _lock:
            _trials.setdefault(ip, []).append(now)
            _daily["count"] += 1
    return render_template_string(PAGE, creds=creds, error=error)


@app.route("/healthz")
def healthz():
    return "ok"
