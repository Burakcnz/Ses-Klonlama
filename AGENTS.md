# ses_klon - Ses Klonlama Sistemi

## Proje Tanimi
Chatterbox TTS ile ses klonlama sistemi. Verilen ses orneklerinden yeni sesler uretir.

## Tech Stack
- **Backend:** Python FastAPI
- **TTS:** Chatterbox TTS
- **ML:** PyTorch
- **Language:** Python 3.11+

## Yapilandirma
```
ses_klon/
├── main.py            # FastAPI uygulamasi
├── services/          # TTS servisleri
├── models/            # Model dosyalari
├── samples/           # Ses ornekleri
├── outputs/           # Uretilen sesler
├── requirements.txt
└── AGENTS.md
```

## Build & Run
```bash
pip install -r requirements.txt
python main.py        # FastAPI sunucusu baslat
# veya
uvicorn main:app --reload
```

## Onemli Kurallar
- GPU bellek yonetimine dikkat et - modeli memory'de tut
- Large batch islemlerinde streaming response kullan
- Ses dosyalarini samples/ ve outputs/ icinde ayri tut
- PyTorch modelini lazy loading ile yukle

## Token Tasarruf (ZORUNLU)
- Dosya okurken sadece gerekli satirlari oku
- Gereksiz tekrar aciklama yapma
- Uzun ciktilari ozetle
- Plan modunu kullan: once plan olustur, sonra uygula
