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
def _font(size, bold=False):
    candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arialbd.ttf" if bold else "arial.ttf"]
    for p in candidates:
        try: return ImageFont.truetype(p, size=max(10,int(size)))
        except Exception: pass
    return ImageFont.load_default()

def _map_tile(lat, lon, zoom=16, size=300):
    """Fetch a real OSM map crop. Return None rather than drawing a fake map."""
    try:
        import math
        lat=max(-85.0511,min(85.0511,float(lat))); lon=float(lon)
        n=2**zoom
        xf=(lon+180)/360*n
        latr=math.radians(lat); yf=(1-math.asinh(math.tan(latr))/math.pi)/2*n
        tx,ty=int(xf),int(yf); px,py=int((xf-tx)*256),int((yf-ty)*256)
        canvas=Image.new('RGB',(768,768),(235,235,235))
        for dx in range(-1,2):
            for dy in range(-1,2):
                x,y=tx+dx,ty+dy
                url=f'https://tile.openstreetmap.org/{zoom}/{x% n}/{max(0,min(n-1,y))}.png'
                r=requests.get(url,timeout=4,headers={'User-Agent':'SmartClean-Control/0.4 (field inspection)'})
                r.raise_for_status()
                tile=Image.open(io.BytesIO(r.content)).convert('RGB')
                canvas.paste(tile,((dx+1)*256,(dy+1)*256))
        crop=canvas.crop((256+px-size//2,256+py-size//2,256+px-size//2+size,256+py-size//2+size))
        d=ImageDraw.Draw(crop); cx=cy=size//2
        d.ellipse((cx-9,cy-9,cx+9,cy+9),fill=(230,45,45),outline='white',width=3)
        d.ellipse((cx-3,cy-3,cx+3,cy+3),fill='white')
        return crop
    except Exception:
        return None

def _location(lat, lon):
    try:
        r=requests.get('https://nominatim.openstreetmap.org/reverse',params={'format':'jsonv2','lat':lat,'lon':lon,'zoom':10},headers={'User-Agent':'SmartClean-Control/0.4 (field inspection)'},timeout=4)
        r.raise_for_status(); a=r.json().get('address',{})
        city=a.get('city') or a.get('town') or a.get('village') or a.get('municipality') or a.get('county') or 'Localisation GPS'
        region=a.get('state') or a.get('region') or ''
        country=a.get('country') or 'Maroc'
        return city,region,country
    except Exception: return 'Localisation GPS','','Maroc'

def stamped(raw, stamp, lat, lon):
    im=Image.open(io.BytesIO(raw)).convert('RGB')
    im.thumbnail((1800,1800),Image.Resampling.LANCZOS)
    w,h=im.size
    panel_h=max(205,int(w*.235))
    panel=Image.new('RGBA',(w,panel_h),(54,53,51,218))
    d=ImageDraw.Draw(panel)
    pad=max(12,int(w*.018)); map_size=min(panel_h-2*pad,int(w*.34))
    map_im=None
    if str(lat).strip() and str(lon).strip(): map_im=_map_tile(lat,lon,16,map_size)
    if map_im:
        panel.alpha_composite(map_im.convert('RGBA'),(pad,pad))
    else:
        # Clearly mark map unavailable; never imitate a real street map.
        d.rounded_rectangle((pad,pad,pad+map_size,pad+map_size),radius=8,fill=(95,95,95,255),outline=(220,220,220,255),width=2)
        d.text((pad+12,pad+map_size//2-10),'Carte indisponible',font=_font(max(12,w//95)),fill='white')
    x=pad+map_size+pad; avail=w-x-pad
    city,region,country=_location(lat,lon) if lat and lon else ('Localisation non vérifiée','','Maroc')
    big=_font(max(20,min(48,w//28)),True); med=_font(max(14,min(30,w//42))); small=_font(max(12,min(23,w//55)))
    d.text((x,pad),f'{city} · {region}'.strip(' ·'),font=big,fill='white',stroke_width=0)
    d.text((x,pad+int(panel_h*.30)),country,font=med,fill=(245,245,245,255))
    d.text((x,pad+int(panel_h*.51)),f'GPS : {lat}, {lon}' if lat and lon else 'GPS : non disponible',font=small,fill=(245,245,245,255))
    d.text((x,pad+int(panel_h*.70)),f'{stamp}',font=small,fill=(255,255,255,255))
    # small orange/red location marker at the far right when space permits
    rr=max(7,min(16,w//90)); cx=w-pad-rr; cy=pad+rr
    d.ellipse((cx-rr,cy-rr,cx+rr,cy+rr),fill=(238,65,45,255),outline='white',width=2)
    out=Image.new('RGB',(w,h+panel_h),(255,255,255)); out.paste(im,(0,0)); out.paste(panel.convert('RGB'),(0,h))
    return out

def make_pdf(r,logo_path):
    b=io.BytesIO(); doc=SimpleDocTemplate(b,pagesize=A4,rightMargin=1.3*cm,leftMargin=1.3*cm,topMargin=1.2*cm,bottomMargin=1.2*cm)
    s=getSampleStyleSheet(); s.add(ParagraphStyle(name="Small",parent=s["BodyText"],fontSize=8,leading=10)); story=[]
    if logo_path and os.path.exists(logo_path):
        try: story.append(PDFImage(logo_path,width=3*cm,height=1.4*cm,kind="proportional"))
        except: pass
    story += [Paragraph("SMARTCLEAN CONTROL",s["Title"]),Paragraph("FICHE DE CONSTAT D'ANOMALIE",s["Heading2"]),Paragraph(f"<b>Référence :</b> SCC-{r['id']:05d} &nbsp; <b>Statut :</b> {esc(r['status'])}",s["BodyText"]),Spacer(1,8)]
    data=[["Commune",r["commune"],"Contrat",r["contrat"]],["Délégataire",r["delegataire"],"BET",r["bet"]],["Secteur/circuit",r["secteur"],"Prestation",r["prestation"]],["Date",r["date_constat"],"Heure",r["heure_constat"]],["Anomalie",r["anomaly"],"Clause CPS",r["clause_ref"] or "À rattacher"],["Latitude",r["latitude"],"Longitude",r["longitude"]]]
    t=Table([[Paragraph(esc(str(x)),s["Small"]) for x in line] for line in data],colWidths=[2.4*cm,5.1*cm,2.4*cm,5.1*cm])
    t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.4,colors.grey),("BACKGROUND",(0,0),(0,-1),colors.whitesmoke),("BACKGROUND",(2,0),(2,-1),colors.whitesmoke),("VALIGN",(0,0),(-1,-1),"TOP"),("FONTSIZE",(0,0),(-1,-1),8)]))
    story += [t,Spacer(1,8),Paragraph("Description du constat",s["Heading3"]),Paragraph(esc(r["observation"]),s["BodyText"])]
    if r["image_path"] and os.path.exists(r["image_path"]):
        try: story += [Spacer(1,8),Paragraph("Photo initiale",s["Heading3"]),PDFImage(r["image_path"],width=14*cm,height=9.8*cm,kind="proportional")]
        except: pass
    story += [Spacer(1,8),Paragraph("Réponse du délégataire",s["Heading3"]),Paragraph(f"<b>Date/heure :</b> {esc(r['response_at'] or 'En attente')}",s["BodyText"]),Paragraph(f"<b>Observations :</b> {esc(r['response_note'] or 'En attente')}",s["BodyText"])]
    if r["response_image_path"] and os.path.exists(r["response_image_path"]):
        try: story += [Spacer(1,6),Paragraph("Photo après traitement",s["Heading3"]),PDFImage(r["response_image_path"],width=14*cm,height=9.8*cm,kind="proportional")]
        except: pass
    story += [Spacer(1,8),Paragraph("Validation du contrôleur",s["Heading3"]),Paragraph(f"<b>Décision :</b> {esc(r['validation_status'] or 'En attente')} | <b>Date/heure :</b> {esc(r['validation_at'] or 'En attente')}",s["BodyText"]),Paragraph(f"<b>Observations :</b> {esc(r['validation_note'] or '—')}",s["BodyText"]),Spacer(1,8),Paragraph("ÉMARGEMENTS",s["Heading3"])]
    sig=Table([["BET","Société délégataire","Cellule de contrôle de la commune"],[esc(r["bet_signatory"] or "Nom / qualité"),esc(r["delegate_signatory"] or "Nom / qualité"),esc(r["commune_signatory"] or "Nom / qualité")],["","",""]],colWidths=[5*cm,5*cm,5*cm],rowHeights=[.7*cm,1*cm,1.3*cm])
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
    logo=st.file_uploader("Logo du BET (PNG/JPG)",type=["png","jpg","jpeg"]); logo_path=None
    if logo:
        logo_path=os.path.join(CONFIG,"logo_bet"+os.path.splitext(logo.name)[1].lower())
        with open(logo_path,"wb") as f:f.write(logo.getvalue())
    else:
        ls=[os.path.join(CONFIG,x) for x in os.listdir(CONFIG) if x.startswith("logo_bet")]; logo_path=ls[0] if ls else None
    st.caption("Le calcul des pénalités est retiré de la fiche. Le logo enregistré dans configuration/logo_bet.png est réutilisé automatiquement.")
tabs=st.tabs(["Nouveau constat","Traitement / validation","Historique","Synthèse mensuelle","Liste des anomalies CPS"])
with tabs[4]:
    st.subheader("Configurer les anomalies selon le CPS"); af=os.path.join(CONFIG,"anomalies.json")
    if "anom_text" not in st.session_state:
        try:
            with open(af,encoding="utf-8") as f: st.session_state.anom_text="\n".join(json.load(f))
        except: st.session_state.anom_text="\n".join(DEFAULTS)
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
        st.markdown("#### Position GPS"); glat,glon="",""
        if streamlit_geolocation:
            try:
                g=streamlit_geolocation()
                if g and g.get("latitude") is not None:
                    glat=str(g["latitude"]); glon=str(g["longitude"]); st.success(f"GPS obtenu : {glat}, {glon}")
                    st.markdown(f"[Voir sur la carte](https://www.openstreetmap.org/?mlat={glat}&mlon={glon}#map=18/{glat}/{glon})")
                else: st.warning("Autorise la localisation dans le navigateur.")
            except: st.warning("GPS indisponible; saisis les coordonnées.")
        else: st.warning("Composant GPS indisponible; saisis les coordonnées.")
        lat=st.text_input("Latitude GPS *",value=glat,key="lat_new"); lon=st.text_input("Longitude GPS *",value=glon,key="lon_new")
        st.caption("Pour une photo importée, le GPS du téléphone est le lieu au moment de l’estampillage, sauf si vous saisissez des coordonnées fiables du lieu du constat.")
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
        sig1=st.text_input("Émargement BET — nom / qualité"); sig2=st.text_input("Émargement délégataire — nom / qualité"); sig3=st.text_input("Émargement cellule communale — nom / qualité")
        save=st.form_submit_button("Enregistrer le constat",type="primary",use_container_width=True)
    if save:
        if not raw: st.error("La photo est obligatoire.")
        elif not lat.strip() or not lon.strip(): st.error("La position GPS est obligatoire.")
        elif not all(x.strip() for x in [commune,contrat,delegataire,bet,obs]): st.error("Complète les champs obligatoires.")
        else:
            t=now(); im=stamped(raw,t.strftime("%Y-%m-%d %H:%M:%S %Z"),lat,lon); stem="constat_"+t.strftime("%Y%m%d_%H%M%S_%f")
            path=os.path.join(PROOFS,stem+"_gps.jpg"); im.save(path,"JPEG",quality=92)
            with open(os.path.join(PROOFS,stem+"_original.jpg"),"wb") as f:f.write(raw)
            c=db(); cur=c.execute("""INSERT INTO constats(created_at,commune,contrat,delegataire,bet,secteur,prestation,date_constat,heure_constat,latitude,longitude,anomaly,observation,clause_ref,deadline_hours,image_path,image_sha256,status,bet_signatory,delegate_signatory,commune_signatory)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(t.isoformat(),commune,contrat,delegataire,bet,secteur,prestation,t.strftime("%Y-%m-%d"),t.strftime("%H:%M:%S"),lat,lon,anomaly,obs,clause,int(deadline),path,sha(raw),"Ouvert",sig1,sig2,sig3))
            c.commit(); n=cur.lastrowid; c.close(); st.success(f"Constat SCC-{n:05d} enregistré.")
            st.info("La photo estampillée comprend une mini-carte réelle lorsque le service cartographique est accessible. Pour une photo importée, le GPS affiché correspond à la localisation obtenue au moment de l’estampillage, pas nécessairement au lieu de prise de vue.")
with tabs[1]:
    st.subheader("Réponse du délégataire et validation du contrôleur"); rs=rows()
    if not rs: st.info("Crée d'abord un constat.")
    else:
        opts={f"SCC-{r['id']:05d} | {r['anomaly']} | {r['date_constat']} | {r['status']}":r for r in rs}
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
                rb=chosen_r.getvalue(); t=now(); stem=f"reponse_{r['id']}_{t.strftime('%Y%m%d_%H%M%S_%f')}"; p=os.path.join(PROOFS,stem+"_estampillee.jpg")
                stamped_response=stamped(rb,t.strftime("%Y-%m-%d %H:%M:%S %Z"),r["latitude"],r["longitude"]); stamped_response.save(p,"JPEG",quality=93,optimize=True)
                with open(os.path.join(PROOFS,stem+"_original.jpg"),"wb") as f:f.write(rb)
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
        st.dataframe(pd.DataFrame([{"Référence":f"SCC-{r['id']:05d}","Date":r["date_constat"],"Commune":r["commune"],"Anomalie":r["anomaly"],"Statut":r["status"],"Réponse":r["response_at"] or "En attente","Délai":r["response_deadline_ok"] or "—"} for r in rs]),use_container_width=True,hide_index=True)
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
