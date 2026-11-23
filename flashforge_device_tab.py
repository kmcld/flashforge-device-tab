# /// script
# dependencies = []
# [tool.orcaslicer.plugin]
# name = "FlashForge Device Tab"
# description = "Follow local FlashForge prints in the Device tab with live progress, temperatures, camera, print history and job controls."
# author = "Max"
# version = "1.8.1"
# ///
import base64
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import threading
import time
from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import TCPServer, ThreadingMixIn
from urllib.parse import urlsplit

import orca

PORT = 8899
API_PORT = 8898
MAX_PREVIEW = 2 * 1024 * 1024
PNG_HEADER = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"

# A stalled printer should fail one read, not one whole catalog request: the
# idle timeout applies per read while the total cap still bounds a trickling
# transfer.
READ_IDLE = 4.0
READ_TOTAL = 30.0

POLL_INTERVAL = 5
IDLE_POLL_INTERVAL = 15
HISTORY_LIMIT = 500
HISTORY_CHECKPOINT = 30
MAX_JOB_GAP = 180

# The Orca upload worker must not be held hostage by a monitor request that is
# already talking to the printer, so the drain wait is short and best-effort.
UPLOAD_DRAIN = 1.5
# If UploadFinished never arrives (cancelled or failed transfer), monitoring
# resumes on its own instead of answering 503 until the plugin is reloaded.
UPLOAD_TIMEOUT = 900

PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{printer}}</title>
<style>
:root {
  color-scheme: light;
  --bg: #efefef;
  --panel: #fff;
  --header: #f7f7f7;
  --text: #242b31;
  --muted: #5d6873;
  --line: #d9dee3;
  --blue: #166ac7;
  --track: #e4e9ef;
  --ok: #23764b;
  --warn: #92502a;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #242829;
    --panel: #303637;
    --header: #373f41;
    --text: #f0f3f6;
    --muted: #b7c1c8;
    --line: #4b5558;
    --blue: #77baff;
    --track: #50585c;
    --ok: #88d8a8;
    --warn: #f0b488;
  }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px "Segoe UI", system-ui, "Noto Sans", "DejaVu Sans", sans-serif; font-variant-numeric: tabular-nums; }
main { max-width: 1280px; margin: auto; padding: 20px 24px 28px; }
h1, h2, p, dl, dd { margin: 0; }
h1 { font-size: 17px; font-weight: 600; }
h2 { font-size: 14px; font-weight: 600; }
.muted { color: var(--muted); }
.device { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 16px; padding: 0 0 10px; margin-bottom: 12px; border-bottom: 2px solid var(--panel); }
.identity, .connection, .device-actions, .row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; }
.device-message { margin: -4px 0 12px; font-size: 12px; color: var(--warn); }
.row { justify-content: space-between; }
.device-state { font-size: 13px; color: var(--muted); }
.device-state[data-state="active"] { color: var(--blue); }
.device-state[data-state="complete"] { color: var(--ok); }
.connection { gap: 6px; color: var(--muted); font-size: 12px; white-space: nowrap; }
.connection::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.connection.connected { color: var(--ok); }
.connection.disconnected { color: var(--warn); }
.dashboard { display: grid; grid-template-columns: minmax(0, 1.75fr) minmax(280px, 1fr); gap: 12px; align-items: start; }
.printer-sidebar { display: grid; gap: 12px; }
.dashboard > * { min-width: 0; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
.panel > .panel-title:first-child { border-radius: 7px 7px 0 0; }
.identity::before { content: ""; width: 24px; height: 24px; background: center / contain no-repeat url(data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAF7klEQVR4nK2XbYydRRXHf2dmnpd77/buu+lWtEJSsYsEP/mSiLEpVBpQGuFqjBE/GLUiRCWmxKSJRkOESBRsgn7R2KgQWdrFmLQFQ2hLjFUS24Dd1i3WUNB2F2F3u3f33vu8zPHD031pd7t2dznJ8+GZzMz5zX/OnJwjzJkAumbbtm4XB5u95zpBI1SF1ZiIKiYxRk8krey5qcHB0Qu+AFQu/Ajg22u1+8TIt8TIVYhZld8Fpop6fxb1u8affOqHMxBCrWYZGMjba3fstnHpLp8kkOceQeVtglD1oAjWGhOG+GbrqfH+/s8yNCQC0FH79DckLj+izUaCEogxYkRIswyvuirnRoTAOXyhgCKkplQKfaO1c3xg4AGp1mpdBn8SY7vJc8QYk+eeqWaDrmqVKAhYKYIASZbx5sQE5TjGWYuqKiKq3tedSzc64GaxrlezzBfOc5xzPHT3dj7z8U3E5WhVAK1GwtMvvMAPfrWbRpLgrBX1Xk0QVLOMW52F6zFGEdSIMNlq8ZOvfZV7Nm/h6ZeP8trICIFz6DKvQkRIs5y+nm7u2XILlbYy2x/8EUG5jAqKiOK53ik+FBARQzNNuaq3l89vvonfHPkTX/je93EuQFeogREhbSX87Ds7uGvTTTy07re8NjJCFIYzAoUG5nb33lMplShhOPbKK9BsEkchztrZL7COwF3ms+6iuVEYQpJwdPgUoUJbuUQRh7MyqbuUWoFEPe9b/24++pEP016pkHu/IgWsMUxMTfH+a64mlcVVXABgjaGuKXfeuInP3bhpwQKdL9klNpPRLh1LgDoZ1izMKwsA8jynakJ+8ex+HtszSGd1DXmeY61lfLLOl2//JNs/sZVxn2CtnV3TYUJ2H3yOnz65h441bXNrzk/yxVu3ct+ntpHn+f8HKAaF0bExhk+doq2jkzTPCJyjPjZGfXqayFjUF0EGkAGRsUy3WvxjeJi2zk7SLCOwjvr4OGf/+yYWWVS5RQEUCJzDlEqU44gsdxhjyKpVXjxxkrrPiG1ApkVsRMbRRDny9+PE1SqlMCR0RUBOl0qESySzyyZ7VcV7j1fFq5LlOZU4Zt+fj/Clhx/GqWARRCE2ju2PPsLe5w9TiWPSPJ9d571fMocsqsDlLPee7mqVvc8fRFF+ueN+AjF85dEf8/iBZ+np6CBb5J6XsmUBAOSqvKO7i8GDh2krl4mCgMef+SO9XV3Ldr48AAHNPa16gwZKxQT8evAPoFApl6mPnQeUoBRjnLn8W10pgOZKqb2N/i0foqgTFGMMAhclqn+9OMT0W+cRe2W1xBUBiBHSRgufxfRd+56F2WZ2nuH0X46TNhOitvLbBCDgk5yu9WtZ+971/PPIy4gsTqCqvOuGDYyWY8b+/QY2ClYPIGJoTTeJKiU2fOwDZK1kSYAgjpgcHePc8BmiOFo5gIggIuRJytpr11PprHLs94cRs3SRrF4JKzHr+q9m4vVRuLDPFQOICCikWYaxFryy/oYNbLz5gyRTzSsDKMecOnyUo2dGcNaQZhmoLgrimBdSRoRWkpAJrOvpIZ+sM9GmHHpiP4ee2A9LnORiiuIN2jAgn6zT19uDF6GZFNenc/PEiUgKqKonCgLOjIyy729/5e7NW2gmCa+eO1fkcr/MkswUVXVfTzffvu12Dgy9xOnX/0Mchah6pHjHqVPPkGhRpalA6Bw7dj1GcK/w9a23YWc2XJb7uTzkgQMvHeObu3YhRor2SxFUxeCPS1ut1uvwJxHTgfcixkiSpuTec03fOkpxtOKaUIBmK+H02bOICHEQ4OfK8oax2UYBaK/dcb8tlR70jbnGRIBGkuC9R5Z9/hkVimwZh2HR+81rTPJG44GJgT07i9asv187hoZ+Z+LoTt9qccGripgVup4PMa81M8aYKCJvNvdNdHZtY2zMX1TGddRqO7HmXhHpLSJeWf7tL4YgM83pW6r+5xMjb3yXQ4dy5u0+2y531WrvzI25RVSvU9W4GF1hiy5FKSyGlmBOaJo+M75376vzfOr/AJq6riJACkPeAAAAAElFTkSuQmCC); }
.panel-title { padding: 9px 13px; background: var(--header); }
.job { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; align-items: center; padding: 16px; min-height: 158px; }
.job[data-preview="none"] { grid-template-columns: minmax(0, 1fr); }
.filename { font-size: 16px; font-weight: 600; overflow-wrap: anywhere; }
.job-image { width: clamp(72px, 10vw, 130px); aspect-ratio: 1; object-fit: contain; border-radius: 5px; background: var(--header); }
.time { margin-top: 15px; }
.eta { display: block; color: var(--blue); font-size: 27px; font-weight: 600; line-height: 1.15; }
.caption { display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }
.progress-line { display: flex; align-items: center; gap: 10px; margin-top: 13px; }
progress { display: block; appearance: none; width: 100%; height: 8px; border: 0; border-radius: 5px; background: var(--track); overflow: hidden; }
progress::-webkit-progress-bar { background: var(--track); border-radius: 5px; }
progress::-webkit-progress-value { background: var(--blue); border-radius: 5px; }
progress::-moz-progress-bar { background: var(--blue); border-radius: 5px; }
.percent { min-width: 4ch; text-align: right; }
.job-meta { display: flex; flex-wrap: wrap; gap: 4px 15px; margin-top: 8px; font-size: 12px; color: var(--muted); }
.readouts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 0 12px; }
.readout { padding: 11px 8px; border-bottom: 1px solid var(--line); }
.readout:nth-child(odd) { border-right: 1px solid var(--line); }
.readout:nth-child(even) { padding-left: 13px; }
.readout:nth-last-child(-n+2) { border-bottom: 0; }
.readout dt { color: var(--muted); font-size: 12px; }
.readout dd { margin-top: 4px; font-size: 14px; overflow-wrap: anywhere; }
.readout strong { color: var(--blue); font-size: 19px; font-weight: 600; }
.stale .job, .stale .readouts { opacity: .65; }
.catalog { margin-top: 16px; }
.monitor-tools { margin-top: 12px; }
.tool-body { padding: 12px 13px; }
.chart { display: block; width: 100%; height: 207px; }
.chart-legend { display: flex; flex-wrap: wrap; gap: 5px 12px; margin: 4px 0 1px; font-size: 12px; }
.chart-legend span::before { content: ""; display: inline-block; width: 15px; border-top: 2px solid var(--series); margin-right: 5px; vertical-align: 3px; }
.chart-legend .target::before { border-top-style: dashed; }
.nozzle-series { --series: var(--blue); }
.bed-series { --series: #a34a20; }
@media (prefers-color-scheme: dark) { .bed-series { --series: #ffc08d; } }
summary.panel-title { cursor: pointer; font-weight: 600; }
details .tool-body { border-top: 1px solid var(--line); }
.camera-form { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0; }
.camera-form input { flex: 1 1 220px; width: auto; }
.camera-image { display: block; width: 100%; max-height: 360px; object-fit: contain; background: var(--header); margin-top: 8px; }
.history-summary, .history-table { margin-top: 8px; }
.history-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.history-table th, .history-table td { padding: 7px 9px; text-align: left; border-bottom: 1px solid var(--line); }
.history-table th:not(:first-child), .history-table td:not(:first-child) { white-space: nowrap; }
.history-table td:first-child { overflow-wrap: anywhere; }
.table-wrap { overflow: auto; max-height: 350px; }
.file-controls { justify-content: flex-start; padding: 10px 12px; }
input, button { font: inherit; color: inherit; background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 6px 9px; }
input { min-width: 0; width: min(100%, 245px); }
input::placeholder { color: var(--muted); }
button { cursor: pointer; }
button:hover:enabled, button:focus-visible, input:focus-visible { border-color: var(--blue); }
button:disabled { opacity: .55; cursor: default; }
.files { display: grid; grid-template-columns: repeat(auto-fill, minmax(125px, 1fr)); gap: 8px; padding: 0 12px 12px; }
.file-card { min-width: 0; overflow: hidden; border: 1px solid var(--line); border-radius: 4px; }
.preview { position: relative; display: grid; place-items: center; aspect-ratio: 1.65; background: var(--header); font-size: 11px; }
.preview img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; background: inherit; }
.file-name { padding: 7px 8px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 12px; }
.pager { border-top: 1px solid var(--line); padding: 8px 12px; }
@media (max-width: 740px) {
  main { padding: 14px 12px; }
  .dashboard { grid-template-columns: minmax(0, 1fr); }
}
dialog.settings { width: min(420px, calc(100vw - 32px)); padding: 0; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); color: var(--text); }
dialog.settings::backdrop { background: rgba(10, 14, 16, .55); }
dialog.settings form { display: flex; flex-direction: column; gap: 6px; padding: 18px 20px 16px; }
dialog.settings h2 { margin-bottom: 6px; }
dialog.settings label { margin-top: 8px; font-size: 12px; color: var(--muted); }
dialog.settings input, dialog.settings textarea { width: 100%; }
dialog.settings textarea { font: 13px "Segoe UI", system-ui, "Noto Sans", "DejaVu Sans", sans-serif; resize: vertical; min-height: 96px; }
.fleet-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); cursor: pointer; white-space: nowrap; }
.fleet-toggle input { margin: 0; }
button.danger { border-color: #a4443f; color: #ffb4ae; }
button.danger.armed { background: #a4443f; color: #fff; }
.setup ol { margin: 6px 0 10px; padding-left: 20px; line-height: 1.55; }
.setup code { background: var(--header); border-radius: 4px; padding: 1px 5px; font-size: 12px; }
.fleet-list { display: flex; flex-direction: column; gap: 8px; }
.fleet-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 10px 12px; border: 1px solid var(--line); border-radius: 6px; }
.fleet-row.current { border-color: var(--blue); }
.fleet-row .name { font-weight: 600; overflow-wrap: anywhere; }
.fleet-row .meta { font-size: 12px; color: var(--muted); }
.fleet-row .value { font-size: 13px; white-space: nowrap; }
.fleet-row a { color: var(--blue); font-size: 12px; font-weight: 600; text-decoration: none; }
dialog.settings .caption { margin-top: 2px; }
.settings-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.settings-actions button[type="submit"] { background: var(--blue); border-color: var(--blue); color: #fff; }
</style>
</head>
<body>
<main>
  <header class="device">
    <div class="identity"><h1 id="deviceName">{{printer}}</h1><span class="device-state" id="status"></span></div>
    <div class="device-actions">
      <span class="connection" id="connection" role="status">Connecting…</span>
      <label class="fleet-toggle"><input type="checkbox" id="fleetToggle"> All printers</label>
      <span class="device-actions" id="jobActions" hidden>
      <button id="pausePrint" type="button" hidden>Pause</button>
      <button id="resumePrint" type="button" hidden>Resume</button>
      <button id="cancelPrint" type="button" class="danger" hidden>Cancel</button>
      </span>
      <button id="settingsButton" type="button" aria-haspopup="dialog">Settings</button>
      <button id="nativePage" type="button">Printer Web Interface</button>
    </div>
  </header>
  <p class="device-message" id="viewMessage" role="status" hidden></p>
  <section class="panel" id="setup" hidden>
    <h2 class="panel-title">Set up your first printer</h2>
    <div class="tool-body setup">
      <ol>
        <li>Press <b>Settings</b> in the panel header.</li>
        <li>Add one line per printer: <code>192.168.1.50 | 1a2b3c4d | Adventurer 5M</code> —
            address | CheckCode | name. The CheckCode is in the printer's network settings.</li>
        <li>Save and wait a few seconds: temperatures and progress appear here.</li>
      </ol>
      <p class="caption">Only FlashForge printers on the local network are supported. The panel never starts a
        print or deletes files; it can only pause, resume or cancel the running job.</p>
    </div>
  </section>
  <section class="panel" id="fleet" hidden>
    <div class="row panel-title"><h2>Printers</h2><button id="fleetRefresh" type="button">Refresh</button></div>
    <div class="tool-body"><div class="fleet-list" id="fleetList"></div></div>
  </section>
  <div class="dashboard" id="monitor">
    <section class="panel" aria-labelledby="job-title">
      <h2 class="panel-title" id="job-title">Print Job</h2>
      <div class="job" id="job" data-preview="none">
        <div>
          <p class="filename" id="jobFile">—</p>
          <div class="time" id="time" hidden>
            <strong class="eta" id="remaining"></strong>
            <span class="caption">Time Remaining</span>
          </div>
          <div class="progress-line" id="progressLine" hidden>
            <progress id="bar" max="100" aria-label="Print progress"></progress>
            <strong class="percent" id="percent"></strong>
          </div>
          <div class="job-meta" id="jobMeta" hidden>
            <span id="layers"></span><span id="elapsed"></span><span id="filament"></span>
          </div>
          <p class="caption" id="timeHint" hidden></p>
        </div>
        <img class="job-image" id="jobImage" alt="Current print job preview" hidden>
      </div>
    </section>
    <section class="panel" aria-labelledby="readouts-title">
      <h2 class="panel-title" id="readouts-title">Printer Readings</h2>
      <dl class="readouts">
        <div class="readout"><dt id="nozzleLabel">Nozzle</dt><dd><strong id="nozzle">—</strong> <span class="muted" id="nozzleTarget"></span></dd></div>
        <div class="readout"><dt>Bed</dt><dd><strong id="bed">—</strong> <span class="muted" id="bedTarget"></span></dd></div>
        <div class="readout" id="extraNozzlesReading" hidden><dt>Other tools</dt><dd id="extraNozzles"></dd></div>
        <div class="readout" id="chamberReading" hidden><dt>Chamber</dt><dd id="chamber"></dd></div>
        <div class="readout" id="fanReading" hidden><dt>Part Cooling Fan</dt><dd><strong id="fan"></strong></dd></div>
        <div class="readout" id="speedReading" hidden><dt>Print Speed</dt><dd><strong id="speed"></strong></dd></div>
      </dl>
    </section>
  </div>
  <div class="dashboard monitor-tools" id="printerTools">
    <section class="panel" aria-labelledby="chart-title">
      <h2 class="panel-title" id="chart-title">Temperatures · Last 30 Minutes</h2>
      <div class="tool-body">
        <canvas class="chart" id="chart" role="img" aria-label="Chart of actual and target temperatures for the nozzle and bed"></canvas>
        <div class="chart-legend muted">
          <span class="nozzle-series" id="chartNozzle">Nozzle</span><span class="nozzle-series target" id="chartNozzleTarget">Nozzle Target</span>
          <span class="bed-series">Bed</span><span class="bed-series target">Bed Target</span>
        </div>
      </div>
    </section>
    <div class="printer-sidebar">
      <details class="panel" id="camera">
        <summary class="panel-title">Camera</summary>
        <div class="tool-body">
          <label for="cameraUrl">MJPEG stream URL (HTTP or HTTPS)</label>
          <form class="camera-form" id="cameraForm">
            <input id="cameraUrl" type="url" maxlength="2048" placeholder="http://…" autocomplete="off">
            <button id="cameraStart" type="submit">Connect</button>
            <button id="cameraStop" type="button" hidden>Disconnect</button>
          </form>
          <p class="caption" id="cameraStatus" role="status"></p>
          <button id="cameraSnapshot" type="button" hidden>Save snapshot</button>
          <img class="camera-image" id="cameraImage" alt="Printer camera feed" referrerpolicy="no-referrer" hidden>
        </div>
      </details>
      <section class="panel" aria-labelledby="totals-title">
        <h2 class="panel-title" id="totals-title">Totals</h2>
        <div class="tool-body">
          <dl class="readouts">
            <div class="readout"><dt>Jobs</dt><dd id="statJobs"></dd></div>
            <div class="readout"><dt>Print time</dt><dd id="statHours"></dd></div>
            <div class="readout"><dt>Finished cleanly</dt><dd id="statSuccess"></dd></div>
          </dl>
          <p class="caption">Counted from the print history on this computer.</p>
        </div>
      </section>
      <details class="panel">
        <summary class="panel-title">Printer details</summary>
        <div class="tool-body">
          <dl class="readouts">
            <div class="readout"><dt>Model</dt><dd id="printerModel"></dd></div>
            <div class="readout"><dt>Firmware</dt><dd id="printerFirmware"></dd></div>
            <div class="readout"><dt>Serial</dt><dd id="printerSerial"></dd></div>
            <div class="readout"><dt>MAC</dt><dd id="printerMac"></dd></div>
            <div class="readout"><dt>Build volume</dt><dd id="printerVolume"></dd></div>
            <div class="readout"><dt>Light</dt><dd id="printerLight"></dd></div>
          </dl>
          <p class="caption" id="printerSensors"></p>
        </div>
      </details>
    </div>
  </div>
  <details class="panel monitor-tools" id="history">
    <summary class="panel-title">Print History</summary>
    <div class="tool-body">
      <p class="muted" id="historyStatus" role="status">Print history is recorded while OrcaSlicer is open.</p>
      <p class="history-summary" id="historySummary"></p>
      <div class="table-wrap" id="historyTable" hidden>
        <table class="history-table">
          <thead><tr><th scope="col">File</th><th scope="col">Started</th><th scope="col">Print Time</th><th scope="col">Result</th></tr></thead>
          <tbody id="historyRows"></tbody>
        </table>
      </div>
    </div>
  </details>
  <section class="panel catalog" id="files-section" aria-labelledby="files-title">
    <div class="row panel-title"><h2 id="files-title">Printer Files</h2><button id="refresh">Refresh</button></div>
    <div class="row file-controls">
      <input id="search" type="search" placeholder="Search files" aria-label="Search files">
      <span class="muted" id="fileCount" role="status"></span>
    </div>
    <div class="files" id="files"></div>
    <div class="row pager" id="pager" hidden>
      <button id="previous">Previous</button>
      <span class="muted" id="pageNumber"></span>
      <button id="next">Next</button>
    </div>
  </section>
</main>
<dialog class="settings" id="settingsDialog" aria-labelledby="settingsTitle">
  <form id="settingsForm">
    <h2 id="settingsTitle">Printers</h2>
    <label for="printersText">One printer per line: address | CheckCode | name | serial number</label>
    <textarea id="printersText" rows="5" spellcheck="false" placeholder="192.168.1.50 | 1a2b3c4d | Adventurer 5M"></textarea>
    <p class="caption">Creator 5 and 5 Pro require both CheckCode and the printer serial number.
      Adventurer 5M reads its serial number automatically.</p>
    <p class="caption" id="settingsStatus" role="status"></p>
    <div class="settings-actions">
      <button type="button" id="settingsCancel">Cancel</button>
      <button type="submit" id="settingsSave">Save</button>
    </div>
  </form>
</dialog>
<script>
const el = id => document.getElementById(id);
const pageSize = 12;
const endpoint = path => new URL(path.replace(/^\//, ''), window.location.href);
let files = [];
let page = 0;
let renderVersion = 0;
let polling = false;
let jobFile = '';
let previewKey = '';
let catalogLoaded = false;
const imageRequests = new Map();

async function getJson(path) {
  const response = await fetch(endpoint(path), {cache: 'no-store', signal: AbortSignal.timeout(12000)});
  const data = await response.json();
  if (!response.ok) throw Object.assign(Error(data.error || 'No response'), {status: response.status});
  return data;
}

el('nativePage').onclick = async () => {
  const button = el('nativePage');
  const message = el('viewMessage');
  button.disabled = true;
  message.hidden = true;
  try {
    const response = await fetch(endpoint('/native'), {method: 'POST',
      headers: {'X-FlashForge-Tab-Action': 'restore'}, signal: AbortSignal.timeout(12000)});
    if (!response.ok) throw Error((await response.json()).error);
    message.textContent = 'Opening the printer web interface…';
    message.hidden = false;
    setTimeout(() => { button.disabled = false; message.hidden = true; }, 5000);
  } catch (error) {
    message.textContent = error.message || 'Could not open the printer web interface.';
    message.hidden = false;
    button.disabled = false;
  }
};

function duration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const rest = seconds % 60;
  const pad = value => String(value).padStart(2, '0');
  return pad(hours) + ':' + pad(minutes) + ':' + pad(rest);
}

const celsius = value => Number.isFinite(value) ? Math.round(value) + ' °C' : '';
const plain = value => Number.isFinite(value) ? String(Math.round(value)) : '—';

const chart = el('chart');
let temperatures = [];
const shortTime = new Intl.DateTimeFormat('en-US', {hour: '2-digit', minute: '2-digit'});
const historyTime = new Intl.DateTimeFormat('en-US', {dateStyle: 'short', timeStyle: 'short'});

function drawChart() {
  const width = chart.clientWidth;
  const height = chart.clientHeight;
  if (!width || !height) return;
  const scale = window.devicePixelRatio || 1;
  chart.width = Math.round(width * scale);
  chart.height = Math.round(height * scale);
  const ctx = chart.getContext('2d');
  ctx.scale(scale, scale);
  const colors = getComputedStyle(chart);
  const series = [
    ['nozzle', colors.getPropertyValue('--blue').trim(), []],
    ['nozzleTarget', colors.getPropertyValue('--blue').trim(), [5, 4]],
    ['bed', colors.colorScheme === 'dark' ? '#ffc08d' : '#a34a20', []],
    ['bedTarget', colors.colorScheme === 'dark' ? '#ffc08d' : '#a34a20', [5, 4]]
  ];
  const end = Date.now();
  const start = end - 30 * 60 * 1000;
  const left = 41, right = width - 16, top = 11, bottom = height - 26;
  const samples = temperatures.filter(sample => sample.t >= start);
  const max = Math.max(100, Math.ceil(Math.max(0, ...samples.flatMap(sample =>
    series.map(([field]) => sample[field] ?? 0))) / 50) * 50);
  const x = t => left + (t - start) / (end - start) * (right - left);
  const y = value => bottom - Math.max(0, value) / max * (bottom - top);
  ctx.font = '11px "Segoe UI", system-ui, "Noto Sans", "DejaVu Sans", sans-serif';
  ctx.fillStyle = colors.getPropertyValue('--muted').trim();
  ctx.strokeStyle = colors.getPropertyValue('--line').trim();
  for (let tick = 0; tick <= 4; tick++) {
    const value = max * tick / 4;
    ctx.beginPath();
    ctx.moveTo(left, y(value));
    ctx.lineTo(right, y(value));
    ctx.stroke();
    ctx.fillText(Math.round(value) + '°', 5, y(value) + 4);
  }
  ctx.fillText(shortTime.format(start), left, height - 5);
  const current = shortTime.format(end);
  ctx.fillText(current, right - ctx.measureText(current).width, height - 5);
  if (!samples.some(sample => sample.nozzle != null || sample.bed != null)) {
    const label = 'Waiting for temperature readings';
    ctx.fillText(label, Math.max(left + 5, (width - ctx.measureText(label).width) / 2), height / 2);
  }
  for (const [field, color, dash] of series) {
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash(dash);
    ctx.beginPath();
    let previous = 0;
    let last = null;
    for (const sample of samples) {
      const value = sample[field];
      if (!Number.isFinite(value)) {
        previous = 0;
        continue;
      }
      const px = x(sample.t), py = y(value);
      if (previous && sample.t - previous < 18000) ctx.lineTo(px, py);
      else ctx.moveTo(px, py);
      previous = sample.t;
      last = [px, py];
    }
    ctx.stroke();
    if (last && !dash.length) {
      ctx.beginPath();
      ctx.arc(...last, 2.5, 0, 2 * Math.PI);
      ctx.fill();
    }
  }
}

async function updateTools() {
  try {
    const data = await getJson('/telemetry');
    temperatures = data.temperatures;
    if (Array.isArray(data.printers)) {
      fleetPrinters = data.printers;
      applyView();
    }
    if (data.stats) {
      el('statJobs').textContent = String(data.stats.jobs || 0);
      el('statHours').textContent = data.stats.seconds ? duration(data.stats.seconds) : '0 min';
      el('statSuccess').textContent = data.stats.successRate == null
        ? '—' : data.stats.successRate + ' %';
    }
    drawChart();
    if (data.cameraUrl && !el('cameraUrl').value && !el('cameraUrl').matches(':focus')) {
      el('cameraUrl').value = data.cameraUrl;
    }
    if (!el('history').open) return;
    const jobs = data.history;
    let message = 'Print history is recorded while OrcaSlicer is open.';
    if (data.historyError) message = data.historyError;
    else if (!jobs.length) message = 'No print jobs recorded yet. Print history is recorded while OrcaSlicer is open.';
    el('historyStatus').textContent = message;
    const completed = jobs.filter(job => job.result === 'complete');
    const total = completed.reduce((seconds, job) => seconds + (job.elapsed ?? 0), 0);
    el('historySummary').textContent = jobs.length
      ? 'Completed: ' + completed.length + ' of ' + jobs.length
        + (total ? ' · Total print time: ' + duration(total) : '')
      : '';
    const results = {active: 'In Progress', complete: 'Completed', cancelled: 'Canceled',
                     error: 'Error', unknown: 'Unknown Result'};
    el('historyRows').replaceChildren(...jobs.map(job => {
      const row = document.createElement('tr');
      for (const value of [job.file || 'Unknown filename', historyTime.format(job.started * 1000),
                           job.elapsed == null ? '—' : duration(job.elapsed), results[job.result]]) {
        const cell = row.insertCell();
        cell.textContent = value;
      }
      return row;
    }));
    el('historyTable').hidden = !jobs.length;
  } catch {
    drawChart();
    el('historyStatus').textContent = 'Print history is temporarily unavailable.';
  }
}

const cameraImage = el('cameraImage');
const cameraUrl = el('cameraUrl');
const cameraStatus = el('cameraStatus');
const cameraStorageKey = 'flashforge-device-tab-camera-' + window.location.pathname;
let cameraTimeout;
try { cameraUrl.value = localStorage.getItem(cameraStorageKey) || ''; } catch {}

function stopCamera(message = '') {
  clearTimeout(cameraTimeout);
  cameraImage.removeAttribute('src');
  cameraImage.hidden = true;
  el('cameraSnapshot').hidden = true;
  el('cameraStop').hidden = true;
  el('cameraStart').disabled = false;
  cameraStatus.textContent = message;
}

el('cameraForm').onsubmit = event => {
  event.preventDefault();
  let url;
  try { url = new URL(cameraUrl.value.trim()); } catch {}
  if (!url || !['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    stopCamera('Enter an HTTP or HTTPS MJPEG URL without a username or password.');
    return;
  }
  try { localStorage.setItem(cameraStorageKey, url.href); } catch {}
  cameraImage.src = url.href;
  cameraImage.hidden = false;
  el('cameraSnapshot').hidden = false;
  el('cameraStart').disabled = true;
  el('cameraStop').hidden = false;
  cameraStatus.textContent = 'Connecting…';
  cameraTimeout = setTimeout(() => stopCamera('No frame received. Check the URL and confirm the stream uses MJPEG.'), 12000);
};
cameraImage.onload = () => {
  clearTimeout(cameraTimeout);
  cameraStatus.textContent = '';
};
cameraImage.onerror = () => {
  if (cameraImage.hasAttribute('src')) stopCamera('Could not open the MJPEG stream. Check the camera URL.');
};
el('cameraStop').onclick = () => stopCamera();
el('camera').ontoggle = () => { if (!el('camera').open) stopCamera(); };
el('history').ontoggle = () => { if (el('history').open) updateTools(); };
new ResizeObserver(drawChart).observe(chart);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', drawChart);

function previewUrl(id) {
  if (!imageRequests.has(id)) {
    const promise = fetch(endpoint('/preview/' + id), {signal: AbortSignal.timeout(12000)})
      .then(response => {
        if (response.status === 404) return null;
        if (!response.ok) throw Error('Preview unavailable');
        return response.blob().then(blob => URL.createObjectURL(blob));
      })
      .catch(() => {
        if (imageRequests.get(id) === promise) imageRequests.delete(id);
        return null;
      });
    imageRequests.set(id, promise);
  }
  return imageRequests.get(id);
}

async function updateJobPreview() {
  const matching = files.filter(file => file.name === jobFile);
  const key = jobFile && matching.length === 1 ? matching[0].id : '';
  const image = el('jobImage');
  if (key !== previewKey) {
    previewKey = key;
    image.hidden = true;
    el('job').dataset.preview = 'none';
  }
  if (!image.hidden) return;
  if (!key) return;
  const request = previewUrl(key);
  const url = await request;
  if (!url || previewKey !== key || imageRequests.get(key) !== request) return;
  image.src = url;
  image.hidden = false;
  el('job').dataset.preview = 'available';
}

function updateJob(data) {
  const previousJob = jobFile;
  jobFile = data.file || '';
  if (catalogLoaded && data.active && jobFile && previousJob !== jobFile
      && !files.some(file => file.name === jobFile)) refreshFiles();
  el('jobFile').textContent = jobFile || (data.complete ? 'Print complete' : data.active ? 'Printing' : 'No active print job');
  el('progressLine').hidden = data.percent == null;
  el('percent').textContent = data.percent == null ? '' : data.percent + '%';
  el('bar').value = data.percent ?? 0;
  el('time').hidden = data.remaining == null;
  el('remaining').textContent = data.remaining == null ? '' : duration(data.remaining);
  el('timeHint').hidden = !data.active || !data.timeHint;
  el('timeHint').textContent = data.timeHint || '';
  el('layers').textContent = data.layer == null || !data.layerTotal
    ? '' : 'Layer ' + data.layer + ' of ' + data.layerTotal;
  el('elapsed').textContent = data.elapsed == null ? '' : 'Elapsed: ' + duration(data.elapsed);
  el('filament').textContent = data.filament == null ? '' : 'Estimated material: ' + data.filament.toLocaleString('en-US', {maximumFractionDigits: 2}) + ' g';
  el('jobMeta').hidden = !el('layers').textContent && data.elapsed == null && data.filament == null;
  updateJobPreview();
}

async function updateStatus() {
  if (polling || document.visibilityState !== 'visible') return;
  polling = true;
  const connection = el('connection');
  try {
    const data = await getJson('/status');
    connection.className = 'connection connected';
    connection.textContent = 'Connected';
    el('monitor').classList.remove('stale');
    el('status').textContent = data.status;
    el('status').dataset.state = data.complete ? 'complete' : data.active ? 'active' : '';
    el('nozzle').textContent = plain(data.nozzle);
    el('nozzleTarget').textContent = data.nozzleTarget == null
      ? (Number.isFinite(data.nozzle) ? '°C' : '') : '/ ' + celsius(data.nozzleTarget);
    el('bed').textContent = plain(data.bed);
    el('bedTarget').textContent = data.bedTarget == null
      ? (Number.isFinite(data.bed) ? '°C' : '') : '/ ' + celsius(data.bedTarget);
    const tools = data.tools || [];
    el('nozzleLabel').textContent = tools.length ? 'T1' : 'Nozzle';
    el('chartNozzle').textContent = el('nozzleLabel').textContent;
    el('chartNozzleTarget').textContent = el('nozzleLabel').textContent + ' Target';
    const temperatures = (actual, target) => [actual, target]
      .map(value => value == null ? '—' : celsius(value)).join(' / ');
    el('extraNozzlesReading').hidden = tools.length < 2;
    el('extraNozzles').textContent = tools.slice(1).map((tool, index) =>
      'T' + (index + 2) + ' ' + temperatures(tool.actual, tool.target)).join(' · ');
    el('chamberReading').hidden = data.chamber == null;
    el('chamber').textContent = data.chamber == null ? ''
      : temperatures(data.chamber.actual, data.chamber.target);
    for (const key of ['fan', 'speed']) {
      el(key + 'Reading').hidden = data[key] == null;
      el(key).textContent = data[key] == null ? '' : data[key] + ' %';
    }
    updateJob(data);
    updatePrinter(data);
    updateActions(data);
    if (!catalogLoaded) refreshFiles();
  } catch (error) {
    connection.className = 'connection disconnected';
    connection.textContent = error.message === 'Failed to fetch' ? 'Disconnected' : error.message;
    el('monitor').classList.add('stale');
    updateActions({});
    el('status').textContent = '';
    el('time').hidden = true;
    el('timeHint').hidden = true;
    if (error.status === 409) {
      files = [];
      jobFile = '';
      catalogLoaded = false;
      for (const promise of imageRequests.values()) promise.then(url => { if (url) URL.revokeObjectURL(url); });
      imageRequests.clear();
      updateJobPreview();
      renderFiles();
      stopCamera();
    }
  } finally {
    polling = false;
  }
  updateTools();
}

function updatePrinter(data) {
  const info = data.info || {};
  const sensors = data.sensors || {};
  const set = (id, value) => { const node = el(id); if (node) node.textContent = value || ''; };
  set('printerModel', info.model);
  set('printerFirmware', info.firmware);
  set('printerSerial', info.serial);
  set('printerMac', info.mac);
  set('printerVolume', info.volume ? info.volume.join(' x ') + ' mm' : '');
  set('printerLight', sensors.led == null ? '' : sensors.led ? 'On' : 'Off');
  const ends = sensors.endstops || {};
  const parts = Object.entries(ends).map(([axis, value]) => axis.toUpperCase() + ' ' + value);
  if (sensors.door) parts.push('Door / lid: ' + sensors.door);
  if (sensors.errorCode) parts.push('Error: ' + sensors.errorCode);
  set('printerSensors', (Object.keys(ends).length ? 'Endstops: ' : '') + parts.join('  '));
}

function updateActions(data) {
  const state = data.state || '';
  const paused = data.jobControls && state === 'PAUSED';
  const running = data.jobControls && data.active && !paused;
  el('pausePrint').hidden = !running;
  el('resumePrint').hidden = !paused;
  el('cancelPrint').hidden = !(running || paused);
  const cancel = el('cancelPrint');
  if (cancel.classList.contains('armed')) {
    cancel.classList.remove('armed');
    cancel.textContent = 'Cancel';
  }
}

async function runAction(action) {
  const connection = el('connection');
  try {
    const response = await fetch(endpoint('/action'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-FlashForge-Tab-Action': 'action'},
      body: JSON.stringify({action}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'The printer did not accept the command.');
    connection.className = 'connection connected';
    connection.textContent = 'Connected';
    await updateStatus();
  } catch (error) {
    connection.className = 'connection disconnected';
    connection.textContent = error.message || 'Command failed';
  }
}

function downloadSnapshot() {
  const url = endpoint('/snapshot');
  fetch(url).then(async response => {
    if (!response.ok) throw new Error('No snapshot');
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'printer-snapshot.jpg';
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  }).catch(() => {
    el('cameraStatus').textContent = 'Could not read a snapshot from the printer.';
  });
}

function fileCard(file) {
  const card = document.createElement('article');
  card.className = 'file-card';
  const preview = document.createElement('div');
  preview.className = 'preview muted';
  const placeholder = document.createElement('span');
  placeholder.textContent = 'Preview unavailable';
  preview.append(placeholder);
  const name = document.createElement('div');
  name.className = 'file-name';
  name.textContent = file.name;
  name.title = file.name;
  card.append(preview, name);
  return {card, preview};
}

async function loadPreviews(entries, version) {
  for (const {file, preview} of entries) {
    if (version !== renderVersion) return;
    const url = await previewUrl(file.id);
    if (url && version === renderVersion) {
      const image = new Image();
      image.alt = '';
      image.src = url;
      preview.replaceChildren(image);
    }
  }
}

function renderFiles() {
  const version = ++renderVersion;
  const query = el('search').value.trim().toLocaleLowerCase();
  const matching = files.filter(file => file.name.toLocaleLowerCase().includes(query));
  const pageCount = Math.ceil(matching.length / pageSize);
  page = Math.max(0, Math.min(page, pageCount - 1));
  el('fileCount').textContent = matching.length ? String(matching.length) : query ? 'No matching files' : 'No files';
  el('pager').hidden = pageCount < 2;
  el('previous').disabled = page === 0;
  el('next').disabled = page + 1 >= pageCount;
  el('pageNumber').textContent = (page + 1) + ' / ' + pageCount;
  const entries = matching.slice(page * pageSize, (page + 1) * pageSize).map(file => ({file, ...fileCard(file)}));
  el('files').replaceChildren(...entries.map(entry => entry.card));
  loadPreviews(entries, version);
}

async function refreshFiles() {
  if (el('refresh').disabled) return;
  el('refresh').disabled = true;
  el('fileCount').textContent = 'Loading…';
  try {
    files = (await getJson('/files')).files;
    catalogLoaded = true;
    for (const promise of imageRequests.values()) promise.then(url => { if (url) URL.revokeObjectURL(url); });
    imageRequests.clear();
    previewKey = '';
    el('jobImage').hidden = true;
    el('job').dataset.preview = 'none';
    page = 0;
    updateJobPreview();
    renderFiles();
  } catch {
    el('fileCount').textContent = 'Could not load the file list';
  } finally {
    el('refresh').disabled = false;
  }
}

el('refresh').onclick = refreshFiles;
el('search').oninput = () => { page = 0; renderFiles(); };
el('previous').onclick = () => { page--; renderFiles(); };
el('next').onclick = () => { page++; renderFiles(); };
document.addEventListener('visibilitychange', updateStatus);
updateStatus();
refreshFiles();
const settingsDialog = el('settingsDialog');
function parsePrinters(value) {
  const printers = [];
  for (const line of value.split(/\r?\n/)) {
    const parts = line.split('|').map(part => part.trim());
    if (!parts[0]) continue;
    printers.push({host: parts[0], code: parts[1] || '', name: parts[2] || '', serial: parts[3] || ''});
  }
  return printers;
}

function formatPrinters(printers) {
  return (printers || []).map(item => [item.host, item.code || '', item.name || '', item.serial || '']
    .join(' | ').replace(/(?: \| )+$/, '')).join('\n');
}

const fleetToggle = el('fleetToggle');
let fleetPrinters = [];

function renderFleet() {
  const rows = fleetPrinters.map(item => {
    const row = document.createElement('div');
    row.className = 'fleet-row' + (item.current ? ' current' : '');
    const left = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = item.name || item.host;
    const meta = document.createElement('div');
    meta.className = 'meta';
    const state = item.status || (item.error ? 'No answer' : 'Waiting…');

    const bits = [item.host, state];
    if (item.file) bits.push(item.file);
    if (item.percent != null) bits.push(item.percent + '%');
    if (item.remaining != null) bits.push(duration(item.remaining) + ' left');
    meta.textContent = bits.join(' · ');
    left.append(name, meta);
    const right = document.createElement('div');
    right.className = 'value';
    if (item.current) {
      right.textContent = 'open';
    } else {
      const link = document.createElement('a');
      link.href = '/' + item.host + '/';
      link.textContent = 'Open';
      right.append(link);
    }
    row.append(left, right);
    return row;
  });
  el('fleetList').replaceChildren(...rows);
}

function applyView() {
  const hasPrinters = fleetPrinters.length > 0;
  const showFleet = hasPrinters && fleetToggle.checked;
  const current = fleetPrinters.find(item => item.current);
  el('deviceName').textContent = showFleet ? 'FlashForge' : current?.name || 'FlashForge';
  el('setup').hidden = hasPrinters;
  el('fleet').hidden = !showFleet;
  el('monitor').hidden = !hasPrinters || showFleet;
  for (const id of ['printerTools', 'history', 'files-section', 'jobActions']) {
    const node = el(id);
    if (node) node.hidden = !hasPrinters || showFleet;
  }
  if (showFleet) renderFleet();
}

fleetToggle.checked = localStorage.getItem('ff-fleet') === '1';
fleetToggle.onchange = () => {
  try { localStorage.setItem('ff-fleet', fleetToggle.checked ? '1' : '0'); } catch {}
  applyView();
};
el('fleetRefresh').onclick = () => updateStatus();
el('pausePrint').onclick = () => runAction('pause');
el('resumePrint').onclick = () => runAction('resume');
el('cancelPrint').onclick = () => {
  const button = el('cancelPrint');
  if (!button.classList.contains('armed')) {
    button.classList.add('armed');
    button.textContent = 'Cancel print?';
    setTimeout(() => {
      if (button.classList.contains('armed')) {
        button.classList.remove('armed');
        button.textContent = 'Cancel';
      }
    }, 5000);
    return;
  }
  button.classList.remove('armed');
  button.textContent = 'Cancel';
  runAction('cancel');
};
el('cameraSnapshot').onclick = downloadSnapshot;

el('settingsButton').onclick = async () => {
  el('settingsStatus').textContent = '';
  settingsDialog.showModal();
  try {
    const data = await getJson('/settings');
    el('printersText').value = formatPrinters(data.printers);
    el('printersText').focus();
  } catch (error) {
    el('settingsStatus').textContent = error.message || 'Could not read the settings.';
  }
};
el('settingsCancel').onclick = () => settingsDialog.close();
el('settingsForm').onsubmit = async event => {
  event.preventDefault();
  const status = el('settingsStatus');
  const save = el('settingsSave');
  save.disabled = true;
  status.textContent = 'Saving…';
  try {
    const response = await fetch(endpoint('/settings'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-FlashForge-Tab-Action': 'settings'},
      body: JSON.stringify({printers: parsePrinters(el('printersText').value)}),
      signal: AbortSignal.timeout(12000),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Could not save the settings.');
    settingsDialog.close();
    await updateStatus();
  } catch (error) {
    status.textContent = error.message || 'Could not save the settings.';
  } finally {
    save.disabled = false;
  }
};
applyView();
setInterval(updateStatus, 5000);
</script>
</body>
</html>'''

CONFIG_PAGE = r'''<!doctype html>
<html lang="en">
<meta charset="utf-8">
<style>
body { font: 14px "Segoe UI", system-ui, "Noto Sans", "DejaVu Sans", sans-serif; color: var(--orca-fg, #eee); margin: 12px; }
label { display: block; margin: 8px 0 5px; }
input, button { font: inherit; padding: 6px 8px; }
p { max-width: 520px; }
</style>
<div>
  <label for="flashforgeHost">Printer IPv4 address</label>
  <input type="text" id="flashforgeHost" placeholder="192.168.1.51" maxlength="15" autocomplete="off">
  <label for="code">CheckCode</label>
  <input type="password" id="code" maxlength="32" autocomplete="off">
  <label for="serial">Serial number (required for Creator 5 / 5 Pro)</label>
  <input type="text" id="serial" maxlength="64" autocomplete="off">
  <p>From the Device tab, open Speed Dial and choose FlashForge Device Tab.</p>
  <p>Allow OrcaSlicer to access your printer when prompted, then reload the plugin.</p>
  <button id="save" type="button">Save</button>
  <span id="saved" role="status"></span>
</div>
<script>
const byId = id => document.getElementById(id);
const fields = {flashforge_host: byId('flashforgeHost'), check_code: byId('code'), serial_number: byId('serial')};
const saved = byId('saved');
let pending = false;
let printers = [];
window.orca.onConfig(config => {
  printers = Array.isArray(config.printers) ? config.printers : [];
  const first = printers[0];
  fields.flashforge_host.value = first?.host || config.flashforge_host || '';
  fields.check_code.value = first?.code ?? config.check_code ?? '';
  fields.serial_number.value = first?.serial ?? config.serial_number ?? '';
  if (pending) saved.textContent = 'Saved';
  pending = false;
});
for (const field of Object.values(fields)) field.oninput = () => { saved.textContent = ''; };
byId('save').onclick = () => {
  pending = true;
  const config = Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.value.trim()]));
  if (printers.length) {
    config.printers = [{...printers[0], host: config.flashforge_host, code: config.check_code,
      serial: config.serial_number}, ...printers.slice(1)];
  }
  window.orca.saveConfig(config);
};
</script>
</html>'''

STATES = {
    "READY": "Ready", "IDLE": "Ready", "BUSY": "Busy",
    "PRINTING": "Printing", "BUILDING": "Printing", "BUILDING_FROM_SD": "Printing",
    "PAUSED": "Paused", "ERROR": "Error",
    "COMPLETED": "Completed", "BUILDING_COMPLETED": "Completed",
    "HEATING": "Heating", "PAUSING": "Pausing",
    "CANCELING": "Canceling", "CANCELLING": "Canceling",
    "CANCELLED": "Canceled", "CANCELED": "Canceled",
    "FINISHED": "Completed", "STOPPED": "Canceled", "ATTENTION": "Needs Attention",
}
ACTIVE_JOB = {"PRINTING", "BUILDING", "BUILDING_FROM_SD", "PAUSED", "HEATING", "PAUSING"}
ENDED_JOB = {"CANCELLED": "cancelled", "CANCELED": "cancelled", "STOPPED": "cancelled", "ERROR": "error"}
ACK = re.compile(rb"(?:^|\n)ok\r?\n")
TEMP_ACK = re.compile(rb"(?:^|\n)ok[ \t]+T0:[^\r\n]+\r?\n")
LIST_HEADER = b"D\xaa\xaaD"
LIST_ENTRY = b"::\xa3\xa3"
IMAGE_FRAME = b"**\xa2\xa2"


def command_reply(sock, request, deadline=None):
    if deadline is None:
        deadline = time.monotonic() + 2
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("The printer did not finish sending data")
    sock.settimeout(remaining)
    sock.sendall(b"~" + request + b"\r\n")
    data = bytearray()
    while len(data) < 16384:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("The printer did not finish sending data")
        sock.settimeout(remaining)
        block = sock.recv(4096)
        if not block:
            break
        data.extend(block)
        ack = ACK.search(data)
        if ack:
            return bytes(data[:ack.start()]), bytearray(data[ack.end():])
        if request == b"M105":
            inline = TEMP_ACK.search(data)
            if inline:
                return bytes(data[:inline.end()]), bytearray(data[inline.end():])
    raise ValueError("Incomplete response from the printer")


def command(sock, name, deadline=None):
    reply, _ = command_reply(sock, name.encode("ascii"), deadline)
    return reply.decode("utf-8", "replace")


def payloads(sock, data, limit, idle=READ_IDLE, total=READ_TOTAL):
    """Yield the reply as it grows.

    Each read gets its own idle timeout, so a slow but steady transfer still
    completes; the total cap only stops an endless trickle.
    """
    started = time.monotonic()
    while True:
        if len(data) > limit:
            raise ValueError("The printer response is too large")
        yield data
        remaining_total = total - (time.monotonic() - started)
        if remaining_total <= 0:
            raise TimeoutError("The printer did not finish sending data")
        sock.settimeout(min(idle, remaining_total))
        try:
            block = sock.recv(8192)
        except TimeoutError:
            raise TimeoutError("The printer stopped sending data") from None
        if not block:
            return
        data.extend(block)


def decode_file_list(data):
    if len(data) < 8:
        return None
    if not data.startswith(LIST_HEADER):
        raise ValueError("Unrecognized file list format")
    count = int.from_bytes(data[4:8], "big")
    files = {}
    offset = 8
    for _ in range(count):
        if offset + 8 > len(data):
            return None
        if data[offset:offset + 4] != LIST_ENTRY:
            raise ValueError("Invalid entry in the file list")
        size = int.from_bytes(data[offset + 4:offset + 8], "big")
        offset += 8
        if offset + size > len(data):
            return None
        path = bytes(data[offset:offset + size])
        offset += size
        if path.startswith(b"/") and not any(c in path for c in (b"\0", b"\r", b"\n")):
            files[hashlib.sha256(path).hexdigest()[:32]] = path
    return files


def read_files(host):
    with socket.create_connection((host, PORT), timeout=2) as sock:
        _, data = command_reply(sock, b"M661")
        for payload in payloads(sock, data, 1024 * 1024):
            files = decode_file_list(payload)
            if files is not None:
                return files
    raise ValueError("Incomplete file list")


def read_preview(host, path):
    with socket.create_connection((host, PORT), timeout=2) as sock:
        _, data = command_reply(sock, b"M662 " + path)
        try:
            for payload in payloads(sock, data, MAX_PREVIEW + 8):
                if payload.startswith(IMAGE_FRAME) and len(payload) >= 8:
                    size = int.from_bytes(payload[4:8], "big")
                    if not 0 < size <= MAX_PREVIEW:
                        return None
                    if len(payload) >= 8 + size:
                        image = bytes(payload[8:8 + size])
                        return image if image.startswith(PNG_HEADER) and image.endswith(PNG_END) else None
                elif payload.startswith(PNG_HEADER) and payload.endswith(PNG_END):
                    return bytes(payload)
        except TimeoutError:
            pass
    return None


def history_stats(jobs, now):
    finished = [job for job in jobs
                if job.get("result") in ("complete", "cancelled", "error")]
    seconds = 0
    for job in jobs:
        elapsed = job.get("elapsed")
        if not isinstance(elapsed, int) or elapsed < 0:
            continue
        seconds += elapsed if job.get("result") != "active" else max(0, now - job.get("started", now))
    complete = sum(1 for job in finished if job.get("result") == "complete")
    return {
        "jobs": len(jobs),
        "finished": len(finished),
        "complete": complete,
        "successRate": round(complete * 100 / len(finished)) if finished else None,
        "seconds": seconds,
        "last": jobs[0].get("file", "") if jobs else "",
    }


class PrinterAPIError(ValueError):
    pass


def printer_api(host, serial, check_code, path, fields=None, timeout=3, limit=65536):
    body = json.dumps({"serialNumber": serial, "checkCode": check_code, **(fields or {})})
    connection = http.client.HTTPConnection(host, API_PORT, timeout=timeout)
    try:
        connection.request("POST", path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise PrinterAPIError(f"The printer HTTP API returned status {response.status}.")
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise PrinterAPIError("The printer HTTP response is too large.")
        result = json.loads(raw)
    except (ValueError, RecursionError) as error:
        if isinstance(error, PrinterAPIError):
            raise
        raise PrinterAPIError("The printer returned invalid HTTP data.") from error
    except http.client.HTTPException as error:
        raise OSError("The printer HTTP connection failed.") from error
    finally:
        connection.close()

    if not isinstance(result, dict) or type(result.get("code")) is not int:
        raise PrinterAPIError("The printer returned invalid HTTP data.")
    code = result["code"]
    message = result.get("message")
    if code == -2:
        raise PrinterAPIError("Enable LAN-only mode on the printer.")
    if code == 1:
        if message == "SN is different":
            raise PrinterAPIError("Check the printer serial number in the plugin settings.")
        if message == "Access code is different":
            raise PrinterAPIError("Check the CheckCode in the plugin settings.")
        raise PrinterAPIError("Check the serial number and CheckCode in the plugin settings.")
    if code:
        raise PrinterAPIError(f"The printer rejected the HTTP request (code {code}).")
    return result


def control_job(host, serial, check_code, action):
    """Pause, resume or cancel the current job through the printer's HTTP API."""
    printer_api(host, serial, check_code, "/control",
                {"payload": {"cmd": "jobCtl_cmd", "args": {"jobID": "", "action": action}}})


def read_snapshot(camera_url, limit=2_000_000):
    """One JPEG frame from the printer's MJPEG stream."""
    parts = urlsplit(camera_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=3)
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            return None
        buffer = b""
        while len(buffer) < limit:
            block = response.read(8192)
            if not block:
                break
            buffer += block
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                buffer = buffer[-2:]
                continue
            end = buffer.find(b"\xff\xd9", start + 2)
            if end > 0:
                return buffer[start:end + 2]
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()
    return None


def read_job_detail(serial, check_code, host):
    try:
        return read_http_detail(host, serial, check_code), ""
    except PrinterAPIError as error:
        return {}, str(error)
    except OSError:
        return {}, "The printer API is unavailable"


def read_http_detail(host, serial, check_code):
    detail = printer_api(host, serial, check_code, "/detail", timeout=1.5).get("detail")
    if not isinstance(detail, dict):
        raise PrinterAPIError("The printer returned invalid status data.")
    return detail


def detail_number(detail, key, lower, upper):
    if isinstance(detail.get(key), bool):
        return None
    try:
        value = float(detail[key])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return value if lower <= value <= upper else None


def read_http_status(host, serial, check_code):
    detail = read_http_detail(host, serial, check_code)

    def field(key):
        value = detail.get(key)
        return value.strip() if isinstance(value, str) else ""

    state = field("status").upper()
    state = {"PAUSE": "PAUSED", "CANCEL": "CANCELING", "CALIBRATE_DOING": "BUSY"}.get(state, state)
    if not state:
        raise PrinterAPIError("The printer returned invalid status data.")
    complete = state in {"COMPLETED", "BUILDING_COMPLETED", "FINISHED"}
    active = state in ACTIVE_JOB
    has_job = active or complete or state in ENDED_JOB
    model = field("model")
    pid = detail.get("pid")
    creator_pro = model.casefold() == "creator 5 pro" or pid in (41, "29", "0x29")
    creator = creator_pro or model.casefold() == "creator 5" or pid in (40, "28", "0x28")
    volume = re.fullmatch(r"(\d{1,4})[xX](\d{1,4})[xX](\d{1,4})", field("measure"))

    def tool_values(key):
        values = detail.get(key)
        if not isinstance(values, list):
            return []
        return [detail_number({index: value}, index, 0, 500)
                for index, value in enumerate(values[:4])]

    actuals = tool_values("nozzleTemps")
    targets = tool_values("nozzleTargetTemps")
    tools = [{"actual": actual, "target": target}
             for actual, target in zip((actuals + [None] * 4)[:4],
                                       (targets + [None] * 4)[:4])] if creator or actuals or targets else []
    readings = {
        "nozzle": tools[0]["actual"] if tools else detail_number(detail, "rightTemp", 0, 500),
        "nozzleTarget": tools[0]["target"] if tools else detail_number(detail, "rightTargetTemp", 0, 500),
        "bed": detail_number(detail, "platTemp", 0, 200),
        "bedTarget": detail_number(detail, "platTargetTemp", 0, 200),
    }
    chamber = {"actual": detail_number(detail, "chamberTemp", 0, 150),
               "target": detail_number(detail, "chamberTargetTemp", 0, 150)}
    camera = field("cameraStreamUrl")
    try:
        parts = urlsplit(camera)
        if parts.scheme != "http" or parts.hostname != host or parts.port != 8080 or parts.username is not None:
            camera = ""
    except ValueError:
        camera = ""
    led = field("lightStatus")
    door = field("doorStatus")
    remaining = detail_number(detail, "estimatedTime", 1e-9, 365 * 24 * 3600) if active else None
    elapsed = detail_number(detail, "printDuration", 1 if complete else 0, 365 * 24 * 3600) if has_job else None
    progress = detail_number(detail, "printProgress", 0, 1) if active else None
    total = detail_number(detail, "targetPrintLayer", 1, 1_000_000) if has_job else None
    layer = detail_number(detail, "printLayer", 0, total) if total is not None else None
    return {
        "status": STATES.get(state, state), "state": state, **readings,
        "temperatures": readings, "tools": tools, "jobControls": True,
        "chamber": chamber if creator_pro and any(value is not None for value in chamber.values()) else None,
        "layer": int(layer) if layer is not None else None,
        "layerTotal": int(total) if total is not None else None,
        "percent": 100 if complete else round(progress * 100) if progress is not None else None,
        "file": field("printFileName").rsplit("/", 1)[-1] if has_job else "",
        "active": active, "complete": complete,
        "remaining": math.ceil(remaining) if remaining is not None else None,
        "timeHint": "The printer has not reported the remaining time yet" if active and remaining is None else "",
        "elapsed": int(elapsed) if elapsed is not None else None,
        "filament": None,
        "fan": detail_number(detail, "coolingFanSpeed", 0, 100),
        "speed": detail_number(detail, "printSpeedAdjust", 1, 1000),
        "cameraUrl": camera,
        "info": {"model": model, "name": field("name"), "firmware": field("firmwareVersion"),
                 "serial": serial, "mac": field("macAddr"), "volume": list(map(int, volume.groups())) if volume else None,
                 "tools": detail_number(detail, "nozzleCnt", 1, 16)},
        "sensors": {"led": led == "open" if led in {"open", "close"} else None,
                    "door": door if creator_pro and door in {"open", "close"} else "",
                    "errorCode": field("errorCode"), "endstops": {}, "moveMode": ""},
    }


def read_status(host, check_code, include_details=False):
    with socket.create_connection((host, PORT), timeout=2) as sock:
        deadline = time.monotonic() + 2.5
        temp = command(sock, "M105", deadline)
        progress = command(sock, "M27", deadline)
        status = command(sock, "M119", deadline)
        state_match = re.search(r"^MachineStatus:[ \t]*(\S+)", status, re.M)
        if not state_match or not re.search(r"\bT0:", temp):
            raise ValueError("Unexpected response from the printer")
        state = state_match[1]
        serial = None
        machine = ""
        try:
            machine = command(sock, "M115", deadline)
        except (OSError, ValueError):
            pass
        if machine:
            serial = re.search(r"^(?:SN|Serial Number):[ \t]*([\w-]+)", machine, re.M | re.I)

    def temperature(key):
        match = re.search(r"\b" + key + r":\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)", temp)
        if not match:
            return None, None
        return float(match[1]), float(match[2])

    filename = re.search(r"^CurrentFile:[ \t]*([^\r\n]*)", status, re.M)
    layer = re.search(r"^Layer:\s*(\d+)\s*/\s*(\d+)", progress, re.M | re.I)
    current, total = (int(layer[1]), int(layer[2])) if layer else (0, 0)
    complete = state in {"COMPLETED", "BUILDING_COMPLETED"}
    active = state in ACTIVE_JOB
    percent = 100 if complete else min(100, round(current * 100 / total)) if active and total else None
    if active and percent is None:
        # Older firmware exposes progress through M27 without a layer count.
        sd = re.search(r"^SD printing byte[ \t]+(\d{1,12})/(\d{1,12})\s*$", progress, re.M | re.I)
        if sd:
            done, size = map(int, sd.groups())
            if 0 <= done <= size and size:
                percent = round(done * 100 / size)
    detail, time_hint = {}, ""
    if serial:
        detail, time_hint = read_job_detail(serial[1], check_code, host)
    elif active:
        if not check_code:
            time_hint = "Enter your CheckCode in the plugin settings"
        else:
            time_hint = "Printer serial number unavailable"

    seconds = detail_number(detail, "estimatedTime", 1e-9, 365 * 24 * 3600) if active else None
    elapsed = detail_number(detail, "printDuration", 1, 365 * 24 * 3600) if active or complete else None
    filament = detail_number(detail, "estimatedRightWeight", 1e-9, math.inf) if active else None
    if filament is not None and not math.isfinite(filament):
        filament = None
    readings = dict(zip(("nozzle", "nozzleTarget", "bed", "bedTarget"), (*temperature("T0"), *temperature("B"))))
    raw_name = filename[1].strip() if filename else ""
    detail_name = detail.get("printFileName")
    if not raw_name and isinstance(detail_name, str):
        raw_name = detail_name.strip()
    camera = detail.get("cameraStreamUrl")
    info = {}
    if machine:
        fields = {}
        for line in machine.splitlines():
            if ":" in line:
                key, _, value = line.removeprefix("echo: ").partition(":")
                fields[key.strip().lower()] = value.strip()
        build = re.search(r"^X:[ \t]*(\d+)\s*Y:\s*(\d+)\s*Z:\s*(\d+)", machine, re.M)
        info = {
            "model": fields.get("machine type", ""),
            "name": fields.get("machine name", ""),
            "firmware": fields.get("firmware", ""),
            "serial": serial[1] if serial else "",
            "mac": fields.get("mac address", ""),
            "tools": detail_number({"tools": fields.get("tool count")}, "tools", 0, 16),
            "volume": [int(build[1]), int(build[2]), int(build[3])] if build else None,
        }
    endstops = {}
    endstop_match = re.search(r"^Endstop:[ \t]*(.+)$", status, re.M)
    if endstop_match:
        for axis, key in (("x", "X-max"), ("y", "Y-max"), ("z", "Z-min")):
            match = re.search(key + r":\s*(-?\d+)", endstop_match[1], re.I)
            if match:
                endstops[axis] = int(match[1])
    led = re.search(r"^LED:[ \t]*(\d+)", status, re.M)
    move = re.search(r"^MoveMode:[ \t]*(\S+)", status, re.M)
    sensors = {"endstops": endstops,
               "led": bool(int(led[1])) if led else None,
               "moveMode": move[1] if move else ""}
    return {
        "status": STATES.get(state, state),
        "state": state,
        "nozzle": readings["nozzle"],
        "nozzleTarget": readings["nozzleTarget"],
        "bed": readings["bed"],
        "bedTarget": readings["bedTarget"],
        "temperatures": readings,
        "layer": current if layer and total and (active or complete) else None,
        "layerTotal": total if layer and total and (active or complete) else None,
        "percent": percent,
        "file": raw_name.rsplit("/", 1)[-1] if active or complete or state in ENDED_JOB else "",
        "active": active, "complete": complete,
        "jobControls": bool(serial and check_code and not time_hint),
        "remaining": math.ceil(seconds) if seconds is not None else None,
        "timeHint": time_hint or ("The printer has not reported the remaining time yet" if active and seconds is None else ""),
        "elapsed": int(elapsed) if elapsed is not None else None,
        "filament": filament,
        "fan": detail_number(detail, "coolingFanSpeed", 0, 100),
        "speed": detail_number(detail, "printSpeedAdjust", 1, 1000),
        "cameraUrl": camera if isinstance(camera, str) and camera.startswith(("http://", "https://")) else "",
        "info": info,
        "sensors": sensors,
    }


def local_address(host):
    try:
        address = ipaddress.IPv4Address((host or "").strip())
    except ipaddress.AddressValueError as error:
        raise ValueError("Enter a local IPv4 address for the printer.") from error
    # A LAN printer only: loopback stays available for local proxies and tests.
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8")
    if not any(address in ipaddress.IPv4Network(cidr) for cidr in networks):
        raise ValueError("Enter a local IPv4 address for the printer.")
    return str(address)


def printer_serial(value):
    serial = str(value or "").strip()
    if serial and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", serial):
        raise ValueError("Enter a serial number using up to 64 letters, digits, hyphens or underscores.")
    return serial


class FlashForge:
    def __init__(self, address, check_code, serial=""):
        self.host = local_address(address)
        self.http_only = False
        self.check_code = check_code
        self.serial = serial

    def configure(self, check_code, serial):
        if (check_code, serial) != (self.check_code, self.serial):
            self.http_only = False
        self.check_code = check_code
        self.serial = serial

    def status(self, include_details):
        if not self.http_only:
            try:
                return read_status(self.host, self.check_code, include_details)
            except PermissionError:
                raise
            except (OSError, ValueError):
                if not self.serial or not self.check_code:
                    raise PrinterAPIError(
                        "TCP connection failed. For Creator 5 / 5 Pro, set the serial number and CheckCode."
                    ) from None
        try:
            status = read_http_status(self.host, self.serial, self.check_code)
        except PermissionError:
            raise
        except OSError:
            self.http_only = False
            raise
        self.http_only = True
        return status

    def files(self):
        if self.http_only:
            names = printer_api(self.host, self.serial, self.check_code, "/gcodeList",
                                limit=1024 * 1024).get("gcodeList")
            if not isinstance(names, list):
                raise PrinterAPIError("The printer returned an invalid file list.")
            files = {}
            for name in names:
                if isinstance(name, str) and name and not any(c in name for c in "/\\\0\r\n"):
                    try:
                        key = hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]
                    except UnicodeEncodeError:
                        continue
                    files[key] = (name, name)
            return files
        return {
            key: (path.rsplit(b"/", 1)[-1].decode("utf-8", "replace"), path)
            for key, path in read_files(self.host).items()
        }

    def preview(self, path):
        if self.http_only:
            if not isinstance(path, str):
                return None
            result = printer_api(self.host, self.serial, self.check_code, "/gcodeThumb",
                                 {"fileName": path}, limit=4 * ((MAX_PREVIEW + 2) // 3) + 4096)
            encoded = result.get("imageData")
            if not isinstance(encoded, str) or not encoded:
                return None
            try:
                image = base64.b64decode(encoded, validate=True)
            except ValueError:
                return None
            if len(image) <= MAX_PREVIEW and preview_type(image):
                return image
            return None
        if not isinstance(path, bytes):
            return None
        return read_preview(self.host, path)


def preview_type(image):
    if image and image.startswith(PNG_HEADER) and image.endswith(PNG_END):
        return "image/png"
    if image and image.startswith(b"\xff\xd8\xff") and image.endswith(b"\xff\xd9"):
        return "image/jpeg"
    return None


class PrintHistory:
    def __init__(self, path):
        self.path = path
        self.jobs = []
        self.error = ""
        self.writable = True
        self.dirty = False
        self.saved_at = 0
        try:
            jobs = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(jobs, list):
                raise ValueError("Invalid print history format")
            for job in jobs[:HISTORY_LIMIT]:
                if (not isinstance(job, dict) or type(job.get("started")) is not int
                        or not 0 <= job["started"] <= 253402300799
                        or job.get("result") not in {"active", "complete", "cancelled", "error", "unknown"}
                        or not isinstance(job.get("file", ""), str)
                        or any(job.get(field) is not None
                               and (type(job[field]) is not int or job[field] < 0)
                               for field in ("elapsed", "layer", "seen"))):
                    raise ValueError("Invalid print history format")
                # Old or incomplete jobs can still be displayed. Without a last
                # observation, their result cannot be inferred from the next poll.
                if job["result"] == "active" and job.get("seen") is None:
                    job["result"] = "unknown"
                for field, default in (("file", ""), ("elapsed", None), ("layer", None)):
                    job.setdefault(field, default)
                self.jobs.append(job)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.jobs = []
            self.writable = False
            self.error = "The saved print history is damaged. New entries are available for this session."

    def save(self, now):
        if not self.dirty or not self.writable:
            return
        self.saved_at = now
        pending = self.path.with_suffix(".tmp")
        try:
            pending.write_text(json.dumps(self.jobs, ensure_ascii=False), encoding="utf-8")
            os.replace(pending, self.path)
        except OSError:
            self.error = "Could not save print history. New entries are still available for this session."
        else:
            self.error = ""
            self.dirty = False

    def observe(self, status, now):
        job = self.jobs[0] if self.jobs and self.jobs[0]["result"] == "active" else None
        name = status["file"]
        elapsed, layer = status["elapsed"], status["layer"]
        state = status["state"]
        write_now = False
        if job:
            changed = bool(job["file"] and name and job["file"] != name)
            reset = status["active"] and (
                (elapsed is not None and job["elapsed"] is not None and elapsed + 10 < job["elapsed"])
                or (layer is not None and job["layer"] is not None and layer + 1 < job["layer"])
            )
            gap = now - job["seen"] > MAX_JOB_GAP
            if (gap and (status["active"] or status["complete"]) and name and name == job["file"]
                    and elapsed is not None and job["elapsed"] is not None):
                # A continuing job advances by the time we were away. A new
                # run of the same file has a different inferred start time.
                gained = elapsed - job["elapsed"]
                gap = abs(gained - (now - job["seen"])) > 10 or elapsed <= now - job["seen"]
            if changed or reset or gap:
                job["result"] = "unknown"
            elif status["complete"]:
                job["result"] = "complete"
            elif state in ENDED_JOB:
                job["result"] = ENDED_JOB[state]
            elif state in {"READY", "IDLE"}:
                job["result"] = "unknown"
            if job["result"] != "active":
                if job["result"] == "complete" and elapsed is not None:
                    job["elapsed"] = elapsed
                self.dirty = write_now = True
                job = None

        if status["active"] and not job:
            job = {"file": name, "started": now - (elapsed or 0), "seen": now,
                   "elapsed": elapsed, "layer": layer, "result": "active"}
            self.jobs.insert(0, job)
            del self.jobs[HISTORY_LIMIT:]
            self.dirty = write_now = True
        elif status["active"]:
            job["file"] = name or job["file"]
            job["seen"] = now
            job["elapsed"] = elapsed if elapsed is not None else job["elapsed"]
            job["layer"] = layer if layer is not None else job["layer"]
            self.dirty = True
        if write_now or now - self.saved_at >= HISTORY_CHECKPOINT:
            self.save(now)


class PrinterMonitor:
    def __init__(self, adapter, history_path):
        self.adapter = adapter
        self.history = PrintHistory(history_path)
        self.temperatures = deque(maxlen=361)
        self.files = {}
        self.latest = None
        self.camera_url = ""
        self.next_camera_probe = 0
        self.error = "Connecting to the printer…"
        self.permission_denied = False
        self.state_lock = threading.Lock()
        self.history_snapshot = [job.copy() for job in self.history.jobs]
        self.history_error = self.history.error

    def sample(self, now):
        if self.permission_denied:
            return
        probe_camera = time.monotonic() >= self.next_camera_probe
        try:
            status = self.adapter.status(include_details=probe_camera)
        except PermissionError:
            with self.state_lock:
                self.latest = None
                self.permission_denied = True
                self.error = "Allow access to the printer, then reload the plugin."
        except PrinterAPIError as error:
            with self.state_lock:
                self.latest = None
                self.error = str(error)
        except (OSError, ValueError):
            with self.state_lock:
                self.latest = None
                self.error = "The printer did not respond"
        else:
            self.history.observe(status, now)
            with self.state_lock:
                self.latest = status
                self.temperatures.append({"t": now * 1000, **status["temperatures"]})
                self.history_snapshot = [job.copy() for job in self.history.jobs]
                self.history_error = self.history.error
                if probe_camera or status["cameraUrl"]:
                    self.camera_url = status["cameraUrl"]
                    self.next_camera_probe = time.monotonic() + 60


class UploadInProgress(Exception):
    pass


WEB_HOST = "127.0.0.1"
WEB_PORT = 18771
DEVICE_UI_KEY = "print_host_webui"
MONITOR_TITLE = "FlashForge"


def monitor_url(endpoint=""):
    path = f"{WEB_PORT}/{endpoint}/" if endpoint else f"{WEB_PORT}/"
    return f"http://{WEB_HOST}:{path}"


def monitor_identity(url):
    try:
        url = urlsplit(url)
    except ValueError:
        return None
    if url.scheme != "http" or url.netloc != f"{WEB_HOST}:{WEB_PORT}":
        return None
    parts = url.path.strip("/").split("/")
    if not parts[0]:
        return ""
    try:
        return local_address(parts[0]) if len(parts) == 1 else None
    except ValueError:
        return None


def printer_endpoint(print_host):
    try:
        url = urlsplit(print_host if "://" in print_host else "http://" + print_host)
    except ValueError as error:
        raise ValueError("Enter a local IPv4 address for the printer.") from error
    return local_address(url.hostname)


class LoopbackServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    restore_page = None
    check_view = None

    def configure(self, get_config, storage):
        self.get_config = get_config
        self.storage = storage
        self.monitors = {}
        self.error = ""
        self.state_lock = threading.Lock()
        self.printer_lock = threading.Lock()
        self.apply_settings = None
        self.notify = None
        self.uploading = threading.Event()
        self.uploaded_at = 0.0
        self.stopping = threading.Event()
        self.poller = None

    def printers(self):
        """Configured printers and their plugin credentials."""
        config = self.get_config()
        result = []
        for entry in config.get("printers") or []:
            if not isinstance(entry, dict):
                continue
            try:
                host = local_address(str(entry.get("host") or ""))
            except ValueError:
                continue
            if any(item["host"] == host for item in result):
                continue
            result.append({"host": host, "code": str(entry.get("code") or ""),
                           "name": str(entry.get("name") or host)[:60],
                           "serial": str(entry.get("serial") or "").strip()})
        if not result:
            # Older releases kept a single address in the plugin config.
            try:
                host = local_address(config.get("flashforge_host") or "")
            except ValueError:
                host = ""
            if host:
                result.append({"host": host, "code": config.get("check_code") or "",
                               "name": "Printer", "serial": config.get("serial_number") or ""})
        return result

    def settings_for(self):
        printers = self.printers()
        return (printers[0]["host"], printers[0]["code"]) if printers else ("", "")

    def settings_payload(self):
        printers = self.printers()
        return {"printers": printers, "endpoint": printers[0]["host"] if printers else ""}

    def fleet_payload(self, current=None):
        items = []
        for entry in self.printers():
            monitor = self.monitors.get(entry["host"])
            data, error = None, ""
            if monitor is not None:
                with monitor.state_lock:
                    data = monitor.latest
                    error = monitor.error
            item = {"host": entry["host"], "name": entry["name"],
                    "current": entry["host"] == current, "error": error,
                    "status": None, "percent": None, "remaining": None,
                    "file": "", "nozzle": None, "bed": None,
                    "printing": False, "complete": False}
            if data:
                item.update(status=data.get("status"), percent=data.get("percent"),
                            remaining=data.get("remaining"), file=data.get("file"),
                            nozzle=data.get("nozzle"), bed=data.get("bed"),
                            printing=bool(data.get("active")), complete=bool(data.get("complete")))
            items.append(item)
        return items

    def upload_active(self):
        # A cancelled or crashed Orca upload may never deliver UploadFinished;
        # without this the monitor would answer 503 until the plugin reloads.
        if self.uploading.is_set() and time.monotonic() - self.uploaded_at > UPLOAD_TIMEOUT:
            self.uploading.clear()
        return self.uploading.is_set()

    @contextmanager
    def printer_io(self):
        if self.upload_active() or self.stopping.is_set():
            raise UploadInProgress
        with self.printer_lock:
            if self.upload_active() or self.stopping.is_set():
                raise UploadInProgress
            yield

    def begin_upload(self):
        # The Orca upload worker calls this before opening its printer connection.
        # Drain the in-flight request, but never hold the caller for long: the
        # port has a single client, so a bounded wait is better than a GUI stall.
        self.uploading.set()
        self.uploaded_at = time.monotonic()
        acquired = self.printer_lock.acquire(timeout=UPLOAD_DRAIN)
        if acquired:
            self.printer_lock.release()

    def poll_printer(self):
        try:
            while not self.stopping.is_set():
                now = int(time.time())
                mono = time.monotonic()
                try:
                    entries = self.printers()
                    hosts = {entry["host"] for entry in entries}
                    with self.state_lock:
                        for host in [name for name in self.monitors if name not in hosts]:
                            self.monitors.pop(host).history.save(now)
                        for entry in entries:
                            monitor = self.monitors.get(entry["host"])
                            if monitor is None:
                                try:
                                    adapter = FlashForge(entry["host"], entry["code"], entry["serial"])
                                except ValueError:
                                    continue
                                monitor = PrinterMonitor(
                                    adapter, self.storage / f"flashforge-{entry['host']}-history.json")
                                monitor.endpoint = entry["host"]
                                monitor.next_due = 0.0
                                self.monitors[entry["host"]] = monitor
                            monitor.name = entry["name"]
                    for position, entry in enumerate(entries):
                        monitor = self.monitors.get(entry["host"])
                        if monitor is None or mono < getattr(monitor, "next_due", 0.0):
                            continue
                        # The first printer is the one usually on screen.
                        interval = POLL_INTERVAL if position == 0 or len(entries) <= 3 else IDLE_POLL_INTERVAL
                        monitor.next_due = mono + interval
                        with self.printer_io():
                            monitor.adapter.configure(entry["code"], entry["serial"])
                            monitor.sample(now)
                        if self.notify is not None:
                            try:
                                self.notify(monitor)
                            except Exception:
                                pass
                except UploadInProgress:
                    pass
                except Exception:
                    for monitor in list(self.monitors.values()):
                        with monitor.state_lock:
                            monitor.latest = None
                            monitor.error = "Could not update printer data."
                self.stopping.wait(0.5)
        finally:
            for monitor in list(self.monitors.values()):
                monitor.history.save(int(time.time()))

    def serve_forever(self, poll_interval=0.5):
        worker = threading.Thread(target=self.poll_printer, name="FlashForge Device Tab poller", daemon=True)
        worker.start()
        self.poller = worker
        super().serve_forever(poll_interval)

    def shutdown(self):
        self.stopping.set()
        super().shutdown()

    def server_close(self):
        if hasattr(self, "stopping"):
            self.stopping.set()
        super().server_close()
        if self.poller is not None:
            self.poller.join(timeout=3)

    def server_bind(self):
        # HTTPServer.server_bind() resolves the host name. A numeric loopback
        # listener only needs the socket permission for its configured address.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


class AliasServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class AliasRedirectHandler(BaseHTTPRequestHandler):
    """Answers on the ports older releases used and forwards to the panel."""

    def do_GET(self):
        service = FlashForgeService.instance
        server = service._server if service is not None else None
        host = ""
        for part in [part for part in self.path.partition("?")[0].split("/") if part]:
            try:
                host = local_address(part)
                break
            except ValueError:
                continue
        if host:
            target = monitor_url(host)
        elif server is not None:
            target = monitor_url(server.settings_for()[0] or "")
        else:
            target = monitor_url()
        self.send_response(308)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        pass


class MonitorHandler(BaseHTTPRequestHandler):
    def request_route(self):
        path = self.path.partition("?")[0]
        endpoint, _, route = path.lstrip("/").partition("/")
        return endpoint, route

    def do_POST(self):
        authority = f"{WEB_HOST}:{WEB_PORT}"
        action = self.headers.get("X-FlashForge-Tab-Action")
        if (self.headers.get("Host") != authority
                or self.headers.get("Origin") != f"http://{authority}"
                or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin")
                or action not in ("restore", "settings", "action")):
            self.reply_json({"error": "Open the monitor from the Device tab."}, 403)
            return
        endpoint, route = self.request_route()
        if route == "settings" and action == "settings":
            self.save_settings()
            return
        if route == "action" and action == "action":
            self.run_action(endpoint)
            return
        if route != "native" or action != "restore" or self.server.restore_page is None:
            self.reply_json({"error": "The printer web interface is unavailable."}, 404)
            return
        try:
            self.server.restore_page(endpoint)
        except (OSError, RuntimeError, ValueError) as error:
            self.reply_json({"error": str(error)}, 409)
        else:
            self.reply_json({})

    def run_action(self, endpoint):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= 1024:
            self.reply_json({"error": "Invalid request."}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeError):
            self.reply_json({"error": "Invalid request."}, 400)
            return
        command = payload.get("action") if isinstance(payload, dict) else None
        act = getattr(self.server, "act", None)
        if act is None:
            self.reply_json({"error": "Printer control is unavailable."}, 503)
            return
        try:
            state = act(endpoint, str(command or ""))
        except ValueError as error:
            self.reply_json({"error": str(error)}, 400)
            return
        except (OSError, RuntimeError) as error:
            self.reply_json({"error": str(error)}, 503)
            return
        self.reply_json({"state": state})

    def save_settings(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= 4096:
            self.reply_json({"error": "Invalid request."}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeError):
            self.reply_json({"error": "Invalid request."}, 400)
            return
        if not isinstance(payload, dict):
            self.reply_json({"error": "Invalid request."}, 400)
            return
        apply = self.server.apply_settings
        if apply is None:
            self.reply_json({"error": "Settings are unavailable."}, 503)
            return
        try:
            previous = self.server.settings_for()[0]
            endpoint = apply(payload.get("printers"))
        except ValueError as error:
            self.reply_json({"error": str(error)}, 400)
            return
        except (OSError, RuntimeError) as error:
            self.reply_json({"error": str(error)}, 409)
            return
        self.reply_json({"endpoint": endpoint, "moved": endpoint != previous})

    def do_GET(self):
        if self.headers.get("Host") != f"{WEB_HOST}:{WEB_PORT}":
            self.send_error(403)
            return
        endpoint, route = self.request_route()
        printers = self.server.printers()
        configured = printers[0]["host"] if printers else ""
        if not endpoint:
            if configured:
                self.send_response(308)
                self.send_header("Location", monitor_url(configured))
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.reply_json({"error": "Enter your printer address in the plugin settings."}, 503)
            return
        if not route:
            if not self.path.partition("?")[0].endswith("/"):
                self.send_response(308)
                self.send_header("Location", monitor_url(endpoint))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            page = PAGE.replace("{{printer}}", MONITOR_TITLE)
            self.reply(page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            self.send_error(403)
            return
        if route == "settings":
            self.reply_json(self.server.settings_payload())
            return

        try:
            if self.server.check_view is not None:
                endpoint = self.server.check_view(endpoint)
            if printers and endpoint not in {entry["host"] for entry in printers}:
                raise ValueError("This printer is not in the plugin settings.")
        except (OSError, RuntimeError, ValueError) as error:
            self.reply_json({"error": str(error)}, 409)
            return

        try:
            monitor = self.server.monitors.get(endpoint)
            uploading = self.server.upload_active()
            if monitor is None:
                if route == "telemetry":
                    self.reply_json({"temperatures": [], "history": [], "historyError": "",
                                     "cameraUrl": "", "printers": self.server.fleet_payload(endpoint)})
                else:
                    self.reply_json({"error": "This printer is not configured yet."}, 503)
                return
            if route == "telemetry":
                with monitor.state_lock:
                    data = {"temperatures": list(monitor.temperatures),
                            "history": monitor.history_snapshot,
                            "historyError": monitor.history_error,
                            "cameraUrl": monitor.camera_url}
                data["stats"] = history_stats(monitor.history_snapshot, int(time.time()))
                data["printers"] = self.server.fleet_payload(endpoint)
                self.reply_json(data)
                return
            if uploading:
                raise UploadInProgress
            with monitor.state_lock:
                data = monitor.latest
                error = monitor.error
            if data is None:
                self.reply_json({"error": error}, 503)
                return
            if route == "status":
                self.reply_json(data)
                return
            if route == "files":
                with self.server.printer_io():
                    files = monitor.adapter.files()
                with monitor.state_lock:
                    monitor.files = files
                self.reply_json({"files": [{"id": key, "name": name} for key, (name, _) in files.items()]})
                return
            if route == "snapshot":
                camera = monitor.camera_url
                if not camera:
                    self.reply_json({"error": "This printer has no camera."}, 404)
                    return
                with self.server.printer_io():
                    image = read_snapshot(camera)
                if not image:
                    self.reply_json({"error": "Could not read the camera."}, 503)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(image)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Disposition", 'inline; filename="snapshot.jpg"')
                self.end_headers()
                self.wfile.write(image)
                return
            if route.startswith("preview/"):
                key = route.removeprefix("preview/")
                with monitor.state_lock:
                    entry = monitor.files.get(key)
                if entry is not None:
                    with self.server.printer_io():
                        image = monitor.adapter.preview(entry[1])
                    media_type = preview_type(image)
                    self.reply(image if media_type else b"", media_type or "image/png", 200 if media_type else 404)
                    return
        except UploadInProgress:
            self.reply_json({"error": "OrcaSlicer is sending a print job. Monitoring will resume when the transfer is complete."}, 503)
            return
        except PrinterAPIError as error:
            self.reply_json({"error": str(error)}, 503)
            return
        except (OSError, ValueError, RuntimeError):
            self.reply_json({"error": "The printer did not respond or returned incomplete data"}, 503)
            return
        except Exception:
            self.reply_json({"error": "Could not read printer data."}, 500)
            return
        self.send_error(404)

    def snapshot(self):
        with self.server.state_lock:
            return None, self.server.error, self.server.upload_active()

    def reply_json(self, data, status=200):
        self.reply(json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def reply(self, body, content_type, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, TimeoutError):
            pass

    def log_message(self, format, *args):
        pass


class FlashForgeService(orca.script.ScriptPluginCapabilityBase):
    _server = None
    instance = None

    def get_name(self):
        return 'FlashForge Device Tab'

    def get_default_config(self):
        return {"printers": [], "flashforge_host": "", "check_code": "", "serial_number": ""}

    def has_config_ui(self):
        return True

    def get_config_ui(self):
        return CONFIG_PAGE

    def config(self):
        try:
            config = json.loads(self.get_config())
        except (TypeError, ValueError):
            return self.get_default_config()
        if not isinstance(config, dict):
            return self.get_default_config()
        result = {name: config[name].strip() if isinstance(config.get(name), str) else ""
                  for name in ("flashforge_host", "check_code", "serial_number")}
        result["printers"] = config.get("printers") if isinstance(config.get("printers"), list) else []
        return result

    def on_load(self):
        # Bind during the lifecycle callback so OrcaSlicer reports startup
        # errors in plugin diagnostics instead of losing them in a worker.
        storage = Path(orca.host.plugin.storage())
        self._page_store = storage / "device-pages.json"
        self._view_store = storage / "device-views.json"
        self._switch_lock = threading.Lock()
        self._stopping = threading.Event()
        self._view_refresh = threading.Event()
        server = LoopbackServer((WEB_HOST, WEB_PORT), MonitorHandler)
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.2},
                                  name='FlashForge Device Tab HTTP', daemon=True)
        try:
            server.configure(self.config, storage)
            server.restore_page = self.restore_page
            server.act = self.act
            server.notify = self.notify
            server.check_view = self.check_view
            server.apply_settings = self.apply_settings
            worker.start()
        except Exception:
            server.server_close()
            raise
        for port in (18769, 18765):
            try:
                alias = AliasServer((WEB_HOST, port), AliasRedirectHandler)
            except OSError:
                continue
            threading.Thread(target=alias.serve_forever, kwargs={'poll_interval': 0.5},
                             name=f'FlashForge Device Tab alias {port}', daemon=True).start()
            self._aliases = getattr(self, "_aliases", [])
            self._aliases.append(alias)
        self._server = server
        FlashForgeService.instance = self
        try:
            self.adopt_profile_address()
        except Exception:
            pass
        # Preset transactions block while Orca dispatches to the UI. Never run
        # them inside on_load or a lifecycle callback, which may own that UI.
        self._view_worker = threading.Thread(target=self._restore_views,
                                            name='FlashForge Device page', daemon=True)
        self._view_worker.start()
        self._view_refresh.set()

    def _restore_views(self):
        while True:
            self._view_refresh.wait()
            self._view_refresh.clear()
            if self._stopping.is_set():
                return
            for _ in range(10):
                try:
                    self.restore_selected_view()
                    break
                except (OSError, RuntimeError, ValueError):
                    # Startup and rapid preset changes can briefly invalidate
                    # the target. Retry with a fresh snapshot, off the UI thread.
                    if self._stopping.wait(0.1):
                        return

    def saved_views(self):
        try:
            views = json.loads(self._view_store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(views, dict):
            return {}
        return {name: view for name, view in views.items()
                if isinstance(view, dict) and isinstance(view.get("host"), str)
                and isinstance(view.get("monitor"), bool)}

    def restore_selected_view(self):
        server = self._server
        if server is None or self._stopping.is_set():
            return
        preset = orca.host.preset_bundle().current_printer_preset()
        if preset.config_value("host_type") != "flashforge":
            return
        endpoint = printer_endpoint(preset.config_value("print_host") or "")
        if not any(item["host"] == endpoint for item in server.printers()):
            return
        choice = self.saved_views().get(preset.name, {})
        show_monitor = choice.get("monitor", True) if choice.get("host") == endpoint else True
        current_url = preset.config_value(DEVICE_UI_KEY) or ""
        if (show_monitor and current_url == monitor_url(endpoint)
                or not show_monitor and monitor_identity(current_url) is None):
            return
        expected = (preset.name, endpoint, current_url)
        self.switch_view(show_monitor=show_monitor, remember=False, expected_profile=expected)

    def adopt_profile_address(self):
        """First run after an upgrade: take the address from the active profile."""
        config = self.config()
        if config.get("flashforge_host"):
            return
        preset = orca.host.preset_bundle().current_printer_preset()
        if preset.config_value("host_type") != "flashforge":
            return
        print_host = preset.config_value("print_host") or ""
        if not print_host:
            return
        host = printer_endpoint(print_host)
        self.save_config(json.dumps({"flashforge_host": host,
                                     "check_code": config.get("check_code", ""),
                                     "serial_number": config.get("serial_number", "")},
                                    ensure_ascii=False))

    def on_unload(self):
        self._stopping.set()
        self._view_refresh.set()
        # Do not join the page worker here: it may be waiting for the UI thread
        # that is unloading us. It checks _stopping before committing a change.
        for alias in getattr(self, "_aliases", []):
            alias.shutdown()
            alias.server_close()
        self._aliases = []
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def on_lifecycle_event(self, event, context):
        server = self._server
        if server is None:
            return
        if event == orca.LifecycleEvent.UploadStarted:
            server.begin_upload()
        elif event == orca.LifecycleEvent.UploadFinished:
            server.uploading.clear()
        elif event in (orca.LifecycleEvent.PresetSelected, orca.LifecycleEvent.ProjectOpened,
                       orca.LifecycleEvent.NewProject):
            self._view_refresh.set()

    def check_view(self, endpoint):
        preset = orca.host.preset_bundle().current_printer_preset()
        if preset.config_value("host_type") != "flashforge":
            raise ValueError("Open FlashForge Device Tab for a FlashForge printer profile from Speed Dial.")
        if monitor_identity(preset.config_value(DEVICE_UI_KEY) or "") != endpoint:
            raise ValueError("Open FlashForge Device Tab for the current printer profile from Speed Dial.")
        current = printer_endpoint(preset.config_value("print_host") or "")
        if endpoint and endpoint != current:
            raise ValueError("The printer address changed. Reopen FlashForge Device Tab for the current printer.")
        return current

    def saved_pages(self):
        try:
            pages = json.loads(self._page_store.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(pages, dict) or any(not isinstance(url, str) for url in pages.values()):
            raise ValueError("Could not read the saved printer web interface URLs.")
        return pages

    def switch_view(self, restore_view=None, *, show_monitor=None, remember=True, expected_profile=None):
        if not self._switch_lock.acquire(blocking=False):
            raise ValueError("A page switch is already in progress.")
        try:
            if self._stopping.is_set():
                raise ValueError("FlashForge Device Tab is shutting down.")
            transaction = orca.host.preset.printer.begin_update()
            presets = orca.host.preset_bundle().printers
            preset = presets.edited_preset()
            if preset.name != transaction.target_name:
                raise ValueError("The active printer profile changed. Try again.")
            if preset.config_value("host_type") != "flashforge":
                raise ValueError("Select a printer profile that uses the FlashForge host.")
            current_url = preset.config_value(DEVICE_UI_KEY) or ""
            print_host = preset.config_value("print_host") or ""
            endpoint = printer_endpoint(print_host)
            if expected_profile is not None and expected_profile != (preset.name, endpoint, current_url):
                raise ValueError("The active printer profile changed. Try again.")
            current_view = monitor_identity(current_url)
            if restore_view is not None and restore_view != current_view:
                raise ValueError("This monitor page is not open for the current OrcaSlicer profile.")
            if show_monitor is None:
                show_monitor = current_view is None
            if (show_monitor and current_url == monitor_url(endpoint)
                    or not show_monitor and current_view is None):
                return "The selected Device page is already open."
            if not show_monitor:
                # A damaged cache or an older URL can always fall back to Orca's profile.
                fallback = presets.selected_preset().config_value(DEVICE_UI_KEY) or ""
                if monitor_identity(fallback) is not None:
                    fallback = ""
                try:
                    pages = self.saved_pages()
                except (OSError, ValueError):
                    pages = {}
                target = pages.get(preset.name, fallback)
                if monitor_identity(target) is not None:
                    target = fallback
                message = "Opened the printer web interface."
            else:
                entry = next((item for item in self._server.printers() if item["host"] == endpoint), None)
                if entry is None:
                    raise ValueError("The printer address in the plugin settings does not match the active profile.")
                FlashForge(endpoint, entry.get("code", ""))
                try:
                    pages = self.saved_pages()
                except ValueError:
                    archived = self._page_store.with_suffix(f".invalid-{time.time_ns()}.json")
                    self._page_store.rename(archived)
                    pages = {}
                if current_view is None:
                    pages[preset.name] = current_url
                pending = self._page_store.with_suffix(".tmp")
                pending.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
                os.replace(pending, self._page_store)
                target = monitor_url(endpoint)
                message = "Opened FlashForge Device Tab in the Device tab."

            if self._stopping.is_set():
                raise ValueError("FlashForge Device Tab is shutting down.")
            transaction.set(DEVICE_UI_KEY, target)
            transaction.commit()
            if remember:
                views = self.saved_views()
                views[preset.name] = {"host": endpoint, "monitor": show_monitor}
                pending = self._view_store.with_suffix(".tmp")
                pending.write_text(json.dumps(views, ensure_ascii=False), encoding="utf-8")
                os.replace(pending, self._view_store)
            return message
        finally:
            self._switch_lock.release()

    def apply_settings(self, printers):
        if not isinstance(printers, list):
            raise ValueError("Invalid printer list.")
        clean = []
        for entry in printers:
            if not isinstance(entry, dict):
                raise ValueError("Invalid printer entry.")
            host = local_address(str(entry.get("host") or ""))
            code = str(entry.get("code") or "")
            if len(code) > 32 or any(not 33 <= ord(c) <= 126 for c in code):
                raise ValueError("The CheckCode must be printable ASCII, up to 32 characters.")
            if any(item["host"] == host for item in clean):
                continue
            clean.append({"host": host, "code": code,
                          "name": str(entry.get("name") or host)[:60]})
            serial = printer_serial(entry.get("serial"))
            if serial:
                clean[-1]["serial"] = serial
        if not clean:
            raise ValueError("Add at least one printer address.")
        host, check_code = clean[0]["host"], clean[0]["code"]
        self.save_config(json.dumps({"printers": clean, "flashforge_host": host,
                                     "check_code": check_code,
                                     "serial_number": clean[0].get("serial", "")}, ensure_ascii=False))
        self._point_device_tab(host)
        self._view_refresh.set()
        return host

    def _point_device_tab(self, host):
        """Keep the Device tab pointed at the first configured printer."""
        try:
            preset = orca.host.preset_bundle().current_printer_preset()
            if preset.config_value("host_type") != "flashforge":
                return
            current = printer_endpoint(preset.config_value("print_host") or "")
            if current == host:
                return
            transaction = orca.host.preset.printer.begin_update()
            edited = orca.host.preset_bundle().printers.edited_preset()
            if edited.name != transaction.target_name:
                return
            if monitor_identity(edited.config_value(DEVICE_UI_KEY) or "") == current:
                transaction.set(DEVICE_UI_KEY, monitor_url(host))
            transaction.set("print_host", host)
            transaction.commit()
        except (OSError, RuntimeError, ValueError):
            pass

    def notify(self, monitor):
        """Show a host notification when a job ends."""
        with monitor.state_lock:
            data = monitor.latest
        if not data:
            return
        state = data.get("state")
        previous = getattr(monitor, "last_state", None)
        monitor.last_state = state
        if previous == state:
            return
        if state in ("COMPLETED", "BUILDING_COMPLETED"):
            message = "Print finished"
        elif state in ("CANCELLED", "CANCELED", "STOPPED"):
            message = "Print cancelled"
        elif state == "ERROR":
            message = "Printer error"
        else:
            return
        name = getattr(monitor, "name", "") or "Printer"
        file = data.get("file") or ""
        try:
            ui = orca.host.ui
            levels = ui.NotificationLevel
            level = getattr(levels, "WarningNotificationLevel" if state == "ERROR"
                            else "RegularNotificationLevel")
            ui.push_notification(level, f"{name}: {message}" + (f" — {file}" if file else ""))
        except (AttributeError, RuntimeError, TypeError):
            pass

    ACTIONS = {"pause": "pause", "resume": "continue", "cancel": "cancel"}

    def act(self, host, action):
        target = self.ACTIONS.get(action)
        if target is None:
            raise ValueError("Unknown printer command.")
        printers = self._server.printers()
        entry = next((item for item in printers if item["host"] == host), None)
        if entry is None:
            raise ValueError("This printer is not in the plugin settings.")
        monitor = self._server.monitors.get(host)
        if monitor is None:
            raise ValueError("The printer is not connected yet.")
        try:
            with self._server.printer_io():
                with monitor.state_lock:
                    data = monitor.latest
                serial = ((data or {}).get("info") or {}).get("serial") or ""
                if not serial:
                    raise ValueError("The printer did not report its serial number.")
                if monitor.adapter.http_only and serial != entry["serial"]:
                    raise ValueError("Wait for the printer to reconnect with the new settings.")
                control_job(host, serial, entry["code"], target)
                monitor.sample(int(time.time()))
        except UploadInProgress as error:
            raise RuntimeError("OrcaSlicer is sending a print job. Try again when it finishes.") from error
        with monitor.state_lock:
            return (monitor.latest or {}).get("state", "")

    def restore_page(self, view):
        # Request threads are not joined during unload. They can wait for the
        # GUI commit and report its outcome without blocking the server.
        self.switch_view(restore_view=view)

    def execute(self):
        try:
            message = self.switch_view()
        except (OSError, RuntimeError, ValueError) as error:
            return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError, str(error))
        return orca.ExecutionResult.success(message)


@orca.plugin
class FlashForgeDeviceTab(orca.base):
    def register_capabilities(self):
        # The loopback page is declared here. Reaching the printer itself needs a
        # per-host grant, which OrcaSlicer asks for the first time the plugin
        # connects to a new address.
        orca.request_permissions(network_socket=[f"('{WEB_HOST}', {WEB_PORT})"], threading=True)
        orca.register_capability(FlashForgeService)

