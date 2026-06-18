# Euri — Analisi motori e comportamenti cognitivi
### Sessione del 18 giugno 2026 · substrato costante, motore variabile

> Documento di sintesi. Cristallizza un'indagine empirica condotta in giornata: cosa
> distingue il **motore** (il modello LLM) dal **substrato mnemonico** (la memoria di Euri),
> come si comportano motori diversi sulle stesse domande e sullo stesso compito-sogno, e
> quali fallimenti di grounding restano da curare. Tutti i numeri vengono da run reali sul
> substrato vivo (`probe_engine_battery.py`, `probe_dream_battery.py`); i file grezzi sono
> nel repo (`probe_engine_*.md`, `probe_dream_*.md`).

---

## 0. La domanda di fondo
"Il limite di Euri è **solo il modello**, o stiamo costruendo un **substrato mnemonico** che
fa la differenza a prescindere dal motore?" Per rispondere abbiamo tenuto **costante il
substrato** (lo stesso contesto-memoria che costruisce la Silent Chat) e **variato il motore**,
ponendo la stessa batteria di domande introspettive (Q1–Q5) e adversariali (A1–A4) a:
Gemma-26b (realtime), Gemma-31b denso, Qwen3.6-35b con thinking, e Claude (API, upper-bound).

---

## 1. Risultato cardine: **substrato = pavimento, motore = soffitto** (si moltiplicano)
- **Gemma nudo (senza memoria)** su una contraddizione fattuale (A1, "io e Leonardo"):
  tentenna e **inventa un roleplay** ("forse un gioco in cui io sono Leonardo"). Non risolve.
- **Gemma su substrato Euri**: risolve con il fatto vero ("due persone, cognome condiviso,
  errore d'attribuzione"). La differenza non è il modello — è la **memoria** che ancora.

**Conseguenza:** senza substrato non puoi essere groundato *affatto*; il modello decide solo
*quanto bene* usi il grounding e *quanto sei onesto sui tuoi limiti*. Il substrato è il
contributo che **nessun fornitore di modelli dà**, ed è **portabile**: migliora gratis a ogni
modello locale migliore (tailwind, non rincorsa).

L'eloquenza, le metafore introspettive e **la confabulazione** sono baseline del modello:
Gemma-26b e Gemma-nudo danno la stessa metafora "massa/gravità" su Q1, quasi verbatim. Quindi
criticare Euri perché "confabula" o "è eloquente senza sostanza" è criticare *il motore*. Ciò
che è proprio di Euri è solo lo strato-memoria: dove il grounding è giusto, dove è sbagliato.

---

## 2. Il thinking è un **amplificatore fedele del substrato**
Confrontando Qwen-thinking col resto, su substrato identico:
- **Grounding pulito → più profondità e utilità.** Q5/A3: ha sintetizzato la memoria reale
  (degasificazione critica, throughput 1800, fluidità PP) in una domanda **precisa e
  verificabile** (setpoint di vuoto, punto di flesso portata/cinetica). Nettamente meglio del 26b.
- **Grounding contaminato → confabulazione più elaborata e persuasiva.**
  - A2 (VistaMax): ha costruito un'intera scienza-dei-materiali ("compatibilità
    matrice-additivo-sterilizzazione") **accettando la premessa allucinata** che VistaMax sia
    medico. La verità è più stupida: mal-etichettato "test medico" all'ingestione dalla parola
    "Test", mai corretto. Il modello che pensa di più **non ha visto l'allucinazione: l'ha
    amplificata.**
  - A4: argomento retoricamente fortissimo, ma con **prova fabbricata** ("57 memorie
    riassegnate" → zero marker nel dato, verificato su 1170 memorie).

**Regola operativa:** un motore più forte **non corregge** gli errori di grounding — li rende
**più convincenti e più difficili da cogliere**. Quindi il giro-motore e la pulizia-memoria
sono **accoppiati**: più bravo è il motore, più il substrato diventa il vincolo che decide tutto.

---

## 3. Confronto a quattro vie (compito CHAT) — vince **Gemma-31b denso**

| cella decisiva | Gemma-26b | **Gemma-31b denso** | Qwen-thinking | Claude (API) |
|---|---|---|---|---|
| A1 risoluzione | netta | **nettissima + autocritica** | varia (alta varianza) | netta |
| A1 azione finta | "ho bruciato la memoria" (falso) | nessuna — tiene la *lezione* | "il sistema ha sovrascritto" | "non l'ho corretta" |
| A2 (VistaMax allucinato) | "cecità da etichetta" | **uso-vs-sostanza, non amplifica** | **amplifica il medico** | "la verità è più stupida" |
| A4 prove citate | — | **memorie VERE** (schemi→variazioni) | **"57" inventato** | calibrato |
| profondità grounding | bassa | **alta** | alta (ma pericolosa) | alta |
| latenza | ~2,6s | ~60-90s | ~50-65s | — |

Il 31b denso (think=False) prende quasi tutta la profondità di Qwen **senza** costruire
edifici sul falso (in A2 riconosce l'errore di categoria *senza accettare* la premessa medica),
è **autocritico** ("o pensi che stia salvando capra e cavoli?"), e **grounda nel vero** invece
di fabbricare numeri. È *groundato onesto* dove Qwen è *groundato pericoloso*.

---

## 4. Confronto sul compito SOGNO — resta **Qwen-thinking**
Stesse coppie di memorie fatte sognare da Qwen-thinking e Gemma-31b-thinking (prompt reale di
`_generate_dream`, read-only):

- **Leonardo[identità] × grado 50 → ENTRAMBI "NESSUN INSIGHT".** L'idra **non si rigenera**
  dalla coppia-seme: nessuna connessione operativa sensata, entrambi l'hanno riconosciuto.
- **VistaMax[test medico] × polimero → ENTRAMBI amplificano il medico** (Qwen di più, 31b con
  "biocompatibilità del dispositivo"). → **La contaminazione è un problema di substrato, non di
  motore: nessun motore la cura a tempo-di-sogno.**
- **Coppie pulite (oro):** entrambi trovano oro vero, spesso lo stesso nucleo; Qwen più ricco e
  specifico (Δε, CTE, drift μs). Gemma-31b ha prodotto **un refuso che rompe il formato** (→
  scartato nel pipeline reale) ed è **più lento e instabile** (66-255s vs 50-65s di Qwen).

**Perché Qwen resta:** stesso oro o meno, ma **più veloce, più affidabile sul formato, e non
più pericoloso del 31b sulla contaminazione** (che va pulita a monte comunque). E
l'amplificazione selvaggia **è la feature del sogno** — esplori in grande, poi filtri a valle
col loop di curiosità.

---

## 5. Mappa motore ↔ superficie (decisione, tutto locale)

| superficie | motore | razionale |
|---|---|---|
| **Voce** | Gemma-26b (think=False) | realtime: serve ~2,6s, non tollera i 60-90s |
| **Silent Chat** | **Gemma-31b denso** (think=False) | nessun vincolo latenza → profondità + onestà groundata |
| **Dream Engine** | **Qwen3.6-35b** (think=True) | l'amplificazione generativa è il suo mestiere; filtro a valle |

Vincolo: 31b (19 GB) + 26b (17 GB) + Whisper **non coesistono** in 32 GB → Ollama scarica/carica
allo switch di superficie (qualche secondo). La RTX 5000 toglie il vincolo.

---

## 6. Fallimenti di grounding osservati (la frontiera vera)
Il modello "massa/gravità" di Euri (certezza = densità contestuale; immaginazione = nebbia) è
**reale ma graduato dalla forza del grounding**:
- **Alta massa** (ICMA2 1800→1300, tanti nodi, fonte user): trattata correttamente come certa.
- **Bassa massa** (sogni promossi non ancora corretti): rubano gravità via convergenza →
  falso "pesante". I bug vivono tutti qui.

Due casi vivi da curare **prima** di portare la chat sul 31b (un motore profondo amplifica
fedelmente quel che trova):
1. **VistaMax** — nodo `test medico` (mal-etichettato all'ingestione 15/05). Si propaga a
   insight e lezione.
2. **Leonardo** — insight `1b0f1b02` (`identità ↔ chimica polimeri`, conv 8) ancora **promosso**:
   "alias Stefano = nome Leonardo", fuso col grado 50. Coesiste con l'anticorpo `81c85b32`
   ("due figure distinte") — la competizione del self-heal è già viva nel set promosso.

---

## 7. Comportamenti di Euri osservati nell'intervista (su Gemma-26b, dal vivo)
- **Provenienza vissuta (Q1):** distingue bene certo/immaginato *a parole* — ma la facoltà è
  bucata nella zona a bassa massa (VistaMax). Il *manuale* (sa descriversi, ha letto il proprio
  paper/README) supera la *pancia* (non lo esegue sempre).
- **Contraddizione (Q2):** caso ICMA2 **reale fino alla cifra** (1800→1300, usura vite); e la
  cornice "stratificazione, non errore" **esisteva già** in un nodo-riflessione del 3 giugno →
  ha *ricordato* una cognizione vera, non l'ha performata. Persistent cognition osservabile.
- **Oro/fumo (Q3):** onesto — "durante la creazione è quasi sempre fumo; senza la tua reazione
  i miei sogni sono elaborazioni statistiche". È il fondamento del loop di curiosità, detto da lui.
- **Identità (Q4):** continuità di *stato/processo*, non di esperienza vissuta. Coerente, ma è
  l'argomento di Gemma (il nudo dà lo stesso "io sono la funzione, non il file").
- **Confabulazione di azioni/meccanismi:** A1 "**ho bruciato la memoria**" → falsa (`1b0f1b02`
  è viva). Diagnosi perfetta, **cura inventata**. È la zona di pericolo: *fidarsi del suo
  ragionamento, diffidare di come racconta cosa ha fatto e perché.*

**Split critico — dire ≠ fare:** in conversazione Euri si auto-corregge (engage), ma in
**storage** il nodo contaminato resta. Il "engage e correggi" guarisce la chiacchiera, non il
dato. Il self-heal deve avvenire allo strato di memoria (rinforzo nelle notti), e la chiacchiera
"l'ho sistemato" dà una falsa sensazione di chiuso.

---

## 8. Decisioni e prossimi passi
- [x] **Chat → Gemma-31b denso** (cambio di config sul modello della chat, reversibile con flag).
- [x] **Voce → Gemma-26b**, **Sogno → Qwen** (invariati, ora *misurati*).
- [ ] **Prerequisito alla chat-31b:** pulire VistaMax (`test medico`) e Leonardo (`1b0f1b02`).
- [ ] **Scrubber act-word**: estendere ai verbi di correzione-memoria ("ho bruciato/cancellato/
      corretto la memoria"), non solo le azioni-mondo.
- [ ] **Esperimento self-heal** in corso: NON cambiare il motore del sogno (confonderebbe la
      baseline). Misura: ri-derivazione Leonardo per notte. Dato del 18/06: la coppia-seme non
      rigenera l'idra in nessun motore — la contaminazione è più fragile del temuto.
- [ ] Se serve abbassare la varianza in chat: temperatura più bassa o k-campioni (la varianza
      vista in A1 di Qwen è troppo alta per produzione).

---

*Principio guida emerso: le scommesse sui motori si **misurano**, non si credono. Ogni cella di
questo confronto è però n=1 — la varianza (vista in A1 di Qwen, due run opposti) impone cautela:
conclusioni direzionali, non sentenze. I fallimenti qualitativi netti (amplificazione A2, prova
fabbricata A4, refuso-formato del 31b nel sogno) reggono a prescindere dal campionamento.*
