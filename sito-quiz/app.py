import time
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

# Struttura rigorosa del quiz per Gemini
class Domanda(BaseModel):
    testo: str
    opzioni: list[str]
    risposta_corretta: str
    spiegazione: str

class QuizGenerato(BaseModel):
    domande: list[Domanda]

st.title("🧠 I Miei Quiz")

# Navigazione a schede
tab_esercitati, tab_crea = st.tabs(["📝 Esercitati", "➕ Crea Nuovo Quiz"])

# ==========================================
# SCHEDA 1: ESERCITATI CON I QUIZ SALVATI
# ==========================================
with tab_esercitati:
    if st.button("🔄 Aggiorna elenco quiz"):
        st.rerun()

    # Scarica tutti i quiz salvati nel database
    risposta_db = supabase.table("quiz").select("*").order("created_at", desc=True).execute()
    tutti_i_quiz = risposta_db.data

    if not tutti_i_quiz:
        st.info("Non hai ancora creato nessun quiz. Vai nella scheda 'Crea Nuovo Quiz' per iniziare!")
    else:
        # Filtra per materia
        materie = sorted(list(set(q["materia"] for q in tutti_i_quiz)))
        materia_scelta = st.selectbox("Scegli la materia:", materie)

        # Filtra i quiz della materia selezionata (include l'ID per evitare sovrascritture)
        quiz_materia = [q for q in tutti_i_quiz if q["materia"] == materia_scelta]
        titoli = {f"{q['titolo']} (#{q['id']})": q for q in quiz_materia}
        titolo_scelto = st.selectbox("Scegli il quiz:", list(titoli.keys()))

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
                st.caption(f"Rimuove '{materia_scelta}' e tutti i suoi {len(quiz_materia)} quiz.")
                if st.button("Elimina materia", type="primary", key=f"del_mat_{materia_scelta}"):
                    supabase.table("quiz").delete().eq("materia", materia_scelta).execute()
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

# ==========================================
# SCHEDA 2: CREA E SALVA UN NUOVO QUIZ
# ==========================================
with tab_crea:
    st.subheader("Genera un quiz con Gemini")

    materia = st.text_input("Materia (es. Diritto Privato, Biologia, Storia)")
    titolo = st.text_input("Titolo del quiz (es. Capitolo 3 - I Contratti)")
    file_appunti = st.file_uploader("Carica i tuoi appunti (PDF o TXT)", type=["pdf", "txt"])
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
                        "domande": [d.model_dump() for d in quiz_pronto.domande]
                    }
                    supabase.table("quiz").insert(dati_da_salvare).execute()

                    st.success(f"Quiz '{titolo}' salvato in '{materia}'! Vai nella scheda 'Esercitati' per provarlo.")
                except Exception as e:
                    st.error(f"Si è verificato un errore: {e}")
