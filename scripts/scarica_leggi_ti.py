import os
import time
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Configurazione
URL_ELENCO = "https://m3.ti.ch/CAN/RLeggi/public/index.php/raccolta-leggi/elenco-atti"
BASE_URL_LEGGE = "https://m3.ti.ch/CAN/RLeggi/public/index.php/raccolta-leggi/legge-piatta/num/"
OUTPUT_DIR = "Leggi_Ticino_PDF"

def get_lista_leggi():
    """Recupera la lista degli ID dal sommario."""
    print("Recupero elenco leggi...")
    try:
        resp = requests.get(URL_ELENCO)
        soup = BeautifulSoup(resp.text, 'html.parser')
        leggi = []
        for a in soup.find_all('a', href=True):
            if 'legge-piatta/num/' in a['href']:
                numero = a['href'].split('/')[-1]
                titolo = a.text.strip()
                if numero and numero not in [l['id'] for l in leggi]:
                    leggi.append({'id': numero, 'titolo': titolo})
        return leggi
    except Exception as e:
        print(f"Errore recupero lista: {e}")
        return []

def main():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    leggi = get_lista_leggi()
    print(f"Trovate {len(leggi)} leggi.")

    # Avviamo Playwright (il browser)
    with sync_playwright() as p:
        # Lancia un browser Chromium (tipo Chrome)
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for i, legge in enumerate(leggi):
            # Limita per test (rimuovi [:5] per scaricarle tutte)
            # if i > 5: break 

            id_legge = legge['id']
            # Pulizia nome file
            safe_title = "".join([c for c in legge['titolo'] if c.isalnum() or c in (' ', '-')]).strip()[:50]
            filename = f"{id_legge}_{safe_title}.pdf"
            filepath = os.path.join(OUTPUT_DIR, filename)
            url = f"{BASE_URL_LEGGE}{id_legge}"

            if os.path.exists(filepath):
                print(f"Già presente: {filename}")
                continue

            print(f"[{i+1}/{len(leggi)}] Elaboro {id_legge}...", end=" ")

            try:
                # 1. Vai alla pagina
                page.goto(url, timeout=60000)
                
                # 2. Pulizia della pagina via Javascript (rimuove menu, header, ecc.)
                # Questo script viene eseguito DENTRO il browser prima di stampare
                page.evaluate("""() => {
                    // Nascondi header, menu laterali, footer noti
                    const selectors = ['nav', 'header', 'footer', '.menu-laterale', '#header'];
                    selectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => el.style.display = 'none');
                    });
                    // Forza il contenuto al 100% width se necessario
                    const content = document.querySelector('#content') || document.body;
                    content.style.width = '100%';
                    content.style.margin = '0';
                }""")

                # 3. Salva come PDF
                page.pdf(path=filepath, format="A4", margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"})
                print("-> PDF Creato.")
                
            except Exception as e:
                print(f"-> Errore: {e}")

            time.sleep(0.5)

        browser.close()

if __name__ == "__main__":
    main()