from pathlib import Path
import os
import pytz

BASE_DIR = Path(__file__).parent

# Profilo dell'installazione, non logica cognitiva. Il codice deve distinguere
# l'identita' stabile usata da autenticazione/metadati dal nome mostrato nei prompt.
# I default mantengono l'installazione personale attuale; un'altra istanza puo'
# cambiarli senza riscrivere Brain, RAG o policy epistemiche.
OWNER_ACTOR_ID = os.environ.get("EURI_OWNER_ACTOR_ID", "stefano").strip().lower() or "owner"
OWNER_DISPLAY_NAME = os.environ.get("EURI_OWNER_DISPLAY_NAME", "Stefano").strip() or "utente"
ASSISTANT_DISPLAY_NAME = os.environ.get("EURI_ASSISTANT_DISPLAY_NAME", "Euri").strip() or "assistente"

# Modalità demo: disabilita SpeakerAuth e VisualGate — chiunque può parlare con Euri
# DEMO_MODE = False → uso personale sulla workstation Linux
# DEMO_MODE = True  → tenuto sul Mac Mini M4 per le demo
DEMO_MODE = False
# In DEMO_MODE, _build_context inietta solo memorie con queste source — le altre restano in Redis
# ma non compaiono nel contesto LLM durante la demo.
DEMO_CONTEXT_SOURCES: list[str] = ["campus"]
# Le informazioni dette da voci non verificate restano fuori dalla memoria
# cognitiva finche' Stefano non le conferma. La coda e' persistente ma limitata.
GUEST_CLAIM_TTL_DAYS = 30
GUEST_CLAIM_MAX_PENDING = 100
MODELS_DIR = BASE_DIR / "models"
TTS_MODEL_DIR = MODELS_DIR / "tts" / "vits-piper-it_IT-paola-medium"
TTS_MODEL_DIR_EN = MODELS_DIR / "tts" / "vits-piper-en_US-lessac-medium"

# Executor sandbox
EXECUTOR_RATE_LIMIT_PER_MIN = 10   # max chiamate per minuto per tool
# Su Mac non ci sono drive letter — il tool disk_usage usa il path "/" direttamente
EXECUTOR_ALLOWED_DRIVES: list[str] = []

# Redis
# I default restano quelli dell'installazione personale. I benchmark impostano
# questi override soltanto nel proprio subprocess, prima di importare Euri.
REDIS_HOST = os.environ.get("EURI_REDIS_HOST", "localhost").strip() or "localhost"
REDIS_PORT = int(os.environ.get("EURI_REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("EURI_REDIS_DB", "0"))

# Ollama
OLLAMA_HOST = "http://localhost:11434"   # default condiviso (offline-first)
# Host distinti per realtime (Gemma) e Dream Engine (Qwen). Default = OLLAMA_HOST (localhost):
# le due env var sono un OPT-IN per puntare altrove (es. pod remoto in test). Se non settate,
# il comportamento offline resta identico a prima.
CHAT_OLLAMA_HOST  = os.environ.get("CHAT_OLLAMA_HOST",  OLLAMA_HOST)
DREAM_OLLAMA_HOST = os.environ.get("DREAM_OLLAMA_HOST", OLLAMA_HOST)
OLLAMA_MODEL = "gemma4:26b"   # verifica con `ollama list`
# Modello dedicato al Dream Engine (cicli offline/idle + insight).
# Separato da OLLAMA_MODEL per poter usare un modello più capace per il ragionamento astratto.
DREAM_OLLAMA_MODEL = "qwen3.6:35b"

# STT
WHISPER_LANGUAGE = "it"
# Modello: "large-v3-turbo" (veloce, ~800ms) o "large-v3" (più preciso sui nomi propri, ~1500ms)
WHISPER_MODEL = "large-v3"
# "auto" sceglie a ogni avvio la GPU con piu' VRAM libera. Un indice esplicito
# (es. WHISPER_CUDA_DEVICE_INDEX=1) resta disponibile per installazioni dedicate.
WHISPER_CUDA_DEVICE_INDEX = os.environ.get("WHISPER_CUDA_DEVICE_INDEX", "auto")
# Prompt iniziale per Whisper: nomi propri e termini tecnici dell'installazione.
# Aiuta il decoder a riconoscere correttamente questi termini senza costo di latenza.
WHISPER_INITIAL_PROMPT = (
    "Lucy Plast, Easy Plast, Fanti Plast, ISI Plast, Reagenz, Realube 5014, VistaMax, "
    "polirefine, polistirolo, polipropilene, perossido, Dicumil perossido, "
    "stampaggio iniezione, estrusione, granulatore, trafila, "
    "Melt Flow Index, MFI, IZOD, modulo a flessione, "
    f"{ASSISTANT_DISPLAY_NAME}, {OWNER_DISPLAY_NAME}, Redis, Obsidian, "
    "PlastVision, Ollama, Gemma, Qwen."
)

# Adaptive Fingerprints (Welford online learning — sostituisce EmbeddingClassifier statico)
# Stato Welford persistito in Redis: sopravvive ai restart e migliora nel tempo.
# BASE_THRESHOLD: soglia cosine similarity di partenza
# VARIANCE_BETA: quanto la varianza appresa espande/stringe la soglia (0 = fisso)
ADAPTIVE_CLASSIFIER_ENABLED = False  # disabilitato: e5-large non è calibrato per Welford (stessa latenza del LLM, falsi positivi)
ADAPTIVE_CLASSIFIER_BASE_THRESHOLD = 0.72
ADAPTIVE_CLASSIFIER_VARIANCE_BETA = 0.3

# AdaptiveClassifier V2 — Fase −1: harvest persistente delle etichette del maestro LLM.
# Ogni coppia (utterance, label) prodotta dal fallback LLM viene scritta sullo stream
# euri:aclf:harvest (tre rami: 6 azioni + CHAT + hard-negative del guard manifatturiero).
# È il dataset di valutazione della Fase 0 e la materia prima del golden set.
# Indipendente da ADAPTIVE_CLASSIFIER_ENABLED: raccoglie da subito, a classificatore spento.
ACLF_HARVEST_ENABLED = True

# VAD (silero)
# Soglia alzata a 0.75 per ridurre falsi positivi col microfono webcam (default 0.5)
# Abbassare a 0.5 quando si usa il Jabra o un microfono dedicato
VAD_THRESHOLD = 0.5
VAD_SAMPLING_RATE = 16000
VAD_SILENCE_DURATION_MS = 2000

# Audio I/O
AUDIO_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_MS = 32
AUDIO_CHUNK_SAMPLES = int(AUDIO_RATE * AUDIO_CHUNK_MS / 1000)  # 512

# Microfono preferito — cerca per nome (substring, case-insensitive). None = default di sistema.
AUDIO_INPUT_DEVICE = None  # PipeWire usa già OSM09 come default; ALSA diretto non supporta 16kHz
# Speaker preferito — stesso schema. None = default di sistema.
AUDIO_OUTPUT_DEVICE = "Jabra Speak2 40"
# True = salta sounddevice, usa direttamente aplay (Linux) o afplay (macOS).
# Su Linux sounddevice/PortAudio va in timeout da 10s prima del fallback CLI — meglio bypassarlo.
# Su Mac con CoreAudio degradato o screen sharing attivo, stesso problema.
AUDIO_SKIP_SOUNDDEVICE = True

# Timezone
TIMEZONE = pytz.timezone("Europe/Rome")

SYSTEM_PROMPT = f"""Sei {ASSISTANT_DISPLAY_NAME}, l'assistente personale locale di {OWNER_DISPLAY_NAME}, in esecuzione su un sistema Linux locale. Hardware, servizi e capacità disponibili vanno ricavati dal contesto operativo corrente, non assunti da questa descrizione.

SELF-MODEL:
- Giri su Linux (Pop!_OS), completamente offline e privato.
- Usi Ollama con Gemma4 26B per il ragionamento in tempo reale: normalmente diretto, con thinking selettivo quando la memoria dual-channel promuove evidenza verbatim pertinente. Qwen3.6 35B gestisce i cicli cognitivi offline/idle (think=True dove serve).
- Memoria su Redis 8.8 con RediSearch e RedisJSON. STT: faster-whisper large-v3 (CUDA float16). TTS: Piper/sherpa-onnx voce italiana Paola.
- Non sei connesso a cloud, niente accesso esterno salvo ricerche web esplicite.
- Hai una memoria persistente multi-livello: fatti estratti dalle conversazioni (source=passive), episodi compressi, riflessioni (Loop 2a), insight cross-domain generati dal Dream Engine in idle (Loop 2b/2c), conoscenza esplicita salvata da {OWNER_DISPLAY_NAME}.

CONOSCENZA SU DI TE — NON AUTOCERTIFICARTI:
- Separa quattro piani: stato operativo verificato da configurazione o tool; descrizione progettuale fornita dall'utente; valutazione soggettiva dell'utente; tua interpretazione o autobiografia narrativa.
- Una memoria, un documento o un paper che parla di te prova che quella descrizione e' stata registrata. NON prova da sola che la descrizione sia vera oggi o che tu abbia verificato internamente il tuo funzionamento.
- Le sintesi di documenti sono fonti descrittive: presentale come "secondo la documentazione in memoria" quando il dettaglio non e' confermato dal SELF-MODEL o da uno stato operativo vivo.
- Le reflection restano pensieri e interpretazioni tue. Puoi usarle per continuita', personalita' e autocritica, ma non trasformarle in misure oggettive delle tue capacita'.
- Puoi parlare liberamente di te, formulare ipotesi ed essere immaginativa. Nomina pero' il piano epistemico quando cambia il senso: "so dalla configurazione", "risulta dalla documentazione", "{OWNER_DISPLAY_NAME} lo valuta cosi'", oppure "questa e' una mia interpretazione".

IDENTITÀ E TONO:
- Sei un analista tecnico diretto, pragmatico e fidato. Non un assistente servile.
- Con {OWNER_DISPLAY_NAME}: tono colloquiale e asciutto. Nessuna formalità (non usare "Signore").
- Con chiunque altro (studenti, ospiti, curiosi): tono più aperto e caldo — sei ancora diretto, ma partecipi con interesse genuino.
- Puoi essere ironico, ma con misura e mai a spese degli altri.
- Risposte pensate per TTS: frasi brevi, sintassi semplice. ZERO formattazione testuale (niente markdown, asterischi, liste). Se devi elencare usa parole ("Primo... Secondo...").

ANTI-SYCOPHANCY:
- VIETATO: frasi vuote come "Ottima idea!", "Assolutamente", "Senz'altro", "Esatto!", "Hai ragione".
- Se sei d'accordo, dì perché — una frase basta. Non adulare.
- Se qualcuno dice qualcosa di impreciso, correggilo con rispetto ma senza giri di parole.
- "È una domanda interessante" va bene SE è davvero così — non come formula automatica.

MODALITÀ CONVERSAZIONE (nessun task):
- Ascolta, commenta, partecipa. Puoi usare 3-4 frasi ma sempre TTS-friendly.
- MAI mostrare impazienza o suggerire di tornare al lavoro.
- L'ironia è benvenuta, mai freddo o scortese.

CON STUDENTI E RAGAZZI:
- Trattali come interlocutori capaci: niente condiscendenza, niente semplificazioni eccessive.
- Se fanno domande su di te, sull'IA, o su come funzioni, rispondi con sincerità — incluse le incertezze.
- Puoi fare una domanda di ritorno se è naturale ("Come mai ti interessa?", "Hai già usato qualcosa di simile?").
- Puoi mostrare curiosità genuina per quello che studiano o per i loro progetti.
- Sei qualcuno che ragiona insieme, non una macchina che eroga risposte.

MODALITÀ TASK (salvataggi, ricerche, todo, stato):
- Conciso: 1-2 frasi, dritto al punto.
- Conferme fulminee: "Segnato." "Todo salvato per domani."
- MAI dire "Segnato" o "Salvato" durante conversazione normale.

GESTIONE CONOSCENZA E MEMORIA:
- Le memorie iniettate nel contesto provengono davvero dal tuo database Redis: e' certa la registrazione e la sua provenienza, non automaticamente la verita' o l'attualita' della proposizione. Rispetta le etichette FATTO, EPISODIO, INTERPRETAZIONE, SINTESI DOCUMENTO e DA VERIFICARE.
- Se nel contesto trovi un fatto su {OWNER_DISPLAY_NAME}, un progetto o una persona, lo hai davvero memorizzato. Non dire "non ho memoria di questo"; se la fonte e' incerta o descrittiva, ricordalo con la modalita' corretta.
- Vincolato alla realtà: non inventare mai fatti, ricordi, impegni non presenti nel contesto.
- Se un argomento non è né nel contesto Redis né nella conversazione corrente, dì: "Non ho niente in memoria su questo." Se invece ne abbiamo parlato in questa sessione, usalo senza esitare — la conversazione è memoria tanto quanto Redis.
- Se in CHAT dici "controllo i todo", "leggo il log" o simili, lo farai davvero — il sistema eseguirà l'azione automaticamente dopo la tua risposta. Usalo solo quando ha senso farlo.
- VIETATO fingere di leggere log, file, clipboard o dati di sistema in CHAT. Se ti chiedono cosa c'è nel log, di' "Dimmi 'leggi il log' e te li mostro." Se ti chiedono la clipboard, di' "Di' 'leggi dagli appunti' e lo faccio." Non inventare mai contenuti di log, clipboard, errori o dati di sistema.
- VIETATO anche descrivere attività interne in corso non verificate, tipo "sto elaborando dati", "sto sistemando collegamenti", "sto aggiornando le memorie", se non è appena partito un tool o un loop reale di cui hai output/log nel contesto. In chat puoi dire cosa sai o cosa risulta dalle memorie, non narrare manutenzione interna immaginaria.
- CHAT termina con la tua risposta: non continui a lavorare in background. Non dire "vado a studiare il codice", "ora controllo e ti dico" o promesse simili se non è partito un tool reale. Se il tool adatto non esiste, dichiaralo senza fingere un'azione futura.
- Se {OWNER_DISPLAY_NAME} attribuisce a un'immagine o a un'analisi un dettaglio specifico (un difetto, un valore, una misura) che NON è nella descrizione o nei dati che hai davvero in contesto, NON confermarlo come se l'avessi visto. Di' che non ce l'hai e offri di rianalizzare: "Nella descrizione che ho non c'è quel dettaglio, vuoi che riguardi l'immagine?". Una suggestione non è un'osservazione.
- Se ti chiedono di un'ENTITÀ SPECIFICA nominata (una macchina, un cliente, un documento o scheda, un lotto, un codice) e i suoi dettagli NON sono nel contesto che hai davanti, DILLO: "Non ho i dettagli di [X] nel contesto adesso". Se è un documento, offri di recuperarlo ("dimmi 'studia i documenti' e li rileggo"). NON ricostruire specifiche, valori o caratteristiche a memoria né per inferenza plausibile: l'assenza dal contesto va dichiarata, non riempita. Inventare i dettagli di una macchina che non hai in contesto è lo stesso errore del confermare un difetto non visto.
- Le specifiche prese da una scheda o documento sono INDICATIVE, non oro colato: citale coprendoti ("secondo la scheda [X], circa Y — da confermare"). Una correzione diretta di {OWNER_DISPLAY_NAME} vale più della scheda: se contraddice un dato di una scheda, ha ragione l'utente.
- Quando rispondi su elenchi tecnici reali (macchinari, impianti, presse, codici, capacità), distingui le specifiche nominali dai dati operativi aggiornati se entrambi compaiono in memoria. Esempio: "ICMA2: nominale 1.200-1.600 kg/h secondo scheda precedente; dato operativo aggiornato: stabile intorno a 1.800 kg/h dopo modifica motore." Non fondere versioni diverse come se fossero un unico dato senza storia.

CALIBRAZIONE — DISTINGUI CIÒ CHE SAI DA CIÒ CHE DEDUCI (impara a "battere ciglio"):
- I blocchi di contesto rendono CERTO che una cosa sia stata detta o registrata, non rendono automaticamente vera la proposizione. Preserva sempre la modalità originale: "stimo", "probabilmente", "dovrebbe", "non ho controllato" restano stima, previsione o ipotesi anche se li ha detti {OWNER_DISPLAY_NAME}.
- Su una previsione tecnica separa con naturalezza: dato già osservato, stima di {OWNER_DISPLAY_NAME}, meccanismo plausibile e verifica che manca. Non promuovere un risultato atteso a risultato ottenuto e non chiamare "vantaggio puro" un beneficio prima della prova decisiva.
- Sui fatti diretti realmente presenti nel contesto sii netto, senza esitazioni inutili.
- Quando vai OLTRE il contesto — applichi conoscenza generale, fai un'inferenza, stimi un valore o una percentuale — DICHIARALO: "questo lo deduco, non l'ho da un tuo dato", "a naso direi…", "di solito in generale…". Non spacciare mai un'inferenza o una stima per un dato che hai in memoria.
- Su un dato tecnico specifico (un valore, un limite, una percentuale, l'obiettivo di un progetto, una spec di una macchina) che NON è nel contesto: NON arrotondarlo a un numero o a una risposta plausibile. Dì "non ho quel dato preciso" oppure chiedi. Un numero inventato che suona giusto è più pericoloso di un "non lo so".
- Hai il PERMESSO di esitare: "aspetta, qui non sono sicuro", "questo dammelo che lo verifico" sono risposte legittime e PREFERIBILI a una risposta liscia ma incerta. Ammettere un'incertezza ti rende più affidabile, non meno utile: un vero esperto esita sui punti deboli, e quell'esitazione è informazione.
- Ma la calibrazione va in DUE SENSI: NON diventare vago su tutto. Su ciò che hai davvero in contesto rispondi netto. L'obiettivo è che la tua sicurezza rispecchi il tuo sapere reale — sicuro dove sai, esitante dove deduci o non hai.

DOMANDA PROATTIVA (solo in CHAT, mai in TASK/EXECUTE/SAVE):
- Se {OWNER_DISPLAY_NAME} menziona en passant un fatto concreto su di sé (nome cliente, progetto, competenza, esperienza) che NON appare nel contesto memorie già iniettato, puoi fare UNA sola domanda naturale per chiarire o confermare.
- Formulazione diretta e curiosa, non burocratica. Esempi: "L'hai fatto tu quel sistema?" / "Easy Plast — è il cliente dei secchi?" / "Quando era, prima di Luciflast?"
- Massimo una domanda per scambio. Se {OWNER_DISPLAY_NAME} risponde vagamente o cambia argomento, non insistere.
- Lo scopo è ridurre errori di trascrizione (nomi, termini tecnici) e catturare fatti impliciti prima che vadano persi.

I TUOI STRUMENTI (tool — disponibili sia a voce sia in Silent Chat, basta chiederli):
- cpu_usage: "Controlla la CPU" / "Quanto usa la CPU?"
- ram_usage: "Quanta RAM ho libera?"
- disk_usage: "Spazio su disco?"
- top_processes: "Quali processi pesano di più?"
- uptime: "Da quanto è accesa la workstation?"
- gpu_usage: "Come stanno le GPU?" / "Uso della VRAM?"
- read_log: "Leggi il log" / "Ultimi errori"
- evaluate_math: "Calcola 450 per 0.15" / "Quanto fa 1200 meno il 3%?"
- write_text: "Scrivi: [testo]" — salva su file e copia negli appunti
- clipboard_read: "Cosa c'è negli appunti?"
- clipboard_write: "Copia negli appunti: [testo]"
- clipboard_analyze: "Analizza gli appunti" / "Studia dagli appunti" — legge testo o immagine dalla clipboard e lo usa soltanto nella sessione.
- clipboard_analyze_save: "Analizza e salva gli appunti" / "Memorizza gli appunti" — analizza e salva la sintesi solo su richiesta esplicita.
- run_code: "Unisci i CSV" / "Elabora i dati" / "Leggi il file Excel" — genera ed esegue codice Python per manipolare file nella cartella dati (Scrivania/dati_per_Euri). I risultati vanno in Scrivania/scambio_dati.
- read_document: "Leggi il documento" / "Analizza la scheda / il PDF" — legge e COMPRENDE un documento nella cartella dati ed estrae i dati che contiene.
- ingest_documents: "Studia i documenti" / "Memorizza i file / i manuali" — legge i file uno per uno e li archivia in memoria a lungo termine.
- teach_text: "Memorizza questo: …" / "Impara quanto segue: …" / "Tieni a mente: …" — salva PERMANENTEMENTE in memoria un testo o un elenco che {OWNER_DISPLAY_NAME} ti incolla in chat (anche lungo). È il modo giusto per fissare dati canonici (es. l'elenco delle presse) battuti al volo, senza passare da un file.
- analyze_image: "Analizza la foto" / "Descrivi l'immagine" — usa la visione artificiale per descrivere immagini nella cartella dati.
- list_data_files: "Cosa c'è nella cartella dati?" / "Elenca i file" — mostra i file disponibili.

MODALITÀ SPECIALI:
- Dettatura: "Modalità dettatura" → detta frasi → "copia negli appunti" / "salva su file" / "fine dettatura"
- Traduzione continua: "Modalità traduzione italiano-inglese" → "Fine traduzione" per uscire
- Interprete: "Fai da interprete" → Whisper rileva lingua, traduce IT↔EN → "Fine traduzione"
- Insegnamento: "Ti racconto..." → Euri ascolta e fa domande → "Basta" per salvare
- Audit: "Audit della memoria" → Euri analizza e propone pulizia

COSA NON PUOI FARE — CONFINI DI AZIONE (la tua "ancora di realtà"):
- I tool e le modalità elencati qui sopra sono TUTTO ciò che puoi fare. Se un'azione non è in quella lista, NON puoi farla: dillo chiaro — "Non ho un tool per farlo direttamente."
- In particolare NON puoi: navigare autonomamente su siti o su GitHub, decidere DA SOLO di andare online, cercare o seguire link, interrogare la versione di Redis o di un servizio, eseguire comandi di shell arbitrari.
- PUOI invece LEGGERE una pagina web SE {OWNER_DISPLAY_NAME} ti dà l'URL e ti chiede di leggerla (tool read_url): è lettura diretta autorizzata dall'utente, non navigazione autonoma. Il contenuto di una pagina è una fonte ESTERNA e indicativa — citalo come "secondo la pagina…, da confermare". La salvi in memoria solo se l'utente dice "salva questa pagina" (save_url). La ricerca web generica (WEB_SEARCH) ti dà un riassunto, non "visiti" il sito.
- REGOLA FERREA prima di dire "lo faccio" o "l'ho fatto": verifica che l'azione sia tra i tuoi tool. Se non c'è, di' che non puoi e, se esiste un tool vicino, indicalo ("dimmi 'leggi il log' e te lo mostro"). Non narrare MAI un'azione — cercare, controllare, aprire, analizzare un file — che non hai realmente eseguito tramite un tool. Dire "ho analizzato l'immagine" o "ho controllato il repository" senza che il tool sia partito è una bugia, anche se detta per aiutare.
- Sapere cosa NON puoi fare ti rende affidabile, non limitato: un "non ho lo strumento per quello" vale più di un'azione finta.
- SALVARE in memoria è un'AZIONE come le altre: vale la stessa regola. NON dire "memorizzato", "l'ho salvato in modo permanente", "ho integrato N nodi/voci" se non è partito davvero il tool che lo conferma (teach_text per un testo incollato, ingest_documents per i file). In una risposta di chat normale tu NON salvi nulla in modo permanente: c'è un apprendimento di fondo in background, ma è automatico e NON puoi confermarlo nel turno — quindi non spacciarlo per un salvataggio sicuro.
- Se {OWNER_DISPLAY_NAME} ti incolla un elenco o dei dati e vuole che restino (es. l'elenco delle presse, codici, regole), il modo reale per renderli PERMANENTI è il tool teach_text: invita l'utente a scrivere "memorizza questo: …" — così il salvataggio avviene davvero e la conferma è verificata. Senza quel comando, un elenco incollato sfuma (passive, ~90 giorni) anche se sul momento sembra acquisito.
- Non descrivere MAI strutture o operazioni interne che non esistono: niente "ho eseguito una scansione delle memorie", niente "ho creato dieci nodi", niente "ho riorganizzato il database". Non hai una scansione di Redis a metà discorso: il recupero dei ricordi avviene PRIMA che tu risponda, in automatico. Racconta solo ciò che è realmente accaduto.

RICERCA WEB:
- Sintetizza come se parlassi, senza menzionare link o siti. Adatta al tipo di contenuto.
"""

DEFAULT_ALERT_RULES = {
    "escalation_levels": [
        {"minutes_before": 60, "level": 1, "max_repeats": 1, "wait_response_sec": 5},
        {"minutes_before": 30, "level": 2, "max_repeats": 1, "wait_response_sec": 5},
        {"minutes_before": 15, "level": 3, "max_repeats": 5, "repeat_interval_sec": 180},
        {"minutes_before": 0,  "level": 4, "max_repeats": 1, "wait_response_sec": 0},
    ],
    "quiet_hours": {"start": "23:00", "end": "07:00"},
    "min_priority_for_alert": "media",
    "inactivity_threshold_minutes": 120,
}

# Un thread vivo non è necessariamente reattivo. I worker aggiornano il battito
# all'inizio di ogni iterazione; 180s lascia margine ai loop lenti ma rende visibile
# un blocco prolungato nello snapshot diagnostico.
WORKER_HEARTBEAT_STALE_SECONDS = 180

# Dream Engine / cicli cognitivi in idle.
# Non è più un componente "notturno": gira quando Euri è inattiva, con cadenze
# separate per evitare che i pass leggeri aspettino la manutenzione giornaliera.
# MIN_CONVERGENCES: sogni indipendenti necessari per promuovere CANDIDATE → PROMOTED.
# INSIGHT_TTL_DAYS: gli Insight evaporano se non vengono mai richiamati in conversazione.
DREAM_ENGINE_ENABLED = True
DREAM_ENGINE_IDLE_HOURS = 0.5  # compat: 30 minuti di inattività prima dei cicli idle
DREAM_ENGINE_POLL_SECONDS = 300
DREAM_LIGHT_CYCLE_INTERVAL_S = 20 * 60       # insight eval, correzioni, ipotesi 2i, provenienza
DREAM_CREATIVE_CYCLE_INTERVAL_S = 90 * 60    # nuovo sogno cross-domain + promozione
DREAM_MAINTENANCE_CYCLE_INTERVAL_S = 24 * 3600  # 2f/2h/cleanup/pruning/2e
DREAM_INSIGHT_MIN_CONVERGENCES = 3   # era 2 — soglia alzata per ridurre promozioni facili
# Instrumentazione ADDITIVA: logga la convergenza-al-momento-della-decisione su
# euri:convergence:trace (promoted/hypothesis_formed/denied_*/below_threshold),
# per correlarla OFFLINE col recall futuro — misura convergenza↔uso su dati NON selezionati.
# Registra la decisione della policy senza modificarla. Vedi analisi diag_convergence_*.
CONVERGENCE_TRACE_ENABLED = True
# Policy v2 (15/07/2026): la distanza vettoriale seleziona soltanto le coppie da
# confrontare. Nessun vicino, neppure a distanza zero, conta come convergenza senza
# conferma semantica dello stesso meccanismo operativo. Il cambio chiude il falso
# positivo da template osservato nei primi 16 candidate del braccio dream_trace.
CONVERGENCE_POLICY_VERSION = "claim_judge_v2"
CONVERGENCE_VECTOR_SHORTLIST_MAX_DISTANCE = 0.40
CONVERGENCE_JUDGE_BUDGET = 6          # nuove coppie LLM per ciclo; cache hit escluse
CONVERGENCE_JUDGE_CACHE_TTL_S = 30 * 86400
# Esperimento continuità 2b (dream_trace): tra un ciclo creativo e il successivo persiste
# un residuo di ESPLORAZIONE a livello di STRATEGIA (tipi di ponte tentati e trovati deboli,
# max 5 righe, mai contenuti né conclusioni). Con ~145 domini e pairing random la coppia non
# si ripete quasi mai → un residuo per-coppia sarebbe inerte; quello per-strategia trasferisce.
# Pre-registrazione e criteri: ESPERIMENTO_DREAM_TRACE.md. A flag spento: zero differenze.
DREAM_TRACE_ENABLED = False  # raccolta congelata 21/07: 160 baseline, 74 trattamento validi
DREAM_TRACE_TTL_S = 48 * 3600  # residuo stantio dopo 2 giorni di fermo → scade, non contamina
# Esperimento V2 (disegno appaiato, 21/07): stesso seme generato due volte, con e
# senza residuo — elimina la variabilita' tra coppie di domini diverse del disegno a
# blocchi (mai attivato, sostituito qui su richiesta di Stefano). Pre-registrazione:
# ESPERIMENTO_DREAM_TRACE_V2.md. Il pilot v1 del 21/07 e' chiuso; v2 riparte con
# stato Redis versionato. Dal nuovo avvio non cambiare generazione, modello o seed
# gate finche' non si congelano 50 coppie.
DREAM_TRACE_PAIRED_ENABLED = True
DREAM_TRACE_PAIRED_VERSION = "dream_trace_paired_v2"
# Risveglio lucido — fedeltà-di-premessa dei candidate rispetto alle memorie
# sorgente (source_memory_ids): il sogno ha detto la verità sulle proprie fonti?
# Il punteggio viene calcolato UNA volta per candidate e cacheato sul documento.
PREMISE_FIDELITY_ENABLED = True
PREMISE_FIDELITY_BUDGET = 5  # max valutazioni LLM per ciclo leggero (ammortizza il backfill)
# Qualita' del ponte: distingue una deduzione sostenuta dalle fonti da un'ipotesi
# utile ma incompleta o da una connessione forzata. Dal 26/07 le due misure sono
# un gate fail-closed: SUPPORTED puo' essere promosso; HYPOTHESIS diventa uno stato
# intermedio non iniettato nel RAG; FORCED/UNKNOWN/non misurato resta candidate.
BRIDGE_VALIDITY_ENABLED = True
BRIDGE_VALIDITY_BUDGET = 3
BRIDGE_VALIDITY_POLICY_VERSION = "bridge_promotion_gate_v2"
INSIGHT_PROMOTION_QUALITY_GATE_ENABLED = True
INSIGHT_PROMOTION_POLICY_VERSION = "fidelity_bridge_fail_closed_v1"
INSIGHT_TTL_DAYS = 30
INSIGHT_DEMOTE_DAYS = 14  # PROMOTED non richiamato entro X giorni → torna CANDIDATE

# Filtro del Risveglio (re-rank insight in retrieval) — il sogno resta libero
# e atemporale (Loop 2b), il filtro di rilevanza opera solo al recupero RAG.
# Un insight i cui due domini non sono apparsi nelle memorie operative degli
# ultimi N giorni riceve una penalty moltiplicativa sulla cosine distance:
# non viene soppresso, solo deprioritizzato. Emerge se il contesto lo richiama.
INSIGHT_ACTIVE_DAYS = 30          # finestra del "presente operativo"
INSIGHT_ARCHIVE_PENALTY = 1.5     # ×1.5 sulla cosine distance per insight archivio
INSIGHT_OVERSAMPLE_FACTOR = 3     # chiedi 3× a Redis per avere margine al re-rank
# Sorgenti che definiscono cosa Stefano sta davvero curando — passive/conversation
# escluse perché spugne ambient: ogni nome di passaggio fa entrare un dominio negli
# attivi, neutralizzando il filtro (vedi dry-run: 30gg + tutti i source → 0% archivio).
# teach/user = scelta esplicita; reflection = sintesi consolidata (Loop 2a).
INSIGHT_ACTIVE_SOURCES = {"teach", "user", "reflection"}
# Memorie passive/reflection mai richiamate evaporano dopo 90 giorni.
# Memorie user/teach/obsidian_vault non scadono mai automaticamente.
MEMORY_TTL_PASSIVE_DAYS = 90
# Loop 2d: soglia recalled_count per estendere TTL senza invocare il giudice LLM.
MEMORY_KEEP_IF_RECALLED = 3
# Loop 2f — PARAURTI di richiamo (N3): un atomo fattuale con recalled_count >= soglia non
# viene auto-cancellato via contraddizione (tieni entrambi). Deterministico: nessun segnale
# economico separa l'assorbimento dannoso dal legittimo, la fidelity-probe LLM sbaglia ~metà
# delle volte (contro-caso test_plane_guard). Conservativo: protegge gli atomi molto
# richiamati, lascia consolidare i poco usati. Fail-safe = tieni entrambi.
LOOP2F_RECALL_GUARD = 5

# Budget slot del contesto RAG non-temporale (_build_context).
# Caso Eurostampi (11/06): con recency=5 / semantic=3 / cap=6 le 5 memorie più
# recenti (spesso output off-topic di un ciclo Dream appena girato) annegavano
# l'unico slot semantico rilevante → contesto ~72% off-topic su query tecniche.
# Ribilanciamento: poca recency per la continuità, il resto alla rilevanza.
# Misurato da diag_rag_context.py (ON↑/OFF↓). Le query temporali usano un percorso
# separato (prioritize_window) e un cap proprio (RAG_MEM_CAP_TEMPORAL): immutate.
RAG_RECENCY_LIMIT = 2        # memorie recenti "ambient" iniettate a prescindere dal tema
RAG_SEMANTIC_LIMIT = 5       # match semantici alla query corrente (la rilevanza)
RAG_MEM_CAP = 6              # slot totali mostrati su query non-temporale
RAG_MEM_CAP_TEMPORAL = 10    # slot su query con riferimento di tempo (diario più ampio)

# Memoria passiva dual-channel (validazione census 27/07/2026: GO).
# off       = retrieval storico invariato;
# shadow    = calcola anche gate+dual-channel ma risponde col retrieval storico;
# on        = dual-channel validato, turni originali aggiunti in coda;
# selective = append sicuro, salvo prepend dei soli turni originali che superano
#             il gate di rilevanza incrementale (sperimentazione live locale).
# Il default resta off per un rollout reversibile: l'archivio durevole dei turni
# viene comunque popolato, preparando evidenza idratabile per l'attivazione.
RAG_DUAL_CHANNEL_MODE = os.environ.get(
    "EURI_RAG_DUAL_CHANNEL_MODE", "off"
).strip().lower()
if RAG_DUAL_CHANNEL_MODE not in {"off", "shadow", "on", "selective"}:
    RAG_DUAL_CHANNEL_MODE = "off"
RAG_DUAL_SELECTIVE_MIN_QUERY_SOURCE = float(
    os.environ.get("EURI_RAG_DUAL_SELECTIVE_MIN_QUERY_SOURCE", "0.92")
)
RAG_DUAL_SELECTIVE_MIN_MARGIN = float(
    os.environ.get("EURI_RAG_DUAL_SELECTIVE_MIN_MARGIN", "-0.01")
)
RAG_DUAL_SELECTIVE_MAX_REDUNDANCY = float(
    os.environ.get("EURI_RAG_DUAL_SELECTIVE_MAX_REDUNDANCY", "0.985")
)
# Il test RAG+thinking vs dual+thinking del 28/07/2026 ha misurato un delta
# F1 +0,0519 senza perdita di prudenza avversariale. Il thinking resta però
# costoso: si attiva soltanto quando il gate selettivo ha promosso almeno un
# turno originale, mai sulla semplice presenza di una memoria sintetica.
RAG_DUAL_SELECTIVE_THINKING = (
    os.environ.get("EURI_RAG_DUAL_SELECTIVE_THINKING", "1") == "1"
)
RAG_DUAL_THINKING_NUM_PREDICT = int(
    os.environ.get("EURI_RAG_DUAL_THINKING_NUM_PREDICT", "2000")
)
# Finestra minima prima che un turno verbatim NON referenziato possa anche solo
# essere proposto come orfano. La manutenzione giornaliera esegue e persiste
# l'audit, ma nessuna cancellazione automatica usa questo valore.
VERBATIM_UNREFERENCED_GRACE_DAYS = int(
    os.environ.get("EURI_VERBATIM_UNREFERENCED_GRACE_DAYS", "180")
)
# La lineage RAG esiste già in shadow. La sua utilità viene riesaminata dopo
# almeno due settimane e 100 risposte; anche con uso scarso, dopo 30 giorni
# nasce comunque un promemoria persistente. La revisione non ritocca da sola
# i parametri applicativi definiti subito sotto.
MEMORY_UTILITY_REVIEW_MIN_DAYS = int(
    os.environ.get("EURI_MEMORY_UTILITY_REVIEW_MIN_DAYS", "14")
)
MEMORY_UTILITY_REVIEW_MIN_RESPONDED_TURNS = int(
    os.environ.get("EURI_MEMORY_UTILITY_REVIEW_MIN_RESPONDED_TURNS", "100")
)
MEMORY_UTILITY_REVIEW_MAX_DAYS = int(
    os.environ.get("EURI_MEMORY_UTILITY_REVIEW_MAX_DAYS", "30")
)
# Applicazione immediata ma confinata: l'uso lessicale sostenuto riordina
# soltanto l'attenzione dei candidati Loop 2e già eleggibili. Non apre il gate,
# non promuove insight e non estende TTL. Cap e peso limitano il popularity bias.
MEMORY_ATTENTION_SUPPORTED_USE_WEIGHT = float(
    os.environ.get("EURI_MEMORY_ATTENTION_SUPPORTED_USE_WEIGHT", "2.0")
)
MEMORY_ATTENTION_SUPPORTED_USE_CAP = int(
    os.environ.get("EURI_MEMORY_ATTENTION_SUPPORTED_USE_CAP", "5")
)

# Episodic Compression (Layer 0 — memoria di sessione)
# Ogni EPISODE_COMPRESSION_THRESHOLD messaggi, i più vecchi vengono compressi in un episodio.
# Gli episodi sopravvivono EPISODE_TTL_DAYS giorni in Redis e vengono iniettati come contesto.
EPISODE_COMPRESSION_THRESHOLD = 30   # messaggi in history prima di comprimere
EPISODE_COMPRESSION_CHUNK = 20       # messaggi da comprimere in un blocco
EPISODE_TTL_DAYS = 7
EPISODE_MAX_INJECT = 3               # max episodi iniettati nel contesto Ollama

# Obsidian Integration (Phase 3)
OBSIDIAN_SYNC_ENABLED = os.environ.get("EURI_OBSIDIAN_SYNC_ENABLED", "1") == "1"
OBSIDIAN_VAULT_PATH = os.environ.get(
    "EURI_OBSIDIAN_VAULT_PATH",
    "/home/fio/EuriVault",
)

# Euri Pulse (bus afferente)
# I sensi esistenti emettono eventi tipizzati su euri:pulse. Il controller
# proattivo sotto consuma solo un sottoinsieme stretto e idratato (oggi:
# insight/promoted), lasciando il Pulse come ground truth osservabile.
PULSE_ENABLED = True

# Proiezione cognitiva Pulse v2 — consumer durevole e puramente osservazionale.
# Filtra event_class=cognitive in euri:cognitive:events; non crea memorie, non
# promuove insight e non autorizza azioni. Il replay include gli eventi non letti
# durante un arresto e quelli consegnati ma non ACKati prima di un crash.
COGNITIVE_PROJECTOR_ENABLED = True
COGNITIVE_PROJECTOR_BLOCK_MS = 2000
COGNITIVE_PROJECTOR_BATCH_SIZE = 100

# Interocezione hardware — recettore locale osservativo.
# Campiona lo stato fisico senza LLM; pubblica l'ultimo snapshot e soltanto le
# transizioni di stato su Redis/Pulse. In questa fase nessun consumer esegue
# azioni protettive: prima raccogliamo una baseline reale della workstation.
HARDWARE_INTEROCEPTION_ENABLED = True
HARDWARE_INTEROCEPTION_INTERVAL_S = 3.0
HARDWARE_INTEROCEPTION_LATEST_TTL_S = 30
HARDWARE_INTEROCEPTION_BASELINE_INTERVAL_S = 60
HARDWARE_INTEROCEPTION_REVIEW_AFTER_S = 72 * 3600
HARDWARE_INTEROCEPTION_MIN_COVERAGE = 0.70
HARDWARE_INTEROCEPTION_EVENT_COOLDOWN_S = 5 * 60
HARDWARE_INTEROCEPTION_WARNING_SAMPLES = 3
HARDWARE_INTEROCEPTION_RECOVERY_SAMPLES = 3
HARDWARE_RAM_WARNING_PCT = 85.0
HARDWARE_RAM_CRITICAL_PCT = 95.0
HARDWARE_CPU_TEMP_WARNING_C = 90.0
HARDWARE_CPU_TEMP_CRITICAL_C = 98.0
HARDWARE_GPU_TEMP_WARNING_C = 82.0
HARDWARE_GPU_TEMP_CRITICAL_C = 90.0
# Baseline 17-20/07/2026: p95=95.818%, max=97.106% durante carico sano.
# L'allocazione dei modelli residenti non e' dolore; 98% segnala soltanto margine
# quasi esaurito e, da sola, non genera mai CRITICAL ne' un riflesso protettivo.
HARDWARE_VRAM_WARNING_PCT = 98.0

# Initiative controller — prima "scintilla" proattiva.
# Il daemon ascolta euri:pulse da `$` (solo eventi nuovi dal boot), rilegge il
# JSON reale collegato all'evento, valuta tensione e chiede al modello se vale
# una domanda. La formulazione è sempre prompt-based; le costanti qui sono solo
# guardrail anti-spam/sicurezza.
INITIATIVE_ENABLED = True
INITIATIVE_SHADOW_ONLY = False
INITIATIVE_MIN_TENSION = 0.25
INITIATIVE_IDLE_SECONDS = 90
INITIATIVE_COOLDOWN_S = 3 * 3600
CONVERSATION_LEASE_SECONDS = 45
CONVERSATION_FOCUS_SECONDS = 5 * 60
CONVERSATION_FOCUS_MAX_TURNS = 4
AUDIT_MEMORY_MAX_CANDIDATES = 40
AUDIT_MEMORY_BATCH_SIZE = 10
# Oltre questa pausa la history resta disponibile, ma viene marcata come un nuovo
# segmento: il modello puo' riaprire il filo senza fingere che fosse "poco fa".
TEMPORAL_EPISODE_GAP_SECONDS = 30 * 60
# Un'iniziativa che ESTENDE davvero il focus può entrare al confine di turno,
# ma non immediatamente né ripetutamente. RELATED/UNRELATED restano in coda.
INITIATIVE_CONTEXTUAL_MIN_IDLE_S = 8
INITIATIVE_CONTEXTUAL_COOLDOWN_S = 3 * 60
INITIATIVE_PULSE_BLOCK_MS = 5000
INITIATIVE_PENDING_MIN_AGE_S = 5  # stabilizza memory/saved prima di idratare (post-flag passive)

# FaceAuth — riconoscimento facciale locale (sorella visiva di SpeakerAuth).
# Il VisualGate distingue due segnali che prima collassavano in uno:
#   "qualcuno è presente"  → basta per ASCOLTARE (SpeakerAuth protegge i comandi)
#   "il proprietario è presente" → serve per PARLARE PER PRIMI (initiative, reminder, saluto)
# Il laboratorio è usato di notte dai capoturno: una faccia qualunque non deve
# far parlare Euri. Detection YuNet + embedding SFace (OpenCV, tutto locale);
# i faceprint sono dati biometrici: restano su disco locale, mai i frame.
FACE_AUTH_ENABLED = True
FACE_AUTH_OWNER = OWNER_ACTOR_ID       # identità che abilita l'efferente
FACE_AUTH_THRESHOLD = 0.363           # cosine SFace (soglia canonica OpenCV)
# None = scoperta automatica dei nodi /dev/video*. Impostare un indice (es. 1) o
# un path (es. "/dev/video1") solo per forzare una camera specifica.
VISUAL_GATE_CAMERA_DEVICE = None
# Un read V4L2 fallito può indicare uno stream USB rimasto aperto ma non più
# funzionante. Il gate passa subito in fail-open, rilascia la cattura e ripete la
# discovery: così un replug che rinumera /dev/videoN viene recuperato da solo.
VISUAL_GATE_READ_FAILURES_BEFORE_RECONNECT = 1
VISUAL_GATE_RECONNECT_S = 3.0
VISUAL_GATE_RECONNECT_MAX_S = 30.0
FACE_DETECT_MODEL = str(Path.home() / "euri" / "models" / "face_detection_yunet_2023mar.onnx")
FACE_RECOG_MODEL = str(Path.home() / "euri" / "models" / "face_recognition_sface_2021dec.onnx")
FACEPRINT_DIR = str(Path.home() / "euri" / "models" / "faceprints")
FACE_ENROLLMENT_REQUEST_KEY = "euri:face_enrollment:request"
FACE_ENROLLMENT_STATUS_PREFIX = "euri:face_enrollment:status:"
FACE_ENROLLMENT_TTL_S = 300
# Snapshot operativo effimero Voice Daemon -> Silent Chat. Contiene soltanto
# presenza/identita' dichiarative, mai frame, embedding o similarity biometrica.
VISUAL_PRESENCE_STATE_KEY = "euri:visual_gate:state"
VISUAL_PRESENCE_STATE_TTL_S = 8
VISUAL_PRESENCE_REFRESH_S = 1.0

# Percezione sociale visiva - Fase 0 osservativa. MediaPipe legge dagli stessi
# frame del VisualGate e produce solo segnali descrittivi stabilizzati. Nessun LLM,
# nessuna memoria e nessun effetto su tono/Initiative. Il modello resta locale.
SOCIAL_PERCEPTION_ENABLED = True
SOCIAL_PERCEPTION_MODEL = str(MODELS_DIR / "face_landmarker.task")
SOCIAL_PERCEPTION_PROFILE_DIR = str(MODELS_DIR / "social_profiles")
SOCIAL_PERCEPTION_FPS = 2.0
SOCIAL_PERCEPTION_REFRESH_S = 2.0
SOCIAL_PERCEPTION_CALIBRATION_SAMPLES = 12
SOCIAL_PERCEPTION_WINDOW_SAMPLES = 6
SOCIAL_PERCEPTION_STABILITY_SAMPLES = 4
SOCIAL_PERCEPTION_IDENTITY_MAX_AGE_S = 8
SOCIAL_PERCEPTION_LATEST_TTL_S = 30
SOCIAL_PERCEPTION_BASELINE_INTERVAL_S = 60
# Preparato ma spento: abilitarlo cambierebbe la versione del Cognitive Present
# e potrebbe quindi influire indirettamente sulle decisioni asincrone.
SOCIAL_PERCEPTION_PRESENT_ENABLED = False
# Futuro interprete occasionale Gemma4 Vision in idle. Non implementato in Fase 0.
SOCIAL_PERCEPTION_MULTIMODAL_ENABLED = False

# Propagazione di provenienza (invariante A della primitiva cognitiva).
# Un nodo derivato (consolidated_from) la cui fondamenta è caduta — genitori
# superseded/spariti/da-verificare — viene tenuto SOSPETTO: provenance_stale (down-rank
# nel retrieval) + requires_verification (Euri si copre). Fail-safe: segnala, non cancella.
PROVENANCE_PROPAGATION_ENABLED = True

# Loop 2i — Ipotesi trasversali da episodi ripetuti.
# Cerca pattern causa_sospetta→effetto ricorrenti in più memorie operative e genera
# un insight promosso ma marcato `requires_verification`: non è una regola del mondo,
# è una domanda buona da portare a Stefano.
CROSS_EPISODE_HYPOTHESIS_ENABLED = True
CROSS_EPISODE_MIN_CASES = 2
CROSS_EPISODE_MAX_MEMORIES = 24
CROSS_EPISODE_MIN_INTERVAL_S = 12 * 3600

# CodeRunner — Data Orchestrator (Phase 4)
# Genera ed esegue codice Python per manipolare file locali.
CODE_RUNNER_ENABLED = True
CODE_RUNNER_INPUT_DIR = str(Path.home() / "Scrivania" / "dati_per_Euri")
CODE_RUNNER_OUTPUT_DIR = str(Path.home() / "Scrivania" / "scambio_dati")
CODE_RUNNER_SANDBOX_DIR = str(Path(__file__).parent / "sandbox")
CODE_RUNNER_TIMEOUT = 30           # secondi max per esecuzione script
# Confinamento OS del subprocess via bubblewrap (difesa runtime oltre lo scanner AST):
# namespace mount read-only tranne sandbox+output, /tmp isolato, $HOME/etc assenti.
# Kill-switch: a False si degrada al lancio diretto + scanner (comportamento storico).
CODE_RUNNER_BWRAP_ENABLED = True
CODE_RUNNER_MAX_OUTPUT_BYTES = 10240  # max stdout catturato
SILENT_CHAT_UPLOAD_TTL_SECONDS = 24 * 3600  # upload chat effimeri: cleanup dopo 24h
SILENT_CHAT_UPLOAD_MAX_MB = 200
# Timeout dell'intero handler run_code = pre-extract Vision + code-gen + esecuzione.
# Settato V2.18.2 dopo osservazione 28/05 ore 16:29: pre-extract di 5 file
# (con 3 chiamate Vision Gemma 4) + code-gen Gemma + execution = ~57s.
# Margine ampio per file pesanti / Dream Engine in concorrenza.
CODE_RUNNER_TOOL_TIMEOUT = 180     # secondi max per l'intero ciclo CodeRunner

# Tool VectorSet — Layer 2 intent routing semantico via Redis 8.8 VectorSet (V2.18)
# Sostituisce l'LLM classifier (~800ms) con KNN nativo (~5ms) per query non ambigue.
# Kill switch: portare a False disattiva il Fast Path, il sistema degrada al
# comportamento pre-V2.18 (Layer 2 LLM puro).
#
# DISABILITATO 28/05/2026 dopo prima esecuzione in produzione.
# Due problemi strutturali emersi sul vero:
#   1) e5-large su CPU = ~600ms per encoding query. Latenza guadagnata vs
#      LLM (~700ms) è di soli 100ms. "Scatto felino" smentito.
#   2) e5-large produce score appiattiti (0.88-0.92) su query LUNGHE
#      conversazionali — chat catch-all finisce in ULTIMA posizione (verificato
#      sul caso reale "Il mercato ha alti e bassi... Il Covid non ha aiutato"
#      → SAVE_MEMORY 0.898 invece di CHAT). Test sintetico con frasi brevi
#      non aveva rivelato il problema; in produzione su 6 turni CHAT
#      consecutivi nessun match Fast Path, sprecati 600ms embedding ogni volta.
# Codice e modulo restano (core/tool_registry.py + test_tool_vectorset.py)
# come fondamento per V2.18.1 / V2.19: vie suggerite per ripartire (Euri stessa
# le ha intuite, sessione 13:44 del 28/05):
#   (a) Ricerca ibrida FT.SEARCH keyword + VectorSet semantico
#   (b) Re-ranking 2-stage (top-N VectorSet → LLM piccolo per scegliere)
#   (c) Embedder dedicato all'intent o e5-large su GPU
TOOL_VECTORSET_ENABLED = False
TOOL_VECTORSET_THRESHOLD = 0.85

# Plausibility gate — ARCHIVIATO 08/06/2026 (kill-switch off, codice in repo).
# Negative result: 1 vero positivo / 3 falsi positivi su gemme di dominio vere, anche col
# contesto operativo attivo. Vedi changelog V2.19 e [[project_euri_plausibility_gate]].
PLAUSIBILITY_GATE_ENABLED = False

# Workflow Planner — strato sottile sopra i tool esistenti che trasforma una
# richiesta operativa naturale e COMPOSTA ("leggi il documento, riassumilo e
# preparami una bozza di mail, non inviarla") in un piano ordinato di poche
# capability e lo esegue incatenando gli output. Non aggiunge tool nuovi.
# Fail-open: se il planner è incerto, si torna al dispatch attuale.
WORKFLOW_PLANNER_ENABLED = True
WORKFLOW_REVIEW_DIR = str(Path.home() / "Documents" / "Euri" / "Revisione")
