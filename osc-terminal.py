"""
OSC CRT Terminal – Pygame Prototype
Green phosphor CRT aesthetic with scanlines, glow, flicker, vignette,
boot sequence, typewriter effect, blinking cursor, and message history.

Usage:
    python osc_crt_terminal.py [--ip 127.0.0.1] [--port 8010] [--delay 0.5]

Dependencies:
    pip install pygame python-osc
"""

import pygame
import sys
import os
import math
import random
import argparse
import threading
import time
from datetime import datetime, date
from pathlib import Path

from pythonosc import udp_client

# ============================================================
# CONFIGURATION – tweak to taste, especially for Pi / monitor
# ============================================================

SCREEN_W, SCREEN_H = 1280, 1024
FULLSCREEN = False
FPS = 30
FONT_SIZE = 20
LINE_SPACING = 6
MARGIN_X, MARGIN_Y = 24, 20

# --- Green phosphor palette ---
CRT_GREEN = (0, 255, 65)
CRT_GREEN_DIM = (0, 120, 30)
CRT_BG = (5, 5, 5)

# --- Visual effects (toggle + intensity) ---
SCANLINES = True
SCANLINE_ALPHA = 45

GLOW = True
GLOW_OFFSETS = [1, 2]
GLOW_ALPHA = 20

FLICKER = True
FLICKER_RANGE = 12

VIGNETTE = False
VIGNETTE_STR = 0.75

# --- Typewriter ---
TYPE_MS = 30

# --- Cursor ---
CURSOR_BLINK_MS = 500

# --- Mouse auto-hide ---
MOUSE_HIDE_MS = 2000  # idle time before the mouse cursor auto-hides

# --- Message history cap ---
MAX_MESSAGES = 23

# --- Boot sequence: (line_text, pause_after_complete_ms) ---
BOOT_SEQ = [
    ("LAZOR LIGHT COMMUNICATOR V5.1.2", 500),
    ("(C) 1984 KREKTECH SYSTEMS", 1000),
    ("MEMORY TEST... 64K  OK", 350),
    ("LOADING TERMINAL DRIVER...  OK", 250),
    ("INIT SUBSYSTEMS...   OK", 700),
    ("LINK ACTIVE!", 250),
    ("", 150),
    ("COMMUNICATE WITH THE ALIENS. TYPE YOUR MESSAGE & PRESS RETURN TO TRANSMIT.", 300),
    ("", 100),
]

# --- Character-to-number mapping (edit values to match receiver) ---
CHAR_MAP = {
    " ": 0,
    "A": 1,  "B": 2,  "C": 3,  "D": 4,  "E": 5,
    "F": 6,  "G": 7,  "H": 8,  "I": 9,  "J": 10,
    "K": 11, "L": 12, "M": 13, "N": 14, "O": 15,
    "P": 16, "Q": 17, "R": 18, "S": 19, "T": 20,
    "U": 21, "Ü": 22, "V": 23, "W": 24, "X": 25,
    "Y": 26, "Z": 27,
    ":": 28, ",": 29, ".": 30, "!": 31, "-": 32,
    "?": 33,
}
ALLOWED = set(CHAR_MAP.keys())
OSC_PATH = "/letter"
OSC_DELAY = 1.0
OSC_MAX_GLYPH_INDEX = 127  # sent once on launch — sentinel glyph index in MadMapper
LOOP_PAUSE = 1.0           # empty gap (s) between repeat transmissions of the last message

# --- Logging ---
SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = SCRIPT_DIR / "logs"


# ============================================================
# MESSAGE LOGGER
# ============================================================

class MessageLogger:
    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = None
        self._file = None

    def _ensure_file(self):
        today = date.today()
        if self._current_date != today:
            if self._file:
                self._file.close()
            filename = self.log_dir / f"{today.isoformat()}.log"
            self._file = open(filename, "a", encoding="utf-8")
            self._current_date = today

    def log(self, message: str):
        self._ensure_file()
        ts = datetime.now().isoformat(timespec="seconds")
        self._file.write(f"[{ts}] {message}\n")
        self._file.flush()

    def close(self):
        if self._file:
            self._file.close()
            self._file = None


# ============================================================
# CRT TERMINAL
# ============================================================

class CRTTerminal:
    def __init__(self, ip, port, delay):
        pygame.init()
        self.fullscreen = FULLSCREEN
        flags = pygame.FULLSCREEN if self.fullscreen else 0
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), flags)
        pygame.display.set_caption("LAZOR LIGHT COMMUNICATOR V3.7.1")
        self.clock = pygame.time.Clock()

        self.font = self._load_font()
        self.char_w, self.char_h = self.font.size("W")
        self.line_h = self.char_h + LINE_SPACING

        # How many characters fit on one visual line
        self.cols = (SCREEN_W - 2 * MARGIN_X) // self.char_w

        self.scanline_surf = self._build_scanlines() if SCANLINES else None
        self.vignette_surf = self._build_vignette() if VIGNETTE else None

        # Boot header lines (permanent)
        self.header_lines = []

        # Message history: [{"text": str, "reveal": int, "done": bool, "osc_sync": bool}]
        self.msg_lines = []

        # Layout
        self.header_zone_h = 0

        # Input state
        self.input_buf = ""
        self.cursor_on = True
        self.cursor_timer = 0

        # Input history (for up-arrow recall)
        self.input_history = []
        self.history_idx = -1

        # State machine
        self.state = "boot"
        self.boot_idx = 0
        self.boot_pause_until = 0

        # Typewriter timing
        self.type_timer = 0

        # Transmission status
        self.tx_status = ""
        self.tx_line = None
        # Loop control: stop_event tells the running loop thread to exit; the
        # generation counter lets it know whether it's still the "current" loop
        # (so a stale thread can't overwrite tx_status / tx_line of its successor).
        self.loop_stop_event = None
        self.loop_generation = 0

        # Mouse auto-hide
        self.mouse_visible = True
        self.last_mouse_move = pygame.time.get_ticks()

        # OSC
        self.osc = udp_client.SimpleUDPClient(ip, port)
        self.osc.send_message(OSC_PATH, OSC_MAX_GLYPH_INDEX)
        self.osc_delay = delay

        self.logger = MessageLogger()
        self.running = True

    # --- setup helpers ---

    def _load_font(self):
        for name in ["Courier New", "Courier", "Liberation Mono", "DejaVu Sans Mono"]:
            font = pygame.font.SysFont(name, FONT_SIZE)
            if font.size("W")[0] == font.size("i")[0]:
                return font
        return pygame.font.Font(None, FONT_SIZE)

    def _build_scanlines(self):
        surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        for y in range(0, SCREEN_H, 2):
            pygame.draw.line(surf, (0, 0, 0, SCANLINE_ALPHA), (0, y), (SCREEN_W, y))
        return surf

    def _build_vignette(self):
        sw, sh = SCREEN_W // 4, SCREEN_H // 4
        small = pygame.Surface((sw, sh), pygame.SRCALPHA)
        cx, cy = sw / 2, sh / 2
        max_d = math.sqrt(cx ** 2 + cy ** 2)
        for y in range(sh):
            for x in range(sw):
                d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2) / max_d
                a = int(255 * VIGNETTE_STR * min(d ** 1.8, 1.0))
                small.set_at((x, y), (0, 0, 0, a))
        return pygame.transform.smoothscale(small, (SCREEN_W, SCREEN_H))

    def _calc_header_zone(self):
        self.header_zone_h = MARGIN_Y + len(self.header_lines) * self.line_h + self.line_h // 2

    def _toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        flags = pygame.FULLSCREEN if self.fullscreen else 0
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), flags)

    # --- text wrapping ---

    def _wrap(self, text, max_cols=None):
        """Break text into visual lines of max_cols characters."""
        if max_cols is None:
            max_cols = self.cols
        if not text:
            return [""]
        lines = []
        while len(text) > max_cols:
            lines.append(text[:max_cols])
            text = text[max_cols:]
        lines.append(text)
        return lines

    def _wrap_with_reveal(self, text, reveal, max_cols=None):
        """Wrap text but only return characters up to reveal count."""
        visible = text[:reveal]
        return self._wrap(visible, max_cols)

    # --- text rendering with glow ---

    def _blit_text(self, text, x, y, color=CRT_GREEN):
        if not text:
            return
        base = self.font.render(text, True, color)
        if GLOW:
            glow = base.copy()
            glow.set_alpha(GLOW_ALPHA)
            for off in GLOW_OFFSETS:
                for dx, dy in [(-off, 0), (off, 0), (0, -off), (0, off)]:
                    self.screen.blit(glow, (x + dx, y + dy))
        self.screen.blit(base, (x, y))

    # --- line management ---

    def _add_header_line(self, text, instant=False):
        line = {"text": text, "reveal": len(text) if instant else 0, "done": instant}
        self.header_lines.append(line)
        return line

    def _add_msg_line(self, text, instant=False, osc_sync=False):
        line = {
            "text": text,
            "reveal": len(text) if instant else 0,
            "done": instant,
            "osc_sync": osc_sync,
        }
        self.msg_lines.append(line)
        while len(self.msg_lines) > MAX_MESSAGES:
            self.msg_lines.pop(0)
        return line

    def _tick_typewriter(self, now):
        for ln in self.header_lines:
            if not ln["done"]:
                if now - self.type_timer >= TYPE_MS:
                    ln["reveal"] += 1
                    self.type_timer = now
                    if ln["reveal"] >= len(ln["text"]):
                        ln["done"] = True
                return

        for ln in self.msg_lines:
            if not ln["done"] and not ln["osc_sync"]:
                if now - self.type_timer >= TYPE_MS:
                    ln["reveal"] += 1
                    self.type_timer = now
                    if ln["reveal"] >= len(ln["text"]):
                        ln["done"] = True
                return

    # --- boot sequence ---

    def _tick_boot(self, now):
        if self.boot_idx >= len(BOOT_SEQ):
            self._calc_header_zone()
            self.state = "ready"
            return

        if self.header_lines and not self.header_lines[-1]["done"]:
            return

        if self.boot_pause_until > 0:
            if now < self.boot_pause_until:
                return
            self.boot_pause_until = 0
            self.boot_idx += 1
            return

        text, pause = BOOT_SEQ[self.boot_idx]
        self._add_header_line(text)
        self.type_timer = now
        self.boot_pause_until = now + pause

    # --- input handling ---

    def _handle_key(self, event):
        # Cmd/Ctrl + Shift + F → toggle fullscreen (works in any state).
        # Cmd on macOS, Ctrl on Windows/Linux.
        if event.key == pygame.K_f:
            plat_mod = pygame.KMOD_META if sys.platform == "darwin" else pygame.KMOD_CTRL
            if (event.mod & plat_mod) and (event.mod & pygame.KMOD_SHIFT):
                self._toggle_fullscreen()
                return
        if self.state != "ready":
            return
        if event.key == pygame.K_RETURN:
            self._submit()
        elif event.key == pygame.K_BACKSPACE:
            self.input_buf = self.input_buf[:-1]
        elif event.key == pygame.K_ESCAPE:
            self.running = False
        elif event.key == pygame.K_UP:
            self._history_prev()
        elif event.key == pygame.K_DOWN:
            self._history_next()
        elif event.unicode:
            ch = event.unicode.upper()
            if ch in ALLOWED:
                self.input_buf += ch
                self.history_idx = -1

    def _history_prev(self):
        if not self.input_history:
            return
        if self.history_idx == -1:
            self.history_idx = len(self.input_history) - 1
        elif self.history_idx > 0:
            self.history_idx -= 1
        self.input_buf = self.input_history[self.history_idx]

    def _history_next(self):
        if self.history_idx == -1:
            return
        if self.history_idx < len(self.input_history) - 1:
            self.history_idx += 1
            self.input_buf = self.input_history[self.history_idx]
        else:
            self.history_idx = -1
            self.input_buf = ""

    def _submit(self):
        text = self.input_buf.strip()
        self.input_buf = ""
        self.history_idx = -1
        if not text:
            return
        bad = set(c for c in text if c not in ALLOWED)
        if bad:
            self._add_msg_line(f"ERR: INVALID CHARS: {' '.join(sorted(bad))}")
            self.type_timer = pygame.time.get_ticks()
            return

        self.input_history.append(text)
        self.logger.log(text)

        # Stop any currently-looping transmission and lock its line into history.
        if self.loop_stop_event:
            self.loop_stop_event.set()
        if self.tx_line:
            self.tx_line["reveal"] = len(self.tx_line["text"])
            self.tx_line["done"] = True
            self.tx_line["osc_sync"] = False

        line = self._add_msg_line(f"> {text}", osc_sync=True)
        line["reveal"] = 2
        self.tx_line = line
        self.tx_status = "TRANSMITTING..."

        self.loop_generation += 1
        self.loop_stop_event = threading.Event()
        threading.Thread(
            target=self._loop_osc,
            args=(text, self.loop_generation, self.loop_stop_event),
            daemon=True,
        ).start()

    def _loop_osc(self, text, my_gen, stop_event):
        """Send the message via OSC, then loop with LOOP_PAUSE gaps until stopped.
        Every iteration restarts the typewriter reveal so the visible line
        re-runs in sync with each OSC send — highlights which letter is
        currently being transmitted, both on the first pass and on every loop.
        """
        seq = text + " "  # trailing space (CHAR_MAP[" "] = 0) clears the receiver between loops
        first_pass = True
        while not stop_event.is_set():
            if my_gen == self.loop_generation and self.tx_line:
                self.tx_line["reveal"] = 2  # back to just "> "
                self.tx_line["done"] = False

            for ch in seq:
                if stop_event.is_set():
                    return
                self.osc.send_message(OSC_PATH, CHAR_MAP[ch])
                if (my_gen == self.loop_generation
                        and self.tx_line
                        and self.tx_line["reveal"] < len(self.tx_line["text"])):
                    self.tx_line["reveal"] += 1
                if stop_event.wait(self.osc_delay):
                    return

            if my_gen == self.loop_generation and self.tx_line:
                self.tx_line["reveal"] = len(self.tx_line["text"])
                self.tx_line["done"] = True

            if first_pass:
                first_pass = False
                if my_gen == self.loop_generation:
                    self.tx_status = "LOOPING..."

            if stop_event.wait(LOOP_PAUSE):
                return

    # --- layout helpers ---

    def _input_visual_lines(self):
        """Return the wrapped lines for the current input prompt."""
        prompt = f"> {self.input_buf}"
        return self._wrap(prompt)

    def _input_zone_height(self):
        """Height of the input area: wrapped input lines + padding + separator."""
        n = len(self._input_visual_lines())
        return MARGIN_Y + n * self.line_h + self.line_h // 2

    def _msg_visual_line_count(self, msg):
        """How many visual lines does a message take when wrapped?"""
        return len(self._wrap(msg["text"]))

    # --- drawing ---

    def _draw(self):
        now = pygame.time.get_ticks()
        self.screen.fill(CRT_BG)

        # ---- Header zone (permanent boot text) ----
        y = MARGIN_Y
        for ln in self.header_lines:
            visible = ln["text"][: ln["reveal"]]
            self._blit_text(visible, MARGIN_X, y)
            y += self.line_h

        if self.state != "ready":
            self._draw_overlays()
            return

        # Header separator
        sep_top_y = self.header_zone_h - self.line_h // 4
        pygame.draw.line(
            self.screen, CRT_GREEN_DIM,
            (MARGIN_X, sep_top_y), (SCREEN_W - MARGIN_X, sep_top_y)
        )

        # ---- Calculate dynamic zones ----
        input_h = self._input_zone_height()
        msg_zone_y = self.header_zone_h
        msg_zone_bottom = SCREEN_H - input_h
        available_visual_lines = max(1, (msg_zone_bottom - msg_zone_y) // self.line_h)

        # ---- Message zone ----
        # Collect all visual rows from messages, then show the last N that fit
        all_rows = []
        for msg in self.msg_lines:
            wrapped = self._wrap_with_reveal(msg["text"], msg["reveal"])
            for row in wrapped:
                all_rows.append(row)

        visible_rows = all_rows[-available_visual_lines:]
        y = msg_zone_y
        for row in visible_rows:
            self._blit_text(row, MARGIN_X, y)
            y += self.line_h

        # ---- Input zone ----
        sep_bot_y = msg_zone_bottom
        pygame.draw.line(
            self.screen, CRT_GREEN_DIM,
            (MARGIN_X, sep_bot_y), (SCREEN_W - MARGIN_X, sep_bot_y)
        )

        # Transmission status (right side on bottom separator)
        if self.tx_status:
            self._blit_text(
                self.tx_status,
                SCREEN_W - MARGIN_X - self.char_w * len(self.tx_status),
                sep_bot_y + 4, CRT_GREEN_DIM
            )

        # Wrapped input text
        input_lines = self._input_visual_lines()
        iy = sep_bot_y + self.line_h // 2
        for row in input_lines:
            self._blit_text(row, MARGIN_X, iy)
            iy += self.line_h

        # Blinking block cursor at end of last input row
        if now - self.cursor_timer >= CURSOR_BLINK_MS:
            self.cursor_on = not self.cursor_on
            self.cursor_timer = now
        if self.cursor_on:
            last_row = input_lines[-1] if input_lines else ""
            cursor_y = sep_bot_y + self.line_h // 2 + (len(input_lines) - 1) * self.line_h
            cx = MARGIN_X + self.char_w * len(last_row)
            cursor_surf = pygame.Surface((self.char_w, self.char_h), pygame.SRCALPHA)
            cursor_surf.fill((*CRT_GREEN, 170))
            self.screen.blit(cursor_surf, (cx, cursor_y))

        self._draw_overlays()

    def _draw_overlays(self):
        if self.scanline_surf:
            self.screen.blit(self.scanline_surf, (0, 0))
        if self.vignette_surf:
            self.screen.blit(self.vignette_surf, (0, 0))
        if FLICKER:
            alpha = random.randint(0, FLICKER_RANGE)
            flicker = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            flicker.fill((0, 0, 0, alpha))
            self.screen.blit(flicker, (0, 0))

    # --- main loop ---

    def run(self):
        while self.running:
            now = pygame.time.get_ticks()

            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                elif ev.type == pygame.KEYDOWN:
                    self._handle_key(ev)
                elif ev.type == pygame.MOUSEMOTION:
                    self.last_mouse_move = now
                    if not self.mouse_visible:
                        pygame.mouse.set_visible(True)
                        self.mouse_visible = True

            # Hide the cursor after MOUSE_HIDE_MS of mouse idleness
            if self.mouse_visible and now - self.last_mouse_move > MOUSE_HIDE_MS:
                pygame.mouse.set_visible(False)
                self.mouse_visible = False

            if self.state == "boot":
                self._tick_boot(now)
            self._tick_typewriter(now)

            self._draw()
            pygame.display.flip()
            self.clock.tick(FPS)

        self.logger.close()
        pygame.quit()


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    p = argparse.ArgumentParser(description="OSC CRT Terminal")
    p.add_argument("--ip", default="127.0.0.1", help="OSC target IP")
    p.add_argument("--port", type=int, default=8010, help="OSC target port")
    p.add_argument("--delay", type=float, default=OSC_DELAY, help="Delay between OSC letters (s)")
    p.add_argument("--fullscreen", action="store_true", help="Launch in fullscreen")
    args = p.parse_args()

    if args.fullscreen:
        global FULLSCREEN
        FULLSCREEN = True

    terminal = CRTTerminal(args.ip, args.port, args.delay)
    terminal.run()


if __name__ == "__main__":
    main()
