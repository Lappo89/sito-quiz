import time
from datetime import datetime
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel
from supabase import create_client

# Impostazione ottimizzata per smartphone
st.set_page_config(page_title="I Miei Quiz", page_icon="🧠", layout="centered")

# ==========================================
# CONTROLLO PASSWORD DI ACCESSO
# ==========================================
if "autenticato" not in st.session_state:
    st.session_state["autenticato"] = False

if not st.session_state["autenticato"]:
    st.title("🔒 Accesso Riservato")
    password_inserita = st.text_input("Inserisci la password per accedere ai tuoi quiz:", type="password")
    if st.button("Accedi", type="primary", use_container_width=True):
        if password_inserita == st.secrets["APP_PASSWORD"]:
            st.session_state["autenticato"] = True
            st.rerun()
        else:
            st.error("Password errata. Riprova.")
    st.stop()

# Stato per ricordare quale materia (quadrotto) è stata aperta
if "materia_selezionata" not in st.session_state:
    st.session_state["materia_selezionata"] = None

# Connessione ai servizi tramite i "Secrets" di Streamlit
client_gemini = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

# Struttura rigorosa del quiz per Gemini
class Domanda(BaseModel):
    testo: str
    opzioni: list[str]
    risposta_corretta: str
    spiegazione: str

class QuizGenerato(BaseModel):
    domande: list[Domanda]

st.title("🧠 I Miei Quiz")

# Scarica tutti i quiz salvati nel database una volta sola
risposta_db = supabase.table("quiz").select("*").order("created_at", desc=True).execute()
tutti_i_quiz = risposta_db.data or []

quiz_da_fare = [q for q in tutti_i_quiz if not q.get("completato")]
quiz_completati = [q for q in tutti_i_quiz if q.get("completato")]

# Navigazione a 3 schede
tab_esercitati, tab_completati, tab_crea = st.tabs([
    f"📝 Da Fare ({len(quiz_da_fare)})",
    f"✅ Completati ({len(quiz_completati)})",
    "➕ Crea Nuovo"
])

# ==========================================
# SCHEDA 1: ESERCITATI (QUADROTTI MATERIE)
# ==========================================
with tab_esercitati:
    materie = sorted(list(set(q["materia"] for q in quiz_da_fare)))

    # Se la materia aperta non esiste più (eliminata o completata), torna alla griglia
    if st.session_state["materia_selezionata"] not in materie:
        st.session_state["materia_selezionata"] = None

    # VISTA 1: GRIGLIA DI QUADROTTI (se nessuna materia è aperta)
    if st.session_state["materia_selezionata"] is None:
        if materie:
            if st.button("🔄 Aggiorna"):
                st.rerun()

            st.write("")
            # Crea i quadrotti a coppie (2 per riga)
            for i in range(0, len(materie), 2):
                cols = st.columns(2)
                for j, col in enumerate(cols):
                    if i + j < len(materie):
                        mat = materie[i + j]
                        n_quiz = sum(1 for q in quiz_da_fare if q["materia"] == mat)
                        testo_quiz = "1 quiz" if n_quiz == 1 else f"{n_quiz} quiz"
                        with col:
                            if st.button(
                                f"📚 **{mat}**\n\n{testo_quiz}",
                                key=f"card_{mat}",
                                use_container_width=True
                            ):
                                st.session_state["materia_selezionata"] = mat
                                st.rerun()
        # Se non ci sono materie, lo spazio rimane vuoto

    # VISTA 2: QUIZ DENTRO LA MATERIA CLICCATA
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

        # Filtra i quiz della materia selezionata
        quiz_materia = [q for q in quiz_da_fare if q["materia"] == materia_scelta]
        titoli = {f"{q['titolo']} (#{q['id']})": q for q in quiz_materia}
        titolo_scelto = st.selectbox("Scegli il quiz:", list(titoli.keys()), key="tit_da_fare")

        quiz_attivo = titoli[titolo_scelto]
        domande = quiz_attivo["domande"]

        # Menu a tendina per eliminare il singolo quiz o l'intera materia in sicurezza
        with st.expander("🗑️ Elimina quiz o materia"):
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Elimina solo questo quiz**")
                st.caption(f"Rimuove '{quiz_attivo['titolo']}'.")
                if st.button("Elimina quiz", type="secondary", key=f"del_quiz_{quiz_attivo['id']}"):
                    supabase.table("quiz").delete().eq("id", quiz_attivo["id"]).execute()
                    st.toast("Quiz eliminato!")
                    st.rerun()

            with col2:
                st.markdown("**Elimina intera materia**")
                st.caption(f"Rimuove '{materia_scelta}' e tutti i suoi quiz.")
                if st.button("Elimina materia", type="primary", key=f"del_mat_{materia_scelta}"):
                    supabase.table("quiz").delete().eq("materia", materia_scelta).execute()
                    st.session_state["materia_selezionata"] = None
                    st.toast(f"Materia '{materia_scelta}' eliminata!")
                    st.rerun()

        st.divider()

        # Mostra le domande con correzione immediata
        punteggio = 0
        risposte_date = 0

        for i, d in enumerate(domande):
            st.markdown(f"**{i+1}. {d['testo']}**")
            scelta = st.radio(
                "Seleziona una risposta:",
                d["opzioni"],
                key=f"quiz_{quiz_attivo['id']}_q_{i}",
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

            if st.button("✅ Salva e archivia in Completati", type="primary", use_container_width=True, key=f"archivia_{quiz_attivo['id']}"):
                data_oggi = datetime.now().strftime("%d/%m/%Y %H:%M")
                supabase.table("quiz").update({
                    "completato": True,
                    "punteggio": f"{punteggio} / {len(domande)}",
                    "data_completamento": data_oggi
                }).eq("id", quiz_attivo["id"]).execute()
                st.toast("Quiz archiviato nella cartella Completati!")
                st.rerun()

# ==========================================
# SCHEDA 2: CARTELLA QUIZ COMPLETATI
# ==========================================
with tab_completati:
    if not quiz_completati:
        st.info("Non hai ancora archiviato nessun quiz completato.")
    else:
        materie_comp = ["Tutte le materie"] + sorted(list(set(q["materia"] for q in quiz_completati)))
        filtro_mat = st.selectbox("Filtra per materia:", materie_comp, key="filtro_comp")

        lista_filtrata = (
            quiz_completati if filtro_mat == "Tutte le materie"
            else [q for q in quiz_completati if q["materia"] == filtro_mat]
        )

        for q in lista_filtrata:
            punteggio_str = q.get("punteggio") or "N/D"
            data_str = q.get("data_completamento") or "N/D"
            etichetta = f"✅ {q['titolo']} ({q['materia']}) — Punteggio: {punteggio_str} | Data: {data_str}"

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
                    st.markdown(f"**{idx+1}. {d['testo']}**")
                    st.markdown(f"👉 *{d['risposta_corretta']}* — {d['spiegazione']}")

# ==========================================
# SCHEDA 3: CREA E SALVA UN NUOVO QUIZ
# ==========================================
with tab_crea:
    st.subheader("Genera un quiz con Gemini")

    materia = st.text_input("Materia (es. Diritto Privato, Biologia, Storia)")
    titolo = st.text_input("Titolo del quiz (es. Capitolo 3 - I Contratti)")
    file_appunti = st.file_uploader("Carica i tuoi appunti (PDF o TXT)", type=["pdf", "txt"])
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
            with st.spinner("Gemini sta leggendo gli appunti e preparando le domande..."):
                try:
                    # Carica il file temporaneamente su Gemini
                    uploaded_file = client_gemini.files.upload(
                        file=file_appunti,
                        config={"mime_type": file_appunti.type}
                    )

                    prompt = f"""
                    Crea un quiz di {num_domande} domande a risposta multipla (4 opzioni ciascuna) basato SOLO sul documento allegato.
                    La risposta corretta deve corrispondere esattamente a una delle 4 opzioni.
                    Aggiungi una spiegazione chiara e sintetica per ogni domanda.
                    """
                    if istruzioni_extra:
                        prompt += f"\nSegui attentamente queste istruzioni specifiche dell'utente:\n{istruzioni_extra}"
                    if esempi_quiz:
                        prompt += f"\nSegui rigorosamente lo stile, il tono e la difficoltà di questi esempi:\n{esempi_quiz}"

                    # Generazione con cambio automatico di modello in caso di 503 o 429
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
                                contents=[uploaded_file, prompt],
                                config=types.GenerateContentConfig(
                                    response_mime_type="application/json",
                                    response_schema=QuizGenerato,
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

                    quiz_pronto = QuizGenerato.model_validate_json(response.text)

                    # Salva il quiz nel database Supabase
                    dati_da_salvare = {
                        "materia": materia.strip(),
                        "titolo": titolo.strip(),
                        "domande": [d.model_dump() for d in quiz_pronto.domande],
                        "completato": False
                    }
                    supabase.table("quiz").insert(dati_da_salvare).execute()

                    st.success(f"Quiz '{titolo}' salvato in '{materia}'! Vai nella scheda 'Da Fare' per provarlo.")
                except Exception as e:
                    st.error(f"Si è verificato un errore: {e}")
