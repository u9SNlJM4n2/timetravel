#!/usr/bin/env python3
"""
Epic Seven Guild War Replay (.dat) Parser & Complete Unit Stat Extractor
========================================================================
Decompresses Epic Seven `clanwar-*.dat` replay files (8-byte LE header + raw LZ4
block) and extracts 100% of User (offense) and Enemy (defense) hero builds:
  - Exact Sheet Stats (ATK, DEF, HP, SPD, CRIT%, CDMG%, EFF%, ER%, Dual Attack%)
  - Uncapped CRIT% & CDMG% (revealing overcap hidden by UI caps)
  - Equipment Sets (including Immunity, Penetration, Counter, Lifesteal, Riposte, etc.)
  - Full 6-Piece Equipment Breakdown (slot, tier i85/i88/i90, main stat, substats,
    modification gems, reforge values, Fribbels Gear Score)
  - Exact Artifact & Enhancement Level (+0..+30)
  - Exclusive Equipment (exact stat value, Option #1/#2/#3, and skill description)
  - Memory Imprint Grade & Mode (Imprint Concentration [Self] vs Release [Team])
  - Hero Level, Star Grade, Awakening (0..6), and MolaGora Skill Enhancements
  - Battle-Start Snapshot (In-Battle Starting HP, Turn-1 Order, Initial CR%)
  - Combat Summary (MVP, Damage Dealt, Tanked, Healed, Kills) & Turn-by-Turn Log

Zero mandatory third-party pip dependencies (uses pure Python 3 stdlib;
automatically uses `lz4.block` C extension if installed).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import glob
import json
import math
import os
import re
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import lz4.block as _lz4_c  # type: ignore
except ImportError:
    _lz4_c = None


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_GAMEDATA_DIR = SCRIPT_DIR / "gamedata"
DEFAULT_DATA_DIR = SCRIPT_DIR / "data" / "gw"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"

FRIBBELS_HERODATA_URL = "http://e7-optimizer-game-data.s3-accelerate.amazonaws.com/herodata.json"
FRIBBELS_ARTIFACT_URL = "http://e7-optimizer-game-data.s3-accelerate.amazonaws.com/artifactdata.json"
CECILIA_HERO_LIST_URL = "https://cecilia-bot-api.vercel.app/api/v1/getList?list=hero"
CECILIA_ARTI_LIST_URL = "https://cecilia-bot-api.vercel.app/api/v1/getList?list=artifact"
CECILIA_HERO_ITEM_URL = "https://cecilia-bot-api.vercel.app/api/v1/getItem?list=hero&id={slug}"


# ============================================================================
# Game Constants & Stat Tables
# ============================================================================

SET_META: Dict[str, Dict[str, Any]] = {
    "set_speed":     {"name": "Speed",       "pieces": 4, "stat": "spd_base_pct", "val": 0.25},
    "set_revenge":   {"name": "Revenge",     "pieces": 4, "stat": "spd_base_pct", "val": 0.12},
    "set_revenant":  {"name": "Reversal",    "pieces": 4, "stat": "spd_base_pct", "val": 0.15},
    "set_weak":      {"name": "Weakening",   "pieces": 4, "stat": "spd_base_pct", "val": 0.15},
    "set_att":       {"name": "Attack",      "pieces": 4, "stat": "att_rate",     "val": 0.45},
    "set_cri_dmg":   {"name": "Destruction", "pieces": 4, "stat": "cri_dmg",      "val": 0.60},
    "set_opener":    {"name": "Warfare",     "pieces": 4, "stat": "max_hp_rate",  "val": 0.20},
    "set_counter":   {"name": "Counter",     "pieces": 4, "stat": None,           "val": 0.0},
    "set_vampire":   {"name": "Lifesteal",   "pieces": 4, "stat": None,           "val": 0.0},
    "set_rage":      {"name": "Rage",        "pieces": 4, "stat": None,           "val": 0.0},
    "set_scar":      {"name": "Injury",      "pieces": 4, "stat": None,           "val": 0.0},
    "set_shield":    {"name": "Protection",  "pieces": 4, "stat": None,           "val": 0.0},
    "set_riposte":   {"name": "Riposte",     "pieces": 4, "stat": None,           "val": 0.0},
    "set_max_hp":    {"name": "Health",      "pieces": 2, "stat": "max_hp_rate",  "val": 0.20},
    "set_def":       {"name": "Defense",     "pieces": 2, "stat": "def_rate",     "val": 0.20},
    "set_torrent":   {"name": "Torrent",     "pieces": 2, "stat": "max_hp_rate",  "val": -0.10},
    "set_cri":       {"name": "Critical",    "pieces": 2, "stat": "cri",          "val": 0.12},
    "set_acc":       {"name": "Hit",         "pieces": 2, "stat": "acc",          "val": 0.20},
    "set_res":       {"name": "Resist",      "pieces": 2, "stat": "res",          "val": 0.20},
    "set_coop":      {"name": "Unity",       "pieces": 2, "stat": "coop",         "val": 0.08},
    "set_immune":    {"name": "Immunity",    "pieces": 2, "stat": None,           "val": 0.0},
    "set_penetrate": {"name": "Penetration", "pieces": 2, "stat": None,           "val": 0.0},
    "set_chase":     {"name": "Pursuit",     "pieces": 2, "stat": None,           "val": 0.0},
    "set_might":     {"name": "Fervor",      "pieces": 2, "stat": None,           "val": 0.0},
}

STAT_LABELS: Dict[str, str] = {
    "att": "ATK",
    "def": "DEF",
    "max_hp": "HP",
    "att_rate": "ATK%",
    "def_rate": "DEF%",
    "max_hp_rate": "HP%",
    "speed": "SPD",
    "cri": "CRIT%",
    "cri_dmg": "CDMG%",
    "acc": "EFF%",
    "res": "ER%",
    "coop": "DAC%",
}

SLOT_BY_CHAR: Dict[str, str] = {
    "w": "Weapon",
    "h": "Helmet",
    "a": "Armor",
    "n": "Necklace",
    "r": "Ring",
    "b": "Boots",
}
SLOT_ORDER: Dict[str, int] = {
    "Weapon": 0, "Helmet": 1, "Armor": 2, "Necklace": 3, "Ring": 4, "Boots": 5, "Gear": 6
}

GRADE_NAMES: Dict[int, str] = {5: "Epic", 4: "Heroic", 3: "Rare", 2: "Good", 1: "Normal"}

IMPRINT_GRADES: Dict[int, Dict[int, str]] = {
    5: {1: "B", 2: "A", 3: "S", 4: "SS", 5: "SSS"},
    4: {1: "C", 2: "B", 3: "A", 4: "S", 5: "SS", 6: "SSS"},
    3: {1: "D", 2: "C", 3: "B", 4: "A", 5: "S", 6: "SS", 7: "SSS"},
}

# Formation position (1..4) -> index into Cecilia devotion.slots [Top, Front, Back, Bottom]
POS_NAMES: Dict[int, str] = {1: "Front", 2: "Top", 3: "Bottom", 4: "Back"}
POS_TO_DEVOTION_SLOT_IDX: Dict[int, int] = {1: 1, 2: 0, 3: 3, 4: 2}

# Cumulative hero EXP at Level 60 by base hero star rarity
HERO_LV60_EXP: Dict[int, int] = {5: 1384450, 4: 1110500, 3: 833510, 2: 600000, 1: 450000}

# Cumulative 5-star Artifact EXP thresholds (+0 .. +30), scaled by rarity factor
ARTI_5_XP: List[int] = [
    0, 950, 2000, 3150, 4410, 5500, 7300, 8960, 10780, 12780,
    14980, 17400, 20060, 22990, 26210, 34290, 39000, 44200, 50140, 57000,
    66000, 77450, 85000, 93500, 103200, 115000, 130000, 148000, 168000, 188000, 206040,
]
ARTI_RARITY_XP_SCALE: Dict[int, float] = {5: 1.0, 4: 0.7778, 3: 0.5395, 2: 0.35, 1: 0.20}

ZODIAC_NORM: Dict[str, str] = {
    "aries": "ram", "taurus": "bull", "gemini": "twins", "cancer": "crab",
    "leo": "lion", "virgo": "maiden", "libra": "scales", "scorpio": "scorpion",
    "sagittarius": "archer", "capricorn": "goat", "aquarius": "waterbearer", "pisces": "fish",
    "ram": "ram", "bull": "bull", "twins": "twins", "crab": "crab",
    "lion": "lion", "maiden": "maiden", "scales": "scales", "scorpion": "scorpion",
    "archer": "archer", "goat": "goat", "waterbearer": "waterbearer", "fish": "fish",
}

ZODIAC_GAINS: Dict[str, float] = {
    "att_rate": 0.03, "max_hp_rate": 0.03, "def_rate": 0.03,
    "speed": 2, "cri": 0.04, "cri_dmg": 0.05, "acc": 0.06, "res": 0.06,
}

ZODIAC_TREES: Dict[str, Dict[str, Optional[List[Optional[str]]]]] = {
    "warrior": {
        "ram": ["def_rate", "max_hp_rate", None, "speed", "def_rate", "max_hp_rate"],
        "bull": ["att_rate", "cri_dmg", None, "cri", "att_rate", "cri_dmg"],
        "twins": None,
        "crab": ["max_hp_rate", "def_rate", None, "max_hp_rate", "max_hp_rate", "def_rate"],
        "lion": ["att_rate", "att_rate", None, "cri", "att_rate", "att_rate"],
        "maiden": ["acc", "cri", None, "speed", "acc", "cri"],
        "scales": ["att_rate", "max_hp_rate", None, "def_rate", "att_rate", "max_hp_rate"],
        "scorpion": ["max_hp_rate", "att_rate", None, "def_rate", "max_hp_rate", "att_rate"],
        "archer": ["att_rate", "cri", None, "speed", "att_rate", "cri"],
        "goat": ["max_hp_rate", "max_hp_rate", None, "def_rate", "max_hp_rate", "max_hp_rate"],
        "waterbearer": ["acc", "speed", None, "max_hp_rate", "acc", "speed"],
        "fish": ["def_rate", "res", None, "res", "def_rate", "res"],
    },
    "knight": {
        "ram": ["max_hp_rate", "acc", None, "speed", "max_hp_rate", "acc"],
        "bull": ["att_rate", "att_rate", None, "max_hp_rate", "att_rate", "att_rate"],
        "twins": ["speed", "cri", None, "att_rate", "speed", "cri"],
        "crab": ["max_hp_rate", "def_rate", None, "max_hp_rate", "max_hp_rate", "def_rate"],
        "lion": ["att_rate", "att_rate", None, "cri", "att_rate", "att_rate"],
        "maiden": None,
        "scales": ["max_hp_rate", "def_rate", None, "res", "max_hp_rate", "def_rate"],
        "scorpion": ["max_hp_rate", "max_hp_rate", None, "att_rate", "max_hp_rate", "max_hp_rate"],
        "archer": ["max_hp_rate", "speed", None, "def_rate", "max_hp_rate", "speed"],
        "goat": ["def_rate", "res", None, "def_rate", "def_rate", "res"],
        "waterbearer": ["def_rate", "max_hp_rate", None, "cri", "def_rate", "max_hp_rate"],
        "fish": ["def_rate", "res", None, "max_hp_rate", "def_rate", "res"],
    },
    "assassin": {
        "ram": ["att_rate", "speed", None, "cri", "att_rate", "speed"],
        "bull": ["att_rate", "att_rate", None, "speed", "att_rate", "att_rate"],
        "twins": ["att_rate", "cri", None, "acc", "att_rate", "cri"],
        "crab": ["max_hp_rate", "max_hp_rate", None, "att_rate", "max_hp_rate", "max_hp_rate"],
        "lion": ["att_rate", "att_rate", None, "cri", "att_rate", "att_rate"],
        "maiden": ["max_hp_rate", "acc", None, "speed", "max_hp_rate", "acc"],
        "scales": ["att_rate", "cri_dmg", None, "speed", "att_rate", "cri_dmg"],
        "scorpion": ["att_rate", "max_hp_rate", None, "cri", "att_rate", "max_hp_rate"],
        "archer": ["att_rate", "cri", None, "speed", "att_rate", "cri"],
        "goat": ["acc", "speed", None, "acc", "acc", "speed"],
        "waterbearer": ["speed", "cri", None, "cri", "speed", "cri"],
        "fish": ["acc", "cri", None, "speed", "acc", "cri"],
    },
    "ranger": {
        "ram": None,
        "bull": ["att_rate", "att_rate", None, "cri", "att_rate", "att_rate"],
        "twins": ["acc", "att_rate", None, "att_rate", "acc", "att_rate"],
        "crab": None,
        "lion": ["att_rate", "max_hp_rate", None, "cri", "att_rate", "max_hp_rate"],
        "maiden": ["att_rate", "att_rate", None, "speed", "att_rate", "att_rate"],
        "scales": ["cri", "att_rate", None, "cri_dmg", "cri", "att_rate"],
        "scorpion": ["att_rate", "cri", None, "max_hp_rate", "att_rate", "cri"],
        "archer": ["speed", "speed", None, "att_rate", "speed", "speed"],
        "goat": ["speed", "acc", None, "acc", "speed", "acc"],
        "waterbearer": ["att_rate", "cri", None, "speed", "att_rate", "cri"],
        "fish": ["def_rate", "max_hp_rate", None, "speed", "def_rate", "max_hp_rate"],
    },
    "mage": {
        "ram": ["acc", "cri", None, "att_rate", "acc", "cri"],
        "bull": ["res", "acc", None, "speed", "res", "acc"],
        "twins": ["att_rate", "acc", None, "acc", "att_rate", "acc"],
        "crab": ["att_rate", "att_rate", None, "cri", "att_rate", "att_rate"],
        "lion": ["att_rate", "att_rate", None, "cri", "att_rate", "att_rate"],
        "maiden": ["att_rate", "speed", None, "cri", "att_rate", "speed"],
        "scales": ["max_hp_rate", "max_hp_rate", None, "speed", "max_hp_rate", "max_hp_rate"],
        "scorpion": ["cri", "att_rate", None, "cri_dmg", "cri", "att_rate"],
        "archer": ["att_rate", "cri", None, "speed", "att_rate", "cri"],
        "goat": None,
        "waterbearer": ["att_rate", "acc", None, "max_hp_rate", "att_rate", "acc"],
        "fish": ["speed", "max_hp_rate", None, "speed", "speed", "max_hp_rate"],
    },
    "manauser": {
        "ram": None,
        "bull": ["speed", "res", None, "res", "speed", "res"],
        "twins": ["res", "max_hp_rate", None, "res", "res", "max_hp_rate"],
        "crab": ["max_hp_rate", "max_hp_rate", None, "def_rate", "max_hp_rate", "max_hp_rate"],
        "lion": None,
        "maiden": ["speed", "acc", None, "def_rate", "speed", "acc"],
        "scales": ["max_hp_rate", "speed", None, "def_rate", "max_hp_rate", "speed"],
        "scorpion": ["acc", "max_hp_rate", None, "acc", "acc", "max_hp_rate"],
        "archer": ["speed", "res", None, "acc", "speed", "res"],
        "goat": None,
        "waterbearer": ["att_rate", "speed", None, "att_rate", "att_rate", "speed"],
        "fish": ["max_hp_rate", "def_rate", None, "res", "max_hp_rate", "def_rate"],
    },
}

DEV_TABLE: Dict[str, List[float]] = {
    "att_g3_s2": [0, 36, 54, 72, 90, 108, 126, 144], "att_g3_s4": [0, 30, 45, 60, 75, 90, 105, 120],
    "def_g3_s2": [0, 18, 27, 36, 45, 54, 63, 72], "def_g3_s4": [0, 16, 24, 32, 40, 48, 56, 64],
    "max_hp_g3_s2": [0, 175, 262, 350, 437, 525, 612, 700], "max_hp_g3_s4": [0, 150, 225, 300, 375, 450, 525, 600],
    "speed_g3_s2": [0, 3, 4, 6, 7, 9, 10, 12], "speed_g3_s4": [0, 2, 3, 4, 5, 6, 7, 8],
    "att_rate_g4_s2": [0, 0, .04, .06, .08, .1, .11, .12], "att_rate_g4_s4": [0, 0, .03, .05, .06, .07, .08, .09],
    "def_rate_g4_s2": [0, 0, .04, .06, .08, .1, .11, .12], "def_rate_g4_s4": [0, 0, .03, .05, .06, .07, .08, .09],
    "max_hp_rate_g4_s2": [0, 0, .04, .06, .08, .1, .11, .12], "max_hp_rate_g4_s4": [0, 0, .03, .05, .06, .07, .08, .09],
    "speed_g4_s2": [0, 0, 4, 6, 8, 10, 12, 14], "speed_g4_s4": [0, 0, 3, 4, 6, 7, 9, 10],
    "cri_g4_s2": [0, 0, .05, .07, .09, .11, .13, .15], "cri_g4_s4": [0, 0, .04, .06, .08, .1, .12, .13],
    "acc_g4_s2": [0, 0, .05, .07, .09, .11, .13, .15], "acc_g4_s4": [0, 0, .04, .06, .08, .1, .12, .13],
    "res_g4_s2": [0, 0, .05, .07, .09, .11, .13, .15], "res_g4_s4": [0, 0, .04, .06, .08, .1, .12, .13],
    "coop_g4_s2": [0, 0, .02, .03, .04, .05, .06, .07], "coop_g4_s4": [0, 0, .01, .02, .03, .04, .05, .06],
    "att_rate_g5_s2": [0, 0, 0, .05, .08, .11, .13, .15], "att_rate_g5_s4": [0, 0, 0, .04, .06, .08, .1, .12],
    "def_rate_g5_s2": [0, 0, 0, .05, .08, .11, .13, .15], "def_rate_g5_s4": [0, 0, 0, .04, .06, .08, .1, .12],
    "max_hp_rate_g5_s2": [0, 0, 0, .05, .08, .11, .13, .15], "max_hp_rate_g5_s4": [0, 0, 0, .04, .06, .08, .1, .12],
    "speed_g5_s2": [0, 0, 0, 6, 9, 12, 14, 16], "speed_g5_s4": [0, 0, 0, 4, 6, 8, 10, 12],
    "cri_g5_s2": [0, 0, 0, .06, .09, .12, .15, .18], "cri_g5_s4": [0, 0, 0, .05, .08, .11, .13, .15],
    "acc_g5_s2": [0, 0, 0, .06, .09, .12, .15, .18], "acc_g5_s4": [0, 0, 0, .05, .08, .11, .13, .15],
    "res_g5_s2": [0, 0, 0, .06, .09, .12, .15, .18], "res_g5_s4": [0, 0, 0, .05, .08, .11, .13, .15],
    "coop_g5_s2": [0, 0, 0, .04, .06, .08, .09, .1], "coop_g5_s4": [0, 0, 0, .03, .05, .07, .08, .09],
}

SELF_DEV_TABLE: Dict[str, List[float]] = {
    "att_rate_g5": [0, 0, 0, .07, .11, .15, .18, .21],
    "max_hp_rate_g5": [0, 0, 0, .07, .11, .15, .18, .21],
    "def_rate_g5": [0, 0, 0, .07, .11, .15, .18, .21],
    "cri_g5": [0, 0, 0, .06, .09, .12, .15, .18],
    "acc_g5": [0, 0, 0, .09, .14, .19, .23, .27],
    "res_g5": [0, 0, 0, .09, .14, .19, .23, .27],
    "coop_g5": [0, 0, 0, .04, .06, .08, .1, .12],
    "att_rate_g4": [0, 0, .06, .09, .12, .14, .16, .18],
    "max_hp_rate_g4": [0, 0, .06, .09, .12, .14, .16, .18],
    "def_rate_g4": [0, 0, .06, .09, .12, .14, .16, .18],
    "cri_g4": [0, 0, .05, .07, .09, .11, .13, .15],
    "acc_g4": [0, 0, .08, .12, .15, .18, .21, .24],
    "res_g4": [0, 0, .08, .12, .15, .18, .21, .24],
    "coop_g4": [0, 0, .03, .05, .06, .07, .08, .09],
    "att_g3": [0, 42, 63, 84, 105, 126, 147, 168],
    "max_hp_g3": [0, 230, 345, 460, 575, 690, 805, 920],
    "def_g3": [0, 22, 33, 44, 55, 66, 77, 88],
    "cri_g3": [0, .04, .06, .08, .09, .1, .11, .12],
    "acc_g3": [0, .06, .08, .1, .12, .14, .16, .18],
    "res_g3": [0, .06, .08, .1, .12, .14, .16, .18],
    "coop_g3": [0, .01, .02, .03, .04, .05, .06, .07],
}


# ============================================================================
# LZ4 Container Decoder (.dat & verify_data)
# ============================================================================

def decompress_dat_bytes(raw: bytes) -> Dict[str, Any]:
    """
    Decode an Epic Seven binary container:
      - Bytes 0..3: uint32 LE uncompressed size
      - Bytes 4..7: uint32 LE compressed payload size
      - Bytes 8.. : raw LZ4 block decompressing to UTF-8 JSON
    """
    if len(raw) < 8:
        raise ValueError(f"Payload too short ({len(raw)} bytes); expected 8-byte header + LZ4 block.")
    uncomp_len, comp_len = struct.unpack("<II", raw[:8])
    if comp_len <= 0 or 8 + comp_len > len(raw):
        raise ValueError(
            f"Invalid container header: uncomp={uncomp_len}, comp={comp_len}, file_size={len(raw)}"
        )
    src = memoryview(raw)[8 : 8 + comp_len]

    if _lz4_c is not None:
        out_bytes = _lz4_c.decompress(bytes(src), uncompressed_size=uncomp_len)
        return json.loads(out_bytes.decode("utf-8"))

    dst = bytearray()
    i = 0
    n = len(src)
    while i < n:
        token = src[i]
        i += 1
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                b = src[i]
                i += 1
                lit_len += b
                if b != 255:
                    break
        if lit_len:
            dst.extend(src[i : i + lit_len])
            i += lit_len
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0 or offset > len(dst):
            raise ValueError(f"Corrupt LZ4 match offset {offset} at pos {i} (dst={len(dst)})")
        match_len = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while True:
                b = src[i]
                i += 1
                match_len += b
                if b != 255:
                    break
        start = len(dst) - offset
        if offset >= match_len:
            dst.extend(dst[start : start + match_len])
        else:
            for j in range(match_len):
                dst.append(dst[start + j])

    return json.loads(dst.decode("utf-8"))


def load_replay_dat(path: os.PathLike | str) -> Dict[str, Any]:
    with open(path, "rb") as f:
        raw = f.read()
    return decompress_dat_bytes(raw)


# ============================================================================
# Game Database Management & Live Updater
# ============================================================================

def _fetch_json(url: str, timeout: int = 25) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 E7GWParser/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_dev_grades(dtype: Optional[str], rarity: int, slots: Optional[List[int]]) -> Dict[str, float]:
    if not dtype or not slots:
        return {}
    scount = sum(1 for x in slots if x)
    arr = DEV_TABLE.get(f"{dtype}_g{rarity}_s{scount}")
    if not arr:
        return {}
    labels = ["Z", "D", "C", "B", "A", "S", "SS", "SSS"]
    offset = max(0, rarity - 2)
    return {labels[i]: arr[i] for i in range(offset, len(labels))}


def _build_self_grades(dtype: Optional[str], rarity: int) -> Dict[str, float]:
    if not dtype:
        return {}
    arr = SELF_DEV_TABLE.get(f"{dtype}_g{rarity}")
    if not arr:
        return {}
    labels = ["Z", "D", "C", "B", "A", "S", "SS", "SSS"]
    offset = max(0, rarity - 2)
    return {labels[i]: arr[i] for i in range(offset, len(labels))}


def update_gamedata(gamedata_dir: Path = DEFAULT_GAMEDATA_DIR) -> Tuple[int, int]:
    """Download fresh Hero & Artifact databases from Fribbels S3 + CeciliaBot API."""
    gamedata_dir.mkdir(parents=True, exist_ok=True)
    print("Updating Epic Seven game databases from Fribbels & CeciliaBot...")
    fh = _fetch_json(FRIBBELS_HERODATA_URL)
    fa = _fetch_json(FRIBBELS_ARTIFACT_URL)
    ch = _fetch_json(CECILIA_HERO_LIST_URL)
    ca = _fetch_json(CECILIA_ARTI_LIST_URL)

    fh_by_code = {v["code"]: v for v in fh.values() if isinstance(v, dict) and "code" in v}
    ch_by_code = {h["id"]: h for h in ch if isinstance(h, dict) and "id" in h}

    heroes_out: Dict[str, Any] = {}
    for code in sorted(set(fh_by_code.keys()) | set(ch_by_code.keys())):
        f_item = fh_by_code.get(code, {})
        c_item = ch_by_code.get(code, {})
        rarity = f_item.get("rarity") or c_item.get("rarity") or 5
        zod_raw = (c_item.get("zodiac") or f_item.get("zodiac") or "").lower()
        zod = ZODIAC_NORM.get(zod_raw, zod_raw)

        sd = f_item.get("self_devotion") or c_item.get("self_devotion") or {}
        sd_type = sd.get("type")
        sd_grades = sd.get("grades") or _build_self_grades(sd_type, rarity)

        dev = c_item.get("devotion") or f_item.get("devotion") or {}
        dev_type = dev.get("type")
        dev_slots = dev.get("slots")
        dev_grades = dev.get("grades") or _build_dev_grades(dev_type, rarity, dev_slots)

        lv60 = (f_item.get("calculatedStatus") or {}).get("lv60SixStarFullyAwakened")
        heroes_out[code] = {
            "code": code,
            "name": f_item.get("name") or c_item.get("name") or code,
            "slug": c_item.get("_id") or f_item.get("_id") or "",
            "rarity": rarity,
            "role": f_item.get("role") or c_item.get("role") or "",
            "attribute": f_item.get("attribute") or c_item.get("attribute") or "",
            "zodiac": zod,
            "lv60": lv60,
            "stats": c_item.get("stats"),
            "self_devotion": {"type": sd_type, "grades": sd_grades} if sd_type else None,
            "devotion": {"type": dev_type, "slots": dev_slots, "grades": dev_grades} if dev_type else None,
            "icon": (f_item.get("assets") or {}).get("icon")
            or f"https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/face/{code}_s.png",
        }

    def _norm_key(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    ca_by_name = {a["name"].lower().strip(): a for a in ca if isinstance(a, dict) and "name" in a}
    ca_by_norm = {_norm_key(a["name"]): a for a in ca if isinstance(a, dict) and "name" in a}
    artifacts_out: Dict[str, Any] = {}
    for v in fa.values():
        if not isinstance(v, dict) or "code" not in v:
            continue
        code = v["code"]
        v_name = v.get("name", code)
        c_art = ca_by_name.get(v_name.lower().strip()) or ca_by_norm.get(_norm_key(v_name)) or {}
        art_id = c_art.get("id", "")
        artifacts_out[code] = {
            "code": code,
            "name": v_name,
            "slug": c_art.get("_id", ""),
            "art_id": art_id,
            "rarity": v.get("rarity", 5),
            "role": v.get("role", ""),
            "stats": v.get("stats", {}),
            "icon": f"https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/item_arti/icon_{art_id}.png"
            if art_id
            else "",
        }

    with open(gamedata_dir / "heroes.json", "w", encoding="utf-8") as f:
        json.dump(heroes_out, f, separators=(",", ":"), ensure_ascii=False)
    with open(gamedata_dir / "artifacts.json", "w", encoding="utf-8") as f:
        json.dump(artifacts_out, f, separators=(",", ":"), ensure_ascii=False)
    if not (gamedata_dir / "ee_cache.json").exists():
        with open(gamedata_dir / "ee_cache.json", "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)

    print(f"Database updated: {len(heroes_out)} heroes, {len(artifacts_out)} artifacts saved to {gamedata_dir}")
    return len(heroes_out), len(artifacts_out)


class GameDatabase:
    def __init__(self, gamedata_dir: Path = DEFAULT_GAMEDATA_DIR, offline: bool = False):
        self.gamedata_dir = gamedata_dir
        self.offline = offline
        self._did_auto_refresh = False

        heroes_path = gamedata_dir / "heroes.json"
        artis_path = gamedata_dir / "artifacts.json"
        ee_path = gamedata_dir / "ee_cache.json"

        if (not heroes_path.exists() or not artis_path.exists()) and not offline:
            update_gamedata(gamedata_dir)

        self.heroes: Dict[str, Any] = (
            json.loads(heroes_path.read_text(encoding="utf-8")) if heroes_path.exists() else {}
        )
        self.artifacts: Dict[str, Any] = (
            json.loads(artis_path.read_text(encoding="utf-8")) if artis_path.exists() else {}
        )
        self.ee_cache: Dict[str, Any] = (
            json.loads(ee_path.read_text(encoding="utf-8")) if ee_path.exists() else {}
        )

    def _maybe_auto_refresh(self) -> None:
        if self.offline or self._did_auto_refresh:
            return
        self._did_auto_refresh = True
        try:
            update_gamedata(self.gamedata_dir)
            self.heroes = json.loads((self.gamedata_dir / "heroes.json").read_text(encoding="utf-8"))
            self.artifacts = json.loads((self.gamedata_dir / "artifacts.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    def get_hero(self, code: str) -> Dict[str, Any]:
        if code not in self.heroes:
            self._maybe_auto_refresh()
        return self.heroes.get(code, {
            "code": code, "name": f"Unknown ({code})", "slug": "", "rarity": 5,
            "role": "", "attribute": "", "zodiac": "", "lv60": None, "stats": None,
            "self_devotion": None, "devotion": None, "icon": "",
        })

    def get_artifact(self, code: str) -> Dict[str, Any]:
        if code not in self.artifacts:
            self._maybe_auto_refresh()
        return self.artifacts.get(code, {
            "code": code, "name": f"Unknown Artifact ({code})", "slug": "",
            "art_id": "", "rarity": 5, "role": "", "stats": {},
        })

    def get_ee_info(self, hero_code: str, slug: str) -> Optional[Dict[str, Any]]:
        if hero_code in self.ee_cache:
            return self.ee_cache[hero_code]
        if self.offline or not slug:
            return None
        try:
            item = _fetch_json(CECILIA_HERO_ITEM_URL.format(slug=slug), timeout=8)
            ex_list = item.get("ex_equip") or []
            if ex_list:
                ex0 = ex_list[0]
                entry = {
                    "code": ex0.get("id", ""),
                    "name": ex0.get("name", ""),
                    "stat_type": (ex0.get("stat") or {}).get("type", ""),
                    "options": [
                        {"option": idx + 1, "skill": sk.get("skill"), "description": sk.get("description", "")}
                        for idx, sk in enumerate(ex0.get("skills", []))
                    ],
                }
                self.ee_cache[hero_code] = entry
                with open(self.gamedata_dir / "ee_cache.json", "w", encoding="utf-8") as f:
                    json.dump(self.ee_cache, f, indent=2, ensure_ascii=False)
                return entry
        except Exception:
            pass
        return None


# ============================================================================
# Exact Hero, Gear, Artifact, EE & Imprint Stat Engine
# ============================================================================

def estimate_hero_level(exp: int, rarity: int, stars: int) -> int:
    max_lv_by_stars = min(60, max(1, stars) * 10)
    lv60_exp = HERO_LV60_EXP.get(rarity, 1384450)
    if exp >= lv60_exp:
        return max_lv_by_stars
    if exp <= 0:
        return 1
    est = int(round(60.0 * ((exp / float(lv60_exp)) ** (1.0 / 3.45))))
    return max(1, min(est, max_lv_by_stars))


def compute_hero_base_stats(
    hero: Dict[str, Any], level: int, stars: int, awaken: int, skill_levels: List[Optional[int]]
) -> Dict[str, Any]:
    """
    Compute exact hero base stats (including Zodiac awakenings, SC rune trees,
    and sheet-visible passive skill stats).
    """
    lv60 = hero.get("lv60") or {}
    c_stats = hero.get("stats")

    b_max_atk = 1.0 + (lv60.get("bonusMaxAtkPercent") or 0) / 100.0
    b_max_def = 1.0 + (lv60.get("bonusMaxDefPercent") or 0) / 100.0
    b_max_hp = 1.0 + (lv60.get("bonusMaxHpPercent") or 0) / 100.0

    # Fast exact path for standard 6-star Lv60 fully awakened heroes
    if stars == 6 and level == 60 and awaken >= 6 and lv60:
        base = {
            "atk": lv60["atk"],
            "def": lv60["def"],
            "hp": lv60["hp"],
            "spd": lv60["spd"],
            "chc": lv60["chc"],
            "chd": lv60["chd"],
            "eff": lv60["eff"],
            "efr": lv60["efr"],
            "dac": lv60.get("dac", 0.03),
            "b_max_atk": b_max_atk,
            "b_max_def": b_max_def,
            "b_max_hp": b_max_hp,
            "ovr_cr": lv60.get("overrideAdditionalCr") or 0.0,
            "ovr_cd": lv60.get("overrideAdditionalCd") or 0.0,
            "ovr_spd": lv60.get("overrideAdditionalSpd") or 0,
            "ovr_eff": lv60.get("overrideAdditionalEff") or 0.0,
            "ovr_res": lv60.get("overrideAdditionalRes") or 0.0,
        }
        if lv60.get("overrideAtk"):
            base["atk"] = lv60["overrideAtk"]
        if lv60.get("overrideDef"):
            base["def"] = lv60["overrideDef"]
        if lv60.get("overrideHp"):
            base["hp"] = lv60["overrideHp"]

        # Adjust if a passive-stat hero (e.g., Gunther, Eaton, Lena) has < max mola on S2
        if c_stats and c_stats.get("skills"):
            sk_iter = (
                enumerate(c_stats["skills"])
                if isinstance(c_stats["skills"], list)
                else ((int(k), v) for k, v in c_stats["skills"].items())
            )
            for sk_idx, sk_arr in sk_iter:
                enh_table = sk_arr[1] if len(sk_arr) > 1 else []
                cur_mola = max(0, int(skill_levels[sk_idx] or 0)) if sk_idx < len(skill_levels) else len(enh_table)
                missing = enh_table[cur_mola:]
                for m_step in missing:
                    for item in m_step:
                        st, val = item["type"], item["value"]
                        if st == "cri":
                            base["chc"] = round(base["chc"] - val, 4)
                        elif st == "cri_dmg":
                            base["chd"] = round(base["chd"] - val, 4)
                        elif st == "acc":
                            base["eff"] = round(base["eff"] - val, 4)
                        elif st == "res":
                            base["efr"] = round(base["efr"] - val, 4)
                        elif st == "att_rate" and b_max_atk > 1.0:
                            b_max_atk -= val
                            base["b_max_atk"] = b_max_atk
                        elif st == "def_rate" and b_max_def > 1.0:
                            b_max_def -= val
                            base["b_max_def"] = b_max_def
                        elif st == "max_hp_rate" and b_max_hp > 1.0:
                            b_max_hp -= val
                            base["b_max_hp"] = b_max_hp
        return base

    # General Yuna Engine formula for z < 6, level < 60, or stars < 6
    if c_stats and all(k in c_stats for k in ("int", "bra", "fai", "des")):
        r, a, l_val, c = c_stats["int"], c_stats["bra"], c_stats["fai"], c_stats["des"]
        p = max(math.ceil(level / 10.0), stars)
        hp = math.floor((50 + 1.4 * r) * (level / 3.0 + 1) * (1 + 0.075 * (p - 1)))
        atk = math.floor(0.6 * a * (level / 6.0 + 1) * (1 + 0.075 * (p - 1)))
        dfn = math.floor((30 + 0.3 * l_val) * (level / 8.0 + 1) * (1 + 0.075 * (p - 1)))
        spd = math.floor(60 + c / 1.6)

        acc: Dict[str, float] = {}
        role_trees = ZODIAC_TREES.get(hero.get("role", ""), {})
        tree = role_trees.get(hero.get("zodiac", ""))
        max_awk = min(awaken, math.floor(level / 10.0), 6)
        if tree:
            for t in range(max_awk):
                stat = tree[t]
                if stat:
                    acc[stat] = acc.get(stat, 0.0) + ZODIAC_GAINS[stat] * (2 if t > 2 else 1)
                acc["att"] = acc.get("att", 0.0) + (30 if t > 2 else 20)
                acc["max_hp"] = acc.get("max_hp", 0.0) + (80 if t > 2 else 60)

        for sc in c_stats.get("sc_tree") or []:
            acc[sc["type"]] = acc.get(sc["type"], 0.0) + sc["value"]

        hp = math.floor(hp * (1.0 + acc.get("max_hp_rate", 0.0)) + acc.get("max_hp", 0.0))
        atk = math.floor(atk * (1.0 + acc.get("att_rate", 0.0)) + acc.get("att", 0.0))
        dfn = math.floor(dfn * (1.0 + acc.get("def_rate", 0.0)) + acc.get("def", 0.0))
        spd = int(spd + acc.get("speed", 0.0))

        chc = round(0.15 + acc.get("cri", 0.0), 4)
        chd = round(1.50 + acc.get("cri_dmg", 0.0), 4)
        eff = round(acc.get("acc", 0.0), 4)
        efr = round(acc.get("res", 0.0), 4)
        dac = round(0.03 + acc.get("coop", 0.0), 4)

        # Passive skills (only apply non-bonusMax passives to base; bonusMax handled via b_max_*)
        if c_stats.get("skills"):
            sk_iter2 = (
                enumerate(c_stats["skills"])
                if isinstance(c_stats["skills"], list)
                else ((int(k), v) for k, v in c_stats["skills"].items())
            )
            for sk_idx, sk_arr in sk_iter2:
                base_passives = sk_arr[0] if len(sk_arr) > 0 else []
                enh_table = sk_arr[1] if len(sk_arr) > 1 else []
                cur_mola = max(0, int(skill_levels[sk_idx] or 0)) if sk_idx < len(skill_levels) else 0
                active_items = list(base_passives)
                for step in enh_table[:cur_mola]:
                    active_items.extend(step)
                for item in active_items:
                    st, val = item["type"], item["value"]
                    if st == "cri":
                        chc = round(chc + val, 4)
                    elif st == "cri_dmg":
                        chd = round(chd + val, 4)
                    elif st == "acc":
                        eff = round(eff + val, 4)
                    elif st == "res":
                        efr = round(efr + val, 4)

        return {
            "atk": atk, "def": dfn, "hp": hp, "spd": spd,
            "chc": chc, "chd": chd, "eff": eff, "efr": efr, "dac": dac,
            "b_max_atk": b_max_atk, "b_max_def": b_max_def, "b_max_hp": b_max_hp,
            "ovr_cr": lv60.get("overrideAdditionalCr") or 0.0,
            "ovr_cd": lv60.get("overrideAdditionalCd") or 0.0,
            "ovr_spd": lv60.get("overrideAdditionalSpd") or 0,
            "ovr_eff": lv60.get("overrideAdditionalEff") or 0.0,
            "ovr_res": lv60.get("overrideAdditionalRes") or 0.0,
        }

    # Fallback if neither Lv60 nor Cecilia stats exist
    return {
        "atk": lv60.get("atk", 0), "def": lv60.get("def", 0), "hp": lv60.get("hp", 0),
        "spd": lv60.get("spd", 0), "chc": lv60.get("chc", 0.15), "chd": lv60.get("chd", 1.5),
        "eff": lv60.get("eff", 0.0), "efr": lv60.get("efr", 0.0), "dac": lv60.get("dac", 0.03),
        "b_max_atk": b_max_atk, "b_max_def": b_max_def, "b_max_hp": b_max_hp,
        "ovr_cr": 0.0, "ovr_cd": 0.0, "ovr_spd": 0, "ovr_eff": 0.0, "ovr_res": 0.0,
    }


def extract_init_artifact_cs_levels(
    init_record: List[Dict[str, Any]], uid_to_arti_code: Dict[int, str]
) -> Dict[int, Dict[str, int]]:
    """
    Scan `init_record` for `cs_<arti_code><cs_idx>` states, where `cs_idx` = floor(level / 3) (0..10).
    Returns `{unit_uid: {arti_code: cs_idx}}`.
    """
    out: Dict[int, Dict[str, int]] = {}

    def _record(uid: Any, state_id: str) -> None:
        if not isinstance(uid, int) or uid not in uid_to_arti_code:
            return
        acode = uid_to_arti_code[uid]
        prefix = f"cs_{acode}"
        if state_id.startswith(prefix):
            suffix = state_id[len(prefix):]
            m = re.match(r"^(10|[0-9])(?:_|$)", suffix)
            if m:
                out.setdefault(uid, {}).setdefault(acode, int(m.group(1)))

    for rec in init_record or []:
        sid = rec.get("state_id") or (rec.get("state_data") or {}).get("c") or ""
        if sid:
            _record(rec.get("target"), sid)
        cs_state = rec.get("cs_state")
        if isinstance(cs_state, dict):
            for uid_str, u_obj in cs_state.items():
                if str(uid_str).isdigit() and isinstance(u_obj, dict):
                    uid_i = int(uid_str)
                    for item in u_obj.get("list") or []:
                        if isinstance(item, dict) and item.get("c"):
                            _record(uid_i, item["c"])
    return out


def determine_artifact_level(
    eq: Dict[str, Any], rarity: int, cs_idx: Optional[int]
) -> int:
    sk = eq.get("sk") or 0
    max_lv = 15 + 3 * sk
    e = eq.get("e") or 0
    scale = ARTI_RARITY_XP_SCALE.get(rarity, 1.0)

    xp_lv = 0
    for idx, thresh in enumerate(ARTI_5_XP):
        if e >= thresh * scale * 0.96:
            xp_lv = idx
    xp_lv = min(xp_lv, max_lv)

    if cs_idx is not None:
        lo = cs_idx * 3
        hi = min(cs_idx * 3 + 2, max_lv)
        if lo >= hi:
            return lo
        return max(lo, min(xp_lv, hi))
    return xp_lv


def parse_gear_piece(eq: Dict[str, Any]) -> Dict[str, Any]:
    code = eq.get("code", "")
    is_reforged = code.endswith("_u")
    stripped = re.sub(r"(_chaos)?(_u)?$", "", code)
    slot = SLOT_BY_CHAR.get(stripped[-1] if stripped else "", "Gear")

    e = eq.get("e") or 0
    g = eq.get("g") or 5
    ops = eq.get("op") or []

    # Determine item tier & enhancement level (+0..+15)
    main_type = ops[0][0] if ops else ""
    main_base = ops[0][1] if ops else 0

    if is_reforged:
        ilvl = 90
    elif e >= 93750 or (main_type == "att" and main_base == 103) or (main_type == "max_hp" and main_base == 553) or (main_type == "speed" and main_base == 9):
        ilvl = 88
    elif (main_type == "att" and main_base == 100) or (main_type == "def" and main_base == 60) or (main_type == "max_hp" and main_base == 540) or e >= 82031:
        ilvl = 85
    else:
        ilvl = 78

    max_e = 93750 if ilvl == 88 else (82031 if ilvl >= 85 else 63000)
    if e >= max_e:
        enhance_lv = 15
    elif e <= 0:
        enhance_lv = 0
    else:
        # Approximate +0..+14 from non-reforge/non-mod roll count & EXP curve
        base_subs = max(0, g - 1)
        normal_rolls = sum(1 for s in ops[1:] if not (len(s) >= 3 and s[2] == "u"))
        roll_ups = max(0, normal_rolls - base_subs)
        min_lv = min(15, roll_ups * 3)
        exp_lv = min(14, int((e / float(max_e)) ** 0.32 * 15))
        enhance_lv = max(min_lv, exp_lv)

    main_mult = 5.0 if enhance_lv == 15 else (1.0 + 4.0 * (enhance_lv / 15.0))
    main_val = main_base * main_mult

    # Aggregate substats
    sub_totals: Dict[str, float] = {}
    sub_reforge: Dict[str, float] = {}
    sub_modded: set[str] = set()
    sub_rolls: Dict[str, int] = {}

    for sub in ops[1:]:
        st, val = sub[0], sub[1]
        tag = sub[2] if len(sub) >= 3 else None
        sub_totals[st] = sub_totals.get(st, 0.0) + val
        if tag == "u":
            sub_reforge[st] = sub_reforge.get(st, 0.0) + val
        elif tag == "c":
            sub_modded.add(st)
            sub_rolls[st] = sub_rolls.get(st, 0) + 1
        else:
            sub_rolls[st] = sub_rolls.get(st, 0) + 1

    # Compute Substat Gear Score (Fribbels WSS) & Official In-Game Equipment Score (ES)
    substat_gs = 0.0
    for st, val in sub_totals.items():
        if st in ("att_rate", "def_rate", "max_hp_rate", "acc", "res"):
            substat_gs += val * 100.0
        elif st == "speed":
            substat_gs += val * 2.0
        elif st == "cri":
            substat_gs += val * 100.0 * 1.6
        elif st == "cri_dmg":
            substat_gs += val * 100.0 * (8.0 / 7.0)
        elif st == "att":
            substat_gs += val * (3.46 / 39.0)
        elif st == "def":
            substat_gs += val * (4.99 / 31.0)
        elif st == "max_hp":
            substat_gs += val * (3.09 / 174.0)

    # Official In-Game Equipment Score adds fixed Base Score from item level & enhance (+0..+15):
    #   Lv 88 / Lv 90 (Reforged) +15 = 26 | Lv 85 +15 = 25 | Lv 78-80 +15 = 24 | Lv 75 +15 = 23
    if ilvl >= 88:
        max_base_es = 26.0
    elif ilvl >= 85:
        max_base_es = 25.0
    elif ilvl >= 78:
        max_base_es = 24.0
    elif ilvl >= 72:
        max_base_es = 23.0
    else:
        max_base_es = max(10.0, round(ilvl * 25.0 / 85.0))
    base_es = max_base_es * (1.0 if enhance_lv >= 15 else (0.2 + 0.8 * (enhance_lv / 15.0)))
    equipment_score = int(round(base_es + substat_gs))

    substats_list = []
    for st, tot in sub_totals.items():
        is_pct = st in ("att_rate", "def_rate", "max_hp_rate", "cri", "cri_dmg", "acc", "res", "coop")
        disp = round(tot * 100.0, 1) if is_pct else int(round(tot))
        ref_v = sub_reforge.get(st, 0.0)
        ref_disp = (round(ref_v * 100.0, 1) if is_pct else int(round(ref_v))) if ref_v else 0
        substats_list.append({
            "stat": st,
            "label": STAT_LABELS.get(st, st),
            "value": disp,
            "raw": round(tot, 5),
            "rolls": sub_rolls.get(st, 1),
            "reforge_bonus": ref_disp,
            "modded": st in sub_modded,
            "is_percent": is_pct,
        })

    is_main_pct = main_type in ("att_rate", "def_rate", "max_hp_rate", "cri", "cri_dmg", "acc", "res", "coop")
    main_disp = round(main_val * 100.0, 1) if is_main_pct else int(round(main_val))

    set_code = eq.get("f", "")
    set_name = (SET_META.get(set_code) or {}).get("name", set_code)

    return {
        "slot": slot,
        "code": code,
        "ilvl": ilvl,
        "reforged": is_reforged,
        "rank": GRADE_NAMES.get(g, f"G{g}"),
        "enhance": enhance_lv,
        "set_code": set_code,
        "set": set_name,
        "main": {
            "stat": main_type,
            "label": STAT_LABELS.get(main_type, main_type),
            "value": main_disp,
            "raw": round(main_val, 5),
            "is_percent": is_main_pct,
        },
        "substats": substats_list,
        "equipment_score": equipment_score,
        "gear_score": equipment_score,
        "substat_gs": round(substat_gs, 1),
    }


def format_imprint_bonus(stat_type: str, val: float) -> str:
    label = STAT_LABELS.get(stat_type, stat_type)
    if stat_type in ("att_rate", "def_rate", "max_hp_rate", "cri", "cri_dmg", "acc", "res", "coop"):
        return f"+{round(val * 100, 1)}% {label.replace('%', '')}"
    return f"+{int(round(val))} {label}"


def analyze_team(
    units_raw: List[Dict[str, Any]],
    db: GameDatabase,
    init_arti_cs: Dict[int, Dict[str, int]],
    snap0_states: Dict[str, Any],
    pvp_stats_by_uid: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Analyze all units on one team (sorted by formation position `pos` 1..4)."""
    sorted_raw = sorted(units_raw, key=lambda x: x.get("pos", 99))

    # First pass: compute individual sheet stats & gather active Team Imprint Releases
    team_imprints: List[Dict[str, Any]] = []
    analyzed: List[Dict[str, Any]] = []

    for u_entry in sorted_raw:
        pos = u_entry.get("pos", 1)
        u = u_entry.get("unit", {})
        uid = u.get("id", 0)
        hcode = u.get("code", "")
        hero = db.get_hero(hcode)

        rarity = hero.get("rarity", 5)
        stars = u.get("g", 6)
        awaken = u.get("z", 6)
        exp = u.get("exp", 0)
        level = estimate_hero_level(exp, rarity, stars)
        raw_skills = u.get("s") or [0, 0, 0]
        mola_levels = [max(0, int(s or 0)) for s in raw_skills]

        base = compute_hero_base_stats(hero, level, stars, awaken, raw_skills)

        flat_atk = flat_def = flat_hp = 0.0
        pct_atk = pct_def = pct_hp = 0.0
        spd = cr = cd = eff = res = dac = 0.0

        def _add(stype: str, val: float) -> None:
            nonlocal flat_atk, flat_def, flat_hp, pct_atk, pct_def, pct_hp, spd, cr, cd, eff, res, dac
            if stype == "att":
                flat_atk += val
            elif stype == "def":
                flat_def += val
            elif stype == "max_hp":
                flat_hp += val
            elif stype == "att_rate":
                pct_atk += val
            elif stype == "def_rate":
                pct_def += val
            elif stype == "max_hp_rate":
                pct_hp += val
            elif stype == "speed":
                spd += val
            elif stype == "cri":
                cr += val
            elif stype == "cri_dmg":
                cd += val
            elif stype == "acc":
                eff += val
            elif stype == "res":
                res += val
            elif stype == "coop":
                dac += val

        set_counts: Dict[str, int] = {}
        gear_pieces: List[Dict[str, Any]] = []
        artifact_obj: Optional[Dict[str, Any]] = None
        ee_obj: Optional[Dict[str, Any]] = None

        for eq in u_entry.get("equips") or []:
            ecode = eq.get("code", "")
            if "f" in eq:
                piece = parse_gear_piece(eq)
                gear_pieces.append(piece)
                sc = piece["set_code"]
                set_counts[sc] = set_counts.get(sc, 0) + 1
                _add(piece["main"]["stat"], piece["main"]["raw"])
                for sub in piece["substats"]:
                    _add(sub["stat"], sub["raw"])
            elif ecode.startswith("ex"):
                ops = eq.get("op") or []
                ee_stat_type = ops[0][0] if ops else ""
                ee_stat_val = ops[0][1] if ops else 0.0
                _add(ee_stat_type, ee_stat_val)
                ee_opt_num = ops[1][1] if len(ops) > 1 and len(ops[1]) > 1 else 1
                ee_meta = db.get_ee_info(hcode, hero.get("slug", ""))
                opt_desc = ""
                opt_skill = None
                ee_name = ecode
                if ee_meta:
                    ee_name = ee_meta.get("name") or ecode
                    for o in ee_meta.get("options") or []:
                        if o.get("option") == ee_opt_num:
                            opt_desc = o.get("description", "")
                            opt_skill = o.get("skill")
                is_pct = ee_stat_type in ("att_rate", "def_rate", "max_hp_rate", "cri", "cri_dmg", "acc", "res", "coop")
                ee_obj = {
                    "code": ecode,
                    "name": ee_name,
                    "stat": ee_stat_type,
                    "stat_label": STAT_LABELS.get(ee_stat_type, ee_stat_type),
                    "value": round(ee_stat_val * 100.0, 1) if is_pct else int(round(ee_stat_val)),
                    "is_percent": is_pct,
                    "option": ee_opt_num,
                    "skill": opt_skill,
                    "description": re.sub(r"(\+\+|--|\*\*)", "", opt_desc),
                    "icon": f"https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/item/icon_eq_exclusive_{hcode}.png",
                }
            else:
                arti_meta = db.get_artifact(ecode)
                a_rar = arti_meta.get("rarity", 5)
                cs_idx = (init_arti_cs.get(uid) or {}).get(ecode)
                a_lv = determine_artifact_level(eq, a_rar, cs_idx)
                sk_lb = eq.get("sk") or 0
                mult = 1.0 + 12.0 * (a_lv / 30.0)
                ops_raw = eq.get("op") or []
                a_stats_out: Dict[str, float] = {}
                a_flat_map: Dict[str, float] = {"att": 0.0, "def": 0.0, "max_hp": 0.0}
                if ops_raw:
                    for op in ops_raw:
                        if isinstance(op, (list, tuple)) and len(op) >= 2:
                            st_k = str(op[0])
                            leveled = round(float(op[1] or 0.0) * mult, 1)
                            _add(st_k, leveled)
                            a_stats_out[STAT_LABELS.get(st_k, st_k)] = leveled
                            if st_k in a_flat_map:
                                a_flat_map[st_k] += leveled
                else:
                    mstats = arti_meta.get("stats") or {}
                    for st_k, mk in (("att", "attack"), ("def", "defense"), ("max_hp", "health")):
                        b_val = float(mstats.get(mk) or 0.0)
                        if b_val > 0:
                            leveled = round(b_val * mult, 1)
                            _add(st_k, leveled)
                            a_stats_out[STAT_LABELS.get(st_k, st_k)] = leveled
                            a_flat_map[st_k] = leveled
                art_id = arti_meta.get("art_id", "")
                a_icon = arti_meta.get("icon") or (
                    f"https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/item_arti/icon_{art_id}.png"
                    if art_id
                    else ""
                )
                artifact_obj = {
                    "code": ecode,
                    "name": arti_meta.get("name", ecode),
                    "rarity": a_rar,
                    "role": arti_meta.get("role", ""),
                    "level": a_lv,
                    "limit_break": sk_lb,
                    "max_level": 15 + 3 * sk_lb,
                    "flat_atk": int(round(a_flat_map["att"])),
                    "flat_def": int(round(a_flat_map["def"])),
                    "flat_hp": int(round(a_flat_map["max_hp"])),
                    "stats": a_stats_out,
                    "icon": a_icon,
                }

        gear_pieces.sort(key=lambda p: SLOT_ORDER.get(p["slot"], 99))
        total_es = int(sum(p["equipment_score"] for p in gear_pieces))
        avg_es = round(total_es / len(gear_pieces), 1) if gear_pieces else 0.0
        total_sub_gs = round(sum(p["substat_gs"] for p in gear_pieces), 1)

        # Memory Imprint (Concentration vs Release)
        d_num = u.get("d")
        opt = u.get("opt") or 0
        grade_letter = IMPRINT_GRADES.get(rarity, {}).get(d_num) if d_num else None
        is_self_imprint = bool(grade_letter and ((opt // 10) % 10) == 2)

        imprint_summary = "None"
        imprint_detail: Dict[str, Any] = {"grade": grade_letter, "mode": "None", "bonus": None}
        if grade_letter:
            if is_self_imprint:
                sd = hero.get("self_devotion") or {}
                sd_type = sd.get("type")
                sd_val = (sd.get("grades") or {}).get(grade_letter, 0.0)
                if sd_type and sd_val:
                    _add(sd_type, sd_val)
                    b_str = format_imprint_bonus(sd_type, sd_val)
                    imprint_summary = f"{grade_letter} Self ({b_str})"
                    imprint_detail = {"grade": grade_letter, "mode": "Concentration (Self)", "stat": sd_type, "value": sd_val, "bonus": b_str}
            else:
                dev = hero.get("devotion") or {}
                dev_type = dev.get("type")
                dev_slots = dev.get("slots") or [1, 1, 1, 1]
                dev_val = (dev.get("grades") or {}).get(grade_letter, 0.0)
                b_str = format_imprint_bonus(dev_type, dev_val) if (dev_type and dev_val) else ""
                imprint_summary = f"{grade_letter} Team ({b_str})" if b_str else f"{grade_letter} Team"
                imprint_detail = {
                    "grade": grade_letter, "mode": "Release (Team)",
                    "stat": dev_type, "value": dev_val, "slots": dev_slots, "bonus": b_str,
                }
                if dev_type and dev_val:
                    team_imprints.append({
                        "caster_pos": pos,
                        "caster_name": hero["name"],
                        "stat": dev_type,
                        "value": dev_val,
                        "slots": dev_slots,
                        "bonus": b_str,
                    })

        # Active Equipment Sets
        active_sets: List[str] = []
        used_pieces = 0
        spd_set_bonus = 0
        for sc, count in sorted(set_counts.items(), key=lambda kv: (-SET_META.get(kv[0], {"pieces": 2})["pieces"], kv[0])):
            meta = SET_META.get(sc)
            if not meta:
                continue
            n_sets = count // meta["pieces"]
            for _ in range(n_sets):
                active_sets.append(meta["name"])
                used_pieces += meta["pieces"]
                if meta["stat"] == "spd_base_pct":
                    spd_set_bonus += int(base["spd"] * meta["val"])
                elif meta["stat"]:
                    _add(meta["stat"], meta["val"])
        broken_pieces = len(gear_pieces) - used_pieces

        # Overrides
        cr += base["ovr_cr"]
        cd += base["ovr_cd"]
        spd += base["ovr_spd"]
        eff += base["ovr_eff"]
        res += base["ovr_res"]

        sheet_atk = int((base["atk"] * (1.0 + pct_atk) + flat_atk) * base["b_max_atk"])
        sheet_def = int((base["def"] * (1.0 + pct_def) + flat_def) * base["b_max_def"])
        sheet_hp = int((base["hp"] * (1.0 + pct_hp) + flat_hp) * base["b_max_hp"])
        sheet_spd = int(base["spd"] + spd + spd_set_bonus)

        raw_cr = round((base["chc"] + cr) * 100.0, 1)
        raw_cd = round((base["chd"] + cd) * 100.0, 1)
        raw_eff = round((base["eff"] + eff) * 100.0, 1)
        raw_res = round((base["efr"] + res) * 100.0, 1)
        raw_dac = round((base["dac"] + dac) * 100.0, 1)

        ui_cr = min(100.0, raw_cr)
        ui_cd = min(350.0, raw_cd)
        ui_dac = min(30.0, raw_dac)

        ehp = int(sheet_hp * (sheet_def / 300.0 + 1.0))
        mcd = int(sheet_atk * (ui_cd / 100.0))

        snap_st = snap0_states.get(str(uid)) or {}
        pvp_st = pvp_stats_by_uid.get(uid) or {}

        analyzed.append({
            "uid": uid,
            "pos": pos,
            "pos_name": POS_NAMES.get(pos, f"Pos {pos}"),
            "code": hcode,
            "name": hero["name"],
            "attribute": hero.get("attribute", ""),
            "role": hero.get("role", ""),
            "rarity": rarity,
            "icon": hero.get("icon", ""),
            "level": level,
            "stars": stars,
            "awakening": awaken,
            "molagora": mola_levels,
            "molagora_total": sum(mola_levels),
            "skin_code": u.get("skin_code"),
            "imprint_summary": imprint_summary,
            "imprint": imprint_detail,
            "sets": active_sets,
            "broken_pieces": broken_pieces,
            "gear_count": len(gear_pieces),
            "total_equipment_score": total_es,
            "avg_equipment_score": avg_es,
            "total_gear_score": total_es,
            "total_substat_gs": total_sub_gs,
            "artifact": artifact_obj,
            "exclusive_equipment": ee_obj,
            "sheet_stats": {
                "atk": sheet_atk,
                "def": sheet_def,
                "hp": sheet_hp,
                "spd": sheet_spd,
                "cr": ui_cr,
                "cr_uncapped": raw_cr,
                "cd": ui_cd,
                "cd_uncapped": raw_cd,
                "eff": raw_eff,
                "res": raw_res,
                "dac": ui_dac,
                "dac_uncapped": raw_dac,
                "ehp": ehp,
                "mcd": mcd,
            },
            "_calc_internals": (base, pct_atk, flat_atk, pct_def, flat_def, pct_hp, flat_hp, sheet_spd, raw_cr, raw_eff, raw_res, raw_dac),
            "battle_start": {
                "max_hp": snap_st.get("max_hp"),
                "turn_order": snap_st.get("order"),
                "initial_cr_pct": 100.0 if snap_st.get("order") == 1 else (round(snap_st["et"] / 10.0, 2) if "et" in snap_st else None),
                "post_turn1_cr_pct": round(snap_st["et"] / 10.0, 2) if "et" in snap_st else None,
            },
            "combat_performance": {
                "damage_dealt": int(round(pvp_st.get("att", 0))),
                "damage_tanked": int(round(pvp_st.get("def", 0))),
                "healing": int(round(pvp_st.get("heal", 0))),
                "kills": pvp_st.get("kill_count", 0),
                "turns_taken": pvp_st.get("total_turn", 0),
                "is_mvp": bool(pvp_st.get("is_mvp", False)),
            },
            "gear": gear_pieces,
        })

    # Second pass: apply Team Imprint Releases across formation slots
    for unit_out in analyzed:
        (base, p_atk, f_atk, p_def, f_def, p_hp, f_hp, s_spd, r_cr, r_eff, r_res, r_dac) = unit_out.pop("_calc_internals")
        slot_idx = POS_TO_DEVOTION_SLOT_IDX.get(unit_out["pos"], 0)
        received: List[str] = []

        for imp in team_imprints:
            if imp["caster_pos"] == unit_out["pos"]:
                continue
            slots = imp["slots"]
            if slot_idx < len(slots) and slots[slot_idx]:
                st, val = imp["stat"], imp["value"]
                received.append(f"{imp['bonus']} (from {imp['caster_name']})")
                if st == "att":
                    f_atk += val
                elif st == "att_rate":
                    p_atk += val
                elif st == "def":
                    f_def += val
                elif st == "def_rate":
                    p_def += val
                elif st == "max_hp":
                    f_hp += val
                elif st == "max_hp_rate":
                    p_hp += val
                elif st == "speed":
                    s_spd += int(val)
                elif st == "cri":
                    r_cr = round(r_cr + val * 100.0, 1)
                elif st == "acc":
                    r_eff = round(r_eff + val * 100.0, 1)
                elif st == "res":
                    r_res = round(r_res + val * 100.0, 1)
                elif st == "coop":
                    r_dac = round(r_dac + val * 100.0, 1)

        unit_out["team_imprints_received"] = received
        unit_out["post_imprint_stats"] = {
            "atk": int((base["atk"] * (1.0 + p_atk) + f_atk) * base["b_max_atk"]),
            "def": int((base["def"] * (1.0 + p_def) + f_def) * base["b_max_def"]),
            "hp": int((base["hp"] * (1.0 + p_hp) + f_hp) * base["b_max_hp"]),
            "spd": int(s_spd),
            "cr_uncapped": r_cr,
            "eff": r_eff,
            "res": r_res,
            "dac_uncapped": r_dac,
        }

    return analyzed


# ============================================================================
# Buff/Debuff Metadata, Opening CR Speed-RNG Solver & Rich Battle Timeline
# ============================================================================

STATE_CODE_META: Dict[str, Tuple[str, str]] = {
    # Standard Buffs
    "101": ("Increase Attack", "buff"),
    "102": ("Increase Attack", "buff"),
    "103": ("Increase Defense", "buff"),
    "104": ("Increase Speed", "buff"),
    "105": ("Increase Crit Chance", "buff"),
    "106": ("Continuous Heal", "buff"),
    "107": ("Invincible", "buff"),
    "108": ("Immortal", "buff"),
    "109": ("Stealth", "buff"),
    "110": ("Increase Crit Damage", "buff"),
    "112": ("Skill Nullifier", "buff"),
    "113": ("Counterattack", "buff"),
    "114": ("Reflect", "buff"),
    "115": ("Increase Evasion", "buff"),
    "116": ("Increase Hit Chance", "buff"),
    "117": ("Increase Attack (Greater)", "buff"),
    "118": ("Escort", "buff"),
    "119": ("Increase Effect Resistance", "buff"),
    "120": ("Immunity", "buff"),
    "125": ("Increase Effectiveness", "buff"),
    "130": ("Vigor", "buff"),
    "153": ("Increase Penetration Resistance", "buff"),
    "1298": ("Perception", "buff"),
    "1314": ("Multilayer Barrier", "buff"),
    "1400": ("Phantom Sword", "buff"),
    "1401": ("Neo Phantom Sword", "buff"),
    "1409": ("Archdemon's Might (+70% Evasion)", "buff"),
    "1412": ("Explosives", "buff"),
    "1415": ("Special Friendship", "buff"),
    "1416": ("Rampage", "buff"),
    "1418": ("Ferocious Stand", "buff"),
    "1421": ("Spoils", "buff"),
    "1422": ("Sacred Covenant", "buff"),
    "1429": ("God of Battle", "buff"),
    "1435": ("Dawn", "buff"),
    "1442": ("Divinity", "buff"),
    "shield_caster_att": ("Barrier", "buff"),
    "shield_caster_def": ("Barrier", "buff"),
    "shield_caster_maxhp": ("Barrier", "buff"),
    "stic_protect": ("Barrier", "buff"),
    # Standard & Unique Debuffs
    "201": ("Decrease Attack", "debuff"),
    "202": ("Decrease Attack", "debuff"),
    "203": ("Decrease Defense", "debuff"),
    "204": ("Decrease Speed", "debuff"),
    "205": ("Stun", "debuff"),
    "206": ("Sleep", "debuff"),
    "207": ("Silence", "debuff"),
    "208": ("Provoke", "debuff"),
    "209": ("Poison", "debuff"),
    "210": ("Bleed", "debuff"),
    "211": ("Burn", "debuff"),
    "212": ("Vampiric Touch", "debuff"),
    "213": ("Unhealable", "debuff"),
    "214": ("Cannot Buff", "debuff"),
    "215": ("Burn / Blind", "debuff"),
    "216": ("Target", "debuff"),
    "217": ("Curse", "debuff"),
    "218": ("Bomb", "debuff"),
    "219": ("Restrict", "debuff"),
    "220": ("Redirected Provoke", "debuff"),
    "223": ("Seal", "debuff"),
    "224": ("Injury", "debuff"),
    "225": ("Venom", "debuff"),
    "230": ("Bind", "debuff"),
    "243": ("Fear", "debuff"),
    "1402": ("Omen", "debuff"),
    "1420": ("Pilfer", "debuff"),
    "cs_revive_forbid_text": ("Cannot Revive", "debuff"),
}

HERO_EVASION_SOURCES: Dict[str, str] = {
    "c1176": "Setsuka Evasion Passive",
    "c1168": "Rinak Evasion Passive",
    "c1133": "Violet Evasion Passive",
    "c2032": "Remnant Violet Evasion",
    "c2053": "Lone Wolf Peira Evasion",
    "c2074": "Savior Adin Evasion",
    "c2015": "Assassin Cartuja Team Evasion",
    "c1103": "Celine Evasion",
    "c3024": "Mirsa Evasion Passive",
    "c2103": "Tempest Surin Evasion",
    "c1159": "Shalltear Evasion",
}

ARTIFACT_EVASION_NOTES: Dict[str, str] = {
    "Moonlight Dreamblade": "Moonlight Dreamblade (+20% Evasion)",
    "Violet Talisman": "Violet Talisman (+Evasion)",
    "Shepherd of the Hollow": "Shepherd of the Hollow (+20% Evasion)",
    "Manica of Control": "Manica of Control (+Hit/Crit)",
    "Snow Crystal": "Snow Crystal",
}


def _format_skill_code(skill_id: str) -> str:
    if not skill_id:
        return "Skill"
    m = re.match(r"^sk_c\d+_(\d+)(u?)", skill_id)
    if m:
        num = int(m.group(1))
        sb_suffix = ""
        if num in (1, 2, 3):
            return f"S{num}{sb_suffix}"
        return f"Extra/Passive (S{num})"
    if skill_id.startswith("sk_seteff_"):
        return "Equipment Set Effect"
    return skill_id


def _decode_passive_label(
    text_code: str,
    state_data: Dict[str, Any],
    target_unit: Optional[Dict[str, Any]],
    db: GameDatabase,
) -> Optional[str]:
    """Decode `invoke_passive` codes into readable Hero Passive or Artifact names."""
    c_code = str(state_data.get("c") or "")
    # Artifact proc: e.g., `ef504_name`, `efm32_name`, or `cs_efa29_27`
    m_art = re.match(r"^(ef[a-z0-9]+?)_name$", text_code or "")
    if m_art:
        acode = m_art.group(1)
        art = db.get_artifact(acode)
        if art and not art["name"].startswith("Unknown"):
            if target_unit and target_unit.get("artifact") and target_unit["artifact"]["code"] == acode:
                return f"Artifact: {art['name']} (+{target_unit['artifact']['level']})"
            return f"Artifact: {art['name']}"
        if target_unit and target_unit.get("artifact"):
            return f"Artifact: {target_unit['artifact']['name']} (+{target_unit['artifact']['level']})"
        return f"Artifact ({acode})"

    # Hero skill passive proc: e.g., `sk_c2007_2_sknm` or `pa_c1176_5`
    m_sk = re.match(r"^sk_(c\d+)_(\d+)_sknm$", text_code or "")
    if m_sk:
        hcode, snum = m_sk.group(1), m_sk.group(2)
        hname = db.get_hero(hcode).get("name", hcode)
        return f"{hname} S{snum} Passive"

    if c_code in STATE_CODE_META:
        return STATE_CODE_META[c_code][0]
    m_num = re.match(r"^(\d+)_csnm$", text_code or "")
    if m_num and m_num.group(1) in STATE_CODE_META:
        return STATE_CODE_META[m_num.group(1)][0]

    # Prominent start-of-battle passives without `text`
    if c_code.startswith("pa_c1176_5"):
        return "Setsuka S2 (Team Evasion Aura)"
    if c_code.startswith("pa_c1117_5"):
        return "Belian S2 (Block Enemy Soul Gain)"
    if c_code.startswith("pa_c1178_5"):
        return "Hecate S2 (Cannot Revive Aura)"
    return None


def analyze_opening_cr_rng(
    turn1_winner_uid: Optional[int],
    turn0_snap: Dict[int, Dict[str, Any]],
    pre_t1_pushes: Dict[int, float],
    all_units: Dict[int, Tuple[Dict[str, Any], str]],
) -> Dict[str, Any]:
    """
    Solve the Turn-0 Starting Combat Readiness & 0%-5% Speed RNG rolls across all 6 units
    using the exact `e7calc.xyz/speed-solver` physics + simultaneous linear bounds.
    """
    if not turn1_winner_uid or turn1_winner_uid not in all_units:
        return {"available": False, "units": [], "alerts": []}

    w_unit, w_side = all_units[turn1_winner_uid]
    s1 = max(1, int(w_unit["post_imprint_stats"]["spd"]))
    d1 = pre_t1_pushes.get(turn1_winner_uid, 0.0) / 100.0

    # Solve feasible interval [r1_min, r1_max] in [0.0, 0.05] for Turn-1 winner's RNG roll:
    #   C_i - d_i = R_i + (S_i / S_1) * (1 - d_1 - R_1), with 0 <= R_i <= 0.05
    r1_min, r1_max = 0.0, 0.05
    unit_raw: List[Dict[str, Any]] = []

    for uid, (u_obj, side) in all_units.items():
        si = max(1, int(u_obj["post_imprint_stats"]["spd"]))
        di = pre_t1_pushes.get(uid, 0.0) / 100.0
        st = turn0_snap.get(uid) or {}
        if uid == turn1_winner_uid:
            ci = 1.0
        else:
            ci = float(st.get("et") or 0.0) / 1000.0

        ci_pure = ci - di
        if uid != turn1_winner_uid and si > 0:
            # Allow tiny 0.0006 tolerance for 2-decimal `et` rounding in .dat snapshots
            lo = (1.0 - d1) - (s1 / float(si)) * (ci_pure + 0.0006)
            hi = (1.0 - d1) - (s1 / float(si)) * (ci_pure - 0.05 - 0.0006)
            if lo <= r1_max and hi >= r1_min:
                r1_min = max(r1_min, lo)
                r1_max = min(r1_max, hi)

        unit_raw.append({
            "uid": uid,
            "name": u_obj["name"],
            "icon": u_obj.get("icon", ""),
            "side": side,
            "spd": si,
            "sheet_spd": u_obj["sheet_stats"]["spd"],
            "cr_frac": ci,
            "cr_pure": ci_pure,
            "pre_push_pct": round(di * 100.0, 2),
            "team_order": st.get("order"),
        })

    if r1_min > r1_max:
        mid = max(0.0, min(0.05, 0.5 * (r1_min + r1_max)))
        r1_min = r1_max = mid

    # Sort descending by actual Turn-0 CR (Turn-1 winner at 100% first)
    unit_raw.sort(key=lambda x: (-1.0 if x["uid"] == turn1_winner_uid else -x["cr_frac"], -x["spd"]))

    rows: List[Dict[str, Any]] = []
    for global_rank, item in enumerate(unit_raw, 1):
        uid = item["uid"]
        si = item["spd"]
        ci = item["cr_frac"]
        ci_pure = item["cr_pure"]
        base_cr_pct = round(min(100.0, (si / float(s1)) * 100.0) if uid != turn1_winner_uid else 100.0, 2)
        actual_cr_pct = round(ci * 100.0, 2)

        if uid == turn1_winner_uid:
            ri_min, ri_max = r1_min, r1_max
            e7calc_min_spd = s1
            e7calc_max_spd = s1
        else:
            ri_lo = ci_pure - (si / float(s1)) * (1.0 - d1 - r1_min)
            ri_hi = ci_pure - (si / float(s1)) * (1.0 - d1 - r1_max)
            ri_min = max(0.0, min(0.05, min(ri_lo, ri_hi)))
            ri_max = max(0.0, min(0.05, max(ri_lo, ri_hi)))
            # Exact e7calc.xyz/speed-solver blind formula from CR% & S1 alone:
            e7calc_min_spd = max(1, math.ceil(s1 * max(0.0, ci_pure - 0.05)))
            e7calc_max_spd = max(e7calc_min_spd, math.floor(s1 * (ci_pure / 0.95)))

        ri_mid_pct = round(0.5 * (ri_min + ri_max) * 100.0, 2)
        rows.append({
            "global_order": global_rank,
            "team_order": item["team_order"],
            "uid": uid,
            "name": item["name"],
            "icon": item["icon"],
            "side": item["side"],
            "spd": si,
            "sheet_spd": item["sheet_spd"],
            "base_cr_pct": round((si / float(s1)) * 100.0, 2),
            "actual_cr_pct": actual_cr_pct,
            "net_rng_delta_pct": round(actual_cr_pct - (si / float(s1)) * 100.0, 2),
            "rng_roll_min_pct": round(ri_min * 100.0, 2),
            "rng_roll_max_pct": round(ri_max * 100.0, 2),
            "rng_roll_est_pct": ri_mid_pct,
            "pre_push_pct": item["pre_push_pct"],
            "e7calc_blind_spd_range": [e7calc_min_spd, e7calc_max_spd],
            "is_turn1_winner": uid == turn1_winner_uid,
        })

    # Detect Speed Cuts (slower unit ahead of faster unit) & Exact Speed Ties!
    alerts: List[str] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            diff_cr = round(a["actual_cr_pct"] - b["actual_cr_pct"], 2)
            a_tag = f"[{'Enemy' if a['side'] == 'enemy' else 'User'}] {a['name']}"
            b_tag = f"[{'Enemy' if b['side'] == 'enemy' else 'User'}] {b['name']}"
            if a["spd"] < b["spd"] and a["pre_push_pct"] <= b["pre_push_pct"]:
                alerts.append(
                    f"⚡ SPEED CUT! {a_tag} ({a['spd']} SPD, {a['actual_cr_pct']:.1f}% CR) out-rolled faster "
                    f"{b_tag} ({b['spd']} SPD, {b['actual_cr_pct']:.1f}% CR) by +{diff_cr:.2f}% CR via 0–5% Speed RNG!"
                )
            elif a["spd"] == b["spd"]:
                alerts.append(
                    f"🎲 SPEED TIE ({a['spd']} SPD)! {a_tag} ({a['actual_cr_pct']:.1f}% CR) beat tied "
                    f"{b_tag} ({b['actual_cr_pct']:.1f}% CR) by +{diff_cr:.2f}% CR RNG!"
                )
            elif b["spd"] >= a["spd"] - 8 and a["is_turn1_winner"]:
                # Contested Turn-1 speed race within 5% RNG danger zone
                alerts.append(
                    f"🔥 Close Turn-1 Race: {b_tag} ({b['spd']} SPD) was within Speed-RNG cut range of {a_tag} ({a['spd']} SPD)!"
                )

    return {
        "available": True,
        "turn1_winner_uid": turn1_winner_uid,
        "turn1_winner_name": w_unit["name"],
        "turn1_winner_side": w_side,
        "turn1_winner_spd": s1,
        "units": rows,
        "alerts": alerts,
    }


def build_battle_timeline(
    init_record: List[Dict[str, Any]],
    record_data: List[Dict[str, Any]],
    user_units: List[Dict[str, Any]],
    enemy_units: List[Dict[str, Any]],
    db: GameDatabase,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """
    Reconstruct the complete battle timeline across `init_record` + `record_data`,
    tracking live HP/MaxHP/Injury/Barrier/CR%/Souls/Buffs at every turn and detecting
    hidden RNG checks (15% Absolute Resistance, Evasion/Miss causes, Speed RNG, Crit RNG).
    """
    all_units: Dict[int, Tuple[Dict[str, Any], str]] = {}
    has_setsuka_team_evasion: Dict[str, bool] = {"user": False, "enemy": False}
    for u in user_units:
        all_units[u["uid"]] = (u, "user")
        if u["code"] == "c1176":
            has_setsuka_team_evasion["user"] = True
    for u in enemy_units:
        all_units[u["uid"]] = (u, "enemy")
        if u["code"] == "c1176":
            has_setsuka_team_evasion["enemy"] = True

    def _u_label(uid: Any) -> str:
        if isinstance(uid, int) and uid in all_units:
            u, side = all_units[uid]
            return f"[{'Enemy' if side == 'enemy' else 'User'}] {u['name']}"
        return str(uid)

    def _u_short(uid: Any) -> Tuple[str, str]:
        if isinstance(uid, int) and uid in all_units:
            u, side = all_units[uid]
            return u["name"], side
        return str(uid), "neutral"

    # Build unified event stream (`init_record` holds Turn 0 + any opening AI turns before User's first input!)
    stream: List[Dict[str, Any]] = list(init_record or [])
    for blk in record_data or []:
        stream.extend((blk.get("record_info") or {}).get("records") or [])

    # Initialize live unit & team state trackers
    live_units: Dict[int, Dict[str, Any]] = {}
    for uid, (u, side) in all_units.items():
        hp0 = u["post_imprint_stats"]["hp"]
        live_units[uid] = {
            "hp": hp0,
            "max_hp": hp0,
            "dhp": 0,
            "et": 0.0,
            "order": None,
            "sp": 0,
            "dead": False,
            "barrier": 0,
            "states": {},  # state_instance_id (`a`) -> {"code": c, "name": label, "kind": kind}
        }
    live_souls: Dict[str, int] = {"user": 0, "enemy": 0}

    def _capture_snapshot(active_uid: Optional[int] = None) -> Dict[str, Any]:
        snap_units: List[Dict[str, Any]] = []
        for uid, (u, side) in all_units.items():
            st = live_units[uid]
            effects = []
            seen_eff = set()
            for ef in st["states"].values():
                key = (ef["name"], ef["kind"])
                if key not in seen_eff:
                    seen_eff.add(key)
                    effects.append({"name": ef["name"], "kind": ef["kind"]})
            cr_val = 100.0 if (uid == active_uid and not st["dead"]) else round(float(st.get("et") or 0.0) / 10.0, 1)
            snap_units.append({
                "uid": uid,
                "name": u["name"],
                "icon": u.get("icon", ""),
                "side": side,
                "pos_name": u["pos_name"],
                "hp": max(0, int(round(st.get("hp") or 0))),
                "max_hp": max(1, int(round(st.get("max_hp") or u["post_imprint_stats"]["hp"]))),
                "dhp": max(0, int(round(st.get("dhp") or 0))),
                "barrier": max(0, int(round(st.get("barrier") or 0))),
                "cr_pct": 0.0 if st.get("dead") else cr_val,
                "order": None if st.get("dead") else st.get("order"),
                "sp": int(round(st.get("sp") or 0)),
                "dead": bool(st.get("dead")) or int(round(st.get("hp") or 0)) <= 0,
                "effects": effects[:6],
            })
        return {
            "user_souls": int(round(live_souls["user"])),
            "enemy_souls": int(round(live_souls["enemy"])),
            "units": snap_units,
        }

    turns: List[Dict[str, Any]] = []
    cur_turn: Optional[Dict[str, Any]] = None
    cur_skill_hits: Dict[Tuple[int, int], Dict[str, Any]] = {}
    cur_caster_uid: Optional[int] = None
    cur_skill_id: str = ""
    cur_is_soulburn: bool = False
    pending_counter_uid: Optional[int] = None

    turn1_winner_uid: Optional[int] = None
    turn0_snap_raw: Dict[int, Dict[str, Any]] = {}
    pre_t1_pushes: Dict[int, float] = {}
    seen_turn_1: bool = False
    last_turn_num: int = 0

    rng_counts = {
        "resist_15_pct": 0,
        "resist_er": 0,
        "immunity_blocks": 0,
        "evades_misses": 0,
        "crit_misses": 0,
        "counters": 0,
        "extra_turns": 0,
        "dual_attacks": 0,
        "revives": 0,
        "kills": 0,
    }

    def _ensure_turn0() -> Dict[str, Any]:
        nonlocal cur_turn
        if cur_turn is None:
            cur_turn = {
                "turn": 0,
                "actor_uid": 0,
                "actor": "Battle Start",
                "actor_name": "Battle Start",
                "actor_side": "neutral",
                "actor_icon": "",
                "is_skipped": False,
                "events": [],
                "rich_events": [],
                "tags": [],
                "start_snapshot": _capture_snapshot(),
            }
            turns.append(cur_turn)
        return cur_turn

    def _add_tag(t: Dict[str, Any], tag: str) -> None:
        if tag not in t["tags"]:
            t["tags"].append(tag)

    def _explain_miss(attacker_uid: int, target_uid: int, is_weak: bool) -> List[str]:
        reasons: List[str] = []
        if is_weak:
            reasons.append("Elemental Disadvantage (-50% Hit)")
        if attacker_uid in all_units:
            a_st = live_units[attacker_uid]
            if any("Blind" in ef["name"] or "Hit Chance" in ef["name"] for ef in a_st["states"].values() if ef["kind"] == "debuff"):
                reasons.append("Attacker Blinded (-50% Hit)")
        if target_uid in all_units:
            t_u, t_side = all_units[target_uid]
            t_st = live_units[target_uid]
            for ef in t_st["states"].values():
                if "Archdemon's Might" in ef["name"]:
                    reasons.append("Archdemon's Might (+70% Evasion)")
                elif "Increase Evasion" in ef["name"]:
                    reasons.append("Evasion Buff (+50% Evasion)")
            if t_u["code"] in HERO_EVASION_SOURCES:
                reasons.append(HERO_EVASION_SOURCES[t_u["code"]])
            elif has_setsuka_team_evasion.get(t_side) and t_u["code"] != "c1176":
                reasons.append("Setsuka Ally Evasion Passive")
            art_name = (t_u.get("artifact") or {}).get("name", "")
            if art_name in ARTIFACT_EVASION_NOTES:
                reasons.append(ARTIFACT_EVASION_NOTES[art_name])
        if not reasons:
            reasons.append("Evasion / Hit-Chance RNG Roll")
        return reasons

    def _flush_hits() -> None:
        nonlocal cur_skill_hits
        if not cur_skill_hits or cur_turn is None:
            cur_skill_hits = {}
            return
        for (frm_uid, tgt_uid), h in cur_skill_hits.items():
            a_name, a_side = _u_short(frm_uid)
            t_name, t_side = _u_short(tgt_uid)
            badges: List[Dict[str, str]] = []
            rng_note = ""

            if h["nullified"] and h["hp_dmg"] == 0:
                badges.append({"text": "🛡 SKILL NULLIFIER / INVINCIBLE", "tone": "cyan"})
            elif h["miss"]:
                rng_counts["evades_misses"] += 1
                _add_tag(cur_turn, "evade")
                _add_tag(cur_turn, "rng")
                reasons = _explain_miss(frm_uid, tgt_uid, h["weak"])
                badges.append({"text": "💨 EVADED / MISS", "tone": "amber"})
                rng_note = " + ".join(reasons)
            elif h["crit"]:
                badges.append({"text": "💥 CRIT", "tone": "red"})
            elif h["smite"]:
                badges.append({"text": "🔨 CRUSHING", "tone": "purple"})

            # Check if a high-crit hero (<100% CRIT) missed a crit on a non-miss hit!
            if not h["miss"] and not h["crit"] and not h["weak"] and frm_uid in all_units:
                u_cr = all_units[frm_uid][0]["post_imprint_stats"].get("cr_uncapped", 15.0)
                if 70.0 <= u_cr < 100.0:
                    rng_counts["crit_misses"] += 1
                    _add_tag(cur_turn, "rng")
                    badges.append({"text": f"🎲 NO CRIT @ {u_cr:.0f}% CRIT!", "tone": "magenta"})

            if h["coop"]:
                rng_counts["dual_attacks"] += 1
                _add_tag(cur_turn, "extra")
                badges.append({"text": "🤝 DUAL ATTACK", "tone": "cyan"})
            if h["shield_dmg"] > 0:
                badges.append({"text": f"🛡 {h['shield_dmg']:,} Barrier Absorbed", "tone": "blue"})

            # Update tracked barrier down by absorbed shield damage
            if tgt_uid in live_units and h["shield_dmg"] > 0:
                live_units[tgt_uid]["barrier"] = max(0, live_units[tgt_uid]["barrier"] - h["shield_dmg"])

            shown_dmg = 0 if (h["nullified"] and h["hp_dmg"] == 0) else h["dmg"]
            plain_tags = [b["text"] for b in badges]
            tag_str = f" ({', '.join(plain_tags)})" if plain_tags else ""
            note_str = f" — {rng_note}" if rng_note else ""
            plain_line = (
                f"{_u_label(frm_uid)} hit {_u_label(tgt_uid)} for {shown_dmg:,} dmg"
                f"{f' [{h['hits']} hits]' if h['hits'] > 1 else ''}{tag_str}{note_str}"
            )
            cur_turn["events"].append(plain_line)
            cur_turn["rich_events"].append({
                "kind": "hit",
                "actor_uid": frm_uid,
                "actor_name": a_name,
                "actor_side": a_side,
                "target_uid": tgt_uid,
                "target_name": t_name,
                "target_side": t_side,
                "damage": shown_dmg,
                "hp_damage": h["hp_dmg"],
                "shield_damage": h["shield_dmg"],
                "hits": h["hits"],
                "miss": h["miss"],
                "crit": h["crit"],
                "nullified": h["nullified"] and h["hp_dmg"] == 0,
                "badges": badges,
                "detail": rng_note,
                "text": plain_line,
            })
        cur_skill_hits = {}

    for r in stream:
        rtype = r.get("type")

        if rtype == "SNAP":
            us = r.get("unit_state")
            if isinstance(us, dict):
                for k, v in us.items():
                    if str(k).isdigit():
                        uid_i = int(k)
                        if uid_i in live_units and isinstance(v, dict):
                            live_units[uid_i].update(v)
                            if v.get("hp") is not None and v["hp"] <= 0:
                                live_units[uid_i]["dead"] = True
                                live_units[uid_i]["barrier"] = 0
            ts = r.get("team_state")
            if isinstance(ts, dict):
                if isinstance(ts.get("friend"), dict) and "soul_piece" in ts["friend"]:
                    live_souls["user"] = int(ts["friend"]["soul_piece"] // 10) if ts["friend"]["soul_piece"] >= 100 else int(ts["friend"]["soul_piece"])
                if isinstance(ts.get("enemy"), dict) and "soul_piece" in ts["enemy"]:
                    live_souls["enemy"] = int(ts["enemy"]["soul_piece"] // 10) if ts["enemy"]["soul_piece"] >= 100 else int(ts["enemy"]["soul_piece"])

        elif rtype == "HANDLE":
            _flush_hits()
            tnum = int(r.get("turn_count") or 0)
            owner = r.get("turn_owner")
            if tnum == 0:
                _ensure_turn0()
                continue
            if tnum == 1 and not seen_turn_1:
                seen_turn_1 = True
                turn1_winner_uid = owner if isinstance(owner, int) else None
                turn0_snap_raw = {uid: dict(st) for uid, st in live_units.items()}
                if cur_turn is not None and cur_turn["turn"] == 0:
                    cur_turn["start_snapshot"] = _capture_snapshot(turn1_winner_uid)

            last_turn_num = tnum
            a_name, a_side = _u_short(owner)
            a_icon = all_units[owner][0].get("icon", "") if isinstance(owner, int) and owner in all_units else ""
            cur_caster_uid = owner if isinstance(owner, int) else None
            pending_counter_uid = None
            cur_turn = {
                "turn": tnum,
                "actor_uid": owner,
                "actor": _u_label(owner),
                "actor_name": a_name,
                "actor_side": a_side,
                "actor_icon": a_icon,
                "is_skipped": False,
                "events": [],
                "rich_events": [],
                "tags": [],
                "start_snapshot": _capture_snapshot(owner if isinstance(owner, int) else None),
            }
            turns.append(cur_turn)

        elif rtype == "skip_turn":
            _flush_hits()
            tgt = r.get("target")
            reason = str(r.get("reason") or "incapacitated").upper()
            last_turn_num += 1
            a_name, a_side = _u_short(tgt)
            a_icon = all_units[tgt][0].get("icon", "") if isinstance(tgt, int) and tgt in all_units else ""
            skip_turn_obj = {
                "turn": last_turn_num,
                "actor_uid": tgt,
                "actor": _u_label(tgt),
                "actor_name": a_name,
                "actor_side": a_side,
                "actor_icon": a_icon,
                "is_skipped": True,
                "events": [f"{_u_label(tgt)} skipped turn due to {reason}!"],
                "rich_events": [{
                    "kind": "skip_turn",
                    "actor_uid": tgt,
                    "actor_name": a_name,
                    "actor_side": a_side,
                    "badges": [{"text": f"💫 TURN SKIPPED ({reason})", "tone": "amber"}],
                    "detail": f"Reached 100% CR while afflicted with {reason.title()}",
                    "text": f"{_u_label(tgt)} skipped turn due to {reason}!",
                }],
                "tags": ["skip"],
                "start_snapshot": _capture_snapshot(tgt if isinstance(tgt, int) else None),
            }
            turns.append(skip_turn_obj)
            cur_turn = skip_turn_obj

        elif rtype == "text":
            txt = r.get("text")
            tgt = r.get("target")
            if txt == "sk_counter" and isinstance(tgt, int):
                _flush_hits()
                pending_counter_uid = tgt
                rng_counts["counters"] += 1
                t_obj = _ensure_turn0()
                _add_tag(t_obj, "passive")
                _add_tag(t_obj, "rng")

        elif rtype == "invoke_passive":
            _flush_hits()
            tgt = r.get("target")
            sd = r.get("state_data") or {}
            txt = r.get("text") or ""
            u_meta = all_units[tgt][0] if isinstance(tgt, int) and tgt in all_units else None
            label = _decode_passive_label(txt, sd, u_meta, db)
            if label and isinstance(tgt, int):
                t_obj = _ensure_turn0()
                _add_tag(t_obj, "passive")
                a_name, a_side = _u_short(tgt)
                line = f"{_u_label(tgt)} triggered {label}"
                # Deduplicate back-to-back identical passive triggers (e.g., multi-target aura registration)
                if not t_obj["events"] or t_obj["events"][-1] != line:
                    t_obj["events"].append(line)
                    t_obj["rich_events"].append({
                        "kind": "passive",
                        "actor_uid": tgt,
                        "actor_name": a_name,
                        "actor_side": a_side,
                        "badges": [{"text": f"✨ {label}", "tone": "purple"}],
                        "detail": "",
                        "text": line,
                    })

        elif rtype == "@skill_start":
            _flush_hits()
            t_obj = _ensure_turn0()
            ai = r.get("att_info") or {}
            a_uid = ai.get("a_unit")
            d_uid = ai.get("d_unit")
            cur_caster_uid = a_uid if isinstance(a_uid, int) else cur_caster_uid
            cur_skill_id = str(ai.get("skill_id") or "")
            cur_is_soulburn = bool(ai.get("is_soulburn"))
            sk_label = _format_skill_code(cur_skill_id)
            a_name, a_side = _u_short(a_uid)
            d_name, d_side = _u_short(d_uid) if (d_uid and d_uid != a_uid) else ("", "")

            badges = []
            if pending_counter_uid == a_uid:
                badges.append({"text": "⚔ COUNTERATTACK", "tone": "purple"})
                pending_counter_uid = None
            if cur_is_soulburn:
                badges.append({"text": "🔥 SOULBURN", "tone": "cyan"})

            sb = " [SOULBURN]" if cur_is_soulburn else ""
            tgt_part = f" -> {_u_label(d_uid)}" if d_uid and d_uid != a_uid else ""
            line = f"{_u_label(a_uid)} used {sk_label}{sb}{tgt_part}"
            m_sk_ico = re.match(r"^sk_(c\d+)_([123])$", cur_skill_id)
            sk_icon = (
                f"https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/skill/sk_{m_sk_ico.group(1)}_{m_sk_ico.group(2)}.png"
                if m_sk_ico
                else ""
            )
            t_obj["events"].append(line)
            t_obj["rich_events"].append({
                "kind": "skill",
                "actor_uid": a_uid,
                "actor_name": a_name,
                "actor_side": a_side,
                "target_uid": d_uid if d_uid != a_uid else None,
                "target_name": d_name,
                "target_side": d_side,
                "skill_label": sk_label,
                "skill_id": cur_skill_id,
                "skill_icon": sk_icon,
                "soulburn": cur_is_soulburn,
                "badges": badges,
                "detail": "",
                "text": line,
            })

        elif rtype == "attack":
            tot_dmg = r.get("total_damage")
            tgt = r.get("target")
            frm = r.get("from")
            if tot_dmg is not None and isinstance(tgt, int) and isinstance(frm, int):
                key = (frm, tgt)
                prev = cur_skill_hits.get(key)
                sub_hp = int(round(r.get("dec_hp") if r.get("dec_hp") is not None else (r.get("damage") or 0)))
                sub_sh = int(round(r.get("shield") or 0))
                cur_skill_hits[key] = {
                    "dmg": int(round(tot_dmg)),
                    "hp_dmg": (prev["hp_dmg"] if prev else 0) + sub_hp,
                    "shield_dmg": (prev["shield_dmg"] if prev else 0) + sub_sh,
                    "hits": int(r.get("tot_hit") or (prev["hits"] + 1 if prev else 1)),
                    "crit": bool(r.get("critical")) or (prev["crit"] if prev else False),
                    "miss": bool(r.get("miss")) or (prev["miss"] if prev else False),
                    "smite": bool(r.get("smite")) or (prev["smite"] if prev else False),
                    "weak": bool(r.get("weak")) or (prev["weak"] if prev else False),
                    "coop": bool(r.get("is_coop")) or (prev["coop"] if prev else False),
                    "nullified": bool(r.get("antiskilldamage")) or (prev["nullified"] if prev else False),
                }

        elif rtype == "add_state":
            sd = r.get("state_data") or {}
            c_code = str(sd.get("c") or "")
            inst_id = str(sd.get("a") or "")
            tgt = r.get("target")
            if isinstance(tgt, int) and tgt in live_units and c_code in STATE_CODE_META:
                b_name, b_kind = STATE_CODE_META[c_code]
                # Disambiguate `215` (Furious Burn vs Blind)
                g_sk = str(sd.get("g") or "")
                if c_code == "215":
                    b_name = "Burn" if "c1087" in g_sk else "Decrease Hit Chance (Blind)"
                dur = sd.get("f")
                sh_amt = int(round(sd.get("i") or 0))
                if b_name == "Barrier" and sh_amt > 0:
                    live_units[tgt]["barrier"] = max(live_units[tgt]["barrier"], sh_amt)
                if inst_id:
                    live_units[tgt]["states"][inst_id] = {"code": c_code, "name": b_name, "kind": b_kind}
                if seen_turn_1 or b_name in ("Barrier", "Immunity", "Defensive Magic", "Archdemon's Might (+70% Evasion)"):
                    _flush_hits()
                    t_obj = _ensure_turn0()
                    t_name, t_side = _u_short(tgt)
                    dur_s = f" ({dur}T)" if isinstance(dur, int) and 0 < dur < 50 else ""
                    amt_s = f" (+{sh_amt:,})" if (b_name == "Barrier" and sh_amt > 0) else ""
                    tone = "red" if b_kind == "debuff" else "green"
                    icon = "⬇" if b_kind == "debuff" else "⬆"
                    line = f"{_u_label(tgt)} gained {b_kind}: {b_name}{amt_s}{dur_s}"
                    t_obj["events"].append(line)
                    t_obj["rich_events"].append({
                        "kind": b_kind,
                        "target_uid": tgt,
                        "target_name": t_name,
                        "target_side": t_side,
                        "badges": [{"text": f"{icon} {b_name}{amt_s}{dur_s}", "tone": tone}],
                        "detail": "",
                        "text": line,
                    })

        elif rtype == "remove_state":
            tgt = r.get("target")
            rem_id = str(r.get("remove") or "")
            if isinstance(tgt, int) and tgt in live_units and rem_id in live_units[tgt]["states"]:
                popped = live_units[tgt]["states"].pop(rem_id)
                if popped["name"] == "Barrier":
                    live_units[tgt]["barrier"] = 0

        elif rtype == "resist_state":
            _flush_hits()
            t_obj = _ensure_turn0()
            tgt = r.get("target")
            t_name, t_side = _u_short(tgt)

            # Determine opposing-team caster & compute exact 15% vs ER check!
            caster_uid = cur_caster_uid
            if isinstance(tgt, int) and tgt in all_units:
                if not (isinstance(caster_uid, int) and caster_uid in all_units and all_units[caster_uid][1] != t_side):
                    t_actor = t_obj.get("actor_uid")
                    if isinstance(t_actor, int) and t_actor in all_units and all_units[t_actor][1] != t_side:
                        caster_uid = t_actor

            caster_eff = 0.0
            caster_name = "Attacker"
            if isinstance(caster_uid, int) and caster_uid in all_units:
                c_u, _ = all_units[caster_uid]
                caster_name = c_u["name"]
                caster_eff = float(c_u["post_imprint_stats"]["eff"])
                if any(ef["code"] == "125" for ef in live_units[caster_uid]["states"].values()):
                    caster_eff += 50.0

            target_er = 0.0
            if isinstance(tgt, int) and tgt in all_units:
                t_u, _ = all_units[tgt]
                target_er = float(t_u["post_imprint_stats"]["res"])
                if any(ef["code"] == "119" for ef in live_units[tgt]["states"].values()):
                    target_er += 50.0

            raw_diff = target_er - caster_eff
            res_prob = max(15.0, min(100.0, raw_diff))
            _add_tag(t_obj, "resist")
            _add_tag(t_obj, "rng")

            if raw_diff <= 15.0:
                rng_counts["resist_15_pct"] += 1
                _add_tag(t_obj, "resist15")
                badge = {"text": "🎲 15% ABSOLUTE RESIST!", "tone": "magenta"}
                detail = f"{caster_name} ({caster_eff:.1f}% EFF) vs {t_name} ({target_er:.1f}% ER) → Innate 15% RNG Floor Triggered!"
            elif raw_diff < 100.0:
                rng_counts["resist_er"] += 1
                badge = {"text": f"🎲 ER RESIST ({res_prob:.0f}% Chance)", "tone": "gold"}
                detail = f"{caster_name} ({caster_eff:.1f}% EFF) vs {t_name} ({target_er:.1f}% ER) → Won {res_prob:.1f}% ER Roll"
            else:
                rng_counts["resist_er"] += 1
                badge = {"text": "✨ 100% ER RESIST", "tone": "cyan"}
                detail = f"{t_name} ({target_er:.1f}% ER) >= {caster_name} ({caster_eff:.1f}% EFF) + 100% → Guaranteed Resist"

            line = f"{_u_label(tgt)} RESISTED debuff [{badge['text']}] ({detail})"
            t_obj["events"].append(line)
            t_obj["rich_events"].append({
                "kind": "resist",
                "actor_uid": caster_uid,
                "target_uid": tgt,
                "target_name": t_name,
                "target_side": t_side,
                "is_15_pct": raw_diff <= 15.0,
                "resist_prob_pct": round(res_prob, 1),
                "caster_eff": round(caster_eff, 1),
                "target_er": round(target_er, 1),
                "badges": [badge],
                "detail": detail,
                "text": line,
            })

        elif rtype == "immune":
            _flush_hits()
            t_obj = _ensure_turn0()
            tgt = r.get("target")
            t_name, t_side = _u_short(tgt)
            rng_counts["immunity_blocks"] += 1
            _add_tag(t_obj, "resist")
            line = f"{_u_label(tgt)} blocked debuff with IMMUNITY"
            if not t_obj["events"] or t_obj["events"][-1] != line:
                t_obj["events"].append(line)
                t_obj["rich_events"].append({
                    "kind": "immune",
                    "target_uid": tgt,
                    "target_name": t_name,
                    "target_side": t_side,
                    "badges": [{"text": "🛡 IMMUNITY BLOCKED", "tone": "blue"}],
                    "detail": "Debuff negated by Immunity buff",
                    "text": line,
                })

        elif rtype == "add_tick":
            _flush_hits()
            tgt = r.get("target")
            tick = float(r.get("tick") or 0.0)
            cr_delta = round(tick / 10.0, 1)
            if isinstance(tgt, int) and abs(cr_delta) >= 0.5:
                if not seen_turn_1:
                    pre_t1_pushes[tgt] = pre_t1_pushes.get(tgt, 0.0) + cr_delta
                t_obj = _ensure_turn0()
                _add_tag(t_obj, "cr")
                t_name, t_side = _u_short(tgt)
                sign = "+" if cr_delta > 0 else ""
                tone = "cyan" if cr_delta > 0 else "amber"
                label = f"⚡ {sign}{cr_delta:.1f}% CR {'Push' if cr_delta > 0 else 'Pushback'}"
                line = f"{_u_label(tgt)} Combat Readiness {sign}{cr_delta:.1f}%"
                t_obj["events"].append(line)
                t_obj["rich_events"].append({
                    "kind": "cr_push",
                    "target_uid": tgt,
                    "target_name": t_name,
                    "target_side": t_side,
                    "cr_delta_pct": cr_delta,
                    "badges": [{"text": label, "tone": tone}],
                    "detail": "",
                    "text": line,
                })

        elif rtype == "add_turn":
            _flush_hits()
            tgt = r.get("target")
            if isinstance(tgt, int):
                rng_counts["extra_turns"] += 1
                t_obj = _ensure_turn0()
                _add_tag(t_obj, "extra")
                a_name, a_side = _u_short(tgt)
                line = f"{_u_label(tgt)} granted an EXTRA TURN!"
                t_obj["events"].append(line)
                t_obj["rich_events"].append({
                    "kind": "extra_turn",
                    "actor_uid": tgt,
                    "actor_name": a_name,
                    "actor_side": a_side,
                    "badges": [{"text": "⚡ EXTRA TURN", "tone": "gold"}],
                    "detail": "",
                    "text": line,
                })

        elif rtype == "heal":
            amt = int(round(r.get("heal") or 0))
            tgt = r.get("target")
            if amt > 0 and isinstance(tgt, int):
                _flush_hits()
                t_obj = _ensure_turn0()
                t_name, t_side = _u_short(tgt)
                line = f"{_u_label(tgt)} healed +{amt:,} HP"
                t_obj["events"].append(line)
                t_obj["rich_events"].append({
                    "kind": "heal",
                    "target_uid": tgt,
                    "target_name": t_name,
                    "target_side": t_side,
                    "amount": amt,
                    "badges": [{"text": f"💚 +{amt:,} HP", "tone": "green"}],
                    "detail": "",
                    "text": line,
                })

        elif rtype == "resurrect":
            _flush_hits()
            u_id = r.get("unit")
            if isinstance(u_id, int):
                rng_counts["revives"] += 1
                if u_id in live_units:
                    live_units[u_id]["dead"] = False
                t_obj = _ensure_turn0()
                _add_tag(t_obj, "kill")
                t_name, t_side = _u_short(u_id)
                line = f"REVIVED: {_u_label(u_id)} resurrected!"
                t_obj["events"].append(line)
                t_obj["rich_events"].append({
                    "kind": "revive",
                    "target_uid": u_id,
                    "target_name": t_name,
                    "target_side": t_side,
                    "badges": [{"text": "✨ REVIVED!", "tone": "gold"}],
                    "detail": "",
                    "text": line,
                })

        elif rtype == "dead":
            _flush_hits()
            t_uid = r.get("target_uid")
            if isinstance(t_uid, int):
                rng_counts["kills"] += 1
                if t_uid in live_units:
                    live_units[t_uid]["dead"] = True
                    live_units[t_uid]["hp"] = 0
                    live_units[t_uid]["barrier"] = 0
                t_obj = _ensure_turn0()
                _add_tag(t_obj, "kill")
                t_name, t_side = _u_short(t_uid)
                line = f"DEFEATED: {_u_label(t_uid)}"
                t_obj["events"].append(line)
                t_obj["rich_events"].append({
                    "kind": "death",
                    "target_uid": t_uid,
                    "target_name": t_name,
                    "target_side": t_side,
                    "badges": [{"text": "💀 DEFEATED", "tone": "red"}],
                    "detail": "",
                    "text": line,
                })

    _flush_hits()
    filtered_turns = [t for t in turns if t["events"] or t["turn"] >= 1]
    if filtered_turns:
        filtered_turns[-1]["end_snapshot"] = _capture_snapshot()

    opening_cr = analyze_opening_cr_rng(turn1_winner_uid, turn0_snap_raw, pre_t1_pushes, all_units)
    return filtered_turns, opening_cr, rng_counts


# ============================================================================
# Top-Level Replay File & Match Analyzer
# ============================================================================

def parse_replay_file(path: Path, db: GameDatabase) -> Dict[str, Any]:
    raw_json = load_replay_dat(path)

    # Filename metadata: clanwar-{war_uid}-{war_day}-{battle_uid}-{round}.dat
    fname = path.name
    m_fn = re.match(r"^clanwar-(\d+)-(\d+)-(\d+)-(\d+)\.dat$", fname)
    war_uid = int(m_fn.group(1)) if m_fn else None
    war_day = int(m_fn.group(2)) if m_fn else None
    battle_uid = int(m_fn.group(3)) if m_fn else None
    round_from_fn = int(m_fn.group(4)) if m_fn else None

    logic = raw_json.get("logic_data") or {}
    map_data = logic.get("map_data") or {}
    cache_info = map_data.get("cache_info") or {}
    round_num = cache_info.get("round") or round_from_fn or 1

    user_team_raw = logic.get("team_data") or {}
    ways = map_data.get("ways") or {}
    first_way = next(iter(ways.values()), [{}])
    enemy_team_raw = (first_way[0] if first_way else {}).get("team") or {}
    enemy_info = cache_info.get("enemy_info") or {}

    # Decode verify_data -> snap_data[0].unit_state
    snap0_states: Dict[str, Any] = {}
    if raw_json.get("verify_data"):
        try:
            vd = decompress_dat_bytes(base64.b64decode(raw_json["verify_data"]))
            snaps = vd.get("snap_data") or []
            if snaps:
                snap0_states = snaps[0].get("unit_state") or {}
        except Exception:
            snap0_states = {}

    # Decode result_info.net_pvp_result
    pvp_res: Dict[str, Any] = {}
    pvp_stats_by_uid: Dict[int, Dict[str, Any]] = {}
    net_str = (raw_json.get("result_info") or {}).get("net_pvp_result")
    if isinstance(net_str, str) and net_str:
        try:
            pvp_res = json.loads(net_str)
            for info in (pvp_res.get("result_stat") or {}).get("infos") or []:
                if "uid" in info:
                    pvp_stats_by_uid[info["uid"]] = info
        except Exception:
            pass

    uid_to_arti_code: Dict[int, str] = {}
    for u_entry in (user_team_raw.get("units") or []) + (enemy_team_raw.get("units") or []):
        uid_i = (u_entry.get("unit") or {}).get("id")
        if isinstance(uid_i, int):
            for eq in u_entry.get("equips") or []:
                ec = eq.get("code", "")
                if ec and "f" not in eq and not ec.startswith("ex"):
                    uid_to_arti_code[uid_i] = ec

    init_arti_cs = extract_init_artifact_cs_levels(raw_json.get("init_record") or [], uid_to_arti_code)

    user_units = analyze_team(
        user_team_raw.get("units") or [], db, init_arti_cs, snap0_states, pvp_stats_by_uid
    )
    enemy_units = analyze_team(
        enemy_team_raw.get("units") or [], db, init_arti_cs, snap0_states, pvp_stats_by_uid
    )

    timeline, opening_cr, rng_summary = build_battle_timeline(
        raw_json.get("init_record") or [],
        raw_json.get("record_data") or [],
        user_units,
        enemy_units,
        db,
    )

    # Back-fill each unit's `battle_start` with exact pre-Turn-1 CR% & global Turn-1 order
    if opening_cr.get("available"):
        by_uid_cr = {row["uid"]: row for row in opening_cr.get("units") or []}
        for u in user_units + enemy_units:
            cr_row = by_uid_cr.get(u["uid"])
            if cr_row:
                u["battle_start"]["global_turn1_order"] = cr_row["global_order"]
                u["battle_start"]["turn_order"] = cr_row["global_order"]
                u["battle_start"]["initial_cr_pct"] = cr_row["actual_cr_pct"]
                u["battle_start"]["base_cr_pct"] = cr_row["base_cr_pct"]
                u["battle_start"]["rng_roll_range_pct"] = [cr_row["rng_roll_min_pct"], cr_row["rng_roll_max_pct"]]

    win_raw = pvp_res.get("win")
    attacker_won = win_raw in ("win", 1, True)
    ts_epoch = pvp_res.get("battle_play_time")
    played_at_utc = (
        dt.datetime.fromtimestamp(ts_epoch, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if isinstance(ts_epoch, (int, float)) and ts_epoch > 1_000_000_000
        else None
    )

    return {
        "file": path.name,
        "war_uid": war_uid,
        "war_day": war_day,
        "battle_uid": battle_uid,
        "round": round_num,
        "played_at_utc": played_at_utc,
        "epoch": ts_epoch,
        "attacker_result": "Victory" if attacker_won else ("Defeat" if win_raw is not None else "Unknown"),
        "defender_result": "Defeat" if attacker_won else ("Victory" if win_raw is not None else "Unknown"),
        "turn_count": pvp_res.get("turn_count"),
        "attacker": {
            "name": user_team_raw.get("name", "User"),
            "account_id": user_team_raw.get("id"),
            "level": user_team_raw.get("level"),
            "cp": user_team_raw.get("team_point"),
            "units": user_units,
        },
        "defender": {
            "name": enemy_info.get("name") or enemy_team_raw.get("name", "Enemy"),
            "account_id": enemy_team_raw.get("id"),
            "level": enemy_info.get("level") or enemy_team_raw.get("level"),
            "cp": enemy_team_raw.get("team_point"),
            "units": enemy_units,
        },
        "opening_cr": opening_cr,
        "rng_summary": rng_summary,
        "timeline": timeline,
    }


def group_matches(replays: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[Any, List[Dict[str, Any]]] = {}
    for r in replays:
        key = r["battle_uid"] or r["file"]
        by_key.setdefault(key, []).append(r)

    matches: List[Dict[str, Any]] = []
    for idx, (b_uid, rounds) in enumerate(sorted(by_key.items(), key=lambda kv: (kv[1][0].get("epoch") or 0, str(kv[0]))), 1):
        rounds.sort(key=lambda x: x["round"])
        r0 = rounds[0]
        matches.append({
            "match_index": idx,
            "battle_uid": b_uid,
            "war_uid": r0["war_uid"],
            "war_day": r0["war_day"],
            "attacker_name": r0["attacker"]["name"],
            "defender_name": r0["defender"]["name"],
            "played_at_utc": r0["played_at_utc"],
            "rounds": rounds,
        })
    return matches


# ============================================================================
# Console Summary Printer
# ============================================================================

def print_match_summary(matches: List[Dict[str, Any]]) -> None:
    for m in matches:
        print("=" * 124)
        print(
            f"MATCH #{m['match_index']}: {m['attacker_name']} (Offense) vs {m['defender_name']} (Defense) "
            f"| Battle UID: {m['battle_uid']} | {m['played_at_utc'] or ''}"
        )
        print("=" * 124)
        for rnd in m["rounds"]:
            print(
                f"\n  [Round {rnd['round']}] Attacker Result: {rnd['attacker_result']} "
                f"(Defender UI Badge: {rnd['defender_result']}) | Turns: {rnd['turn_count']} | File: {rnd['file']}"
            )
            for side_label, side_key in (
                ("ENEMY DEFENSE (UNMASKED)", "defender"),
                ("USER OFFENSE", "attacker"),
            ):
                team = rnd[side_key]
                print(f"    -- {side_label}: {team['name']} (CP: {team['cp']:,}) --" if team.get("cp") else f"    -- {side_label}: {team['name']} --")
                print(
                    f"    {'Pos':<6} {'Hero':<26} {'ATK':>5} {'DEF':>5} {'HP':>6} {'SPD':>4} "
                    f"{'CRIT%':>6} {'CDMG%':>6} {'EFF%':>5} {'ER%':>5} {'DAC%':>5} | {'ES':>4} | {'Mola':>4} | {'Sets':<20} | {'Artifact & Imprint'}"
                )
                for u in team["units"]:
                    st = u["sheet_stats"]
                    sets_str = "/".join(u["sets"]) if u["sets"] else "Broken"
                    if u["broken_pieces"] >= 2 and u["sets"]:
                        sets_str += "+Brk"
                    arti = u["artifact"]
                    arti_s = f"{arti['name']} +{arti['level']}" if arti else "No Artifact"
                    cr_s = f"{st['cr']:.1f}" + (f"*" if st["cr_uncapped"] > 100.0 else "")
                    cd_s = f"{st['cd']:.1f}" + (f"*" if st["cd_uncapped"] > 350.0 else "")
                    ee_s = f" | EE#{u['exclusive_equipment']['option']}" if u["exclusive_equipment"] else ""
                    imp_s = f" | {u['imprint_summary']}" if u["imprint_summary"] != "None" else ""
                    mola_s = f"+{u['molagora_total']}"
                    print(
                        f"    {u['pos_name']:<6} {u['name'][:26]:<26} {st['atk']:5d} {st['def']:5d} {st['hp']:6d} {st['spd']:4d} "
                        f"{cr_s:>6} {cd_s:>6} {st['eff']:5.1f} {st['res']:5.1f} {st['dac']:5.1f} | {u['total_equipment_score']:4d} | "
                        f"{mola_s:>4} | {sets_str[:20]:<20} | {arti_s}{ee_s}{imp_s}"
                    )
        print()


# ============================================================================
# CLI Entrypoint
# ============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse Epic Seven Guild War replay (.dat) files and unmask all Enemy & User stats."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Path(s) to .dat replay files or directories containing clanwar-*.dat (defaults to ./data/gw).",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for gw_analysis.json and gw_report.html (default: ./output).",
    )
    parser.add_argument(
        "--gamedata-dir",
        default=str(DEFAULT_GAMEDATA_DIR),
        help="Directory storing local heroes.json, artifacts.json, and ee_cache.json.",
    )
    parser.add_argument(
        "--update-db",
        action="store_true",
        help="Fetch the latest Hero & Artifact databases from Fribbels S3 & CeciliaBot API.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Disable all network calls (use strictly cached local gamedata/).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console match tables and only write JSON/HTML output files.",
    )

    args = parser.parse_args(argv)
    gamedata_dir = Path(args.gamedata_dir)

    if args.update_db:
        update_gamedata(gamedata_dir)
        if not args.inputs and not DEFAULT_DATA_DIR.exists():
            return 0

    targets = args.inputs if args.inputs else [str(DEFAULT_DATA_DIR)]
    dat_files: List[Path] = []
    for t in targets:
        p = Path(t)
        if p.is_file():
            dat_files.append(p)
        elif p.is_dir():
            dat_files.extend(sorted(p.glob("*.dat")))
        else:
            dat_files.extend(Path(x) for x in sorted(glob.glob(t)))

    if not dat_files:
        print(f"No .dat replay files found in: {targets}", file=sys.stderr)
        return 1

    db = GameDatabase(gamedata_dir=gamedata_dir, offline=args.offline)
    replays = [parse_replay_file(p, db) for p in dat_files]
    matches = group_matches(replays)

    if not args.quiet:
        print_match_summary(matches)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "gw_analysis.json"
    export_payload = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_files": len(replays),
        "total_matches": len(matches),
        "matches": matches,
    }
    json_path.write_text(json.dumps(export_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    from e7_gw_report import generate_html_report
    html_path = out_dir / "gw_report.html"
    generate_html_report(export_payload, html_path)

    print(f"Saved JSON analysis -> {json_path}")
    print(f"Saved HTML report   -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
