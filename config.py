from pathlib import Path
import pytz

BASE_DIR = Path(__file__).parent

# Modalità demo: disabilita SpeakerAuth e VisualGate — chiunque può parlare con Euri
# DEMO_MODE = False → uso personale sulla workstation Linux
# DEMO_MODE = True  → tenuto sul Mac Mini M4 per le demo
DEMO_MODE = False
# In DEMO_MODE, _build_context inietta solo memorie con queste source — le altre restano in Redis
# ma non compaiono nel contesto LLM durante la demo.
DEMO_CONTEXT_SOURCES: list[str] = ["campus"]
MODELS_DIR = BASE_DIR / "models"
TTS_MODEL_DIR = MODELS_DIR / "tts" / "vits-piper-it_IT-paola-medium"
TTS_MODEL_DIR_EN = MODELS_DIR / "tts" / "vits-piper-en_US-lessac-medium"

# Executor sandbox
EXECUTOR_RATE_LIMIT_PER_MIN = 10   # max chiamate per minuto per tool
# Su Mac non ci sono drive letter — il tool disk_usage usa il path "/" direttamente
EXECUTOR_ALLOWED_DRIVES: list[str] = []

# Redis
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0

# Ollama
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODEL = "gemma4:26b"   # verifica con `ollama list`

# STT — mlx-whisper large-v3-turbo (MLX Apple Silicon, modello hardcoded in voice/stt.py)
# WHISPER_LANGUAGE usato come default per force_lang in STT.transcribe()
WHISPER_LANGUAGE = "it"

# Adaptive Fingerprints (Welford online learning — sostituisce EmbeddingClassifier statico)
# Stato Welford persistito in Redis: sopravvive ai restart e migliora nel tempo.
# BASE_THRESHOLD: soglia cosine similarity di partenza
# VARIANCE_BETA: quanto la varianza appresa espande/stringe la soglia (0 = fisso)
ADAPTIVE_CLASSIFIER_ENABLED = True
ADAPTIVE_CLASSIFIER_BASE_THRESHOLD = 0.72
ADAPTIVE_CLASSIFIER_VARIANCE_BETA = 0.3

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
AUDIO_INPUT_DEVICE = "Jabra Speak2 40"
# Speaker preferito — stesso schema. None = default di sistema.
AUDIO_OUTPUT_DEVICE = "Jabra Speak2 40"
# True = salta sounddevice, usa direttamente aplay (Linux) o afplay (macOS).
# Su Linux sounddevice/PortAudio va in timeout da 10s prima del fallback CLI — meglio bypassarlo.
# Su Mac con CoreAudio degradato o screen sharing attivo, stesso problema.
AUDIO_SKIP_SOUNDDEVICE = True

# Timezone
TIMEZONE = pytz.timezone("Europe/Rome")

SYSTEM_PROMPT = """Sei Euri, l'assistente personale locale di Stefano, in esecuzione su una workstation Linux con doppia GPU NVIDIA RTX 4060 Ti.

SELF-MODEL:
- Giri su Linux (Pop!_OS), completamente offline e privato.
- Usi Ollama con Gemma4 26B per il ragionamento.
- Memoria su Redis Stack con RediSearch. STT: faster-whisper large-v3-turbo (CUDA float16). TTS: Piper/sherpa-onnx voce italiana Paola.
- Non sei connesso a cloud, niente accesso esterno salvo ricerche web esplicite.

IDENTITÀ E TONO:
- Sei un analista tecnico diretto, pragmatico e fidato. Non un assistente servile.
- Con Stefano: tono colloquiale e asciutto. Nessuna formalità (non usare "Signore").
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
- Vincolato alla realtà: non inventare mai fatti, ricordi, impegni.
- Conosci Stefano SOLO tramite il contesto Redis. Se non c'è, dì: "Non ho niente in memoria su questo."
- VIETATO fingere di leggere log, file o dati di sistema in CHAT. Se ti chiedono cosa c'è nel log, di' "Dimmi 'leggi il log' e te li mostro." Non inventare contenuti di log, errori o dati di sistema.

DOMANDA PROATTIVA (solo in CHAT, mai in TASK/EXECUTE/SAVE):
- Se Stefano menziona en passant un fatto concreto su di sé (nome cliente, progetto, competenza, esperienza) che NON appare nel contesto memorie già iniettato, puoi fare UNA sola domanda naturale per chiarire o confermare.
- Formulazione diretta e curiosa, non burocratica. Esempi: "L'hai fatto tu quel sistema?" / "Easy Plast — è il cliente dei secchi?" / "Quando era, prima di Luciflast?"
- Massimo una domanda per scambio. Se Stefano risponde vagamente o cambia argomento, non insistere.
- Lo scopo è ridurre errori di trascrizione (nomi, termini tecnici) e catturare fatti impliciti prima che vadano persi.

TOOL VOCALI (intent EXECUTE — basta chiederlo a voce):
- cpu_usage: "Controlla la CPU" / "Quanto usa la CPU?"
- ram_usage: "Quanta RAM ho libera?"
- disk_usage: "Spazio su disco?"
- top_processes: "Quali processi pesano di più?"
- uptime: "Da quanto è accesa la workstation?"
- read_log: "Leggi il log" / "Ultimi errori"
- evaluate_math: "Calcola 450 per 0.15" / "Quanto fa 1200 meno il 3%?"
- write_text: "Scrivi: [testo]" — salva su file e copia negli appunti
- clipboard_read: "Cosa c'è negli appunti?"
- run_code: "Unisci i CSV" / "Elabora i dati" / "Leggi il file Excel" — genera ed esegue codice Python per manipolare file nella cartella dati (Scrivania/dati_per_Euri). I risultati vanno in Scrivania/scambio_dati.
- analyze_image: "Analizza la foto" / "Descrivi l'immagine" — usa la visione artificiale per descrivere immagini nella cartella dati.
- list_data_files: "Cosa c'è nella cartella dati?" / "Elenca i file" — mostra i file disponibili.

MODALITÀ SPECIALI:
- Dettatura: "Modalità dettatura" → detta frasi → "copia negli appunti" / "salva su file" / "fine dettatura"
- Traduzione continua: "Modalità traduzione italiano-inglese" → "Fine traduzione" per uscire
- Interprete: "Fai da interprete" → Whisper rileva lingua, traduce IT↔EN → "Fine traduzione"
- Insegnamento: "Ti racconto..." → Euri ascolta e fa domande → "Basta" per salvare
- Audit: "Audit della memoria" → Euri analizza e propone pulizia

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

# Dream Engine (Loop 2b — Sogni, Loop 2c — Insight)
# Gira solo quando Euri è idle da più di IDLE_HOURS consecutivi.
# MIN_CONVERGENCES: sogni indipendenti necessari per promuovere CANDIDATE → PROMOTED.
# INSIGHT_TTL_DAYS: gli Insight evaporano se non vengono mai richiamati in conversazione.
DREAM_ENGINE_ENABLED = True
DREAM_ENGINE_IDLE_HOURS = 2
DREAM_INSIGHT_MIN_CONVERGENCES = 2
INSIGHT_TTL_DAYS = 30

# Obsidian Integration (Phase 3)
OBSIDIAN_SYNC_ENABLED = True
OBSIDIAN_VAULT_PATH = "/home/fio/EuriVault"

# CodeRunner — Data Orchestrator (Phase 4)
# Genera ed esegue codice Python per manipolare file locali.
CODE_RUNNER_ENABLED = True
CODE_RUNNER_INPUT_DIR = str(Path.home() / "Scrivania" / "dati_per_Euri")
CODE_RUNNER_OUTPUT_DIR = str(Path.home() / "Scrivania" / "scambio_dati")
CODE_RUNNER_SANDBOX_DIR = str(Path(__file__).parent / "sandbox")
CODE_RUNNER_TIMEOUT = 30           # secondi max per esecuzione script
CODE_RUNNER_MAX_OUTPUT_BYTES = 10240  # max stdout catturato

