# -*- coding: utf-8 -*-
"""
Ses Klonlama Uygulaesi - Tek Dosya
=====================================
Kullanim: python app.py
- Gerekli driver/paket kontrolu yapar, eksikleri kurar
- FastAPI sunucuyu baslatir (http://localhost:9000)
- Tarayiciyi otomatik acar
"""

# ============================================================
# STDLIB importlar - her zaman guvenli
# ============================================================
import sys
import os
import re
import json
import subprocess
import shutil
import importlib
import asyncio
import logging
import uuid
import time
import datetime
import tempfile
from pathlib import Path

# ============================================================
# BOLUM 0 - Bootstrap fonksiyonlari (sadece stdlib kullanir)
# ============================================================

def _run(cmd, check=False, capture=False):
    kwargs = {"shell": True}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    result = subprocess.run(cmd, **kwargs)
    if check and result.returncode != 0:
        print(f"[HATA] Komut basarisiz: {cmd}")
    return result


def _pip_install(packages, index_url=None):
    pkg_str = " ".join(packages)
    index_arg = f"--index-url {index_url}" if index_url else ""
    print(f"[...] pip install {pkg_str}")
    _run(f'"{sys.executable}" -m pip install {pkg_str} {index_arg}', check=True)


def _is_importable(module):
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        return False


def bootstrap():
    print("=" * 50)
    print("  SES KLONLAMA - Sistem Kontrolu")
    print("=" * 50)

    # 1. Python versiyon kontrolu
    major, minor = sys.version_info.major, sys.version_info.minor
    print(f"[OK] Python {major}.{minor}")
    if major < 3 or (major == 3 and minor < 10):
        print("[HATA] Python 3.10+ gerekli.")
        sys.exit(1)

    # 2. pip yukselt
    print("[...] pip guncelleniyor...")
    _run(f'"{sys.executable}" -m pip install --upgrade pip -q', check=False)
    print("[OK] pip guncel")

    # 3. CUDA kontrol
    cuda_available = False
    result = _run("nvidia-smi", capture=True)
    if result.returncode == 0:
        cuda_available = True
        output = result.stdout.decode(errors="ignore")
        cuda_ver = "?"
        for line in output.splitlines():
            if "CUDA Version" in line:
                parts = line.strip().split("CUDA Version:")
                if len(parts) > 1:
                    cuda_ver = parts[1].strip().split()[0]
                break
        print(f"[OK] NVIDIA GPU bulundu - CUDA {cuda_ver}")
    else:
        print("[i] NVIDIA GPU bulunamadi - CPU modu kullanilacak")

    # 4. FFmpeg kontrol / kur
    ffmpeg_ok = _run("ffmpeg -version", capture=True).returncode == 0
    if ffmpeg_ok:
        print("[OK] FFmpeg mevcut")
    else:
        print("[...] FFmpeg yukleniyor (winget)...")
        r = _run("winget install --id Gyan.FFmpeg -e --silent --accept-package-agreements --accept-source-agreements")
        if r.returncode == 0:
            print("[OK] FFmpeg yuklendi")
        else:
            print("[!] FFmpeg winget ile kurulamadi. Manuel: https://ffmpeg.org/download.html")

    # 5. Python paketleri
    print("\n[...] Python paketleri kontrol ediliyor...")

    # torch / torchaudio
    torch_ok = _is_importable("torch")
    torchaudio_ok = _is_importable("torchaudio")
    if not torch_ok or not torchaudio_ok:
        if cuda_available:
            print("[...] PyTorch CUDA 11.8 yukleniyor...")
            _pip_install(["torch", "torchaudio"], index_url="https://download.pytorch.org/whl/cu118")
        else:
            print("[...] PyTorch CPU yukleniyor...")
            _pip_install(["torch", "torchaudio"], index_url="https://download.pytorch.org/whl/cpu")
    else:
        print("[OK] torch / torchaudio mevcut")

    # Diger paketler
    packages_needed = []
    checks = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "multipart": "python-multipart",
        "chatterbox": "chatterbox-tts",
        "pedalboard": "pedalboard",
        "librosa": "librosa",
        "pyrubberband": "pyrubberband",
    }
    for module, pkg in checks.items():
        if not _is_importable(module):
            packages_needed.append(pkg)
        else:
            print(f"[OK] {pkg} mevcut")

    if packages_needed:
        print(f"[...] Yukleniyor: {', '.join(packages_needed)}")
        _pip_install(packages_needed)

    print("\n[OK] Tum bagimliliklar hazir!\n")
    return cuda_available


# ============================================================
# BOLUM 1 - Ucuncu parti importlar (serve modunda yuklenir)
# Hem "python app.py --serve" hem uvicorn module import'u icin
# env var kullanilir.
# ============================================================

_SERVE_MODE = os.environ.get("SES_KLON_SERVE") in ("1", "panel")
_PANEL_MODE = os.environ.get("SES_KLON_SERVE") == "panel"

if _SERVE_MODE:
    from fastapi import FastAPI, UploadFile, File, Form, HTTPException
    from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
    from pydantic import BaseModel
    import torch
    import torchaudio as ta
    from text_normalizer import normalize, detect_emotion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ses_klon")

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"
VOICES_DIR = BASE_DIR / "voices"
HISTORY_DIR = BASE_DIR / "history"
HISTORY_META = HISTORY_DIR / "meta.json"
ASSETS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
VOICES_DIR.mkdir(exist_ok=True)
HISTORY_DIR.mkdir(exist_ok=True)
NARRATOR_VOICE = ASSETS_DIR / "narrator_voice.wav"


def _clean_old_outputs(max_age_days: int = 7):
    """Output klasöründeki eski geçici dosyaları sil."""
    cutoff = time.time() - max_age_days * 86400
    for f in OUTPUT_DIR.glob("result_*.wav"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except Exception:
            pass


def _load_history() -> list:
    if HISTORY_META.exists():
        try:
            return json.loads(HISTORY_META.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_history(items: list):
    HISTORY_META.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

# Aktif ses dosyasi (runtime'da degisebilir)
_active_voice: Path = NARRATOR_VOICE


# ============================================================
# BOLUM 2 - ChatterboxTTSEngine (chatterbox_tts_engine.py'den)
# ============================================================

if _SERVE_MODE:
    from chatterbox_tts_engine import ChatterboxTTSEngine


# ============================================================
# BOLUM 3 - FastAPI uygulamasi (sadece --serve modunda)
# ============================================================

if _SERVE_MODE:

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(application):
        global tts_engine, model_loading, model_error
        model_loading = True
        _clean_old_outputs()

        async def _load():
            global tts_engine, model_loading, model_error
            try:
                import torch as _torch
                engine = object.__new__(ChatterboxTTSEngine)
                engine.narrator_voice_path = NARRATOR_VOICE
                engine.language_id = "tr"
                engine._lock = asyncio.Lock()
                engine.model = None
                engine.sr = None
                engine.device = "cuda" if _torch.cuda.is_available() else "cpu"
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, engine.load)
                tts_engine = engine
                logger.info("TTS motoru hazir.")
            except Exception as e:
                model_error = str(e)
                logger.error(f"Model yuklenemedi: {e}")
            finally:
                model_loading = False

        asyncio.create_task(_load())
        yield

    app = FastAPI(title="Ses Klonlama", version="2.0", lifespan=lifespan)

    tts_engine = None
    model_loading = False
    model_error = None
    active_voice: Path = NARRATOR_VOICE

    from pydantic import field_validator

    class TTSRequest(BaseModel):
        text: str
        language: str = "tr"
        exaggeration: float = 0.5
        cfg_weight: float = 0.5
        temperature: float = 0.8
        auto_emotion: bool = True
        speed: float = 1.0
        effects_chain: list = []
        save_name: str = ""

        @field_validator("text")
        @classmethod
        def text_not_empty(cls, v):
            v = v.strip()
            if not v:
                raise ValueError("Metin bos olamaz.")
            if len(v) > 8000:
                raise ValueError("Metin 8000 karakterden uzun olamaz.")
            return v

        @field_validator("speed")
        @classmethod
        def speed_range(cls, v):
            return max(0.5, min(2.0, v))

        @field_validator("exaggeration", "cfg_weight", "temperature")
        @classmethod
        def clamp_float(cls, v):
            return max(0.0, min(1.0, v))

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(content=HTML_CONTENT)

    @app.get("/status")
    async def status():
        return JSONResponse({
            "model_ready": tts_engine is not None and tts_engine.model is not None,
            "model_loading": model_loading,
            "model_error": model_error,
            "voice_ready": active_voice.exists(),
            "active_voice": active_voice.stem,
            "device": tts_engine.device if tts_engine else "unknown",
        })

    # ---- Gecmis (History) Endpoint'leri ----

    @app.get("/history")
    async def get_history():
        return JSONResponse({"items": _load_history()})

    @app.post("/history/{gen_id}/rename")
    async def rename_history(gen_id: str, data: dict):
        new_name = re.sub(r'[^\w\-]', '_', data.get("name", "").strip())
        if not new_name:
            raise HTTPException(status_code=400, detail="Gecersiz isim.")
        items = _load_history()
        for item in items:
            if item["id"] == gen_id:
                item["name"] = new_name
                _save_history(items)
                return JSONResponse({"ok": True, "name": new_name})
        raise HTTPException(status_code=404, detail="Kayit bulunamadi.")

    @app.delete("/history/{gen_id}")
    async def delete_history(gen_id: str):
        items = _load_history()
        to_delete = next((i for i in items if i["id"] == gen_id), None)
        if not to_delete:
            raise HTTPException(status_code=404, detail="Kayit bulunamadi.")
        wav_path = OUTPUT_DIR / to_delete["file"]
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass
        items = [i for i in items if i["id"] != gen_id]
        _save_history(items)
        return JSONResponse({"ok": True})

    @app.get("/history/{gen_id}/download")
    async def download_history(gen_id: str):
        items = _load_history()
        entry = next((i for i in items if i["id"] == gen_id), None)
        if not entry:
            raise HTTPException(status_code=404, detail="Kayit bulunamadi.")
        wav_path = OUTPUT_DIR / entry["file"]
        if not wav_path.exists():
            raise HTTPException(status_code=410, detail="Dosya artik mevcut degil.")
        return FileResponse(path=str(wav_path), media_type="audio/wav",
                            filename=f"{entry['name']}.wav")

    @app.get("/effects-info")
    async def effects_info():
        from audio_effects import get_presets, EFFECT_REGISTRY
        return JSONResponse({"presets": get_presets(), "registry": {
            k: {"label": v["label"], "params": v["params"]}
            for k, v in EFFECT_REGISTRY.items()
        }})

    @app.get("/voices")
    async def list_voices():
        voices = []
        for f in sorted(VOICES_DIR.glob("*.wav")):
            try:
                info = ta.info(str(f))
                dur = round(info.num_frames / info.sample_rate, 1)
            except Exception:
                dur = 0
            voices.append({"name": f.stem, "file": f.name, "duration_sec": dur,
                           "active": f == active_voice})
        # narrator_voice.wav de listede olsun
        if NARRATOR_VOICE.exists():
            try:
                info = ta.info(str(NARRATOR_VOICE))
                dur = round(info.num_frames / info.sample_rate, 1)
            except Exception:
                dur = 0
            voices.insert(0, {"name": "narrator_voice", "file": "narrator_voice.wav",
                               "duration_sec": dur, "active": NARRATOR_VOICE == active_voice})
        return JSONResponse({"voices": voices})

    _MIME_TO_EXT: dict[str, str] = {
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/flac": ".flac",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/vnd.wave": ".wav",
    }

    @app.post("/upload-voice")
    async def upload_voice(file: UploadFile = File(...),
                           voice_name: str = Form("")):
        global active_voice
        content = await file.read()
        if len(content) < 1000:
            raise HTTPException(status_code=400, detail="Ses dosyasi cok kucuk.")
        # Isim belirlenmemisse narrator_voice.wav olarak kaydet
        safe_name = re.sub(r'[^\w\-]', '_', voice_name.strip()) if voice_name.strip() else ""
        if safe_name and safe_name != "narrator_voice":
            save_path = VOICES_DIR / f"{safe_name}.wav"
        else:
            save_path = NARRATOR_VOICE

        # Gelen format ne olursa olsun duzgun WAV'a donustur
        content_type = (file.content_type or "").split(";")[0].strip()
        ext = _MIME_TO_EXT.get(content_type, ".webm")
        tmp_path: str | None = None
        converted = False
        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            # 1. Oncelik: ffmpeg (WebM/OGG/MP3 icin en guvenilir)
            ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
            result = subprocess.run(
                [ffmpeg_bin, "-y", "-i", tmp_path, "-f", "wav", str(save_path)],
                capture_output=True,
            )
            if result.returncode == 0:
                converted = True
                logger.info(f"Ses ffmpeg ile donusturuldu: {content_type} -> WAV")
            else:
                err_msg = result.stderr.decode(errors="replace")[-200:]
                logger.warning(f"ffmpeg donusturme hatasi: {err_msg}")

            # 2. Fallback: torchaudio (WAV/FLAC icin yeterli)
            if not converted:
                waveform, sr = ta.load(tmp_path)
                ta.save(str(save_path), waveform, sr)
                converted = True
                logger.info(f"Ses torchaudio ile donusturuldu: {content_type} -> WAV, sr={sr}")

        except Exception as conv_err:
            logger.warning(f"Ses donusturme hatasi ({conv_err}), ham bytes kaydediliyor")
            if not converted:
                save_path.write_bytes(content)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        active_voice = save_path
        if tts_engine:
            tts_engine.narrator_voice_path = save_path
        try:
            info = ta.info(str(save_path))
            duration = round(info.num_frames / info.sample_rate, 1)
        except Exception:
            duration = 0
        return JSONResponse({"ok": True, "duration_sec": duration,
                             "name": save_path.stem, "file": save_path.name})

    @app.post("/set-active-voice")
    async def set_active_voice(data: dict):
        global active_voice
        name = data.get("name", "")
        if name == "narrator_voice" and NARRATOR_VOICE.exists():
            path = NARRATOR_VOICE
        else:
            path = VOICES_DIR / f"{name}.wav"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Ses bulunamadi.")
        active_voice = path
        if tts_engine:
            tts_engine.narrator_voice_path = path
        return JSONResponse({"ok": True, "active": name})

    @app.delete("/delete-voice/{name}")
    async def delete_voice(name: str):
        global active_voice
        path = VOICES_DIR / f"{name}.wav"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Ses bulunamadi.")
        if active_voice == path:
            active_voice = NARRATOR_VOICE
            if tts_engine:
                tts_engine.narrator_voice_path = NARRATOR_VOICE
        path.unlink()
        return JSONResponse({"ok": True})

    @app.post("/generate-tts")
    async def generate_tts(req: TTSRequest):
        if tts_engine is None or tts_engine.model is None:
            if model_loading:
                raise HTTPException(status_code=503, detail="Model yukleniyor, bekleyin...")
            raise HTTPException(status_code=503, detail=f"Model yuklenemedi: {model_error or '?'}")
        if not active_voice.exists():
            raise HTTPException(status_code=400, detail="Once referans ses yukleyin.")
        if not req.text or not req.text.strip():
            raise HTTPException(status_code=400, detail="Metin bos olamaz.")

        from text_normalizer import normalize as _normalize, detect_emotion as _detect_emotion

        # Metni normalize et
        clean_text = _normalize(req.text)
        logger.info(f"Normalize: {req.text[:60]!r} -> {clean_text[:60]!r}")

        # Duygu tespiti — manuel override yoksa otomatik
        if req.auto_emotion:
            exaggeration, cfg_weight = _detect_emotion(req.text)
            # Daha dogal/sakin ses icin hafif offset
            exaggeration = max(0.2, exaggeration - 0.1)
            cfg_weight = min(0.9, cfg_weight + 0.1)
            logger.info(f"Otomatik duygu: exaggeration={exaggeration}, cfg_weight={cfg_weight}")
        else:
            exaggeration = req.exaggeration
            cfg_weight = req.cfg_weight
            logger.info(f"Manuel duygu: exaggeration={exaggeration}, cfg_weight={cfg_weight}")

        gen_id = uuid.uuid4().hex[:12]
        output_file = OUTPUT_DIR / f"result_{gen_id}.wav"
        try:
            tts_engine._validate_voice_file()

            def _generate():
                import numpy as _np
                from text_normalizer import split_sentences as _split
                ref_wav = str(active_voice)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                sr = tts_engine.sr

                def _seg_norm(wav_t, target_db=-18.0):
                    """Peak normalize."""
                    peak = wav_t.abs().max()
                    if peak < 1e-6:
                        return wav_t
                    return wav_t * (10 ** (target_db / 20.0) / peak)

                def _silence_trim(arr: _np.ndarray, threshold=0.002, pad_ms=25) -> _np.ndarray:
                    """Basit threshold ile bas/son sessizligi kirp."""
                    abs_arr = _np.abs(arr)
                    nonsilent = _np.where(abs_arr > threshold)[0]
                    if len(nonsilent) == 0:
                        return arr
                    pad = int(sr * pad_ms / 1000)
                    start = max(0, nonsilent[0] - pad)
                    end = min(len(arr), nonsilent[-1] + pad)
                    return arr[start:end]

                def _fade_out(arr: _np.ndarray, fade_ms: int = 200) -> _np.ndarray:
                    """Son N ms'i smooth fade-out ile bitir — trailing artifact gizler."""
                    fade_n = min(int(sr * fade_ms / 1000), len(arr) // 4)
                    if fade_n < 2:
                        return arr
                    result = arr.copy()
                    fade = _np.linspace(1.0, 0.0, fade_n, dtype=_np.float32)
                    result[-fade_n:] = result[-fade_n:] * fade
                    return result

                def _crossfade_concat(chunks: list, fade_ms=60) -> _np.ndarray:
                    """Numpy crossfade ile segment birlestirme."""
                    if not chunks:
                        return _np.array([], dtype=_np.float32)
                    if len(chunks) == 1:
                        return chunks[0]
                    fade_n = int(sr * fade_ms / 1000)
                    result = chunks[0].copy()
                    for chunk in chunks[1:]:
                        if len(chunk) == 0:
                            continue
                        overlap = min(fade_n, len(result), len(chunk))
                        if overlap > 1:
                            fo = _np.linspace(1.0, 0.0, overlap, dtype=_np.float32)
                            fi = _np.linspace(0.0, 1.0, overlap, dtype=_np.float32)
                            result[-overlap:] = result[-overlap:] * fo + chunk[:overlap] * fi
                            result = _np.concatenate([result, chunk[overlap:]])
                        else:
                            result = _np.concatenate([result, chunk])
                    return result

                # Metin bolme: kisa segmentleri birlestir
                raw_sentences = _split(clean_text)
                if not raw_sentences:
                    raw_sentences = [(clean_text, 0)]

                # Cok kisa segmentleri birlestir; noktalama ve pause'u koru
                merged, buf_text, buf_pause = [], "", 0
                for seg, pause_ms in raw_sentences:
                    seg = seg.strip()
                    if not seg:
                        continue
                    # 15 karakterden kisa segmentleri oncekine ekle
                    if buf_text and len(seg) < 15:
                        # Onceki segmentin sonu noktalama isareti ise kaldirma
                        buf_text = buf_text.rstrip('.,') + ' ' + seg
                        buf_pause = pause_ms
                    else:
                        if buf_text:
                            merged.append((buf_text, buf_pause))
                        buf_text, buf_pause = seg, pause_ms
                if buf_text:
                    merged.append((buf_text, buf_pause))

                np_parts = []
                for seg, pause_ms in merged:
                    try:
                        wav_t = tts_engine.model.generate(
                            seg,
                            audio_prompt_path=ref_wav,
                            language_id=req.language,
                            exaggeration=exaggeration,
                            cfg_weight=cfg_weight,
                            temperature=req.temperature,
                        )
                    except TypeError:
                        # Bazi model surumlerinde bazi parametreler desteklenmez
                        try:
                            wav_t = tts_engine.model.generate(
                                seg,
                                audio_prompt_path=ref_wav,
                                language_id=req.language,
                                exaggeration=exaggeration,
                                cfg_weight=cfg_weight,
                            )
                        except TypeError:
                            wav_t = tts_engine.model.generate(
                                seg,
                                audio_prompt_path=ref_wav,
                            )
                    wav_t = _seg_norm(wav_t)
                    arr = wav_t.squeeze(0).cpu().numpy().astype(_np.float32)
                    arr = _silence_trim(arr)
                    arr = _fade_out(arr, fade_ms=200)
                    np_parts.append(arr)
                    if pause_ms > 0:
                        np_parts.append(_np.zeros(int(sr * pause_ms / 1000), dtype=_np.float32))

                combined_np = _crossfade_concat(np_parts)

                # Final fade-out: en son kalan artifact'leri temizle
                combined_np = _fade_out(combined_np, fade_ms=120)

                # Son normalize
                peak = _np.abs(combined_np).max()
                if peak > 1e-6:
                    combined_np = combined_np * (0.89 / peak)

                # Pitch-koruyucu hiz degisimi
                if abs(req.speed - 1.0) >= 0.01:
                    rate = float(req.speed)
                    stretched = None

                    # 1. pedalboard.time_stretch (en kaliteli, CLI gerektirmez)
                    try:
                        from pedalboard import time_stretch as _pb_ts
                        _inp = combined_np.reshape(1, -1)
                        _out = _pb_ts(
                            _inp,
                            sr,
                            stretch_factor=rate,
                            pitch_shift_in_semitones=0.0,
                        )
                        stretched = _out.reshape(-1)
                        logger.info(f"Hiz: {rate}x (pedalboard.time_stretch)")
                    except Exception as _e:
                        logger.warning(f"pedalboard.time_stretch hatasi: {_e}")

                    # 2. librosa — daha buyuk n_fft ile artifact azaltilir
                    if stretched is None:
                        try:
                            import librosa
                            n_fft = 4096
                            hop = n_fft // 8
                            stretched = librosa.effects.time_stretch(
                                combined_np, rate=rate,
                                n_fft=n_fft, hop_length=hop,
                            )
                            logger.info(f"Hiz: {rate}x (librosa n_fft={n_fft})")
                        except Exception as _e2:
                            logger.warning(f"librosa time_stretch hatasi: {_e2}")

                    # 3. torchaudio resample fallback
                    if stretched is None:
                        combined_t = torch.from_numpy(combined_np).unsqueeze(0)
                        target_sr = int(sr / rate)
                        resampler = ta.transforms.Resample(sr, target_sr)
                        stretched = resampler(combined_t).squeeze(0).cpu().numpy()
                        logger.warning(f"Hiz: {rate}x (resample fallback)")

                    combined_np = stretched.astype(_np.float32)

                # Ses efektleri (pedalboard)
                if req.effects_chain:
                    try:
                        from audio_effects import apply_effects as _apply_fx
                        combined_np = _apply_fx(combined_np, sr, req.effects_chain)
                    except Exception as _fx_err:
                        logger.warning(f"Efekt uygulanamadi: {_fx_err}")

                combined_t = torch.from_numpy(combined_np).unsqueeze(0)
                ta.save(str(output_file), combined_t, sr)
                return str(output_file)

            loop = asyncio.get_running_loop()
            async with tts_engine._lock:
                await loop.run_in_executor(None, _generate)

        except Exception as e:
            logger.error(f"TTS hatasi: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        # Gecmis kaydini guncelle
        try:
            info = ta.info(str(output_file))
            dur = round(info.num_frames / info.sample_rate, 1)
        except Exception:
            dur = 0
        save_name = re.sub(r'[^\w\-]', '_', req.save_name.strip()) if req.save_name.strip() else ""
        hist_items = _load_history()
        hist_entry = {
            "id": gen_id,
            "name": save_name or f"uretim_{datetime.datetime.now().strftime('%H%M%S')}",
            "text_preview": req.text[:80],
            "duration_sec": dur,
            "language": req.language,
            "file": output_file.name,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        hist_items.insert(0, hist_entry)
        hist_items = hist_items[:100]  # maks 100 kayit
        _save_history(hist_items)

        resp = FileResponse(
            path=str(output_file),
            media_type="audio/wav",
            filename="klonlanan_ses.wav",
        )
        resp.headers["X-Generation-Id"] = gen_id
        resp.headers["X-Duration"] = str(dur)
        return resp


# ============================================================
# BOLUM 4 - Gomulu HTML Arayuzu
# ============================================================

HTML_CONTENT = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ses Klonlama</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Courier New', monospace;
    background: #0a0a0a;
    color: #e8e8e0;
    min-height: 100vh;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding: 2rem;
  }
  .container { width: 100%; max-width: 600px; }
  .header { margin-bottom: 2rem; }
  .header h1 { font-size: 11px; letter-spacing: 0.2em; text-transform: uppercase; color: #555; margin-bottom: 0.4rem; }
  .header p { font-size: 20px; color: #e8e8e0; line-height: 1.3; }

  .tabs { display: flex; gap: 0; margin-bottom: 2rem; border-bottom: 1px solid #222; }
  .tab-btn {
    font-family: 'Courier New', monospace; font-size: 11px; letter-spacing: 0.15em;
    text-transform: uppercase; padding: 10px 20px; border: none; background: transparent;
    color: #444; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; transition: all 0.15s;
  }
  .tab-btn:hover { color: #888; }
  .tab-btn.active { color: #e8e8e0; border-bottom-color: #e05555; }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  .script-box {
    background: #111; border: 1px solid #1e1e1e; border-left: 3px solid #333;
    border-radius: 3px; padding: 1.25rem; margin-bottom: 1.5rem;
  }
  .script-box .label { font-size: 10px; letter-spacing: 0.2em; color: #444; text-transform: uppercase; margin-bottom: 0.75rem; }
  .script-box p { font-size: 13px; color: #c8c8c0; line-height: 1.7; }

  .waveform-container {
    background: #111; border: 1px solid #222; border-radius: 4px;
    height: 72px; margin-bottom: 1.25rem; position: relative;
    overflow: hidden; display: flex; align-items: center; justify-content: center;
  }
  canvas#waveform { width: 100%; height: 100%; display: block; }
  .waveform-idle { position: absolute; font-size: 11px; color: #333; letter-spacing: 0.1em; }

  .status-bar {
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 1.25rem; font-size: 11px; color: #555; letter-spacing: 0.05em;
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #333; flex-shrink: 0; transition: background 0.2s; }
  .dot.recording { background: #e05555; animation: pulse 1s infinite; }
  .dot.ready { background: #55a055; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .timer { margin-left: auto; font-size: 13px; color: #666; font-variant-numeric: tabular-nums; }

  .controls { display: flex; gap: 8px; margin-bottom: 1.25rem; }

  button {
    font-family: 'Courier New', monospace; font-size: 11px; letter-spacing: 0.1em;
    text-transform: uppercase; padding: 11px 18px; border: 1px solid #333;
    border-radius: 3px; background: transparent; color: #aaa; cursor: pointer;
    transition: all 0.15s; flex: 1;
  }
  button:hover:not(:disabled) { border-color: #666; color: #e8e8e0; }
  button:active:not(:disabled) { transform: scale(0.98); }
  button:disabled { opacity: 0.25; cursor: not-allowed; }
  button.primary { border-color: #e05555; color: #e05555; }
  button.primary:hover:not(:disabled) { background: #e05555; color: #0a0a0a; }
  button.primary.active { background: #e05555; color: #0a0a0a; }
  button.success { border-color: #55a055; color: #55a055; }
  button.success:hover:not(:disabled) { background: #55a055; color: #0a0a0a; }
  button.accent { border-color: #5588cc; color: #5588cc; }
  button.accent:hover:not(:disabled) { background: #5588cc; color: #0a0a0a; }

  .quality-bar { margin-bottom: 1.25rem; }
  .quality-bar .label { font-size: 10px; color: #444; letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 6px; display: flex; justify-content: space-between; }
  .bar-bg { height: 3px; background: #1e1e1e; border-radius: 2px; overflow: hidden; }
  .bar-fill { height: 100%; width: 0%; background: #55a055; border-radius: 2px; transition: width 0.3s; }

  .audio-preview { display: none; margin-bottom: 1.25rem; }
  .audio-preview audio { width: 100%; filter: invert(1) hue-rotate(180deg); }

  .info-box {
    background: #0d150d; border: 1px solid #1a2e1a; border-radius: 3px;
    padding: 1rem; margin-bottom: 1.25rem; display: none;
  }
  .info-box .label { font-size: 10px; letter-spacing: 0.2em; color: #2a5a2a; text-transform: uppercase; margin-bottom: 0.5rem; }
  .info-box p { font-size: 12px; color: #55a055; line-height: 1.6; }

  .upload-progress { font-size: 11px; color: #666; margin-bottom: 1.25rem; letter-spacing: 0.05em; min-height: 16px; }
  .upload-progress.ok { color: #55a055; }
  .upload-progress.err { color: #e05555; }

  .checklist { list-style: none; margin-bottom: 1.5rem; }
  .checklist li { font-size: 11px; color: #333; padding: 4px 0; letter-spacing: 0.03em; display: flex; align-items: center; gap: 10px; transition: color 0.3s; }
  .checklist li.ok { color: #55a055; }
  .checklist li .ci { font-size: 11px; width: 14px; }

  .field { margin-bottom: 1.5rem; }
  .field label { display: block; font-size: 10px; letter-spacing: 0.2em; color: #444; text-transform: uppercase; margin-bottom: 8px; }
  .field textarea {
    width: 100%; background: #111; border: 1px solid #222; border-radius: 3px;
    color: #e8e8e0; font-family: 'Courier New', monospace; font-size: 13px;
    padding: 12px; resize: vertical; min-height: 120px; outline: none; transition: border-color 0.15s;
  }
  .field textarea:focus { border-color: #444; }
  .field select {
    background: #111; border: 1px solid #222; border-radius: 3px;
    color: #e8e8e0; font-family: 'Courier New', monospace; font-size: 12px;
    padding: 10px 12px; outline: none; width: 100%;
  }

  .tts-status { font-size: 11px; color: #555; letter-spacing: 0.05em; margin-bottom: 1.25rem; min-height: 16px; }
  .tts-status.loading { color: #a08030; }
  .tts-status.ok { color: #55a055; }
  .tts-status.err { color: #e05555; }

  .result-box { display: none; margin-bottom: 1.5rem; }
  .result-box audio { width: 100%; filter: invert(1) hue-rotate(180deg); margin-bottom: 0.75rem; }
  .result-box .dl-row { display: flex; gap: 8px; }

  .gen-progress { display: none; margin-bottom: 1.5rem; }
  .gen-progress .gp-label { display: flex; justify-content: space-between; font-size: 10px; color: #555; letter-spacing: 0.08em; margin-bottom: 6px; }
  .gen-progress .gp-label .gp-elapsed { color: #a08030; font-variant-numeric: tabular-nums; }
  .gen-progress .gp-bar-bg { height: 2px; background: #1a1a1a; border-radius: 1px; overflow: hidden; }
  .gen-progress .gp-bar-fill { height: 100%; width: 0%; background: #5588cc; border-radius: 1px;
    animation: gpslide 2.2s ease-in-out infinite; }
  .gen-progress .gp-steps { margin-top: 10px; display: flex; flex-direction: column; gap: 4px; }
  .gen-progress .gp-step { font-size: 10px; color: #333; letter-spacing: 0.05em; display: flex; align-items: center; gap: 8px; transition: color 0.3s; }
  .gen-progress .gp-step.active { color: #a08030; }
  .gen-progress .gp-step.done { color: #55a055; }
  .gen-progress .gp-step .gp-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
  @keyframes gpslide { 0%{width:0%;margin-left:0} 50%{width:60%;margin-left:20%} 100%{width:0%;margin-left:100%} }

  .model-status-bar {
    font-size: 10px; letter-spacing: 0.1em; color: #333; margin-bottom: 1.5rem;
    padding: 8px 10px; background: #0d0d0d; border: 1px solid #1a1a1a; border-radius: 3px;
    display: flex; align-items: center; gap: 8px;
  }
  .model-status-bar .mdot { width: 6px; height: 6px; border-radius: 50%; background: #333; flex-shrink: 0; }
  .model-status-bar .mdot.ok { background: #55a055; }
  .model-status-bar .mdot.loading { background: #a08030; animation: pulse 1.5s infinite; }
  .model-status-bar .mdot.err { background: #e05555; }

  .tip { font-size: 10px; color: #333; line-height: 1.6; border-top: 1px solid #1a1a1a; padding-top: 1.25rem; }
  .tip strong { color: #555; }

  .history-list { list-style: none; }
  .history-item {
    display: grid; grid-template-columns: 1fr auto;
    gap: 6px; padding: 10px 0; border-bottom: 1px solid #111;
    font-size: 11px;
  }
  .history-item:last-child { border-bottom: none; }
  .history-item .hi-name { color: #e8e8e0; letter-spacing: 0.05em; margin-bottom: 3px; font-size: 12px; }
  .history-item .hi-meta { color: #444; font-size: 10px; letter-spacing: 0.03em; }
  .history-item .hi-text { color: #555; font-size: 10px; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 340px; }
  .history-item .hi-actions { display: flex; gap: 6px; align-items: flex-start; flex-shrink: 0; }
  .history-item .hi-actions button { flex: none; padding: 5px 9px; font-size: 10px; letter-spacing: 0; }
  .history-search { width: 100%; background: #111; border: 1px solid #222; border-radius: 3px;
    color: #e8e8e0; font-family: 'Courier New',monospace; font-size: 12px;
    padding: 9px 12px; outline: none; margin-bottom: 1rem; }
  .history-search:focus { border-color: #444; }
  .history-empty { font-size: 11px; color: #333; padding: 1.5rem 0; text-align: center; letter-spacing: 0.1em; }

  .name-dialog {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8);
    z-index: 100; align-items: center; justify-content: center;
  }
  .name-dialog.open { display: flex; }
  .name-dialog-box {
    background: #111; border: 1px solid #333; border-radius: 4px;
    padding: 1.5rem; width: 320px; max-width: 90vw;
  }
  .name-dialog-box .nd-title { font-size: 10px; letter-spacing: 0.2em; color: #555; text-transform: uppercase; margin-bottom: 1rem; }
  .name-dialog-box input { width: 100%; background: #0a0a0a; border: 1px solid #333; border-radius: 3px;
    color: #e8e8e0; font-family: 'Courier New',monospace; font-size: 13px;
    padding: 9px 12px; outline: none; margin-bottom: 1rem; }
  .name-dialog-box input:focus { border-color: #666; }
  .name-dialog-box .nd-row { display: flex; gap: 8px; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Chatterbox TTS</h1>
    <p>Ses Klonlama</p>
  </div>

  <div class="model-status-bar" id="model-status-bar">
    <div class="mdot loading" id="mdot"></div>
    <span id="model-status-text">Model yukleniyor...</span>
  </div>

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('record')">Ses Kaydi</button>
    <button class="tab-btn" onclick="switchTab('tts')">TTS Uret</button>
    <button class="tab-btn" onclick="switchTab('history')">Gecmis</button>
  </div>

  <!-- SEKME 1: Ses Kaydi -->
  <div class="tab-panel active" id="panel-record">

    <!-- Mod secici: Mikrofon / Dosya -->
    <div style="display:flex; gap:0; margin-bottom:1.5rem; border-bottom:1px solid #1a1a1a;">
      <button id="mode-btn-mic" onclick="switchInputMode('mic')"
        style="font-family:'Courier New',monospace; font-size:10px; letter-spacing:0.15em; text-transform:uppercase;
               padding:8px 16px; border:none; background:transparent; color:#e8e8e0; cursor:pointer;
               border-bottom:2px solid #e05555; margin-bottom:-1px;">&#127908; Mikrofon</button>
      <button id="mode-btn-file" onclick="switchInputMode('file')"
        style="font-family:'Courier New',monospace; font-size:10px; letter-spacing:0.15em; text-transform:uppercase;
               padding:8px 16px; border:none; background:transparent; color:#444; cursor:pointer;
               border-bottom:2px solid transparent; margin-bottom:-1px;">&#128190; Dosya Yukle</button>
    </div>

    <!-- MIK MODU -->
    <div id="mode-mic">
    <div class="script-box">
      <div class="label">Okuyacagin metin</div>
      <p>"Turkiye, batida Avrupa ile doguda Asya'nin kesistigi essiz bir cografyada yer almaktadir. Kadim medeniyetlerin izlerini tasiyan bu topraklar, binlerce yillik tarihi miras barindirmaktadir. Istanbul'un silueeti, Kapadokya'nin peri bacalari ve Ege'nin turkuaz sulari bu zenginligin gorunur yuzudur."</p>
    </div>

    <div class="waveform-container">
      <canvas id="waveform"></canvas>
      <span class="waveform-idle" id="idle-text">-- kaydi baslat --</span>
    </div>

    <div class="status-bar">
      <div class="dot" id="status-dot"></div>
      <span id="status-text">Hazir</span>
      <span class="timer" id="timer">00:00</span>
    </div>

    <div class="controls">
      <button class="primary" id="btn-record" onclick="toggleRecord()">Kayit Baslat</button>
      <button id="btn-play" disabled onclick="playRecording()">Dinle</button>
    </div>

    <div class="quality-bar">
      <div class="label"><span>Kayit kalitesi</span><span id="quality-pct">--</span></div>
      <div class="bar-bg"><div class="bar-fill" id="quality-fill"></div></div>
    </div>
    </div><!-- /mode-mic -->

    <!-- DOSYA MODU -->
    <div id="mode-file" style="display:none;">
      <div id="drop-zone"
        ondragover="event.preventDefault(); this.style.borderColor='#5588cc';"
        ondragleave="this.style.borderColor='#222';"
        ondrop="handleFileDrop(event)"
        style="border:2px dashed #222; border-radius:4px; padding:2.5rem 1rem; text-align:center;
               color:#444; font-size:11px; letter-spacing:0.1em; cursor:pointer; transition:border-color 0.2s;
               margin-bottom:1.25rem;"
        onclick="document.getElementById('file-input').click()">
        <div style="font-size:28px; margin-bottom:0.5rem;">&#128190;</div>
        <div>WAV / MP3 / FLAC / M4A surukle birak</div>
        <div style="margin-top:6px; color:#333;">ya da tikla sec</div>
      </div>
      <input type="file" id="file-input" accept=".wav,.mp3,.flac,.m4a,audio/*"
             style="display:none;" onchange="handleFileSelect(event)">
      <div id="file-preview" style="display:none;">
        <audio id="file-audio-player" controls style="width:100%; filter:invert(1) hue-rotate(180deg); margin-bottom:1rem;"></audio>
        <div id="file-info" style="font-size:11px; color:#555; margin-bottom:1rem;"></div>
      </div>
    </div><!-- /mode-file -->

    <div class="audio-preview" id="audio-preview">
      <audio id="audio-player" controls></audio>
    </div>

    <div id="save-panel" style="display:none; background:#111; border:1px solid #1e1e1e; border-radius:3px; padding:1rem; margin-bottom:1.25rem;">
      <div style="font-size:10px; letter-spacing:0.2em; color:#444; text-transform:uppercase; margin-bottom:8px;">Ses Kaydet</div>
      <input id="voice-name-input" type="text" placeholder="isim (orn: berk_sesi)" style="width:100%; background:#0a0a0a; border:1px solid #333; border-radius:3px; color:#e8e8e0; font-family:'Courier New',monospace; font-size:12px; padding:9px 12px; outline:none; margin-bottom:8px;">
      <div style="display:flex; gap:8px;">
        <button class="success" onclick="uploadVoice()" id="btn-save-voice">Kaydet ve Kullan</button>
        <button onclick="document.getElementById('save-panel').style.display='none'" style="flex:0.4;">Iptal</button>
      </div>
    </div>

    <div class="upload-progress" id="upload-progress"></div>

    <div class="info-box" id="download-section">
      <div class="label">Ses kaydedildi</div>
      <p id="voice-info">Ses sunucuya kaydedildi.</p>
    </div>

    <ul class="checklist" id="checklist">
      <li id="chk-duration"><span class="ci">o</span> Sure 10+ saniye</li>
      <li id="chk-noise"><span class="ci">o</span> Sessiz ortam</li>
      <li id="chk-complete"><span class="ci">o</span> Metin tamamlandi</li>
    </ul>

    <p class="tip"><strong>Ipucu:</strong> Dogal, sakin tempoda oku. Mikrofonu 20-30cm uzakta tut. 10-30 saniye arasi ideal.</p>
  </div>

  <!-- SEKME 2: TTS Uret -->
  <div class="tab-panel" id="panel-tts">
    <div class="field">
      <label>Referans Ses</label>
      <div style="display:flex; gap:8px; align-items:center;">
        <select id="voice-select" onchange="setActiveVoice(this.value)" style="flex:1; background:#111; border:1px solid #222; border-radius:3px; color:#e8e8e0; font-family:'Courier New',monospace; font-size:12px; padding:10px 12px; outline:none;">
          <option value="">-- ses yukleniyor --</option>
        </select>
        <button onclick="refreshVoices()" style="flex:0 0 auto; padding:10px 14px; font-size:18px;" title="Listeyi yenile">&#8635;</button>
      </div>
      <div id="voice-list-detail" style="margin-top:8px;"></div>
    </div>
    <div class="field">
      <label>Seslendirilecek metin</label>
      <textarea id="tts-text" placeholder="Buraya metni yazin..."></textarea>
    </div>
    <div class="field">
      <label>Konuşma Hızı</label>
      <div style="margin-bottom: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
          <span style="font-size: 11px; color: #666;">Hız</span>
          <span id="speed-value" style="font-size: 11px; color: #aaa;">1.0x</span>
        </div>
        <input type="range" id="speed-slider" min="0.5" max="1.5" step="0.05" value="1.0"
               oninput="document.getElementById('speed-value').textContent=parseFloat(this.value).toFixed(2)+'x'"
               style="width: 100%; height: 3px; background: #222; outline: none; -webkit-appearance: none; cursor: pointer;">
        <div style="display: flex; justify-content: space-between; font-size: 9px; color: #333; margin-top: 2px;"><span>yavaş</span><span>normal</span><span>hızlı</span></div>
      </div>
    </div>
    <div class="field">
      <label>Duygu Kontrolü</label>
      <div style="margin-bottom: 8px;">
        <label style="font-size: 10px; color: #555; display: flex; align-items: center; gap: 6px; cursor: pointer;">
          <input type="checkbox" id="auto-emotion" checked onchange="toggleEmotionSliders()" style="margin: 0;">
          Otomatik duygu tespiti (!, ?, ... analiz edilir)
        </label>
      </div>
      <div id="emotion-sliders" style="display: none;">
        <div style="margin-bottom: 12px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-size: 11px; color: #666;">Vurgu / Exaggeration</span>
            <span id="exaggeration-value" style="font-size: 11px; color: #aaa;">0.50</span>
          </div>
          <input type="range" id="exaggeration-slider" min="0.1" max="1.0" step="0.05" value="0.5"
                 oninput="document.getElementById('exaggeration-value').textContent=parseFloat(this.value).toFixed(2)"
                 style="width: 100%; height: 3px; background: #222; outline: none; -webkit-appearance: none; cursor: pointer;">
          <div style="display: flex; justify-content: space-between; font-size: 9px; color: #333; margin-top: 2px;"><span>sakin</span><span>normal</span><span>dramatik</span></div>
        </div>
        <div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-size: 11px; color: #666;">Ses Kılavuzu / CFG Weight</span>
            <span id="cfg-weight-value" style="font-size: 11px; color: #aaa;">0.50</span>
          </div>
          <input type="range" id="cfg-weight-slider" min="0.1" max="1.0" step="0.05" value="0.5"
                 oninput="document.getElementById('cfg-weight-value').textContent=parseFloat(this.value).toFixed(2)"
                 style="width: 100%; height: 3px; background: #222; outline: none; -webkit-appearance: none; cursor: pointer;">
          <div style="display: flex; justify-content: space-between; font-size: 9px; color: #333; margin-top: 2px;"><span>serbest</span><span>dengeli</span><span>sabit</span></div>
        </div>
      </div>
    </div>
    <div class="controls" style="margin-bottom:1.25rem;">
      <button class="accent" id="btn-generate" onclick="generateTTS()">Seslendir</button>
    </div>
    <div class="tts-status" id="tts-status"></div>
    <div class="gen-progress" id="gen-progress">
      <div class="gp-label">
        <span id="gp-label-txt">Ses sentezleniyor...</span>
        <span class="gp-elapsed" id="gp-elapsed">0s</span>
      </div>
      <div class="gp-bar-bg"><div class="gp-bar-fill" id="gp-bar-fill"></div></div>
      <div class="gp-steps">
        <div class="gp-step" id="gps-0"><span class="gp-dot"></span>Metin normalize ediliyor</div>
        <div class="gp-step" id="gps-1"><span class="gp-dot"></span>Cumleler ayristiriliyor</div>
        <div class="gp-step" id="gps-2"><span class="gp-dot"></span>Ses sentezleniyor</div>
        <div class="gp-step" id="gps-3"><span class="gp-dot"></span>Ses birlestiriliyor ve optimize ediliyor</div>
      </div>
    </div>
    <div class="result-box" id="result-box">
      <audio id="result-audio" controls></audio>
      <div class="dl-row">
        <button class="success" onclick="downloadResult()">Indir</button>
        <button onclick="openNameDialog()" style="flex:0.6;">Kaydet...</button>
      </div>
    </div>
    <div class="field">
      <label>Ses Efektleri</label>
      <div style="display:flex; gap:8px; margin-bottom:8px;">
        <select id="fx-preset-select" style="flex:1; background:#111; border:1px solid #222; border-radius:3px; color:#e8e8e0; font-family:'Courier New',monospace; font-size:12px; padding:10px 12px; outline:none;">
          <option value="none">Efekt Yok</option>
        </select>
        <button onclick="applyFxPreset()" style="flex:0 0 auto; padding:10px 14px; font-size:11px; letter-spacing:0.1em;">Uygula</button>
        <button onclick="clearFxChain()" style="flex:0 0 auto; padding:10px 12px; color:#e05555; border-color:#e05555; font-size:11px;">Temizle</button>
      </div>
      <div id="fx-chain-display" style="font-size:10px; color:#555; min-height:14px; letter-spacing:0.05em;"></div>
    </div>

    <p class="tip"><strong>Not:</strong> Once "Ses Kaydi" sekmesinden referans ses yukleyin. Model ilk kez ~1-2GB indirir (bir kerelik).</p>
  </div>

  <!-- SEKME 3: Gecmis -->
  <div class="tab-panel" id="panel-history">
    <input class="history-search" id="history-search" type="text" placeholder="Ara..." oninput="filterHistory(this.value)">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
      <span id="history-count" style="font-size:10px; color:#444; letter-spacing:0.1em;"></span>
      <button onclick="deleteAllHistory()" style="flex:none; padding:6px 12px; font-size:10px; color:#e05555; border-color:#e05555;">Tumunu Sil</button>
    </div>
    <ul class="history-list" id="history-list"></ul>
    <div class="history-empty" id="history-empty" style="display:none;">Henuz uretim yok</div>
  </div>
</div>

<!-- Isimlendirme Dialog -->
<div class="name-dialog" id="name-dialog">
  <div class="name-dialog-box">
    <div class="nd-title">Sesi Kaydet</div>
    <input type="text" id="nd-name-input" placeholder="isim (orn: deneme_1)">
    <div class="nd-row">
      <button class="success" onclick="confirmSaveName()">Kaydet</button>
      <button onclick="closeNameDialog()">Atla</button>
    </div>
  </div>
</div>

<script>
function switchTab(name) {
  const tabs = ['record', 'tts', 'history'];
  document.querySelectorAll('.tab-btn').forEach((b, i) => {
    b.classList.toggle('active', tabs[i] === name);
  });
  tabs.forEach(t => {
    document.getElementById('panel-' + t).classList.toggle('active', t === name);
  });
  if (name === 'history') loadHistory();
}

let modelReady = false;
let voiceReady = false;

async function pollStatus() {
  try {
    const r = await fetch('/status');
    const d = await r.json();
    const mdot = document.getElementById('mdot');
    const mtxt = document.getElementById('model-status-text');
    modelReady = d.model_ready;
    // voiceReady'yi refreshVoices/setActiveVoice yonetir, pollStatus override etmesin
    if (d.model_error) {
      mdot.className = 'mdot err';
      mtxt.textContent = 'Model hatasi: ' + d.model_error;
    } else if (!d.model_ready) {
      mdot.className = 'mdot loading';
      mtxt.textContent = 'Model yukleniyor... (' + (d.device || '') + ')';
      setTimeout(pollStatus, 3000);
    } else {
      mdot.className = 'mdot ok';
      const voiceLabel = d.voice_ready ? ('| ' + (d.active_voice || 'ses') + ' [aktif]') : '| referans ses yok';
      mtxt.textContent = 'Model hazir - ' + d.device + ' ' + voiceLabel;
    }
  } catch(e) { setTimeout(pollStatus, 5000); }
}
pollStatus();
refreshVoices();
loadFxPresets();

let mediaRecorder, audioChunks = [], audioBlob, stream;
let timerInterval, startTime, analyser, animFrame;
const canvas = document.getElementById('waveform');
const ctx2d = canvas.getContext('2d');

function resize() {
  canvas.width = canvas.offsetWidth * devicePixelRatio;
  canvas.height = canvas.offsetHeight * devicePixelRatio;
  ctx2d.scale(devicePixelRatio, devicePixelRatio);
}
resize();
window.addEventListener('resize', resize);

function drawIdle() {
  const W = canvas.offsetWidth, H = canvas.offsetHeight;
  ctx2d.clearRect(0, 0, W, H);
  ctx2d.strokeStyle = '#222'; ctx2d.lineWidth = 1;
  ctx2d.beginPath(); ctx2d.moveTo(0, H/2); ctx2d.lineTo(W, H/2); ctx2d.stroke();
}
drawIdle();

function drawWave() {
  if (!analyser) return;
  const W = canvas.offsetWidth, H = canvas.offsetHeight;
  const bufLen = analyser.fftSize;
  const data = new Float32Array(bufLen);
  analyser.getFloatTimeDomainData(data);
  ctx2d.clearRect(0, 0, W, H);
  ctx2d.strokeStyle = '#e05555'; ctx2d.lineWidth = 1.5;
  ctx2d.beginPath();
  const step = W / bufLen;
  for (let i = 0; i < bufLen; i++) {
    const x = i * step, y = H/2 + data[i] * H * 0.45;
    i === 0 ? ctx2d.moveTo(x, y) : ctx2d.lineTo(x, y);
  }
  ctx2d.stroke();
  animFrame = requestAnimationFrame(drawWave);
}

function formatTime(ms) {
  const s = Math.floor(ms / 1000), m = Math.floor(s / 60);
  return String(m).padStart(2,'0') + ':' + String(s%60).padStart(2,'0');
}

async function toggleRecord() {
  if (mediaRecorder && mediaRecorder.state === 'recording') stopRecording();
  else await startRecording();
}

async function startRecording() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: { ideal: false },
        noiseSuppression: { ideal: false },
        autoGainControl: { ideal: false }
      }
    });
  } catch(e) { alert('Mikrofon izni gerekli.'); return; }

  const audioCtx = new AudioContext();
  const source = audioCtx.createMediaStreamSource(stream);
  analyser = audioCtx.createAnalyser(); analyser.fftSize = 1024;
  source.connect(analyser);
  audioChunks = [];
  // Browser varsayilan codec, 128kbps - en guvenli yuksek kalite
  let _recOpts = {};
  try {
    _recOpts = { audioBitsPerSecond: 128000 };
    new MediaRecorder(stream, _recOpts);  // desteklenip desteklenmedigini test et
  } catch (_e) {
    _recOpts = {};
  }
  mediaRecorder = new MediaRecorder(stream, _recOpts);
  mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
  mediaRecorder.onstop = processRecording;
  mediaRecorder.start(100);
  startTime = Date.now();
  timerInterval = setInterval(() => {
    document.getElementById('timer').textContent = formatTime(Date.now() - startTime);
    updateChecklist(Date.now() - startTime);
  }, 200);
  document.getElementById('idle-text').style.display = 'none';
  document.getElementById('status-dot').className = 'dot recording';
  document.getElementById('status-text').textContent = 'Kayit ediliyor...';
  document.getElementById('btn-record').textContent = 'Durdur';
  document.getElementById('btn-record').classList.add('active');
  document.getElementById('btn-play').disabled = true;
  document.getElementById('download-section').style.display = 'none';
  document.getElementById('audio-preview').style.display = 'none';
  document.getElementById('upload-progress').textContent = '';
  drawWave();
}

function stopRecording() {
  mediaRecorder.stop();
  stream.getTracks().forEach(t => t.stop());
  clearInterval(timerInterval);
  cancelAnimationFrame(animFrame);
  document.getElementById('status-dot').className = 'dot';
  document.getElementById('status-text').textContent = 'Isleniyor...';
  document.getElementById('btn-record').textContent = 'Kayit Baslat';
  document.getElementById('btn-record').classList.remove('active');
}

// WAV encoder: ham PCM 16-bit WAV olusturur
function _writeStr(view, offset, str) {
  for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
}
function _encodeWAV(audioBuf) {
  const numCh = 1, bitsPerSample = 16;
  const sr = audioBuf.sampleRate;
  let samples;
  if (audioBuf.numberOfChannels === 1) {
    samples = audioBuf.getChannelData(0);
  } else {
    const ch0 = audioBuf.getChannelData(0), ch1 = audioBuf.getChannelData(1);
    samples = new Float32Array(ch0.length);
    for (let i = 0; i < ch0.length; i++) samples[i] = (ch0[i] + ch1[i]) * 0.5;
  }
  const dataBytes = samples.length * 2;
  const buf = new ArrayBuffer(44 + dataBytes);
  const v = new DataView(buf);
  _writeStr(v, 0, 'RIFF');  v.setUint32(4, 36 + dataBytes, true);
  _writeStr(v, 8, 'WAVE');
  _writeStr(v, 12, 'fmt '); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, numCh, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * numCh * 2, true);
  v.setUint16(32, numCh * 2, true); v.setUint16(34, bitsPerSample, true);
  _writeStr(v, 36, 'data'); v.setUint32(40, dataBytes, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    off += 2;
  }
  return new Blob([buf], { type: 'audio/wav' });
}

async function processRecording() {
  const rawBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
  const duration = Date.now() - startTime;
  // Opus/WebM decode -> 16-bit PCM WAV re-encode: codec kayipsiz temiz ses
  try {
    document.getElementById('status-text').textContent = 'WAV donusturuluyor...';
    const arrBuf = await rawBlob.arrayBuffer();
    const tmpCtx = new AudioContext();
    const decoded = await tmpCtx.decodeAudioData(arrBuf);
    tmpCtx.close();
    audioBlob = _encodeWAV(decoded);
  } catch (_wavErr) {
    console.warn('WAV encode hatasi, ham blob kullaniliyor:', _wavErr);
    audioBlob = rawBlob;
  }
  const url = URL.createObjectURL(audioBlob);
  document.getElementById('audio-player').src = url;
  document.getElementById('audio-preview').style.display = 'block';
  document.getElementById('btn-play').disabled = false;
  document.getElementById('status-dot').className = 'dot ready';
  document.getElementById('status-text').textContent = 'Kayit tamamlandi';
  document.getElementById('idle-text').style.display = 'flex';
  document.getElementById('idle-text').textContent = '-- kayit tamamlandi --';
  const quality = Math.min(100, Math.round((duration / 25000) * 100));
  document.getElementById('quality-fill').style.width = quality + '%';
  document.getElementById('quality-fill').style.background = quality >= 60 ? '#55a055' : quality >= 30 ? '#a08030' : '#a03030';
  document.getElementById('quality-pct').textContent = quality + '%';
  updateChecklist(duration, true);
  drawIdle();
  document.getElementById('save-panel').style.display = 'block';
  document.getElementById('voice-name-input').focus();
}

function updateChecklist(durationMs, done = false) {
  const dur = document.getElementById('chk-duration');
  if (durationMs >= 10000) { dur.className = 'ok'; dur.querySelector('.ci').textContent = 'V'; }
  if (done) {
    const comp = document.getElementById('chk-complete');
    comp.className = 'ok'; comp.querySelector('.ci').textContent = 'V';
    const noise = document.getElementById('chk-noise');
    noise.className = 'ok'; noise.querySelector('.ci').textContent = 'V';
  }
}

function playRecording() { document.getElementById('audio-player').play(); }

let _inputMode = 'mic';
let _fileBlob = null;

function switchInputMode(mode) {
  _inputMode = mode;
  document.getElementById('mode-mic').style.display  = mode === 'mic'  ? 'block' : 'none';
  document.getElementById('mode-file').style.display = mode === 'file' ? 'block' : 'none';
  const btnMic  = document.getElementById('mode-btn-mic');
  const btnFile = document.getElementById('mode-btn-file');
  btnMic.style.color        = mode === 'mic'  ? '#e8e8e0' : '#444';
  btnMic.style.borderBottom = mode === 'mic'  ? '2px solid #e05555' : '2px solid transparent';
  btnFile.style.color        = mode === 'file' ? '#e8e8e0' : '#444';
  btnFile.style.borderBottom = mode === 'file' ? '2px solid #e05555' : '2px solid transparent';
  // save-panel'i sifirla
  document.getElementById('save-panel').style.display = 'none';
  document.getElementById('upload-progress').textContent = '';
  document.getElementById('download-section').style.display = 'none';
}

function _loadFileBlob(file) {
  if (!file) return;
  _fileBlob = file;
  const url = URL.createObjectURL(file);
  document.getElementById('file-audio-player').src = url;
  document.getElementById('file-preview').style.display = 'block';
  const sizekb = (file.size / 1024).toFixed(0);
  document.getElementById('file-info').textContent = file.name + ' — ' + sizekb + ' KB';
  // Isim inputunu dosya adiyla doldur (uzantisiz)
  const stem = file.name.replace(/[.][^.]+$/, '').replace(/[^\\w\\-]/g, '_');
  document.getElementById('voice-name-input').value = stem;
  document.getElementById('save-panel').style.display = 'block';
}

function handleFileSelect(e) { _loadFileBlob(e.target.files[0]); }

function handleFileDrop(e) {
  e.preventDefault();
  document.getElementById('drop-zone').style.borderColor = '#222';
  const file = e.dataTransfer.files[0];
  if (file && file.type.startsWith('audio/')) _loadFileBlob(file);
  else alert('Sadece ses dosyasi yukleyebilirsiniz.');
}

async function uploadVoice() {
  const blob = _inputMode === 'file' ? _fileBlob : audioBlob;
  if (!blob) return;
  const prog = document.getElementById('upload-progress');
  const nameInput = document.getElementById('voice-name-input');
  const voiceName = nameInput.value.trim();
  prog.textContent = 'Kaydediliyor...'; prog.className = 'upload-progress';
  document.getElementById('btn-save-voice').disabled = true;
  const form = new FormData();
  let fname;
  if (_inputMode === 'file' && _fileBlob) {
    fname = _fileBlob.name;
  } else {
    const _btype = (audioBlob && audioBlob.type) || '';
    const _ext = _btype === 'audio/wav' ? '.wav' : _btype.includes('ogg') ? '.ogg' : _btype.includes('mp4') ? '.m4a' : '.webm';
    fname = 'voice' + _ext;
  }
  form.append('file', blob, fname);
  if (voiceName) form.append('voice_name', voiceName);
  try {
    const r = await fetch('/upload-voice', { method: 'POST', body: form });
    const d = await r.json();
    if (r.ok) {
      prog.textContent = '"' + d.name + '" kaydedildi - ' + d.duration_sec + 's';
      prog.className = 'upload-progress ok';
      document.getElementById('download-section').style.display = 'block';
      document.getElementById('voice-info').textContent = '"' + d.name + '" aktif ses olarak ayarlandi (' + d.duration_sec + 's).';
      document.getElementById('save-panel').style.display = 'none';
      voiceReady = true;
      pollStatus();
      refreshVoices();
    } else {
      prog.textContent = 'Hata: ' + (d.detail || 'bilinmeyen');
      prog.className = 'upload-progress err';
    }
  } catch(e) {
    prog.textContent = 'Baglanti hatasi';
    prog.className = 'upload-progress err';
  }
  document.getElementById('btn-save-voice').disabled = false;
}

async function refreshVoices() {
  try {
    const r = await fetch('/voices');
    const d = await r.json();
    const sel = document.getElementById('voice-select');
    const detail = document.getElementById('voice-list-detail');
    sel.innerHTML = '';
    if (d.voices.length === 0) {
      sel.innerHTML = '<option value="">-- kayitli ses yok --</option>';
      detail.innerHTML = '';
      return;
    }
    d.voices.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.name;
      opt.textContent = v.name + ' (' + v.duration_sec + 's)';
      if (v.active) { opt.selected = true; opt.textContent += ' [aktif]'; }
      sel.appendChild(opt);
    });
    voiceReady = d.voices.some(v => v.active);
    // Detay satiri
    const active = d.voices.find(v => v.active);
    if (active) {
      detail.innerHTML = '<span style="font-size:10px;color:#55a055;">&#10003; Aktif: ' + active.name + ' &mdash; ' + active.duration_sec + 's</span>';
    } else { detail.innerHTML = ''; }
  } catch(e) { console.warn('refreshVoices error', e); }
}

async function setActiveVoice(name) {
  if (!name) return;
  try {
    const r = await fetch('/set-active-voice', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name})
    });
    if (r.ok) {
      voiceReady = true;
      refreshVoices();
      pollStatus();
    }
  } catch(e) { console.warn('setActiveVoice error', e); }
}

async function deleteVoice(name) {
  if (!confirm('"' + name + '" silinsin mi?')) return;
  try {
    await fetch('/delete-voice/' + encodeURIComponent(name), { method: 'DELETE' });
    refreshVoices();
  } catch(e) { console.warn('deleteVoice error', e); }
}

let resultBlob = null;
let _lastGenId = '';
let _lastDuration = '';
let _pendingSaveName = '';
let _historyItems = [];
let _historyFilter = '';

// Emotion slider event listeners
document.getElementById('exaggeration-slider').addEventListener('input', function(e) {
  document.getElementById('exaggeration-value').textContent = e.target.value;
  document.getElementById('auto-emotion').checked = false;
});

document.getElementById('cfg-weight-slider').addEventListener('input', function(e) {
  document.getElementById('cfg-weight-value').textContent = e.target.value;
  document.getElementById('auto-emotion').checked = false;
});

document.getElementById('auto-emotion').addEventListener('change', function(e) {
  if (e.target.checked) {
    document.getElementById('exaggeration-slider').value = 0.5;
    document.getElementById('cfg-weight-slider').value = 0.5;
    document.getElementById('exaggeration-value').textContent = '0.5';
    document.getElementById('cfg-weight-value').textContent = '0.5';
  }
});

function toggleEmotionSliders() {
  const auto = document.getElementById('auto-emotion').checked;
  document.getElementById('emotion-sliders').style.display = auto ? 'none' : 'block';
}

let _gpTimer = null;

function _gpStart() {
  const gp = document.getElementById('gen-progress');
  gp.style.display = 'block';
  // Adımlari sifirla
  for (let i = 0; i < 4; i++) {
    const el = document.getElementById('gps-' + i);
    el.className = 'gp-step';
  }
  // Gecen sure
  const t0 = Date.now();
  document.getElementById('gp-elapsed').textContent = '0s';
  _gpTimer = setInterval(() => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    document.getElementById('gp-elapsed').textContent = sec + 's';
    // Adim simulasyonu (zamanla ilerleme goster)
    if (sec >= 1)  _gpStep(0);
    if (sec >= 2)  _gpStep(1);
    if (sec >= 4)  _gpStep(2, true);  // aktif kal
  }, 500);
}

function _gpStep(idx, active = false) {
  for (let i = 0; i < idx; i++) {
    const el = document.getElementById('gps-' + i);
    if (!el.classList.contains('done')) el.className = 'gp-step done';
  }
  const cur = document.getElementById('gps-' + idx);
  if (active) cur.className = 'gp-step active';
  else cur.className = 'gp-step done';
}

function _gpFinish(ok) {
  clearInterval(_gpTimer);
  if (ok) {
    for (let i = 0; i < 4; i++) _gpStep(i);
    setTimeout(() => { document.getElementById('gen-progress').style.display = 'none'; }, 800);
  } else {
    document.getElementById('gen-progress').style.display = 'none';
  }
}

async function generateTTS() {
  const text = document.getElementById('tts-text').value.trim();
  const lang = 'tr';
  const status = document.getElementById('tts-status');
  const resultBox = document.getElementById('result-box');
  const autoEmotion = document.getElementById('auto-emotion').checked;
  
  if (!text) { status.textContent = 'Metin bos olamaz.'; status.className = 'tts-status err'; return; }
  const voiceSel = document.getElementById('voice-select').value;
  if (!voiceSel) { status.textContent = 'Once referans ses secin (Ses Kaydi sekmesi).'; status.className = 'tts-status err'; return; }
  if (!modelReady) { status.textContent = 'Model yukleniyor, bekleyin...'; status.className = 'tts-status loading'; return; }
  
  status.textContent = '';
  status.className = 'tts-status';
  resultBox.style.display = 'none';
  document.getElementById('btn-generate').disabled = true;
  _gpStart();
  
  try {
    const requestBody = { 
      text, 
      language: lang,
      auto_emotion: autoEmotion,
      exaggeration: parseFloat(document.getElementById('exaggeration-slider').value),
      cfg_weight: parseFloat(document.getElementById('cfg-weight-slider').value),
      speed: parseFloat(document.getElementById('speed-slider').value),
      effects_chain: _fxChain,
      save_name: _pendingSaveName
    };
    
    const r = await fetch('/generate-tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      _gpFinish(false);
      status.textContent = 'Hata: ' + (d.detail || r.statusText);
      status.className = 'tts-status err';
    } else {
      _lastGenId = r.headers.get('X-Generation-Id') || '';
      _lastDuration = r.headers.get('X-Duration') || '';
      resultBlob = await r.blob();
      const url = URL.createObjectURL(resultBlob);
      document.getElementById('result-audio').src = url;
      _gpFinish(true);
      resultBox.style.display = 'block';
      const elapsed = document.getElementById('gp-elapsed').textContent;
      status.textContent = 'Ses uretildi - ' + elapsed + (_lastDuration ? ' | ' + _lastDuration + 's' : '');
      status.className = 'tts-status ok';
    }
  } catch(e) {
    _gpFinish(false);
    status.textContent = 'Baglanti hatasi: ' + e.message;
    status.className = 'tts-status err';
  }
  document.getElementById('btn-generate').disabled = false;
}

// ---- Ses Efektleri ----
let _fxChain = [];
let _fxPresetsData = {};

async function loadFxPresets() {
  try {
    const r = await fetch('/effects-info');
    if (!r.ok) return;
    const d = await r.json();
    _fxPresetsData = d.presets;
    const sel = document.getElementById('fx-preset-select');
    sel.innerHTML = '';
    for (const [key, preset] of Object.entries(d.presets)) {
      const opt = document.createElement('option');
      opt.value = key;
      opt.textContent = preset.label;
      sel.appendChild(opt);
    }
  } catch(e) { /* pedalboard yoksa sessizce gecebilir */ }
}

function applyFxPreset() {
  const key = document.getElementById('fx-preset-select').value;
  const preset = _fxPresetsData[key];
  if (!preset) return;
  _fxChain = JSON.parse(JSON.stringify(preset.effects_chain));
  _renderFxChain();
}

function clearFxChain() {
  _fxChain = [];
  _renderFxChain();
}

function _renderFxChain() {
  const el = document.getElementById('fx-chain-display');
  if (!_fxChain || _fxChain.length === 0) {
    el.textContent = 'Efekt yok';
    el.style.color = '#333';
    return;
  }
  el.style.color = '#5588cc';
  el.textContent = _fxChain.filter(e => e.enabled !== false)
    .map(e => e.type).join(' → ');
}

function downloadResult() {
  if (!resultBlob) return;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(resultBlob);
  a.download = (_lastGenId ? ('ses_' + _lastGenId.slice(0,6)) : 'klonlanan_ses') + '.wav';
  a.click();
}

// ---- Isimlendirme Dialog ----
function openNameDialog() {
  if (!resultBlob) return;
  document.getElementById('nd-name-input').value = '';
  document.getElementById('name-dialog').classList.add('open');
  setTimeout(() => document.getElementById('nd-name-input').focus(), 50);
}

function closeNameDialog() {
  document.getElementById('name-dialog').classList.remove('open');
}

async function confirmSaveName() {
  const name = document.getElementById('nd-name-input').value.trim();
  closeNameDialog();
  if (!name || !_lastGenId) return;
  try {
    await fetch('/history/' + _lastGenId + '/rename', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name})
    });
    const status = document.getElementById('tts-status');
    status.textContent = '"' + name + '" olarak kaydedildi.';
    status.className = 'tts-status ok';
  } catch(e) { console.warn('rename error', e); }
}

document.getElementById('nd-name-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') confirmSaveName();
  if (e.key === 'Escape') closeNameDialog();
});

// ---- Gecmis ----
async function loadHistory() {
  try {
    const r = await fetch('/history');
    const d = await r.json();
    _historyItems = d.items || [];
    renderHistory(_historyFilter);
  } catch(e) { console.warn('history error', e); }
}

function filterHistory(q) {
  _historyFilter = q.toLowerCase();
  renderHistory(_historyFilter);
}

function renderHistory(filter) {
  const list = document.getElementById('history-list');
  const empty = document.getElementById('history-empty');
  const count = document.getElementById('history-count');
  const items = filter
    ? _historyItems.filter(i => i.name.toLowerCase().includes(filter) || i.text_preview.toLowerCase().includes(filter))
    : _historyItems;
  list.innerHTML = '';
  if (items.length === 0) {
    empty.style.display = 'block';
    count.textContent = '';
    return;
  }
  empty.style.display = 'none';
  count.textContent = items.length + ' kayit';
  items.forEach(item => {
    const li = document.createElement('li');
    li.className = 'history-item';
    const date = item.created_at ? item.created_at.replace('T', ' ') : '';
    li.innerHTML = `
      <div>
        <div class="hi-name">${_esc(item.name)}</div>
        <div class="hi-meta">${item.duration_sec}s &nbsp;|&nbsp; ${item.language} &nbsp;|&nbsp; ${_esc(date)}</div>
        <div class="hi-text">${_esc(item.text_preview)}</div>
      </div>
      <div class="hi-actions">
        <button onclick="playHistory('${item.id}')" title="Oynat">&#9654;</button>
        <button class="success" onclick="downloadHistory('${item.id}')" title="Indir">&#8595;</button>
        <button onclick="deleteHistoryItem('${item.id}')" style="color:#e05555;border-color:#e05555;" title="Sil">&#10005;</button>
      </div>`;
    list.appendChild(li);
  });
}

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function playHistory(id) {
  const url = '/history/' + id + '/download';
  const audio = new Audio(url);
  audio.play();
}

function downloadHistory(id) {
  const a = document.createElement('a');
  a.href = '/history/' + id + '/download';
  a.download = 'ses.wav';
  a.click();
}

async function deleteHistoryItem(id) {
  if (!confirm('Bu kayit silinsin mi?')) return;
  try {
    await fetch('/history/' + id, {method: 'DELETE'});
    _historyItems = _historyItems.filter(i => i.id !== id);
    renderHistory(_historyFilter);
  } catch(e) { console.warn('delete history error', e); }
}

async function deleteAllHistory() {
  if (!confirm('Tum gecmis silinsin mi?')) return;
  for (const item of [..._historyItems]) {
    try { await fetch('/history/' + item.id, {method: 'DELETE'}); } catch(e) {}
  }
  _historyItems = [];
  renderHistory('');
}

// ---- Klavye Kisayollari ----
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
  if (e.key === ' ' || e.code === 'Space') {
    e.preventDefault();
    const audio = document.getElementById('result-audio');
    if (audio && audio.src) {
      audio.paused ? audio.play() : audio.pause();
    }
  }
});
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    generateTTS();
  }
});
</script>
</body>
</html>"""


# ============================================================
# BOLUM 5 - Giris noktasi
# ============================================================

if __name__ == "__main__":
    import webbrowser
    import threading

    # --install-only: sadece bootstrap, sunucu baslatma
    if "--install-only" in sys.argv:
        bootstrap()
        print("[OK] Kurulum tamamlandi.")
        sys.exit(0)

    if _SERVE_MODE:
        # Bootstrap zaten yapildi, sunucuyu baslat
        import uvicorn
        import socket

        def _find_free_port(start=9000):
            for port in range(start, start + 20):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.bind(("0.0.0.0", port))
                        return port
                    except OSError:
                        continue
            return start

        PORT = _find_free_port(9000)

        if not _PANEL_MODE:
            def _open_browser():
                time.sleep(3)
                webbrowser.open(f"http://localhost:{PORT}")
            threading.Thread(target=_open_browser, daemon=True).start()

        print(f"\n[OK] Uygulama baslatildi -> http://localhost:{PORT}")
        print("[i] Durdurmak icin Ctrl+C\n")

        uvicorn.run("app:app", host="0.0.0.0", port=PORT, log_level="info")

    else:
        # Ilk calistirma: bootstrap yap, sonra SES_KLON_SERVE=1 ile yeniden baslat
        bootstrap()
        print("[OK] Kurulum tamamlandi. Sunucu baslatiliyor...\n")
        env = os.environ.copy()
        env["SES_KLON_SERVE"] = "1"
        result = subprocess.run(
            [sys.executable, __file__],
            env=env,
            check=False,
        )
        sys.exit(result.returncode)
