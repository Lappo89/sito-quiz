import base64
import hashlib
import time
from datetime import datetime
import fitz  # PyMuPDF per testo rapido e ritaglio intelligente dai PDF
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel
from supabase import create_client

# Impostazione ottimizzata per smartphone
st.set_page_config(page_title="I Miei Quiz", page_icon="🧠", layout="centered")

# Connessione ai servizi tramite i "Secrets" di Streamlit
client_gemini = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def cifra_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def estrai_testo_pdf(pdf_bytes: bytes) -> str:
    """Estrae velocemente il testo da un PDF per generazioni rapide senza immagini."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        testo = "\n".join(page.get_text("text") for page in doc)
        doc.close()
        return testo.strip()
    except Exception:
        return ""

def estrai_ritaglio_pdf(pdf_bytes: bytes, pagina: int | None, box: list[int] | None) -> str | None:
    """Ritaglia solo il riquadro [ymin, xmin, ymax, xmax] (scala 0-1000) dalla pagina del PDF."""
    if not pdf_bytes or not pagina or not box or len(box) != 4:
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if pagina < 1 or pagina > len(doc):
            doc.close()
            return None

        page = doc[pagina - 1]
        w, h = page.rect.width, page.rect.height
        ymin, xmin, ymax, xmax = box

        x0 = max(0.0, min(w, (xmin / 1000.0) * w))
        y0 = max(0.0, min(h, (ymin / 1000.0) * h))
        x1 = max(0.0, min(w, (xmax / 1000.0) * w))
        y1 = max(0.0, min(h, (ymax / 1000.0) * h))

        if (x1 - x0) < 25 or (y1 - y0) < 25:
            doc.close()
            return None

        clip = fitz.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(clip=clip, dpi=150)
        img_bytes = pix.tobytes("jpeg", jpg_quality=80)
        doc.close()
        return base64.b64encode(img_bytes).decode("utf-8")
    except Exception:
        return None

def mostra_testo_e_immagine(indice: int, domanda: dict):
    """Mostra il testo della domanda e, se presente, l'immagine ritagliata di fianco."""
    img_b64 = domanda.get("immagine_b64")
    if img_b64:
        col_txt, col_img = st.columns([3, 2])
        with col_txt:
            st.markdown(f"**{indice}. {domanda['testo']}**")
        with col_img:
            st.image(base64.b64decode(img_b64), use_container_width=True)
    else:
        st.markdown(f"**{indice}. {domanda['testo']}**")

# ==========================================
# CONTROLLO LOGIN (ADMIN E UTENTI)
# ==========================================
if "autenticato" not in st.session_state:
    st.session_state["autenticato"] = False
if "ruolo" not in st.session_state:
    st.session_state["ruolo"] = None
if "utente_loggato" not in st.session_state:
    st.session_state["utente_loggato"] = None
if "profilo_attivo" not in st.session_state:
    st.session_state["profilo_attivo"] = None

if not st.session_state["autenticato"]:
    st.title("🔒 Accesso Riservato")
    username_inserito = st.text_input("Username (es. admin):").strip().lower()
    password_inserita = st.text_input("Password:", type="password")

    if st.button("Accedi", type="primary", use_container_width=True):
        if not username_inserito or not password_inserita:
            st.warning("Inserisci sia lo username che la password.")
        elif username_inserito == "admin" and password_inserita == st.secrets["APP_PASSWORD"]:
            st.session_state["autenticato"] = True
            st.session_state["ruolo"] = "admin"
            st.session_state["utente_loggato"] = "admin"
            st.session_state["profilo_attivo"] = "admin"
            st.rerun()
        else:
            res = supabase.table("utenti").select("*").eq("username", username_inserito).execute()
            if res.data and res.data[0]["password_hash"] == cifra_password(password_inserita):
                st.session_state["autenticato"] = True
                st.session_state["ruolo"] = "utente"
                st.session_state["utente_loggato"] = username_inserito
                st.session_state["profilo_attivo"] = username_inserito
                st.rerun()
            else:
                st.error("Username o password errati. Riprova.")
    st.stop()

if "materia_selezionata" not in st.session_state:
    st.session_state["materia_selezionata"] = None

# Schemi Pydantic: uno veloce (solo testo) e uno con coordinate immagini
class DomandaSemplice(BaseModel):
    testo: str
    opzioni: list[str]
    risposta_corretta: str
    spiegazione: str

class QuizSemplice(BaseModel):
    domande: list[DomandaSemplice]

class DomandaConImmagine(BaseModel):
    testo: str
    opzioni: list[str]
    risposta_corretta: str
    spiegazione: str
    pagina_immagine: int | None = None
    box_immagine: list[int] | None = None

class QuizConImmagini(BaseModel):
    domande: list[DomandaConImmagine]

# Frammento live per il timer della modalità Esame
@st.fragment(run_every=1)
def mostra_timer_esame(quiz_id, fine_timestamp):
    rimanenti = int(fine_timestamp - time.time())
    if rimanenti <= 0:
        st.session_state[f"esame_consegnato_{quiz_id}"] = True
        st.session_state[f"esame_scaduto_{quiz_id}"] = True
        st.rerun()
    else:
        minuti, secondi = divmod(rimanenti, 60)
        if rimanenti <= 60:
            st.error(f"⏱️ Tempo rimanente: **{minuti:02d}:{secondi:02d}**")
        else:
            st.info(f"⏱️ Tempo rimanente: **{minuti:02d}:{secondi:02d}**")

# Calcolo media punteggi
def calcola_media_percentuale(lista_quiz):
    percentuali = []
    for q in lista_quiz:
        p = q.get("punteggio")
        if p and "/" in str(p):
            try:
                num, den = str(p).split("/")
                num, den = float(num.strip()), float(den.strip())
                if den > 0:
                    percentuali.append((num / den) * 100)
            except ValueError:
                pass
    if not percentuali:
        return None
    return sum(percentuali) / len(percentuali)

# ==========================================
# INTESTAZIONE E CAMBIO PROFILO (SOLO ADMIN)
# ==========================================
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.title("🧠 I Miei Quiz")
with col_h2:
    st.write("")
    if st.button("🚪 Esci", use_container_width=True):
        st.session_state.clear()
        st.rerun()

tutti_gli_utenti = []
if st.session_state["ruolo"] == "admin":
    res_utenti = supabase.table("utenti").select("username").order("username").execute()
    tutti_gli_utenti = [u["username"] for u in (res_utenti.data or [])]
    lista_profili = ["admin"] + tutti_gli_utenti

    if st.session_state["profilo_attivo"] not in lista_profili:
        st.session_state["profilo_attivo"] = "admin"

    profilo_scelto = st.selectbox(
        "👑 Profilo attivo (solo Admin):",
        lista_profili,
        index=lista_profili.index(st.session_state["profilo_attivo"]),
        key="selettore_profilo_admin"
    )
    if profilo_scelto != st.session_state["profilo_attivo"]:
        st.session_state["profilo_attivo"] = profilo_scelto
        st.session_state["materia_selezionata"] = None
        st.rerun()
else:
    st.caption(f"👤 Profilo connesso: **{st.session_state['utente_loggato']}**")

profilo_attivo = st.session_state["profilo_attivo"]

# Scarica SOLO i quiz del profilo attivo
risposta_db = (
    supabase.table("quiz")
    .select("*")
    .eq("proprietario", profilo_attivo)
    .order("created_at", desc=True)
    .execute()
)
tutti_i_quiz = risposta_db.data or []

# Schede principali
if st.session_state["ruolo"] == "admin":
    tab_materie, tab_crea, tab_utenti = st.tabs(["📚 Le Mie Materie", "➕ Crea Nuovo", "👑 Gestione Utenti"])
else:
    tab_materie, tab_crea = st.tabs(["📚 Le Mie Materie", "➕ Crea Nuovo"])
    tab_utenti = None

# ==========================================
# SCHEDA 1: LE MIE MATERIE
# ==========================================
with tab_materie:
    materie = sorted(list(set(q["materia"] for q in tutti_i_quiz)))

    if st.session_state["materia_selezionata"] not in materie:
        st.session_state["materia_selezionata"] = None

    # VISTA 1: GRIGLIA DI QUADROTTI
    if st.session_state["materia_selezionata"] is None:
        if materie:
            if st.button("🔄 Aggiorna"):
                st.rerun()

            st.write("")
            for i in range(0, len(materie), 2):
                cols = st.columns(2)
                for j, col in enumerate(cols):
                    if i + j < len(materie):
                        mat = materie[i + j]
                        n_da_fare = sum(1 for q in tutti_i_quiz if q["materia"] == mat and not q.get("completato"))
                        n_comp = sum(1 for q in tutti_i_quiz if q["materia"] == mat and q.get("completato"))
                        with col:
                            if st.button(
                                f"📚 **{mat}**\n\n{n_da_fare} da fare • {n_comp} completati",
                                key=f"card_{profilo_attivo}_{mat}",
                                use_container_width=True
                            ):
                                st.session_state["materia_selezionata"] = mat
                                st.rerun()

    # VISTA 2: DENTRO LA MATERIA SELEZIONATA
    else:
        materia_scelta = st.session_state["materia_selezionata"]

        col_top1, col_top2 = st.columns([2, 1])
        with col_top1:
            if st.button("⬅️ Torna alle materie"):
                st.session_state["materia_selezionata"] = None
                st.rerun()
        with col_top2:
            if st.button("🔄 Aggiorna", use_container_width=True):
                st.rerun()

        st.subheader(f"📚 {materia_scelta}")

        with st.expander("🗑️ Gestisci materia"):
            st.caption(f"Rimuove definitivamente '{materia_scelta}' e tutti i suoi quiz da questo profilo.")
            if st.button("Elimina intera materia", type="primary", key=f"del_mat_{profilo_attivo}_{materia_scelta}"):
                supabase.table("quiz").delete().eq("materia", materia_scelta).eq("proprietario", profilo_attivo).execute()
                st.session_state["materia_selezionata"] = None
                st.toast(f"Materia '{materia_scelta}' eliminata!")
                st.rerun()

        quiz_mat_da_fare = [q for q in tutti_i_quiz if q["materia"] == materia_scelta and not q.get("completato")]
        quiz_mat_completati = [q for q in tutti_i_quiz if q["materia"] == materia_scelta and q.get("completato")]

        sub_da_fare, sub_completati = st.tabs([
            f"📝 Da Fare ({len(quiz_mat_da_fare)})",
            f"✅ Completati ({len(quiz_mat_completati)})"
        ])

        # SOTTO-SCHEDA 1: DA FARE
        with sub_da_fare:
            if not quiz_mat_da_fare:
                st.info("Hai completato tutti i quiz di questa materia! Puoi rivederli in '✅ Completati' o crearne di nuovi.")
            else:
                titoli = {f"{q['titolo']} (#{q['id']})": q for q in quiz_mat_da_fare}
                titolo_scelto = st.selectbox("Scegli il quiz:", list(titoli.keys()), key=f"tit_da_fare_{profilo_attivo}_{materia_scelta}")

                quiz_attivo = titoli[titolo_scelto]
                quiz_id = quiz_attivo["id"]
                domande = quiz_attivo["domande"]

                with st.expander("🗑️ Elimina questo quiz"):
                    st.caption(f"Rimuove definitivamente '{quiz_attivo['titolo']}'.")
                    if st.button("Conferma eliminazione", type="secondary", key=f"del_quiz_{quiz_id}"):
                        supabase.table("quiz").delete().eq("id", quiz_id).execute()
                        st.toast("Quiz eliminato!")
                        st.rerun()

                modalita = st.radio(
                    "Modalità di svolgimento:",
                    ["🏋️ Allenamento", "🎓 Esame con Timer"],
                    horizontal=True,
                    key=f"mod_{quiz_id}"
                )

                st.divider()

                # MODALITÀ ALLENAMENTO
                if modalita == "🏋️ Allenamento":
                    risposte_date_pre = sum(
                        1 for i in range(len(domande))
                        if st.session_state.get(f"quiz_{quiz_id}_q_{i}") is not None
                    )
                    st.progress(
                        risposte_date_pre / len(domande),
                        text=f"Avanzamento: {risposte_date_pre} / {len(domande)} domande"
                    )

                    punteggio = 0
                    risposte_date = 0

                    for i, d in enumerate(domande):
                        mostra_testo_e_immagine(i + 1, d)
                        scelta = st.radio(
                            "Seleziona una risposta:",
                            d["opzioni"],
                            key=f"quiz_{quiz_id}_q_{i}",
                            index=None,
                            label_visibility="collapsed"
                        )
                        if scelta:
                            risposte_date += 1
                            if scelta == d["risposta_corretta"]:
                                st.success(f"Esatto! {d['spiegazione']}")
                                punteggio += 1
                            else:
                                st.error(f"Sbagliato. Risposta corretta: **{d['risposta_corretta']}**\n\n{d['spiegazione']}")
                        st.write("")

                    if risposte_date == len(domande):
                        st.balloons()
                        st.info(f"🎯 Quiz completato! Punteggio finale: **{punteggio} / {len(domande)}**")

                        if st.button("✅ Salva e archivia in Completati", type="primary", use_container_width=True, key=f"archivia_{quiz_id}"):
                            data_oggi = datetime.now().strftime("%d/%m/%Y %H:%M")
                            supabase.table("quiz").update({
                                "completato": True,
                                "punteggio": f"{punteggio} / {len(domande)}",
                                "data_completamento": data_oggi
                            }).eq("id", quiz_id).execute()
                            st.toast("Quiz archiviato in Completati!")
                            st.rerun()

                # MODALITÀ ESAME CON TIMER
                else:
                    chiave_avviato = f"esame_avviato_{quiz_id}"
                    chiave_fine = f"esame_fine_{quiz_id}"
                    chiave_consegnato = f"esame_consegnato_{quiz_id}"
                    chiave_scaduto = f"esame_scaduto_{quiz_id}"

                    if chiave_avviato not in st.session_state:
                        st.session_state[chiave_avviato] = False
                    if chiave_consegnato not in st.session_state:
                        st.session_state[chiave_consegnato] = False
                    if chiave_scaduto not in st.session_state:
                        st.session_state[chiave_scaduto] = False

                    if not st.session_state[chiave_avviato]:
                        st.info("In modalità **Esame** non vedrai le correzioni fino alla consegna. Se il tempo scade, il quiz verrà consegnato automaticamente.")
                        durata_minuti = st.number_input(
                            "Durata dell'esame (in minuti):",
                            min_value=1,
                            max_value=180,
                            value=max(2, len(domande)),
                            step=1,
                            key=f"durata_{quiz_id}"
                        )
                        if st.button("▶️ Avvia Esame", type="primary", use_container_width=True, key=f"start_{quiz_id}"):
                            st.session_state[chiave_avviato] = True
                            st.session_state[chiave_consegnato] = False
                            st.session_state[chiave_scaduto] = False
                            st.session_state[chiave_fine] = time.time() + (durata_minuti * 60)
                            st.rerun()

                    elif not st.session_state[chiave_consegnato]:
                        mostra_timer_esame(quiz_id, st.session_state[chiave_fine])

                        risposte_date_pre = sum(
                            1 for i in range(len(domande))
                            if st.session_state.get(f"esame_{quiz_id}_q_{i}") is not None
                        )
                        st.progress(
                            risposte_date_pre / len(domande),
                            text=f"Avanzamento: {risposte_date_pre} / {len(domande)} domande risposte"
                        )

                        for i, d in enumerate(domande):
                            mostra_testo_e_immagine(i + 1, d)
                            st.radio(
                                "Seleziona una risposta:",
                                d["opzioni"],
                                key=f"esame_{quiz_id}_q_{i}",
                                index=None,
                                label_visibility="collapsed"
                            )
                            st.write("")

                        if st.button("📤 Consegna Esame", type="primary", use_container_width=True, key=f"submit_{quiz_id}"):
                            st.session_state[chiave_consegnato] = True
                            st.rerun()

                    else:
                        if st.session_state[chiave_scaduto]:
                            st.warning("⏰ **Tempo scaduto!** L'esame è stato consegnato automaticamente.")
                        else:
                            st.success("📤 **Esame consegnato!** Ecco il risultato:")

                        punteggio = 0
                        for i, d in enumerate(domande):
                            scelta = st.session_state.get(f"esame_{quiz_id}_q_{i}")
                            if scelta == d["risposta_corretta"]:
                                punteggio += 1

                        perc = int((punteggio / len(domande)) * 100)
                        st.info(f"🎯 Punteggio finale: **{punteggio} / {len(domande)}** ({perc}%)")

                        col_e1, col_e2 = st.columns(2)
                        with col_e1:
                            if st.button("✅ Archivia in Completati", type="primary", use_container_width=True, key=f"arch_esame_{quiz_id}"):
                                data_oggi = datetime.now().strftime("%d/%m/%Y %H:%M")
                                supabase.table("quiz").update({
                                    "completato": True,
                                    "punteggio": f"{punteggio} / {len(domande)}",
                                    "data_completamento": data_oggi
                                }).eq("id", quiz_id).execute()
                                st.session_state[chiave_avviato] = False
                                st.session_state[chiave_consegnato] = False
                                st.session_state[chiave_scaduto] = False
                                st.toast("Esame archiviato in Completati!")
                                st.rerun()
                        with col_e2:
                            if st.button("🔄 Ripeti Esame", use_container_width=True, key=f"ripeti_esame_{quiz_id}"):
                                st.session_state[chiave_avviato] = False
                                st.session_state[chiave_consegnato] = False
                                st.session_state[chiave_scaduto] = False
                                for i in range(len(domande)):
                                    st.session_state.pop(f"esame_{quiz_id}_q_{i}", None)
                                st.rerun()

                        st.divider()
                        st.subheader("Correzione dettagliata")
                        for i, d in enumerate(domande):
                            scelta = st.session_state.get(f"esame_{quiz_id}_q_{i}")
                            mostra_testo_e_immagine(i + 1, d)
                            if scelta is None:
                                st.warning(f"Nessuna risposta data. Risposta corretta: **{d['risposta_corretta']}**\n\n{d['spiegazione']}")
                            elif scelta == d["risposta_corretta"]:
                                st.success(f"Esatto! ({scelta}) — {d['spiegazione']}")
                            else:
                                st.error(f"Hai risposto: *{scelta}*. Risposta corretta: **{d['risposta_corretta']}**\n\n{d['spiegazione']}")

        # SOTTO-SCHEDA 2: COMPLETATI
        with sub_completati:
            if not quiz_mat_completati:
                st.info("Non hai ancora completato nessun quiz di questa materia.")
            else:
                media_perc = calcola_media_percentuale(quiz_mat_completati)
                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    st.metric("Quiz completati", len(quiz_mat_completati))
                with col_s2:
                    st.metric("Media punteggi", f"{media_perc:.1f}%" if media_perc is not None else "N/D")

                st.divider()

                for q in quiz_mat_completati:
                    punteggio_str = q.get("punteggio") or "N/D"
                    data_str = q.get("data_completamento") or "N/D"
                    etichetta = f"✅ {q['titolo']} — Punteggio: {punteggio_str} | Data: {data_str}"

                    with st.expander(etichetta):
                        col_a, col_b = st.columns(2)
                        with col_a:
                            if st.button("🔄 Rifai questo quiz", key=f"rifai_{q['id']}", use_container_width=True):
                                supabase.table("quiz").update({
                                    "completato": False,
                                    "punteggio": None,
                                    "data_completamento": None
                                }).eq("id", q["id"]).execute()
                                st.toast("Quiz spostato di nuovo in 'Da Fare'!")
                                st.rerun()
                        with col_b:
                            if st.button("🗑️ Elimina definitivamente", key=f"del_comp_{q['id']}", use_container_width=True):
                                supabase.table("quiz").delete().eq("id", q["id"]).execute()
                                st.toast("Quiz eliminato!")
                                st.rerun()

                        st.divider()
                        st.caption("Riepilogo domande e risposte corrette:")
                        for idx, d in enumerate(q["domande"]):
                            mostra_testo_e_immagine(idx + 1, d)
                            st.markdown(f"👉 *{d['risposta_corretta']}* — {d['spiegazione']}")

# ==========================================
# SCHEDA 2: CREA E SALVA UN NUOVO QUIZ
# ==========================================
with tab_crea:
    st.subheader(f"Genera un quiz con Gemini (profilo: {profilo_attivo})")

    materie_esistenti = sorted(list(set(q["materia"] for q in tutti_i_quiz)))
    if materie_esistenti:
        scelta_mat = st.selectbox(
            "Materia:",
            materie_esistenti + ["➕ Nuova materia..."],
            key=f"smart_mat_select_{profilo_attivo}"
        )
        if scelta_mat == "➕ Nuova materia...":
            materia = st.text_input("Nome della nuova materia (es. Diritto Privato, Biologia, Storia)")
        else:
            materia = scelta_mat
    else:
        materia = st.text_input("Materia (es. Diritto Privato, Biologia, Storia)")

    titolo = st.text_input("Titolo del quiz (es. Capitolo 3 - I Contratti)")
    file_appunti = st.file_uploader("Carica i tuoi appunti (PDF o TXT)", type=["pdf", "txt"])
    includi_immagini = st.checkbox("🖼️ Estrai figure/grafici dal PDF (più lento)", value=False)
    istruzioni_extra = st.text_area(
        "Istruzioni personalizzate per Gemini (opzionale)",
        placeholder="Es: Concentrati solo sul capitolo 2, fai domande difficili con casi pratici, insisti sulle formule..."
    )
    esempi_quiz = st.text_area("Incolla 1 o 2 esempi di domande per far capire lo stile a Gemini (opzionale)")
    num_domande = st.slider("Numero di domande", min_value=3, max_value=20, value=5)

    if st.button("Genera e Salva nel Sito", type="primary", use_container_width=True):
        if not materia or not titolo or not file_appunti:
            st.warning("Compila materia, titolo e carica almeno un file di appunti.")
        else:
            msg_spinner = (
                "Gemini sta analizzando le pagine e ritagliando le figure..."
                if includi_immagini
                else "Gemini sta leggendo gli appunti e preparando le domande..."
            )
            with st.spinner(msg_spinner):
                try:
                    e_pdf = file_appunti.type == "application/pdf"
                    pdf_bytes = file_appunti.getvalue() if e_pdf else None

                    prompt = f"""
                    Crea un quiz di {num_domande} domande a risposta multipla (4 opzioni ciascuna) basato SOLO sul documento allegato.
                    La risposta corretta deve corrispondere esattamente a una delle 4 opzioni.
                    Aggiungi una spiegazione chiara e sintetica per ogni domanda.
                    """

                    usa_immagini = includi_immagini and e_pdf
                    if usa_immagini:
                        prompt += """
                        IMMAGINI E FIGURE:
                        - Se una domanda si riferisce a una figura, un grafico, un circuito o uno schema presente nel PDF, compila `pagina_immagine` (numero di pagina a partire da 1) e `box_immagine` come [ymin, xmin, ymax, xmax] su scala 0-1000.
                        - Il riquadro `box_immagine` deve contenere SOLO la figura/grafico/schema, escludendo il testo circostante della slide che potrebbe rivelare la risposta.
                        - Se la domanda non necessita di alcuna immagine, lascia `pagina_immagine` e `box_immagine` a null.
                        """

                    if istruzioni_extra:
                        prompt += f"\nSegui attentamente queste istruzioni specifiche dell'utente:\n{istruzioni_extra}"
                    if esempi_quiz:
                        prompt += f"\nSegui rigorosamente lo stile, il tono e la difficoltà di questi esempi:\n{esempi_quiz}"

                    # Percorso veloce (solo testo) vs percorso visivo (PDF completo)
                    if not usa_immagini:
                        testo_documento = (
                            estrai_testo_pdf(pdf_bytes)
                            if e_pdf
                            else file_appunti.getvalue().decode("utf-8", errors="ignore").strip()
                        )
                        if len(testo_documento) > 100:
                            contenuto_gemini = [f"DOCUMENTO:\n{testo_documento}\n\nISTRUZIONI:\n{prompt}"]
                        else:
                            # Fallback per PDF scansionati senza testo selezionabile
                            uploaded_file = client_gemini.files.upload(
                                file=file_appunti,
                                config={"mime_type": file_appunti.type}
                            )
                            contenuto_gemini = [uploaded_file, prompt]
                        schema_risposta = QuizSemplice
                    else:
                        uploaded_file = client_gemini.files.upload(
                            file=file_appunti,
                            config={"mime_type": file_appunti.type}
                        )
                        contenuto_gemini = [uploaded_file, prompt]
                        schema_risposta = QuizConImmagini

                    modelli_disponibili = [
                        "gemini-4-hydrogen",
                        "gemini-3.5-flash-lite",
                        "gemini-3.6-flash",
                        "gemini-3.8-flash",
                    ]
                    response = None
                    ultimo_errore = None

                    for nome_modello in modelli_disponibili:
                        try:
                            response = client_gemini.models.generate_content(
                                model=nome_modello,
                                contents=contenuto_gemini,
                                config=types.GenerateContentConfig(
                                    response_mime_type="application/json",
                                    response_schema=schema_risposta,
                                ),
                            )
                            break
                        except Exception as err:
                            ultimo_errore = err
                            if "503" in str(err) or "429" in str(err):
                                st.toast(f"{nome_modello} occupato, provo il prossimo modello...")
                                time.sleep(2)
                            else:
                                raise err

                    if response is None:
                        raise ultimo_errore

                    quiz_pronto = schema_risposta.model_validate_json(response.text)

                    domande_salvate = []
                    immagini_trovate = 0
                    for d in quiz_pronto.domande:
                        d_dict = {
                            "testo": d.testo,
                            "opzioni": d.opzioni,
                            "risposta_corretta": d.risposta_corretta,
                            "spiegazione": d.spiegazione,
                        }
                        if usa_immagini and d.pagina_immagine and d.box_immagine:
                            img_b64 = estrai_ritaglio_pdf(pdf_bytes, d.pagina_immagine, d.box_immagine)
                            if img_b64:
                                d_dict["immagine_b64"] = img_b64
                                immagini_trovate += 1
                        domande_salvate.append(d_dict)

                    dati_da_salvare = {
                        "materia": materia.strip(),
                        "titolo": titolo.strip(),
                        "domande": domande_salvate,
                        "completato": False,
                        "proprietario": profilo_attivo
                    }
                    supabase.table("quiz").insert(dati_da_salvare).execute()

                    msg_img = f" (con {immagini_trovate} figure ritagliate)" if immagini_trovate > 0 else ""
                    st.success(f"Quiz '{titolo}' salvato in '{materia}'{msg_img}! Aprila da '📚 Le Mie Materie' per provarlo.")
                except Exception as e:
                    st.error(f"Si è verificato un errore: {e}")

# ==========================================
# SCHEDA 3: GESTIONE UTENTI (SOLO ADMIN)
# ==========================================
if tab_utenti is not None:
    with tab_utenti:
        st.subheader("➕ Crea un nuovo profilo")
        nuovo_user = st.text_input("Username del nuovo utente (es. marco):", key="new_u_name").strip().lower()
        nuova_pass = st.text_input("Password per questo utente:", type="password", key="new_u_pass")

        if st.button("Crea Utente", type="primary", use_container_width=True):
            if not nuovo_user or not nuova_pass:
                st.warning("Inserisci sia lo username che la password.")
            elif nuovo_user == "admin":
                st.error("Lo username 'admin' è riservato.")
            elif nuovo_user in tutti_gli_utenti:
                st.error(f"Esiste già un utente chiamato '{nuovo_user}'.")
            else:
                supabase.table("utenti").insert({
                    "username": nuovo_user,
                    "password_hash": cifra_password(nuova_pass)
                }).execute()
                st.success(f"Utente '{nuovo_user}' creato! Ora può accedere con la sua password.")
                st.rerun()

        st.divider()
        st.subheader("👥 Utenti esistenti")
        if not tutti_gli_utenti:
            st.info("Non hai ancora creato nessun altro utente.")
        else:
            for u in tutti_gli_utenti:
                col_u1, col_u2, col_u3 = st.columns([2, 1, 1])
                with col_u1:
                    st.markdown(f"👤 **{u}**")
                with col_u2:
                    if st.button("👁️ Entra", key=f"entra_{u}", use_container_width=True):
                        st.session_state["profilo_attivo"] = u
                        st.session_state["materia_selezionata"] = None
                        st.rerun()
                with col_u3:
                    if st.button("🗑️ Elimina", key=f"elimina_u_{u}", use_container_width=True):
                        supabase.table("quiz").delete().eq("proprietario", u).execute()
                        supabase.table("utenti").delete().eq("username", u).execute()
                        if st.session_state["profilo_attivo"] == u:
                            st.session_state["profilo_attivo"] = "admin"
                        st.toast(f"Utente '{u}' e tutti i suoi quiz eliminati!")
                        st.rerun()
