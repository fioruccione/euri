# Baseline interocezione hardware - 20/07/2026

## Perimetro

- Finestra osservata: 17/07/2026 11:19 - 20/07/2026 10:07 (70,8 ore)
- Campioni validi: 4152 su 4249 attesi
- Copertura: 97,7%
- Fault di campionamento: 0
- Carico rappresentativo: si

La review e' stata chiusa 1,2 ore prima delle 72 ore nominali per decisione
esplicita di Stefano. Copertura, durata e presenza di carico reale rendono la
finestra sufficiente per la calibrazione osservativa iniziale.

## Distribuzioni

| Sensore | Min | P50 | P95 | Max |
| --- | ---: | ---: | ---: | ---: |
| CPU package (C) | 55 | 58 | 65 | 80 |
| GPU 0 (C) | 41 | 44 | 52,45 | 65 |
| GPU 0 VRAM (%) | 7,521 | 32,192 | 95,818 | 97,106 |
| GPU 1 (C) | 38 | 40 | 50 | 65 |
| GPU 1 VRAM (%) | 0,763 | 0,995 | 82,869 | 82,869 |
| RAM (%) | 15,1 | 18,1 | 20,7 | 24,3 |

## Decisioni

1. Temperature e RAM restano sulle soglie esistenti: la baseline non mostra
   pressione termica o di memoria di sistema.
2. La VRAM GPU 0 al 95-97% e' un regime normale durante il caricamento dei
   modelli. La soglia WARNING passa da 92% a 98%.
3. La VRAM non possiede una soglia CRITICAL e non autorizza azioni automatiche.
4. Nessun riflesso protettivo viene attivato. Un futuro consumer dovra' combinare
   margine VRAM, fallimento reale di allocazione e snapshot fresco, senza uccidere
   processi dalla sola percentuale.
5. La raccolta osservativa continua per intercettare fault o regimi non presenti
   nella prima finestra.

## Prossimo checkpoint

Prima della Fase 1 serve almeno un evento realmente anomalo o un test controllato
che dimostri il comportamento del riflesso. Fino ad allora l'interocezione resta
un senso afferente, non un attuatore.
