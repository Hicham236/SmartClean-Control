import io
import json
import math
import os
import sqlite3
import textwrap
import uuid
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

try:
    from streamlit_js_eval import get_geolocation
except Exception:
    get_geolocation = None

APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = APP_DIR / "configuration"
DATA_DIR = APP_DIR / "data"
PHOTO_DIR = DATA_DIR / "photos"
DB_PATH = DATA_DIR / "smartclean.db"
LOGO_PATHS = [CONFIG_DIR / "logo_bet.png", CONFIG_DIR / "logo_bet.jpg", CONFIG_DIR / "logo_bet.jpeg"]

for p in [CONFIG_DIR, PHOTO_DIR]:
    p.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="SmartClean Control", page_icon="🧹", layout="wide")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS anomalies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        libelle TEXT NOT NULL,
        montant REAL NOT NULL DEFAULT 0,
        delai INTEGER NOT NULL DEFAULT 0,
        actif INTEGER NOT NULL DEFAULT 1
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS constats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reference TEXT UNIQUE,
        created_at TEXT NOT NULL,
        agent TEXT,
        anomaly_id INTEGER,
        anomaly_label TEXT,
        penalty REAL,
        deadline_days INTEGER,
        lat REAL,
        lon REAL,
        accuracy REAL,
        gps_timestamp TEXT,
        original_path TEXT,
        stamped_path TEXT,
        status TEXT DEFAULT 'Constat initial',
        delegate_response TEXT,
        treatment_date TEXT,
        controller_decision TEXT
    )""")
    if con.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] == 0:
        defaults = [
            ("AN-001", "Conteneur débordant", 0, 24),
            ("AN-002", "Absence de collecte", 0, 24),
            ("AN-003", "Dépôt sauvage", 0, 48),
            ("AN-004", "Balayage non réalisé", 0, 24),
            ("AN-005", "Lavage non réalisé", 0, 48),
        ]
        con.executemany("INSERT INTO anomalies(code,libelle,montant,delai) VALUES(?,?,?,?)", defaults)
    con.commit()
    return con


def font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for f in candidates:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def tile_xy(lat, lon, zoom):
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def get_map(lat, lon, zoom=16, size=360):
    """Build a small OSM map from public tiles. Returns None on failure."""
    try:
        cx, cy = tile_xy(lat, lon, zoom)
        tx, ty = int(cx), int(cy)
        canvas = Image.new("RGB", (size, size), "#eeeeee")
        radius = 1
        tile_px = 256
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                url = f"https://tile.openstreetmap.org/{zoom}/{tx+dx}/{ty+dy}.png"
                r = requests.get(url, timeout=8, headers={"User-Agent": "SmartClean-Control/0.3"})
                r.raise_for_status()
                tile = Image.open(io.BytesIO(r.content)).convert("RGB")
                px = int(size/2 + (tx + dx - cx) * tile_px)
                py = int(size/2 + (ty + dy - cy) * tile_px)
                canvas.paste(tile, (px, py))
        px = int(size/2)
        py = int(size/2)
        d = ImageDraw.Draw(canvas)
        d.ellipse((px-10, py-10, px+10, py+10), fill="#d71920", outline="white", width=4)
        d.ellipse((px-3, py-3, px+3, py+3), fill="white")
        return canvas
    except Exception:
        return None


def reverse_geocode(lat, lon):
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14, "accept-language": "fr"}
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": "SmartClean-Control/0.3"})
        r.raise_for_status()
        a = r.json().get("address", {})
        city = a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or a.get("county") or "Position GPS"
        region = a.get("state") or a.get("region") or ""
        country = a.get("country") or "Maroc"
        return city, region, country
    except Exception:
        return "Position GPS", "", "Maroc"


def make_stamped_photo(original, lat, lon, gps_time, accuracy):
    original = original.convert("RGB")
    max_w = 1400
    if original.width > max_w:
        ratio = max_w / original.width
        original = original.resize((max_w, int(original.height * ratio)), Image.Resampling.LANCZOS)
    w, h = original.size
    panel_h = max(230, int(w * 0.28))
    panel = Image.new("RGB", (w, panel_h), "#f5f5f5")
    draw = ImageDraw.Draw(panel)
    map_size = panel_h - 30
    map_img = get_map(lat, lon, size=map_size)
    if map_img:
        panel.paste(map_img, (15, 15))
    else:
        draw.rectangle((15, 15, 15+map_size, 15+map_size), fill="#dfe7ef", outline="#777")
        draw.ellipse((15+map_size//2-7, 15+map_size//2-7, 15+map_size//2+7, 15+map_size//2+7), fill="#d71920")
    city, region, country = reverse_geocode(lat, lon)
    f_title = font(32, True)
    f = font(25, False)
    f_small = font(21, False)
    x = map_size + 40
    y = 20
    draw.text((x, y), city, font=f_title, fill="#111")
    y += 42
    if region:
        draw.text((x, y), region, font=f, fill="#222")
        y += 34
    draw.text((x, y), country, font=f, fill="#222")
    y += 42
    local_dt = datetime.fromisoformat(gps_time.replace("Z", "+00:00")) if gps_time else datetime.now().astimezone()
    draw.text((x, y), local_dt.strftime("%Y-%m-%d  %H:%M:%S"), font=f, fill="#111")
    y += 35
    draw.text((x, y), f"GPS  {lat:.6f}, {lon:.6f}", font=f_small, fill="#333")
    y += 30
    draw.text((x, y), f"Précision ± {accuracy:.0f} m" if accuracy else "Précision GPS non disponible", font=f_small, fill="#555")
    draw.text((w-220, panel_h-28), "SmartClean", font=f_small, fill="#555")
    return Image.new("RGB", (w, h+panel_h), "white") if False else Image.fromarray(__import__('numpy').vstack([__import__('numpy').array(original), __import__('numpy').array(panel)]))


def save_image(img, path):
    img.save(path, format="JPEG", quality=92, optimize=True)


def next_reference(con, when):
    prefix = when.strftime("%Y%m")
    row = con.execute("SELECT reference FROM constats WHERE reference LIKE ? ORDER BY id DESC LIMIT 1", (prefix+"-%",)).fetchone()
    n = 1
    if row:
        try: n = int(row["reference"].split("-")[-1]) + 1
        except Exception: pass
    return f"{prefix}-{n:04d}"


def build_pdf(row, output):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak

    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=14*mm, leftMargin=14*mm, topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["Normal"], fontSize=8.5, leading=11))
    styles.add(ParagraphStyle(name="Title2", parent=styles["Title"], fontSize=18, leading=22, alignment=1))
    story = []
    logo = next((p for p in LOGO_PATHS if p.exists()), None)
    if logo:
        im = RLImage(str(logo), width=48*mm, height=22*mm, kind="proportional")
        header = Table([[im, Paragraph("<b>FICHE CONSTAT</b>", styles["Title2"])]], colWidths=[65*mm, 105*mm])
        header.setStyle(TableStyle([["VALIGN",(0,0),(-1,-1),"MIDDLE"],["ALIGN",(1,0),(1,0),"CENTER"]]))
        story += [header, Spacer(1,5*mm)]
    else:
        story += [Paragraph("<b>FICHE CONSTAT</b>", styles["Title2"]), Spacer(1,5*mm)]
    data = [
        ["Référence", row["reference"], "Date / heure", row["created_at"]],
        ["Anomalie", row["anomaly_label"] or "—", "Statut", row["status"] or "—"],
        ["Coordonnées GPS", f"{row['lat']:.6f}, {row['lon']:.6f}" if row["lat"] is not None else "—", "Précision", f"± {row['accuracy']:.0f} m" if row["accuracy"] else "—"],
        ["Pénalité estimée", f"{row['penalty']:.2f} DH", "Délai configuré", f"{row['deadline_days']} jour(s)"],
    ]
    t = Table(data, colWidths=[36*mm, 55*mm, 38*mm, 51*mm])
    t.setStyle(TableStyle([["GRID",(0,0),(-1,-1),0.4,colors.grey],["BACKGROUND",(0,0),(0,-1),colors.whitesmoke],["BACKGROUND",(2,0),(2,-1),colors.whitesmoke],["VALIGN",(0,0),(-1,-1),"MIDDLE"],["FONTNAME",(0,0),(-1,-1),"Helvetica"],["FONTSIZE",(0,0),(-1,-1),8.5],["BOTTOMPADDING",(0,0),(-1,-1),6],["TOPPADDING",(0,0),(-1,-1),6]]))
    story += [t, Spacer(1,5*mm)]
    if row["stamped_path"] and Path(row["stamped_path"]).exists():
        pic = RLImage(row["stamped_path"], width=180*mm, height=100*mm, kind="proportional")
        story += [pic, Spacer(1,4*mm)]
    story += [Paragraph("<b>Réponse / traitement du délégataire</b>", styles["Small"]), Spacer(1,2*mm)]
    story += [Paragraph((row["delegate_response"] or "").replace("&","&amp;"), styles["Small"]), Spacer(1,4*mm)]
    story += [Paragraph("<b>Émargement</b>", styles["Small"]), Spacer(1,2*mm)]
    sign = Table([["Contrôleur", "Délégataire"], ["", ""], ["", ""]], colWidths=[90*mm, 90*mm], rowHeights=[9*mm, 25*mm, 25*mm])
    sign.setStyle(TableStyle([["GRID",(0,0),(-1,-1),0.5,colors.grey],["BACKGROUND",(0,0),(-1,0),colors.whitesmoke],["VALIGN",(0,0),(-1,-1),"MIDDLE"],["ALIGN",(0,0),(-1,-1),"CENTER"]]))
    story += [sign]
    doc.build(story)


con = db()

st.sidebar.title("SmartClean Control")
page = st.sidebar.radio("Navigation", ["Nouveau constat", "Suivi des constats", "Configuration CPS", "Synthèse"])

if page == "Nouveau constat":
    st.title("Nouveau constat")
    st.caption("La position GPS est récupérée par le téléphone via le navigateur. Aucune saisie manuelle de latitude/longitude n'est prévue.")

    if get_geolocation is None:
        st.error("Le module de géolocalisation n'est pas installé. Ajoutez streamlit-js-eval aux dépendances.")
        st.stop()

    loc = get_geolocation()
    gps_ok = bool(loc and "coords" in loc)
    if gps_ok:
        c = loc["coords"]
        lat, lon = float(c["latitude"]), float(c["longitude"])
        accuracy = float(c.get("accuracy") or 0)
        gps_time = datetime.now().astimezone().isoformat()
        st.success(f"GPS acquis automatiquement — précision ±{accuracy:.0f} m")
    elif loc and "error" in loc:
        st.error(f"GPS indisponible : {loc['error'].get('message','erreur inconnue')}. Autorisez la localisation du navigateur puis rechargez la page.")
        st.stop()
    else:
        st.info("Acquisition automatique de la position en cours… Autorisez la localisation si le navigateur le demande.")
        st.stop()

    anomalies = con.execute("SELECT * FROM anomalies WHERE actif=1 ORDER BY id").fetchall()
    options = {f"{a['code']} — {a['libelle']}": a for a in anomalies}
    selected = st.selectbox("Anomalie constatée", list(options.keys()))
    anomaly = options[selected]

    photo = st.camera_input("Prendre la photo du constat")
    if photo:
        taken_at = datetime.now().astimezone()
        original = Image.open(photo).convert("RGB")
        stamped = make_stamped_photo(original, lat, lon, taken_at.isoformat(), accuracy)
        ref = next_reference(con, taken_at)
        token = uuid.uuid4().hex[:8]
        original_path = PHOTO_DIR / f"{ref}_{token}_original.jpg"
        stamped_path = PHOTO_DIR / f"{ref}_{token}_estampille.jpg"
        save_image(original, original_path)
        save_image(stamped, stamped_path)
        con.execute("""INSERT INTO constats(reference,created_at,agent,anomaly_id,anomaly_label,penalty,deadline_days,lat,lon,accuracy,gps_timestamp,original_path,stamped_path)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (ref,taken_at.isoformat(),"",anomaly["id"],anomaly["libelle"],anomaly["montant"],anomaly["delai"],lat,lon,accuracy,gps_time,str(original_path),str(stamped_path)))
        con.commit()
        row = con.execute("SELECT * FROM constats WHERE reference=?", (ref,)).fetchone()
        st.success(f"Constat {ref} enregistré.")
        st.image(stamped, caption="Photo estampillée générée automatiquement", use_container_width=True)
        pdf_path = DATA_DIR / f"Fiche_constat_{ref}.pdf"
        build_pdf(row, pdf_path)
        with open(pdf_path, "rb") as f:
            st.download_button("Télécharger la fiche constat PDF", f.read(), file_name=pdf_path.name, mime="application/pdf")

elif page == "Suivi des constats":
    st.title("Suivi des constats")
    rows = con.execute("SELECT * FROM constats ORDER BY id DESC").fetchall()
    if not rows:
        st.info("Aucun constat enregistré.")
    else:
        for r in rows:
            with st.expander(f"{r['reference']} — {r['anomaly_label']} — {r['status']}"):
                st.write(f"**Date :** {r['created_at']}")
                st.write(f"**GPS :** {r['lat']:.6f}, {r['lon']:.6f} — précision ±{r['accuracy']:.0f} m")
                if r['stamped_path'] and Path(r['stamped_path']).exists(): st.image(r['stamped_path'], width=650)
                st.write(f"**Pénalité estimée configurée :** {r['penalty']:.2f} DH — délai : {r['deadline_days']} jour(s)")
                response = st.text_area("Réponse / traitement du délégataire", value=r['delegate_response'] or "", key=f"resp_{r['id']}")
                decision = st.selectbox("Décision du contrôleur", ["En attente", "Validé", "Rejeté"], index=["En attente","Validé","Rejeté"].index(r['controller_decision'] or "En attente"), key=f"dec_{r['id']}")
                if st.button("Enregistrer le traitement", key=f"save_{r['id']}"):
                    status = "Traité" if response.strip() else "Constat initial"
                    con.execute("UPDATE constats SET delegate_response=?, treatment_date=?, controller_decision=?, status=? WHERE id=?", (response, datetime.now().astimezone().isoformat() if response.strip() else None, decision, status, r['id']))
                    con.commit()
                    st.success("Traitement enregistré. Rechargez la fiche pour produire le PDF mis à jour.")
                pdf_path = DATA_DIR / f"Fiche_constat_{r['reference']}.pdf"
                current = con.execute("SELECT * FROM constats WHERE id=?", (r['id'],)).fetchone()
                build_pdf(current, pdf_path)
                with open(pdf_path, "rb") as f: st.download_button("Télécharger le PDF", f.read(), file_name=pdf_path.name, mime="application/pdf", key=f"pdf_{r['id']}")

elif page == "Configuration CPS":
    st.title("Configuration des anomalies — CPS")
    st.caption("Les montants et délais sont des paramètres du CPS pilote. Ils ne sont pas présentés comme des règles générales marocaines.")
    rows = con.execute("SELECT * FROM anomalies ORDER BY id").fetchall()
    for a in rows:
        c1,c2,c3,c4,c5 = st.columns([1,4,2,2,1])
        c1.write(a['code']); c2.write(a['libelle'])
        m = c3.number_input("Montant", value=float(a['montant']), min_value=0.0, step=100.0, key=f"m_{a['id']}")
        d = c4.number_input("Délai (j)", value=int(a['delai']), min_value=0, step=1, key=f"d_{a['id']}")
        active = c5.checkbox("Actif", value=bool(a['actif']), key=f"a_{a['id']}")
        if st.button("Enregistrer", key=f"u_{a['id']}"):
            con.execute("UPDATE anomalies SET montant=?, delai=?, actif=? WHERE id=?", (m,d,1 if active else 0,a['id']))
            con.commit(); st.success("Paramètres enregistrés.")
    st.divider()
    st.subheader("Ajouter une anomalie")
    with st.form("new_anomaly"):
        code=st.text_input("Code")
        lib=st.text_input("Libellé")
        mon=st.number_input("Montant estimé (DH)", min_value=0.0, step=100.0)
        delai=st.number_input("Délai contractuel (jours)", min_value=0, step=1)
        if st.form_submit_button("Ajouter"):
            try:
                con.execute("INSERT INTO anomalies(code,libelle,montant,delai) VALUES(?,?,?,?)", (code,lib,mon,delai)); con.commit(); st.success("Anomalie ajoutée.")
            except sqlite3.IntegrityError: st.error("Ce code existe déjà.")

else:
    st.title("Synthèse mensuelle")
    rows = con.execute("SELECT * FROM constats ORDER BY created_at").fetchall()
    if rows:
        import pandas as pd
        df = pd.DataFrame([dict(r) for r in rows])
        df["mois"] = pd.to_datetime(df["created_at"]).dt.strftime("%Y-%m")
        synth = df.groupby(["mois","anomaly_label"]).size().reset_index(name="nombre")
        st.dataframe(synth, use_container_width=True)
        st.bar_chart(df.groupby("mois").size())
        st.download_button("Exporter CSV", df.to_csv(index=False).encode("utf-8-sig"), "smartclean_constats.csv", "text/csv")
    else:
        st.info("Aucune donnée à synthétiser.")
