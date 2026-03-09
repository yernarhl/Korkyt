import sys
import math
import random
import array
import os
from dataclasses import dataclass

import pygame


# ---------------------------
# Audio (no external assets)
# ---------------------------
SAMPLE_RATE = 44100


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def make_tone(
    freq_hz: float,
    duration_s: float,
    volume: float = 0.3,
    waveform: str = "sine",
    attack_s: float = 0.01,
    release_s: float = 0.04,
) -> pygame.mixer.Sound:
    """Generate a simple mono 16-bit PCM tone."""
    n = max(1, int(SAMPLE_RATE * duration_s))
    attack_n = int(SAMPLE_RATE * attack_s)
    release_n = int(SAMPLE_RATE * release_s)
    volume = _clamp(volume, 0.0, 1.0)

    buf = array.array("h")
    two_pi = 2.0 * math.pi
    for i in range(n):
        t = i / SAMPLE_RATE
        phase = two_pi * freq_hz * t
        if waveform == "square":
            s = 1.0 if math.sin(phase) >= 0 else -1.0
        elif waveform == "triangle":
            # cheap triangle via asin(sin)
            s = (2.0 / math.pi) * math.asin(math.sin(phase))
        else:
            s = math.sin(phase)

        # envelope
        env = 1.0
        if attack_n > 0 and i < attack_n:
            env = i / attack_n
        if release_n > 0 and i > n - release_n:
            env = max(0.0, (n - i) / release_n)
        sample = int(32767 * volume * env * s)
        buf.append(sample)

    return pygame.mixer.Sound(buffer=buf.tobytes())


def make_howl(duration_s: float = 1.6, volume: float = 0.25) -> pygame.mixer.Sound:
    """A simple 'howl' made from a pitch sweep with tremolo."""
    n = max(1, int(SAMPLE_RATE * duration_s))
    buf = array.array("h")
    volume = _clamp(volume, 0.0, 1.0)
    for i in range(n):
        t = i / SAMPLE_RATE
        # sweep 180 -> 420 Hz
        f = 520.0 + 400.0 * (0.5 - 0.5 * math.cos(min(1.0, t / duration_s) * math.pi))
        trem = 0.55 + 0.45 * math.sin(2.0 * math.pi * 5.0 * t)
        s = math.sin(2.0 * math.pi * f * t) * trem
        # fade in/out
        fade = 1.0
        if t < 0.08:
            fade = t / 0.08
        if duration_s - t < 0.12:
            fade = max(0.0, (duration_s - t) / 0.12)
        buf.append(int(32767 * volume * fade * s))
    return pygame.mixer.Sound(buffer=buf.tobytes())


# ---------------------------
# Game constants
# ---------------------------
SCREEN_WIDTH = 900
SCREEN_HEIGHT = 600
FPS = 60
CENTER = pygame.Vector2(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 30)

# Visual palette (sunset + silhouettes)
BLACK = (0, 0, 0)
WHITE = (240, 240, 240)
SUN_TOP = (255, 125, 60)
SUN_MID = (235, 90, 35)
SUN_LOW = (120, 35, 60)
GOLD = (240, 210, 90)
GOLD_DIM = (190, 160, 70)
FROST = (170, 210, 255)

MIN_SHIELD_R = 110
MAX_SHIELD_R = 300

# Default tuning (can be changed in-game with 1/2/3)
ENERGY_DECAY_PER_SEC = 0.12
ENERGY_GAIN_GOOD = 0.11
ENERGY_GAIN_GREAT = 0.15
ENERGY_GAIN_PERFECT = 0.20
ENERGY_PENALTY_MISS = 0.12
OFFBEAT_PENALTY = 0.035

HIT_WINDOW_S = 0.16  # base timing window (seconds)

NORMAL_WOLF_HP = 30.0
SHADOW_WOLF_HP = 26.0


@dataclass
class Wave:
    radius: float
    speed: float
    alpha: float
    width: int
    color: tuple[int, int, int]

    def update(self, dt: float) -> None:
        self.radius += self.speed * dt
        self.alpha -= 150.0 * dt
        self.alpha = max(0.0, self.alpha)

    @property
    def alive(self) -> bool:
        return self.alpha > 0.0


@dataclass
class Wolf:
    pos: pygame.Vector2
    kind: str  # "normal" | "shadow"
    speed: float
    hp: float
    wobble_phase: float

    @property
    def is_shadow(self) -> bool:
        return self.kind == "shadow"

    def update(self, dt: float, energy: float, shield_r: float, waves: list[Wave]) -> None:
        # Approach center; weaker music -> faster wolves
        # (softened) low energy still hurts, but doesn't instantly snowball
        approach_mul = 1.15 - 0.35 * energy  # energy 0..1
        v = (CENTER - self.pos)
        dist = v.length() + 1e-6
        dirv = v / dist
        base = self.speed * approach_mul

        # Simple wiggle for life
        self.wobble_phase += dt * (2.2 if self.is_shadow else 1.6)
        wiggle = pygame.Vector2(math.sin(self.wobble_phase), math.cos(self.wobble_phase)) * (8.0 if self.is_shadow else 5.0)

        self.pos += dirv * base * dt + wiggle * dt

        # Continuous pressure from the shield: inside shield they take damage (sound burns)
        if dist < shield_r:
            burn = (18.0 if self.is_shadow else 14.0) * (0.35 + 0.65 * energy)
            self.hp -= burn * dt

        # Extra damage from passing waves
        for w in waves:
            # When the wavefront is near the wolf distance, apply pulse damage
            if abs(dist - w.radius) < max(10.0, w.width * 1.8):
                self.hp -= (16.0 if self.is_shadow else 20.0) * dt * (0.4 + 0.6 * energy)

    def draw(self, surface: pygame.Surface) -> None:
        p = (int(self.pos.x), int(self.pos.y))
        # Prefer sprite assets if loaded
        img = None
        if self.is_shadow and WOLF_SHADOW_IMAGE is not None:
            img = WOLF_SHADOW_IMAGE
        elif (not self.is_shadow) and WOLF_NORMAL_IMAGE is not None:
            img = WOLF_NORMAL_IMAGE

        if img is not None:
            # Rotate sprite so that its "right" side (как в PNG) смотрит на Коркыта
            dx = CENTER.x - self.pos.x
            dy = CENTER.y - self.pos.y
            # atan2 с учётом экранных координат (ось Y вниз)
            angle = -math.degrees(math.atan2(dy, dx)) + 180.0
            rotated = pygame.transform.rotozoom(img, angle, 1.0)
            rect = rotated.get_rect(center=p)
            surface.blit(rotated, rect)
            return

        # Fallback: simple polygon silhouettes
        if self.is_shadow:
            wolf_surf = pygame.Surface((60, 40), pygame.SRCALPHA)
            pygame.draw.polygon(
                wolf_surf,
                (0, 0, 0, 170),
                [(4, 32), (16, 14), (26, 20), (36, 10), (48, 18), (55, 30), (44, 34), (30, 26), (18, 34)],
            )
            surface.blit(wolf_surf, (p[0] - 30, p[1] - 20))
        else:
            pygame.draw.polygon(
                surface,
                (0, 0, 0),
                [(p[0] - 22, p[1] + 12), (p[0] - 10, p[1] - 2), (p[0] - 2, p[1] + 2), (p[0] + 10, p[1] - 10),
                 (p[0] + 22, p[1] - 2), (p[0] + 28, p[1] + 12), (p[0] + 10, p[1] + 16), (p[0] - 4, p[1] + 10)]
            )


def draw_text(surface: pygame.Surface, text: str, font: pygame.font.Font, color: tuple[int, int, int], center: tuple[int, int]) -> None:
    render = font.render(text, True, color)
    rect = render.get_rect(center=center)
    surface.blit(render, rect)


def draw_sunset(surface: pygame.Surface, brightness: float) -> None:
    """Simple vertical gradient; brightness 0..1."""
    brightness = _clamp(brightness, 0.0, 1.0)
    top = tuple(int(c * (0.55 + 0.45 * brightness)) for c in SUN_TOP)
    mid = tuple(int(c * (0.55 + 0.45 * brightness)) for c in SUN_MID)
    low = tuple(int(c * (0.55 + 0.45 * brightness)) for c in SUN_LOW)

    h = SCREEN_HEIGHT
    for y in range(h):
        t = y / max(1, h - 1)
        if t < 0.55:
            k = t / 0.55
            col = (
                int(top[0] + (mid[0] - top[0]) * k),
                int(top[1] + (mid[1] - top[1]) * k),
                int(top[2] + (mid[2] - top[2]) * k),
            )
        else:
            k = (t - 0.55) / 0.45
            col = (
                int(mid[0] + (low[0] - mid[0]) * k),
                int(mid[1] + (low[1] - mid[1]) * k),
                int(mid[2] + (low[2] - mid[2]) * k),
            )
        pygame.draw.line(surface, col, (0, y), (SCREEN_WIDTH, y))

    # Steppe silhouette
    pygame.draw.polygon(
        surface,
        (0, 0, 0),
        [(0, h), (0, int(h * 0.72)), (140, int(h * 0.76)), (300, int(h * 0.73)), (520, int(h * 0.78)), (720, int(h * 0.74)), (SCREEN_WIDTH, int(h * 0.77)), (SCREEN_WIDTH, h)],
    )


def spawn_wolf(kind: str) -> Wolf:
    margin = 60
    side = random.choice(["top", "bottom", "left", "right"])
    if side == "top":
        pos = pygame.Vector2(random.uniform(-margin, SCREEN_WIDTH + margin), -margin)
    elif side == "bottom":
        pos = pygame.Vector2(random.uniform(-margin, SCREEN_WIDTH + margin), SCREEN_HEIGHT + margin)
    elif side == "left":
        pos = pygame.Vector2(-margin, random.uniform(40, SCREEN_HEIGHT - 40))
    else:
        pos = pygame.Vector2(SCREEN_WIDTH + margin, random.uniform(40, SCREEN_HEIGHT - 40))

    if kind == "shadow":
        speed = random.uniform(80.0, 105.0)
        hp = SHADOW_WOLF_HP
    else:
        speed = random.uniform(55.0, 80.0)
        hp = NORMAL_WOLF_HP
    return Wolf(pos=pos, kind=kind, speed=speed, hp=hp, wobble_phase=random.random() * 10.0)


def main() -> None:
    pygame.mixer.pre_init(SAMPLE_RATE, -16, 1, 512)
    pygame.init()

    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Korkyt — Kobyz Against the Darkness")
    clock = pygame.time.Clock()

    # Resolve asset paths relative to this file so the game
    # works even if launched from another working directory.
    asset_dir = os.path.dirname(__file__)

    def asset_path(name: str) -> str:
        return os.path.join(asset_dir, name)

    # Load image assets (optional, game falls back to primitives if missing)
    global BG_IMAGE, KORKYT_IMAGE, WOLF_NORMAL_IMAGE, WOLF_SHADOW_IMAGE
    try:
        BG_IMAGE = pygame.image.load(asset_path("bg_steppe.png")).convert()
        BG_IMAGE = pygame.transform.smoothscale(BG_IMAGE, (SCREEN_WIDTH, SCREEN_HEIGHT))
    except pygame.error:
        BG_IMAGE = None

    try:
        KORKYT_IMAGE = pygame.image.load(asset_path("korkyt.png")).convert_alpha()
        # If спрайт слишком большой — мягко уменьшим
        max_w, max_h = 180, 210
        w, h = KORKYT_IMAGE.get_size()
        if w > max_w or h > max_h:
            scale = min(max_w / float(w), max_h / float(h))
            KORKYT_IMAGE = pygame.transform.smoothscale(
                KORKYT_IMAGE, (int(w * scale), int(h * scale))
            )
    except pygame.error:
        KORKYT_IMAGE = None

    try:
        WOLF_NORMAL_IMAGE = pygame.image.load(asset_path("wolf_normal.png")).convert_alpha()
        # Если волк слишком большой — мягко уменьшаем
        max_w, max_h = 150, 110
        w, h = WOLF_NORMAL_IMAGE.get_size()
        if w > max_w or h > max_h:
            scale = min(max_w / float(w), max_h / float(h))
            WOLF_NORMAL_IMAGE = pygame.transform.smoothscale(
                WOLF_NORMAL_IMAGE, (int(w * scale), int(h * scale))
            )
    except pygame.error:
        WOLF_NORMAL_IMAGE = None

    try:
        WOLF_SHADOW_IMAGE = pygame.image.load(asset_path("wolf_shadow.png")).convert_alpha()
        max_w, max_h = 150, 110
        w, h = WOLF_SHADOW_IMAGE.get_size()
        if w > max_w or h > max_h:
            scale = min(max_w / float(w), max_h / float(h))
            WOLF_SHADOW_IMAGE = pygame.transform.smoothscale(
                WOLF_SHADOW_IMAGE, (int(w * scale), int(h * scale))
            )
    except pygame.error:
        # Если отдельной тени нет — будем использовать обычного и затемнять в коде
        WOLF_SHADOW_IMAGE = WOLF_NORMAL_IMAGE

    # Menu / kobyz theme music (optional: put menu_kobyz.ogg or .mp3 in project folder)
    menu_music_loaded = False
    for name in ("menu_kobyz.ogg", "menu_kobyz.mp3", "kobyz_menu.ogg", "menu_music.ogg"):
        path = asset_path(name)
        if os.path.isfile(path):
            try:
                pygame.mixer.music.load(path)
                menu_music_loaded = True
                break
            except pygame.error:
                pass

    font = pygame.font.SysFont("arial", 18)
    big_font = pygame.font.SysFont("arial", 34, bold=True)

    # ---------------------------
    # Menu UI setup
    # ---------------------------
    button_w, button_h = 260, 60
    menu_start_rect = pygame.Rect(0, 0, button_w, button_h)
    menu_rules_rect = pygame.Rect(0, 0, button_w, button_h)
    menu_start_rect.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 10)
    menu_rules_rect.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 70)

    back_button_rect = pygame.Rect(30, SCREEN_HEIGHT - 70, 160, 44)

    diff_button_w, diff_button_h = 220, 55
    diff_easy_rect = pygame.Rect(0, 0, diff_button_w, diff_button_h)
    diff_norm_rect = pygame.Rect(0, 0, diff_button_w, diff_button_h)
    diff_hard_rect = pygame.Rect(0, 0, diff_button_w, diff_button_h)
    diff_easy_rect.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 40)
    diff_norm_rect.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 30)
    diff_hard_rect.center = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 100)

    # Sounds
    try:
        click = make_tone(660, 0.045, 0.25, waveform="square", attack_s=0.001, release_s=0.03)
        good = make_tone(990, 0.06, 0.22, waveform="sine")
        perfect = make_tone(1320, 0.08, 0.26, waveform="triangle")
        error = make_tone(220, 0.12, 0.20, waveform="square", attack_s=0.001, release_s=0.09)
        burst = make_tone(160, 0.18, 0.22, waveform="triangle", attack_s=0.001, release_s=0.14)
        howl = make_howl(1.6, 0.20)
    except pygame.error:
        click = good = perfect = error = burst = howl = None

    howl_channel = None
    if howl is not None:
        howl_channel = pygame.mixer.Channel(2)
        howl_channel.play(howl, loops=-1)
        howl_channel.set_volume(0.0)

    # Rhythm / progression (defaults; can be changed in-game with 1/2/3)
    difficulty = "easy"  # "easy" | "normal" | "hard"
    bpm = 64.0
    bpm_max = 132.0
    bpm_ramp_per_sec = 0.55
    start_time = pygame.time.get_ticks() / 1000.0
    prev_beat = -1
    beat_hit = False

    # Gameplay state (values will be initialized by reset_game)
    energy = 0.62
    streak = 0
    waves: list[Wave] = []
    wolves: list[Wolf] = []
    spawn_timer = 0.0
    score_time = 0.0
    game_over = False
    game_over_reason = ""

    # Strong hit (space) shockwave
    strong_burst_time = -999.0
    strong_burst_duration = 0.35
    strong_burst_max_r = 380.0
    last_feedback = ""
    last_feedback_t = 0.0

    # Simple state machine: top-level menu and in-game
    state = "menu"  # "menu" | "difficulty" | "rules" | "playing"

    def apply_difficulty(name: str) -> None:
        nonlocal difficulty, bpm, bpm_max, bpm_ramp_per_sec, energy
        global HIT_WINDOW_S, ENERGY_DECAY_PER_SEC, ENERGY_PENALTY_MISS, OFFBEAT_PENALTY
        difficulty = name
        if name == "hard":
            bpm = 74.0
            bpm_max = 158.0
            bpm_ramp_per_sec = 0.9
            HIT_WINDOW_S = 0.125
            ENERGY_DECAY_PER_SEC = 0.15
            ENERGY_PENALTY_MISS = 0.18
            OFFBEAT_PENALTY = 0.05
            energy = max(energy, 0.58)
        elif name == "normal":
            bpm = 68.0
            bpm_max = 145.0
            bpm_ramp_per_sec = 0.7
            HIT_WINDOW_S = 0.145
            ENERGY_DECAY_PER_SEC = 0.135
            ENERGY_PENALTY_MISS = 0.15
            OFFBEAT_PENALTY = 0.04
            energy = max(energy, 0.60)
        else:
            bpm = 64.0
            bpm_max = 132.0
            bpm_ramp_per_sec = 0.55
            HIT_WINDOW_S = 0.16
            ENERGY_DECAY_PER_SEC = 0.12
            ENERGY_PENALTY_MISS = 0.12
            OFFBEAT_PENALTY = 0.035
            energy = max(energy, 0.62)

    def reset_game() -> None:
        nonlocal energy, streak, waves, wolves, spawn_timer, score_time
        nonlocal game_over, game_over_reason, strong_burst_time, last_feedback, last_feedback_t
        nonlocal start_time, prev_beat, beat_hit
        energy = 0.62
        streak = 0
        waves = []
        wolves = []
        spawn_timer = 0.0
        score_time = 0.0
        game_over = False
        game_over_reason = ""
        strong_burst_time = -999.0
        last_feedback = ""
        last_feedback_t = 0.0
        start_time = pygame.time.get_ticks() / 1000.0
        prev_beat = -1
        beat_hit = False

    apply_difficulty(difficulty)
    reset_game()

    def beat_interval() -> float:
        return 60.0 / max(1.0, bpm)

    def current_beat(now: float) -> int:
        return int((now - start_time) / beat_interval())

    def beat_phase(now: float) -> float:
        interval = beat_interval()
        x = (now - start_time) % interval
        return x

    def nearest_beat_error(now: float) -> float:
        interval = beat_interval()
        ph = beat_phase(now)
        return min(ph, interval - ph)

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        now = pygame.time.get_ticks() / 1000.0
        # Input
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                # ESC: в меню из игры/экранов, выход только из главного меню
                if event.key == pygame.K_ESCAPE:
                    if state == "menu":
                        running = False
                    else:
                        state = "menu"
                        reset_game()
                        if howl_channel is not None:
                            howl_channel.set_volume(0.0)
                    continue
                # Keyboard handling depends on current state
                if state == "playing":
                    if not game_over:
                        if event.key == pygame.K_1:
                            apply_difficulty("easy")
                            last_feedback, last_feedback_t = "Difficulty: EASY", 0.9
                        elif event.key == pygame.K_2:
                            apply_difficulty("normal")
                            last_feedback, last_feedback_t = "Difficulty: NORMAL", 0.9
                        elif event.key == pygame.K_3:
                            apply_difficulty("hard")
                            last_feedback, last_feedback_t = "Difficulty: HARD", 0.9
                    else:
                        if event.key == pygame.K_r:
                            # soft restart keeps current difficulty
                            reset_game()
                            apply_difficulty(difficulty)
                        continue

                    is_hit_key = event.key in (pygame.K_SPACE, pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT)
                    if not is_hit_key:
                        continue

                    timing_err = nearest_beat_error(now)
                    window = HIT_WINDOW_S * (1.05 - 0.25 * energy)  # slightly tighter when you're doing well
                    if timing_err <= window and not beat_hit:
                        beat_hit = True
                        # accuracy tiers
                        if timing_err <= window * 0.35:
                            gain = ENERGY_GAIN_PERFECT
                            streak += 1
                            if perfect is not None:
                                perfect.play()
                            last_feedback, last_feedback_t = "PERFECT", 0.35
                        elif timing_err <= window * 0.7:
                            gain = ENERGY_GAIN_GREAT
                            streak += 1
                            if good is not None:
                                good.play()
                            last_feedback, last_feedback_t = "GREAT", 0.35
                        else:
                            gain = ENERGY_GAIN_GOOD
                            streak = max(0, streak) + 1
                            if good is not None:
                                good.play()
                            last_feedback, last_feedback_t = "OK", 0.35

                        energy = min(1.0, energy + gain + 0.015 * min(12, streak))

                        # emit a golden sound wave
                        waves.append(
                            Wave(
                                radius=25.0,
                                speed=520.0 + 250.0 * energy,
                                alpha=220.0,
                                width=4,
                                color=GOLD,
                            )
                        )

                        # Strong hit: SPACE triggers shockwave that banishes shadow wolves nearby
                        if event.key == pygame.K_SPACE:
                            strong_burst_time = now
                            if burst is not None:
                                burst.play()
                            # instant banish of shadow wolves within base radius (feels snappy)
                            for w in wolves:
                                d = (w.pos - CENTER).length()
                                if w.is_shadow and d < 190.0 + 120.0 * energy:
                                    w.hp = 0.0
                    else:
                        # early/late: "string scratch"
                        energy = max(0.0, energy - OFFBEAT_PENALTY)
                        streak = 0
                        if error is not None:
                            error.play()
                        last_feedback, last_feedback_t = "MISS", 0.45
                # No extra keyboard controls for menu / rules / difficulty besides ESC.
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = event.pos
                if state == "menu":
                    if menu_start_rect.collidepoint(mx, my):
                        state = "difficulty"
                    elif menu_rules_rect.collidepoint(mx, my):
                        state = "rules"
                elif state == "difficulty":
                    if back_button_rect.collidepoint(mx, my):
                        state = "menu"
                    elif diff_easy_rect.collidepoint(mx, my):
                        reset_game()
                        apply_difficulty("easy")
                        pygame.mixer.music.stop()
                        state = "playing"
                    elif diff_norm_rect.collidepoint(mx, my):
                        reset_game()
                        apply_difficulty("normal")
                        pygame.mixer.music.stop()
                        state = "playing"
                    elif diff_hard_rect.collidepoint(mx, my):
                        reset_game()
                        apply_difficulty("hard")
                        pygame.mixer.music.stop()
                        state = "playing"
                elif state == "rules":
                    if back_button_rect.collidepoint(mx, my):
                        state = "menu"
                elif state == "playing":
                    if back_button_rect.collidepoint(mx, my):
                        state = "menu"
                        reset_game()
                        if howl_channel is not None:
                            howl_channel.set_volume(0.0)

        # ---------------------------
        # Non-game states: only draw menus / rules
        # ---------------------------
        if state != "playing":
            # Play menu / kobyz theme while in menu, difficulty select, or rules
            if menu_music_loaded and not pygame.mixer.music.get_busy():
                pygame.mixer.music.set_volume(0.5)
                pygame.mixer.music.play(loops=-1)
            if BG_IMAGE is not None:
                screen.blit(BG_IMAGE, (0, 0))
            else:
                draw_sunset(screen, 0.7)

            overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 110))
            screen.blit(overlay, (0, 0))

            if state == "menu":
                title = big_font.render("Korkyt — Kobyz Against the Darkness", True, WHITE)
                title_rect = title.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 120))
                screen.blit(title, title_rect)

                # Start button
                pygame.draw.rect(screen, (20, 20, 20), menu_start_rect, border_radius=10)
                pygame.draw.rect(screen, GOLD, menu_start_rect, width=2, border_radius=10)
                txt = big_font.render("Start Game", True, WHITE)
                txt_rect = txt.get_rect(center=menu_start_rect.center)
                screen.blit(txt, txt_rect)

                # Rules button
                pygame.draw.rect(screen, (20, 20, 20), menu_rules_rect, border_radius=10)
                pygame.draw.rect(screen, GOLD, menu_rules_rect, width=2, border_radius=10)
                txt2 = big_font.render("Game Rules", True, WHITE)
                txt2_rect = txt2.get_rect(center=menu_rules_rect.center)
                screen.blit(txt2, txt2_rect)

                hint = font.render("ESC — exit", True, WHITE)
                hint_rect = hint.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT - 40))
                screen.blit(hint, hint_rect)

            elif state == "difficulty":
                title = big_font.render("Choose Difficulty", True, WHITE)
                title_rect = title.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 140))
                screen.blit(title, title_rect)

                # Easy
                pygame.draw.rect(screen, (20, 20, 20), diff_easy_rect, border_radius=10)
                pygame.draw.rect(screen, GOLD, diff_easy_rect, width=2, border_radius=10)
                t_easy = big_font.render("Easy", True, WHITE)
                t_easy_rect = t_easy.get_rect(center=diff_easy_rect.center)
                screen.blit(t_easy, t_easy_rect)

                # Normal
                pygame.draw.rect(screen, (20, 20, 20), diff_norm_rect, border_radius=10)
                pygame.draw.rect(screen, GOLD, diff_norm_rect, width=2, border_radius=10)
                t_norm = big_font.render("Normal", True, WHITE)
                t_norm_rect = t_norm.get_rect(center=diff_norm_rect.center)
                screen.blit(t_norm, t_norm_rect)

                # Hard
                pygame.draw.rect(screen, (20, 20, 20), diff_hard_rect, border_radius=10)
                pygame.draw.rect(screen, GOLD, diff_hard_rect, width=2, border_radius=10)
                t_hard = big_font.render("Hard", True, WHITE)
                t_hard_rect = t_hard.get_rect(center=diff_hard_rect.center)
                screen.blit(t_hard, t_hard_rect)

                # Back button
                pygame.draw.rect(screen, (20, 20, 20), back_button_rect, border_radius=10)
                pygame.draw.rect(screen, GOLD, back_button_rect, width=2, border_radius=10)
                t_back = font.render("Back to Menu", True, WHITE)
                t_back_rect = t_back.get_rect(center=back_button_rect.center)
                screen.blit(t_back, t_back_rect)

            elif state == "rules":
                title = big_font.render("Game Rules", True, WHITE)
                title_rect = title.get_rect(center=(SCREEN_WIDTH // 2, 80))
                screen.blit(title, title_rect)

                rules_lines = [
                    "SPACE or arrow keys — hit in time with the kobyz beat.",
                    "SPACE — strong burst that banishes nearby shadow wolves.",
                    "1 / 2 / 3 — change difficulty (Easy / Normal / Hard).",
                    "R — restart after game over.",
                    "ESC — return to menu.",
                ]
                y = 150
                for line in rules_lines:
                    surf = font.render(line, True, WHITE)
                    rect = surf.get_rect(center=(SCREEN_WIDTH // 2, y))
                    screen.blit(surf, rect)
                    y += 32

                # Back button
                pygame.draw.rect(screen, (20, 20, 20), back_button_rect, border_radius=10)
                pygame.draw.rect(screen, GOLD, back_button_rect, width=2, border_radius=10)
                t_back = font.render("Back to Menu", True, WHITE)
                t_back_rect = t_back.get_rect(center=back_button_rect.center)
                screen.blit(t_back, t_back_rect)

            pygame.display.flip()
            continue

        # From here on we are in active gameplay state
        score_time += dt

        # Tempo ramps up with time (difficulty)
        bpm = min(bpm_max, bpm + bpm_ramp_per_sec * dt)

        # Beat logic: detect missed beats
        b = current_beat(now)
        if b != prev_beat:
            if prev_beat >= 0 and not beat_hit and not game_over:
                # Miss penalty
                energy = max(0.0, energy - ENERGY_PENALTY_MISS)
                streak = 0
                if error is not None:
                    error.play()
                last_feedback, last_feedback_t = "MISS", 0.55
            beat_hit = False
            prev_beat = b
            # soft click on each beat
            if click is not None and not game_over:
                click.play()

        # Spawn wolves: more often when music weak; also scales with time
        if not game_over:
            spawn_timer -= dt
            base_spawn = 1.35 - 0.55 * energy  # weak music -> faster spawns
            # Start easier, then ramp harder (smaller factor over time)
            progress = min(1.0, score_time / 85.0)
            time_factor = 1.15 - 0.55 * progress
            if spawn_timer <= 0.0:
                # chance for shadow wolves increases over time
                shadow_chance = 0.06 + 0.20 * min(1.0, score_time / 95.0)
                kind = "shadow" if random.random() < shadow_chance else "normal"
                wolves.append(spawn_wolf(kind))
                spawn_timer = random.uniform(0.85, 1.45) * base_spawn * time_factor

        # Passive energy decay (music must be maintained)
        if not game_over:
            energy = max(0.0, energy - ENERGY_DECAY_PER_SEC * dt * (0.65 + 0.35 * (1.0 - energy)))

        shield_r = MIN_SHIELD_R + (MAX_SHIELD_R - MIN_SHIELD_R) * energy

        # Update waves
        for wv in waves:
            wv.update(dt)
        waves = [wv for wv in waves if wv.alive and wv.radius < 1200]

        # Update wolves
        if not game_over:
            for w in wolves:
                w.update(dt, energy, shield_r, waves)
            wolves = [w for w in wolves if w.hp > 0.0]

            # Lose condition: wolf reaches center
            for w in wolves:
                if (w.pos - CENTER).length() < 22.0:
                    game_over = True
                    game_over_reason = "The darkness reached Korkyt."
                    if howl_channel is not None:
                        howl_channel.set_volume(0.0)
                    break

        # Howl volume scales with nearest wolf
        if howl_channel is not None and not game_over:
            nearest = None
            for w in wolves:
                d = (w.pos - CENTER).length()
                nearest = d if nearest is None else min(nearest, d)
            if nearest is None:
                howl_channel.set_volume(0.0)
            else:
                # 0 volume far, loud near
                v = _clamp(1.0 - (nearest / 520.0), 0.0, 1.0)
                howl_channel.set_volume(0.05 + 0.55 * (v**1.8))

        # ---------------------------
        # Render gameplay
        # ---------------------------
        if BG_IMAGE is not None:
            screen.blit(BG_IMAGE, (0, 0))
            # Лёгкое затемнение/осветление от силы музыки
            if energy < 1.0:
                dark = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
                dark.fill((0, 0, 0, int(120 * (1.0 - energy))))
                screen.blit(dark, (0, 0))
        else:
            world_brightness = 0.35 + 0.65 * energy
            draw_sunset(screen, world_brightness)

        # Protective dome
        dome = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        dome_alpha = int(35 + 95 * energy)
        pygame.draw.circle(dome, (GOLD_DIM[0], GOLD_DIM[1], GOLD_DIM[2], dome_alpha), (int(CENTER.x), int(CENTER.y)), int(shield_r), width=0)
        pygame.draw.circle(dome, (0, 0, 0, 165), (int(CENTER.x), int(CENTER.y)), int(shield_r - 2), width=0)
        screen.blit(dome, (0, 0))

        # Waves (rings)
        ring = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        for wv in waves:
            a = int(_clamp(wv.alpha, 0.0, 255.0))
            col = (wv.color[0], wv.color[1], wv.color[2], a)
            pygame.draw.circle(ring, col, (int(CENTER.x), int(CENTER.y)), int(wv.radius), width=wv.width)
        screen.blit(ring, (0, 0))

        # Korkyt sprite + kobyz sparks
        if KORKYT_IMAGE is not None:
            k_rect = KORKYT_IMAGE.get_rect(center=(int(CENTER.x), int(CENTER.y)))
            screen.blit(KORKYT_IMAGE, k_rect)
        else:
            pygame.draw.circle(screen, (0, 0, 0), (int(CENTER.x), int(CENTER.y)), 18)  # head
            pygame.draw.rect(
                screen,
                (0, 0, 0),
                pygame.Rect(int(CENTER.x) - 8, int(CENTER.y) + 10, 16, 34),
                border_radius=6,
            )  # body
            pygame.draw.line(
                screen,
                (0, 0, 0),
                (int(CENTER.x) - 6, int(CENTER.y) + 24),
                (int(CENTER.x) - 40, int(CENTER.y) + 6),
                5,
            )  # kobyz neck
        if energy > 0.35:
            spark_count = int(2 + 8 * energy)
            for _ in range(spark_count):
                ang = random.random() * math.tau
                r = random.uniform(10.0, 30.0) * energy
                sp = pygame.Vector2(math.cos(ang), math.sin(ang)) * r
                pygame.draw.circle(screen, GOLD, (int(CENTER.x - 24 + sp.x), int(CENTER.y + 10 + sp.y)), 1 + int(energy * 2))

        # Wolves
        for w in wolves:
            w.draw(screen)

        # Strong burst visualization
        if now - strong_burst_time < strong_burst_duration:
            t = (now - strong_burst_time) / strong_burst_duration
            r = (0.15 + 0.85 * t) * strong_burst_max_r * (0.75 + 0.25 * energy)
            a = int(200 * (1.0 - t))
            bsurf = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            pygame.draw.circle(bsurf, (255, 245, 210, a), (int(CENTER.x), int(CENTER.y)), int(r), width=10)
            screen.blit(bsurf, (0, 0))

        # Frost overlay when danger is close
        nearest = None
        for w in wolves:
            d = (w.pos - CENTER).length()
            nearest = d if nearest is None else min(nearest, d)
        if nearest is not None:
            frost_amt = _clamp(1.0 - nearest / 260.0, 0.0, 1.0)
            if frost_amt > 0:
                frost = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
                frost.fill((FROST[0], FROST[1], FROST[2], int(115 * frost_amt)))
                screen.blit(frost, (0, 0))

        # Minimal UI: campfire/energy orb + tempo
        ui = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        orb_x, orb_y = 70, 70
        pygame.draw.circle(ui, (0, 0, 0, 170), (orb_x, orb_y), 26)
        pygame.draw.circle(ui, (GOLD[0], GOLD[1], GOLD[2], int(90 + 140 * energy)), (orb_x, orb_y), int(8 + 18 * energy))
        pygame.draw.circle(ui, (255, 255, 255, 50), (orb_x - 6, orb_y - 8), int(4 + 6 * energy))
        screen.blit(ui, (0, 0))

        # Beat hint (subtle pulse at top)
        ph = beat_phase(now) / beat_interval()
        pulse = 1.0 - abs(ph - 0.5) * 2.0
        pulse = max(0.0, pulse)
        pygame.draw.circle(screen, GOLD, (SCREEN_WIDTH // 2, 70), int(6 + 10 * pulse), width=2)

        # Text (keep minimal)
        draw_text(
            screen,
            f"BPM: {int(bpm)}   Streak: {streak}   {difficulty.upper()} (1/2/3)",
            font,
            WHITE,
            (SCREEN_WIDTH - 195, 24),
        )

        if last_feedback_t > 0:
            last_feedback_t = max(0.0, last_feedback_t - dt)
            col = GOLD if last_feedback in ("PERFECT", "GREAT") else WHITE if last_feedback == "OK" else (240, 200, 200)
            draw_text(screen, last_feedback, big_font, col, (SCREEN_WIDTH // 2, int(CENTER.y - 95)))

        if game_over:
            overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 170))
            screen.blit(overlay, (0, 0))
            draw_text(screen, "Game Over", big_font, (255, 210, 180), (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 30))
            draw_text(screen, game_over_reason, font, (255, 230, 210), (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 10))
            draw_text(screen, "Press R to restart, ESC for menu.", font, WHITE, (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 46))

        # Back-to-menu button during gameplay (включая после поражения)
        pygame.draw.rect(screen, (20, 20, 20), back_button_rect, border_radius=10)
        pygame.draw.rect(screen, GOLD, back_button_rect, width=2, border_radius=10)
        t_back_game = font.render("To Menu", True, WHITE)
        t_back_game_rect = t_back_game.get_rect(center=back_button_rect.center)
        screen.blit(t_back_game, t_back_game_rect)

        pygame.display.flip()
        continue

        # Passive energy decay (music must be maintained)
        if not game_over:
            energy = max(0.0, energy - ENERGY_DECAY_PER_SEC * dt * (0.65 + 0.35 * (1.0 - energy)))

        shield_r = MIN_SHIELD_R + (MAX_SHIELD_R - MIN_SHIELD_R) * energy

        # Update waves
        for wv in waves:
            wv.update(dt)
        waves = [wv for wv in waves if wv.alive and wv.radius < 1200]

        # Update wolves
        if not game_over:
            for w in wolves:
                w.update(dt, energy, shield_r, waves)
            wolves = [w for w in wolves if w.hp > 0.0]

            # Lose condition: wolf reaches center
            for w in wolves:
                if (w.pos - CENTER).length() < 22.0:
                    game_over = True
                    game_over_reason = "The darkness reached Korkyt."
                    if howl_channel is not None:
                        howl_channel.set_volume(0.0)
                    break

        # Howl volume scales with nearest wolf
        if howl_channel is not None and not game_over:
            nearest = None
            for w in wolves:
                d = (w.pos - CENTER).length()
                nearest = d if nearest is None else min(nearest, d)
            if nearest is None:
                howl_channel.set_volume(0.0)
            else:
                # 0 volume far, loud near
                v = _clamp(1.0 - (nearest / 520.0), 0.0, 1.0)
                howl_channel.set_volume(0.05 + 0.55 * (v**1.8))

        # ---------------------------
        # Render
        # ---------------------------
        if BG_IMAGE is not None:
            screen.blit(BG_IMAGE, (0, 0))
            # Лёгкое затемнение/осветление от силы музыки
            if energy < 1.0:
                dark = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
                dark.fill((0, 0, 0, int(120 * (1.0 - energy))))
                screen.blit(dark, (0, 0))
        else:
            world_brightness = 0.35 + 0.65 * energy
            draw_sunset(screen, world_brightness)

        # Protective dome
        dome = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        dome_alpha = int(35 + 95 * energy)
        pygame.draw.circle(dome, (GOLD_DIM[0], GOLD_DIM[1], GOLD_DIM[2], dome_alpha), (int(CENTER.x), int(CENTER.y)), int(shield_r), width=0)
        pygame.draw.circle(dome, (0, 0, 0, 165), (int(CENTER.x), int(CENTER.y)), int(shield_r - 2), width=0)
        screen.blit(dome, (0, 0))

        # Waves (rings)
        ring = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        for wv in waves:
            a = int(_clamp(wv.alpha, 0.0, 255.0))
            col = (wv.color[0], wv.color[1], wv.color[2], a)
            pygame.draw.circle(ring, col, (int(CENTER.x), int(CENTER.y)), int(wv.radius), width=wv.width)
        screen.blit(ring, (0, 0))

        # Korkyt sprite + kobyz sparks
        if KORKYT_IMAGE is not None:
            k_rect = KORKYT_IMAGE.get_rect(center=(int(CENTER.x), int(CENTER.y)))
            screen.blit(KORKYT_IMAGE, k_rect)
        else:
            pygame.draw.circle(screen, (0, 0, 0), (int(CENTER.x), int(CENTER.y)), 18)  # head
            pygame.draw.rect(
                screen,
                (0, 0, 0),
                pygame.Rect(int(CENTER.x) - 8, int(CENTER.y) + 10, 16, 34),
                border_radius=6,
            )  # body
            pygame.draw.line(
                screen,
                (0, 0, 0),
                (int(CENTER.x) - 6, int(CENTER.y) + 24),
                (int(CENTER.x) - 40, int(CENTER.y) + 6),
                5,
            )  # kobyz neck
        if energy > 0.35:
            spark_count = int(2 + 8 * energy)
            for _ in range(spark_count):
                ang = random.random() * math.tau
                r = random.uniform(10.0, 30.0) * energy
                sp = pygame.Vector2(math.cos(ang), math.sin(ang)) * r
                pygame.draw.circle(screen, GOLD, (int(CENTER.x - 24 + sp.x), int(CENTER.y + 10 + sp.y)), 1 + int(energy * 2))

        # Wolves
        for w in wolves:
            w.draw(screen)

        # Strong burst visualization
        if now - strong_burst_time < strong_burst_duration:
            t = (now - strong_burst_time) / strong_burst_duration
            r = (0.15 + 0.85 * t) * strong_burst_max_r * (0.75 + 0.25 * energy)
            a = int(200 * (1.0 - t))
            bsurf = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            pygame.draw.circle(bsurf, (255, 245, 210, a), (int(CENTER.x), int(CENTER.y)), int(r), width=10)
            screen.blit(bsurf, (0, 0))

        # Frost overlay when danger is close
        nearest = None
        for w in wolves:
            d = (w.pos - CENTER).length()
            nearest = d if nearest is None else min(nearest, d)
        if nearest is not None:
            frost_amt = _clamp(1.0 - nearest / 260.0, 0.0, 1.0)
            if frost_amt > 0:
                frost = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
                frost.fill((FROST[0], FROST[1], FROST[2], int(115 * frost_amt)))
                screen.blit(frost, (0, 0))

        # Minimal UI: campfire/energy orb + tempo
        ui = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        orb_x, orb_y = 70, 70
        pygame.draw.circle(ui, (0, 0, 0, 170), (orb_x, orb_y), 26)
        pygame.draw.circle(ui, (GOLD[0], GOLD[1], GOLD[2], int(90 + 140 * energy)), (orb_x, orb_y), int(8 + 18 * energy))
        pygame.draw.circle(ui, (255, 255, 255, 50), (orb_x - 6, orb_y - 8), int(4 + 6 * energy))
        screen.blit(ui, (0, 0))

        # Beat hint (subtle pulse at top)
        ph = beat_phase(now) / beat_interval()
        pulse = 1.0 - abs(ph - 0.5) * 2.0
        pulse = max(0.0, pulse)
        pygame.draw.circle(screen, GOLD, (SCREEN_WIDTH // 2, 70), int(6 + 10 * pulse), width=2)

        # Text (keep minimal)
        draw_text(
            screen,
            f"BPM: {int(bpm)}   Streak: {streak}   {difficulty.upper()} (1/2/3)",
            font,
            WHITE,
            (SCREEN_WIDTH - 195, 24),
        )

        if last_feedback_t > 0:
            last_feedback_t = max(0.0, last_feedback_t - dt)
            col = GOLD if last_feedback in ("PERFECT", "GREAT") else WHITE if last_feedback == "OK" else (240, 200, 200)
            draw_text(screen, last_feedback, big_font, col, (SCREEN_WIDTH // 2, int(CENTER.y - 95)))

        if game_over:
            overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 170))
            screen.blit(overlay, (0, 0))
            draw_text(screen, "Game Over", big_font, (255, 210, 180), (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 30))
            draw_text(screen, game_over_reason, font, (255, 230, 210), (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 10))
            draw_text(screen, "Press R to restart, ESC for menu.", font, WHITE, (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 46))

        pygame.display.flip()

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
