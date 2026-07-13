# Artefatto 1 — Sonda offline: uno stato vettoriale decadente porta segnale predittivo?

**Progetto, 13/07/2026. Solo design — nessuna implementazione, nessun tocco al repo,
nessuna interferenza con la raccolta dream_trace (tutto read-only su dati già registrati).**

## Domanda e ipotesi

**Domanda:** uno stato S costruito come somma decadente di embedding di eventi contiene
informazione sul contesto corrente che una rappresentazione simbolica banale non ha già?

**Ipotesi nulla (previsione di Claude, da falsificare):** nello spazio e5 anisotropo del
progetto (cos ~0.79–0.85 tra tutto), S collassa sul centroide globale dopo pochi update e
il suo potere predittivo non supera "il dominio degli ultimi eventi".

**Patto:** se la sonda dice no, l'integrazione runtime del vettore non si fa. Se dice sì,
si riapre il discorso con numeri in mano.

## Dati (verificati il 13/07)

| Fonte | Quantità | Finestra | Contenuto utile |
|---|---|---|---|
| `euri:memory:*` | 1410 con embedding+domain+created_at | 23/04 → 13/07 | **dataset primario**: evento = creazione memoria (embedding 1024d, dominio, source, ts) |
| `euri:pulse` | 2619 eventi | 13/06 → oggi | secondario: sequenza sense/kind (payload magro — "registra il verbo non l'oggetto", misurato 16/06) |
| domini distinti | 146 | — | fortemente sbilanciati (~10 eventi/dominio in media) |

Nessun daemon, nessuna scrittura: si legge la storia e si riproduce la sequenza cronologica.

## Compito di predizione

Dato lo stato S dopo l'evento *t*, predire il **dominio dell'evento t+1**.

- **Classi:** solo i domini con ≥20 eventi; il resto collassa in "altro". Riportare la
  copertura (quota di eventi nelle classi tenute).
- **Valutazione doppia:** (a) su tutte le transizioni; (b) **solo sulle transizioni con
  cambio di dominio** — il passive learner salva a raffiche nello stesso dominio, quindi
  la baseline "ultimo dominio" è gonfiata dai burst; il sottoinsieme discriminante è dove
  il dominio cambia.
- Split **temporale** 70/30 per il probe lineare (mai shuffle: leakage).

## Stati da testare

| Variante | Definizione |
|---|---|
| S1 | somma decadente: `S ← S·exp(−Δt/τ) + e_t`, τ ∈ {1h, 6h, 24h, 7g} |
| S2 | media mobile pesata (EMA, α ∈ {0.1, 0.3, 0.5}) |
| S3 | S1 − centroide globale (rimozione anisotropia, la regola-relativa del progetto) |
| S4 | S1 su embedding sbiancati (PCA whitening, sklearn 1.8 presente) |
| S5 | differenza dalla media recente (S1 con τ corto − S1 con τ lungo) |
| S6 | ciascuna variante con e senza normalizzazione L2 |
| S7 | **controllo simbolico**: one-hot dei domini con lo stesso decay di S1 — se S7 ≈ S-embedding, l'informazione era l'IDENTITÀ del dominio, non la geometria del vettore |

## Predittori e baseline

**Predittori sullo stato:** (a) nearest-centroid per dominio su similarità centrata;
(b) regressione logistica leggera (solo per misurare l'informazione presente in S —
ammessa dal mandato, nessun training complesso).

**Baseline obbligatorie:**
- B1 dominio dell'ultimo evento
- B2 moda degli ultimi 3
- B3 moda degli ultimi 5
- B4 prior globale (dominio più frequente)
- B5 recency di progetto: adattamento del set "domini attivi" del Filtro del Risveglio
  (teach/user/reflection, 30 giorni)

## Metriche

- top-1 accuracy, top-3 accuracy, macro-F1 (sulle classi tenute), matrice di confusione
- copertura, latenza per update, dimensione stato
- **deriva verso il centroide:** cos(S_t, centroide globale) in funzione del numero di
  update — misura diretta dell'ipotesi-collasso
- **separazione:** cos medio tra stati campionati in momenti dominati da domini diversi
  vs stesso dominio
- stabilità: ripetere su 3 seed (dove serve) e su τ adiacenti; se il ranking delle
  varianti balla, il segnale non è robusto

## Criteri pre-registrati

**GO (vettore merita l'integrazione):** la migliore variante S batte la migliore baseline
simbolica di **≥5pp top-1** (o ≥0.05 macro-F1) sulle transizioni con cambio-dominio, con
bootstrap 95% che non attraversa lo zero, **E** il probe lineare su S supera B2/B3, **E**
S-embedding > S7 one-hot (la geometria aggiunge qualcosa oltre il simbolo).

**NO-GO:** deriva-al-centroide (cos(S, centroide) > 0.95 entro ~50 update senza
centratura, e la centratura non ripristina la separazione), **oppure** S7 ≈ S-embedding,
**oppure** nessuna variante batte B2 fuori dai burst.

**Caveat onesto:** next-domain è un proxy. Un no non prova che S sia inutile per ogni
scopo; toglie però la giustificazione pattuita all'integrazione runtime.

## Rischi

- burst del passive learner → B1 gonfiata → mitigato dalla valutazione su cambio-dominio;
- classi rare → collasso in "altro" + macro-F1 solo su classi tenute;
- doppioni/near-duplicate (dedup passivo imperfetto) → dedup per contenuto prima della
  sequenza, dichiarato nel report;
- 146 domini auto-scoperti = etichette rumorose esse stesse (assegnate da LLM) — il
  tetto di ogni predittore include questo rumore; vale per S e per le baseline in eguale
  misura (confronto equo).

## Costo e superficie

Uno script read-only (`scripts/experiments/probe_vector_state.py`, ~250 righe), mezza
giornata. Zero scritture Redis, zero daemon, zero dipendenze nuove (numpy+sklearn presenti).
Eseguibile in qualunque momento — non tocca la raccolta dream_trace.

---

## ESITO (13/07/2026, sonda eseguita: `scripts/experiments/probe_vector_state.py`)

**NO-GO — tutti e tre i criteri di abbandono pre-registrati sono scattati.**

Dataset: 1407 eventi dedup (23/04→13/07), 15 classi ≥20 eventi + "altro", copertura 69%.

1. **Collasso sul centroide, confermato:** cos(S_24h, centroide globale) = **0.9885 medio**
   (p5 0.9775): lo stato È il centroide anisotropo in ogni istante. Separazione tra stati
   con dominio dominante diverso: 0.9801 vs 0.9754 (**Δ = +0.005** — indistinguibili).
2. **S7 one-hot ≈ S-embedding (il criterio-killer):** il gemello simbolico con la stessa
   dinamica di decadimento fa **0.381 top-1 / 0.694 top-3** (tutte le transizioni) contro
   0.352/0.636 della migliore variante embedding — su top-3 il one-hot è il migliore di
   TUTTI i predittori. L'informazione era l'identità del dominio, non la geometria.
3. **Nessuna variante batte le baseline:** sul cambio-dominio, miglior vettoriale
   S1_7g 0.285 vs miglior baseline B4_prior 0.280 — Δ bootstrap 95% **[−0.7pp, +1.9pp]**,
   zero. Probe lineare (split temporale): top-1 0.151 contro 0.431 della moda-ultimi-3
   sullo stesso segmento: il vettore contiene MENO informazione usabile di 3 stringhe.
   Centrare o sottrarre il centroide PEGGIORA (0.153/0.102): la poca resa della somma
   grezza era il prior di frequenza dei domini, che il simbolico cattura meglio.

Nota di contesto: anche i predittori simbolici si fermano a ~0.28–0.38 top-1 — il
next-domain su 146 etichette auto-scoperte è intrinsecamente rumoroso — ma il confronto
è equo (stesso task, stessi dati) e il vettore non aggiunge nulla, ovunque.

**Conseguenze:** (a) il "cognitive_state" vettoriale runtime NON si costruisce — la
strada per la continuità è quella simbolica/groundata (SPEC_ACTIVE_FOCUS.md);
(b) risultato negativo utile per la serie paper (lo stato-somma in uno spazio
anisotropo è un timbro, coerente con l'analisi convergenza 04–13/07);
(c) latenza mai il problema (0.8 ms/update): l'obiezione era epistemica, non di costo.
