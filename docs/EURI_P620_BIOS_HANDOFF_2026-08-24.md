# Handoff BIOS Lenovo ThinkStation P620

**Data:** 24 agosto 2026
**Scopo:** riprendere in sicurezza l'aggiornamento BIOS dopo il riavvio, senza
affidarsi al contesto della sessione Codex precedente.

## Stato verificato prima del riavvio

- Macchina: Lenovo ThinkStation P620, machine type/model `30E1S36700`.
- Sistema: Pop!_OS 24.04 LTS.
- BIOS iniziale osservato: `S07KT1FA`, esposto da fwupd come `1.31`.
- Passaggio intermedio completato con successo: `S07KT29A`, fwupd `1.41`.
- Verifica diretta effettuata tramite `/sys/class/dmi/id/bios_version`:
  `S07KT29A`.
- `fwupdmgr get-updates --no-unreported-check` riporta `Update State: Success`
  per il passaggio a `1.41`.
- Redis e la memoria Euri sono tornati operativi dopo il riavvio: `PONG`, 6.675
  chiavi ricaricate, audit read-only con 1.651 memorie, 383 insight e 128 dream.

## Aggiornamento successivo già verificato

Il feed LVFS firmato propone direttamente il firmware Lenovo `1.110`, release
ID `145880`, creato il 16 luglio 2026 e testato da Lenovo il 18 agosto 2026 su
Ubuntu 24.04 partendo esattamente dalla versione `1.41`.

- Device fwupd: `557960baa042cab815d0c30d318fa147f32cdd55`
- GUID: `94d6edee-8f19-40a9-be14-d881e2e3d55c`
- Target fwupd: `1.110`
- Target BIOS atteso: `S07KT6EA`
- SHA-256 dichiarato dal feed LVFS:
  `4736f5ad331dcf41d8d4180b3b114eaa8f67af60255e79fe796ef3b87331aecd`

La procedura Lenovo consente, una volta raggiunto il BIOS intermedio `29A`, di
passare a `45A` oppure all'ultima versione disponibile. Non è quindi necessario
installare manualmente `1.81` o ogni versione intermedia.

## Comando autorizzato da eseguire

Salvare il lavoro aperto, assicurarsi che l'alimentazione sia stabile, quindi:

```bash
sudo fwupdmgr update 557960baa042cab815d0c30d318fa147f32cdd55
```

Confermare soltanto se il target mostrato è `1.110`. Accettare il riavvio e non
spegnere o resettare la workstation durante il flash; più riavvii possono essere
normali.

## Verifiche dopo il rientro

Non rilanciare alla cieca l'aggiornamento. Leggere prima questo handoff, poi
eseguire controlli read-only:

```bash
cat /sys/class/dmi/id/bios_version
sudo fwupdmgr get-history
sudo fwupdmgr get-updates --no-unreported-check
```

Esito atteso: BIOS `S07KT6EA` / fwupd `1.110`, stato dell'aggiornamento
`Success`. Se l'esito è diverso, fermarsi e diagnosticare prima di riprovare.

Dopo il successo, entrare nel BIOS con `F1` e controllare insieme a Stefano:

- `Re-Size BAR Support`;
- Secure Boot, che deve restare disabilitato per l'assetto corrente;
- ordine di boot UEFI;
- SMT, NUMA e virtualizzazione;
- eventuali impostazioni PCIe/GPU tornate ai default.

## Memoria Redis precedente da correlare

La memoria esplicita creata prima del passaggio intermedio è:
`euri:memory:568dcec9-f559-437c-acd8-0858a1e5f05e`.

Va trattata come fotografia precedente: prescriveva almeno `1.81`, mentre il
controllo hardware successivo ha stabilito che LVFS offre e supporta direttamente
`1.110` dalla versione attuale `1.41`. Questo handoff è il checkpoint operativo
più recente.
