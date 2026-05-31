# Archive — Working Paper History

Versioni precedenti del Working Paper *From Volatile Computation to
Persistent Cognition*, mantenute qui in coerenza con il principio di
soft-delete che il sistema applica alle proprie memorie (vedi Loop 2f
nel paper corrente): le versioni superate non vengono cancellate, sono
marcate come *superseded* e restano consultabili come audit trail.

## Contenuto

- `paper_v2_13.md` — Working Paper V2.13 (snapshot al commit `f5cfa43`,
  2026-05-14). Stato del paper subito dopo l'introduzione del Filtro del
  Risveglio (§7g), del Loop 2f esteso ai consolidati, e dell'audit
  ricalibrato.

- `paper_v2_14.md` — Working Paper V2.14 (snapshot al commit `cabab97`,
  2026-05-15). Stato del paper subito dopo l'introduzione del Loop 2g
  (Audit di Coerenza), della §7h sulla continuità trans-restart e sulla
  sintesi emergente.

- `paper_v2_15.md` — Working Paper V2.15 (snapshot dello stato datato 2026-05-18,
  catturato prima della riconciliazione a V2.19 del 2026-05-31). Stato del paper prima
  dell'aggiunta della §7j (Related Work) e della verifica/correzione dei
  riferimenti in §10.

La versione attuale (V2.19) è in `../paper_persistent_cognition.md`.
La storia completa di tutte le revisioni intermedie è nel git log del
file principale; questi snapshot sono punti fissi citabili.

## Convenzione

Il file principale del paper mantiene il nome `paper_persistent_cognition.md`
attraverso le versioni. La versione è dichiarata internamente nell'header
(*Date* e *Euri VX.YY* nell'abstract) e formalmente nella §0 Document
History. Ogni nuovo bump di versione che introduce contenuto sostanziale
(non solo refinement) produce uno snapshot in `archive/`.

## README del progetto

Lo stesso principio si applica al `README.md` root: a ogni bump di versione
con contenuto sostanziale si conserva qui lo snapshot precedente.

- `README_v2_18.md` — README alla linea **V2.18.2** (snapshot prima del bump a
  V2.19, 2026-05-30). Stato subito prima dell'introduzione di P1 (domini
  disambiguati dai vicini), del Memory Guard anti-poisoning e del passaggio
  del domain gating da filtro rigido a boost morbido nel retrieval.

Il README attuale (V2.19) è in `../README.md`.
