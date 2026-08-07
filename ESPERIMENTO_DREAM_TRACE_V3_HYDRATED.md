# Dream Trace Paired V3 — semi reidratati

**Versione:** `dream_trace_paired_v3_hydrated`
**Avvio protocollo:** 6 agosto 2026
**Stato:** raccolta congelata il 7 agosto 2026, prima della soglia pianificata.

## Motivo del congelamento

Il primo ciclo notturno ha confermato il valore diagnostico della reidratazione:
nel caso con un solo lato idratato il generatore è rimasto fedele a quel lato e ha
inventato dettagli sul seme legacy, correttamente bloccati dal giudice `NO/SI`.

La raccolta non prosegue fino a 50 coppie perché il runtime di produzione passa
ora all'architettura `rem_wake_v1`: un primo passaggio REM divergente produce
materiale non cognitivo e un secondo passaggio lucido tenta la distillazione.
Questo cambia trattamento, numero di chiamate e significato dell'output; continuare
lo stesso batch violerebbe la separazione dichiarata qui. Stream e chiavi V3 restano
intatti per audit storico. Il flag paired è spento e nessun risultato V3 viene
presentato come confronto conclusivo.

## Perché una nuova versione

Il candidato workstation/perossido ha mostrato un difetto nel substrato del
Dream: la memoria “Il prezzo pagato per il sistema è di 980 euro IVA inclusa”
aveva perso il referente presente nella conversazione originale. Il generatore
ha identificato arbitrariamente “il sistema” con un impianto di dosaggio.

La v3 modifica il prompt di entrambi i bracci. Ogni seme con provenienza riceve:

- il turno indicato da `temporal_context.source_turn_refs`;
- fino a due turni precedenti nello stesso segmento e scope;
- etichette che separano fonte utente e testo dell'assistente;
- il vincolo che il contesto risolva soltanto referenti e non aggiunga premesse.

Poiché cambia il substrato presentato al modello, i record v2 restano validi
soltanto per il protocollo precedente e non entrano nell'analisi v3.

## Disegno invariato

Come nella v2, ogni coppia usa gli stessi due semi in entrambi i bracci:

- **baseline:** prompt Dream senza residuo di esplorazione;
- **trattamento:** stesso prompt e stessi semi, con residuo di strategia.

Solo il baseline può diventare un insight vivo. Il trattamento resta
strumentazione. Residuo, sequence e record portano la nuova versione; nessuna
chiave v2 viene letta o modificata.

## Nuove invarianti

1. La memoria compatta resta canonica e non viene riscritta.
2. `source_turn_refs` contiene soltanto le fonti reali del fatto.
3. `dream_context_turn_refs` registra l'intera finestra mostrata al modello.
4. Il metadato persistito non può iniettare turni estranei: i giudici lo
   intersecano con la finestra nuovamente derivata dalla fonte canonica.
5. Memorie legacy senza provenienza dichiarano il contesto indisponibile e il
   modello deve astenersi se il referente necessario resta ambiguo.
6. Entrambi i bracci ricevono la stessa idratazione.

## Raccolta e separazione

Conservare criteri e dimensione pianificata della v2: congelare almeno 50 coppie
valide prima di modificare modello, generazione, seed gate, idratazione o
residuo. L'export deve filtrare esclusivamente
`experiment_version=dream_trace_paired_v3_hydrated`.

I test di correttezza sono in `test_dream_seed_hydration.py` e
`test_dream_trace_paired.py`.
