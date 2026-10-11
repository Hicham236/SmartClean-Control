import os, io, sqlite3, hashlib, json
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st
import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as PDFImage, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm

try:
    from streamlit_geolocation import streamlit_geolocation
except Exception:
    streamlit_geolocation = None

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
    c.commit(); return c
def rows():
    c=db(); r=c.execute("SELECT * FROM constats ORDER BY id DESC").fetchall(); c.close(); return r
def sha(b): return hashlib.sha256(b).hexdigest() if b else ""
def esc(s): return str(s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n","<br/>")
def reverse_location(lat, lon):
    if not lat or not lon: return ""
    try:
        r=requests.get("https://nominatim.openstreetmap.org/reverse",params={"format":"jsonv2","lat":lat,"lon":lon},headers={"User-Agent":"SmartCleanControl/0.3 (prototype)"},timeout=3)
        if r.ok:
            a=r.json().get("address",{})
            return ", ".join(x for x in [a.get("suburb") or a.get("neighbourhood"),a.get("city") or a.get("town") or a.get("village"),a.get("state"),a.get("country")] if x)
    except Exception: pass
    return ""

def make_pdf(r,logo_path):
    b=io.BytesIO(); doc=SimpleDocTemplate(b,pagesize=A4,rightMargin=1.3*cm,leftMargin=1.3*cm,topMargin=1.2*cm,bottomMargin=1.2*cm)
    s=getSampleStyleSheet(); s.add(ParagraphStyle(name="Small",parent=s["BodyText"],fontSize=8,leading=10)); story=[]
    if logo_path and os.path.exists(logo_path):
        try: story.append(PDFImage(logo_path,width=6*cm,height=2.8*cm,kind="proportional"))
        except: pass
    story += [Paragraph("FICHE CONSTAT",s["Title"]),Paragraph(f"<b>Référence :</b> {datetime.fromisoformat(r['created_at']).strftime('%Y%m')}-{r['id']:04d} &nbsp; <b>Statut :</b> {esc(r['status'])}",s["BodyText"]),Spacer(1,8)]
    data=[["Commune",r["commune"],"Contrat",r["contrat"]],["Délégataire",r["delegataire"],"BET",r["bet"]],["Secteur/circuit",r["secteur"],"Prestation",r["prestation"]],["Date",r["date_constat"],"Heure",r["heure_constat"]],["Anomalie",r["anomaly"],"Clause CPS",r["clause_ref"] or "À rattacher"],["Latitude",r["latitude"],"Longitude",r["longitude"]]]
    t=Table([[Paragraph(esc(str(x)),s["Small"]) for x in line] for line in data],colWidths=[2.4*cm,5.1*cm,2.4*cm,5.1*cm])
    t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.4,colors.grey),("BACKGROUND",(0,0),(0,-1),colors.whitesmoke),("BACKGROUND",(2,0),(2,-1),colors.whitesmoke),("VALIGN",(0,0),(-1,-1),"TOP"),("FONTSIZE",(0,0),(-1,-1),8)]))
    story += [t,Spacer(1,8),Paragraph("Description du constat",s["Heading3"]),Paragraph(esc(r["observation"]),s["BodyText"])]
    if r["image_path"] and os.path.exists(r["image_path"]):
        try: story += [Spacer(1,8),Paragraph("Photo initiale",s["Heading3"]),PDFImage(r["image_path"],width=14*cm,height=9.8*cm,kind="proportional")]
        except: pass
    story += [Spacer(1,8),Paragraph("ÉMARGEMENTS",s["Heading3"])]
    sig=Table([["BET","Société délégataire","Représentant de la commune"],[esc("MAPERPRO"),esc(r["delegataire"]),esc("Service Technique")],["","",""]],colWidths=[5*cm,5*cm,5*cm],rowHeights=[.7*cm,1*cm,1.3*cm])
    sig.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.5,colors.grey),("BACKGROUND",(0,0),(-1,0),colors.whitesmoke),("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("FONTSIZE",(0,0),(-1,-1),7)]))
    story += [sig,Spacer(1,8)]
    doc.build(story); b.seek(0); return b.getvalue()

st.title("🧹 SmartClean Control")
st.caption("Constat d'anomalies • Réponse photo du délégataire • Validation du contrôleur • Synthèse mensuelle")
with st.sidebar:
    st.header("Contrat pilote")
    commune0=st.text_input("Commune","Sidi Harazem"); contrat0=st.text_input("Contrat / marché","")
    delegataire0=st.text_input("Société délégataire",""); bet0=st.text_input("BET chargé du suivi","MAPERPRO")
    clause0=st.text_input("Clause CPS par défaut",""); delay0=st.number_input("Délai attendu (heures)",1,720,24)
    logo_path=next((os.path.join(CONFIG,x) for x in ["logo_bet.png","logo_bet.jpg","logo_bet.jpeg"] if os.path.exists(os.path.join(CONFIG,x))),None)
    logo=st.file_uploader("Remplacer le logo du BET (facultatif)",type=["png","jpg","jpeg"]);
    if logo:
        logo_path=os.path.join(CONFIG,"logo_bet"+os.path.splitext(logo.name)[1].lower())
        with open(logo_path,"wb") as f:f.write(logo.getvalue())
        logo_path=os.path.join(CONFIG,"logo_bet"+os.path.splitext(logo.name)[1].lower())
    st.caption("Le calcul des pénalités est retiré de la fiche.")
tabs=st.tabs(["Nouveau constat","Traitement / validation","Historique","Synthèse mensuelle","Liste des anomalies CPS"])
with tabs[4]:
    st.subheader("Configurer les anomalies selon le CPS"); af=os.path.join(CONFIG,"anomalies.json")
    if "anom_text" not in st.session_state:
        try:
            with open(af,encoding="utf-8") as f: st.session_state.anom_text="\n".join(json.load(f))
        except: st.session_state.anom_text=""
    txt=st.text_area("Une anomalie par ligne",value=st.session_state.anom_text,height=250)
    if st.button("Enregistrer la liste CPS"):
        st.session_state.anom_text=txt
        with open(af,"w",encoding="utf-8") as f: json.dump([x.strip() for x in txt.splitlines() if x.strip()],f,ensure_ascii=False,indent=2)
        st.success("Liste enregistrée.")
    anomaly_options=[x.strip() for x in st.session_state.anom_text.splitlines() if x.strip()] or DEFAULTS
    st.info("Remplace les libellés par ceux du CPS réel avant l'utilisation officielle.")
with tabs[0]:
    st.subheader("Nouveau constat d'anomalie"); st.write("Sur téléphone, autorise l'accès à la caméra et à la localisation.")
    c1,c2=st.columns([1.2,1])
    with c1:
        cam=st.camera_input("Prendre une photo"); upl=st.file_uploader("Ou importer une photo",type=["jpg","jpeg","png"],key="initial_upload")
        chosen=cam if cam is not None else upl; raw=chosen.getvalue() if chosen else None
        if raw: st.image(raw,caption="Photo source",use_container_width=True)
    with c2:
        st.markdown("#### Position GPS"); glat,glon,accuracy="","",""
        if streamlit_geolocation:
            try:
                g=streamlit_geolocation()
                if g and g.get("latitude") is not None:
                    glat=str(g["latitude"]); glon=str(g["longitude"]); accuracy=str(g.get("accuracy", "")); st.success(f"GPS obtenu : {glat}, {glon}")
                    st.markdown(f"[Voir sur la carte](https://www.openstreetmap.org/?mlat={glat}&mlon={glon}#map=18/{glat}/{glon})")
                else: st.warning("Autorise la localisation dans le navigateur.")
            except: st.warning("GPS indisponible. La photo peut être enregistrée sans localisation vérifiée.")
        else: st.warning("Composant GPS indisponible. Tu peux poursuivre avec une photo importée; la localisation sera indiquée comme non vérifiée.")
        lat=glat; lon=glon
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
            deadline=st.number_input("Délai de traitement (heures)",1,720,int(delay0))
        obs=st.text_area("Description factuelle de l'anomalie *")
        sig1=st.text_input("Émargement BET",value="MAPERPRO",disabled=True); sig2=st.text_input("Émargement délégataire",value=delegataire,disabled=True); sig3=st.text_input("Émargement représentant de la commune",value="Service Technique",disabled=True)
        save=st.form_submit_button("Enregistrer le constat",type="primary",use_container_width=True)
    if save:
        if not raw: st.error("La photo est obligatoire.")
        elif not all(x.strip() for x in [commune,contrat,delegataire,bet,obs]): st.error("Complète les champs obligatoires.")
        else:
            t=now(); stem="constat_"+t.strftime("%Y%m%d_%H%M%S_%f")
            ext=os.path.splitext(chosen.name)[1].lower() if chosen is upl and chosen is not None else ".jpg"
            if ext not in [".jpg", ".jpeg", ".png"]: ext=".jpg"
            path=os.path.join(PROOFS,stem+"_original"+ext)
            with open(path,"wb") as f:f.write(raw)
            c=db(); cur=c.execute("""INSERT INTO constats(created_at,commune,contrat,delegataire,bet,secteur,prestation,date_constat,heure_constat,latitude,longitude,anomaly,observation,clause_ref,deadline_hours,image_path,image_sha256,status,bet_signatory,delegate_signatory,commune_signatory)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(t.isoformat(),commune,contrat,delegataire,bet,secteur,prestation,t.strftime("%Y-%m-%d"),t.strftime("%H:%M:%S"),lat,lon,anomaly,obs,clause,int(deadline),path,sha(raw),"Ouvert",sig1,sig2,sig3))
            c.commit(); n=cur.lastrowid; c.close(); st.success(f"Constat {t.strftime('%Y%m')}-{n:04d} enregistré.")
            st.download_button("Télécharger la fiche PDF",make_pdf({x["id"]:x for x in rows()}[n],logo_path),f"Fiche_constat_{t.strftime('%Y%m')}-{n:04d}.pdf","application/pdf",key=f"newpdf_{n}")
with tabs[1]:
    st.subheader("Réponse du délégataire et validation du contrôleur"); rs=rows()
    if not rs: st.info("Crée d'abord un constat.")
    else:
        opts={f"{datetime.fromisoformat(r['created_at']).strftime('%Y%m')}-{r['id']:04d} | {r['anomaly']} | {r['date_constat']} | {r['status']}":r for r in rs}
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
                rb=chosen_r.getvalue(); t=now(); rlat,rlon,racc="","",""
                if streamlit_geolocation:
                    try:
                        rg=streamlit_geolocation()
                        if rg and rg.get("latitude") is not None:
                            rlat=str(rg.get("latitude")); rlon=str(rg.get("longitude")); racc=str(rg.get("accuracy", ""))
                    except Exception: pass
                stem=f"reponse_{r['id']}_{t.strftime('%Y%m%d_%H%M%S_%f')}"
                ext=os.path.splitext(chosen_r.name)[1].lower() if chosen_r is ru and chosen_r is not None else ".jpg"
                if ext not in [".jpg", ".jpeg", ".png"]: ext=".jpg"
                p=os.path.join(PROOFS,stem+"_original"+ext)
                with open(p,"wb") as f:f.write(rb)
                try: ok="Oui" if (t-datetime.fromisoformat(r["created_at"])).total_seconds()/3600<=int(r["deadline_hours"] or 24) else "Non"
                except: ok="À vérifier"
                c=db(); c.execute("UPDATE constats SET response_at=?,response_note=?,response_image_path=?,response_sha256=?,response_deadline_ok=?,status=? WHERE id=?",(t.isoformat(),note,p,sha(rb),ok,"Traitement déclaré — à valider",r["id"])); c.commit(); c.close()
                st.success(f"Réponse enregistrée. Délai indicatif respecté : {ok}.")
        r={x["id"]:x for x in rows()}[r["id"]]
        if r["response_image_path"] and os.path.exists(r["response_image_path"]):
            st.image(r["response_image_path"],caption=f"Photo après traitement — {r['response_at']}",width=480); st.write(f"**Délai :** {r['response_deadline_ok']} | **Commentaire :** {r['response_note']}")
            with st.form(f"validation_{r['id']}"):
                decision=st.radio("Décision du contrôleur",["Traitement validé","Traitement rejeté — anomalie persistante"])
                vnote=st.text_area("Motif / observations"); vsubmit=st.form_submit_button("Enregistrer la décision",type="primary")
            if vsubmit:
                t=now(); status="Clôturé" if decision=="Traitement validé" else "Réponse rejetée — à reprendre"
                c=db(); c.execute("UPDATE constats SET validation_status=?,validation_note=?,validation_at=?,status=? WHERE id=?",(decision,vnote,t.isoformat(),status,r["id"])); c.commit(); c.close(); st.success("Décision enregistrée.")
        else: st.warning("Aucune photo après traitement reçue.")
with tabs[2]:
    st.subheader("Historique et fiches PDF"); rs=rows()
    if rs:
        st.dataframe(pd.DataFrame([{"Référence":f"{datetime.fromisoformat(r['created_at']).strftime('%Y%m')}-{r['id']:04d}","Date":r["date_constat"],"Commune":r["commune"],"Anomalie":r["anomaly"],"Statut":r["status"],"Réponse":r["response_at"] or "En attente","Délai":r["response_deadline_ok"] or "—"} for r in rs]),use_container_width=True,hide_index=True)
        for r in rs:
            with st.expander(f"SCC-{r['id']:05d} — {r['anomaly']} — {r['status']}"):
                st.write(f"Contrat : {r['contrat']} | Délégataire : {r['delegataire']} | BET : {r['bet']}")
                st.write(f"Date/heure : {r['date_constat']} {r['heure_constat']} | GPS : {r['latitude']}, {r['longitude']}")
                st.write("Observation : "+r["observation"])
                st.download_button("Télécharger la fiche PDF",make_pdf(r,logo_path),f"SmartClean_SCC-{r['id']:05d}.pdf","application/pdf",key=f"pdf_{r['id']}")
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
