# -*- coding: utf-8 -*-
"""
Türkçe Metin Normalizasyonu
============================
Metni TTS için normalize eder: sıra sayıları, kardinal sayılar, kısaltmalar ve noktalama.
"""

import re
import string
from typing import Tuple, Dict


def normalize(text: str) -> str:
    """
    Metni normalize eder - ana fonksiyon
    """
    if not text:
        return text

    # URL ve email temizle (okunması zor oldugu icin kaldir)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)
    text = re.sub(r'\S+@\S+\.\S+', '', text)

    # Para birimleri: önce işle (sayı dönüşümünden önce)
    def _currency(m):
        sym, num_s = m.group(1), m.group(2)
        try:
            n = int(num_s)
        except ValueError:
            return m.group(0)
        _map = {'₺': 'lira', '$': 'dolar', '€': 'euro', '£': 'sterlin'}
        unit = _map.get(sym, sym)
        return num_s + ' ' + unit
    text = re.sub(r'([₺$€£])(\d+)', _currency, text)
    # Sonda sembol: 150₺ → 150 lira
    def _currency_suffix(m):
        num_s, sym = m.group(1), m.group(2)
        _map = {'₺': 'lira', '$': 'dolar', '€': 'euro', '£': 'sterlin'}
        return num_s + ' ' + _map.get(sym, sym)
    text = re.sub(r'(\d+)\s*([₺$€£])', _currency_suffix, text)

    # Saat formatı: 14:30 veya saat 14:30 → saat ondört otuz
    _TIME_NUMS = ["sıfır","bir","iki","üç","dört","beş","altı","yedi","sekiz","dokuz",
                  "on","onbir","oniki","onüç","ondört","onbeş","onaltı","onyedi","onsekiz","ondokuz",
                  "yirmi","yirmi bir","yirmi iki","yirmi üç"]
    _TIME_TENS = ["","","yirmi","otuz","kırk","elli","altmış"]

    def _time_num(x):
        if x < len(_TIME_NUMS):
            return _TIME_NUMS[x]
        t = _TIME_TENS[x // 10]
        u = _TIME_NUMS[x % 10] if x % 10 else ''
        return (t + ' ' + u).strip()

    def _time_replace(m):
        has_saat = bool(m.group(1))
        h, mn = int(m.group(2)), int(m.group(3))
        result = _time_num(h) + (' ' + _time_num(mn) if mn > 0 else '')
        return ('saat ' if not has_saat else '') + result

    text = re.sub(r'(?i)(saat\s+)?([01]?\d|2[0-3]):([0-5]\d)\b', _time_replace, text)

    # Roman rakamlarını dönüştür
    text = convert_roman_numerals(text)
    
    # Önce ondalıklı sayıları işle
    def convert_decimal(match):
        whole = int(match.group(1))
        decimal = int(match.group(2))
        
        def num_to_tr(n):
            if n == 0: return "sıfır"
            if n < 10: 
                birler = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
                return birler[n]
            if n < 20:
                onlar = ["on", "onbir", "oniki", "onüç", "ondört", "onbeş", "onaltı", "onyedi", "onsekiz", "ondokuz"]
                return onlar[n - 10]
            if n < 100:
                onlar = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
                return onlar[n // 10] + (" " + num_to_tr(n % 10) if n % 10 != 0 else "")
            return str(n)
        
        return num_to_tr(whole) + " nokta " + num_to_tr(decimal)
    
    text = re.sub(r'(\d+)\.(\d+)', convert_decimal, text)
    
    # Kısaltmaları dönüştür
    text = convert_abbreviations(text)
    
    # Sıra sayılarını dönüştür: 16. yy -> onaltıncı yy
    # Lookahead kullan, match'e boşluk dahil etme
    _ORDINALS = {
        '1':'birinci','2':'ikinci','3':'üçüncü','4':'dördüncü','5':'beşinci',
        '6':'altıncı','7':'yedinci','8':'sekizinci','9':'dokuzuncu','10':'onuncu',
        '11':'onbirinci','12':'onikinci','13':'onüçüncü','14':'ondördüncü','15':'onbeşinci',
        '16':'onaltıncı','17':'onyedinci','18':'onsekizinci','19':'ondokuzuncu','20':'yirminci',
        '21':'yirmibirinci','22':'yirmikinci','23':'yirmiüçüncü','24':'yirmidördüncü','25':'yirmibeşinci',
        '26':'yirmialtıncı','27':'yirmiyedinci','28':'yirmisekizinci','29':'yirmidokuzuncu','30':'otuzuncu',
        '31':'otuzbirinci','32':'otuzikinci','33':'otuzüçüncü','34':'otuzdordüncü','35':'otuzbeşinci',
        '40':'kırkıncı','50':'ellinci','60':'altmışıncı','70':'yetmişinci','80':'sekseninci','90':'doksanıncı','100':'yüzüncü'
    }
    def convert_ordinal(match):
        num = match.group(1)
        word = _ORDINALS.get(num, num + '.')
        return word  # lookahead ile boşluk korunuyor
    text = re.sub(r'(\d+)\.(?=\s|[A-Za-zçÇğĞıİöÖşŞüÜ])', convert_ordinal, text)
    
    # Kardinal sayıları dönüştür (kalan tam sayılar)
    def convert_cardinal(match):
        num = int(match.group())
        
        def num_to_tr(n):
            if n < 0: return "eksi " + num_to_tr(-n)
            if n == 0: return "sıfır"
            if n < 10:
                birler = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
                return birler[n]
            if n < 20:
                onlar = ["on", "onbir", "oniki", "onüç", "ondört", "onbeş", "onaltı", "onyedi", "onsekiz", "ondokuz"]
                return onlar[n - 10]
            if n < 100:
                onlar = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
                return onlar[n // 10] + (" " + num_to_tr(n % 10) if n % 10 != 0 else "")
            if n < 1000:
                if n == 100: return "yüz"
                if n < 200: return "yüz" + (" " + num_to_tr(n % 100) if n % 100 != 0 else "")
                return num_to_tr(n // 100) + " yüz" + (" " + num_to_tr(n % 100) if n % 100 != 0 else "")
            if n < 1000000:
                if n == 1000: return "bin"
                if n < 2000: return "bin" + (" " + num_to_tr(n % 1000) if n % 1000 != 0 else "")
                return num_to_tr(n // 1000) + " bin" + (" " + num_to_tr(n % 1000) if n % 1000 != 0 else "")
            return str(n)
        
        return num_to_tr(num)
    
    text = re.sub(r'\b\d+\b', convert_cardinal, text)
    
    # Noktalama işaretlerini normalize et
    text = normalize_punctuation(text)
    
    # Çift boşlukları temizle
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


def detect_emotion(text: str) -> Tuple[float, float]:
    """
    Metinden duygu tespiti yapar ve (exaggeration, cfg_weight) döndürür
    """
    if not text:
        return 0.5, 0.5
    
    # Varsayılan değerler
    exaggeration = 0.5
    cfg_weight = 0.5
    
    # Ünlem işaretleri sayısı
    exclamation_count = text.count('!')
    
    # Soru işaretleri sayısı
    question_count = text.count('?')
    
    # Üç nokta sayısı
    ellipsis_count = text.count('...')
    
    # Büyük harf yoğunluğu
    upper_chars = sum(1 for c in text if c.isupper())
    total_chars = sum(1 for c in text if c.isalpha())
    upper_ratio = upper_chars / total_chars if total_chars > 0 else 0
    
    # Duygu kuralları
    if exclamation_count >= 2 or upper_ratio > 0.3:
        # Dramatik
        exaggeration = 0.85
        cfg_weight = 0.7
    elif exclamation_count == 1:
        # Heyecanlı
        exaggeration = 0.75
        cfg_weight = 0.65
    elif ellipsis_count >= 2:
        # Sakin/Üzgün
        exaggeration = 0.3
        cfg_weight = 0.4
    elif question_count >= 1:
        # Soru
        exaggeration = 0.45
        cfg_weight = 0.55
    else:
        # Normal
        exaggeration = 0.5
        cfg_weight = 0.5
    
    return exaggeration, cfg_weight


def convert_ordinal_numbers(text: str) -> str:
    """
    Sıra sayılarını dönüştürür: 16. -> onaltıncı
    """
    # Sıra sayıları sözlüğü (1-100)
    ordinal_dict = {
        '1.': 'birinci', '2.': 'ikinci', '3.': 'üçüncü', '4.': 'dördüncü', '5.': 'beşinci',
        '6.': 'altıncı', '7.': 'yedinci', '8.': 'sekizinci', '9.': 'dokuzuncu', '10.': 'onuncu',
        '11.': 'onbirinci', '12.': 'onikinci', '13.': 'onüçüncü', '14.': 'ondördüncü', '15.': 'onbeşinci',
        '16.': 'onaltıncı', '17.': 'onyedinci', '18.': 'onsekizinci', '19.': 'ondokuzuncu', '20.': 'yirminci',
        '21.': 'yirmibirinci', '22.': 'yirmikinci', '23.': 'yirmiüçüncü', '24.': 'yirmidördüncü', '25.': 'yirmibeşinci',
        '26.': 'yirmialtıncı', '27.': 'yirmiyedinci', '28.': 'yirmisekizinci', '29.': 'yirmidokuzuncu', '30.': 'otuzuncu',
        '31.': 'otuzbirinci', '32.': 'otuzikinci', '33.': 'otuzüçüncü', '34.': 'otuzdördüncü', '35.': 'otuzbeşinci',
        '36.': 'otuzaltıncı', '37.': 'otuzyedinci', '38.': 'otuzsekizinci', '39.': 'otuzdokuzuncu', '40.': 'kırkıncı',
        '41.': 'kırkbirinci', '42.': 'kırkikinci', '43.': 'kırküçüncü', '44.': 'kırkdördüncü', '45.': 'kırkbeşinci',
        '46.': 'kırkaltıncı', '47.': 'kırkyedinci', '48.': 'kırksekizinci', '49.': 'kırkdokuzuncu', '50.': 'ellinci',
        '51.': 'ellibirinci', '52.': 'ellikinci', '53.': 'elliüçüncü', '54.': 'ellidördüncü', '55.': 'ellibeşinci',
        '56.': 'ellialtıncı', '57.': 'elliyedinci', '58.': 'ellisekizinci', '59.': 'ellidokuzuncu', '60.': 'altmışıncı',
        '61.': 'altmışbirinci', '62.': 'altmışikinci', '63.': 'altmışüçüncü', '64.': 'altmışdördüncü', '65.': 'altmışbeşinci',
        '66.': 'altmışaltıncı', '67.': 'altmışyedinci', '68.': 'altmışsekizinci', '69.': 'altmışdokuzuncu', '70.': 'yetmişinci',
        '71.': 'yetmişbirinci', '72.': 'yetmişikinci', '73.': 'yetmişüçüncü', '74.': 'yetmişdördüncü', '75.': 'yetmişbeşinci',
        '76.': 'yetmişaltıncı', '77.': 'yetmişyedinci', '78.': 'yetmişsekizinci', '79.': 'yetmişdokuzuncu', '80.': 'sekseninci',
        '81.': 'seksenbirinci', '82.': 'seksenikinci', '83.': 'seksenüçüncü', '84.': 'seksendördüncü', '85.': 'seksenbeşinci',
        '86.': 'seksenaltıncı', '87.': 'seksenyedinci', '88.': 'seksensekizinci', '89.': 'seksendokuzuncu', '90.': 'doksanıncı',
        '91.': 'doksanbirinci', '92.': 'doksanikinci', '93.': 'doksanüçüncü', '94.': 'doksandördüncü', '95.': 'doksanbeşinci',
        '96.': 'doksanaltıncı', '97.': 'doksanyedinci', '98.': 'doksansekizinci', '99.': 'doksandokuzuncu', '100.': 'yüzüncü'
    }
    
    # Sıra sayılarını değiştir - cümle sonu değilse (sonrasında boşluk veya harf varsa)
    # Önce ondalıklı sayıları korumak için geçici olarak değiştir
    text = re.sub(r'(\d+)\.(\d+)', r'\1<DECIMAL>\2', text)
    
    for num, word in ordinal_dict.items():
        pattern = rf'\b{num}(?=\s|[A-Za-zçÇğĞıİöÖşŞüÜ])'
        text = re.sub(pattern, word, text)
    
    # Ondalıklı sayıları geri getir
    text = text.replace('<DECIMAL>', '.')
    
    return text


def convert_cardinal_numbers(text: str) -> str:
    """
    Kardinal sayıları dönüştürür: 1453 -> bin dörtyüz elli üç
    """
    def number_to_turkish(n):
        if n < 0:
            return "eksi " + number_to_turkish(-n)
        elif n == 0:
            return "sıfır"
        elif n < 10:
            birler = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
            return birler[n]
        elif n < 20:
            onlar = ["on", "onbir", "oniki", "onüç", "ondört", "onbeş", "onaltı", "onyedi", "onsekiz", "ondokuz"]
            return onlar[n - 10]
        elif n < 100:
            onlar = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
            return onlar[n // 10] + (" " + number_to_turkish(n % 10) if n % 10 != 0 else "")
        elif n < 1000:
            if n == 100:
                return "yüz"
            elif n < 200:
                return "yüz" + (" " + number_to_turkish(n % 100) if n % 100 != 0 else "")
            else:
                return number_to_turkish(n // 100) + " yüz" + (" " + number_to_turkish(n % 100) if n % 100 != 0 else "")
        elif n < 1000000:
            if n == 1000:
                return "bin"
            elif n < 2000:
                return "bin" + (" " + number_to_turkish(n % 1000) if n % 1000 != 0 else "")
            else:
                return number_to_turkish(n // 1000) + " bin" + (" " + number_to_turkish(n % 1000) if n % 1000 != 0 else "")
        elif n < 1000000000:
            return number_to_turkish(n // 1000000) + " milyon" + (" " + number_to_turkish(n % 1000000) if n % 1000000 != 0 else "")
        elif n < 1000000000000:
            return number_to_turkish(n // 1000000000) + " milyar" + (" " + number_to_turkish(n % 1000000000) if n % 1000000000 != 0 else "")
        else:
            return str(n)
    
    # Negatif sayılar: boşluk sonrası veya satır başı
    # Satır başı: text "-5" ile başlıyorsa
    text = re.sub(r'^-([0-9]+)', lambda m: 'eksi ' + number_to_turkish(int(m.group(1))), text)
    # Boşluk sonrası
    text = re.sub(r'(\s)-([0-9]+)', lambda m: m.group(1) + 'eksi ' + number_to_turkish(int(m.group(2))), text)
    
    # Ondalıklı sayılar (korunmuş olanları işle)
    text = re.sub(r'(\d+)<DECIMAL>(\d+)', lambda m: number_to_turkish(int(m.group(1))) + " nokta " + number_to_turkish(int(m.group(2))), text)
    
    # Pozitif tam sayılar (ondalıklı olmayanları)
    text = re.sub(r'\b\d+\b(?![.<DECIMAL>])', lambda m: number_to_turkish(int(m.group())), text)
    
    return text


def convert_abbreviations(text: str) -> str:
    """
    Kısaltmaları dönüştürür
    """
    abbreviations = {
        # Para birimleri
        'TL': 'Türk lirası',
        '$': 'dolar',
        '€': 'avro',
        '£': 'sterlin',
        'km': 'kilometre',
        'cm': 'santimetre',
        'mm': 'milimetre',
        'kg': 'kilogram',
        'g': 'gram',
        'mg': 'miligram',
        'l': 'litre',
        'ml': 'mililitre',
        'm²': 'metre kare',
        'km²': 'kilometre kare',
        'm³': 'metre küp',
        'km³': 'kilometre küp',
        
        # Zaman ve tarih
        'yy.': 'yüzyıl',
        'Yüz.': 'yüzyıl',
        'y.': 'yıl',
        'ay.': 'ay',
        'gün.': 'gün',
        'sn.': 'saniye',
        'dk.': 'dakika',
        'sa.': 'saat',
        
        # Genel kısaltmalar
        'vb.': 've benzeri',
        'vs.': 've saire',
        'dr.': 'doktor',
        'prof.': 'profesör',
        'doç.': 'doçent',
        'ist.': 'istasyon',
        'mey.': 'meydan',
        'cad.': 'cadde',
        'sok.': 'sokak',
        'ap.': 'apartman',
        'no.': 'numara',
        'tel.': 'telefon',
        'fax.': 'faks',
        'e-posta': 'e posta',
        
        # Kurumlar
        'İÜ': 'İstanbul Üniversitesi',
        'TBMM': 'Türkiye Büyük Millet Meclisi',
        'TC': 'Türkiye Cumhuriyeti',
        'MEB': 'Milli Eğitim Bakanlığı',
        'TSK': 'Türk Silahlı Kuvvetleri',
        'RTÜK': 'Radyo ve Televizyon Üst Kurulu',
        'BİM': 'B İ M',
        'A101': 'A yüz bir',
        'MİGROS': 'M İ G R O S'
    }
    
    # Büyük harf kısaltmaları harf harf oku
    def abbreviate_upper(match):
        abbrev = match.group()
        if abbrev in abbreviations:
            return abbreviations[abbrev]
        else:
            return ' '.join(list(abbrev))
    
    # Önce özel kısaltmaları değiştir (uzundan kısaya sırala - daha spesifik önce)
    for abbrev, full in sorted(abbreviations.items(), key=lambda x: -len(x[0])):
        escaped = re.escape(abbrev)
        if abbrev[-1] == '.':
            # Noktalı kısaltmalar: kelime sınırı yerine başında \b, sonunda lookahead
            pattern = rf'(?<![\w]){escaped}'
        else:
            pattern = rf'\b{escaped}\b'
        text = re.sub(pattern, full, text, flags=re.IGNORECASE)
    
    # Kalan büyük harf kısaltmalarını harf harf oku
    text = re.sub(r'\b[A-ZÇĞİÖŞÜ]{2,}\b', abbreviate_upper, text)
    
    return text


def normalize_punctuation(text: str) -> str:
    """
    Noktalama işaretlerini normalize eder
    """
    # Üç nokta ve tireler -> virgül (doğal duraklama)
    text = re.sub(r'\.{2,}', ', ', text)  # ...
    text = re.sub(r'—|–', ', ', text)     # em dash ve en dash
    
    # Çoklu noktalama temizliği
    text = re.sub(r'[!?]{2,}', lambda m: m.group()[0], text)
    
    # Gereksiz boşlukları temizle
    text = re.sub(r'\s*,\s*', ', ', text)
    text = re.sub(r'\s*\.\s*', '. ', text)
    text = re.sub(r'\s*[!?]\s*', lambda m: m.group().strip() + ' ', text)
    
    return text.strip()


def convert_roman_numerals(text: str) -> str:
    """
    Roman rakamlarını dönüştürür: XIV. -> on dördüncü
    """
    roman_to_dict = {
        'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000
    }
    
    def roman_to_int(roman):
        total = 0
        prev_value = 0
        for char in reversed(roman):
            value = roman_to_dict[char]
            if value < prev_value:
                total -= value
            else:
                total += value
                prev_value = value
        return total
    
    def int_to_turkish_ordinal(n):
        # 1-20 arası sıra sayıları
        ordinals = {
            1: 'birinci', 2: 'ikinci', 3: 'üçüncü', 4: 'dördüncü', 5: 'beşinci',
            6: 'altıncı', 7: 'yedinci', 8: 'sekizinci', 9: 'dokuzuncu', 10: 'onuncu',
            11: 'onbirinci', 12: 'onikinci', 13: 'onüçüncü', 14: 'ondördüncü', 15: 'onbeşinci',
            16: 'onaltıncı', 17: 'onyedinci', 18: 'onsekizinci', 19: 'ondokuzuncu', 20: 'yirminci'
        }
        
        if n in ordinals:
            return ordinals[n]
        elif n <= 100:
            # Basit sıra sayısı oluşturma
            if n <= 20:
                return ordinals[n]
            elif n < 30:
                return 'yirmi' + ('' if n == 20 else ' ' + ordinals[n - 20])
            elif n < 40:
                return 'otuz' + ('' if n == 30 else ' ' + ordinals[n - 30])
            elif n < 50:
                return 'kırk' + ('' if n == 40 else ' ' + ordinals[n - 40])
            elif n < 60:
                return 'elli' + ('' if n == 50 else ' ' + ordinals[n - 50])
            elif n < 70:
                return 'altmış' + ('' if n == 60 else ' ' + ordinals[n - 60])
            elif n < 80:
                return 'yetmiş' + ('' if n == 70 else ' ' + ordinals[n - 70])
            elif n < 90:
                return 'seksen' + ('' if n == 80 else ' ' + ordinals[n - 80])
            elif n < 100:
                return 'doksan' + ('' if n == 90 else ' ' + ordinals[n - 90])
            else:
                return 'yüzüncü'
        else:
            return str(n) + '.'
    
    # Roman rakamlarını bul ve dönüştür (sadece noktalı olanlar)
    def replace_roman(match):
        roman = match.group(1)
        try:
            number = roman_to_int(roman)
            return int_to_turkish_ordinal(number)
        except:
            return match.group(0)
    
    # Roman rakamlarını değiştir - sıra sayılarından önce yap
    text = re.sub(r'\b([IVXLCDM]+)\.(?=\s|[A-Za-zçÇğĞıİöÖşŞüÜ])', replace_roman, text)
    
    return text


def split_sentences(text: str) -> list:
    """
    Metni (cümle, duraklama_ms) çiftlerine böler.
    Nokta sonrası 600ms, virgül/üç nokta sonrası 350ms, tire sonrası 250ms duraklama.

    Returns:
        [(cümle_str, duraklama_ms), ...]
    """
    if not text:
        return []

    # Önce üç noktayı işaretle
    text = re.sub(r'\.{3,}', '<ELLIPSIS>', text)
    text = re.sub(r'—|–', '<DASH>', text)

    segments = []
    # Önce cümle sonlarında böl (. ! ?)
    # Ardından virgül ve tire gibi kısa duraklamalarda böl
    sentence_pattern = re.compile(
        r'([^.!?]+[.!?]+(?:\s|$)|[^.!?]+$)',
        re.UNICODE
    )

    for sentence_match in sentence_pattern.finditer(text):
        sentence = sentence_match.group().strip()
        if not sentence:
            continue

        # Cümle sonu duraklama
        if sentence.endswith('!') or sentence.endswith('?'):
            sentence_pause = 550
        elif sentence.endswith('.'):
            sentence_pause = 500
        else:
            sentence_pause = 300

        # Cümle içinde virgül ve tire'lerde böl
        inner_parts = re.split(r'(<ELLIPSIS>|<DASH>|,)', sentence)
        buffer = ''
        for part in inner_parts:
            if part == '<ELLIPSIS>':
                if buffer.strip():
                    segments.append((buffer.strip(), 280))
                buffer = ''
            elif part == '<DASH>':
                if buffer.strip():
                    segments.append((buffer.strip(), 150))
                buffer = ''
            elif part == ',':
                if buffer.strip():
                    segments.append((buffer.strip(), 180))
                buffer = ''
            else:
                buffer += part

        if buffer.strip():
            segments.append((buffer.strip(), sentence_pause))

    # Sembolleri temizle ve modelin görmesi için uygun noktalama ekle
    cleaned = []
    for seg, pause in segments:
        seg = seg.replace('<ELLIPSIS>', '...').replace('<DASH>', ',')
        seg = re.sub(r'^[\s,.\-!?]+', '', seg)   # sadece başı temizle
        seg = re.sub(r'[\s]+$', '', seg)           # sondaki boşluk temizle
        if not seg or len(seg) < 2:
            continue
        # Sonda noktalama yoksa pause süresine göre ekle
        if not re.search(r'[.!?,]$', seg):
            if pause >= 450:
                seg += '.'
            elif pause >= 200:
                seg += ','
        cleaned.append((seg, pause))

    return cleaned


if __name__ == "__main__":
    # Test örnekleri
    test_cases = [
        "16. yüzyıl",
        "1453 yılında", 
        "50 TL",
        "Dr. Ahmet",
        "Bekle...",
        "XIV. Louis",
        "Harika! Bu çok güzel.",
        "Neden gittin??",
        "3.14 pi sayısı",
        "-5 derece",
        "İÜ'nde okudum."
    ]
    
    print("=== Türkçe Metin Normalizasyonu Test ===\n")
    
    for test in test_cases:
        normalized = normalize(test)
        exaggeration, cfg_weight = detect_emotion(test)
        print(f"Girdi:    {test}")
        print(f"Çıktı:   {normalized}")
        print(f"Duygu:   exaggeration={exaggeration}, cfg_weight={cfg_weight}")
        print("-" * 50)
