# -*- coding: utf-8 -*-
"""
Ses efektleri motoru.

Spotify pedalboard kutuphanesi ile profesyonel DSP efektleri uygular.
Voicebox projesinden adapte edilmistir.

Desteklenen efekt tipleri:
  - reverb       : oda yankisi
  - chorus       : flanger/chorus efekti
  - delay        : eko / gecikme
  - compressor   : dinamik aralik sikistirma
  - gain         : ses seviyesi ayari (dB)
  - highpass     : yuksek gecis filtresi
  - lowpass      : alcak gecis filtresi
  - pitch_shift  : perde kayma (yari ton)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

# pedalboard opsiyonel — yuklu degilse efektler sessizce devre disi kalir
try:
    from pedalboard import (
        Pedalboard, Chorus, Reverb, Compressor,
        Gain, HighpassFilter, LowpassFilter, Delay, PitchShift,
    )
    _PEDALBOARD_OK = True
except ImportError:
    _PEDALBOARD_OK = False


EFFECT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "reverb": {
        "label": "Reverb",
        "description": "Oda yankisi efekti.",
        "params": {
            "room_size":  {"default": 0.5,  "min": 0.0, "max": 1.0,  "step": 0.01},
            "damping":    {"default": 0.5,  "min": 0.0, "max": 1.0,  "step": 0.01},
            "wet_level":  {"default": 0.33, "min": 0.0, "max": 1.0,  "step": 0.01},
            "dry_level":  {"default": 0.4,  "min": 0.0, "max": 1.0,  "step": 0.01},
            "width":      {"default": 1.0,  "min": 0.0, "max": 1.0,  "step": 0.01},
        },
    },
    "chorus": {
        "label": "Chorus / Flanger",
        "description": "Modulasyonlu gecikme efekti.",
        "params": {
            "rate_hz":         {"default": 1.0, "min": 0.01, "max": 20.0, "step": 0.01},
            "depth":           {"default": 0.5, "min": 0.0,  "max": 1.0,  "step": 0.01},
            "feedback":        {"default": 0.0, "min": 0.0,  "max": 0.95, "step": 0.01},
            "centre_delay_ms": {"default": 7.0, "min": 0.5,  "max": 50.0, "step": 0.1},
            "mix":             {"default": 0.5, "min": 0.0,  "max": 1.0,  "step": 0.01},
        },
    },
    "delay": {
        "label": "Delay / Eko",
        "description": "Eko / gecikme hatti.",
        "params": {
            "delay_seconds": {"default": 0.3,  "min": 0.01, "max": 2.0,  "step": 0.01},
            "feedback":      {"default": 0.3,  "min": 0.0,  "max": 0.95, "step": 0.01},
            "mix":           {"default": 0.3,  "min": 0.0,  "max": 1.0,  "step": 0.01},
        },
    },
    "compressor": {
        "label": "Kompresyon",
        "description": "Dinamik aralik sikistirma.",
        "params": {
            "threshold_db": {"default": -20.0, "min": -60.0, "max": 0.0,    "step": 0.5},
            "ratio":        {"default": 4.0,   "min": 1.0,   "max": 20.0,   "step": 0.1},
            "attack_ms":    {"default": 10.0,  "min": 0.1,   "max": 100.0,  "step": 0.1},
            "release_ms":   {"default": 100.0, "min": 10.0,  "max": 1000.0, "step": 1.0},
        },
    },
    "gain": {
        "label": "Kazanc (dB)",
        "description": "Ses seviyesi ayari.",
        "params": {
            "gain_db": {"default": 0.0, "min": -40.0, "max": 40.0, "step": 0.5},
        },
    },
    "highpass": {
        "label": "Yuksek Gecis Filtresi",
        "description": "Kesim frekansinin altindaki frekanslari keser.",
        "params": {
            "cutoff_frequency_hz": {"default": 80.0, "min": 20.0, "max": 8000.0, "step": 1.0},
        },
    },
    "lowpass": {
        "label": "Alcak Gecis Filtresi",
        "description": "Kesim frekansinin ustundeki frekanslari keser.",
        "params": {
            "cutoff_frequency_hz": {"default": 8000.0, "min": 200.0, "max": 20000.0, "step": 1.0},
        },
    },
    "pitch_shift": {
        "label": "Perde Kaydirma",
        "description": "Sesi yari ton cinsinden yukari/asagi kaydirir.",
        "params": {
            "semitones": {"default": 0.0, "min": -12.0, "max": 12.0, "step": 0.5},
        },
    },
}

BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "none": {
        "label": "Efekt Yok",
        "effects_chain": [],
    },
    "reverb_light": {
        "label": "Hafif Reverb",
        "effects_chain": [
            {"type": "reverb", "enabled": True,
             "params": {"room_size": 0.3, "wet_level": 0.2, "dry_level": 0.8}},
        ],
    },
    "echo_chamber": {
        "label": "Yankili Oda",
        "effects_chain": [
            {"type": "reverb", "enabled": True,
             "params": {"room_size": 0.85, "damping": 0.3, "wet_level": 0.45, "dry_level": 0.55}},
            {"type": "delay",  "enabled": True,
             "params": {"delay_seconds": 0.25, "feedback": 0.3, "mix": 0.2}},
        ],
    },
    "radio": {
        "label": "Radyo Efekti",
        "effects_chain": [
            {"type": "highpass",   "enabled": True, "params": {"cutoff_frequency_hz": 300.0}},
            {"type": "lowpass",    "enabled": True, "params": {"cutoff_frequency_hz": 3500.0}},
            {"type": "compressor", "enabled": True,
             "params": {"threshold_db": -15.0, "ratio": 6.0, "attack_ms": 5.0, "release_ms": 50.0}},
            {"type": "gain",       "enabled": True, "params": {"gain_db": 6.0}},
        ],
    },
    "robotic": {
        "label": "Robotik Ses",
        "effects_chain": [
            {"type": "chorus", "enabled": True,
             "params": {"rate_hz": 0.2, "depth": 1.0, "feedback": 0.35,
                        "centre_delay_ms": 7.0, "mix": 0.5}},
        ],
    },
    "deep_voice": {
        "label": "Derin Ses",
        "effects_chain": [
            {"type": "pitch_shift", "enabled": True, "params": {"semitones": -3.0}},
            {"type": "lowpass",     "enabled": True, "params": {"cutoff_frequency_hz": 6000.0}},
            {"type": "compressor",  "enabled": True,
             "params": {"threshold_db": -18.0, "ratio": 3.0, "attack_ms": 10.0, "release_ms": 150.0}},
        ],
    },
    "pitch_up": {
        "label": "Ince Ses (+3 ton)",
        "effects_chain": [
            {"type": "pitch_shift", "enabled": True, "params": {"semitones": 3.0}},
        ],
    },
}


def get_presets() -> Dict[str, Dict[str, Any]]:
    """Tum preset'lerin listesini dondur."""
    return {k: {"label": v["label"], "effects_chain": v["effects_chain"]}
            for k, v in BUILTIN_PRESETS.items()}


def validate_effects_chain(effects_chain: List[Dict[str, Any]]) -> Optional[str]:
    """Efekt zincirini dogrula. Gecerliyse None, hata varsa mesaj dondurur."""
    if not isinstance(effects_chain, list):
        return "effects_chain liste olmali"
    for i, effect in enumerate(effects_chain):
        if not isinstance(effect, dict):
            return f"Efekt {i} dict olmali"
        etype = effect.get("type")
        if etype not in EFFECT_REGISTRY:
            return f"Bilinmeyen efekt tipi: '{etype}'"
        params = effect.get("params", {})
        for pname, val in params.items():
            if pname not in EFFECT_REGISTRY[etype]["params"]:
                return f"'{etype}' efektinde bilinmeyen parametre: '{pname}'"
            pdef = EFFECT_REGISTRY[etype]["params"][pname]
            if not isinstance(val, (int, float)):
                return f"'{etype}.{pname}' sayisal olmali"
            if val < pdef["min"] or val > pdef["max"]:
                return (f"'{etype}.{pname}' {pdef['min']}-{pdef['max']} arasinda olmali "
                        f"(alindi: {val})")
    return None


def _build_pedalboard(effects_chain: List[Dict[str, Any]]):
    """Efekt zincirinden Pedalboard nesnesi olustur."""
    if not _PEDALBOARD_OK:
        raise RuntimeError("pedalboard yuklu degil. `pip install pedalboard` calistirin.")

    _cls_map = {
        "reverb":      Reverb,
        "chorus":      Chorus,
        "delay":       Delay,
        "compressor":  Compressor,
        "gain":        Gain,
        "highpass":    HighpassFilter,
        "lowpass":     LowpassFilter,
        "pitch_shift": PitchShift,
    }

    plugins = []
    for effect in effects_chain:
        if not effect.get("enabled", True):
            continue
        etype = effect["type"]
        cls = _cls_map[etype]
        defaults = {n: p["default"] for n, p in EFFECT_REGISTRY[etype]["params"].items()}
        params = {**defaults, **effect.get("params", {})}
        plugins.append(cls(**params))
    return Pedalboard(plugins)


def apply_effects(audio: np.ndarray, sample_rate: int,
                  effects_chain: List[Dict[str, Any]]) -> np.ndarray:
    """Efekt zincirini ses verisine uygula.

    Args:
        audio      : 1-D float32 ndarray (mono)
        sample_rate: ornek hizi (Hz)
        effects_chain: efekt konfigurasyonu listesi

    Returns:
        Islenmis ses ndarray.
    """
    if not effects_chain or not any(e.get("enabled", True) for e in effects_chain):
        return audio

    if not _PEDALBOARD_OK:
        import logging
        logging.getLogger("ses_klon").warning(
            "pedalboard yuklu degil, efektler atlanadi. `pip install pedalboard`")
        return audio

    board = _build_pedalboard(effects_chain)

    if audio.ndim == 1:
        audio_2d = audio[np.newaxis, :].astype(np.float32)
    else:
        audio_2d = audio.astype(np.float32)

    processed = board(audio_2d, sample_rate)
    return processed[0] if audio.ndim == 1 else processed
