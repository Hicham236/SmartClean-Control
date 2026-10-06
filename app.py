import os, io, sqlite3, hashlib, json, urllib.request, urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as PDFImage, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm

ROOT=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(ROOT,"smartclean.db")
PROOFS=os.path.join(ROOT,"preuves")
CONFIG=os.path.join(ROOT,"configuration")
os.makedirs(PROOFS,exist_ok=True); os.makedirs(CONFIG,exist_ok=True)
st.set_page_config(page_title="SmartClean Control",page_icon="🧹",layout="wide")

DEFAULTS=["Dépôts de déchets hors conteneur","Débordement de conteneur","Conteneur détérioré ou renversé","Déchets persistants sur chaussée/trottoir","Balayage non réalisé ou insuffisant","Lavage non réalisé ou insuffisant","Collecte non réalisée ou incomplète","Corbeille publique pleine ou détériorée","Autre anomalie définie au CPS"]

def now():
    try: return datetime.now(ZoneInfo("Africa/Casablanca"))
    except Exception: return datetime.now()
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS constats(
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, commune TEXT, contrat TEXT, delegataire TEXT, bet TEXT,
    secteur TEXT, prestation TEXT, date_constat TEXT, heure_constat TEXT, latitude TEXT, longitude TEXT,
    anomaly TEXT, observation TEXT, clause_ref TEXT, deadline_hours INTEGER, image_path TEXT, image_sha256 TEXT,
    status TEXT DEFAULT 'Ouvert', response_at TEXT DEFAULT '', response_note TEXT DEFAULT '',
    response_image_path TEXT DEFAULT '', response_sha256 TEXT DEFAULT '', response_deadline_ok TEXT DEFAULT '',
    validation_status TEXT DEFAULT '', validation_note TEXT DEFAULT '', validation_at TEXT DEFAULT '',
    bet_signatory TEXT DEFAULT '', delegate_signatory TEXT DEFAULT '', commune_signatory TEXT DEFAULT '')""")
    # Migration des bases V0.2 déjà existantes
    cols={x[1] for x in c.execute("PRAGMA table_info(constats)").fetchall()}
    if "reference" not in cols: c.execute("ALTER TABLE constats ADD COLUMN reference TEXT DEFAULT ''")
    if "penalty_amount" not in cols: c.execute("ALTER TABLE constats ADD COLUMN penalty_amount REAL DEFAULT 0")
    c.commit(); return c
def rows():
    c=db(); r=c.execute("SELECT * FROM constats ORDER BY id DESC").fetchall(); c.close(); return r
def sha(b): return hashlib.sha256(b).hexdigest() if b else ""
def esc(s): return str(s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n","<br/>")
def extract_gps_from_photo(raw):
    """Extrait latitude/longitude depuis les métadonnées EXIF GPS de la photo."""
    try:
        im=Image.open(io.BytesIO(raw)); exif=im.getexif(); gps=exif.get(34853)
        if not gps: return None, None
        try: gps=exif.get_ifd(34853)
        except Exception: pass
        def val(x):
            try: return float(x[0])/float(x[1])
            except Exception: return float(x)
        latv,latref=gps.get(2),gps.get(1); lonv,lonref=gps.get(4),gps.get(3)
        if not latv or not lonv: return None, None
        lat=val(latv[0])+val(latv[1])/60+val(latv[2])/3600
        lon=val(lonv[0])+val(lonv[1])/60+val(lonv[2])/3600
        if str(latref).upper().startswith("S"): lat=-lat
        if str(lonref).upper().startswith("W"): lon=-lon
        if not (-90 <= lat <= 90 and -180 <= lon <= 180): return None, None
        return f"{lat:.7f}", f"{lon:.7f}"
    except Exception:
        return None, None

def make_mini_map(lat, lon, outpath):
    """Télécharge une mini-carte statique si le réseau est disponible; retourne None sinon."""
    if not lat or not lon: return None
    try:
        url="https://staticmap.openstreetmap.de/staticmap.php?"+urllib.parse.urlencode({"center":f"{lat},{lon}","zoom":"16","size":"600x220","maptype":"mapnik","markers":f"{lat},{lon},red-pushpin"})
        req=urllib.request.Request(url,headers={"User-Agent":"SmartCleanControl/0.3 (prototype de suivi)"})
        with urllib.request.urlopen(req,timeout=5) as r: data=r.read()
        if not data or len(data)<1000: return None
        Image.open(io.BytesIO(data)).convert("RGB").save(outpath,"JPEG",quality=88)
        return outpath
    except Exception: return None

def stamped(raw, stamp, lat, lon, map_path=None):
    im=Image.open(io.BytesIO(raw)).convert("RGB"); im.thumbnail((1500,1100)); w,h=im.size
    band=max(72,int(h*.10)); footer=Image.new("RGB",(w,band),(12,28,38)); d=ImageDraw.Draw(footer)
    try: f=ImageFont.truetype("arial.ttf",max(16,w//48)); fs=ImageFont.truetype("arial.ttf",max(12,w//60))
    except: f=fs=ImageFont.load_default()
    d.text((12,8),f"Constat | {stamp}",font=f,fill="white")
    d.text((12,band//2),f"GPS : {lat}, {lon}",font=fs,fill=(220,235,245))
    parts=[im,footer]
    if map_path and os.path.exists(map_path):
        try:
            m=Image.open(map_path).convert("RGB"); m.thumbnail((w,260)); mapblock=Image.new("RGB",(w,m.height+30),(255,255,255)); mapblock.paste(m,((w-m.width)//2,25)); ImageDraw.Draw(mapblock).text((8,5),"Mini-carte de localisation — © OpenStreetMap",fill=(30,30,30)); parts.append(mapblock)
        except Exception: pass
    total=sum(x.height for x in parts); canvas=Image.new("RGB",(w,total),(255,255,255)); y=0
    for part in parts: canvas.paste(part,(0,y)); y+=part.height
    return canvas
def make_pdf(r,logo_path):
    b=io.BytesIO(); doc=SimpleDocTemplate(b,pagesize=A4,rightMargin=1.3*cm,leftMargin=1.3*cm,topMargin=1.2*cm,bottomMargin=1.2*cm)
    s=getSampleStyleSheet(); s.add(ParagraphStyle(name="Small",parent=s["BodyText"],fontSize=8,leading=10)); story=[]
    if logo_path and os.path.exists(logo_path):
        try: story.append(PDFImage(logo_path,width=8.4*cm,height=4.0*cm,kind="proportional"))
        except: pass
    story += [Paragraph("FICHE CONSTAT",s["Title"]),Paragraph(f"<b>Référence :</b> {esc(r['reference'] or str(r['id']))} &nbsp; <b>Statut :</b> {esc(r['status'])}",s["BodyText"]),Spacer(1,8)]
    data=[["Commune",r["commune"],"Contrat",r["contrat"]],["Délégataire",r["delegataire"],"BET",r["bet"]],["Secteur/circuit",r["secteur"],"Prestation",r["prestation"]],["Date",r["date_constat"],"Heure",r["heure_constat"]],["Anomalie",r["anomaly"],"Clause CPS",r["clause_ref"] or "À rattacher"],["Pénalité estimée (DH)",f"{float(r['penalty_amount'] or 0):,.2f}","Délai contractuel (h)",str(r["deadline_hours"] or "—")],["Latitude",r["latitude"],"Longitude",r["longitude"]]]
    t=Table([[Paragraph(esc(str(x)),s["Small"]) for x in line] for line in data],colWidths=[2.4*cm,5.1*cm,2.4*cm,5.1*cm])
    t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.4,colors.grey),("BACKGROUND",(0,0),(0,-1),colors.whitesmoke),("BACKGROUND",(2,0),(2,-1),colors.whitesmoke),("VALIGN",(0,0),(-1,-1),"TOP"),("FONTSIZE",(0,0),(-1,-1),8)]))
    story += [t,Spacer(1,8),Paragraph("Description du constat",s["Heading3"]),Paragraph(esc(r["observation"]),s["BodyText"])]
    if r["image_path"] and os.path.exists(r["image_path"]):
        try: story += [Spacer(1,8),Paragraph("Photo initiale et localisation",s["Heading3"]),PDFImage(r["image_path"],width=14*cm,height=12*cm,kind="proportional")]
        except: pass
    story += [Spacer(1,8),Paragraph("Réponse du délégataire",s["Heading3"]),Paragraph(f"<b>Date/heure :</b> {esc(r['response_at'] or 'En attente')}",s["BodyText"]),Paragraph(f"<b>Observations :</b> {esc(r['response_note'] or 'En attente')}",s["BodyText"])]
    try:
        late=(datetime.fromisoformat(r["response_at"])-datetime.fromisoformat(r["created_at"])).total_seconds()/3600>int(r["deadline_hours"] or 24) if r["response_at"] else (now()-datetime.fromisoformat(r["created_at"])).total_seconds()/3600>int(r["deadline_hours"] or 24)
    except: late=False
    if late: story.append(Paragraph(f"<b>Retard constaté : pénalité estimée paramétrée {float(r['penalty_amount'] or 0):,.2f} DH</b> — estimation indicative à confirmer selon le CPS et la procédure contradictoire; aucune pénalité n'est appliquée automatiquement.",s["BodyText"]))
    if r["response_image_path"] and os.path.exists(r["response_image_path"]):
        try: story += [Spacer(1,6),Paragraph("Photo après traitement",s["Heading3"]),PDFImage(r["response_image_path"],width=14*cm,height=9.8*cm,kind="proportional")]
        except: pass
    story += [Spacer(1,8),Paragraph("Validation du contrôleur",s["Heading3"]),Paragraph(f"<b>Décision :</b> {esc(r['validation_status'] or 'En attente')} | <b>Date/heure :</b> {esc(r['validation_at'] or 'En attente')}",s["BodyText"]),Paragraph(f"<b>Observations :</b> {esc(r['validation_note'] or '—')}",s["BodyText"]),Spacer(1,8),Paragraph("ÉMARGEMENTS",s["Heading3"])]
    sig=Table([["BET","Société délégataire","Cellule de contrôle de la commune"],[esc(r["bet_signatory"] or "Nom / qualité"),esc(r["delegate_signatory"] or "Nom / qualité"),esc(r["commune_signatory"] or "Nom / qualité")],["","",""]],colWidths=[5*cm,5*cm,5*cm],rowHeights=[.7*cm,1*cm,3.0*cm])
    sig.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.5,colors.grey),("BACKGROUND",(0,0),(-1,0),colors.whitesmoke),("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("FONTSIZE",(0,0),(-1,-1),7)]))
    story += [sig,Spacer(1,8)]
    doc.build(story); b.seek(0); return b.getvalue()

st.title("🧹 SmartClean Control")
st.caption("Constat d'anomalies • Réponse photo du délégataire • Validation du contrôleur • Synthèse mensuelle")
with st.sidebar:
    st.header("Contrat pilote")
    commune0=st.text_input("Commune","Sidi Harazem"); contrat0=st.text_input("Contrat / marché","Contrat de gestion déléguée — à préciser")
    delegataire0=st.text_input("Société délégataire",""); bet0=st.text_input("BET chargé du suivi","MAPERPRO")
    clause0=st.text_input("Clause CPS par défaut",""); delay0=st.number_input("Délai attendu (heures)",1,720,24)
    # Logo persistant : il est chargé automatiquement depuis configuration/logo_bet.*
    ls=[os.path.join(CONFIG,x) for x in os.listdir(CONFIG) if x.lower().startswith("logo_bet") and x.lower().endswith((".png",".jpg",".jpeg"))]
    logo_path=sorted(ls)[0] if ls else None
    if logo_path: st.caption("Logo BET chargé automatiquement.")
    else: st.warning("Logo BET absent : placez une seule fois votre fichier dans configuration sous le nom logo_bet.png (ou JPG).")
    st.caption("Les pénalités sont estimées à partir de paramètres à renseigner selon le CPS; aucune sanction n’est appliquée automatiquement.")
tabs=st.tabs(["Nouveau constat","Traitement / validation","Historique","Synthèse mensuelle","Liste des anomalies CPS"])
with tabs[4]:
    st.subheader("Configurer les anomalies selon le CPS")
    st.caption("Les montants et délais ci-dessous sont des paramètres de démonstration, pas des valeurs réglementaires. Remplace-les par ceux du CPS signé.")
    af=os.path.join(CONFIG,"anomalies.json")
    if "anom_df" not in st.session_state:
        try:
            with open(af,encoding="utf-8") as f: cfg=json.load(f)
            if isinstance(cfg,list): cfg=[{"anomalie":x,"clause":"","penalite_dh":0,"delai_heures":24} for x in cfg]
        except: cfg=[{"anomalie":x,"clause":"","penalite_dh":0,"delai_heures":24} for x in DEFAULTS]
        st.session_state.anom_df=pd.DataFrame(cfg,columns=["anomalie","clause","penalite_dh","delai_heures"])
    edited=st.data_editor(st.session_state.anom_df,num_rows="dynamic",use_container_width=True,key="anomaly_editor",column_config={"anomalie":"Anomalie CPS","clause":"Article / clause CPS","penalite_dh":st.column_config.NumberColumn("Pénalité estimée (DH)",min_value=0,step=100),"delai_heures":st.column_config.NumberColumn("Délai (heures)",min_value=1,max_value=720,step=1)})
    if st.button("Enregistrer la configuration CPS"):
        cfg=[]
        for _,row in edited.iterrows():
            if str(row.get("anomalie","")).strip(): cfg.append({"anomalie":str(row["anomalie"]).strip(),"clause":str(row.get("clause","") or ""),"penalite_dh":float(row.get("penalite_dh",0) or 0),"delai_heures":int(row.get("delai_heures",24) or 24)})
        with open(af,"w",encoding="utf-8") as f: json.dump(cfg,f,ensure_ascii=False,indent=2)
        st.session_state.anom_df=pd.DataFrame(cfg,columns=["anomalie","clause","penalite_dh","delai_heures"]); st.success("Configuration enregistrée.")
    cfg_list=st.session_state.anom_df.to_dict("records")
    anomaly_options=[str(x.get("anomalie","")) for x in cfg_list if str(x.get("anomalie"," ")).strip()] or DEFAULTS
with tabs[0]:
    st.subheader("Nouveau constat d'anomalie"); st.write("Sur téléphone, autorise l'accès à la caméra et à la localisation.")
    st.markdown("**Importer la photo du constat**")
    st.caption("La photo doit avoir été prise avec une application/appareil qui enregistre les coordonnées GPS dans ses métadonnées EXIF. Aucune saisie manuelle des coordonnées n'est nécessaire.")
    upl=st.file_uploader("Photo du constat (JPG/JPEG/PNG)",type=["jpg","jpeg","png"],key="initial_upload")
    raw=upl.getvalue() if upl else None
    lat=lon=""
    if raw:
        st.image(raw,caption="Photo source",use_container_width=True)
        lat,lon=extract_gps_from_photo(raw)
        if lat and lon:
            st.success(f"GPS extrait de la photo : {lat}, {lon}")
            st.markdown(f"[Voir la position sur OpenStreetMap](https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=18/{lat}/{lon})")
        else:
            st.error("Aucune coordonnée GPS EXIF n'a été trouvée dans cette photo. Activez la localisation dans l'application photo, puis réimportez la photo originale.")
    with st.form("new_form"):
        a,b,c=st.columns(3)
        with a:
            commune=st.text_input("Commune *",value=commune0); contrat=st.text_input("Contrat *",value=contrat0)
            delegataire=st.text_input("Société délégataire *",value=delegataire0); bet=st.text_input("BET *",value=bet0)
        with b:
            secteur=st.text_input("Secteur / circuit"); prestation=st.selectbox("Prestation",["Collecte","Balayage manuel","Balayage mécanique","Lavage","Conteneurs","Autre"])
            anomaly=st.selectbox("Anomalie selon le CPS *",anomaly_options); clause=st.text_input("Article / clause CPS",value=clause0)
        with c:
            st.text_input("Date/heure système",value=now().strftime("%Y-%m-%d %H:%M:%S"),disabled=True)
            cfg_selected=next((x for x in cfg_list if str(x.get("anomalie",""))==str(anomaly)),{})
            deadline=st.number_input("Délai de traitement (heures)",1,720,int(cfg_selected.get("delai_heures",delay0) or delay0))
            penalty=float(cfg_selected.get("penalite_dh",0) or 0)
            st.caption(f"Pénalité estimée paramétrée : {penalty:,.2f} DH (à vérifier au regard du CPS).")
        obs=st.text_area("Description factuelle de l'anomalie *")
        sig1=st.text_input("Émargement BET — nom / qualité"); sig2=st.text_input("Émargement délégataire — nom / qualité"); sig3=st.text_input("Émargement cellule communale — nom / qualité")
        save=st.form_submit_button("Enregistrer le constat",type="primary",use_container_width=True)
    if save:
        if not raw: st.error("La photo est obligatoire.")
        elif not lat or not lon: st.error("Position GPS indisponible dans la photo : réimporte une photo originale avec géolocalisation EXIF.")
        elif not all(x.strip() for x in [commune,contrat,delegataire,bet,obs]): st.error("Complète les champs obligatoires.")
        else:
            t=now(); stem="constat_"+t.strftime("%Y%m%d_%H%M%S_%f"); im=stamped(raw,t.strftime("%Y-%m-%d %H:%M:%S %Z"),lat,lon,None)
            path=os.path.join(PROOFS,stem+"_gps.jpg"); im.save(path,"JPEG",quality=92)
            with open(os.path.join(PROOFS,stem+"_original.jpg"),"wb") as f:f.write(raw)
            c=db(); month=t.strftime("%Y%m"); seq=c.execute("SELECT COUNT(*) FROM constats WHERE date_constat LIKE ?",(t.strftime("%Y-%m")+"%",)).fetchone()[0]+1; ref=f"{month}-{seq:04d}"
            cur=c.execute("""INSERT INTO constats(created_at,commune,contrat,delegataire,bet,secteur,prestation,date_constat,heure_constat,latitude,longitude,anomaly,observation,clause_ref,deadline_hours,image_path,image_sha256,status,bet_signatory,delegate_signatory,commune_signatory,reference,penalty_amount)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(t.isoformat(),commune,contrat,delegataire,bet,secteur,prestation,t.strftime("%Y-%m-%d"),t.strftime("%H:%M:%S"),lat,lon,anomaly,obs,clause or str(cfg_selected.get("clause", "")),int(deadline),path,sha(raw),"Ouvert",sig1,sig2,sig3,ref,penalty))
            c.commit(); n=cur.lastrowid; r=c.execute("SELECT * FROM constats WHERE id=?",(n,)).fetchone(); c.close(); st.success(f"Constat {ref} enregistré.")
            st.download_button("Télécharger immédiatement la fiche constat (PDF)",make_pdf(r,logo_path),f"Fiche_constat_{ref}.pdf","application/pdf",key=f"newpdf_{n}")
with tabs[1]:
    st.subheader("Réponse du délégataire et validation du contrôleur"); rs=rows()
    if not rs: st.info("Crée d'abord un constat.")
    else:
        opts={f"{r['reference'] or r['id']} | {r['anomaly']} | {r['date_constat']} | {r['status']}":r for r in rs}
        key=st.selectbox("Choisir le constat",list(opts.keys())); r=opts[key]
        st.write("**Constat :** "+r["observation"])
        if r["image_path"] and os.path.exists(r["image_path"]): st.image(r["image_path"],width=480)
        with st.form(f"response_{r['id']}"):
            rc=st.camera_input("Photo après traitement",key=f"rc_{r['id']}"); ru=st.file_uploader("Ou importer photo après traitement",type=["jpg","jpeg","png"],key=f"ru_{r['id']}")
            chosen_r=rc if rc is not None else ru; note=st.text_area("Observations du délégataire",key=f"note_{r['id']}")
            submit_r=st.form_submit_button("Enregistrer la réponse")
        if submit_r:
            if not chosen_r: st.error("La photo après traitement est obligatoire.")
            else:
                rb=chosen_r.getvalue(); t=now(); p=os.path.join(PROOFS,f"reponse_{r['id']}_{t.strftime('%Y%m%d_%H%M%S_%f')}.jpg")
                with open(p,"wb") as f:f.write(rb)
                try: ok="Oui" if (t-datetime.fromisoformat(r["created_at"])).total_seconds()/3600<=int(r["deadline_hours"] or 24) else "Non"
                except: ok="À vérifier"
                c=db(); c.execute("UPDATE constats SET response_at=?,response_note=?,response_image_path=?,response_sha256=?,response_deadline_ok=?,status=? WHERE id=?",(t.isoformat(),note,p,sha(rb),ok,"Traitement déclaré — à valider",r["id"])); c.commit(); c.close()
                st.success(f"Réponse enregistrée. Délai indicatif respecté : {ok}.")
                updated={x["id"]:x for x in rows()}[r["id"]]
                st.download_button("Télécharger la fiche PDF mise à jour",make_pdf(updated,logo_path),f"Fiche_constat_{updated['reference'] or updated['id']}.pdf","application/pdf",key=f"responsepdf_{r['id']}")
        r={x["id"]:x for x in rows()}[r["id"]]
        if r["response_image_path"] and os.path.exists(r["response_image_path"]):
            st.image(r["response_image_path"],caption=f"Photo après traitement — {r['response_at']}",width=480); st.write(f"**Délai :** {r['response_deadline_ok']} | **Commentaire :** {r['response_note']}")
            with st.form(f"validation_{r['id']}"):
                decision=st.radio("Décision du contrôleur",["Traitement validé","Traitement rejeté — anomalie persistante"])
                vnote=st.text_area("Motif / observations"); vsubmit=st.form_submit_button("Enregistrer la décision",type="primary")
            if vsubmit:
                t=now(); status="Clôturé" if decision=="Traitement validé" else "Réponse rejetée — à reprendre"
                c=db(); c.execute("UPDATE constats SET validation_status=?,validation_note=?,validation_at=?,status=? WHERE id=?",(decision,vnote,t.isoformat(),status,r["id"])); c.commit(); c.close(); st.success("Décision enregistrée.")
                updated={x["id"]:x for x in rows()}[r["id"]]
                st.download_button("Télécharger la fiche PDF après validation",make_pdf(updated,logo_path),f"Fiche_constat_{updated['reference'] or updated['id']}.pdf","application/pdf",key=f"validationpdf_{r['id']}")
        else: st.warning("Aucune photo après traitement reçue.")
with tabs[2]:
    st.subheader("Historique et fiches PDF"); rs=rows()
    if rs:
        st.dataframe(pd.DataFrame([{"Référence":f"{r['reference'] or r['id']}","Date":r["date_constat"],"Commune":r["commune"],"Anomalie":r["anomaly"],"Statut":r["status"],"Réponse":r["response_at"] or "En attente","Délai":r["response_deadline_ok"] or "—"} for r in rs]),use_container_width=True,hide_index=True)
        for r in rs:
            with st.expander(f"{r['reference'] or r['id']} — {r['anomaly']} — {r['status']}"):
                st.write(f"Contrat : {r['contrat']} | Délégataire : {r['delegataire']} | BET : {r['bet']}")
                st.write(f"Date/heure : {r['date_constat']} {r['heure_constat']} | GPS : {r['latitude']}, {r['longitude']}")
                st.write("Observation : "+r["observation"])
                st.download_button("Télécharger la fiche PDF",make_pdf(r,logo_path),f"Fiche_constat_{r['reference'] or r['id']}.pdf","application/pdf",key=f"pdf_{r['id']}")
    else: st.info("Aucun constat enregistré.")
with tabs[3]:
    st.subheader("Synthèse mensuelle"); rs=rows()
    if not rs: st.info("Les graphiques apparaîtront après les premiers constats.")
    else:
        df=pd.DataFrame([dict(r) for r in rs]); df["mois"]=pd.to_datetime(df["date_constat"],errors="coerce").dt.to_period("M").astype(str)
        months=sorted(x for x in df["mois"].dropna().unique() if x!="NaT")
        if months:
            month=st.selectbox("Mois à analyser",months,index=len(months)-1); m=df[df["mois"]==month]
            responded=m["response_at"].fillna("").astype(str).str.len().gt(0).sum(); closed=(m["status"]=="Clôturé").sum(); late=(m["response_deadline_ok"]=="Non").sum()
            a,b,c,d=st.columns(4); a.metric("Constats",len(m)); b.metric("Réponses reçues",int(responded)); c.metric("Clôturés",int(closed)); d.metric("Hors délai",int(late))
            st.markdown("#### Constats par anomalie"); st.bar_chart(m["anomaly"].value_counts())
            st.markdown("#### État des constats"); st.bar_chart(m["status"].value_counts())
            st.markdown("#### Respect du délai"); st.bar_chart(m["response_deadline_ok"].replace("","Sans réponse").fillna("Sans réponse").value_counts())
            csv=m[["id","date_constat","heure_constat","commune","contrat","delegataire","secteur","prestation","anomaly","status","response_at","response_deadline_ok","validation_status"]].to_csv(index=False).encode("utf-8-sig")
            st.download_button("Exporter la synthèse CSV",csv,f"SmartClean_synthese_{month}.csv","text/csv")
