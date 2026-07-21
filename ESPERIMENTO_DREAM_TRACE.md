# Esperimento dream_trace: continuità di ragionamento tra cicli 2b

**Pre-registrazione — 13/07/2026.** I criteri qui sotto sono fissati PRIMA della raccolta
e non si toccano a posteriori. Qualunque esito va documentato in coda a questo file,
incluso il negativo.

## Ipotesi

L'assenza di traiettoria di ragionamento tra cicli 2b consecutivi contribuisce alla quota
di insight "ovvi ma elaborati" (audit di maggio: 27% non-ovvi / 67% ovvi / 6% incerti su
30 promozioni). Persistendo tra i cicli un residuo compatto dell'esplorazione — a livello
di **strategia** ("che tipo di ponte ho provato e perché era debole"), non di coppia di
domini — la quota non-ovvio dovrebbe salire.

**Perché a livello di strategia:** i domini attivi sono ~145 e il pairing di `_generate_dream`
è random puro → due cicli consecutivi non rivisitano quasi mai la stessa coppia. Un residuo
"ho considerato X↔Y, scartato" sarebbe inerte per il ciclo che pesca A↔B; solo il livello dei
*tipi di connessione* trasferisce tra coppie. (Questa è la correzione principale rispetto alla
bozza web del 04/07, che era per-coppia.)

**Premessa onesta:** la continuità è UNA ipotesi tra le co-cause candidate del 67%
(pairing random; template a tre righe che domina lo spazio embedding — misurato
nell'analisi anisotropia; retrieval per similarità di superficie). Un esito nullo con
residuo-strategia funzionante distingue "ipotesi falsa" da "residuo inerte"; con residuo
malfunzionante non distingue nulla → il check del punto 4 va fatto prima di leggere i numeri.

## Cosa è stato costruito (13/07, dietro flag)

- `config.DREAM_TRACE_ENABLED` (default **False**) + `DREAM_TRACE_TTL_S` (48h).
- `_generate_dream`: a flag acceso legge `euri:dream_trace:latest` e lo inietta come
  sezione marcata «TRACCIA DEL CICLO PRECEDENTE … serve solo a NON ripercorrere»;
  cattura il CoT (message.thinking o blocco `<think>`) PRIMA dello strip.
- `_update_dream_trace`: distillazione sul modello del sogno già caldo
  (temperature 0.2, num_predict 400, **think=False** — il thinking consumerebbe il budget
  e tornerebbe vuoto, failure noto), max 5 righe a livello strategia, `SETEX` 48h.
  Gira ANCHE sui sogni scartati (il "perché era debole" è l'informazione utile).
  Una sola chiave sovrascritta, nessuno storico, mai nel retrieval/memorie/2c.
- Candidate nati a flag acceso portano `trace_injected: true/false` (col residuo o senza);
  il campo viaggia nella `euri:convergence:trace` → **niente log parallelo**: la trace
  esistente è già il registro per-candidate (contenuto, esito, convergenze, vicini).
- A flag spento: prompt bit-identico, una sola chiamata LLM, nessuna chiave scritta,
  nessun campo nuovo (verificato con test a mock: scenari off / on-1° ciclo / on-2° ciclo).

## Bracci e misura

- **Baseline**: i candidate già in `euri:convergence:trace` con `trace_injected` assente
  (raccolti dal 04/07 a flag spento; ~148 al 13/07). Contemporanei e omogenei per
  strumentazione — NON si riusa l'audit di maggio come baseline (V2.6→V2.20+, troppe
  variabili cambiate; resta come riferimento storico).
- **Trattamento**: candidate con `trace_injected == "1"`. I `"0"` (flag acceso ma residuo
  assente: primo ciclo o TTL scaduto) sono esclusi da entrambi i bracci.
- **Unità di misura primaria: il CANDIDATE, non la promozione.** Motivo misurato (analisi
  04–13/07): la promozione è convergenza-per-ripetizione su soglia anisotropa, quasi cieca
  al contenuto (giudice LLM contribuisce zero; ~6 promozioni/giorno costanti in idle;
  convergenza claim-level inesistente tra i vicini del gate) — e il residuo spinge
  esplicitamente a NON ripetere percorsi, quindi interagisce col tasso di ripetizione:
  misurare sui promossi confonderebbe effetto-generazione ed effetto-gate.
- **Audit CIECO**: `scripts/experiments/sample_dream_audit.py` campiona n per braccio,
  mescola, toglie le etichette; Stefano compila la rubrica di maggio (non-ovvio / ovvio /
  incerto) senza sapere il braccio; la chiave si apre a etichette finite.

## Criteri pre-registrati

Con p≈27% e n=60 per braccio, la deviazione standard della DIFFERENZA tra due quote
campionarie è ~8 punti percentuali. I criteri ne tengono conto (la soglia a 5pp della
bozza web era sotto il rumore: 0.6 σ):

1. n = **60 candidate per braccio** (a ~16 candidate/giorno ≈ 4–5 giorni di raccolta
   trattamento; la baseline c'è già).
2. Δ quota non-ovvio (trattamento − baseline) **≥ 15pp** → segnale (≈2σ), si prosegue
   su questa strada.
3. **5pp ≤ Δ < 15pp** → indicazione debole: secondo batch da 60 prima di qualunque
   conclusione (mai dichiarare segnale su questo range al primo batch).
4. **Δ < 5pp** → nullo. PRIMA di concludere "ipotesi falsa": leggere a mano 10 residui
   da `euri:dream_trace:latest` (log del daemon) e verificare che siano davvero a livello
   strategia e non boilerplate — un residuo malfunzionante produce un nullo che non
   dice nulla sull'ipotesi.
5. Quota "incerto" > **15%** in uno dei bracci → red flag di degradazione del formato:
   si ferma e si analizza prima di continuare.
6. Secondario (osservazionale, NON decisionale): tasso di promozione/giorno e quota
   non-ovvio tra i promossi nei due periodi — atteso confuso dall'interazione col gate,
   si documenta e basta.

## Vincoli (invariati dalla bozza)

- UN intervento: pairing random NON si tocca (seconda causa candidata, esperimento
  separato: vector search invertita per domini distanti); formato a tre righe NON si
  tocca (fix V2.6); nessuna modifica a 2a, 2c, 2e–2h, Loop 1, schema memorie.
- Offline-first, domain gating invariato.
- Migliorie emerse durante la raccolta: si annotano qui sotto, non si implementano.

## Procedura operativa

1. Riavvio daemon con flag spento → periodo di controllo implicito (nessun cambiamento).
2. Quando Stefano dà l'ok: `DREAM_TRACE_ENABLED = True` + riavvio.
3. Dopo il primo ciclo creativo: controllare nel log «Dream trace aggiornata» e leggere
   `redis-cli get euri:dream_trace:latest` — il residuo deve essere strategia, max 5 righe.
   Se è boilerplate o contenuti specifici → fermarsi e rifinire il prompt di distillazione
   PRIMA della raccolta (non conta come secondo intervento: il residuo È l'intervento).
4. Raccolta fino a ≥60 candidate con `trace_injected="1"` (query sulla trace).
5. `python scripts/experiments/sample_dream_audit.py` → audit cieco → unblinding → esito
   in coda a questo file.

## Esiti

**Raccolta congelata il 21/07/2026, prima del nuovo gate epistemico sui semi.**
La convergence trace contiene 160 candidate baseline e 76 trattamento grezzi; i due
trattamento precedenti al riavvio anti-eco del 13/07 17:35:58 sono esclusi come
pre-registrato, lasciando 74 candidate trattamento valide. Il campionatore applica ora
questo cutoff in codice e ha prodotto un audit cieco riproducibile da 60+60 item sotto
`audit_output/`, con chiave separata e ignorata da Git. `DREAM_TRACE_ENABLED` torna
`False` dal prossimo riavvio.

Il risultato resta **non letto**: Stefano deve compilare N/O/? senza aprire la chiave.
Dopo l'unblinding, riportare qui quote, delta e strati temporali/policy previsti sopra.

## DEVIAZIONE DOCUMENTATA (15/07/2026) — gate di promozione corretto

Checkpoint prima dell'intervento: **baseline 149 / trattamento 16 / ambiguo 2**.
Tre promozioni notturne hanno fornito una riproduzione live del bug già indicato nella
pre-registrazione: per ciascuna, due vicini full-text con distanza 0.12–0.14 sono stati
auto-contati come convergenze certe pur esprimendo claim operativi manifestamente diversi.
Il template a tre righe dominava quindi la distanza e trasformava una shortlist lessicale
in una decisione epistemica; i vicini falsi venivano inoltre assorbiti, cancellati e la loro
provenienza unita al promosso.

Per decisione esplicita di Stefano il bug concettuale è stato corretto durante la raccolta:

- policy `vector_auto_v1` fino al candidate trattamento 16;
- policy `claim_judge_v2` dal candidate trattamento 17;
- il vettore seleziona soltanto coppie con distanza <0.40;
- ogni coppia, anche a distanza zero, richiede un verdetto semantico `SAME`;
- `RELATED`, `DIFFERENT`, errore, risposta non parsabile o budget esaurito sono fail-closed;
- cache simmetrica content-addressed e budget per ciclo limitano il costo;
- la convergence trace registra versione policy, shortlist, conferme e rinvii.

Il giudice è rimasto in modalità thinking dopo confronto locale sullo stesso corpus di sei
coppie: thinking 6/6 corrette e parsabili, no-thinking 5/6 con un falso `SAME` proprio su
due meccanismi solo genericamente analoghi (validazione AST vs controllo qualità compound).
`num_predict=5000` lascia margine al ragionamento; cache e budget 6/ciclo confinano il costo.

Questa deviazione NON cambia direttamente l'unità primaria dell'esperimento: l'audit cieco
misura il contenuto di tutti i candidate, non promozione o convergenza. Può però produrre
effetti indiretti sul contesto futuro, perché cambia quali insight entrano nel retrieval.
L'analisi finale deve quindi riportare anche il risultato stratificato trattamento 1–16 vs
17–60 e non presentare la raccolta come runtime perfettamente invariato.

## DEVIAZIONE DOCUMENTATA (15/07/2026) — Cognitive Present attivato nel runtime

Durante la stessa raccolta e per decisione esplicita di Stefano è stato attivato nel daemon
il Cognitive Present (`266551a`). L'intervento nasce da un problema conversazionale reale:
Initiative poteva interrompere un turno ancora vivo con una domanda estranea e la risposta
successiva poteva essere attribuita alla reaction sbagliata. Il runtime ora mantiene un lease
dalla fine effettiva del TTS, un focus conversazionale breve, ammette durante quel focus solo
interventi che lo estendono e rivalida la decisione subito prima di parlare.

La generazione del Dream Engine, il residuo `dream_trace` e il formato dei candidate non sono
stati modificati. L'unità primaria e l'audit cieco sul contenuto dei candidate restano quindi
validi. Esiste però un secondo percorso indiretto: Cognitive Present cambia quali iniziative
arrivano all'utente e come le risposte diventano reaction/lezioni; queste memorie possono poi
entrare nel retrieval e nutrire sogni successivi. Di conseguenza l'attribuzione causale al
solo residuo è più debole di quanto previsto dalla pre-registrazione.

Nell'analisi finale vanno distinti almeno i seguenti regimi:

- trattamento precedente a `claim_judge_v2`;
- trattamento con `claim_judge_v2` ma precedente all'attivazione live di Cognitive Present;
- trattamento con entrambi gli interventi attivi.

Il confine del terzo regime va ricavato dal timestamp del riavvio del daemon e dalla trace,
non dall'orario del commit. Se un regime contiene pochi candidate, si riporta comunque come
strato descrittivo senza inferenze forti. Tasso di promozione, reazioni e lezioni restano
misure secondarie osservazionali; il risultato primario non va descritto come effetto puro
del solo `dream_trace`.

## Annotazioni per esperimenti futuri (NON in questo)

- Pairing: sostituire `random.choice` con ricerca del dominio semanticamente DISTANTE
  (il commento nel codice lo riconosce già).
- Template tre-righe: misurato che domina lo spazio embedding (μ coppie ~0.82) →
  candidato a de-boilerplate, ma è il fix V2.6, va trattato con rispetto.
- Gate di promozione: la convergenza-per-ripetizione non misura contenuto (analisi
  04–13/07). Corretto il 15/07 dopo riproduzione live; vedere deviazione documentata.

## NOTA DI RACCOLTA (13/07 sera) — eco a punto fisso, distillazione rifinita

I primi 2 residui reali (13:07 e ~16:40) hanno mostrato il failure mode previsto al
punto 3 della procedura, in forma aggravata: **stesse 3 etichette in entrambi (2 erano
gli esempi del prompt di distillazione), terza riga quasi-verbatim tra cicli su domini
diversi** ("errore di codifica…scalabilità" ricopiato su un sogno trading×QC dove non
c'entrava). Diagnosi: la traccia iniettata rientra dal CoT nel residuo → eco a punto
fisso, non esplorazione. Fix (in-perimetro, previsto dalla prereg): prompt di
distillazione senza esempi (venivano pappagallati), istruzione esplicita di IGNORARE
la [TRACCIA DEL CICLO PRECEDENTE] nel CoT, e "NIENTE DA SEGNALARE" → non si scrive
(meglio nessun residuo di un residuo finto). Attivo al prossimo restart del daemon.

**Regola di esclusione per l'audit:** i candidate `trace_injected="1"` generati PRIMA
del restart col fix (finestra 13/07 ~14:30 → restart) sono ESCLUSI dal braccio
trattamento (residuo degenere ≠ intervento testato). Filtro: created_at < ts restart.
