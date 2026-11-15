"""Шрифты рендера: изоляция fontconfig и подмена отсутствующих гарнитур.

SB Sans / Golos Text всегда скрыты из рендера (force-subst): колода
версталась под подстановку PowerPoint. Остальным отсутствующим семействам
в профиль LO кладутся переименованные копии метрических клонов из
render_fonts/ (таблица FontSubstitutions в headless не применяется).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from fontTools.ttLib import TTFont

from . import LIBREOFFICE

logging.getLogger("fontTools.ttLib.tables._h_e_a_d").setLevel(logging.ERROR)

# Семейства, которые всегда считаем отсутствующими (по префиксу).
# Колода могла верстаться на машине без них, и PowerPoint подставлял
# шрифт темы с узкими метриками. Настоящий фирменный шрифт шире, поэтому
# рендер всегда идёт с force-subst этих гарнитур.
_FORCE_SUBST_PREFIXES = ["sb sans", "golos text"]

# Изоляция fontconfig выполняется один раз на процесс.
_FONTCONFIG_ISOLATED = False

# Системные гарнитуры-заглушки OOXML, которые не проверяем по fc-list.
_OOXML_FONT_PLACEHOLDERS = {
    "+mj-lt", "+mj-ea", "+mj-cs", "+mn-lt", "+mn-ea", "+mn-cs", "",
}


def _norm_font_name(family: str) -> str:
    """Нормализация имени шрифта как в таблице LO: нижний регистр, без пробелов."""
    return re.sub(r"\s+", "", family.lower())


def _fontconfig_files_of(families: List[str]) -> List[str]:
    """Файлы, которыми fontconfig сейчас резолвит данные семейства
    (вызывать ДО переключения FONTCONFIG_FILE)."""
    files: List[str] = []
    for fam in families:
        try:
            res = subprocess.run(
                ["fc-list", fam, "file"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=30)
            for line in res.stdout.splitlines():
                p = line.strip().rstrip(":").strip()
                if p.lower().endswith((".ttf", ".otf", ".ttc")) \
                        and os.path.exists(p):
                    files.append(p)
        except Exception:
            pass
    return files


def isolate_fontconfig(reject_families: List[str], work_dir: Path) -> None:
    """Построить свой fonts.conf и переключить на него процесс.

    reject-семейства становятся невидимы для fontconfig и LO; каталог
    fc_fonts/ регистрируется для копий-замен.
    """
    global _FONTCONFIG_ISOLATED
    if _FONTCONFIG_ISOLATED:
        return
    fc_dir = work_dir / "fc_fonts"
    fc_dir.mkdir(parents=True, exist_ok=True)
    os.environ["RENDER_FC_FONTS_DIR"] = str(fc_dir)
    # Reject по файлам, а не по имени семейства, чтобы не задеть копии-замены.
    reject_files = _fontconfig_files_of(reject_families) \
        if reject_families else []
    rejects = "".join('<glob>%s</glob>' % p for p in reject_files)
    conf = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        '<fontconfig>\n'
        ' <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
        ' <dir>%s</dir>\n'
        ' <cachedir>%s</cachedir>\n'
        % (fc_dir, work_dir / "fc_cache")
        + (' <selectfont><rejectfont>%s</rejectfont></selectfont>\n'
           % rejects if rejects else '')
        + '</fontconfig>\n')
    conf_path = work_dir / "fonts.conf"
    conf_path.write_text(conf, encoding="utf-8")
    os.environ["FONTCONFIG_FILE"] = str(conf_path)
    _FONTCONFIG_ISOLATED = True


def is_fontconfig_isolated() -> bool:
    """Уже ли переключён fontconfig на изолированный конфиг."""
    return _FONTCONFIG_ISOLATED


def force_substituted(fonts: Set[str]) -> List[str]:
    """Семейства колоды, попадающие под force-subst (по префиксу)."""
    return sorted(
        f for f in fonts
        if any(f.lower().startswith(p) for p in _FORCE_SUBST_PREFIXES))


# Контроль шрифтов

def collect_pptx_fonts(file_path: Path) -> Set[str]:
    """Все гарнитуры из слайдов, layout'ов и master'ов pptx."""
    fonts: Set[str] = set()
    try:
        with zipfile.ZipFile(file_path) as zf:
            for name in zf.namelist():
                # theme не берём: там script-шрифты всех языков — только шум.
                if not re.fullmatch(
                    r"ppt/(slides|slideLayouts|slideMasters)/[^/]+\.xml",
                    name,
                ):
                    continue
                try:
                    xml = zf.read(name).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                fonts.update(re.findall(r'typeface="([^"]+)"', xml))
    except Exception:
        pass
    return {f for f in fonts if f.strip().lower() not in _OOXML_FONT_PLACEHOLDERS}


def _native_font_index() -> Dict[str, Dict[str, str]]:
    """{нормализованное семейство: {style: path}} — перечисление шрифтов ОС
    БЕЗ fontconfig: обход стандартных каталогов с чтением таблицы name.
    Строится один раз."""
    global _NATIVE_FONT_INDEX
    if _NATIVE_FONT_INDEX is not None:
        return _NATIVE_FONT_INDEX
    idx: Dict[str, Dict[str, str]] = {}

    def add(family: str, tail: str, path: str) -> None:
        style = _NATIVE_STYLE_MAP.get(tail)
        if style is None:
            style = tail.title() if tail else "Regular"
        idx.setdefault(_norm_font_name(family), {}).setdefault(style, path)

    files: List[str] = []
    for d in ("/usr/share/fonts", "/usr/local/share/fonts",
              os.path.expanduser("~/.fonts"),
              os.path.expanduser("~/.local/share/fonts"),
              "/Library/Fonts", os.path.expanduser("~/Library/Fonts")):
        if os.path.isdir(d):
            for ext in ("*.ttf", "*.otf", "*.ttc"):
                files.extend(str(p) for p in Path(d).rglob(ext))
    for p in files:
        fam, tail = _font_family_style_from_file(p)
        if fam:
            add(fam, tail, p)
    _NATIVE_FONT_INDEX = idx
    return idx


def missing_fonts(fonts: Set[str]) -> List[str]:
    """Гарнитуры, которых нет в системе (fc-list или нативный индекс ОС)."""
    if not fonts:
        return []
    available: Optional[Set[str]] = None
    try:
        res = subprocess.run(
            ["fc-list", ":", "family"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
        available = {
            fam.strip().lower()
            for line in res.stdout.splitlines()
            for fam in line.split(",")
        }
    except Exception:
        available = None
    if not available:
        # fontconfig недоступен — перечисляем шрифты напрямую из ОС.
        native = _native_font_index()
        if native:
            return sorted(f for f in fonts if _norm_font_name(f) not in native)
        return []
    avail_norm = {_norm_font_name(a) for a in available}
    return sorted(f for f in fonts if _norm_font_name(f) not in avail_norm)


# Подстановка отсутствующих шрифтов средствами LibreOffice

# Кандидаты-замены (имя LO -> человеческое для fc-list).
_LO_SUBST_PRETTY = {
    "carlito": "Carlito",
    "caladea": "Caladea",
    "liberationsans": "Liberation Sans",
    "liberationserif": "Liberation Serif",
    "liberationmono": "Liberation Mono",
    "dejavusans": "DejaVu Sans",
    "dejavuserif": "DejaVu Serif",
    "dejavusansmono": "DejaVu Sans Mono",
}

# Запасной список семейств встроенной таблицы LO (основной — парсинг main.xcd).
_LO_BUILTIN_SUBST_FALLBACK = {
    "arial", "arialnarrow", "arialblack", "arialunicode", "timesnewroman",
    "times", "calibri", "cambria", "carlito", "caladea", "couriernew",
    "courier", "georgia", "verdana", "tahoma", "trebuchet", "trebuchetms",
    "comicsansms", "impact", "bookantiqua", "palatinolinotype", "palatino",
    "garamond", "century", "centuryschoolbook", "monotypecorsiva",
    "symbol", "wingdings", "webdings", "msmincho", "msgothic",
}

_LO_SUBST_TABLE_CACHE: Optional[Dict[str, List[str]]] = None


def _lo_subst_table() -> Dict[str, List[str]]:
    """нормализованное имя -> упорядоченный список замен (SubstFonts main.xcd)."""
    global _LO_SUBST_TABLE_CACHE
    if _LO_SUBST_TABLE_CACHE is not None:
        return _LO_SUBST_TABLE_CACHE
    table: Dict[str, List[str]] = {}
    try:
        xcd = Path(LIBREOFFICE).resolve().parent.parent / "share" / "registry" / "main.xcd"
        if not xcd.exists():
            xcd = Path("/usr/lib/libreoffice/share/registry/main.xcd")
        data = xcd.read_text(encoding="utf-8", errors="ignore")
        i = data.find('<node oor:name="FontSubstitutions">')
        if i < 0:
            raise ValueError("no FontSubstitutions")
        seg = data[i:i + 400000]
        for m in re.finditer(
                r'<node oor:name="([^"]+)" oor:op="replace">(.*?)</node>',
                seg, re.S):
            name, body = m.group(1), m.group(2)
            if name == "en":
                continue
            sm = re.search(
                r'<prop oor:name="SubstFonts">\s*<value>([^<]*)</value>', body)
            table[name] = [c for c in (sm.group(1).split(";") if sm else []) if c]
    except Exception:
        table = {}
    if not table:
        table = {n: [] for n in _LO_BUILTIN_SUBST_FALLBACK}
        table.update({
            "calibri": ["carlito", "liberationsans"],
            "cambria": ["caladea", "liberationserif"],
            "arial": ["liberationsans", "dejavusans"],
            "timesnewroman": ["liberationserif", "dejavuserif"],
            "couriernew": ["liberationmono", "dejavusansmono"],
        })
    _LO_SUBST_TABLE_CACHE = table
    return table


def _lo_builtin_substitutions() -> Set[str]:
    """Множество нормализованных имён, которые LO подставляет сам."""
    table = _lo_subst_table()
    if table:
        return set(table)
    return set(_LO_BUILTIN_SUBST_FALLBACK)


def _lo_subst_installed(family: str) -> bool:
    """Установлена ли замена из встроенной таблицы LO (видна fontconfig).

    Если нет, LO рисует fc-дефолтом (Noto Sans) — семейство снабжаем
    переименованной копией, как любое отсутствующее.
    """
    for cand in _lo_subst_table().get(_norm_font_name(family), []):
        pretty = _LO_SUBST_PRETTY.get(cand, cand)
        if _find_family_faces(pretty).get("Regular"):
            return True
    return False


def _lo_subst_for(family: str) -> str:
    """Шрифт-замена для семейства, которого нет в таблице LO."""
    f = family.lower()
    if any(k in f for k in ("mono", "courier", "consol", "typewriter")):
        return "Liberation Mono"
    if any(k in f for k in ("serif", "times", "georgia", "book", "roman")):
        return "Caladea"
    return "Carlito"


_NATIVE_STYLE_MAP = {"": "Regular", "regular": "Regular", "bold": "Bold",
                     "italic": "Italic", "oblique": "Italic",
                     "bolditalic": "Bold Italic", "boldoblique": "Bold Italic"}

_NATIVE_FONT_INDEX: Optional[Dict[str, Dict[str, str]]] = None


def _font_family_style_from_file(path: str) -> Tuple[str, str]:
    """(семейство, хвост-стиль) из таблицы name: поля 16/17 или 1/2."""
    try:
        try:
            f = TTFont(path, lazy=True)
        except Exception:
            f = TTFont(path, lazy=True, fontNumber=0)  # ttc
        name = f["name"]

        def get(nid: int) -> str:
            rec = (name.getName(nid, 3, 1, 0x409) or name.getName(nid, 1, 0, 0)
                   or name.getName(nid, 3, 1, 0))
            return str(rec) if rec else ""

        fam, sub = get(16) or get(1), get(17) or get(2)
        f.close()
        if fam:
            return fam, re.sub(r"\s+", "", sub.lower())
    except Exception:
        pass
    return "", ""


# Стиль -> вес fontconfig (для нативного индекса ОС без fontconfig)
_STYLE_WEIGHT = {"thin": 0, "hairline": 0, "extralight": 40,
                 "ultralight": 40, "light": 50, "book": 75,
                 "regular": 80, "normal": 80, "roman": 80, "medium": 100,
                 "demibold": 180, "semibold": 180, "bold": 200,
                 "extrabold": 205, "ultrabold": 205, "black": 210,
                 "heavy": 210}

_FC_FACES_CACHE: Dict[str, Dict[str, str]] = {}


def _faces_slots(rows: List[Tuple[str, int, int]]) -> Dict[str, str]:
    """Слоты Regular/Bold/Italic/Bold Italic из (path, вес, наклон).

    Выбирается ближайший вес, как делает fontconfig: Regular+Semibold
    дают Bold-слот на Semibold.
    """
    faces: Dict[str, str] = {}
    roman = [(p, w) for p, w, s in rows if s == 0]
    ital = [(p, w) for p, w, s in rows if s != 0]

    def pick(pool: List[Tuple[str, int]], target: int) -> str:
        return min(pool, key=lambda r: (abs(r[1] - target), r[1]))[0]

    if roman:
        faces["Regular"] = pick(roman, 80)
        faces["Bold"] = pick(roman, 200)
    if ital:
        faces["Italic"] = pick(ital, 80)
        faces["Bold Italic"] = pick(ital, 200)
    return faces


def _find_family_faces(family: str) -> Dict[str, str]:
    """Начертания семейства глазами fontconfig (тот же резолвер, что у LO).

    Слоты подбираются по весу, а не по имени стиля. Если семейства нет —
    {}.
    """
    key = _norm_font_name(family)
    if key in _FC_FACES_CACHE:
        return dict(_FC_FACES_CACHE[key])
    faces: Dict[str, str] = {}
    rows: List[Tuple[str, int, int]] = []
    nat_rows: List[Tuple[str, int, int]] = []
    try:
        res = subprocess.run(
            ["fc-list", family, "file", "style", "weight", "slant"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=30,
        )
        for line in res.stdout.splitlines():
            m = re.match(
                r"^(.*?\.(?:ttf|otf|ttc)):\s*:?style=([^:]*)(?::slant=(\d+))?"
                r"(?::weight=([\d.]+))?",
                line.strip())
            if not m:
                continue
            path = m.group(1)
            try:
                slant = int(m.group(3) or 0)
            except ValueError:
                slant = 0
            try:
                weight = int(float(m.group(4))) if m.group(4) else None
            except ValueError:
                weight = None
            if weight is None:
                st = re.sub(r"[^a-z]", "",
                            m.group(2).split(",")[0].lower())
                st = st.replace("italic", "").replace("oblique", "")
                weight = _STYLE_WEIGHT.get(st, 80)
            rows.append((path, weight, slant))
    except Exception:
        rows = []
    if rows:
        faces = _faces_slots(rows)
    if not faces:
        # fontconfig недоступен или не видит шрифт — нативный реестр ОС
        native = _native_font_index().get(key, {})
        for style, path in native.items():
            st = re.sub(r"[^a-z]", "", style.lower())
            slant = 100 if ("italic" in st or "oblique" in st) else 0
            st = st.replace("italic", "").replace("oblique", "")
            nat_rows.append((path, _STYLE_WEIGHT.get(st, 80), slant))
        if nat_rows:
            faces = _faces_slots(nat_rows)
    _FC_FACES_CACHE[key] = dict(faces)
    return faces


def _bundled_font_dirs() -> List[Path]:
    """Каталоги со шрифтами-компаньонами: render_fonts/ и шрифты установки LO."""
    dirs = [
        Path(__file__).resolve().parent / "render_fonts",
        Path(LIBREOFFICE).resolve().parent.parent / "share" / "fonts" / "truetype",
        Path("/usr/lib/libreoffice/share/fonts/truetype"),
    ]
    seen, out = set(), []
    for d in dirs:
        if d.resolve() not in seen and d.is_dir():
            seen.add(d.resolve())
            out.append(d)
    return out


def _find_family_faces_bundled(family: str) -> Dict[str, str]:
    """Поиск начертаний семейства в каталогах-компаньонах (без fontconfig).
    Ожидаемые имена файлов: Family-Regular.ttf, Family-Bold.ttf, ..."""
    norm = _norm_font_name(family)
    styles = {"": "Regular", "regular": "Regular", "bold": "Bold",
              "italic": "Italic", "bolditalic": "Bold Italic"}
    faces: Dict[str, str] = {}
    for d in _bundled_font_dirs():
        for p in sorted(d.glob("*.ttf")) + sorted(d.glob("*.otf")):
            stem = re.sub(r"[^a-z0-9]+", "", p.stem.lower())
            if not stem.startswith(norm):
                continue
            style = styles.get(stem[len(norm):])
            if style and style not in faces:
                faces[style] = str(p)
    return faces


def _resolve_subst(family: str) -> Tuple[str, Dict[str, str]]:
    """Замена для отсутствующего семейства: (источник, {style: path}).

    Сначала системный fontconfig, затем каталог render_fonts/."""
    pretty = _lo_subst_for(family)
    faces = _find_family_faces(pretty)
    if faces.get("Regular"):
        return pretty, faces
    faces = _find_family_faces_bundled(pretty)
    return f"{pretty} [render_fonts]", faces


def _make_renamed_font(src_ttf: str, dst_path: str, family: str,
                       subfamily: str = "Regular") -> None:
    """Копия шрифта с переписанной таблицей name под другое семейство."""
    f = TTFont(src_ttf)
    full = family if subfamily == "Regular" else f"{family} {subfamily}"
    ps = f"{family}-{subfamily}".replace(" ", "")
    name = f["name"]
    for nid, val in ((1, family), (2, subfamily), (3, full),
                     (4, full), (6, ps), (16, family), (17, subfamily)):
        name.setName(val, nid, 3, 1, 0x409)
        name.setName(val, nid, 1, 0, 0)
    f.save(dst_path)


def seed_lo_profile_fonts(lo_profile: Path,
                          missing: List[str]) -> Dict[str, dict]:
    """Положить в user/fonts профиля переименованные копии шрифтов-замен.

    Возвращает {семейство: {target, faces, err}}; пустой err — успех.
    """
    out: Dict[str, dict] = {}
    if not missing:
        return out
    known = _lo_builtin_substitutions()
    # Из «подставит сам LO» исключаем только тех, чья замена установлена;
    # иначе копию сажаем сами, чтобы LO не рисовал fc-дефолтом.
    todo = [f for f in missing
            if _norm_font_name(f) not in known or not _lo_subst_installed(f)]
    fonts_dir = lo_profile / "user" / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for family in todo:
        source, faces = _resolve_subst(family)
        try:
            stem = re.sub(r"[^A-Za-z0-9]+", "", family)
            _make_renamed_font(faces["Regular"],
                               str(fonts_dir / f"{stem}-Regular.ttf"),
                               family, "Regular")
            for style in ("Bold", "Italic", "Bold Italic"):
                if faces.get(style):
                    _make_renamed_font(
                        faces[style],
                        str(fonts_dir / f"{stem}-{style.replace(' ', '')}.ttf"),
                        family, style)
            # Дублируем копии в fontconfig-видимый каталог на случай
            # сборок LO, игнорирующих user/fonts профиля.
            fc_extra = os.environ.get("RENDER_FC_FONTS_DIR")
            if fc_extra:
                for p in fonts_dir.glob(f"{stem}-*.ttf"):
                    shutil.copy2(str(p), os.path.join(fc_extra, p.name))
                subprocess.run(["fc-cache", "-f"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            out[family] = {"target": source, "faces": faces, "err": ""}
        except Exception as e:
            out[family] = {"target": source, "faces": faces,
                           "err": f"{type(e).__name__}: {e}"}
    return out


def font_substitution_warning(missing: List[str], forced: List[str]) -> str:
    """Текст предупреждения о подмене отсутствующих шрифтов колоды."""
    covered = [f for f in missing
               if _norm_font_name(f) in _lo_builtin_substitutions()
               and _lo_subst_installed(f)]
    custom = [f for f in missing if f not in covered]
    msg = ("в системе нет шрифтов: " + ", ".join(missing)
           + " — подстановка средствами LibreOffice")
    if forced:
        msg = ("force-subst: "
               + ", ".join(forced)
               + " скрыты из рендера — вёрстка колоды "
                 "подогнана под подстановку PowerPoint. ") + msg
    parts = []
    if covered:
        parts.append("встроенная таблица LO: " + ", ".join(covered))
    if custom:
        parts.append("копии шрифтов в профиле LO: "
                     + ", ".join(f"{f}→{_resolve_subst(f)[0]}" for f in custom))
    return msg + " (" + "; ".join(parts) + ")"