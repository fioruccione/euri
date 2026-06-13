# Reflection Cascade Audit

Generato: 2026-06-09 13:31:22
Near-duplicate threshold: cosine >= 0.90

## 1. Volume
source         | count | % totale
---------------+-------+---------
passive        | 472   | 48.0%   
reflection     | 219   | 22.3%   
loop2e         | 177   | 18.0%   
user           | 40    | 4.1%    
teach          | 36    | 3.7%    
episode        | 15    | 1.5%    
web            | 14    | 1.4%    
conversation   | 8     | 0.8%    
obsidian_vault | 2     | 0.2%    

Reflection: 219 / 983 = 22.3%

## 2. Sottotipi reflection
sottotipo           | count | % reflection
--------------------+-------+-------------
2a reflection       | 190   | 86.8%       
2f confronto        | 15    | 6.8%        
2h self-observation | 14    | 6.4%        

## 3. Tasso di generazione reflection ultimi 14 giorni
giorno     | reflection create
-----------+------------------
2026-05-27 | 14               
2026-05-28 | 12               
2026-05-29 | 11               
2026-05-30 | 1                
2026-05-31 | 0                
2026-06-01 | 3                
2026-06-02 | 2                
2026-06-03 | 17               
2026-06-04 | 23               
2026-06-05 | 21               
2026-06-06 | 8                
2026-06-07 | 0                
2026-06-08 | 27               
2026-06-09 | 2                

## 4. Cluster transitivi (union-find, cosine >= soglia)
Reflection senza embedding: 0
Cluster totali: 33 | dimensione media: 5.67 | max: 82 | reflection in cluster>=2: 187 / 219 = 85.4%

domain                     | refl con emb | cluster | avg size | max | % assorbite
---------------------------+--------------+---------+----------+-----+------------
chimica polimeri           | 97           | 4       | 24.00    | 82  | 99.0%      
generale                   | 14           | 3       | 4.33     | 9   | 92.9%      
automazione industriale    | 13           | 2       | 6.00     | 10  | 92.3%      
business                   | 6            | 1       | 5.00     | 5   | 83.3%      
intelligenza artificiale   | 6            | 2       | 2.50     | 3   | 83.3%      
gestione operativa         | 6            | 1       | 5.00     | 5   | 83.3%      
sviluppo software          | 5            | 1       | 5.00     | 5   | 100.0%     
processi operativi         | 5            | 2       | 2.00     | 2   | 80.0%      
produzione plastica        | 5            | 2       | 2.50     | 3   | 100.0%     
controllo qualità          | 5            | 1       | 5.00     | 5   | 100.0%     
informatica                | 5            | 1       | 2.00     | 2   | 40.0%      
estrusione plastica        | 4            | 1       | 2.00     | 2   | 50.0%      
telecomunicazioni          | 4            | 2       | 2.00     | 2   | 100.0%     
gestione prodotti          | 4            | 1       | 3.00     | 3   | 75.0%      
processi industriali       | 4            | 1       | 4.00     | 4   | 100.0%     
gestione dati              | 4            | 1       | 2.00     | 2   | 50.0%      
produzione industriale     | 3            | 1       | 2.00     | 2   | 66.7%      
elettronica radiofrequenza | 3            | 1       | 3.00     | 3   | 100.0%     
automazione tecnologica    | 3            | 1       | 2.00     | 2   | 66.7%      
packaging plastica         | 2            | 1       | 2.00     | 2   | 100.0%     

## 4b. Duplicati diretti (nearest-neighbor, stesso dominio, no superseded/no confronto)
Reflection escluse da metrica diretta (superseded o [confronto]): 18
Reflection senza embedding nella metrica diretta: 0
NN-distance | count | % dirette
------------+-------+----------
<=0.05      | 127   | 68.3%    
<=0.10      | 39    | 21.0%    
<=0.15      | 17    | 9.1%     
<=0.20      | 3     | 1.6%     
>0.20       | 0     | 0.0%     

Con vicino diretto <= 0.10 (dedup-abili): 166 / 186 = 89.2%

domain                   | n NN | mediana NN | p90 NN | dedup-abili | % dedup-abili
-------------------------+------+------------+--------+-------------+--------------
chimica polimeri         | 89   | 0.022      | 0.049  | 87          | 97.8%        
generale                 | 14   | 0.063      | 0.097  | 13          | 92.9%        
automazione industriale  | 13   | 0.054      | 0.095  | 12          | 92.3%        
business                 | 6    | 0.015      | 0.104  | 5           | 83.3%        
intelligenza artificiale | 6    | 0.055      | 0.097  | 5           | 83.3%        

## 5. Auto-amplificazione recalled_count
gruppo     | n   | avg   | p50 | p90 | max | toccate | % toccate
-----------+-----+-------+-----+-----+-----+---------+----------
reflection | 219 | 15.51 | 8   | 37  | 143 | 209     | 95.4%    
vissute    | 740 | 8.88  | 8   | 18  | 41  | 670     | 90.5%    

## 6. TTL / lifecycle reflection
TTL bucket   | count | % reflection
-------------+-------+-------------
30-90 giorni | 219   | 100.0%      

Scadenze JSON expires_at presenti: 219 | giorni residui avg=77.8, p50=83.2, p90=89.9, max=89.9

## 7. Concentrazione per dominio
domain                     | reflection | % reflection
---------------------------+------------+-------------
chimica polimeri           | 97         | 44.3%       
generale                   | 14         | 6.4%        
automazione industriale    | 13         | 5.9%        
business                   | 6          | 2.7%        
intelligenza artificiale   | 6          | 2.7%        
gestione operativa         | 6          | 2.7%        
sviluppo software          | 5          | 2.3%        
processi operativi         | 5          | 2.3%        
produzione plastica        | 5          | 2.3%        
controllo qualità          | 5          | 2.3%        
informatica                | 5          | 2.3%        
estrusione plastica        | 4          | 1.8%        
telecomunicazioni          | 4          | 1.8%        
gestione prodotti          | 4          | 1.8%        
processi industriali       | 4          | 1.8%        
gestione dati              | 4          | 1.8%        
produzione industriale     | 3          | 1.4%        
elettronica radiofrequenza | 3          | 1.4%        
automazione tecnologica    | 3          | 1.4%        
packaging plastica         | 2          | 0.9%        

## Riepilogo numerico neutro
- Reflection totali: 219 (22.3% del totale memorie).
- Cluster transitivi: 33 cluster; 187 reflection assorbite (85.4% delle reflection con embedding).
- Duplicati diretti dedup-abili (NN distance <= 0.10): 166 / 186 = 89.2%.
- recalled_count medio: reflection 15.51, vissute 8.88.
- Dominio reflection principale: chimica polimeri (97).


---

## Verifica post-dedup latest-wins (2026-06-13 16:50) — daemon riavviato 12:46

Confronto col baseline 9 giu, i 3 indicatori attesi (superseded↑, cluster max↓, %diretti↓):

| indicatore | 9 giu | 13 giu | atteso | esito |
|---|---|---|---|---|
| reflection escluse §4b (superseded o [confronto]) | 18 | 63 | ↑ | ✅ dedup scatta (×3.5) |
| % diretti dedup-abili ≤0.10 | 89.2% | 86.3% | ↓ | ✅ lieve |
| quota gemelli ≤0.05 | 68.3% (127) | 56.6% (103) | ↓ | ✅ |
| cluster transitivo max | 82 | 94 | ↓ | ❌ cresce |
| reflection totali | 219 | 260 | — | +41 (generazione 3–8 giu) |
| recalled avg reflection | 15.51 | 13.52 | — | ↓ (raffreddamento auto-amplificazione) |
| % reflection toccate | 95.4% | 86.5% | — | ↓ (effetto touch=False rag-rebalance) |

**Verdetto:** il dedup latest-wins funziona nel suo scopo (doppioni DIRETTI: superseded triplicati,
gemelli ≤0.05 da 68%→57%) ma NON contiene la cascata transitiva (blob chimica-polimeri 82→94).
Coerente con la scelta di disegno (dedup centrato sui diretti). N3 ha già stabilito che nessun
segnale economico/fidelity separa assorbimento dannoso da legittimo → la crescita del cluster
resta senza azione finché non emerge un segnale di danno reale. Verifica pendente CHIUSA.
