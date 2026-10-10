"""
Chatterbox Multilingual TTS Engine — Ses Klonlamalı
Kullanım: narrator_voice.wav referans alınarak Türkçe TTS üretir.
"""

import os
import asyncio
import logging
import torch
import torchaudio as ta
from pathlib import Path

logger = logging.getLogger(__name__)


class ChatterboxTTSEngine:
    """
    Tek sabit anlatıcı sesiyle klonlamalı TTS motoru.
    Model startup'ta yüklenir, her istekte referans .wav kullanılır.
    """

    def __init__(
        self,
        narrator_voice_path: str,
        language_id: str = "tr",
        device: str | None = None,
    ):
        """
        Args:
            narrator_voice_path: Referans ses dosyası (.wav, 10–30 sn önerilir)
            language_id: Hedef dil kodu (varsayılan: "tr")
            device: "cuda" | "cpu" | None (None ise otomatik seçilir)
        """
        self.narrator_voice_path = Path(narrator_voice_path)
        self.language_id = language_id
        self._lock = asyncio.Lock()
        self.model = None
        self.sr = None

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self._validate_voice_file()

    def _validate_voice_file(self) -> None:
        if not self.narrator_voice_path.exists():
            raise FileNotFoundError(
                f"Referans ses bulunamadı: {self.narrator_voice_path}\n"
                f"Çözüm: voice_recorder.html ile ses kaydedin, "
                f"narrator_voice.wav olarak kaydedin."
            )

        # Süre kontrolü
        try:
            info = ta.info(str(self.narrator_voice_path))
            duration_sec = info.num_frames / info.sample_rate
            if duration_sec < 5:
                logger.warning(
                    f"Referans ses çok kısa: {duration_sec:.1f}s "
                    f"(önerilen: 10–30s). Klonlama kalitesi düşük olabilir."
                )
            elif duration_sec > 60:
                logger.warning(
                    f"Referans ses çok uzun: {duration_sec:.1f}s "
                    f"(önerilen: 10–30s). Model ilk 30s kullanır."
                )
            else:
                logger.info(f"Referans ses: {duration_sec:.1f}s — OK")
        except Exception as e:
            logger.warning(f"Ses dosyası kontrol edilemedi: {e}")

    def load(self) -> None:
        """
        Modeli belleğe yükle. main.py startup'ında çağır.
        İlk yüklemede ~1–2GB HuggingFace'den indirilir.
        """
        if self.model is not None:
            return

        logger.info(f"Chatterbox Multilingual yükleniyor... (device={self.device})")

        try:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            self.model = ChatterboxMultilingualTTS.from_pretrained(device=self.device)
            self.sr = self.model.sr
            logger.info(f"Model yüklendi. Sample rate: {self.sr}Hz")
        except ImportError:
            raise ImportError(
                "chatterbox-tts paketi bulunamadı.\n"
                "Kur: pip install chatterbox-tts"
            )
        except Exception as e:
            logger.error(f"Model yüklenemedi: {e}")
            raise

    def generate_speech(
        self,
        text: str,
        output_path: str,
        language_id: str | None = None,
        audio_prompt_path: str | None = None,
    ) -> str:
        """
        Metni seslendirip dosyaya yazar.

        Args:
            text: Seslendirilecek metin
            output_path: Çıktı .wav dosya yolu
            language_id: Dil kodu (None ise init'teki kullanılır)
            audio_prompt_path: Farklı referans ses (None ise narrator_voice.wav)

        Returns:
            output_path (başarılıysa)
        """
        if self.model is None:
            raise RuntimeError("Model yüklenmedi. Önce load() çağır.")

        lang = language_id or self.language_id
        ref_wav = audio_prompt_path or str(self.narrator_voice_path)

        if not text or not text.strip():
            raise ValueError("Boş metin seslendirilemez.")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            wav = self.model.generate(
                text.strip(),
                audio_prompt_path=ref_wav,
                language_id=lang,
            )
            ta.save(str(output_path), wav, self.sr)
            logger.info(f"TTS üretildi: {output_path}")
            return str(output_path)

        except Exception as e:
            logger.error(f"TTS üretilemedi: {e}\nMetin: {text[:80]}...")
            raise

    async def generate_speech_async(
        self,
        text: str,
        output_path: str,
        language_id: str | None = None,
    ) -> str:
        """
        Thread-safe async wrapper. FastAPI endpoint'lerinde kullan.
        Eş zamanlı istekleri sıraya koyar (model paylaşım sorunu önlenir).
        """
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self.generate_speech,
                text,
                output_path,
                language_id,
                None,
            )


# ---------------------------------------------------------------------------
# Hızlı test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    narrator_wav = sys.argv[1] if len(sys.argv) > 1 else "narrator_voice.wav"
    output_wav   = sys.argv[2] if len(sys.argv) > 2 else "test_output.wav"
    test_text    = (
        sys.argv[3]
        if len(sys.argv) > 3
        else "16. yüzyıl. Osmanlı İmparatorluğu'nun kalbi, Kostantiniyye'de atıyor."
    )

    engine = ChatterboxTTSEngine(narrator_voice_path=narrator_wav)
    engine.load()
    result = engine.generate_speech(test_text, output_wav)
    print(f"OK → {result}")
