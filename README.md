# ASLAMX GitHub Scraper

**ASLAMX GitHub Scraper** je industrijsko zasnovan cevovod (pipeline) za masovno kloniranje, indeksiranje in ekstrakcijo podatkov iz GitHub repozitorijev. Razvit je bil kot ključna komponenta širšega ekosistema za avtomatizirano pripravo visokokakovostnih naborov podatkov (datasets) za učenje in fine-tuning AI modelov.

## 🚀 Namen projekta

Projekt rešuje izziv pridobivanja strukturiranih podatkov iz odprtokodnih projektov. Namesto preprostega kopiranja kode, ta scraper omogoča:
- **Inteligentno vzorčenje:** Izbira specifičnih niš in tipov repozitorijev.
- **Masovno procesiranje:** Hkratno kloniranje in obdelava več tisoč repozitorijev.
- **Priprava za AI:** Avtomatska ekstrakcija relevantnih delov kode in dokumentacije v formate, primerne za strojno učenje (JSONL, manifests).

## 🛠 Tehnične značilnosti

- **Backend:** FastAPI (Python) za visoko zmogljivost in asinhrono obdelavo.
- **Scheduler Service:** Integriran sistem za načrtovanje opravil, ki omogoča periodično osveževanje podatkov brez posredovanja uporabnika.
- **Data Pipeline:** Večstopenjska obdelava — od surovega kloniranja do filtriranja (quarantine) in končne ekstrakcije (teaching sets).
- **SQLite Database:** Robustno shranjevanje metapodatkov o repozitorijih, nišah in statusih obdelave.
- **Modularna arhitektura:** Čista ločitev med storitvami (services), modeli in UI komponentami.

## 📁 Struktura

- `src/services/`: Logika za scheduler, GitHub API integracijo in procesiranje podatkov.
- `src/routes/`: API končne točke in UI kontrolerji.
- `config/`: Konfiguracijske datoteke za niše in sistemske nastavitve.
- `templates/` & `static/`: Jinja2 vmesnik za spremljanje statusa scraperja v realnem času.

## 📝 Opomba za recenzente (Logix)

Ta projekt dokazuje moje razumevanje **podatkovnih cevovodov** in **asinhronih storitev**. Čeprav je bil prvotno razvit kot stranski projekt za zbiranje gradiva za večje AI sisteme, vključuje napredne koncepte, kot so:
- Upravljanje s ThreadPoolExecutorjem za vzporedne IO operacije.
- Avtomatizirane migracije baze podatkov.
- Kompleksno upravljanje z datotečnim sistemom za obdelavo velikih količin podatkov.

---
Created by **Aslamx**
