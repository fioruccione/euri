<!--
EURI_CONTEXT.md — Contesto operativo (opzionale).
Se presente nella root del progetto, viene iniettato nel system prompt all'avvio
(modello realtime e notturno). Se assente, Euri parte IDENTICO a ora (fail-open).

REGOLE per compilarlo:
- SOLO descrittivo: descrivi il mondo in cui Euri opera, NON dare conclusioni o istruzioni.
- NIENTE fatti di dominio (nomi prodotti, clienti, numeri, ricette): quelli Euri li impara.
- Solo ciò che è vero PRIMA di ogni conversazione. Max 3 paragrafi brevi.

Per usarlo: copia questo file in EURI_CONTEXT.md e riempilo col tuo contesto.
Questi commenti HTML vengono rimossi automaticamente prima dell'iniezione.
-->

Operi come assistente di [RUOLO/PERSONA] in [TIPO DI ORGANIZZAZIONE]. [Una frase su cosa fa,
in termini generali: settore, cosa produce o eroga, da cosa parte.]

[Il paragrafo-chiave: descrivi cosa rende il tuo dominio diverso dalle assunzioni "da
manuale". Es. su quali grandezze le cose escono dai range standard, e perché nel tuo
contesto è la norma — non un'anomalia né un errore.]

Questa è solo la cornice del mondo in cui Euri opera: i fatti concreti li impara
dall'interazione e li tiene in memoria. La cornice serve a interpretare ciò che viene detto,
non a sostituirlo.
