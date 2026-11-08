#!/usr/bin/env python3
"""
Epic Seven World Arena (RTA) HTML Report & Interactive 4v4 Battle Visualizer
=============================================================================
Generates a self-contained, responsive, eye-friendly, icon-rich interactive HTML
report (`output/rta_report.html`) from parsed `net_rank-*.dat` World Arena replays:

  - Official Epic Seven UI Icons (Elements, Classes, Stats, 24 Equipment Sets,
    Gear Slots, Artifacts, Exclusive Equipment, Skills, and Buffs/Debuffs)
  - Full RTA Draft & Ban Board (Pre-Bans, 1-2-2-2-2-1 Snake Picks, Ban Protection, Post-Bans)
  - Spacious 4v4 Unmasked Build Cards with `In-Battle` vs `Hero Sheet` Stat Mode Toggle,
    Dynamic Artifact Stat Display (`ATK` / `DEF` / `HP`), and Expand-All 6-Piece Gear Drawers
  - Collision-Free Multi-Lane Opening Turn-0 CR Race Track + `e7calc.xyz` Speed RNG Solver
    with interactive pin <-> table cross-highlighting
  - Sticky Side-by-Side 8-Unit Live Battle HUD (accurate Room-Host-aware Souls, Frenzy,
    HP/Barrier/Injury, CR%, Active Buff/Debuff icons, Click-Hero-to-Filter, Auto-Play & `←`/`→` keys)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


RTA_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Epic Seven World Arena (RTA) — Unmasked Draft, Builds &amp; Timeline</title>
<style>
  :root {
    --bg-page: #0d1321;
    --bg-surf-1: #131b2e;
    --bg-surf-2: #19233c;
    --bg-surf-3: #212e4d;
    --bg-inset: #0a0f1c;
    --border-subtle: rgba(148, 163, 184, 0.14);
    --border-strong: rgba(148, 163, 184, 0.28);

    --text-0: #f1f5f9;
    --text-1: #cbd5e1;
    --text-2: #8b9bb4;

    --cyan: #38bdf8;
    --cyan-soft: rgba(56, 189, 248, 0.13);
    --gold: #f5b942;
    --gold-soft: rgba(245, 185, 66, 0.13);
    --green: #34d399;
    --green-soft: rgba(52, 211, 153, 0.13);
    --red: #fb7185;
    --red-soft: rgba(251, 113, 133, 0.14);
    --purple: #c084fc;
    --purple-soft: rgba(192, 132, 252, 0.14);
    --magenta: #f472b6;
    --amber: #fbbf24;

    --radius-lg: 14px;
    --radius-md: 10px;
    --radius-sm: 7px;
    --shadow-card: 0 8px 24px rgba(3, 7, 18, 0.35);
    --font-sans: "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    --font-mono: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    background: radial-gradient(circle at 15% 0%, #162038 0%, var(--bg-page) 55%);
    color: var(--text-0);
    font-family: var(--font-sans);
    font-size: 14px;
    line-height: 1.5;
    min-height: 100vh;
    padding: 20px 24px 64px;
    -webkit-font-smoothing: antialiased;
  }
  @media (max-width: 640px) {
    body { padding: 12px 12px 48px; }
  }

  .container { max-width: 1640px; margin: 0 auto; }

  /* Top App Header */
  .app-header {
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
    gap: 16px; padding: 18px 22px; margin-bottom: 16px;
    background: linear-gradient(135deg, rgba(25, 35, 60, 0.92), rgba(19, 27, 46, 0.96));
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-card);
  }
  .brand-title { font-size: 1.35rem; font-weight: 800; letter-spacing: -0.02em; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .brand-badge {
    font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
    padding: 3px 9px; border-radius: 999px; background: var(--cyan-soft); color: var(--cyan); border: 1px solid rgba(56,189,248,0.35);
  }
  .brand-sub { font-size: 0.84rem; color: var(--text-2); margin-top: 4px; }
  .header-stats { display: flex; gap: 8px; flex-wrap: wrap; }
  .stat-pill {
    background: var(--bg-inset); border: 1px solid var(--border-subtle);
    padding: 5px 12px; border-radius: 999px; font-size: 0.78rem; color: var(--text-1);
  }
  .stat-pill strong { color: var(--text-0); font-variant-numeric: tabular-nums; }

  /* Sticky Interactive Control Bar */
  .sticky-bar {
    position: sticky; top: 10px; z-index: 90;
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px;
    padding: 10px 14px; margin-bottom: 20px;
    background: rgba(15, 22, 38, 0.88);
    backdrop-filter: blur(12px);
    border: 1px solid var(--border-strong);
    border-radius: 12px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  }
  .match-tabs { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .tab-btn {
    background: transparent; color: var(--text-1); border: 1px solid transparent;
    padding: 6px 13px; border-radius: 8px; cursor: pointer; font-size: 0.81rem; font-weight: 600;
    transition: all 0.15s ease;
  }
  .tab-btn:hover { background: var(--bg-surf-2); color: var(--text-0); }
  .tab-btn.active {
    background: var(--cyan-soft); color: var(--cyan); border-color: rgba(56, 189, 248, 0.45);
  }

  .global-tools { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .seg-group {
    display: inline-flex; background: var(--bg-inset); padding: 3px;
    border-radius: 8px; border: 1px solid var(--border-subtle);
  }
  .seg-btn {
    background: transparent; color: var(--text-2); border: none;
    padding: 5px 11px; border-radius: 6px; font-size: 0.76rem; font-weight: 600;
    cursor: pointer; transition: all 0.14s ease; white-space: nowrap;
  }
  .seg-btn:hover { color: var(--text-0); }
  .seg-btn.active { background: var(--bg-surf-3); color: var(--text-0); box-shadow: 0 1px 4px rgba(0,0,0,0.3); }

  .search-wrap { position: relative; display: flex; align-items: center; }
  .search-input {
    background: var(--bg-inset); color: var(--text-0);
    border: 1px solid var(--border-subtle); border-radius: 8px;
    padding: 6px 28px 6px 11px; font-size: 0.79rem; width: 210px;
    transition: border-color 0.15s, width 0.2s;
  }
  .search-input:focus { outline: none; border-color: var(--cyan); width: 250px; }
  .search-clear {
    position: absolute; right: 7px; background: none; border: none;
    color: var(--text-2); cursor: pointer; font-size: 0.85rem; display: none;
  }

  /* Match Container */
  .match-block {
    background: var(--bg-surf-1); border: 1px solid var(--border-subtle);
    border-radius: var(--radius-lg); padding: 20px 22px; margin-bottom: 26px;
    box-shadow: var(--shadow-card);
  }
  @media (max-width: 640px) {
    .match-block { padding: 14px; }
  }

  .match-top-bar {
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;
    gap: 12px; padding-bottom: 14px; margin-bottom: 16px; border-bottom: 1px solid var(--border-subtle);
  }
  .match-meta-left { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
  .match-num { font-size: 1.08rem; font-weight: 800; color: var(--text-0); }
  .res-pill { padding: 3px 11px; border-radius: 999px; font-size: 0.76rem; font-weight: 700; letter-spacing: 0.02em; }
  .res-win { background: var(--green-soft); color: var(--green); border: 1px solid rgba(52,211,153,0.4); }
  .res-loss { background: var(--red-soft); color: var(--red); border: 1px solid rgba(251,113,133,0.4); }
  .meta-tag {
    background: var(--bg-surf-2); color: var(--text-1); border: 1px solid var(--border-subtle);
    padding: 3px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 500;
  }
  .meta-tag.frenzy { color: var(--amber); border-color: rgba(251,191,36,0.35); background: rgba(251,191,36,0.09); }

  .action-btn {
    background: var(--bg-surf-2); color: var(--text-0); border: 1px solid var(--border-strong);
    padding: 6px 13px; border-radius: 8px; font-size: 0.78rem; font-weight: 600;
    cursor: pointer; transition: all 0.15s; display: inline-flex; align-items: center; gap: 6px;
  }
  .action-btn:hover { border-color: var(--cyan); color: var(--cyan); background: var(--bg-surf-3); }

  /* Scoreboard Player Banner */
  .scoreboard {
    display: grid; grid-template-columns: 1fr auto 1fr; gap: 14px; align-items: center; margin-bottom: 16px;
  }
  @media (max-width: 920px) {
    .scoreboard { grid-template-columns: 1fr; gap: 10px; }
    .vs-circle { display: none; }
  }
  .player-card {
    background: var(--bg-surf-2); border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md); padding: 12px 15px; display: flex; align-items: center; gap: 13px;
  }
  .player-card.enemy { border-left: 3px solid var(--gold); }
  .player-card.user { border-left: 3px solid var(--cyan); }
  .p-ava {
    width: 48px; height: 48px; border-radius: 50%; object-fit: cover;
    background: var(--bg-inset); border: 2px solid var(--border-strong); flex-shrink: 0;
  }
  .player-card.enemy .p-ava { border-color: var(--gold); }
  .player-card.user .p-ava { border-color: var(--cyan); }
  .p-body { flex: 1; min-width: 0; }
  .p-row1 { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
  .p-name { font-size: 1rem; font-weight: 800; color: var(--text-0); }
  .player-card.enemy .p-name { color: var(--gold); }
  .player-card.user .p-name { color: var(--cyan); }
  .p-row2 { font-size: 0.77rem; color: var(--text-2); margin-top: 3px; display: flex; gap: 8px; flex-wrap: wrap; }
  .p-row2 strong { color: var(--text-1); }
  .vs-circle {
    width: 38px; height: 38px; border-radius: 50%; background: var(--bg-surf-3);
    border: 1px solid var(--border-strong); display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 0.78rem; color: var(--text-2);
  }

  /* Match Sub-View Navigation + RNG Strip */
  .subnav-row {
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;
    gap: 12px; margin-bottom: 16px; padding: 10px 14px;
    background: var(--bg-inset); border: 1px solid var(--border-subtle); border-radius: var(--radius-md);
  }
  .rng-chips { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
  .rng-lbl { font-size: 0.75rem; font-weight: 700; color: var(--text-2); margin-right: 2px; }
  .rchip {
    font-size: 0.73rem; font-weight: 600; padding: 3px 10px; border-radius: 999px;
    cursor: pointer; transition: transform 0.12s, filter 0.12s; border: 1px solid transparent;
    display: inline-flex; align-items: center; gap: 4px;
  }
  .rchip:hover { transform: translateY(-1px); filter: brightness(1.12); }
  .tone-magenta { background: rgba(244,114,182,0.14); color: #f9a8d4; border-color: rgba(244,114,182,0.4); }
  .tone-amber { background: rgba(251,191,36,0.13); color: #fde68a; border-color: rgba(251,191,36,0.38); }
  .tone-gold { background: var(--gold-soft); color: #fde047; border-color: rgba(245,185,66,0.38); }
  .tone-cyan { background: var(--cyan-soft); color: #7dd3fc; border-color: rgba(56,189,248,0.38); }
  .tone-purple { background: var(--purple-soft); color: #e9d5ff; border-color: rgba(192,132,252,0.38); }
  .tone-green { background: var(--green-soft); color: #6ee7b7; border-color: rgba(52,211,153,0.38); }
  .tone-red { background: var(--red-soft); color: #fda4af; border-color: rgba(251,113,133,0.4); }

  /* Draft & Ban Board */
  .draft-section {
    background: var(--bg-surf-2); border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md); padding: 15px 16px; margin-bottom: 18px;
  }
  .sec-hdr {
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;
    gap: 10px; margin-bottom: 12px;
  }
  .sec-title { font-size: 0.9rem; font-weight: 700; color: var(--text-0); display: flex; align-items: center; gap: 8px; }

  .preban-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
  @media (max-width: 780px) { .preban-grid { grid-template-columns: 1fr; } }
  .preban-card {
    background: var(--bg-inset); border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm); padding: 8px 12px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .preban-lbl { font-size: 0.75rem; font-weight: 700; color: var(--text-2); margin-right: 4px; }
  .preban-pill {
    display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px 3px 4px;
    border-radius: 999px; background: var(--red-soft); border: 1px solid rgba(251,113,133,0.35);
    color: #fecdd3; font-size: 0.75rem; font-weight: 600;
  }
  .preban-pill img { width: 22px; height: 22px; border-radius: 50%; object-fit: cover; }

  .draft-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media (max-width: 1120px) { .draft-cols { grid-template-columns: 1fr; } }
  .draft-team-box {
    background: var(--bg-inset); border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm); padding: 10px 12px;
  }
  .draft-team-hdr {
    display: flex; justify-content: space-between; font-size: 0.76rem; font-weight: 700;
    margin-bottom: 9px; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .draft-team-box.enemy .draft-team-hdr { color: var(--gold); }
  .draft-team-box.user .draft-team-hdr { color: var(--cyan); }

  .picks-strip { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
  @media (max-width: 580px) { .picks-strip { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
  .pick-tile {
    background: var(--bg-surf-2); border: 1px solid var(--border-subtle);
    border-radius: 8px; padding: 8px 6px; text-align: center; position: relative;
    display: flex; flex-direction: column; align-items: center; gap: 4px; transition: transform 0.14s;
  }
  .pick-tile:hover { transform: translateY(-2px); border-color: var(--border-strong); }
  .pick-tile.is-banned {
    background: rgba(251, 113, 133, 0.07); border-color: rgba(251, 113, 133, 0.45);
  }
  .pick-tile.is-protected {
    border-color: rgba(56, 189, 248, 0.55); box-shadow: inset 0 0 12px rgba(56, 189, 248, 0.08);
  }
  .pick-ord-row {
    width: 100%; display: flex; justify-content: space-between; font-size: 0.66rem;
    color: var(--text-2); font-family: var(--font-mono); padding: 0 2px;
  }
  .pick-ord-row .g-num { color: var(--text-0); font-weight: 700; }
  .pick-ava-wrap { position: relative; width: 46px; height: 46px; }
  .pick-ava {
    width: 46px; height: 46px; border-radius: 50%; object-fit: cover;
    border: 2px solid var(--border-strong); background: var(--bg-inset);
  }
  .pick-tile.is-banned .pick-ava { filter: grayscale(0.85) opacity(0.5); border-color: var(--red); }
  .ban-slash {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    font-size: 1.35rem; font-weight: 900; color: var(--red); text-shadow: 0 1px 4px #000;
  }
  .pick-hero-name {
    font-size: 0.74rem; font-weight: 700; color: var(--text-0);
    max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .pick-arti-row {
    display: flex; align-items: center; justify-content: center; gap: 4px;
    font-size: 0.66rem; color: var(--text-2); max-width: 100%;
  }
  .pick-arti-row img { width: 15px; height: 15px; border-radius: 3px; flex-shrink: 0; }
  .pick-arti-row span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pick-badge {
    font-size: 0.62rem; font-weight: 700; padding: 1px 6px; border-radius: 4px; margin-top: auto;
  }
  .pick-badge.in { background: var(--green-soft); color: var(--green); }
  .pick-badge.ban { background: var(--red-soft); color: var(--red); }
  .pick-badge.prot { background: var(--cyan-soft); color: var(--cyan); }

  .snake-flow {
    margin-top: 10px; padding-top: 9px; border-top: 1px dashed var(--border-subtle);
    display: flex; align-items: center; gap: 5px; flex-wrap: wrap; font-size: 0.73rem;
  }
  .snake-pill {
    padding: 2px 8px; border-radius: 5px; font-weight: 600; background: var(--bg-inset); border: 1px solid var(--border-subtle);
  }
  .snake-pill.enemy { color: var(--gold); border-color: rgba(245,185,66,0.3); }
  .snake-pill.user { color: var(--cyan); border-color: rgba(56,189,248,0.3); }
  .snake-pill.banned { text-decoration: line-through; opacity: 0.55; }

  /* Spacious 4-Hero Team Build Grids (Stacked Full-Width Teams so Cards Never Squish!) */
  .builds-wrapper { display: flex; flex-direction: column; gap: 18px; }
  .team-showcase {
    background: var(--bg-surf-2); border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md); padding: 15px 16px;
  }
  .team-showcase.enemy { border-top: 3px solid var(--gold); }
  .team-showcase.user { border-top: 3px solid var(--cyan); }

  .team-hdr {
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;
    gap: 10px; margin-bottom: 13px;
  }
  .team-hdr-title { font-size: 0.88rem; font-weight: 800; letter-spacing: 0.03em; text-transform: uppercase; }
  .team-showcase.enemy .team-hdr-title { color: var(--gold); }
  .team-showcase.user .team-hdr-title { color: var(--cyan); }

  .units-grid-4 {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 14px;
  }
  @media (max-width: 1280px) { .units-grid-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (max-width: 680px) { .units-grid-4 { grid-template-columns: 1fr; } }

  /* Crisp E7-Authentic Unit Card */
  .unit-card {
    background: var(--bg-surf-1); border: 1px solid var(--border-subtle);
    border-radius: 12px; padding: 14px; display: flex; flex-direction: column; gap: 11px;
    transition: border-color 0.15s, box-shadow 0.15s, opacity 0.2s;
  }
  .unit-card:hover { border-color: var(--border-strong); box-shadow: 0 6px 18px rgba(0,0,0,0.28); }
  .unit-card.search-hit { border-color: var(--cyan); box-shadow: 0 0 0 2px rgba(56,189,248,0.3); }
  .unit-card.search-dim { opacity: 0.28; }

  .u-header { display: flex; align-items: center; gap: 11px; }
  .ava-box { position: relative; width: 54px; height: 54px; flex-shrink: 0; }
  .ava-main {
    width: 54px; height: 54px; border-radius: 50%; object-fit: cover;
    background: var(--bg-inset); border: 2px solid var(--border-strong);
  }
  .team-showcase.enemy .ava-main { border-color: rgba(245, 185, 66, 0.75); }
  .team-showcase.user .ava-main { border-color: rgba(56, 189, 248, 0.75); }
  .badge-elem, .badge-role {
    position: absolute; width: 20px; height: 20px; border-radius: 50%;
    background: #0b101d; border: 1px solid var(--border-strong); padding: 2px; object-fit: contain;
  }
  .badge-elem { bottom: -2px; left: -2px; }
  .badge-role { top: -2px; right: -2px; }

  .u-heading { min-width: 0; flex: 1; }
  .u-name-line { display: flex; align-items: center; gap: 6px; }
  .u-name {
    font-size: 0.96rem; font-weight: 800; color: var(--text-0);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .mvp-crown {
    font-size: 0.65rem; font-weight: 800; padding: 1px 6px; border-radius: 4px;
    background: var(--gold-soft); color: var(--gold); border: 1px solid rgba(245,185,66,0.45); flex-shrink: 0;
  }
  .u-meta-pills {
    display: flex; align-items: center; gap: 5px; flex-wrap: wrap; margin-top: 4px;
    font-size: 0.71rem; color: var(--text-2);
  }
  .mini-pill {
    background: var(--bg-surf-2); border: 1px solid var(--border-subtle);
    padding: 1px 6px; border-radius: 4px; color: var(--text-1); font-weight: 600;
    display: inline-flex; align-items: center; gap: 3px;
  }
  .mini-pill img { width: 12px; height: 12px; object-fit: contain; }
  .mini-pill.mola { color: #d8b4fe; border-color: rgba(192,132,252,0.3); }
  .mini-pill.warn { color: var(--red); border-color: rgba(251,113,133,0.4); }

  /* Clean E7 Icon Stat Table */
  .stat-grid {
    background: var(--bg-inset); border: 1px solid var(--border-subtle);
    border-radius: 9px; padding: 6px 10px; display: flex; flex-direction: column; gap: 1px;
  }
  .stat-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 3.5px 2px; border-bottom: 1px solid rgba(148, 163, 184, 0.06);
  }
  .stat-row:last-child { border-bottom: none; }
  .stat-lbl {
    display: flex; align-items: center; gap: 7px; color: var(--text-2);
    font-size: 0.77rem; font-weight: 500;
  }
  .stat-ico { width: 16px; height: 16px; object-fit: contain; opacity: 0.9; flex-shrink: 0; }
  .stat-val-wrap { display: flex; align-items: center; gap: 5px; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
  .stat-val { font-size: 0.82rem; font-weight: 700; color: var(--text-0); }
  .stat-row.unmasked .stat-val { color: #fcd34d; }
  .stat-delta {
    font-size: 0.67rem; font-weight: 600; padding: 0 5px; border-radius: 4px;
    background: var(--green-soft); color: var(--green); cursor: help;
  }
  .stat-overcap { font-size: 0.67rem; color: var(--amber); cursor: help; }

  /* Sets & Equipment Strip */
  .sets-row { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
  .set-badge {
    display: inline-flex; align-items: center; gap: 5px;
    background: var(--bg-surf-2); border: 1px solid var(--border-subtle);
    padding: 2px 8px 2px 5px; border-radius: 6px; font-size: 0.72rem; font-weight: 600; color: var(--text-0);
  }
  .set-badge img { width: 16px; height: 16px; object-fit: contain; }
  .set-badge.es-badge {
    margin-left: auto; background: var(--purple-soft); border-color: rgba(192,132,252,0.4);
    color: #e9d5ff; font-family: var(--font-mono); cursor: help;
  }
  .set-badge.tmpl-badge {
    margin-left: auto; background: rgba(251,191,36,0.12); border-color: rgba(251,191,36,0.4); color: var(--amber);
  }

  /* Artifact & EE Visual Cards */
  .equip-card {
    display: flex; align-items: center; gap: 9px;
    background: var(--bg-inset); border: 1px solid var(--border-subtle);
    border-radius: 8px; padding: 7px 9px; min-height: 48px;
  }
  .eq-thumb {
    width: 34px; height: 34px; border-radius: 6px; object-fit: cover;
    background: var(--bg-surf-2); border: 1px solid var(--border-strong); flex-shrink: 0;
  }
  .eq-thumb-fallback {
    width: 34px; height: 34px; border-radius: 6px; background: var(--bg-surf-2);
    border: 1px dashed var(--border-strong); display: flex; align-items: center; justify-content: center;
    font-size: 0.75rem; color: var(--text-2); flex-shrink: 0;
  }
  .eq-info { min-width: 0; flex: 1; }
  .eq-title-row { display: flex; align-items: center; gap: 5px; }
  .eq-name {
    font-size: 0.77rem; font-weight: 700; color: var(--text-0);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .eq-lvl {
    font-size: 0.69rem; font-weight: 800; color: var(--gold);
    background: var(--gold-soft); padding: 0 5px; border-radius: 4px; flex-shrink: 0;
  }
  .eq-sub { font-size: 0.7rem; color: var(--text-2); font-family: var(--font-mono); margin-top: 1px; }

  .ee-details {
    background: var(--bg-inset); border: 1px solid var(--border-subtle);
    border-radius: 8px; padding: 6px 9px; font-size: 0.73rem;
  }
  .ee-summary {
    display: flex; align-items: center; gap: 8px; cursor: pointer; list-style: none;
  }
  .ee-summary::-webkit-details-marker { display: none; }
  .ee-ico { width: 24px; height: 24px; object-fit: contain; flex-shrink: 0; }
  .ee-desc-body {
    margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border-subtle);
    color: var(--text-1); font-size: 0.71rem; line-height: 1.45;
  }

  .imprint-bar {
    display: flex; justify-content: space-between; align-items: center; gap: 6px; flex-wrap: wrap;
    font-size: 0.71rem; color: var(--text-2); padding: 0 2px;
  }
  .imprint-bar strong { color: var(--text-1); }
  .aura-tag { color: var(--green); font-weight: 600; }

  .combat-foot {
    display: flex; justify-content: space-between; align-items: center;
    padding-top: 7px; border-top: 1px dashed var(--border-subtle);
    font-size: 0.71rem; color: var(--text-2); font-family: var(--font-mono);
  }

  /* 6-Piece Gear Drawer */
  .gear-btn {
    margin-top: auto; width: 100%; background: var(--bg-surf-2); color: var(--text-1);
    border: 1px solid var(--border-subtle); border-radius: 7px; padding: 7px 10px;
    font-size: 0.74rem; font-weight: 600; cursor: pointer; transition: all 0.14s;
    display: flex; justify-content: space-between; align-items: center;
  }
  .gear-btn:hover { background: var(--bg-surf-3); color: var(--text-0); border-color: var(--cyan); }
  .gear-drawer { display: none; flex-direction: column; gap: 6px; margin-top: 4px; }
  .gear-drawer.open { display: flex; }
  .gear-card {
    background: var(--bg-inset); border: 1px solid var(--border-subtle);
    border-radius: 7px; padding: 7px 9px; font-size: 0.72rem;
  }
  .gc-head { display: flex; justify-content: space-between; align-items: center; gap: 6px; margin-bottom: 3px; }
  .gc-slot { display: flex; align-items: center; gap: 5px; font-weight: 700; color: var(--text-0); }
  .gc-slot img { width: 15px; height: 15px; object-fit: contain; }
  .gc-es { font-family: var(--font-mono); font-weight: 700; color: var(--purple); font-size: 0.7rem; }
  .gc-main { color: var(--gold); font-weight: 700; margin-bottom: 4px; font-size: 0.73rem; }
  .gc-subs {
    display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px;
    color: var(--text-1); font-family: var(--font-mono); font-size: 0.69rem;
  }
  .mod-gem { color: var(--cyan); font-size: 0.63rem; font-weight: 700; margin-left: 2px; }

  /* Opening Turn-0 CR Race Track (Multi-Lane Collision-Free!) */
  .timeline-section { display: flex; flex-direction: column; gap: 16px; margin-top: 18px; }
  .cr-box {
    background: var(--bg-surf-2); border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md); padding: 16px 18px;
  }
  .cr-alert-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
  .cr-alert {
    padding: 7px 11px; border-radius: 7px; font-size: 0.77rem; font-weight: 600;
    background: rgba(244,114,182,0.12); border: 1px solid rgba(244,114,182,0.4); color: #fbcfe8;
  }

  .cr-track-stage {
    position: relative;
    background: linear-gradient(90deg, rgba(10,15,28,0.92) 0%, rgba(25,35,60,0.85) 65%, rgba(56,189,248,0.16) 100%);
    border: 1px solid var(--border-strong); border-radius: 10px;
    margin: 10px 0 12px; overflow: hidden;
  }
  .cr-axis-bar {
    position: absolute; left: 28px; right: 28px; bottom: 22px; height: 4px;
    background: rgba(148, 163, 184, 0.25); border-radius: 2px;
  }
  .cr-tick {
    position: absolute; bottom: 4px; transform: translateX(-50%);
    font-size: 0.66rem; font-family: var(--font-mono); color: var(--text-2);
  }
  .cr-pin {
    position: absolute; transform: translateX(-50%);
    display: flex; align-items: center; gap: 5px;
    background: rgba(13, 19, 33, 0.94); border: 1px solid var(--border-strong);
    padding: 2px 7px 2px 3px; border-radius: 999px;
    cursor: pointer; z-index: 5; transition: transform 0.14s ease, border-color 0.14s, box-shadow 0.14s;
    white-space: nowrap;
  }
  .cr-pin.enemy { border-color: rgba(245, 185, 66, 0.65); }
  .cr-pin.user { border-color: rgba(56, 189, 248, 0.65); }
  .cr-pin.winner { border-color: var(--green); box-shadow: 0 0 10px rgba(52, 211, 153, 0.3); }
  .cr-pin:hover, .cr-pin.hl {
    z-index: 60 !important; transform: translateX(-50%) scale(1.1);
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.65); border-color: #fff;
  }
  .cr-pin img { width: 26px; height: 26px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
  .cr-pin-txt { font-size: 0.68rem; font-family: var(--font-mono); font-weight: 700; color: var(--text-0); }
  .cr-pin.enemy .cr-pin-txt { color: #fde68a; }
  .cr-pin.user .cr-pin-txt { color: #bae6fd; }
  .cr-stem {
    position: absolute; width: 1px; background: rgba(148, 163, 184, 0.28);
    pointer-events: none; z-index: 1;
  }
  .cr-hover-readout {
    min-height: 22px; font-size: 0.76rem; color: var(--text-1);
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;
  }

  .cr-table-details summary {
    cursor: pointer; font-size: 0.77rem; font-weight: 700; color: var(--cyan);
    padding: 6px 0; user-select: none;
  }
  .cr-table { width: 100%; border-collapse: collapse; font-size: 0.75rem; margin-top: 6px; }
  .cr-table th {
    text-align: left; color: var(--text-2); font-weight: 600; padding: 6px 8px;
    border-bottom: 1px solid var(--border-strong); font-size: 0.71rem;
  }
  .cr-table td {
    padding: 6px 8px; border-bottom: 1px solid var(--border-subtle);
    font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  }
  .cr-table tr { transition: background 0.12s; cursor: pointer; }
  .cr-table tbody tr:hover, .cr-table tbody tr.hl { background: rgba(56, 189, 248, 0.11); }
  .cr-hero-cell { font-family: var(--font-sans) !important; font-weight: 700; display: flex; align-items: center; gap: 7px; }
  .cr-hero-cell img { width: 22px; height: 22px; border-radius: 50%; object-fit: cover; }

  /* Sticky Side-by-Side Live Battle HUD + Turn Feed */
  .battle-Theater {
    display: grid; grid-template-columns: 460px minmax(0, 1fr); gap: 16px; align-items: start;
  }
  @media (max-width: 1180px) {
    .battle-Theater { grid-template-columns: 1fr; }
  }
  .hud-sticky {
    position: sticky; top: 72px;
    background: var(--bg-surf-2); border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md); padding: 14px;
  }
  @media (max-width: 1180px) { .hud-sticky { position: static; } }

  .hud-hdr {
    display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap;
    padding-bottom: 10px; margin-bottom: 10px; border-bottom: 1px solid var(--border-subtle);
  }
  .hud-title { font-size: 0.85rem; font-weight: 800; color: var(--text-0); }
  .hud-hint { font-size: 0.69rem; color: var(--text-2); margin-bottom: 10px; }

  .hud-side-block { margin-bottom: 12px; }
  .hud-side-block:last-child { margin-bottom: 0; }
  .hud-side-top {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 0.76rem; font-weight: 700; margin-bottom: 7px;
  }
  .hud-side-top.enemy { color: var(--gold); }
  .hud-side-top.user { color: var(--cyan); }
  .soul-meter { display: inline-flex; align-items: center; gap: 5px; font-family: var(--font-mono); font-size: 0.73rem; }
  .soul-pip {
    width: 8px; height: 8px; border-radius: 50%; background: rgba(148,163,184,0.22);
    border: 1px solid rgba(148,163,184,0.35); display: inline-block;
  }
  .soul-pip.lit { background: var(--cyan); border-color: #bae6fd; box-shadow: 0 0 6px var(--cyan); }

  .hud-grid-2x2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  @media (max-width: 500px) { .hud-grid-2x2 { grid-template-columns: 1fr; } }

  .hud-tile {
    background: var(--bg-surf-1); border: 1px solid var(--border-subtle);
    border-radius: 8px; padding: 8px 9px; cursor: pointer; transition: all 0.14s;
  }
  .hud-tile:hover { border-color: var(--border-strong); }
  .hud-tile.acting { border-color: var(--green); box-shadow: 0 0 0 1px rgba(52,211,153,0.35); }
  .hud-tile.hero-flt { border-color: var(--cyan); background: rgba(56,189,248,0.08); }
  .hud-tile.dead { opacity: 0.38; filter: grayscale(0.9); }

  .ht-top { display: flex; align-items: center; gap: 7px; margin-bottom: 5px; }
  .ht-top img { width: 28px; height: 28px; border-radius: 50%; object-fit: cover; border: 1px solid var(--border-strong); }
  .ht-name { font-size: 0.75rem; font-weight: 700; flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ht-cr { font-size: 0.68rem; font-family: var(--font-mono); font-weight: 700; color: var(--cyan); }

  .hp-track {
    height: 7px; background: var(--bg-inset); border-radius: 4px; overflow: hidden;
    display: flex; border: 1px solid rgba(255,255,255,0.06); margin-bottom: 3px;
  }
  .hp-bar-fill { background: linear-gradient(90deg, #10b981, #34d399); height: 100%; }
  .sh-bar-fill { background: #60a5fa; height: 100%; }
  .inj-bar-fill { background: #f43f5e; height: 100%; opacity: 0.7; }
  .ht-nums { display: flex; justify-content: space-between; font-size: 0.65rem; font-family: var(--font-mono); color: var(--text-2); }
  .cr-mini-track { height: 3px; background: var(--bg-inset); border-radius: 2px; overflow: hidden; margin: 4px 0; }
  .cr-mini-track > div { height: 100%; background: var(--cyan); }

  .ht-effs { display: flex; flex-wrap: wrap; gap: 3px; margin-top: 4px; min-height: 18px; }
  .eff-chip {
    display: inline-flex; align-items: center; gap: 3px;
    font-size: 0.61rem; font-weight: 600; padding: 1px 5px 1px 3px; border-radius: 4px;
    max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .eff-chip img { width: 13px; height: 13px; object-fit: contain; flex-shrink: 0; }
  .eff-chip.buff { background: rgba(56,189,248,0.15); color: #bae6fd; border: 1px solid rgba(56,189,248,0.3); }
  .eff-chip.debuff { background: rgba(251,113,133,0.16); color: #fecdd3; border: 1px solid rgba(251,113,133,0.35); }

  /* Right Column: Scrubber + Turn Feed */
  .feed-panel {
    background: var(--bg-surf-2); border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md); padding: 14px 16px;
  }
  .playback-bar {
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;
    gap: 8px; padding-bottom: 10px; margin-bottom: 10px; border-bottom: 1px solid var(--border-subtle);
  }
  .pb-btns { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
  .pb-btn {
    background: var(--bg-surf-1); color: var(--text-0); border: 1px solid var(--border-strong);
    padding: 5px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; cursor: pointer;
  }
  .pb-btn:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
  .pb-btn.playing { background: var(--cyan-soft); border-color: var(--cyan); color: var(--cyan); }
  .pb-btn:disabled { opacity: 0.38; cursor: default; }
  .pb-counter { font-family: var(--font-mono); font-size: 0.76rem; color: var(--text-1); padding: 0 6px; }

  .flt-pills { display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 12px; }
  .flt-pill {
    background: var(--bg-inset); color: var(--text-2); border: 1px solid var(--border-subtle);
    padding: 4px 10px; border-radius: 999px; font-size: 0.72rem; font-weight: 600; cursor: pointer;
  }
  .flt-pill:hover { color: var(--text-0); }
  .flt-pill.active { background: var(--cyan-soft); color: var(--cyan); border-color: rgba(56,189,248,0.45); }

  .turn-feed-scroll {
    display: flex; flex-direction: column; gap: 8px; max-height: 640px; overflow-y: auto; padding-right: 4px;
  }
  .t-card {
    background: var(--bg-surf-1); border: 1px solid var(--border-subtle); border-left: 3px solid var(--border-strong);
    border-radius: 8px; padding: 9px 12px; cursor: pointer; transition: background 0.12s, border-color 0.12s;
  }
  .t-card.side-enemy { border-left-color: var(--gold); }
  .t-card.side-user { border-left-color: var(--cyan); }
  .t-card:hover { background: rgba(255,255,255,0.02); }
  .t-card.selected {
    background: rgba(56, 189, 248, 0.08); border-color: rgba(56, 189, 248, 0.55);
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.25);
  }
  .t-card-hdr { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
  .t-actor { display: flex; align-items: center; gap: 7px; font-weight: 700; font-size: 0.82rem; }
  .t-num {
    font-family: var(--font-mono); font-size: 0.71rem; padding: 1px 6px; border-radius: 4px;
    background: var(--bg-inset); color: var(--text-1); border: 1px solid var(--border-subtle);
  }
  .t-actor img { width: 22px; height: 22px; border-radius: 50%; object-fit: cover; }
  .clock-tag {
    font-size: 0.68rem; font-family: var(--font-mono); color: var(--text-2);
    background: var(--bg-inset); padding: 1px 7px; border-radius: 999px;
  }

  .ev-list { display: flex; flex-direction: column; gap: 4px; }
  .ev-row {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    font-size: 0.77rem; color: var(--text-1); line-height: 1.4;
  }
  .sk-ico { width: 20px; height: 20px; border-radius: 4px; object-fit: cover; border: 1px solid var(--border-strong); }
  .ev-u { font-weight: 700; }
  .ev-u.enemy { color: var(--gold); }
  .ev-u.user { color: var(--cyan); }
  .ev-dmg { font-family: var(--font-mono); font-weight: 700; color: var(--text-0); }
  .ev-badge { font-size: 0.66rem; font-weight: 700; padding: 1px 7px; border-radius: 999px; border: 1px solid transparent; }
  .ev-note { font-size: 0.71rem; color: var(--text-2); }

  .hidden-sec { display: none !important; }
</style>
</head>
<body>
<div class="container">
  <header class="app-header">
    <div>
      <div class="brand-title">
        <span>Epic Seven World Arena (RTA)</span>
        <span class="brand-badge">Unmasked Replay Analyzer</span>
      </div>
      <div class="brand-sub">
        Exact 5-pick Snake Draft &amp; Bans, hidden Opponent builds &amp; Equipment Scores, collision-free Turn-0 Speed RNG Solver, and live 4v4 Battle HUD
      </div>
    </div>
    <div class="header-stats" id="headerPills"></div>
  </header>

  <div class="sticky-bar">
    <div class="match-tabs" id="matchTabs"></div>
    <div class="global-tools">
      <div class="seg-group" title="Switch between In-Battle Stats (with Memory Imprints) and raw Hero Sheet Stats">
        <button class="seg-btn active" id="btnStatBattle" onclick="setGlobalStatMode('battle')">⚔ In-Battle Stats</button>
        <button class="seg-btn" id="btnStatSheet" onclick="setGlobalStatMode('sheet')">📋 Hero Sheet</button>
      </div>
      <div class="search-wrap">
        <input type="text" class="search-input" id="heroSearch" placeholder="🔍 Filter hero, set, artifact..." oninput="onSearchInput(this.value)" />
        <button class="search-clear" id="searchClear" onclick="clearSearch()">✕</button>
      </div>
    </div>
  </div>

  <div id="matchesRoot"></div>
</div>

<script>
const DATA = __PAYLOAD_JSON__;
const TL_STATE = {};
let GLOBAL_STAT_MODE = 'battle'; // 'battle' | 'sheet'
let SELECTED_MATCH = -1;
let ACTIVE_TL_KEY = null;

const FRIB_ASSET = 'https://raw.githubusercontent.com/fribbels/Fribbels-Epic-7-Optimizer/main/app/assets/';
const E7_BUFF_BASE = 'https://raw.githubusercontent.com/CeciliaBot/E7Assets-Temp/main/assets/buff/';

const ELEM_ICON = {
  fire: FRIB_ASSET + 'elementfire.png',
  ice: FRIB_ASSET + 'elementice.png',
  wind: FRIB_ASSET + 'elementearth.png',
  earth: FRIB_ASSET + 'elementearth.png',
  light: FRIB_ASSET + 'elementlight.png',
  dark: FRIB_ASSET + 'elementdark.png',
};

const ROLE_ICON = {
  warrior: FRIB_ASSET + 'classwarrior.png',
  knight: FRIB_ASSET + 'classknight.png',
  assassin: FRIB_ASSET + 'classassassin.png',
  ranger: FRIB_ASSET + 'classranger.png',
  mage: FRIB_ASSET + 'classmage.png',
  manauser: FRIB_ASSET + 'classmanauser.png',
};

const STAT_ICON = {
  atk: FRIB_ASSET + 'statatk_dt.png',
  def: FRIB_ASSET + 'statdef_dt.png',
  hp: FRIB_ASSET + 'stathp_dt.png',
  spd: FRIB_ASSET + 'statspd_dt.png',
  cr: FRIB_ASSET + 'statcr_dt.png',
  cd: FRIB_ASSET + 'statcd_dt.png',
  eff: FRIB_ASSET + 'stateff_dt.png',
  res: FRIB_ASSET + 'statres_dt.png',
  dac: FRIB_ASSET + 'setunity.png',
};

const SET_ICON = {
  Speed: FRIB_ASSET + 'setspeed.png',
  Revenge: FRIB_ASSET + 'setrevenge.png',
  Reversal: FRIB_ASSET + 'setreversal.png',
  Weakening: FRIB_ASSET + 'setweakening.png',
  Attack: FRIB_ASSET + 'setattack.png',
  Destruction: FRIB_ASSET + 'setdestruction.png',
  Warfare: FRIB_ASSET + 'setwarfare.png',
  Counter: FRIB_ASSET + 'setcounter.png',
  Lifesteal: FRIB_ASSET + 'setlifesteal.png',
  Rage: FRIB_ASSET + 'setrage.png',
  Injury: FRIB_ASSET + 'setinjury.png',
  Protection: FRIB_ASSET + 'setprotection.png',
  Riposte: FRIB_ASSET + 'setriposte.png',
  Health: FRIB_ASSET + 'sethealth.png',
  Defense: FRIB_ASSET + 'setdefense.png',
  Torrent: FRIB_ASSET + 'settorrent.png',
  Critical: FRIB_ASSET + 'setcritical.png',
  Hit: FRIB_ASSET + 'sethit.png',
  Resist: FRIB_ASSET + 'setresist.png',
  Unity: FRIB_ASSET + 'setunity.png',
  Immunity: FRIB_ASSET + 'setimmunity.png',
  Penetration: FRIB_ASSET + 'setpenetration.png',
  Pursuit: FRIB_ASSET + 'setpursuit.png',
  Fervor: FRIB_ASSET + 'setfervor.png',
};

const SLOT_ICON = {
  Weapon: FRIB_ASSET + 'gearweapon.png',
  Helmet: FRIB_ASSET + 'gearhelmet.png',
  Armor: FRIB_ASSET + 'geararmor.png',
  Necklace: FRIB_ASSET + 'gearnecklace.png',
  Ring: FRIB_ASSET + 'gearring.png',
  Boots: FRIB_ASSET + 'gearboots.png',
};

const STAR_AWK_ICON = FRIB_ASSET + 'cm_icon_star_j.png';
const REFORGE_ICON = FRIB_ASSET + 'reforge_dt.png';

function buffIconUrl(name) {
  if (!name) return '';
  const n = name.toLowerCase();
  let f = '';
  if (n.includes('immunity')) f = 'stic_debuf_impossible.png';
  else if (n.includes('barrier')) f = 'stic_protect.png';
  else if (n.includes('greater') && n.includes('attack')) f = 'stic_att_up2.png';
  else if (n.includes('increase attack')) f = 'stic_att_up.png';
  else if (n.includes('decrease attack')) f = 'stic_att_dn.png';
  else if (n.includes('increase defense')) f = 'stic_def_up.png';
  else if (n.includes('decrease defense')) f = 'stic_def_dn.png';
  else if (n.includes('increase speed')) f = 'stic_speed_up.png';
  else if (n.includes('decrease speed')) f = 'stic_speed_dn.png';
  else if (n.includes('critical hit chance')) f = 'stic_cri_up.png';
  else if (n.includes('critical hit damage')) f = 'stic_cridmg_up.png';
  else if (n.includes('critical hit resistance') || n.includes('anti-crit')) f = 'stic_crires_up.png';
  else if (n.includes('counterattack')) f = 'stic_counter.png';
  else if (n.includes('stealth')) f = 'stic_hide.png';
  else if (n.includes('invincib')) f = 'stic_invincible.png';
  else if (n.includes('immortal')) f = 'stic_immortality.png';
  else if (n.includes('skill nullifier') || n.includes('escort')) f = 'stic_guard.png';
  else if (n.includes('evasion') || n.includes('archdemon')) f = 'stic_dodge_up.png';
  else if (n.includes('continuous healing')) f = 'stic_heal.png';
  else if (n.includes('vigor')) f = 'stic_con_up.png';
  else if (n.includes('stun')) f = 'stic_stun.png';
  else if (n.includes('sleep')) f = 'stic_sleep.png';
  else if (n.includes('silence')) f = 'stic_silence.png';
  else if (n.includes('redirected provoke')) f = 'stic_provoke_hp.png';
  else if (n.includes('provoke')) f = 'stic_provoke.png';
  else if (n.includes('unbuffable') || n.includes('unable to be buffed')) f = 'stic_buf_impossible.png';
  else if (n.includes('unhealable')) f = 'stic_heal_impossible.png';
  else if (n.includes('blind') || n.includes('hit chance')) f = 'stic_blind.png';
  else if (n.includes('bleed')) f = 'stic_blood.png';
  else if (n.includes('burn')) f = 'stic_blaze.png';
  else if (n.includes('bomb')) f = 'stic_bomb.png';
  else if (n.includes('poison') || n.includes('venom')) f = 'stic_venom.png';
  else if (n.includes('target')) f = 'stic_target.png';
  else if (n.includes('restrict')) f = 'stic_ab_up_block.png';
  else if (n.includes('seal')) f = 'stic_restrict.png';
  else if (n.includes('stigma')) f = 'stic_stigma.png';
  else if (n.includes('fear')) f = 'stic_fear.png';
  else if (n.includes('rupture')) f = 'stic_rupture.png';
  else if (n.includes('vampir')) f = 'stic_vampire.png';
  return f ? (E7_BUFF_BASE + f) : '';
}

function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function formatArtifactStats(arti) {
  if (!arti) return '';
  const parts = [];
  const atk = arti.flat_atk ?? Math.round((arti.stats && arti.stats.ATK) || 0);
  const def = arti.flat_def ?? Math.round((arti.stats && arti.stats.DEF) || 0);
  const hp  = arti.flat_hp  ?? Math.round((arti.stats && arti.stats.HP)  || 0);
  if (atk > 0) parts.push(`+${atk} ATK`);
  if (def > 0) parts.push(`+${def} DEF`);
  if (hp > 0)  parts.push(`+${hp} HP`);
  if (!parts.length && arti.stats) {
    for (const [k, v] of Object.entries(arti.stats)) {
      if (v > 0) parts.push(`+${Math.round(v)} ${k}`);
    }
  }
  return parts.join(' · ');
}

function renderUnitCard(u, side, cardId, matchKey) {
  const st = u.sheet_stats || {};
  const pi = u.post_imprint_stats || {};
  const ml = u.in_battle_ml_stats || {};
  const cp = u.combat_performance || {};
  const bs = u.battle_start || {};
  const unmaskedCls = side === 'enemy' ? 'unmasked' : '';
  const useBattle = GLOBAL_STAT_MODE === 'battle';

  const atkVal = useBattle ? (ml.atk || pi.atk || st.atk || 0) : (st.atk || 0);
  const defVal = useBattle ? (ml.def || pi.def || st.def || 0) : (st.def || 0);
  const hpVal  = useBattle ? (ml.hp  || pi.hp  || st.hp  || 0) : (st.hp  || 0);
  const spdVal = useBattle ? (pi.spd || st.spd || 0) : (st.spd || 0);
  const crVal  = useBattle ? Math.min(100, pi.cr_uncapped ?? st.cr ?? 15) : (st.cr ?? 15);
  const cdVal  = useBattle ? (ml.cd  || st.cd  || 150) : (st.cd || 150);
  const effVal = useBattle ? (ml.eff ?? pi.eff ?? st.eff ?? 0) : (st.eff ?? 0);
  const resVal = useBattle ? (ml.res ?? pi.res ?? st.res ?? 0) : (st.res ?? 0);
  const dacVal = st.dac ?? 3.0;

  const badgeDelta = (battleNum, sheetNum, isPct = false) => {
    const diff = battleNum - sheetNum;
    if (!useBattle || Math.abs(diff) < (isPct ? 0.4 : 3)) return '';
    const sign = diff > 0 ? '+' : '';
    const txt = isPct ? `${sign}${diff.toFixed(1)}%` : `${sign}${Math.round(diff)}`;
    return `<span class="stat-delta" title="Hero Sheet: ${isPct ? sheetNum.toFixed(1)+'%' : sheetNum.toLocaleString()} → In-Battle (Imprint / Artifact Passive): ${isPct ? battleNum.toFixed(1)+'%' : battleNum.toLocaleString()}">${txt}</span>`;
  };

  const crOver = (st.cr_uncapped > 100.0) ? `<span class="stat-overcap" title="Uncapped Crit Chance">${st.cr_uncapped.toFixed(0)}%</span>` : '';
  const cdOver = (st.cd_uncapped > 350.0) ? `<span class="stat-overcap" title="Uncapped Crit Damage">${st.cd_uncapped.toFixed(0)}%</span>` : '';

  const elemUrl = ELEM_ICON[(u.attribute || '').toLowerCase()] || '';
  const roleUrl = ROLE_ICON[(u.role || '').toLowerCase()] || '';
  const mvpBadge = cp.is_mvp ? `<span class="mvp-crown">👑 MVP</span>` : '';
  const molaArr = (u.molagora || [0, 0, 0]).map(x => `+${x}`).join('/');

  const awkPill = u.awakening < (u.stars || 6)
    ? `<span class="mini-pill warn" title="Under-awakened (${u.awakening}/${u.stars}★)">⚠ ${u.awakening}★</span>`
    : `<span class="mini-pill"><img src="${STAR_AWK_ICON}" alt="" onerror="this.style.display='none'"/>${u.awakening}★</span>`;

  const setsList = (u.sets && u.sets.length) ? u.sets : ['Broken'];
  const setsChips = setsList.map(s => {
    const ico = SET_ICON[s] ? `<img src="${SET_ICON[s]}" alt="" onerror="this.style.display='none'"/>` : '';
    return `<span class="set-badge">${ico}${esc(s)}</span>`;
  }).join('') + (u.broken_pieces >= 2 && u.sets && u.sets.length ? `<span class="set-badge">+Broken</span>` : '');

  const scoreBadge = u.is_template_build
    ? `<span class="set-badge tmpl-badge" title="Official Smilegate RTA Server Stat Template (${esc(u.template_id || 'template')})">🤖 Template</span>`
    : `<span class="set-badge es-badge" title="In-Game Equipment Score: ${u.total_equipment_score} ES (Avg ${u.avg_equipment_score || 0}/pc) · Substat WSS: ${u.total_substat_gs || 0}">${u.total_equipment_score} ES</span>`;

  const arti = u.artifact;
  let artiHtml = '';
  if (arti) {
    const aStats = formatArtifactStats(arti);
    const thumb = arti.icon
      ? `<img class="eq-thumb" src="${esc(arti.icon)}" alt="" loading="lazy" onerror="this.style.display='none'" />`
      : `<div class="eq-thumb-fallback">🃏</div>`;
    artiHtml = `<div class="equip-card">
      ${thumb}
      <div class="eq-info">
        <div class="eq-title-row">
          <span class="eq-name" title="${esc(arti.name)}">${esc(arti.name)}</span>
          <span class="eq-lvl">+${arti.level}</span>
        </div>
        <div class="eq-sub">${esc(aStats || `${arti.rarity || 5}★ Artifact`)}</div>
      </div>
    </div>`;
  } else {
    artiHtml = `<div class="equip-card" style="opacity:0.65"><div class="eq-thumb-fallback">—</div><div class="eq-info"><div class="eq-name" style="color:var(--text-2)">No Artifact Equipped</div></div></div>`;
  }

  const ee = u.exclusive_equipment;
  let eeHtml = '';
  if (ee) {
    const eeStatStr = (ee.value !== null && ee.value !== undefined)
      ? `+${ee.value}${ee.is_percent ? '%' : ''} ${esc(ee.stat)}`
      : esc(ee.stat);
    const eeIco = ee.icon ? `<img class="ee-ico" src="${esc(ee.icon)}" alt="" onerror="this.style.display='none'"/>` : '';
    eeHtml = `<details class="ee-details">
      <summary class="ee-summary" title="Click to toggle Exclusive Equipment skill effect">
        ${eeIco}
        <span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
          <strong style="color:var(--text-0)">EE #${ee.option}</strong> · <span style="color:var(--purple)">${eeStatStr}</span>
        </span>
        ${ee.description ? `<span style="color:var(--text-2); font-size:0.68rem;">▾</span>` : ''}
      </summary>
      ${ee.description ? `<div class="ee-desc-body">${esc(ee.description)}</div>` : ''}
    </details>`;
  }

  const impTxt = (u.imprint_summary && u.imprint_summary !== 'None') ? esc(u.imprint_summary) : 'None';
  const recvAura = (u.team_imprints_received && u.team_imprints_received.length)
    ? `<span class="aura-tag" title="Team Imprint Aura Received">✨ ${esc(u.team_imprints_received.join(', '))}</span>` : '';

  const tOrder = bs.global_turn1_order ? `#${bs.global_turn1_order}` : (bs.turn_order ? `#${bs.turn_order}` : '-');
  const crInit = (bs.initial_cr_pct !== null && bs.initial_cr_pct !== undefined) ? ` (${bs.initial_cr_pct.toFixed(0)}% CR)` : '';

  const gearItemsHtml = (u.gear || []).map(g => {
    const slIco = SLOT_ICON[g.slot] ? `<img src="${SLOT_ICON[g.slot]}" alt="" onerror="this.style.display='none'"/>` : '';
    const rfIco = g.reforged ? `<img src="${REFORGE_ICON}" alt="Reforged" title="Reforged i90" style="width:13px;height:13px;" onerror="this.style.display='none'"/>` : '';
    const setIco = SET_ICON[g.set] ? `<img src="${SET_ICON[g.set]}" alt="" style="width:14px;height:14px;" onerror="this.style.display='none'"/>` : '';
    const mv = g.main.is_percent ? `${g.main.value}%` : g.main.value;
    const subs = (g.substats || []).map(sb => {
      const sv = sb.is_percent ? `${sb.value}%` : sb.value;
      const mod = sb.modded ? `<span class="mod-gem" title="Modified Substat">[M]</span>` : '';
      return `<div>${esc(sb.label)} +${sv}${mod}</div>`;
    }).join('');
    return `<div class="gear-card">
      <div class="gc-head">
        <span class="gc-slot">${slIco}${esc(g.slot)} <span style="color:var(--text-2);font-weight:500;">i${g.ilvl} +${g.enhance}</span> ${rfIco}</span>
        <span class="gc-es" title="In-Game ES: ${g.equipment_score} · Fribbels WSS: ${g.substat_gs}">${g.equipment_score} ES</span>
      </div>
      <div class="gc-main" style="display:flex;align-items:center;gap:5px;">${setIco}<span>${esc(g.main.label)} +${mv}</span></div>
      <div class="gc-subs">${subs}</div>
    </div>`;
  }).join('');

  const gearFooter = u.is_template_build
    ? `<div class="gear-btn" style="cursor:default; opacity:0.75;"><span>🤖 Pre-baked RTA Server Template</span><span>${esc(u.template_id || '')}</span></div>`
    : `<button class="gear-btn" onclick="toggleGear('${cardId}')">
         <span>🔍 Inspect ${u.gear_count}/6 Gear Pieces</span>
         <span style="font-family:var(--font-mono);color:var(--text-2);">Avg ${u.avg_equipment_score || 0} ES ▾</span>
       </button>
       <div class="gear-drawer match-gear-${matchKey}" id="${cardId}">${gearItemsHtml}</div>`;

  const searchBlob = [u.name, u.attribute, u.role, ...(u.sets || []), arti ? arti.name : ''].join(' ').toLowerCase();

  return `<div class="unit-card" data-search="${esc(searchBlob)}">
    <div class="u-header">
      <div class="ava-box">
        <img class="ava-main" src="${esc(u.icon)}" alt="${esc(u.name)}" loading="lazy" onerror="this.style.opacity=0.2" />
        ${elemUrl ? `<img class="badge-elem" src="${elemUrl}" alt="${esc(u.attribute)}" title="${esc(u.attribute)}" onerror="this.style.display='none'"/>` : ''}
        ${roleUrl ? `<img class="badge-role" src="${roleUrl}" alt="${esc(u.role)}" title="${esc(u.role)}" onerror="this.style.display='none'"/>` : ''}
      </div>
      <div class="u-heading">
        <div class="u-name-line">
          <div class="u-name" title="${esc(u.name)}">${esc(u.name)}</div>
          ${mvpBadge}
        </div>
        <div class="u-meta-pills">
          <span class="mini-pill">${esc(u.pos_name)}</span>
          ${awkPill}
          <span class="mini-pill mola" title="Skills S1/S2/S3: ${molaArr}">+${u.molagora_total} Mola</span>
          ${u.pick_order ? `<span class="mini-pill">P#${u.pick_order}</span>` : ''}
          ${u.is_ban_protected ? `<span class="mini-pill" style="color:var(--cyan);border-color:rgba(56,189,248,0.4);">🛡 Prot</span>` : ''}
        </div>
      </div>
    </div>

    <div class="stat-grid">
      <div class="stat-row ${unmaskedCls}"><span class="stat-lbl"><img class="stat-ico" src="${STAT_ICON.atk}" alt=""/>Attack</span><span class="stat-val-wrap">${badgeDelta(atkVal, st.atk)}<span class="stat-val">${atkVal.toLocaleString()}</span></span></div>
      <div class="stat-row ${unmaskedCls}"><span class="stat-lbl"><img class="stat-ico" src="${STAT_ICON.def}" alt=""/>Defense</span><span class="stat-val-wrap">${badgeDelta(defVal, st.def)}<span class="stat-val">${defVal.toLocaleString()}</span></span></div>
      <div class="stat-row"><span class="stat-lbl"><img class="stat-ico" src="${STAT_ICON.hp}" alt=""/>Health</span><span class="stat-val-wrap">${badgeDelta(hpVal, st.hp)}<span class="stat-val">${hpVal.toLocaleString()}</span></span></div>
      <div class="stat-row ${unmaskedCls}"><span class="stat-lbl"><img class="stat-ico" src="${STAT_ICON.spd}" alt=""/>Speed</span><span class="stat-val-wrap">${badgeDelta(spdVal, st.spd)}<span class="stat-val">${spdVal}</span></span></div>
      <div class="stat-row ${unmaskedCls}"><span class="stat-lbl"><img class="stat-ico" src="${STAT_ICON.cr}" alt=""/>Crit Chance</span><span class="stat-val-wrap">${crOver}<span class="stat-val">${crVal.toFixed(1)}%</span></span></div>
      <div class="stat-row ${unmaskedCls}"><span class="stat-lbl"><img class="stat-ico" src="${STAT_ICON.cd}" alt=""/>Crit Damage</span><span class="stat-val-wrap">${cdOver}<span class="stat-val">${cdVal.toFixed(1)}%</span></span></div>
      <div class="stat-row ${unmaskedCls}"><span class="stat-lbl"><img class="stat-ico" src="${STAT_ICON.eff}" alt=""/>Effectiveness</span><span class="stat-val-wrap">${badgeDelta(effVal, st.eff, true)}<span class="stat-val">${effVal.toFixed(1)}%</span></span></div>
      <div class="stat-row ${unmaskedCls}"><span class="stat-lbl"><img class="stat-ico" src="${STAT_ICON.res}" alt=""/>Effect Resist</span><span class="stat-val-wrap">${badgeDelta(resVal, st.res, true)}<span class="stat-val">${resVal.toFixed(1)}%</span></span></div>
      <div class="stat-row ${unmaskedCls}"><span class="stat-lbl"><img class="stat-ico" src="${STAT_ICON.dac}" alt=""/>Dual Attack</span><span class="stat-val-wrap"><span class="stat-val">${dacVal.toFixed(1)}%</span></span></div>
    </div>

    <div class="sets-row">${setsChips}${scoreBadge}</div>
    ${artiHtml}
    ${eeHtml}

    <div class="imprint-bar">
      <span>Imprint: <strong>${impTxt}</strong></span>
      ${recvAura}
    </div>

    <div class="combat-foot">
      <span title="Turn-0 Starting Order & Combat Readiness">⏱ T0 ${tOrder}${crInit}</span>
      <span title="Damage Dealt / Tanked / Kills">🗡 ${(cp.damage_dealt || 0).toLocaleString()} · 🛡 ${(cp.damage_tanked || 0).toLocaleString()} · 💀 ${cp.kills || 0}</span>
    </div>

    ${gearFooter}
  </div>`;
}

function renderDraftBoard(m, key) {
  const dr = m.draft || {};
  const renderPre = (arr) => (arr && arr.length)
    ? arr.map(h => `<span class="preban-pill"><img src="${esc(h.icon)}" alt="" onerror="this.style.display='none'"/>🚫 ${esc(h.name)}</span>`).join('')
    : `<span style="font-size:0.75rem; color:var(--text-2);">None</span>`;

  const renderStrip = (picks) => (picks || []).map(p => {
    const cls = p.is_banned ? 'pick-tile is-banned' : (p.is_ban_protected ? 'pick-tile is-protected' : 'pick-tile');
    const gTxt = p.global_pick_order ? `<span class="g-num">#${p.global_pick_order}</span>` : '';
    const artiHtml = p.artifact
      ? `${p.artifact.icon ? `<img src="${esc(p.artifact.icon)}" alt="" onerror="this.style.display='none'"/>` : ''}<span>${esc(p.artifact.name)}</span>`
      : `<span>No Artifact</span>`;
    let badge = `<span class="pick-badge in">✓ ${esc(p.pos_name || 'Fielded')}</span>`;
    if (p.is_banned) badge = `<span class="pick-badge ban">✕ BANNED</span>`;
    else if (p.is_ban_protected) badge = `<span class="pick-badge prot">🛡 PROTECTED</span>`;

    return `<div class="${cls}" title="${esc(p.name)} — Lv.${p.level} (${p.awakening}★)${p.imprint_grade ? ' · Imprint ' + p.imprint_grade : ''}">
      <div class="pick-ord-row"><span>P${p.pick_slot}</span>${gTxt}</div>
      <div class="pick-ava-wrap">
        <img class="pick-ava" src="${esc(p.icon)}" alt="${esc(p.name)}" loading="lazy" onerror="this.style.opacity=0.2"/>
        ${p.is_banned ? `<div class="ban-slash">✕</div>` : ''}
      </div>
      <div class="pick-hero-name">${esc(p.name)}</div>
      <div class="pick-arti-row">${artiHtml}</div>
      ${badge}
    </div>`;
  }).join('');

  const snakeHtml = (dr.snake_draft_order && dr.snake_draft_order.length)
    ? `<div class="snake-flow">
         <span style="color:var(--text-2); font-weight:700; margin-right:4px;">🐍 Snake Order:</span>
         ${dr.snake_draft_order.map(p =>
           `<span class="snake-pill ${p.side} ${p.is_banned ? 'banned' : ''}">#${p.global_pick_order} ${esc(p.name)}${p.is_ban_protected ? ' 🛡' : ''}${p.is_banned ? ' ✕' : ''}</span>`
         ).join('<span style="color:var(--text-2)">→</span>')}
       </div>`
    : '';

  const fpLabel = dr.first_pick_side === 'user'
    ? `<span class="rchip tone-cyan" style="cursor:default">🥇 First Pick: ${esc(m.user.name)}</span>`
    : (dr.first_pick_side === 'enemy' ? `<span class="rchip tone-gold" style="cursor:default">🥇 First Pick: ${esc(m.enemy.name)}</span>` : '');

  return `<div class="draft-section" id="sec_draft_${key}">
    <div class="sec-hdr">
      <div class="sec-title">🎯 Draft &amp; Ban Phase (Pre-Bans · 5v5 Snake Picks · Ban Protection · Post-Bans)</div>
      <div>${fpLabel}</div>
    </div>
    <div class="preban-grid">
      <div class="preban-card"><span class="preban-lbl">Opponent Pre-Bans:</span>${renderPre(dr.enemy_prebans)}</div>
      <div class="preban-card"><span class="preban-lbl">User Pre-Bans:</span>${renderPre(dr.user_prebans)}</div>
    </div>
    <div class="draft-cols">
      <div class="draft-team-box enemy">
        <div class="draft-team-hdr">
          <span>🔓 Opponent 5 Picks (${esc(m.enemy.name)})</span>
          <span>${m.enemy.is_first_pick ? '1st Pick Side' : '2nd Pick Side'}</span>
        </div>
        <div class="picks-strip">${renderStrip(dr.enemy_picks)}</div>
      </div>
      <div class="draft-team-box user">
        <div class="draft-team-hdr">
          <span>⚔ User 5 Picks (${esc(m.user.name)})</span>
          <span>${m.user.is_first_pick ? '1st Pick Side' : '2nd Pick Side'}</span>
        </div>
        <div class="picks-strip">${renderStrip(dr.user_picks)}</div>
      </div>
    </div>
    ${snakeHtml}
  </div>`;
}

function renderOpeningCrSolver(oc, key) {
  if (!oc || !oc.available || !oc.units || !oc.units.length) return '';

  const alertsHtml = (oc.alerts || []).map(a => `<div class="cr-alert">${esc(a)}</div>`).join('');

  // Multi-lane collision-free staggering for up to 8 units!
  const sorted = [...oc.units].sort((a, b) => (a.global_order || 99) - (b.global_order || 99));
  const lanes = []; // each lane holds array of occupied pct positions
  const MIN_GAP_PCT = 12.0;

  const pinItems = sorted.map(u => {
    const pct = Math.max(0, Math.min(100, u.actual_cr_pct));
    let laneIdx = 0;
    while (laneIdx < lanes.length) {
      const clashes = lanes[laneIdx].some(prevPct => Math.abs(prevPct - pct) < MIN_GAP_PCT);
      if (!clashes) break;
      laneIdx++;
    }
    if (!lanes[laneIdx]) lanes[laneIdx] = [];
    lanes[laneIdx].push(pct);
    return { u, pct, laneIdx };
  });

  const numLanes = Math.max(1, lanes.length);
  const stageHeight = 52 + numLanes * 38;
  const barBottom = 22;

  const pinsAndStems = pinItems.map(({ u, pct, laneIdx }) => {
    const topPx = 10 + laneIdx * 36;
    const stemTop = topPx + 30;
    const stemHeight = Math.max(0, stageHeight - barBottom - stemTop);
    const leftCalc = `calc(36px + (100% - 72px) * ${(pct / 100).toFixed(4)})`;
    const winCls = u.is_turn1_winner ? 'winner' : '';
    const infoText = `#${u.global_order} ${u.name} (${u.side === 'enemy' ? 'Opponent' : 'User'}) · ${u.spd} SPD · Actual Turn-0 CR: ${u.actual_cr_pct.toFixed(2)}% (Base ${u.base_cr_pct.toFixed(2)}%, Solved RNG Roll: +${u.rng_roll_min_pct.toFixed(2)}%..+${u.rng_roll_max_pct.toFixed(2)}%)`;

    return `
      <div class="cr-stem" style="left:${leftCalc}; top:${stemTop}px; height:${stemHeight}px;"></div>
      <div class="cr-pin ${u.side} ${winCls}" id="crpin_${key}_${u.uid}"
           style="left:${leftCalc}; top:${topPx}px;"
           onmouseenter="hlCrUnit('${key}', ${u.uid}, ${ JSON.stringify(infoText).replace(/"/g, '&quot;') })"
           onmouseleave="hlCrUnit('${key}', null, '')"
           title="${esc(infoText)}">
        <img src="${esc(u.icon)}" alt="${esc(u.name)}" onerror="this.style.opacity=0.2" />
        <span class="cr-pin-txt">#${u.global_order} · ${u.actual_cr_pct.toFixed(0)}%</span>
      </div>`;
  }).join('');

  const ticksHtml = [0, 25, 50, 75, 100].map(t => {
    const leftCalc = `calc(36px + (100% - 72px) * ${(t / 100).toFixed(2)})`;
    return `<span class="cr-tick" style="left:${leftCalc}">${t}%</span>`;
  }).join('');

  const rowsHtml = sorted.map(u => {
    const sideColor = u.side === 'enemy' ? 'var(--gold)' : 'var(--cyan)';
    const spdExtra = u.spd !== u.sheet_spd ? ` <span style="color:var(--green);font-size:0.68rem;">(Sheet ${u.sheet_spd})</span>` : '';
    const rngRange = u.rng_roll_min_pct === u.rng_roll_max_pct
      ? `+${u.rng_roll_min_pct.toFixed(2)}%`
      : `+${u.rng_roll_min_pct.toFixed(1)}% .. +${u.rng_roll_max_pct.toFixed(1)}%`;
    const pushBadge = u.pre_push_pct
      ? ` <span class="ev-badge tone-cyan">${u.pre_push_pct > 0 ? '+' : ''}${u.pre_push_pct}% Push</span>` : '';
    const blindRange = u.is_turn1_winner
      ? `${u.spd} (Ref)`
      : `${u.e7calc_blind_spd_range[0]}–${u.e7calc_blind_spd_range[1]}`;
    const infoText = `#${u.global_order} ${u.name} · ${u.spd} SPD · Turn-0 CR: ${u.actual_cr_pct.toFixed(2)}% · RNG Roll: ${rngRange}`;

    return `<tr id="crrow_${key}_${u.uid}"
                onmouseenter="hlCrUnit('${key}', ${u.uid}, ${ JSON.stringify(infoText).replace(/"/g, '&quot;') })"
                onmouseleave="hlCrUnit('${key}', null, '')">
      <td><strong>#${u.global_order}</strong></td>
      <td class="cr-hero-cell">
        <img src="${esc(u.icon)}" alt="" onerror="this.style.opacity=0.2" />
        <span style="color:${sideColor}">${esc(u.name)}</span>
      </td>
      <td><strong>${u.spd}</strong>${spdExtra}</td>
      <td>${u.base_cr_pct.toFixed(1)}%</td>
      <td><strong style="color:${u.is_turn1_winner ? 'var(--green)' : 'var(--text-0)'}">${u.actual_cr_pct.toFixed(1)}%</strong>${pushBadge}</td>
      <td style="color:var(--purple)">${rngRange}</td>
      <td style="color:var(--text-2)">${blindRange}</td>
    </tr>`;
  }).join('');

  return `<div class="cr-box">
    <div class="sec-hdr" style="margin-bottom:8px;">
      <div class="sec-title">🏁 Opening Turn-0 Combat Readiness Race Track &amp; Speed RNG Solver</div>
      <div style="font-size:0.77rem; color:var(--text-2);">Turn-1 Opener: <strong style="color:var(--green)">${esc(oc.turn1_winner_name)} (${oc.turn1_winner_spd} SPD)</strong></div>
    </div>
    ${alertsHtml ? `<div class="cr-alert-list">${alertsHtml}</div>` : ''}
    <div class="cr-hover-readout" id="crinfo_${key}">
      <span style="color:var(--text-2)">Hover any hero pin on the Combat Readiness track (or table row) to inspect exact Speed RNG roll</span>
      <span style="font-family:var(--font-mono); font-size:0.71rem; color:var(--text-2);">e7calc.xyz 0%–5% CR RNG Physics</span>
    </div>
    <div class="cr-track-stage" style="height:${stageHeight}px;">
      <div class="cr-axis-bar"></div>
      ${ticksHtml}
      ${pinsAndStems}
    </div>
    <details class="cr-table-details" open>
      <summary>📊 Detailed 8-Unit Speed RNG &amp; Blind Speed Bounds Table</summary>
      <div style="overflow-x:auto;">
        <table class="cr-table">
          <thead>
            <tr>
              <th>Order</th>
              <th>Hero (4v4)</th>
              <th>In-Battle SPD</th>
              <th>Pure SPD Ratio</th>
              <th>Actual Turn-0 CR%</th>
              <th>Solved 0–5% RNG Roll</th>
              <th>e7calc Blind SPD Bounds</th>
            </tr>
          </thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
    </details>
  </div>`;
}

function hlCrUnit(key, uid, text) {
  document.querySelectorAll(`[id^="crpin_${key}_"], [id^="crrow_${key}_"]`).forEach(el => el.classList.remove('hl'));
  const infoEl = document.getElementById(`crinfo_${key}`);
  if (uid !== null && uid !== undefined) {
    const pin = document.getElementById(`crpin_${key}_${uid}`);
    const row = document.getElementById(`crrow_${key}_${uid}`);
    if (pin) pin.classList.add('hl');
    if (row) row.classList.add('hl');
    if (infoEl && text) infoEl.innerHTML = `<strong style="color:var(--cyan)">${esc(text)}</strong>`;
  } else if (infoEl) {
    infoEl.innerHTML = `<span style="color:var(--text-2)">Hover any hero pin on the Combat Readiness track (or table row) to inspect exact Speed RNG roll</span><span style="font-family:var(--font-mono); font-size:0.71rem; color:var(--text-2);">e7calc.xyz 0%–5% CR RNG Physics</span>`;
  }
}

function renderSoulMeter(souls) {
  const s = Math.max(0, Number(souls || 0));
  const sbCount = Math.floor(s / 10);
  const pips = [1, 2].map(idx => `<span class="soul-pip ${sbCount >= idx ? 'lit' : ''}" title="${idx * 10} Souls = Soulburn"></span>`).join('');
  return `<span class="soul-meter" title="${s} Souls (${sbCount} Soulburn${sbCount === 1 ? '' : 's'} available)">🔥 ${s} Souls ${pips}</span>`;
}

function renderHudUnit(u, activeUid, key, heroFilterUid) {
  const isAct = u.uid === activeUid && !u.dead;
  const isFlt = heroFilterUid === u.uid;
  const maxHp = Math.max(1, u.max_hp || 1);
  const injPct = Math.min(50, Math.max(0, ((u.dhp || 0) / maxHp) * 100));
  const hpPct = Math.min(100 - injPct, Math.max(0, ((u.hp || 0) / maxHp) * 100));
  const shPct = Math.min(35, Math.max(0, ((u.barrier || 0) / maxHp) * 100));

  const effs = (u.effects || []).map(ef => {
    const bUrl = buffIconUrl(ef.name);
    const ico = bUrl ? `<img src="${bUrl}" alt="" onerror="this.style.display='none'"/>` : '';
    return `<span class="eff-chip ${esc(ef.kind)}" title="${esc(ef.name)}">${ico}${esc(ef.name)}</span>`;
  }).join('');

  return `<div class="hud-tile ${isAct ? 'acting' : ''} ${isFlt ? 'hero-flt' : ''} ${u.dead ? 'dead' : ''}"
               onclick="toggleHeroFilter('${key}', ${u.uid})"
               title="Click to filter timeline turns involving ${esc(u.name)}">
    <div class="ht-top">
      <img src="${esc(u.icon)}" alt="" onerror="this.style.opacity=0.2" />
      <div class="ht-name">${esc(u.name)}</div>
      <div class="ht-cr">${u.dead ? '💀 KO' : `${(u.cr_pct || 0).toFixed(0)}%`}</div>
    </div>
    <div class="hp-track">
      <div class="hp-bar-fill" style="width:${hpPct.toFixed(1)}%"></div>
      <div class="sh-bar-fill" style="width:${shPct.toFixed(1)}%"></div>
      <div class="inj-bar-fill" style="width:${injPct.toFixed(1)}%"></div>
    </div>
    <div class="ht-nums">
      <span>${(u.hp || 0).toLocaleString()} / ${maxHp.toLocaleString()}</span>
      <span>${u.barrier > 0 ? `🛡+${u.barrier.toLocaleString()}` : (u.dhp > 0 ? `🩸-${u.dhp.toLocaleString()}` : '')}</span>
    </div>
    <div class="cr-mini-track"><div style="width:${Math.min(100, Math.max(0, u.cr_pct || 0)).toFixed(1)}%"></div></div>
    <div class="ht-effs">${effs}</div>
  </div>`;
}

function renderRichEvent(ev) {
  const badges = (ev.badges || []).map(b => `<span class="ev-badge tone-${esc(b.tone || 'cyan')}">${esc(b.text)}</span>`).join('');
  if (ev.kind === 'skill') {
    const skIco = ev.skill_icon ? `<img class="sk-ico" src="${esc(ev.skill_icon)}" alt="" onerror="this.style.display='none'"/>` : '';
    const tgt = ev.target_name ? ` → <span class="ev-u ${ev.target_side}">${esc(ev.target_name)}</span>` : '';
    return `<div class="ev-row">
      ${skIco}
      <span class="ev-u ${ev.actor_side}">${esc(ev.actor_name)}</span>
      <span>used <strong style="color:var(--text-0)">${esc(ev.skill_label)}</strong>${tgt}</span>
      ${badges}
    </div>`;
  }
  if (ev.kind === 'hit') {
    const hits = ev.hits > 1 ? ` <span class="ev-note">[${ev.hits} hits]</span>` : '';
    const det = ev.detail ? `<span class="ev-note">— ${esc(ev.detail)}</span>` : '';
    return `<div class="ev-row">
      <span class="ev-u ${ev.actor_side}">${esc(ev.actor_name)}</span>
      <span>→</span>
      <span class="ev-u ${ev.target_side}">${esc(ev.target_name)}:</span>
      <span class="ev-dmg">${(ev.damage || 0).toLocaleString()} dmg</span>${hits}
      ${badges} ${det}
    </div>`;
  }
  if (ev.kind === 'resist' || ev.kind === 'immune') {
    return `<div class="ev-row">
      <span class="ev-u ${ev.target_side}">${esc(ev.target_name)}</span>
      ${badges}
      <span class="ev-note">${esc(ev.detail || '')}</span>
    </div>`;
  }
  if (ev.kind === 'buff' || ev.kind === 'debuff' || ev.kind === 'cr_push' || ev.kind === 'heal' || ev.kind === 'death' || ev.kind === 'revive') {
    return `<div class="ev-row">
      <span class="ev-u ${ev.target_side}">${esc(ev.target_name)}</span>
      ${badges}
    </div>`;
  }
  if (ev.kind === 'passive' || ev.kind === 'extra_turn' || ev.kind === 'skip_turn') {
    return `<div class="ev-row">
      <span class="ev-u ${ev.actor_side}">${esc(ev.actor_name)}</span>
      ${badges}
      ${ev.detail ? `<span class="ev-note">${esc(ev.detail)}</span>` : ''}
    </div>`;
  }
  return `<div class="ev-row">${badges}<span>${esc(ev.text || '')}</span></div>`;
}

function turnInvolvesHero(t, uid) {
  if (!uid) return true;
  if (t.actor_uid === uid) return true;
  return (t.rich_events || []).some(ev => ev.actor_uid === uid || ev.target_uid === uid);
}

function updateTimelineView(key) {
  const st = TL_STATE[key];
  if (!st) return;
  ACTIVE_TL_KEY = key;
  const tl = st.match.timeline || [];
  const mount = document.getElementById(`tl_dyn_${key}`);
  if (!mount || !tl.length) return;

  const idx = Math.max(0, Math.min(tl.length - 1, st.turnIdx));
  st.turnIdx = idx;
  const cur = tl[idx];
  const snap = (st.showEnd && cur.end_snapshot) ? cur.end_snapshot : (cur.start_snapshot || { units: [], user_souls: 0, enemy_souls: 0, frenzy_stage: 0 });
  const activeUid = st.showEnd ? null : cur.actor_uid;

  const enemyUnits = (snap.units || []).filter(u => u.side === 'enemy');
  const userUnits = (snap.units || []).filter(u => u.side === 'user');
  const frenzyNum = snap.frenzy_stage ?? cur.frenzy_stage ?? 0;

  const stateTitle = st.showEnd
    ? `🏁 Match End (After Turn ${cur.turn})`
    : (cur.turn === 0 ? `⚡ Turn 0 — Battle Start` : `Turn ${cur.turn} — ${cur.actor_name}${cur.is_skipped ? ' (Skipped)' : ''}`);

  const heroFltUnit = st.heroUid ? (snap.units || []).find(u => u.uid === st.heroUid) : null;
  const heroFltChip = heroFltUnit
    ? `<button class="flt-pill active" onclick="toggleHeroFilter('${key}', ${st.heroUid})">🎯 Hero: ${esc(heroFltUnit.name)} ✕</button>`
    : '';

  const filters = [
    ['all', 'All Turns'],
    ['resist15', '🎲 15% Resists'],
    ['resist', '🛡 Resists & Immunity'],
    ['evade', '💨 Evades / Misses'],
    ['cr', '⚡ CR Pushes'],
    ['passive', '✨ Passives & Counters'],
    ['kill', '💀 Kills & Revives'],
  ];

  const matchingTurns = tl
    .map((t, i) => ({ t, i }))
    .filter(({ t }) => (st.filter === 'all' || (t.tags || []).includes(st.filter)) && turnInvolvesHero(t, st.heroUid));

  const feedHtml = matchingTurns.length
    ? matchingTurns.map(({ t, i }) => {
        const isSel = !st.showEnd && i === idx;
        const ava = t.actor_icon ? `<img src="${esc(t.actor_icon)}" alt="" onerror="this.style.display='none'"/>` : '';
        const clk = t.decision_clock
          ? `<span class="clock-tag">⏱ ${t.decision_clock.think_sec}s (${t.decision_clock.remaining_sec}s left)</span>`
          : '';
        const evRows = (t.rich_events && t.rich_events.length)
          ? t.rich_events.map(renderRichEvent).join('')
          : `<div class="ev-note">Turn started</div>`;
        return `<div class="t-card side-${t.actor_side || 'neutral'} ${isSel ? 'selected' : ''}" id="tcard_${key}_${i}" onclick="selectTurn('${key}', ${i})">
          <div class="t-card-hdr">
            <div class="t-actor">
              <span class="t-num">${t.turn === 0 ? 'T0' : `T${t.turn}`}</span>
              ${ava}
              <span>${esc(t.actor_name)}</span>
            </div>
            ${clk}
          </div>
          <div class="ev-list">${evRows}</div>
        </div>`;
      }).join('')
    : `<div style="padding:22px; text-align:center; color:var(--text-2); background:var(--bg-inset); border-radius:8px;">No turns matched the active filter.</div>`;

  mount.innerHTML = `<div class="battle-Theater">
    <div class="hud-sticky">
      <div class="hud-hdr">
        <div class="hud-title">${esc(stateTitle)}</div>
        <span class="meta-tag frenzy">🔥 Frenzy ${frenzyNum}</span>
      </div>
      <div class="hud-hint">💡 Click any hero tile below to filter turns involving them · Use <strong>← / →</strong> keys to step</div>

      <div class="hud-side-block">
        <div class="hud-side-top enemy">
          <span>🔓 Opponent (${esc(st.match.enemy.name)})</span>
          ${renderSoulMeter(snap.enemy_souls)}
        </div>
        <div class="hud-grid-2x2">${enemyUnits.map(u => renderHudUnit(u, activeUid, key, st.heroUid)).join('')}</div>
      </div>

      <div class="hud-side-block">
        <div class="hud-side-top user">
          <span>⚔ User (${esc(st.match.user.name)})</span>
          ${renderSoulMeter(snap.user_souls)}
        </div>
        <div class="hud-grid-2x2">${userUnits.map(u => renderHudUnit(u, activeUid, key, st.heroUid)).join('')}</div>
      </div>
    </div>

    <div class="feed-panel">
      <div class="playback-bar">
        <div class="pb-btns">
          <button class="pb-btn" onclick="stepTurn('${key}', 'first')" ${idx === 0 && !st.showEnd ? 'disabled' : ''}>⏮</button>
          <button class="pb-btn" onclick="stepTurn('${key}', 'prev')" ${idx === 0 && !st.showEnd ? 'disabled' : ''}>◀ Prev</button>
          <button class="pb-btn ${st.timer ? 'playing' : ''}" onclick="toggleAutoPlay('${key}')">${st.timer ? '⏸ Pause' : '▶ Auto-Play'}</button>
          <button class="pb-btn" onclick="stepTurn('${key}', 'next')" ${st.showEnd || (idx === tl.length - 1 && !tl[tl.length - 1].end_snapshot) ? 'disabled' : ''}>Next ▶</button>
          <button class="pb-btn" onclick="stepTurn('${key}', 'last')" ${st.showEnd ? 'disabled' : ''}>⏭ End</button>
          <span class="pb-counter">${st.showEnd ? 'FINAL' : `Step ${idx + 1} / ${tl.length}`}</span>
        </div>
      </div>

      <div class="flt-pills">
        ${filters.map(([fid, flbl]) => `<button class="flt-pill ${st.filter === fid ? 'active' : ''}" onclick="setTlFilter('${key}', '${fid}')">${flbl}</button>`).join('')}
        ${heroFltChip}
      </div>

      <div class="turn-feed-scroll" id="feed_${key}">${feedHtml}</div>
    </div>
  </div>`;
}

function setMatchSubView(key, view, jumpFilter) {
  const st = TL_STATE[key];
  if (!st) return;
  st.view = view; // 'overview' | 'timeline' | 'all'
  ['overview', 'timeline', 'all'].forEach(v => {
    const b = document.getElementById(`subbtn_${key}_${v}`);
    if (b) b.classList.toggle('active', v === view);
  });
  const draftEl = document.getElementById(`sec_draft_wrap_${key}`) || document.getElementById(`sec_draft_${key}`);
  const buildsEl = document.getElementById(`sec_builds_${key}`);
  const tlEl = document.getElementById(`sec_tl_${key}`);

  if (draftEl) draftEl.classList.toggle('hidden-sec', view === 'timeline');
  if (buildsEl) buildsEl.classList.toggle('hidden-sec', view === 'timeline');
  if (tlEl) tlEl.classList.toggle('hidden-sec', view === 'overview');

  if (view === 'timeline' || view === 'all') {
    ACTIVE_TL_KEY = key;
    if (jumpFilter) {
      st.filter = jumpFilter;
      const firstMatch = (st.match.timeline || []).findIndex(t => jumpFilter === 'all' || (t.tags || []).includes(jumpFilter));
      if (firstMatch >= 0) { st.turnIdx = firstMatch; st.showEnd = false; }
    }
    updateTimelineView(key);
  }
}

function stepTurn(key, dir) {
  const st = TL_STATE[key];
  if (!st) return;
  const tl = st.match.timeline || [];
  if (!tl.length) return;
  if (dir === 'first') { st.turnIdx = 0; st.showEnd = false; }
  else if (dir === 'prev') {
    if (st.showEnd) st.showEnd = false;
    else st.turnIdx = Math.max(0, st.turnIdx - 1);
  } else if (dir === 'next') {
    if (st.turnIdx < tl.length - 1) st.turnIdx += 1;
    else if (tl[tl.length - 1].end_snapshot) st.showEnd = true;
  } else if (dir === 'last') {
    st.turnIdx = tl.length - 1;
    st.showEnd = Boolean(tl[tl.length - 1].end_snapshot);
  }
  updateTimelineView(key);
  const activeCard = document.getElementById(`tcard_${key}_${st.turnIdx}`);
  if (activeCard) activeCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function toggleAutoPlay(key) {
  const st = TL_STATE[key];
  if (!st) return;
  if (st.timer) {
    clearInterval(st.timer);
    st.timer = null;
    updateTimelineView(key);
    return;
  }
  if (st.showEnd) { st.turnIdx = 0; st.showEnd = false; }
  st.timer = setInterval(() => {
    const tl = st.match.timeline || [];
    if (st.turnIdx < tl.length - 1) {
      stepTurn(key, 'next');
    } else if (!st.showEnd && tl[tl.length - 1]?.end_snapshot) {
      stepTurn(key, 'next');
    } else {
      clearInterval(st.timer);
      st.timer = null;
      updateTimelineView(key);
    }
  }, 1250);
  updateTimelineView(key);
}

function selectTurn(key, i) {
  const st = TL_STATE[key];
  if (!st) return;
  st.turnIdx = i;
  st.showEnd = false;
  updateTimelineView(key);
}

function setTlFilter(key, fid) {
  const st = TL_STATE[key];
  if (!st) return;
  st.filter = fid;
  const firstMatch = (st.match.timeline || []).findIndex(t => fid === 'all' || (t.tags || []).includes(fid));
  if (firstMatch >= 0) { st.turnIdx = firstMatch; st.showEnd = false; }
  updateTimelineView(key);
}

function toggleHeroFilter(key, uid) {
  const st = TL_STATE[key];
  if (!st) return;
  st.heroUid = (st.heroUid === uid) ? null : uid;
  updateTimelineView(key);
}

function toggleGear(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('open');
}

function toggleAllGear(matchKey, btn) {
  const drawers = document.querySelectorAll(`.match-gear-${matchKey}`);
  const anyClosed = Array.from(drawers).some(d => !d.classList.contains('open'));
  drawers.forEach(d => d.classList.toggle('open', anyClosed));
  btn.textContent = anyClosed ? '🔼 Collapse All Gear' : '🔽 Expand All Gear';
}

function setGlobalStatMode(mode) {
  GLOBAL_STAT_MODE = mode;
  document.getElementById('btnStatBattle').classList.toggle('active', mode === 'battle');
  document.getElementById('btnStatSheet').classList.toggle('active', mode === 'sheet');
  // Preserve current sub-views & timeline positions on re-render
  const prevViews = {};
  for (const [k, v] of Object.entries(TL_STATE)) {
    if (v.timer) clearInterval(v.timer);
    prevViews[k] = { view: v.view, turnIdx: v.turnIdx, filter: v.filter, heroUid: v.heroUid, showEnd: v.showEnd };
  }
  renderApp(SELECTED_MATCH, prevViews);
  const q = document.getElementById('heroSearch').value;
  if (q) onSearchInput(q);
}

function onSearchInput(q) {
  const query = (q || '').trim().toLowerCase();
  document.getElementById('searchClear').style.display = query ? 'block' : 'none';
  document.querySelectorAll('.unit-card').forEach(card => {
    if (!query) {
      card.classList.remove('search-hit', 'search-dim');
      return;
    }
    const hay = card.getAttribute('data-search') || '';
    const hit = hay.includes(query);
    card.classList.toggle('search-hit', hit);
    card.classList.toggle('search-dim', !hit);
  });
}

function clearSearch() {
  const inp = document.getElementById('heroSearch');
  inp.value = '';
  onSearchInput('');
}

document.addEventListener('keydown', (e) => {
  if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
  if (!ACTIVE_TL_KEY || !TL_STATE[ACTIVE_TL_KEY]) return;
  const st = TL_STATE[ACTIVE_TL_KEY];
  if (st.view === 'overview') return;
  if (e.key === 'ArrowRight') { e.preventDefault(); stepTurn(ACTIVE_TL_KEY, 'next'); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); stepTurn(ACTIVE_TL_KEY, 'prev'); }
});

function copyRtaSummary(mIdx, btn) {
  const m = DATA.matches[mIdx];
  const dr = m.draft || {};
  const lines = [`**World Arena (RTA): ${m.user.name} [${m.user.server}] vs ${m.enemy.name} [${m.enemy.server}]** (${m.user_result} — ${m.win_reason})`];
  const uPre = (dr.user_prebans || []).map(x => x.name).join(', ') || 'None';
  const ePre = (dr.enemy_prebans || []).map(x => x.name).join(', ') || 'None';
  lines.push(`Pre-Bans: User [${uPre}] | Opp [${ePre}]`);
  lines.push(`Opponent Draft: ` + (dr.enemy_picks || []).map(p => `${p.name}${p.is_banned ? ' (BANNED)' : ''}`).join(' -> '));
  lines.push(`User Draft: ` + (dr.user_picks || []).map(p => `${p.name}${p.is_banned ? ' (BANNED)' : ''}`).join(' -> '));
  lines.push(`\n**[Opponent Unmasked Builds — ${m.enemy.name}]**`);
  (m.enemy.units || []).forEach(u => {
    const st = u.sheet_stats;
    const pi = u.post_imprint_stats || {};
    const sets = (u.sets && u.sets.length ? u.sets.join('/') : 'Broken');
    const arti = u.artifact ? `${u.artifact.name} +${u.artifact.level}` : 'No Arti';
    const spdStr = (pi.spd && pi.spd !== st.spd) ? `${st.spd} (${pi.spd})` : `${st.spd}`;
    lines.push(`- **${u.name}** (${sets} | ${arti}): ${spdStr} SPD | ${(pi.hp || st.hp).toLocaleString()} HP | ${(pi.def || st.def).toLocaleString()} DEF | ${(pi.atk || st.atk).toLocaleString()} ATK | ${Math.min(100, pi.cr_uncapped ?? st.cr).toFixed(0)}% CC / ${st.cd.toFixed(0)}% CD | ${(pi.eff ?? st.eff).toFixed(0)}% EFF / ${(pi.res ?? st.res).toFixed(0)}% ER`);
  });
  navigator.clipboard.writeText(lines.join('\n')).then(() => {
    const old = btn.textContent;
    btn.textContent = '✅ Copied!';
    setTimeout(() => { btn.textContent = old; }, 1800);
  });
}

function renderPlayerCard(prof) {
  const repIcon = (prof.rep_hero && prof.rep_hero.icon) ? prof.rep_hero.icon : (prof.units[0] ? prof.units[0].icon : '');
  const fpBadge = prof.is_first_pick
    ? `<span class="meta-tag" style="color:var(--cyan);border-color:rgba(56,189,248,0.35);">🥇 1st Pick</span>`
    : `<span class="meta-tag">🥈 2nd Pick</span>`;
  const botBadge = prof.is_bot ? `<span class="meta-tag frenzy">🤖 Bot (${esc(prof.npc_id || 'AI')})</span>` : '';
  const scoreTxt = (prof.old_score !== null && prof.old_score !== undefined && prof.new_score !== null && prof.new_score !== undefined)
    ? `Points: <strong>${prof.old_score} → ${prof.new_score}</strong> (${prof.score_delta >= 0 ? '+' : ''}${prof.score_delta})`
    : '';

  return `<div class="player-card ${prof.side}">
    <img class="p-ava" src="${esc(repIcon)}" alt="" onerror="this.style.opacity=0.2" />
    <div class="p-body">
      <div class="p-row1">
        <span class="p-name">${esc(prof.name)}</span>
        <span class="meta-tag">${esc(prof.server)}</span>
        <span class="meta-tag">${esc(prof.league)}</span>
        ${fpBadge} ${botBadge}
      </div>
      <div class="p-row2">
        <span>Lv.${prof.level || '?'}</span>
        ${prof.clan_name ? `<span>🏰 Guild: <strong>${esc(prof.clan_name)}</strong></span>` : ''}
        ${scoreTxt ? `<span>🏆 ${scoreTxt}</span>` : ''}
        ${prof.ping_ms ? `<span>📡 ${prof.ping_ms}ms</span>` : ''}
      </div>
    </div>
  </div>`;
}

function renderApp(selectedMatchIdx = -1, preserved = {}) {
  SELECTED_MATCH = selectedMatchIdx;
  document.getElementById('headerPills').innerHTML = `
    <span class="stat-pill">Matches: <strong>${DATA.total_matches}</strong></span>
    <span class="stat-pill" style="color:#fde68a">Gold Stats = Unmasked Opponent</span>
    <span class="stat-pill">Generated: <strong>${esc(DATA.generated_at_utc)}</strong></span>
  `;

  const tabs = document.getElementById('matchTabs');
  let tHtml = `<button class="tab-btn ${selectedMatchIdx === -1 ? 'active' : ''}" onclick="renderApp(-1)">All Matches (${DATA.matches.length})</button>`;
  DATA.matches.forEach((m, idx) => {
    tHtml += `<button class="tab-btn ${selectedMatchIdx === idx ? 'active' : ''}" onclick="renderApp(${idx})">Match #${m.match_index}: vs ${esc(m.enemy.name)}</button>`;
  });
  tabs.innerHTML = tHtml;

  let rootHtml = '';
  DATA.matches.forEach((m, mIdx) => {
    if (selectedMatchIdx !== -1 && selectedMatchIdx !== mIdx) return;

    const key = `m${mIdx}`;
    const prev = preserved[key] || {};
    const startTurnIdx = (m.timeline || []).findIndex(t => t.turn >= 1);
    TL_STATE[key] = {
      match: m,
      view: prev.view || 'overview',
      turnIdx: prev.turnIdx ?? (startTurnIdx >= 0 ? startTurnIdx : 0),
      showEnd: prev.showEnd || false,
      filter: prev.filter || 'all',
      heroUid: prev.heroUid || null,
      timer: null,
    };

    const isWin = m.user_result === 'Victory';
    const rs = m.rng_summary || {};
    const oc = m.opening_cr || {};

    const chips = [];
    if (oc.alerts && oc.alerts.length) {
      chips.push(`<span class="rchip tone-cyan" onclick="setMatchSubView('${key}', 'timeline', 'all')">⚡ ${oc.alerts.length} Speed Alert${oc.alerts.length > 1 ? 's' : ''}</span>`);
    }
    if (rs.resist_15_pct > 0) {
      chips.push(`<span class="rchip tone-magenta" onclick="setMatchSubView('${key}', 'timeline', 'resist15')">🎲 ${rs.resist_15_pct}× 15% Resist!</span>`);
    }
    if (rs.resist_er > 0) {
      chips.push(`<span class="rchip tone-gold" onclick="setMatchSubView('${key}', 'timeline', 'resist')">🛡 ${rs.resist_er}× ER Resist</span>`);
    }
    if (rs.evades_misses > 0) {
      chips.push(`<span class="rchip tone-amber" onclick="setMatchSubView('${key}', 'timeline', 'evade')">💨 ${rs.evades_misses}× Evade/Miss</span>`);
    }
    if (rs.counters > 0) {
      chips.push(`<span class="rchip tone-purple" onclick="setMatchSubView('${key}', 'timeline', 'passive')">⚔ ${rs.counters}× Counter</span>`);
    }
    if ((rs.extra_turns || 0) + (rs.dual_attacks || 0) > 0) {
      chips.push(`<span class="rchip tone-cyan" onclick="setMatchSubView('${key}', 'timeline', 'cr')">⚡ ${(rs.extra_turns || 0) + (rs.dual_attacks || 0)}× Extra/Dual</span>`);
    }
    if (!chips.length) {
      chips.push(`<span style="font-size:0.75rem; color:var(--text-2);">Clean RNG execution (0× 15% resists, 0× misses)</span>`);
    }

    const eCards = (m.enemy.units || []).map((u, uIdx) => renderUnitCard(u, 'enemy', `g_${key}_e_${uIdx}`, key)).join('');
    const uCards = (m.user.units || []).map((u, uIdx) => renderUnitCard(u, 'user', `g_${key}_u_${uIdx}`, key)).join('');

    const curView = TL_STATE[key].view;
    const hideBuilds = curView === 'timeline' ? 'hidden-sec' : '';
    const hideTl = curView === 'overview' ? 'hidden-sec' : '';

    rootHtml += `<section class="match-block">
      <div class="match-top-bar">
        <div class="match-meta-left">
          <span class="match-num">Match #${m.match_index}</span>
          <span class="res-pill ${isWin ? 'res-win' : 'res-loss'}">${esc(m.user_result)} · ${esc(m.win_reason)}</span>
          <span class="meta-tag">🌐 ${esc(m.mode_label)} (${esc(m.season_id)})</span>
          <span class="meta-tag">⏱ ${m.duration_seconds ?? '?'}s · ${m.turn_count ?? '?'} Turns</span>
          <span class="meta-tag frenzy">🔥 Frenzy Stage ${m.frenzy_stage ?? 0}</span>
          <span style="font-size:0.76rem; color:var(--text-2);">${esc(m.played_at_utc || '')}</span>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="action-btn" onclick="toggleAllGear('${key}', this)">🔽 Expand All Gear</button>
          <button class="action-btn" onclick="copyRtaSummary(${mIdx}, this)">📋 Copy Discord Summary</button>
        </div>
      </div>

      <div class="scoreboard">
        ${renderPlayerCard(m.enemy)}
        <div class="vs-circle">VS</div>
        ${renderPlayerCard(m.user)}
      </div>

      <div class="subnav-row">
        <div class="seg-group">
          <button class="seg-btn ${curView === 'overview' ? 'active' : ''}" id="subbtn_${key}_overview" onclick="setMatchSubView('${key}', 'overview')">🛡 Draft &amp; 4v4 Builds</button>
          <button class="seg-btn ${curView === 'timeline' ? 'active' : ''}" id="subbtn_${key}_timeline" onclick="setMatchSubView('${key}', 'timeline')">⚡ Speed Track &amp; Live Timeline (${(m.timeline || []).length})</button>
          <button class="seg-btn ${curView === 'all' ? 'active' : ''}" id="subbtn_${key}_all" onclick="setMatchSubView('${key}', 'all')">✨ Show Everything</button>
        </div>
        <div class="rng-chips">
          <span class="rng-lbl">🎲 RNG Highlights:</span>
          ${chips.join('')}
        </div>
      </div>

      <div class="${hideBuilds}" id="sec_draft_wrap_${key}">
        ${renderDraftBoard(m, key)}
      </div>

      <div class="builds-wrapper ${hideBuilds}" id="sec_builds_${key}">
        <div class="team-showcase enemy">
          <div class="team-hdr">
            <span class="team-hdr-title">🔓 Unmasked Opponent Fielded Team — ${esc(m.enemy.name)}</span>
            <span style="font-size:0.77rem; color:var(--text-2);">${m.enemy.cp ? `Team CP: ${m.enemy.cp.toLocaleString()}` : (m.enemy.is_bot ? 'Official RTA Template' : '')}</span>
          </div>
          <div class="units-grid-4">${eCards}</div>
        </div>

        <div class="team-showcase user">
          <div class="team-hdr">
            <span class="team-hdr-title">⚔ User Fielded Team — ${esc(m.user.name)}</span>
            <span style="font-size:0.77rem; color:var(--text-2);">${m.user.cp ? `Team CP: ${m.user.cp.toLocaleString()}` : ''}</span>
          </div>
          <div class="units-grid-4">${uCards}</div>
        </div>
      </div>

      <div class="timeline-section ${hideTl}" id="sec_tl_${key}">
        ${renderOpeningCrSolver(oc, key)}
        <div id="tl_dyn_${key}"></div>
      </div>
    </section>`;
  });

  document.getElementById('matchesRoot').innerHTML = rootHtml;

  for (const [k, st] of Object.entries(TL_STATE)) {
    if (st.view === 'timeline' || st.view === 'all') {
      updateTimelineView(k);
    }
  }
}

renderApp(-1);
</script>
</body>
</html>
"""


def generate_rta_html_report(payload: Dict[str, Any], output_path: Path) -> None:
    safe_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = RTA_HTML_TEMPLATE.replace("__PAYLOAD_JSON__", safe_json)
    output_path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    script_dir = Path(__file__).resolve().parent
    json_in = script_dir / "output" / "rta_analysis.json"
    html_out = script_dir / "output" / "rta_report.html"
    if not json_in.exists():
        from e7_rta_parser import main as run_rta_parser
        raise SystemExit(run_rta_parser())
    data = json.loads(json_in.read_text(encoding="utf-8"))
    generate_rta_html_report(data, html_out)
    print(f"Generated RTA HTML report -> {html_out}")
