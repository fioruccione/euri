# Dual-channel prompt ablation v1

Stato al freeze: development experiment, nessun risultato generato.

## Domanda

Quando il dual-channel aggiunge il turno gold, Gemma usa poco il verbatim perché
è in coda, perché l'etichetta lo rende secondario, o entrambe le cose?

## Bracci congelati

- `append_v1`: contesto e risposta già prodotti dal census.
- `prepend_plain_v1`: stessi byte del blocco aggiunto, spostati prima della base.
- `evidence_first_v1`: stessi turni prima della base, senza testo delle memorie
  passive, con il contratto:

  > I turni sono trascrizioni originali potenzialmente pertinenti; usali solo se
  > rispondono direttamente, preferiscili a una sintesi divergente e trattali
  > come prova di ciò che è stato detto, non della verità nel mondo.

Retrieval, Q=2, R=1, turni aggiunti, localizzazione, modello, temperatura,
`num_predict` e seed per replica restano invariati. Se non ci sono aggiunte il
contesto è identico e la risposta viene riusata senza chiamare il modello.

## Universo e interpretazione

Replay completo delle 10 coppie già aperte: 5 conversazioni × 2 repliche, 989
domande per replica. È un development set diagnostico, non una nuova validazione
indipendente. Gli hash della base e dell'append devono coincidere con ogni report
originale prima di generare.

## Gate di sviluppo congelato

Un candidato è `GO_DEV` soltanto se, rispetto ad `append_v1`:

1. delta medio token-F1 su tutte le answerable > 0;
2. delta token-F1 sui casi evidence-flip > 0;
3. delta accuratezza avversariale >= -0,02;
4. almeno 4/5 conversazioni hanno delta F1 non negativo.

Altrimenti è `NO_GO_DEV`. Anche `GO_DEV` autorizza soltanto una successiva
validazione indipendente; non autorizza da solo l'attivazione in produzione.
