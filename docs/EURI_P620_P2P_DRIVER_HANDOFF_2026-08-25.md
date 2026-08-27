# P620 — checkpoint driver NVIDIA 610 P2P

**Data:** 25 agosto 2026
**Macchina:** Lenovo ThinkStation P620, 2x RTX 4060 Ti 16 GB
**Scopo:** provare il P2P CUDA in modo reversibile, senza abilitarlo
automaticamente nei servizi Euri/Ollama.

## Stato prima dell'installazione

- Pop!_OS 24.04 LTS.
- BIOS Lenovo `S07KT6EA`.
- Kernel attivo `7.0.11-76070011-generic`.
- Kernel alternativi installati: `6.18.7-76061807-generic` e
  `6.17.9-76061709-generic`.
- Driver attivo `580.159.03` open.
- IOMMU disabilitato con `amd_iommu=off`, ma P2P ancora `CNS`/`NS`.
- Nessun servizio e nessuna variabile `GGML_CUDA_P2P` modificati.

### Baseline CUDA ufficiale con driver 580

Test NVIDIA `cuda-samples` tag `v12.0`, commit `2b68922`, compilati con CUDA
12.0:

- `simpleP2P`: 2 GPU rilevate, accesso peer negato in entrambe le direzioni;
- `p2pBandwidthLatencyTest`: matrice di connettivita' `0` fra GPU0 e GPU1;
- banda unidirezionale host-staged: GPU0->GPU1 `9,72 GB/s`, GPU1->GPU0
  `11,00 GB/s`;
- banda bidirezionale host-staged: circa `13,2 GB/s` per direzione;
- latenza GPU cross-device: `12,30 us` e `17,63 us`.

I binari e il checkout ridotto sono conservati sotto
`models/nvidia_p2p/cuda-samples/`.

## Driver preparato e conservato

Repository sorgente:
`https://github.com/aikitoria/open-gpu-kernel-modules.git`

- branch: `610.43.02-p2p`;
- commit: `14de73d818f98eba82f753132bfed8f6ed6314b7`;
- build completata per `7.0.11-76070011-generic`;
- moduli prodotti: `nvidia`, `nvidia_modeset`, `nvidia_drm`, `nvidia_uvm`,
  `nvidia_peermem`;
- versione/vermagic verificati: `610.43.02`, kernel 7.0.11 con modversions.

Copie persistenti, ignorate da Git, in `models/nvidia_p2p/610.43.02-p2p/`:

```text
35294464bc83e6abdc6c96ebda292c8eea384e51db51186b23186ff3e9e540c8  aikitoria-open-gpu-kernel-modules-built-7.0.11.tar.gz
05a3150465dfe5abf9fa14404fb6d2910b6a9a9040dc694348ede7f0cbf01c53  aikitoria-open-gpu-kernel-modules-source.tar.gz
```

## Strategia di installazione

1. Installare il pacchetto Ubuntu `nvidia-driver-610-open`, così user-space,
   firmware GSP e moduli kernel hanno tutti versione 610.43.02.
2. Lasciare intatti i moduli DKMS ufficiali.
3. Installare i cinque moduli P2P in una directory separata, selezionandoli
   esclusivamente per il kernel 7.0.11 tramite una regola `depmod`.
4. Passare da `amd_iommu=off` a `amd_iommu=on iommu=pt`, come richiesto dal
   driver sperimentale.
5. Ricostruire initramfs, verificare con `modprobe -n` quale modulo sara'
   caricato e solo dopo riavviare.

Il kernel 6.18.7 deve rimanere senza overlay P2P e costituisce la via di fuga
dal menu systemd-boot.

## Stato pronto al riavvio

- Stack Ubuntu `610.43.02-0ubuntu0.24.04.1` installato correttamente.
- DKMS ufficiale 610 compilato sia per 7.0.11 sia per 6.18.7.
- Overlay installato in
  `/lib/modules/7.0.11-76070011-generic/updates/aikitoria-p2p/`.
- Regola `/etc/depmod.d/aikitoria-p2p.conf` verificata con `modinfo`:
  tutti i cinque moduli del 7.0.11 puntano all'overlay; quelli del 6.18.7
  puntano a `updates/dkms` ufficiale.
- Voce systemd-boot `Pop_OS-current`: kernel 7.0.11.
- Voce systemd-boot `Pop_OS-oldkern`: kernel 6.18.7 verificato con `file`.
- Voce Recovery presente con kernel 6.17.9.
- Opzioni di boot correnti: `amd_iommu=on iommu=pt`; `amd_iommu=off` rimosso.
- ReBAR attivo: BAR1 fisico da 16 GB su entrambe le GPU.
- ACS dei due root port: `ReqRedir+`, `CmpltRedir+`, `DirectTrans-`. Non e'
  stato applicato alcun override ACS; il primo test deve misurarne l'effetto
  reale.
- Spazio libero: circa 127 GB sulla root e 576 MB sulla ESP.

Prima del riavvio il modulo ancora caricato in RAM e' 580.159.03 mentre lo
user-space su disco e' gia' 610.43.02: un eventuale errore temporaneo di
`nvidia-smi` e' atteso e si risolve soltanto con il nuovo boot.

Rollback 580 locale conservato in
`models/nvidia_p2p/rollback-580.159.03/`: 21 pacchetti `.deb`, cinque moduli
del kernel 7.0.11 e checksum SHA-256, per circa 429 MB.

## Gate dopo il riavvio

Prima di usare Ollama/Euri:

```bash
cat /proc/cmdline
uname -r
modinfo -F filename nvidia
modinfo -F version nvidia
nvidia-smi
nvidia-smi topo -m
nvidia-smi topo -p2p p
nvidia-smi topo -p2p r
nvidia-smi topo -p2p w
journalctl -b -k --no-pager | grep -i -E 'NVRM|Xid|iommu|amd-vi'
```

Eseguire poi `simpleP2P` e `p2pBandwidthLatencyTest`, controllando sia accesso
sia correttezza dei dati. Solo se tutto passa, fare un A/B isolato dello stesso
modello con e senza `GGML_CUDA_P2P=1`. Il driver si mantiene soltanto se e'
stabile e porta un vantaggio misurabile.

## Recupero

- Desktop bloccato ma kernel vivo: `Ctrl+Alt+F3`.
- Blocco completo: `Alt+Stamp`, poi lentamente `S`, `U`, `B`.
- Boot fallito: aprire systemd-boot e avviare il kernel 6.18.7.
- Una volta rientrati, rimuovere la regola e la directory overlay P2P, eseguire
  `depmod` e ripristinare i parametri kernel.
- Se necessario, reinstallare `nvidia-driver-580-open`; il ramo Pop disponibile
  al momento della preparazione e' `580.173.02`.

Non spegnere forzatamente durante l'installazione dei pacchetti o la creazione
dell'initramfs. Un reset e' accettabile solo dopo un vero blocco del sistema.
