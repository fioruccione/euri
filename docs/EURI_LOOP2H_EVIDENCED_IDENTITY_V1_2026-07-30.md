# Loop 2h — identità fondata sulla prova v1

**Data:** 30/07/2026  
**Contratto:** `loop2h-evidenced-identity-v1`  
**Stato:** implementato; validazione indipendente `COG-01` ancora aperta.

## Problema

Il classificatore precedente chiedeva al modello una label
`SAME/RELATED/DIFFERENT/UNKNOWN`, ma non richiedeva una prova verificabile.
Nel primo banco controllato non usava mai `UNKNOWN` sui sei casi costruiti come
ambigui. Una label nuda poteva quindi mantenere o invertire un arco
`superseded_by`.

## Correzione

Il modello deve ora produrre un oggetto JSON che separa:

- il `claim_subject` di ciascuna memoria: l'entità della quale viene aggiornato
  uno stato, una proprietà o un impegno;
- identità `SAME/DISTINCT/UNKNOWN`;
- base `EXPLICIT/INFERRED/INSUFFICIENT`;
- specificità del soggetto;
- tipo di entità;
- eventuale relazione operativa fra soggetti distinti;
- estratti letterali dalle due fonti.

Una policy pura autorizza `SAME`, `RELATED` o `DIFFERENT` soltanto quando:

1. il contratto è completo e usa valori ammessi;
2. la base è `EXPLICIT`;
3. entrambi i claim subject sono specifici;
4. gli estratti compaiono realmente nelle rispettive memorie;
5. i tipi sono noti e coerenti col giudizio;
6. un caso distinto ha anche una relazione risolta.

Ogni altro esito diventa `UNKNOWN`. La prova strutturata viene conservata nella
reflection o nell'audit dell'inversione. Una falsa supersessione continua a
essere invertita soltanto con evidenza affermativa di soggetti distinti.

`UNKNOWN` non consuma la coppia. Per evitare che casi irrisolti occupino ogni
ciclo manutentivo, il loser riceve un rinvio persistente legato allo specifico
winner: 1, 2, 4, 8, 16 e massimo 30 giorni. Un nuovo arco riparte da un giorno.
Nessun timeout o errore di parsing viene trasformato in decisione.

## Diagnostica sul banco già aperto

Il primo tentativo strutturato distingueva meglio gli ambigui ma sceglieva a
volte il nuovo valore della proprietà come referente: Elisa/Paola al posto del
progetto Nadir, oppure la scheda al posto della formulazione F-88. È stato
scartato prima del rollout.

La versione definitiva definisce invece il `claim_subject`. Sul vecchio banco
development di 42 casi, una singola replica diagnostica con il modello locale
ha prodotto:

| controllo | esito |
|---|---:|
| relazione esatta | 34/42 (80,95%) |
| vere supersessioni riaperte | 0/12 |
| entità esplicitamente distinte riconosciute | 12/12 |
| casi ambigui che hanno mutato l'arco | 0/6 |
| casi ambigui classificati `UNKNOWN` | 6/6 |

Le differenze residue fra `RELATED` e `DIFFERENT` non cambiano l'azione:
entrambe riaprono l'arco fra soggetti positivamente distinti. Un caso
complementare sullo stesso viaggio è stato invece proposto come `RELATED`; non
tocca le dodici supersessioni autentiche, ma conferma che il challenge nuovo
resta necessario.

## Limiti

- Il banco è aperto e il prompt è stato corretto osservandone i failure mode:
  questi numeri sono diagnostica post-hoc, non validazione.
- È stato provato un solo modello locale e una sola replica completa.
- La policy verifica che la prova sia presente, non che l'interpretazione
  semantica del modello sia vera.
- `COG-01` resta aperto: serve un challenge opportunity-first nuovo, congelato
  prima dei risultati, con vere e false supersessioni e identità insufficienti.
- Loop 2f legacy resta invariato. Questa modifica non autorizza Loop 2f v3.

## Regressioni

Le regressioni pure coprono contratto, base inferita, soggetti generici,
citazioni inesistenti, tipi incompatibili, riparazione positiva, audit
persistito, `UNKNOWN` ritentabile e backoff specifico per arco.

