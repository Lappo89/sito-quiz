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

# Frammento live per il timer della modalità Esame (si aggiorna ogni secondo)
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

# Funzione di supporto per calcolare la media dei punteggi (es. "4 / 5" -> 80%)
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
        quiz_id = quiz_attivo["id"]
        domande = quiz_attivo["domande"]

        # Menu a tendina per eliminare il singolo quiz o l'intera materia in sicurezza
        with st.expander("🗑️ Elimina quiz o materia"):
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Elimina solo questo quiz**")
                st.caption(f"Rimuove '{quiz_attivo['titolo']}'.")
                if st.button("Elimina quiz", type="secondary", key=f"del_quiz_{quiz_id}"):
                    supabase.table("quiz").delete().eq("id", quiz_id).execute()
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

        # Scelta modalità: Allenamento vs Esame
        modalita = st.radio(
            "Modalità di svolgimento:",
            ["🏋️ Allenamento", "🎓 Esame con Timer"],
            horizontal=True,
            key=f"mod_{quiz_id}"
        )

        st.divider()

        # ------------------------------------------
        # MODALITÀ A: ALLENAMENTO (correzione immediata)
        # ------------------------------------------
        if modalita == "🏋️ Allenamento":
            # Calcola risposte già date per la barra di avanzamento
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
                st.markdown(f"**{i+1}. {d['testo']}**")
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
                    st.toast("Quiz archiviato nella cartella Completati!")
                    st.rerun()

        # ------------------------------------------
        # MODALITÀ B: ESAME CON TIMER E CONSEGNA AUTOMATICA
        # ------------------------------------------
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

            # FASE 1: Impostazione durata prima di iniziare
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

            # FASE 2: Esame in corso
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
                    st.markdown(f"**{i+1}. {d['testo']}**")
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

            # FASE 3: Esame consegnato (manualmente o per tempo scaduto)
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
                        st.toast("Esame archiviato nella cartella Completati!")
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
                    st.markdown(f"**{i+1}. {d['testo']}**")
                    if scelta is None:
                        st.warning(f"Nessuna risposta data. Risposta corretta: **{d['risposta_corretta']}**\n\n{d['spiegazione']}")
                    elif scelta == d["risposta_corretta"]:
                        st.success(f"Esatto! ({scelta}) — {d['spiegazione']}")
                    else:
                        st.error(f"Hai risposto: *{scelta}*. Risposta corretta: **{d['risposta_corretta']}**\n\n{d['spiegazione']}")

# ==========================================
# SCHEDA 2: CARTELLA QUIZ COMPLETATI + STATISTICHE
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

        # Mini-statistiche dinamiche in base al filtro selezionato
        media_perc = calcola_media_percentuale(lista_filtrata)
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            st.metric("Quiz completati", len(lista_filtrata))
        with col_s2:
            st.metric("Media punteggi", f"{media_perc:.1f}%" if media_perc is not None else "N/D")

        st.divider()

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

    # Menu smart per la materia
    materie_esistenti = sorted(list(set(q["materia"] for q in tutti_i_quiz)))
    if materie_esistenti:
        scelta_mat = st.selectbox(
            "Materia:",
            materie_esistenti + ["➕ Nuova materia..."],
            key="smart_mat_select"
        )
        if scelta_mat == "➕ Nuova materia...":
            materia = st.text_input("Nome della nuova materia (es. Diritto Privato, Biologia, Storia)")
        else:
            materia = scelta_mat
    else:
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
