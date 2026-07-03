# Handoff Euri — 2026-06-27

## Stato repo al close

- Branch corrente: `main`.
- `main` e' allineato a `origin/main`.
- Nessuna modifica tracked pendente nel codice al momento del controllo.
- File untracked presenti e lasciati intatti: `CODEX.md`,
  `CODEX_GIADA_CONSOLIDATION.md`, `CODEX_RESUME_ROUTER_BENCH.md`,
  `probe_chat.py`.
- Ultimo commit visto: `94b8aa8 Classificatore pragmatico via Gemma (non regex) per il guard reazione`.

## Nuovi commit visti dopo il vecchio punto `dd1a937`

- `feat/workflow-planner` e merge su `main`: strato Workflow Planner sopra tool gia'
  esistenti; `SAVE_FOR_REVIEW` viene aggiunto dopo `DRAFT`.
- `fix/workflow-thread-continuity`: il risultato resta nel thread conversazionale.
- `feat/open-created-file`: comando per aprire l'ultimo file creato.
- `fix/pragmatics-broaden`: allargate euristiche pragmatiche dopo fallimenti live.
- `feat/pragmatic-classifier-gemma`: classificatore pragmatico via Gemma/non regex
  per il guard reazione.

## Stato cognitivo/funzionale da ricordare

- Pulse/Initiative sta funzionando: Euri porta insight in conversazione, Stefano
  risponde, il sistema salva una memoria-lezione e aggiorna lo stato epistemico
  dell'insight (`DA_VALUTARE` -> `requires_verification`).
- Caso live 26/06 mattina: insight su procedure/stabilita' parametri marcato
  correttamente da verificare dopo risposta prudente di Stefano.
- I loop stanno girando in idle diurno: light frequente, creative separato,
  maintenance separato. Dai log si vedono demotion di insight non validati,
  Loop 2e con gate same-subject e consolidamenti selettivi.
- Il Loop 2e appare molto piu' prudente di prima: molti seed/fammenti esclusi,
  poche consolidazioni, e le sintesi lette erano fedeli alle fonti.
- Il Loop 2i e' piu' disciplinato del Dream creativo: usa episodi diretti e non
  dovrebbe contare reflection/reaction/consolidati come casi indipendenti.

## Finding aperto da non perdere

Nel controllo del 26/06 era emerso che il Dream creativo puo' ancora partire da
semi interni/derivati se `_get_random_memory_from_domain()` pesca senza un gate
epistemico forte. Questo puo' produrre insight belli ma fragili.

Patch sperimentale discussa ma NON presente nel tree corrente:

- filtrare i semi del Dream creativo a fonti dirette (`user`, `teach`, `passive`,
  `episode`, `conversation`);
- escludere `reflection`, `reaction`, `loop2e`, confronti, `superseded_by`,
  `correction_pending`, audit/acefali;
- far ereditare `requires_verification` agli insight se le fonti sono fragili;
- far marcare anche la memoria-lezione di una reaction `DA_VALUTARE/PARZIALE`
  come `requires_verification`, non solo l'insight.

Questa patch va rivalutata contro il codice attuale prima di applicarla: il repo
ha nuovi commit su pragmatica/workflow e non conviene reintrodurre modifiche a
memoria.

## Prossima ripresa consigliata

1. Leggere `git log --oneline -8` e `git status --short --branch`.
2. Se si riparte dal Dream seed gate, prima controllare `_get_random_memory_from_domain`,
   `_generate_dream`, `_evaluate_insights`, `capture_reaction`.
3. Non committare i file scratch untracked a meno che Stefano lo chieda.
4. Se serve un commit, aggiornare CHANGELOG solo dopo una patch verificata.

---

# Handoff Euri — 2026-06-22

## Stato

Euri e' in una fase di osservazione/evoluzione dopo:

- Fase 1 afferente su Pulse.
- Primo efferente reminder presence-aware implementato da Claude.
- `commitment/intero` validato: Euri percepisce claim d'azione non coperti.
- Reminder Poseidon scattato correttamente grazie a todo manuale preesistente.
- VisualGate/camera ancora non affidabile: `/dev/video0` esiste ma OpenCV va in timeout; usare interazione recente come presenza primaria.

## Finding principale: emergenza operativa

Stefano ha insegnato a Euri come leggere documenti tecnici Lucy Plast:

- SDS = sicurezza, normativa, rischi, microparticelle/SPM, responsabilita' di filiera.
- Scheda tecnica = prestazioni, range, proprieta' fisiche/meccaniche.
- Range min/max = finestra documentale/operativa, non valore fisso.
- Codice materiale = famiglia/cornice, non formula immutabile.
- Cliente = sotto-specifica piu' stretta dentro il range.
- Non si creano codici nuovi per ogni piccola variazione di colore, carica o fluidita'.

Euri ha superato una mini-interrogazione:

- distingue SDS da scheda tecnica;
- interpreta correttamente i range;
- capisce stesso codice con paletti cliente diversi;
- respinge la trappola "ogni variazione richiede un codice nuovo";
- distingue dati letti dal documento e metodo interpretativo spiegato da Stefano.

Memorie rilevanti:

- `55aecfae`: SDS/informativa sicurezza R-PP, normativa microparticelle/SPM.
- `fcd5af4d`: scheda tecnica `03PPR044POST - GRANULO PP PSV80`, proprieta' e range.
- `7f46ffe7`: regola operativa core sui range e specializzazione cliente senza nuovi codici ufficiali.
- `a4c1d514`: metodo di analisi schede tecniche: range, cariche/additivi, limiti termici, vincoli cliente/applicazione.
- `3f4c69f2`: test PP/PEMD; dati meccanici ok, ma frase troppo assertiva su PEMD compatibilizzante.
- `3026ee25`: reflection prudente che collega caratterizzazione meccanica PP/PEMD + schede tecniche e identifica la validazione dei range come interesse futuro.

Il punto emergente e' `3026ee25`: Euri non ha salvato "test" e "scheda" come dati separati, ma ha iniziato a collegarli operativamente:

```text
scheda tecnica = promessa/range dichiarato
prova laboratorio = realta' misurata
confronto tra prova e scheda = validazione del materiale
```

Questa relazione non era stata codificata esplicitamente come regola. E' emersa dall'accoppiamento tra documento, spiegazione di Stefano, memorie tecniche e reflection.

Interpretazione consigliata:

- Non coscienza.
- Non semplice RAG.
- "Emergent operational linkage": criterio tecnico-operativo generato dal sistema.

## Caveat

La memoria `3f4c69f2` contiene una frase troppo forte:

```text
Euri conclude che il PEMD agisce come agente tenacizzante e compatibilizzante...
```

Meglio trattarla come ipotesi non verificata. Stefano preferisce non correggerla manualmente ora: se riemerge come affermazione forte, la correggera' a voce. Questo mantiene il ciclo naturale di apprendistato.

La reflection successiva `3026ee25` non ha amplificato questa ipotesi; e' rimasta prudente. Buon segnale.

## Pulse / tension

Ultimo quadro noto:

- Pulse ha eventi veri: `clock`, `memory`, `commitment`, `insight`, `vault`, `provenance`, ecc.
- `clock/threshold` scaduto e' salito a `notify` (`T=0.55`): buon segnale.
- `commitment` e' catturato ma ancora sottopesato (`ignore`).
- `insight/promoted` e' piatto dentro il tipo (`T=0.29`): non ranka qualita'.
- `vault` resta rumoroso/doppio da Obsidian Sync.
- Presenza visiva non affidabile finche' camera/VisualGate non sono sistemati.

## Prossimi test utili

1. Dare a Euri una nuova scheda tecnica o un nuovo risultato laboratorio.
2. Non imboccarla.
3. Verificare se applica spontaneamente:

```text
nuova scheda + nuovo dato laboratorio
→ confronto coi range
→ giudizio di conformita' / fuori range / sotto-specifica cliente
```

Se lo fa senza prompt diretto, il caso diventa molto forte per il paper.

## Nota metodo

Non pulire troppo le memorie. Rumore e correzioni naturali fanno parte del ciclo di apprendistato. Intervenire manualmente solo se un'ipotesi sbagliata distorce una risposta importante.
