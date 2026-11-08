#!/usr/bin/env python3
"""
Epic Seven World Arena / RTA Replay (.dat) Parser & Draft/Unit/Timeline Extractor
=================================================================================
Decompresses Epic Seven World Arena (`net_rank-*.dat` / `world_arena`) replay files
and extracts 100% of both players' RTA match data:
  - Complete RTA Draft & Ban Phase:
      * Pre-Banned Heroes for both players (`preban_units` / `pre_ban`)
      * All 5 Drafted Heroes per side in exact pick order, with Global Pick #1..#10,
        Hero Level/Awakening, Memory Imprint Rank, and Equipped Artifact (even on the
        5th unit that gets post-banned!)
      * Ban-Protected Slots (`lunatic_ban_units`) & Post-Banned Heroes (`ban_units`)
      * First Pick vs Second Pick (`first_pick_arena_uid`), League Tier, Victory Points
        Before/After/Delta, Server (`world_global`, `world_kor`, etc.), Guild, and Ping
  - Complete 4v4 Fielded Unit Builds (User & Opponent):
      * Full 6-Piece Equipment Breakdown (Official In-Game Equipment Score `ES`,
        Fribbels Substat `WSS`, sets, main/substats, reforge values, mod gems)
      * Native support for both Human Gear Builds (`equips`) AND RTA Placement Bot /
        Draft Arena Template Builds (`template_status` + `ml_data` telemetry)
      * Exact Sheet Stats, Uncapped CRIT%/CDMG%, Post-Imprint Stats, In-Battle ML
        Telemetry Stats, Artifacts (+0..+30), Exclusive Equipment, & MolaGora (+0..+15)
  - Complete 8-Unit Turn-by-Turn Battle Timeline & RNG Visualizer Data:
      * Turn-0 8-Unit Starting CR & 0%–5% Speed RNG Solver (`e7calc.xyz` physics)
      * Real-Time Decision Clock per turn (`time_info`: think time & remaining clock)
      * Battle Frenzy stage tracking (`rta_penalty_level`)
      * 15% Absolute Resistance vs ER rolls, Evasion/Miss diagnostics, High-CRIT
        non-crits, Counters, Extra Turns, Dual Attacks, Passives, Barriers & Kills
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from e7_gw_parser import (
    ARTIFACT_EVASION_NOTES,
    DEFAULT_GAMEDATA_DIR,
    DEFAULT_OUTPUT_DIR,
    HERO_EVASION_SOURCES,
    IMPRINT_GRADES,
    POS_NAMES,
    POS_TO_DEVOTION_SLOT_IDX,
    SCRIPT_DIR,
    SET_META,
    SLOT_ORDER,
    STAT_LABELS,
    STATE_CODE_META,
    GameDatabase,
    _decode_passive_label,
    _format_skill_code,
    analyze_opening_cr_rng,
    compute_hero_base_stats,
    decompress_dat_bytes,
    determine_artifact_level,
    estimate_hero_level,
    format_imprint_bonus,
    load_replay_dat,
    parse_gear_piece,
    update_gamedata,
)

DEFAULT_RTA_DATA_DIR = SCRIPT_DIR / "data" / "world_arena"

WORLD_LABELS: Dict[str, str] = {
    "world_global": "Global",
    "world_kor": "Korea",
    "world_asia": "Asia",
    "world_eu": "Europe",
    "world_jpn": "Japan",
}

LEAGUE_LABELS: Dict[str, str] = {
    "unranked": "Placement (Unranked)",
    "bronze": "Bronze",
    "silver": "Silver",
    "gold": "Gold",
    "master": "Master",
    "challenger": "Challenger",
    "champion": "Champion",
    "emperor": "Emperor",
    "legend": "Legend",
}

RTA_MODE_LABELS: Dict[str, str] = {
    "net_rank": "Ranked World Arena",
    "net_friend": "Mock Battle (Custom Lobby)",
    "net_draft": "Draft Arena",
    "net_event": "Event World Arena",
}


# ============================================================================
# RTA State Normalization Helpers (1..8 Sequential IDs -> List or Dict!)
# ============================================================================

def iter_rta_uid_map(container: Any) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """
    Yield `(battle_uid, dict_val)` from an RTA snapshot table (`unit_state` or `cs_state`).
    Because RTA assigns sequential integer battle IDs `1..8`, Lua's JSON serializer
    emits a 0-based JSON `list` (`idx -> uid = idx + 1`) when contiguous, OR a string-keyed
    `dict` (`"5" -> uid = 5`) on sparse delta snapshots!
    """
    if isinstance(container, list):
        for idx, val in enumerate(container):
            if isinstance(val, dict):
                yield idx + 1, val
    elif isinstance(container, dict):
        for k, val in container.items():
            ks = str(k)
            if ks.lstrip("-").isdigit() and isinstance(val, dict):
                yield int(ks), val


def normalize_rta_uid_dict(container: Any) -> Dict[int, Dict[str, Any]]:
    return {uid: val for uid, val in iter_rta_uid_map(container)}


def extract_rta_init_artifact_cs_levels(
    init_record: List[Dict[str, Any]],
    uid_to_arti_code: Dict[int, str],
    db: GameDatabase,
) -> Dict[int, Dict[str, int]]:
    """
    Scan RTA `init_record` for `cs_<arti_code><0..10>` or `cs_<art_id><0..10>` states,
    supporting both `list` and `dict` `cs_state` containers and 4-star `ef403 -> art4_03` aliases.
    """
    out: Dict[int, Dict[str, int]] = {}
    uid_prefixes: Dict[int, List[Tuple[str, str]]] = {}

    for uid, acode in uid_to_arti_code.items():
        if not acode:
            continue
        prefixes = [(acode, f"cs_{acode}")]
        art_meta = db.get_artifact(acode)
        art_id = art_meta.get("art_id") or ""
        if art_id:
            prefixes.append((acode, f"cs_{art_id}"))
        m_efnum = re.match(r"^ef(\d)(\d{2})$", acode)
        if m_efnum:
            prefixes.append((acode, f"cs_art{m_efnum.group(1)}_{m_efnum.group(2)}"))
        uid_prefixes[uid] = prefixes

    def _record(uid: Any, state_id: str) -> None:
        if not isinstance(uid, int) or uid not in uid_prefixes:
            return
        for acode, prefix in uid_prefixes[uid]:
            if state_id.startswith(prefix):
                suffix = state_id[len(prefix):]
                m = re.match(r"^(10|[0-9])(?:_|$)", suffix)
                if m:
                    out.setdefault(uid, {}).setdefault(acode, int(m.group(1)))

    for rec in init_record or []:
        if not isinstance(rec, dict):
            continue
        sid = rec.get("state_id") or (rec.get("state_data") or {}).get("c") or ""
        if sid:
            _record(rec.get("target"), sid)
        for uid_i, u_obj in iter_rta_uid_map(rec.get("cs_state")):
            for item in u_obj.get("list") or []:
                if isinstance(item, dict) and item.get("c"):
                    _record(uid_i, str(item["c"]))
    return out


def extract_ml_telemetry_stats(web_replay_b64: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """
    Decode `web_replay_data` (base64 LZ4 JSON) and extract Smilegate's normalized
    `ml_data.record_datas[0]` opening stat vector for all 8 units (`hero_code -> stats`).
    """
    if not web_replay_b64 or not isinstance(web_replay_b64, str):
        return {}
    try:
        web_json = decompress_dat_bytes(base64.b64decode(web_replay_b64))
        recs = ((web_json.get("ml_data") or {}).get("record_datas") or [])
        if not recs or not isinstance(recs[0], list):
            return {}
        vec = recs[0]
        by_code: Dict[str, Dict[str, Any]] = {}
        idx = 0
        n = len(vec)
        while idx + 25 < n:
            val = vec[idx]
            if isinstance(val, str) and re.match(r"^c\d{4}$", val):
                chunk = vec[idx : idx + 26]
                by_code[val] = {
                    "hp": int(round(float(chunk[3]) * 100000.0)),
                    "atk": int(round(float(chunk[16]) * 8000.0)),
                    "def": int(round(float(chunk[17]) * 4000.0)),
                    "spd": int(round(float(chunk[18]) * 360.0)),
                    "res": round(float(chunk[19]) * 400.0, 1),
                    "eff": round(float(chunk[20]) * 400.0, 1),
                    "cr": round(float(chunk[21]) * 100.0, 1),
                    "cd": round(float(chunk[22]) * 400.0, 1),
                }
                idx += 26
                continue
            idx += 1
        return by_code
    except Exception:
        return {}


# ============================================================================
# RTA Team & Unit Build Analyzer (Supports Human `equips` + Bot/Draft `template_status`)
# ============================================================================

def analyze_rta_team(
    units_raw: List[Dict[str, Any]],
    db: GameDatabase,
    init_arti_cs: Dict[int, Dict[str, int]],
    snap0_states: Dict[int, Dict[str, Any]],
    pvp_stats_by_uid: Dict[int, Dict[str, Any]],
    ml_stats_by_code: Dict[str, Dict[str, Any]],
    pick_meta_by_code: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Analyze all 4 fielded units on an RTA team (sorted by formation position `pos` 1..4).
    Supports both standard player equipment (`equips`) and server stat templates
    (`template_status` used by official RTA placement bots and Draft Arena mode).
    """
    sorted_raw = sorted(units_raw, key=lambda x: x.get("pos", 99))
    team_imprints: List[Dict[str, Any]] = []
    analyzed: List[Dict[str, Any]] = []

    for u_entry in sorted_raw:
        pos = u_entry.get("pos", 1)
        u = u_entry.get("unit") or {}
        uid = int(u.get("id") or 0)
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

        equips_raw = u_entry.get("equips") or []
        t_status = u_entry.get("template_status") if isinstance(u_entry.get("template_status"), dict) else None
        is_template = bool(t_status and not equips_raw)

        set_counts: Dict[str, int] = {}
        gear_pieces: List[Dict[str, Any]] = []
        artifact_obj: Optional[Dict[str, Any]] = None
        ee_obj: Optional[Dict[str, Any]] = None
        account_hero_uid: Optional[int] = None

        if not is_template:
            # Standard Human Player Build (`equips` list with 6 gear pieces + Artifact + EE)
            for eq in equips_raw:
                if account_hero_uid is None and isinstance(eq.get("p"), int):
                    account_hero_uid = eq["p"]
                ecode = eq.get("code", "")
                ops = eq.get("op") or []

                if "f" in eq:
                    set_code = eq["f"]
                    set_counts[set_code] = set_counts.get(set_code, 0) + 1
                    parsed_gear = parse_gear_piece(eq)
                    gear_pieces.append(parsed_gear)
                    _add(parsed_gear["main"]["stat"], parsed_gear["main"]["raw"])
                    for sub in ops[1:]:
                        _add(sub[0], sub[1])

                elif ecode.startswith("ex"):
                    ee_stat_type = ops[0][0] if ops else ""
                    ee_stat_val = ops[0][1] if ops else 0.0
                    ee_opt_num = ops[1][1] if len(ops) > 1 else 1
                    _add(ee_stat_type, ee_stat_val)

                    ee_meta = db.get_ee_info(hcode, hero.get("slug", ""))
                    opt_desc = ""
                    opt_skill = None
                    if ee_meta:
                        for om in ee_meta.get("options", []):
                            if om["option"] == ee_opt_num:
                                opt_desc = om.get("description", "")
                                opt_skill = om.get("skill")
                                break
                    is_pct = ee_stat_type in ("att_rate", "def_rate", "max_hp_rate", "cri", "cri_dmg", "acc", "res", "coop")
                    ee_obj = {
                        "code": ecode,
                        "name": ee_meta.get("name", ecode) if ee_meta else ecode,
                        "stat": STAT_LABELS.get(ee_stat_type, ee_stat_type),
                        "value": round(ee_stat_val * 100.0, 1) if is_pct else int(round(ee_stat_val)),
                        "is_percent": is_pct,
                        "option": ee_opt_num,
                        "skill": opt_skill,
                        "description": re.sub(r"(\+\+|--|\*\*)", "", opt_desc),
                        "icon": f"https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/item/icon_eq_exclusive_{hcode}.png",
                    }

                else:
                    arti_meta = db.get_artifact(ecode)
                    a_rarity = arti_meta.get("rarity", 5)
                    cs_idx = (init_arti_cs.get(uid) or {}).get(ecode)
                    a_lv = determine_artifact_level(eq, a_rarity, cs_idx)
                    mult = 1.0 + 12.0 * (a_lv / 30.0)

                    a_stats_out: Dict[str, float] = {}
                    a_flat_map: Dict[str, float] = {"att": 0.0, "def": 0.0, "max_hp": 0.0}
                    if ops:
                        for op_pair in ops:
                            if isinstance(op_pair, (list, tuple)) and len(op_pair) >= 2:
                                st_k = str(op_pair[0])
                                leveled = round(float(op_pair[1] or 0.0) * mult, 1)
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
                        "rarity": a_rarity,
                        "role": arti_meta.get("role", ""),
                        "level": a_lv,
                        "limit_break": eq.get("sk", 0),
                        "flat_atk": int(round(a_flat_map["att"])),
                        "flat_def": int(round(a_flat_map["def"])),
                        "flat_hp": int(round(a_flat_map["max_hp"])),
                        "stats": a_stats_out,
                        "icon": a_icon,
                    }

            gear_pieces.sort(key=lambda g: SLOT_ORDER.get(g["slot"], 99))

            active_sets: List[str] = []
            broken_pieces = 0
            for sc, cnt in set_counts.items():
                smeta = SET_META.get(sc)
                if not smeta:
                    broken_pieces += cnt
                    continue
                req = smeta["pieces"]
                completed = cnt // req
                broken_pieces += cnt % req
                for _ in range(completed):
                    active_sets.append(smeta["name"])
                    st_type = smeta["stat"]
                    if st_type == "spd_base_pct":
                        spd += base["spd"] * smeta["val"]
                    elif st_type:
                        _add(st_type, smeta["val"])
            active_sets.sort()

        else:
            # RTA Template Build (`template_status` from Placement Bot or Draft Mode)
            st_flat = (t_status or {}).get("status") or {}
            st_rate = (t_status or {}).get("rate") or {}
            _add("att", float(st_flat.get("att") or 0.0))
            _add("def", float(st_flat.get("def") or 0.0))
            _add("max_hp", float(st_flat.get("max_hp") or 0.0))
            _add("speed", float(st_flat.get("speed") or 0.0))
            _add("cri", float(st_flat.get("cri") or 0.0))
            _add("cri_dmg", float(st_flat.get("cri_dmg") or 0.0))
            _add("acc", float(st_flat.get("acc") or 0.0))
            _add("res", float(st_flat.get("res") or 0.0))

            _add("att_rate", float(st_rate.get("att_rate") or 0.0) / 100.0)
            _add("def_rate", float(st_rate.get("def_rate") or 0.0) / 100.0)
            _add("max_hp_rate", float(st_rate.get("max_hp_rate") or 0.0) / 100.0)

            active_sets = []
            broken_pieces = 0
            for sc in (t_status or {}).get("set_op_id") or []:
                smeta = SET_META.get(sc)
                if smeta:
                    active_sets.append(smeta["name"])
                    st_type = smeta["stat"]
                    if st_type == "spd_base_pct":
                        spd += base["spd"] * smeta["val"]
                    elif st_type:
                        _add(st_type, smeta["val"])
            active_sets.sort()

            # Template Artifact (`arti_id`) — Note: flat stats in `template_status.status` already include artifact stats!
            acode = (t_status or {}).get("arti_id") or ""
            if acode:
                arti_meta = db.get_artifact(acode)
                a_rarity = arti_meta.get("rarity", 5)
                cs_idx = (init_arti_cs.get(uid) or {}).get(acode)
                a_lv = cs_idx * 3 if cs_idx is not None else 30
                mult = 1.0 + 12.0 * (a_lv / 30.0)
                mstats = arti_meta.get("stats") or {}
                base_atk = float(mstats.get("attack") or 0.0)
                base_def = float(mstats.get("defense") or 0.0)
                base_hp = float(mstats.get("health") or 0.0)
                a_stats_out = {}
                if base_atk > 0:
                    a_stats_out["ATK"] = round(base_atk * mult, 1)
                if base_def > 0:
                    a_stats_out["DEF"] = round(base_def * mult, 1)
                if base_hp > 0:
                    a_stats_out["HP"] = round(base_hp * mult, 1)
                art_id = arti_meta.get("art_id", "")
                a_icon = arti_meta.get("icon") or (
                    f"https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/item_arti/icon_{art_id}.png"
                    if art_id
                    else ""
                )
                artifact_obj = {
                    "code": acode,
                    "name": arti_meta.get("name", acode),
                    "rarity": a_rarity,
                    "role": arti_meta.get("role", ""),
                    "level": a_lv,
                    "limit_break": 5,
                    "flat_atk": int(round(base_atk * mult)),
                    "flat_def": int(round(base_def * mult)),
                    "flat_hp": int(round(base_hp * mult)),
                    "stats": a_stats_out,
                    "icon": a_icon,
                }

            # Template Exclusive Equipment (`exc_id`, e.g. `ek_c114201_03`) — already baked into `template_status` stats
            exc_id = (t_status or {}).get("exc_id") or ""
            if exc_id:
                m_ex = re.match(r"^ek_(c\d+\d+)_0?(\d+)$", exc_id)
                ee_opt_num = int(m_ex.group(2)) if m_ex else 1
                ee_code = f"ex{m_ex.group(1)}" if m_ex else exc_id
                ee_meta = db.get_ee_info(hcode, hero.get("slug", ""))
                opt_desc = ""
                opt_skill = None
                ee_stat_type = ee_meta.get("stat_type", "") if ee_meta else ""
                if ee_meta:
                    for om in ee_meta.get("options", []):
                        if om["option"] == ee_opt_num:
                            opt_desc = om.get("description", "")
                            opt_skill = om.get("skill")
                            break
                ee_obj = {
                    "code": ee_code,
                    "name": ee_meta.get("name", ee_code) if ee_meta else ee_code,
                    "stat": STAT_LABELS.get(ee_stat_type, ee_stat_type) if ee_stat_type else "Template",
                    "value": None,
                    "is_percent": True,
                    "option": ee_opt_num,
                    "skill": opt_skill,
                    "description": re.sub(r"(\+\+|--|\*\*)", "", opt_desc),
                    "icon": f"https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/item/icon_eq_exclusive_{hcode}.png",
                }

        if account_hero_uid is None and hcode in pick_meta_by_code:
            account_hero_uid = pick_meta_by_code[hcode].get("uid")

        # Memory Imprint (`d` = rank index, `opt` // 10 % 10 == 2 => Self Concentration, else Team Release)
        d_rank = u.get("d", 0) or 0
        opt_val = u.get("opt", 0) or 0
        is_self_imprint = ((opt_val // 10) % 10 == 2)
        imprint_grade = (IMPRINT_GRADES.get(rarity) or {}).get(d_rank)

        imprint_summary = "None"
        imprint_detail: Dict[str, Any] = {"grade": None, "mode": None, "stat": None, "value": 0, "bonus_display": None}

        if d_rank > 0 and imprint_grade:
            if is_self_imprint:
                sd = hero.get("self_devotion") or {}
                sd_type = sd.get("type")
                sd_val = (sd.get("grades") or {}).get(imprint_grade, 0.0)
                if sd_type and sd_val:
                    _add(sd_type, sd_val)
                    b_disp = format_imprint_bonus(sd_type, sd_val)
                    imprint_summary = f"{imprint_grade} Self ({b_disp})"
                    imprint_detail = {
                        "grade": imprint_grade,
                        "mode": "Concentration (Self)",
                        "stat": sd_type,
                        "value": sd_val,
                        "bonus_display": b_disp,
                    }
            else:
                dev = hero.get("devotion") or {}
                dev_type = dev.get("type")
                dev_val = (dev.get("grades") or {}).get(imprint_grade, 0.0)
                dev_slots = dev.get("slots") or [1, 1, 1, 1]
                if dev_type and dev_val:
                    b_disp = format_imprint_bonus(dev_type, dev_val)
                    imprint_summary = f"{imprint_grade} Team ({b_disp})"
                    imprint_detail = {
                        "grade": imprint_grade,
                        "mode": "Release (Team)",
                        "stat": dev_type,
                        "value": dev_val,
                        "slots": dev_slots,
                        "bonus_display": b_disp,
                    }
                    team_imprints.append({
                        "caster_pos": pos,
                        "caster_name": hero.get("name", hcode),
                        "stat": dev_type,
                        "value": dev_val,
                        "slots": dev_slots,
                        "bonus": b_disp,
                    })

        # Finalize Exact Hero Sheet Stats
        sheet_atk = int((base["atk"] * (1.0 + pct_atk) + flat_atk) * base["b_max_atk"])
        sheet_def = int((base["def"] * (1.0 + pct_def) + flat_def) * base["b_max_def"])
        sheet_hp = int((base["hp"] * (1.0 + pct_hp) + flat_hp) * base["b_max_hp"])
        sheet_spd = int(base["spd"] + spd + base["ovr_spd"])

        raw_cr = round((base["chc"] + cr + base["ovr_cr"]) * 100.0, 1)
        raw_cd = round((base["chd"] + cd + base["ovr_cd"]) * 100.0, 1)
        raw_eff = round((base["eff"] + eff + base["ovr_eff"]) * 100.0, 1)
        raw_res = round((base["efr"] + res + base["ovr_res"]) * 100.0, 1)
        raw_dac = round((base["dac"] + dac) * 100.0, 1)

        ui_cr = min(100.0, raw_cr)
        ui_cd = min(350.0, raw_cd)
        ui_dac = min(30.0, raw_dac)

        ehp = int(round(sheet_hp * (sheet_def / 300.0 + 1.0)))
        mcd = int(round(sheet_atk * (ui_cd / 100.0)))

        snap_st = snap0_states.get(uid) or {}
        pvp_st = pvp_stats_by_uid.get(uid) or {}
        total_es = sum(g["equipment_score"] for g in gear_pieces)
        total_sub_gs = round(sum(g["substat_gs"] for g in gear_pieces), 1)
        avg_es = round(total_es / len(gear_pieces), 1) if gear_pieces else 0.0
        pick_info_row = pick_meta_by_code.get(hcode) or {}

        analyzed.append({
            "pos": pos,
            "pos_name": POS_NAMES.get(pos, f"Pos {pos}"),
            "uid": uid,
            "account_hero_uid": account_hero_uid,
            "pick_order": pick_info_row.get("pick_slot"),
            "global_pick_order": pick_info_row.get("global_pick_order"),
            "is_ban_protected": bool(pick_info_row.get("is_ban_protected")),
            "code": hcode,
            "name": hero.get("name", hcode),
            "attribute": hero.get("attribute", ""),
            "role": hero.get("role", ""),
            "rarity": rarity,
            "zodiac": hero.get("zodiac", ""),
            "icon": hero.get("icon", ""),
            "level": level,
            "stars": stars,
            "awakening": awaken,
            "molagora": mola_levels,
            "molagora_total": sum(mola_levels),
            "friendship_exp": u.get("f", 0),
            "skin_code": u.get("skin_code"),
            "is_template_build": is_template,
            "template_id": u.get("template_id"),
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
            "in_battle_ml_stats": ml_stats_by_code.get(hcode),
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

    # Second pass: apply 4-slot RTA Team Imprint Releases across formation slots
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
# RTA Draft, Pre-Ban, Ban-Protection & Post-Ban Extractor
# ============================================================================

def _parse_preban_list(raw_prebans: Any, fallback_json_str: Any, db: GameDatabase) -> List[Dict[str, Any]]:
    codes: List[str] = []
    if isinstance(raw_prebans, list):
        for item in raw_prebans:
            if isinstance(item, dict) and item.get("code"):
                codes.append(str(item["code"]))
            elif isinstance(item, str):
                codes.append(item)
    if not codes and isinstance(fallback_json_str, str) and fallback_json_str.strip():
        try:
            arr = json.loads(fallback_json_str)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict) and item.get("code"):
                        codes.append(str(item["code"]))
        except Exception:
            pass

    out: List[Dict[str, Any]] = []
    for c in codes:
        if not c or c == "nil":
            continue
        h = db.get_hero(c)
        out.append({
            "code": c,
            "name": h.get("name", c),
            "attribute": h.get("attribute", ""),
            "role": h.get("role", ""),
            "rarity": h.get("rarity", 5),
            "icon": h.get("icon", ""),
        })
    return out


def extract_rta_draft_info(
    match_info: Dict[str, Any],
    pvp_res: Dict[str, Any],
    home_uid: str,
    away_uid: str,
    away_is_bot: bool,
    db: GameDatabase,
) -> Dict[str, Any]:
    """
    Reconstruct the complete RTA Pre-Ban, 5-Pick Snake Draft, Ban Protection (`lunatic_ban`),
    and Post-Ban breakdown for both Home (User) and Away (Opponent).
    """
    fp_uid = str(match_info.get("first_pick_arena_uid") or pvp_res.get("first_pick_arena_uid") or "")
    user_info = match_info.get("user_info") or {}
    enemy_info = match_info.get("enemy_user_info") or {}
    room_home_uid = str(match_info.get("home_user") or "")

    if fp_uid not in ("", "nil", "None", "0"):
        first_pick_side = "user" if fp_uid == home_uid else ("enemy" if fp_uid == away_uid else "unknown")
    elif user_info.get("index") == 1 or room_home_uid == home_uid:
        first_pick_side = "user"
    elif enemy_info.get("index") == 1 or room_home_uid == away_uid:
        first_pick_side = "enemy"
    else:
        first_pick_side = "user" if away_is_bot else "unknown"

    # Standard RTA 1-2-2-2-2-1 snake draft global pick slots
    fp_global_slots = [1, 4, 5, 8, 9]
    sp_global_slots = [2, 3, 6, 7, 10]

    preban_map = pvp_res.get("preban_units") or {}

    user_prebans = _parse_preban_list(preban_map.get(home_uid), user_info.get("pre_ban"), db)
    enemy_prebans = _parse_preban_list(preban_map.get(away_uid), enemy_info.get("pre_ban"), db)

    pick_map = pvp_res.get("pick_units") or match_info.get("pickinfo") or {}
    ban_map = pvp_res.get("ban_units") or {}
    prot_map = pvp_res.get("lunatic_ban_units") or {}

    def _build_side_picks(arena_uid: str, side: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        raw_picks = pick_map.get(arena_uid) or []
        banned_codes = {str(x.get("code")) for x in (ban_map.get(arena_uid) or []) if isinstance(x, dict) and x.get("code")}
        prot_codes = {str(x.get("code")) for x in (prot_map.get(arena_uid) or []) if isinstance(x, dict) and x.get("code")}

        if first_pick_side == side:
            g_slots = fp_global_slots
        elif first_pick_side in ("user", "enemy"):
            g_slots = sp_global_slots
        else:
            g_slots = [None, None, None, None, None]  # type: ignore

        picks_out: List[Dict[str, Any]] = []
        banned_pick: Optional[Dict[str, Any]] = None
        protected_pick: Optional[Dict[str, Any]] = None

        for idx, p in enumerate(raw_picks):
            if not isinstance(p, dict):
                continue
            c = str(p.get("code") or "")
            h = db.get_hero(c)
            rarity = h.get("rarity", 5)
            dev_rank = int(p.get("devote") or 0)
            imp_grade = (IMPRINT_GRADES.get(rarity) or {}).get(dev_rank)

            acode = str(p.get("artifact_code") or "")
            arti_summary = None
            if acode and acode != "nil":
                am = db.get_artifact(acode)
                art_id = am.get("art_id", "")
                arti_summary = {
                    "code": acode,
                    "name": am.get("name", acode),
                    "rarity": am.get("rarity", 5),
                    "icon": am.get("icon") or (
                        f"https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/item_arti/icon_{art_id}.png"
                        if art_id
                        else ""
                    ),
                }

            is_banned = c in banned_codes
            is_prot = c in prot_codes
            entry = {
                "pick_slot": idx + 1,
                "global_pick_order": g_slots[idx] if idx < len(g_slots) else None,
                "side": side,
                "code": c,
                "uid": p.get("uid"),
                "name": h.get("name", c),
                "attribute": h.get("attribute", ""),
                "role": h.get("role", ""),
                "rarity": rarity,
                "icon": h.get("icon", ""),
                "level": p.get("g_lv") or 60,
                "stars": p.get("grade") or 6,
                "awakening": p.get("zodiac") or 6,
                "imprint_grade": imp_grade,
                "artifact": arti_summary,
                "template_id": p.get("template_id"),
                "is_banned": is_banned,
                "is_ban_protected": is_prot,
            }
            picks_out.append(entry)
            if is_banned:
                banned_pick = entry
            if is_prot:
                protected_pick = entry

        return picks_out, banned_pick, protected_pick

    user_picks, user_banned, user_prot = _build_side_picks(home_uid, "user")
    enemy_picks, enemy_banned, enemy_prot = _build_side_picks(away_uid, "enemy")

    # Build combined global draft sequence (#1..#10) if snake order is known
    snake_sequence: List[Dict[str, Any]] = []
    if first_pick_side in ("user", "enemy"):
        combined = [p for p in (user_picks + enemy_picks) if p.get("global_pick_order") is not None]
        snake_sequence = sorted(combined, key=lambda x: int(x["global_pick_order"]))

    return {
        "first_pick_arena_uid": fp_uid if fp_uid not in ("", "nil", "None") else None,
        "first_pick_side": first_pick_side,
        "user_prebans": user_prebans,
        "enemy_prebans": enemy_prebans,
        "user_picks": user_picks,
        "enemy_picks": enemy_picks,
        "user_unit_banned_by_enemy": user_banned,
        "enemy_unit_banned_by_user": enemy_banned,
        "user_ban_protected": user_prot,
        "enemy_ban_protected": enemy_prot,
        "snake_draft_order": snake_sequence,
    }


# ============================================================================
# RTA 8-Unit Turn-by-Turn Battle Timeline & RNG Visualizer Engine
# ============================================================================

def build_rta_battle_timeline(
    init_record: List[Dict[str, Any]],
    record_data: List[Any],
    user_units: List[Dict[str, Any]],
    enemy_units: List[Dict[str, Any]],
    home_uid: str,
    away_uid: str,
    db: GameDatabase,
    room_host_uid: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """
    Reconstruct the complete 8-unit RTA battle timeline across `init_record` + `record_data`,
    handling both `list` (index 0..7 -> uid 1..8) and `dict` snapshots, tracking per-turn
    RTA decision clocks (`time_info`), Battle Frenzy stages, live HP/MaxHP/Injury/Barrier/CR%/Souls,
    and hidden RNG checks (15% Absolute Resistance, Evasion/Miss causes, Speed RNG, Crit RNG).
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
            return f"[{'Opp' if side == 'enemy' else 'User'}] {u['name']}"
        return str(uid)

    def _u_short(uid: Any) -> Tuple[str, str]:
        if isinstance(uid, int) and uid in all_units:
            u, side = all_units[uid]
            return u["name"], side
        return str(uid), "neutral"

    # Flatten `init_record` + `record_data` while preserving `time_info` decision clock markers!
    stream: List[Dict[str, Any]] = [r for r in (init_record or []) if isinstance(r, dict)]
    for blk in record_data or []:
        if not isinstance(blk, dict):
            continue
        if isinstance(blk.get("time_info"), dict):
            stream.append({"type": "__rta_time_info__", "time_info": blk["time_info"]})
        recs = (blk.get("record_info") or {}).get("records") or []
        for r in recs:
            if isinstance(r, dict):
                stream.append(r)

    live_units: Dict[int, Dict[str, Any]] = {}
    for uid, (u, side) in all_units.items():
        hp0 = (u.get("in_battle_ml_stats") or {}).get("hp") or u["post_imprint_stats"]["hp"]
        live_units[uid] = {
            "hp": hp0,
            "max_hp": hp0,
            "dhp": 0,
            "et": 0.0,
            "order": None,
            "sp": 0,
            "dead": False,
            "barrier": 0,
            "states": {},
        }
    live_souls: Dict[str, int] = {"user": 0, "enemy": 0}
    live_frenzy_stage: int = 0

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
            "frenzy_stage": live_frenzy_stage,
            "units": snap_units,
        }

    turns: List[Dict[str, Any]] = []
    cur_turn: Optional[Dict[str, Any]] = None
    cur_skill_hits: Dict[Tuple[int, int], Dict[str, Any]] = {}
    cur_caster_uid: Optional[int] = None
    cur_skill_id: str = ""
    cur_is_soulburn: bool = False
    pending_counter_uid: Optional[int] = None
    pending_clock: Optional[Dict[str, Any]] = None

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
                "decision_clock": None,
                "frenzy_stage": live_frenzy_stage,
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

        if rtype == "__rta_time_info__":
            ti = r.get("time_info") or {}
            owner_arena_uid = str(ti.get("owner") or "")
            tmap = ti.get("time") or {}
            owner_clock = tmap.get(owner_arena_uid) if isinstance(tmap, dict) else None
            if isinstance(owner_clock, dict):
                rest = owner_clock.get("rest")
                total = owner_clock.get("total")
                if isinstance(rest, (int, float)) and isinstance(total, (int, float)) and rest >= 0 and total > 0:
                    pending_clock = {
                        "owner_side": "user" if owner_arena_uid == home_uid else ("enemy" if owner_arena_uid == away_uid else "neutral"),
                        "remaining_sec": int(round(rest)),
                        "total_sec": int(round(total)),
                        "think_sec": max(0, int(round(total - rest))),
                    }

        elif rtype == "SNAP":
            for uid_i, v in iter_rta_uid_map(r.get("unit_state")):
                if uid_i in live_units:
                    live_units[uid_i].update(v)
                    if v.get("hp") is not None and v["hp"] <= 0:
                        live_units[uid_i]["dead"] = True
                        live_units[uid_i]["barrier"] = 0
            ts = r.get("team_state")
            if isinstance(ts, dict):
                # In RTA `SNAP.team_state`, `"friend"` is ALWAYS `match_info.home_user` (Room Host / P1)
                # and `"enemy"` is ALWAYS `match_info.away_user` (Room Guest / P2)!
                host_is_user = (not room_host_uid) or (room_host_uid == home_uid) or (room_host_uid != away_uid)
                friend_side = "user" if host_is_user else "enemy"
                enemy_side = "enemy" if host_is_user else "user"
                if isinstance(ts.get("friend"), dict) and "soul_piece" in ts["friend"]:
                    sp_f = ts["friend"]["soul_piece"]
                    live_souls[friend_side] = int(sp_f // 10) if sp_f >= 100 else int(sp_f)
                if isinstance(ts.get("enemy"), dict) and "soul_piece" in ts["enemy"]:
                    sp_e = ts["enemy"]["soul_piece"]
                    live_souls[enemy_side] = int(sp_e // 10) if sp_e >= 100 else int(sp_e)

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

            turn_clock = pending_clock if (pending_clock and pending_clock.get("owner_side") == a_side) else None
            pending_clock = None

            cur_turn = {
                "turn": tnum,
                "actor_uid": owner,
                "actor": _u_label(owner),
                "actor_name": a_name,
                "actor_side": a_side,
                "actor_icon": a_icon,
                "is_skipped": False,
                "decision_clock": turn_clock,
                "frenzy_stage": live_frenzy_stage,
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
                "decision_clock": None,
                "frenzy_stage": live_frenzy_stage,
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
            txt = str(r.get("text") or "")
            tgt = r.get("target")
            if txt == "sk_counter" and isinstance(tgt, int):
                _flush_hits()
                pending_counter_uid = tgt
                rng_counts["counters"] += 1
                t_obj = _ensure_turn0()
                _add_tag(t_obj, "passive")
                _add_tag(t_obj, "rng")
            elif "frenzy" in txt.lower() or "pvp_penalty" in txt.lower() or "rta_fever" in txt.lower():
                m_f = re.search(r"(\d+)", txt)
                live_frenzy_stage = int(m_f.group(1)) if m_f else (live_frenzy_stage + 1)
                t_obj = _ensure_turn0()
                t_obj["frenzy_stage"] = live_frenzy_stage
                line = f"🔥 Battle Frenzy Stage {live_frenzy_stage} Activated!"
                if not t_obj["events"] or t_obj["events"][-1] != line:
                    t_obj["events"].append(line)
                    t_obj["rich_events"].append({
                        "kind": "frenzy",
                        "badges": [{"text": f"🔥 BATTLE FRENZY {live_frenzy_stage}", "tone": "amber"}],
                        "detail": "World Arena Battle Frenzy escalated",
                        "text": line,
                    })

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
                ml_eff = (c_u.get("in_battle_ml_stats") or {}).get("eff")
                caster_eff = float(ml_eff if ml_eff is not None else c_u["post_imprint_stats"]["eff"])
                if any(ef["code"] == "125" for ef in live_units[caster_uid]["states"].values()):
                    caster_eff += 50.0

            target_er = 0.0
            if isinstance(tgt, int) and tgt in all_units:
                t_u, _ = all_units[tgt]
                ml_er = (t_u.get("in_battle_ml_stats") or {}).get("res")
                target_er = float(ml_er if ml_er is not None else t_u["post_imprint_stats"]["res"])
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
# Top-Level RTA Replay File Analyzer
# ============================================================================

def _extract_pvp_result_dict(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    res_info = raw_json.get("result_info") or {}
    outer = res_info.get("net_pvp_result")
    if isinstance(outer, str) and outer:
        try:
            outer = json.loads(outer)
        except Exception:
            return {}
    if isinstance(outer, dict):
        inner = outer.get("net_pvp_result")
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, str) and inner:
            try:
                return json.loads(inner)
            except Exception:
                return outer
        return outer
    return {}


def parse_rta_replay_file(path: Path, db: GameDatabase) -> Dict[str, Any]:
    raw_json = load_replay_dat(path)

    # Filename pattern: net_rank-{room_key}-{epoch}.dat
    fname = path.name
    m_fn = re.match(r"^([a-z0-9_]+?)-(\d+)-(\d+)\.dat$", fname)
    mode_from_fn = m_fn.group(1) if m_fn else None
    room_from_fn = int(m_fn.group(2)) if m_fn else None
    epoch_from_fn = int(m_fn.group(3)) if m_fn else None

    logic = raw_json.get("logic_data") or {}
    init_data = logic.get("init_data") or {}
    map_data = logic.get("map_data") or {}
    match_info = raw_json.get("match_info") if isinstance(raw_json.get("match_info"), dict) else {}
    pvp_res = _extract_pvp_result_dict(raw_json)

    mode_code = str(raw_json.get("mode") or init_data.get("mode") or mode_from_fn or "net_rank")
    season_id = str(
        init_data.get("world_arena_season_id")
        or (match_info.get("user_info") or {}).get("season_id")
        or map_data.get("enter")
        or ""
    )
    battle_room_key = match_info.get("battle_room_key") or room_from_fn
    epoch_ts = (raw_json.get("version_info") or {}).get("time") or epoch_from_fn or (match_info.get("user_info") or {}).get("updated")
    played_at_utc = (
        dt.datetime.fromtimestamp(epoch_ts, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if isinstance(epoch_ts, (int, float)) and epoch_ts > 1_000_000_000
        else None
    )

    user_info_raw = match_info.get("user_info") or {}
    enemy_info_raw = match_info.get("enemy_user_info") or {}

    home_uid = str(user_info_raw.get("uid") or init_data.get("home_arena_uid") or match_info.get("home_user") or "1")
    away_uid = str(enemy_info_raw.get("uid") or init_data.get("away_arena_uid") or match_info.get("away_user") or "2")
    room_host_uid = str(match_info.get("home_user") or home_uid)

    raw_home_end = pvp_res.get("home_user_end_info") or {}
    raw_away_end = pvp_res.get("away_user_end_info") or {}
    end_by_uid: Dict[str, Dict[str, Any]] = {}
    for end_blk in (raw_home_end, raw_away_end):
        if isinstance(end_blk, dict) and end_blk.get("id") is not None:
            end_by_uid[str(end_blk["id"])] = end_blk

    home_end = end_by_uid.get(home_uid) or (raw_home_end if room_host_uid == home_uid else raw_away_end)
    away_end = end_by_uid.get(away_uid) or (raw_away_end if room_host_uid == home_uid else raw_home_end)
    user_turns_taken = pvp_res.get("home_user_turn_count" if room_host_uid == home_uid else "away_user_turn_count")
    enemy_turns_taken = pvp_res.get("away_user_turn_count" if room_host_uid == home_uid else "home_user_turn_count")
    ping_info = match_info.get("pinginfo") or {}

    away_npc_id = enemy_info_raw.get("npc_id")
    away_is_bot = bool(away_npc_id) or (away_uid.startswith("-") and away_uid != "-1")

    # Extract full RTA Draft & Ban phase
    draft_info = extract_rta_draft_info(match_info, pvp_res, home_uid, away_uid, away_is_bot, db)
    user_pick_by_code = {p["code"]: p for p in draft_info["user_picks"]}
    enemy_pick_by_code = {p["code"]: p for p in draft_info["enemy_picks"]}

    # Decode verify_data -> snap_data[0].unit_state (supports both list[0..7] & dict!)
    snap0_states: Dict[int, Dict[str, Any]] = {}
    if raw_json.get("verify_data"):
        try:
            vd = decompress_dat_bytes(base64.b64decode(raw_json["verify_data"]))
            snaps = vd.get("snap_data") or []
            if snaps:
                snap0_states = normalize_rta_uid_dict(snaps[0].get("unit_state"))
        except Exception:
            snap0_states = {}

    # Decode ML Telemetry Stats (`web_replay_data -> ml_data.record_datas[0]`)
    ml_stats_by_code = extract_ml_telemetry_stats(pvp_res.get("web_replay_data"))

    # Combat performance stats by battle UID (1..8)
    pvp_stats_by_uid: Dict[int, Dict[str, Any]] = {}
    for info in (pvp_res.get("result_stat") or {}).get("infos") or []:
        if isinstance(info, dict) and isinstance(info.get("uid"), int):
            pvp_stats_by_uid[info["uid"]] = info

    user_team_raw = logic.get("team_data") or {}
    ways = map_data.get("ways") or {}
    first_way = next(iter(ways.values()), [{}])
    enemy_team_raw = (first_way[0] if first_way else {}).get("team") or {}

    uid_to_arti_code: Dict[int, str] = {}
    for u_entry in (user_team_raw.get("units") or []) + (enemy_team_raw.get("units") or []):
        uid_i = (u_entry.get("unit") or {}).get("id")
        if isinstance(uid_i, int):
            ts_arti = (u_entry.get("template_status") or {}).get("arti_id")
            if ts_arti:
                uid_to_arti_code[uid_i] = str(ts_arti)
            for eq in u_entry.get("equips") or []:
                ec = eq.get("code", "")
                if ec and "f" not in eq and not ec.startswith("ex"):
                    uid_to_arti_code[uid_i] = ec

    init_arti_cs = extract_rta_init_artifact_cs_levels(raw_json.get("init_record") or [], uid_to_arti_code, db)

    user_units = analyze_rta_team(
        user_team_raw.get("units") or [],
        db,
        init_arti_cs,
        snap0_states,
        pvp_stats_by_uid,
        ml_stats_by_code,
        user_pick_by_code,
    )
    enemy_units = analyze_rta_team(
        enemy_team_raw.get("units") or [],
        db,
        init_arti_cs,
        snap0_states,
        pvp_stats_by_uid,
        ml_stats_by_code,
        enemy_pick_by_code,
    )

    # Link drafted picks to their in-battle UIDs (`1..8`) & positions
    user_uid_by_code = {u["code"]: u for u in user_units}
    enemy_uid_by_code = {u["code"]: u for u in enemy_units}
    for p in draft_info["user_picks"]:
        bu = user_uid_by_code.get(p["code"])
        p["battle_uid"] = bu["uid"] if bu else None
        p["pos_name"] = bu["pos_name"] if bu else None
    for p in draft_info["enemy_picks"]:
        bu = enemy_uid_by_code.get(p["code"])
        p["battle_uid"] = bu["uid"] if bu else None
        p["pos_name"] = bu["pos_name"] if bu else None

    timeline, opening_cr, rng_summary = build_rta_battle_timeline(
        raw_json.get("init_record") or [],
        raw_json.get("record_data") or [],
        user_units,
        enemy_units,
        home_uid,
        away_uid,
        db,
        room_host_uid=room_host_uid,
    )

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

    winner_arena_uid = str(pvp_res.get("winner") or "")
    if winner_arena_uid == home_uid or home_end.get("win") == 1:
        winner_side = "user"
        user_result, enemy_result = "Victory", "Defeat"
    elif winner_arena_uid == away_uid or away_end.get("win") == 1:
        winner_side = "enemy"
        user_result, enemy_result = "Defeat", "Victory"
    else:
        winner_side = "unknown"
        user_result, enemy_result = "Unknown", "Unknown"

    # Determine Win Condition (Elimination vs Yield/Concede)
    reason_code = pvp_res.get("reason")
    losing_units_side = "enemy" if winner_side == "user" else "user"
    end_snap_units = (timeline[-1].get("end_snapshot") or {}).get("units") if timeline else []
    losers_alive = sum(1 for u in (end_snap_units or []) if u["side"] == losing_units_side and not u["dead"])
    if end_snap_units and losers_alive == 0:
        win_reason = "Elimination (All 4 Defeated)"
    elif losers_alive > 0 and winner_side in ("user", "enemy"):
        win_reason = "Yield / Concede" if reason_code != 1 else "Battle Ended"
    else:
        win_reason = "Elimination" if reason_code == 1 else "Completed"

    frenzy_final = max(
        int(pvp_res.get("rta_penalty_level") or 0),
        max((t.get("frenzy_stage") or 0 for t in timeline), default=0),
    )

    def _build_player_profile(
        arena_uid: str,
        side: str,
        info_raw: Dict[str, Any],
        end_raw: Dict[str, Any],
        team_raw: Dict[str, Any],
        units: List[Dict[str, Any]],
        is_bot: bool = False,
    ) -> Dict[str, Any]:
        world_raw = str(info_raw.get("world") or "")
        league_raw = str(end_raw.get("league_id") or info_raw.get("league_id") or "unranked").lower()
        old_score = end_raw.get("old_score") if end_raw.get("old_score") is not None else info_raw.get("score")
        new_score = end_raw.get("new_score") if end_raw.get("new_score") is not None else old_score
        score_delta = (int(new_score) - int(old_score)) if (isinstance(old_score, (int, float)) and isinstance(new_score, (int, float))) else 0
        rep_code = str(info_raw.get("leader_code") or team_raw.get("leader_code") or "")
        rep_hero = db.get_hero(rep_code) if rep_code else None
        ping_ms = (ping_info.get(arena_uid) or {}).get("ping") if isinstance(ping_info, dict) else None

        return {
            "side": side,
            "arena_uid": arena_uid,
            "account_id": info_raw.get("user_id") or team_raw.get("id"),
            "name": info_raw.get("name") or team_raw.get("name") or ("Opponent" if side == "enemy" else "User"),
            "level": info_raw.get("level") or team_raw.get("level"),
            "clan_name": info_raw.get("clan_name") or "",
            "world_code": world_raw,
            "server": WORLD_LABELS.get(world_raw, world_raw or "Unknown"),
            "old_league_id": str(end_raw.get("old_league_id") or info_raw.get("league_id") or league_raw).lower(),
            "old_league": LEAGUE_LABELS.get(str(end_raw.get("old_league_id") or info_raw.get("league_id") or league_raw).lower(), league_raw.title()),
            "league_id": league_raw,
            "league": LEAGUE_LABELS.get(league_raw, league_raw.title()),
            "league_rank": end_raw.get("league_rank") or info_raw.get("league_rank") or 0,
            "old_score": old_score,
            "new_score": new_score,
            "score_delta": score_delta,
            "cp": team_raw.get("team_point"),
            "ping_ms": ping_ms,
            "is_first_pick": draft_info["first_pick_side"] == side,
            "is_bot": is_bot,
            "npc_id": info_raw.get("npc_id"),
            "rep_hero": {
                "code": rep_code,
                "name": rep_hero.get("name", rep_code),
                "icon": rep_hero.get("icon", ""),
            } if rep_hero else None,
            "result": user_result if side == "user" else enemy_result,
            "turns_taken": user_turns_taken if side == "user" else enemy_turns_taken,
            "units": units,
        }

    user_profile = _build_player_profile(home_uid, "user", user_info_raw, home_end, user_team_raw, user_units, is_bot=False)
    enemy_profile = _build_player_profile(away_uid, "enemy", enemy_info_raw, away_end, enemy_team_raw, enemy_units, is_bot=away_is_bot)

    return {
        "file": path.name,
        "mode_code": mode_code,
        "mode_label": RTA_MODE_LABELS.get(mode_code, mode_code),
        "season_id": season_id,
        "battle_room_key": battle_room_key,
        "epoch": epoch_ts,
        "played_at_utc": played_at_utc,
        "duration_seconds": pvp_res.get("battle_play_time"),
        "turn_count": pvp_res.get("turn_count"),
        "home_turn_count": user_turns_taken,
        "away_turn_count": enemy_turns_taken,
        "frenzy_stage": frenzy_final,
        "winner_side": winner_side,
        "user_result": user_result,
        "enemy_result": enemy_result,
        "win_reason": win_reason,
        "user": user_profile,
        "enemy": enemy_profile,
        "draft": draft_info,
        "opening_cr": opening_cr,
        "rng_summary": rng_summary,
        "timeline": timeline,
    }


# ============================================================================
# Console RTA Match Summary Printer
# ============================================================================

def print_rta_summary(matches: List[Dict[str, Any]]) -> None:
    for idx, m in enumerate(matches, 1):
        u = m["user"]
        e = m["enemy"]
        dr = m["draft"]
        dur = f"{m['duration_seconds']}s" if m.get("duration_seconds") is not None else "N/A"
        pts_s = ""
        if u.get("old_score") is not None and u.get("new_score") is not None:
            sign = "+" if (u.get("score_delta") or 0) >= 0 else ""
            pts_s = f" | Score: {u['old_score']} -> {u['new_score']} ({sign}{u['score_delta']})"

        print("=" * 128)
        print(
            f"RTA MATCH #{idx} [{m['mode_label']} | Season: {m['season_id']} | Room #{m['battle_room_key']}] "
            f"| {m['played_at_utc'] or ''}"
        )
        print(
            f"  Result: {m['user_result'].upper()} ({m['win_reason']}) | Duration: {dur} | "
            f"Turns: {m['turn_count']} (User {m['home_turn_count']} / Opp {m['away_turn_count']}) | "
            f"Battle Frenzy: Stage {m['frenzy_stage']}{pts_s}"
        )
        print(
            f"  Player 1 (HOME): {u['name']} [{u['server']}] | Guild: {u['clan_name'] or 'None'} | "
            f"Tier: {u['league']}{' (FIRST PICK)' if u['is_first_pick'] else ''}"
        )
        bot_tag = f" [PLACEMENT BOT: {e['npc_id']}]" if e.get("is_bot") else ""
        print(
            f"  Player 2 (AWAY): {e['name']} [{e['server']}] | Guild: {e['clan_name'] or 'None'} | "
            f"Tier: {e['league']}{' (FIRST PICK)' if e['is_first_pick'] else ''}{bot_tag}"
        )
        print("-" * 128)

        u_pre = ", ".join(x["name"] for x in dr["user_prebans"]) or "None"
        e_pre = ", ".join(x["name"] for x in dr["enemy_prebans"]) or "None"
        print(f"  [PRE-BANS]  User Pre-Banned: {u_pre}  ||  Opponent Pre-Banned: {e_pre}")

        for label, picks in (("USER 5-PICK DRAFT", dr["user_picks"]), ("OPPONENT 5-PICK DRAFT", dr["enemy_picks"])):
            parts = []
            for p in picks:
                g_str = f"G#{p['global_pick_order']} " if p.get("global_pick_order") else f"P{p['pick_slot']} "
                art_s = f" [{p['artifact']['name']}]" if p.get("artifact") else ""
                imp_s = f" ({p['imprint_grade']})" if p.get("imprint_grade") else ""
                status = " ❌BANNED" if p["is_banned"] else (" 🛡️PROTECTED" if p["is_ban_protected"] else "")
                parts.append(f"{g_str}{p['name']}{imp_s}{art_s}{status}")
            print(f"  [{label:<21}] " + " -> ".join(parts))

        for side_label, prof in (("OPPONENT 4-HERO FIELDED TEAM (UNMASKED)", e), ("USER 4-HERO FIELDED TEAM", u)):
            print(f"\n  -- {side_label}: {prof['name']} --")
            print(
                f"    {'Pos':<6} {'Hero':<26} {'ATK':>5} {'DEF':>5} {'HP':>6} {'SPD':>4} "
                f"{'CRIT%':>6} {'CDMG%':>6} {'EFF%':>5} {'ER%':>5} | {'ES':>4} | {'Mola':>4} | {'Sets':<20} | {'Artifact, EE & Imprint'}"
            )
            for unit in prof["units"]:
                st = unit["sheet_stats"]
                pi = unit["post_imprint_stats"]
                sets_str = "/".join(unit["sets"]) if unit["sets"] else "Broken"
                if unit["broken_pieces"] >= 2 and unit["sets"]:
                    sets_str += "+Brk"
                arti = unit["artifact"]
                arti_s = f"{arti['name']} +{arti['level']}" if arti else "No Artifact"
                cr_s = f"{st['cr']:.1f}" + ("*" if st["cr_uncapped"] > 100.0 else "")
                cd_s = f"{st['cd']:.1f}" + ("*" if st["cd_uncapped"] > 350.0 else "")
                ee_s = f" | EE#{unit['exclusive_equipment']['option']}" if unit["exclusive_equipment"] else ""
                imp_s = f" | {unit['imprint_summary']}" if unit["imprint_summary"] != "None" else ""
                spd_imp = f"({pi['spd']})" if pi["spd"] != st["spd"] else ""
                es_s = "TMPL" if unit["is_template_build"] else f"{unit['total_equipment_score']:4d}"
                mola_s = f"+{unit['molagora_total']}"
                print(
                    f"    {unit['pos_name']:<6} {unit['name'][:26]:<26} {st['atk']:5d} {st['def']:5d} {st['hp']:6d} {st['spd']:4d}{spd_imp:<5} "
                    f"{cr_s:>6} {cd_s:>6} {st['eff']:5.1f} {st['res']:5.1f} | {es_s:>4} | {mola_s:>4} | {sets_str[:20]:<20} | {arti_s}{ee_s}{imp_s}"
                )
        print()


# ============================================================================
# CLI Entrypoint
# ============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse Epic Seven World Arena / RTA replay (.dat) files and unmask Drafts, Bans, Builds & Timelines."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Path(s) to RTA .dat replay files or directories (defaults to ./data/world_arena).",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for rta_analysis.json and rta_report.html (default: ./output).",
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
        if not args.inputs and not DEFAULT_RTA_DATA_DIR.exists():
            return 0

    targets = args.inputs if args.inputs else [str(DEFAULT_RTA_DATA_DIR)]
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
        print(f"No RTA .dat replay files found in: {targets}", file=sys.stderr)
        return 1

    db = GameDatabase(gamedata_dir=gamedata_dir, offline=args.offline)
    matches = [parse_rta_replay_file(p, db) for p in dat_files]
    matches.sort(key=lambda m: (m.get("epoch") or 0, m.get("file") or ""))
    for i, m in enumerate(matches, 1):
        m["match_index"] = i

    if not args.quiet:
        print_rta_summary(matches)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "rta_analysis.json"
    export_payload = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_matches": len(matches),
        "matches": matches,
    }
    json_path.write_text(json.dumps(export_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    from e7_rta_report import generate_rta_html_report
    html_path = out_dir / "rta_report.html"
    generate_rta_html_report(export_payload, html_path)

    print(f"Saved RTA JSON analysis -> {json_path}")
    print(f"Saved RTA HTML report   -> {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

