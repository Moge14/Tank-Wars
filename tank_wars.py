"""
Tank Wars - Prototype
=====================
Drive your tank down a winding road, dodge or destroy everything that comes at
you, and spend your coins on upgrades.

Controls:
  WASD / arrow keys - drive the tank
  Mouse             - aim the turret
  Left click / Space - fire (hold to keep firing)
  U                 - open/close the upgrade menu (spend coins)
  1-8               - buy an upgrade while the menu is open
  R                 - restart after game over
  ESC               - quit

The route changes as you advance:
  Highway   - wide road with gentle bends
  Downtown  - narrow streets; convoys cross at the intersections
  Canyon    - winding road full of boulders

Leaving the road bogs the tank down in mud. Everything is drawn with simple
shapes, so no image files are needed.
"""

import bisect
import math
import os
import random
import sys

import pygame

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
pygame.init()

WIDTH, HEIGHT = 480, 720
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Tank Wars - Prototype")
clock = pygame.time.Clock()
FPS = 60

font_small = pygame.font.SysFont("consolas", 16)
font_msg = pygame.font.SysFont("consolas", 18, bold=True)
font_big = pygame.font.SysFont("consolas", 40, bold=True)

WHITE = (240, 240, 240)
BLACK = (15, 15, 20)
GRAY = (70, 70, 80)
RED = (220, 60, 60)
GREEN = (70, 200, 100)
YELLOW = (240, 210, 60)
BLUE = (80, 160, 240)
PURPLE = (170, 90, 220)
CYAN = (70, 210, 220)
ORANGE = (220, 140, 50)
PINK = (230, 70, 150)

MAX_LEVEL = 5
SCROLL = 2.4        # how fast the route advances (pixels per frame)
STEP = 6            # road is drawn in horizontal bands this tall
IH = 130            # height of an intersection
CH = 120            # scenery chunk size
PLAYER_BULLET = "player"
ENEMY_BULLET = "enemy"
SAVE_PATH = os.path.join(os.path.expanduser("~"), ".tank_wars_best")


def clamp(value, low, high):
    return max(low, min(high, value))


def turn_toward(angle, target, step):
    diff = (target - angle + math.pi) % math.tau - math.pi
    return angle + clamp(diff, -step, step)


def hrand(i, side, k):
    """Deterministic pseudo-random 0..1 so scenery never flickers."""
    h = (i * 73856093) ^ ((side + 3) * 19349663) ^ (k * 83492791)
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return (h & 0xFFFF) / 65535.0


def load_best():
    try:
        with open(SAVE_PATH) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def save_best(score):
    try:
        with open(SAVE_PATH, "w") as f:
            f.write(str(int(score)))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# The route: a chain of road sections with different looks and shapes
# ---------------------------------------------------------------------------
THEMES = {
    "grass": dict(side=(36, 74, 48), side2=(42, 84, 55), road=(50, 52, 60), edge=(WHITE, 4)),
    "city": dict(side=(52, 54, 62), side2=(57, 59, 68), road=(44, 46, 54), edge=((120, 122, 134), 8)),
    "sand": dict(side=(150, 112, 68), side2=(160, 122, 76), road=(70, 66, 66), edge=(WHITE, 4)),
}

KINDS = {
    "highway": dict(name="Highway", width=360, theme="grass",
                    amp=(0, 45), waves=(1, 2), length=(2000, 3000)),
    "downtown": dict(name="Downtown", width=230, theme="city",
                     amp=(0, 0), waves=(1, 1), length=(2400, 3200)),
    "canyon": dict(name="Canyon Road", width=290, theme="sand",
                   amp=(55, 75), waves=(2, 3), length=(2200, 3000)),
}

ZONE_MESSAGES = {
    "highway": "HIGHWAY: wide open, watch your flanks.",
    "downtown": "DOWNTOWN: convoys cross at intersections!",
    "canyon": "CANYON ROAD: boulders and tight bends.",
}


class Section:
    def __init__(self, start, kind, prev_width, first=False):
        cfg = KINDS[kind]
        self.kind = kind
        self.theme = cfg["theme"]
        self.name = cfg["name"]
        self.start = start
        self.length = 2600 if first else random.randint(*cfg["length"])
        self.end = start + self.length
        self.width = cfg["width"]
        self.prev_width = prev_width
        self.taper = 260
        self.amp = 0 if first else random.uniform(*cfg["amp"]) * random.choice((-1, 1))
        self.waves = random.randint(*cfg["waves"])
        self.intersections = []
        if kind == "downtown":
            d = start + 700
            while d < self.end - 500:
                self.intersections.append(d)
                d += random.randint(800, 1100)

    def center(self, D):
        if self.amp == 0:
            return WIDTH / 2
        t = clamp((D - self.start) / self.length, 0, 1)
        return WIDTH / 2 + self.amp * math.sin(2 * math.pi * self.waves * t) * math.sin(math.pi * t)

    def width_at(self, D):
        if self.prev_width is None:
            return self.width
        local = D - self.start
        if local >= self.taper:
            return self.width
        u = clamp(local / self.taper, 0, 1)
        u = u * u * (3 - 2 * u)
        return self.prev_width + (self.width - self.prev_width) * u


class Track:
    """Endless chain of sections. D is distance along the route in pixels."""
    def __init__(self):
        self.sections = []
        self.starts = []
        self._add(Section(-600, "highway", None, first=True))

    def _add(self, section):
        self.sections.append(section)
        self.starts.append(section.start)

    def ensure(self, D):
        while self.sections[-1].end < D:
            last = self.sections[-1]
            kinds = [k for k in KINDS if k != last.kind]
            self._add(Section(last.end, random.choice(kinds), last.width))

    def index_at(self, D):
        self.ensure(D + 1)
        return max(0, bisect.bisect_right(self.starts, D) - 1)

    def section_at(self, D):
        return self.sections[self.index_at(D)]


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def rect_pts(cx, cy, ang, x0, y0, x1, y1):
    """Corners of a rectangle given in the tank's own frame (forward = +x)."""
    c, s = math.cos(ang), math.sin(ang)
    return [(cx + px * c - py * s, cy + px * s + py * c) for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


def draw_tank(surf, cx, cy, length, width, body_ang, turret_ang, hull, turret, barrel):
    # PLACEHOLDER: replace with rotated sprites (pygame.transform.rotate)
    tw = width * 0.3
    for y0 in (-width / 2, width / 2 - tw):
        pts = rect_pts(cx, cy, body_ang, -length / 2, y0, length / 2, y0 + tw)
        pygame.draw.polygon(surf, (38, 40, 48), pts)
        pygame.draw.polygon(surf, (95, 98, 110), pts, 1)
    pts = rect_pts(cx, cy, body_ang, -length * 0.42, -width * 0.32, length * 0.42, width * 0.32)
    pygame.draw.polygon(surf, hull, pts)
    pygame.draw.polygon(surf, WHITE, pts, 2)
    if barrel > 0:
        end = (cx + math.cos(turret_ang) * barrel, cy + math.sin(turret_ang) * barrel)
        pygame.draw.line(surf, (30, 32, 40), (cx, cy), end, max(4, int(width * 0.16)))
    pygame.draw.circle(surf, turret, (int(cx), int(cy)), int(width * 0.28))
    pygame.draw.circle(surf, WHITE, (int(cx), int(cy)), int(width * 0.28), 2)


def draw_bar(surf, x, y, w, pct, color):
    pygame.draw.rect(surf, BLACK, (x, y - 8, w, 4))
    pygame.draw.rect(surf, color, (x, y - 8, w * clamp(pct, 0, 1), 4))


# ---------------------------------------------------------------------------
# Particles
# ---------------------------------------------------------------------------
class Particle:
    def __init__(self, x, y, vx, vy, life, color, size):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.life = life
        self.color = color
        self.size = size

    def update(self):
        self.x += self.vx
        self.y += self.vy + SCROLL * 0.5
        self.life -= 1

    def draw(self, surf):
        pygame.draw.rect(surf, self.color, (int(self.x), int(self.y), self.size, self.size))


# ---------------------------------------------------------------------------
# Player tank
# ---------------------------------------------------------------------------
class Player:
    SIZE = 28

    def __init__(self):
        self.x = WIDTH // 2 - self.SIZE // 2
        self.y = HEIGHT - 150
        self.vx = self.vy = 0.0
        self.body_angle = -math.pi / 2
        self.turret_angle = -math.pi / 2
        self.base_speed = 4.0

        # upgrade levels
        self.speed_level = 0
        self.agility_level = 0
        self.weapon_level = 0
        self.fire_rate_level = 0
        self.health_level = 0
        self.multishot_level = 0
        self.regen_level = 0
        self.magnet_level = 0

        self.base_max_health = 100
        self.health = self.max_health
        self.coins = 0
        self.shoot_cooldown = 0
        self.invincible_timer = 0
        self.regen_timer = 0
        self.offroad = False

    @property
    def rect(self):
        return pygame.Rect(int(self.x), int(self.y), self.SIZE, self.SIZE)

    @property
    def cx(self):
        return self.x + self.SIZE / 2

    @property
    def cy(self):
        return self.y + self.SIZE / 2

    @property
    def speed(self):
        return self.base_speed + self.speed_level * 0.8

    @property
    def accel(self):
        # Agility: the tank answers the controls faster
        return 0.45 + self.agility_level * 0.1

    @property
    def turret_turn(self):
        # Agility also swings the turret round faster (radians per frame)
        return 0.10 + self.agility_level * 0.03

    @property
    def fire_delay(self):
        return max(5, 20 - self.fire_rate_level * 3)

    @property
    def damage(self):
        return 10 + self.weapon_level * 6

    @property
    def max_health(self):
        return self.base_max_health + self.health_level * 20

    @property
    def shot_count(self):
        return 1 + self.multishot_level

    @property
    def magnet_radius(self):
        return self.magnet_level * 45

    def handle_input(self, keys, mouse_pos, game):
        dx = (1 if keys[pygame.K_RIGHT] or keys[pygame.K_d] else 0) - (1 if keys[pygame.K_LEFT] or keys[pygame.K_a] else 0)
        dy = (1 if keys[pygame.K_DOWN] or keys[pygame.K_s] else 0) - (1 if keys[pygame.K_UP] or keys[pygame.K_w] else 0)
        if dx and dy:
            dx *= 0.7071
            dy *= 0.7071

        lo, hi = game.bounds(self.cy)
        self.offroad = not (lo < self.cx < hi)
        top = self.speed * (0.6 if self.offroad else 1.0)

        if dx or dy:
            self.vx += dx * self.accel
            self.vy += dy * self.accel
        else:
            self.vx *= 0.8
            self.vy *= 0.8
        sp = math.hypot(self.vx, self.vy)
        if sp > top:
            self.vx, self.vy = self.vx / sp * top, self.vy / sp * top
            sp = top

        self.x += self.vx
        self.y += self.vy
        if self.x < 0 or self.x > WIDTH - self.SIZE:
            self.x = clamp(self.x, 0, WIDTH - self.SIZE)
            self.vx = 0
        if self.y < 0 or self.y > HEIGHT - self.SIZE:
            self.y = clamp(self.y, 0, HEIGHT - self.SIZE)
            self.vy = 0

        if sp > 0.5:
            self.body_angle = turn_toward(self.body_angle, math.atan2(self.vy, self.vx), 0.14)
        aim = math.atan2(mouse_pos[1] - self.cy, mouse_pos[0] - self.cx)
        self.turret_angle = turn_toward(self.turret_angle, aim, self.turret_turn)

        if self.shoot_cooldown > 0:
            self.shoot_cooldown -= 1
        if self.invincible_timer > 0:
            self.invincible_timer -= 1

        if self.regen_level > 0:
            self.regen_timer += 1
            if self.regen_timer >= FPS:  # once per second
                self.regen_timer = 0
                self.health = min(self.max_health, self.health + self.regen_level * 2)

        if self.offroad and sp > 1.5 and random.random() < 0.5:
            game.emit(self.cx, self.cy + 10, (110, 90, 60), n=1, speed=1.2, life=18)

    def try_shoot(self, game):
        if self.shoot_cooldown > 0:
            return
        count = self.shot_count
        spread = math.radians(10)
        speed = 10
        for i in range(count):
            ang = self.turret_angle + (i - (count - 1) / 2) * spread
            sx = self.cx + math.cos(self.turret_angle) * 24
            sy = self.cy + math.sin(self.turret_angle) * 24
            game.bullets.append(Bullet(sx, sy, math.cos(ang) * speed, math.sin(ang) * speed,
                                       self.damage, PLAYER_BULLET))
        game.emit(self.cx + math.cos(self.turret_angle) * 26, self.cy + math.sin(self.turret_angle) * 26,
                  YELLOW, n=3, speed=2, life=8, size=3)
        self.shoot_cooldown = self.fire_delay

    def draw(self, surf):
        flicker = self.invincible_timer % 10 < 5
        hull = BLUE if (self.invincible_timer == 0 or flicker) else (150, 200, 255)
        draw_tank(surf, self.cx, self.cy, 44, 32, self.body_angle, self.turret_angle,
                  hull, (120, 190, 255), 28)


# ---------------------------------------------------------------------------
# Bullets
# ---------------------------------------------------------------------------
class Bullet:
    def __init__(self, x, y, vx, vy, damage, owner):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.damage = damage
        self.owner = owner
        self.radius = 4

    @property
    def rect(self):
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius),
                           self.radius * 2, self.radius * 2)

    def update(self):
        self.x += self.vx
        self.y += self.vy

    def offscreen(self):
        return (self.y < -10 or self.y > HEIGHT + 10
                or self.x < -10 or self.x > WIDTH + 10)

    def draw(self, surf):
        color = YELLOW if self.owner == PLAYER_BULLET else RED
        pygame.draw.circle(surf, color, (int(self.x), int(self.y)), self.radius)
        pygame.draw.circle(surf, BLACK, (int(self.x), int(self.y)), self.radius, 1)


# ---------------------------------------------------------------------------
# Obstacles (destructible, no shooting back, they stay put on the road)
# ---------------------------------------------------------------------------
class Obstacle:
    def __init__(self, game):
        _, _, section, _ = game.road(-20)
        self.kind = section.kind
        self.size = random.randint(28, 44)
        self.frac = random.uniform(-0.40, 0.40)
        self.y = -self.size
        self.hp = 20
        self.max_hp = self.hp
        self.collision_damage = max(8, int(self.size / 3))
        self.dead = False
        self.x = 0
        self._place(game)

    def _place(self, game):
        c, wd, _, _ = game.road(self.y + self.size / 2)
        self.x = c + self.frac * wd - self.size / 2

    @property
    def rect(self):
        return pygame.Rect(int(self.x), int(self.y), self.size, self.size)

    def update(self, game):
        self.y += SCROLL
        self._place(game)

    def offscreen(self):
        return self.y > HEIGHT + 10

    def draw(self, surf):
        # PLACEHOLDER: replace with crate / barrier / boulder sprites
        r = self.rect
        if self.kind == "highway":
            pygame.draw.rect(surf, (150, 108, 62), r, border_radius=3)
            pygame.draw.rect(surf, (96, 66, 36), r, 3, border_radius=3)
            pygame.draw.line(surf, (96, 66, 36), r.topleft, r.bottomright, 3)
            pygame.draw.line(surf, (96, 66, 36), r.topright, r.bottomleft, 3)
        elif self.kind == "downtown":
            pygame.draw.rect(surf, (160, 162, 170), r, border_radius=4)
            for i in range(0, r.h, 12):
                pygame.draw.rect(surf, YELLOW if (i // 12) % 2 == 0 else BLACK, (r.x, r.y + i, r.w, 6))
            pygame.draw.rect(surf, (70, 72, 80), r, 2, border_radius=4)
        else:
            pygame.draw.ellipse(surf, (112, 84, 54), r)
            pygame.draw.ellipse(surf, (140, 108, 70), (r.x + 4, r.y + 3, r.w * 0.55, r.h * 0.45))
            pygame.draw.ellipse(surf, (60, 44, 28), r, 2)
        draw_bar(surf, self.x, self.y, self.size, self.hp / self.max_hp, GREEN)


# ---------------------------------------------------------------------------
# Enemies
# ---------------------------------------------------------------------------
class Enemy:
    """Base 'Gunner': patrols the road side to side, fires aimed single shots."""
    color = PURPLE
    score_value = 25
    coin_range = (3, 6)
    collision_damage = 20

    def __init__(self, game):
        self.width, self.height = 34, 34
        lo, hi = game.bounds(-20)
        self.x = random.uniform(lo, max(lo, hi - self.width))
        self.y = -self.height
        self.vy = random.uniform(1.2, 2.0)
        self.vx = random.choice([-1, 1]) * random.uniform(0.5, 1.5)
        self.hp = 30 * game.hp_scale
        self.max_hp = self.hp
        self.shoot_timer = random.randint(30, 90)
        self.body_angle = math.pi / 2
        self.aim = math.pi / 2
        self.barrel = 20
        self.dead = False

    @property
    def rect(self):
        return pygame.Rect(int(self.x), int(self.y), self.width, self.height)

    @property
    def cx(self):
        return self.x + self.width / 2

    @property
    def cy(self):
        return self.y + self.height / 2

    def update(self, game):
        self.move(game)
        p = game.player
        self.aim = math.atan2(p.cy - self.cy, p.cx - self.cx)
        if math.hypot(self.vx, self.vy) > 0.2:
            self.body_angle = turn_toward(self.body_angle, math.atan2(self.vy, self.vx), 0.1)
        self.maybe_shoot(game)

    def move(self, game):
        self.y += self.vy
        self.x += self.vx
        lo, hi = game.bounds(self.cy)
        if self.x < lo:
            self.x, self.vx = lo, abs(self.vx)
        elif self.x > hi - self.width:
            self.x, self.vx = hi - self.width, -abs(self.vx)

    def maybe_shoot(self, game):
        self.shoot_timer -= 1
        if self.shoot_timer <= 0 and 0 < self.y < HEIGHT * 0.85:
            self.shoot(game)
            self.shoot_timer = int(random.randint(60, 120) / game.fire_scale)

    def shoot(self, game):
        ang = self.aim + random.uniform(-0.1, 0.1)
        game.bullets.append(Bullet(self.cx + math.cos(ang) * 20, self.cy + math.sin(ang) * 20,
                                   math.cos(ang) * 4.6, math.sin(ang) * 4.6, 10, ENEMY_BULLET))

    def offscreen(self):
        return self.y > HEIGHT + 10

    def draw(self, surf):
        # PLACEHOLDER: replace with enemy tank sprites
        light = tuple(min(255, c + 50) for c in self.color)
        draw_tank(surf, self.cx, self.cy, self.height, self.width, self.body_angle, self.aim,
                  self.color, light, self.barrel)
        draw_bar(surf, self.x, self.y, self.width, self.hp / self.max_hp, RED)


class ZigzagEnemy(Enemy):
    """Raider: weaves across the road in a sine wave; fast and hard to hit."""
    color = CYAN
    score_value = 35
    coin_range = (4, 7)

    def __init__(self, game):
        super().__init__(game)
        self.hp = 22 * game.hp_scale
        self.max_hp = self.hp
        self.vy = random.uniform(1.6, 2.2)
        self.origin_frac = random.uniform(-0.25, 0.25)
        self.amplitude = random.uniform(50, 100)
        self.frequency = random.uniform(0.03, 0.05)
        self.t = random.uniform(0, math.pi * 2)

    def move(self, game):
        self.t += self.frequency
        self.y += self.vy
        c, wd, _, _ = game.road(self.cy)
        self.x = c + self.origin_frac * wd + math.sin(self.t) * self.amplitude - self.width / 2
        self.vx = math.cos(self.t) * self.amplitude * self.frequency
        lo, hi = game.bounds(self.cy)
        self.x = clamp(self.x, lo, max(lo, hi - self.width))


class TankEnemy(Enemy):
    """Heavy tank: slow, heavily armored, fires a 3-way spread."""
    color = ORANGE
    score_value = 60
    coin_range = (8, 12)
    collision_damage = 30

    def __init__(self, game):
        super().__init__(game)
        self.width, self.height = 50, 46
        lo, hi = game.bounds(-20)
        self.x = random.uniform(lo, max(lo, hi - self.width))
        self.hp = 90 * game.hp_scale
        self.max_hp = self.hp
        self.vy = random.uniform(0.6, 1.0)
        self.vx = random.choice([-1, 1]) * random.uniform(0.3, 0.7)
        self.shoot_timer = random.randint(60, 100)
        self.barrel = 30

    def shoot(self, game):
        for off in (-0.31, 0.0, 0.31):
            ang = self.aim + off
            game.bullets.append(Bullet(self.cx + math.cos(ang) * 28, self.cy + math.sin(ang) * 28,
                                       math.cos(ang) * 5, math.sin(ang) * 5, 12, ENEMY_BULLET))


class DiveEnemy(Enemy):
    """Rammer: fast kamikaze truck that veers toward you. No gun, hits hard."""
    color = PINK
    score_value = 20
    coin_range = (2, 4)
    collision_damage = 35

    def __init__(self, game):
        super().__init__(game)
        self.width, self.height = 26, 34
        lo, hi = game.bounds(-20)
        self.x = random.uniform(lo, max(lo, hi - self.width))
        self.hp = 12 * game.hp_scale
        self.max_hp = self.hp
        self.vy = random.uniform(4.0, 5.5)
        self.vx = 0
        self.barrel = 0

    def move(self, game):
        self.vx = clamp((game.player.cx - self.cx) * 0.012, -1.4, 1.4)
        self.y += self.vy
        self.x += self.vx

    def maybe_shoot(self, game):
        pass  # rammers don't shoot


ENEMY_TYPES = [Enemy, ZigzagEnemy, TankEnemy, DiveEnemy]


class Convoy:
    """A truck driving across an intersection. Fixed to the road, so it scrolls with it."""
    width, height = 64, 34
    score_value = 20
    coin_range = (4, 7)
    collision_damage = 25

    def __init__(self, D, direction):
        self.D = D
        self.dir = direction
        self.speed = random.uniform(4.5, 7.0)
        self.x = -40.0 if direction > 0 else WIDTH + 40.0
        self.y = 0.0
        self.hp = 24
        self.max_hp = self.hp
        self.dead = False

    @property
    def rect(self):
        return pygame.Rect(int(self.x), int(self.y), self.width, self.height)

    def update(self, game):
        self.x += self.dir * self.speed
        self.y = game.dist + HEIGHT - self.D - self.height / 2

    def offscreen(self):
        return self.x < -140 or self.x > WIDTH + 140 or self.y > HEIGHT + 100

    def draw(self, surf):
        # PLACEHOLDER: replace with a truck sprite
        r = self.rect
        pygame.draw.rect(surf, (86, 98, 70), r, border_radius=5)
        cab_w = 20
        cab = pygame.Rect(r.right - cab_w, r.y, cab_w, r.h) if self.dir > 0 else pygame.Rect(r.x, r.y, cab_w, r.h)
        pygame.draw.rect(surf, (60, 70, 50), cab, border_radius=5)
        glass_x = cab.right - 8 if self.dir > 0 else cab.x + 2
        pygame.draw.rect(surf, (25, 30, 45), (glass_x, r.y + 5, 6, r.h - 10))
        pygame.draw.rect(surf, WHITE, r, 2, border_radius=5)
        draw_bar(surf, self.x, self.y, self.width, self.hp / self.max_hp, RED)


# ---------------------------------------------------------------------------
# Coins
# ---------------------------------------------------------------------------
class Coin:
    """Sits on the road and scrolls with it; the magnet upgrade pulls it in."""
    def __init__(self, game):
        self.radius = 8
        self.frac = random.uniform(-0.4, 0.4)
        self.ay = -self.radius
        self.ox = self.oy = 0.0
        self.x = self.y = 0.0
        self._sync(game)

    def _sync(self, game):
        c, wd, _, _ = game.road(self.ay)
        self.x = c + self.frac * wd + self.ox
        self.y = self.ay + self.oy

    @property
    def rect(self):
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius),
                           self.radius * 2, self.radius * 2)

    def update(self, game):
        self.ay += SCROLL
        self._sync(game)
        p = game.player
        if p.magnet_radius > 0:
            ddx, ddy = p.cx - self.x, p.cy - self.y
            dist = math.hypot(ddx, ddy)
            if 1 < dist < p.magnet_radius:
                sx, sy = ddx / dist * 5.5, ddy / dist * 5.5
                self.ox += sx
                self.oy += sy
                self.x += sx
                self.y += sy

    def offscreen(self):
        return self.y > HEIGHT + 10

    def draw(self, surf):
        # PLACEHOLDER: replace with coin sprite
        pygame.draw.circle(surf, YELLOW, (int(self.x), int(self.y)), self.radius)
        pygame.draw.circle(surf, BLACK, (int(self.x), int(self.y)), self.radius, 2)


# ---------------------------------------------------------------------------
# Upgrades (same system as Tuff Jet)
# ---------------------------------------------------------------------------
UPGRADES = [
    {"key": "speed", "label": "Speed", "cost_base": 10},
    {"key": "agility", "label": "Agility", "cost_base": 10},
    {"key": "weapon", "label": "Weapon Damage", "cost_base": 20},
    {"key": "fire_rate", "label": "Fire Rate", "cost_base": 20},
    {"key": "health", "label": "Max Health", "cost_base": 18},
    {"key": "multishot", "label": "Multi-Shot", "cost_base": 30},
    {"key": "regen", "label": "Health Regen", "cost_base": 20},
    {"key": "magnet", "label": "Coin Magnet", "cost_base": 18},
]

UPGRADE_KEYS = [
    pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4,
    pygame.K_5, pygame.K_6, pygame.K_7, pygame.K_8,
]


def upgrade_cost(level, base):
    return base + level * 15


def buy_upgrade(player, key):
    """Returns (status, message) where status is 'ok', 'max', or 'poor'."""
    attr = f"{key}_level"
    cfg = next(u for u in UPGRADES if u["key"] == key)
    level = getattr(player, attr)

    if level >= MAX_LEVEL:
        return "max", f"{cfg['label']} is already maxed out!"

    cost = upgrade_cost(level, cfg["cost_base"])
    if player.coins < cost:
        return "poor", f"Need {cost - player.coins} more coins for {cfg['label']}."

    player.coins -= cost
    setattr(player, attr, level + 1)

    if key == "health":
        player.health = min(player.max_health, player.health + 20)

    return "ok", f"{cfg['label']} upgraded to Lv {level + 1}!"


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------
class Game:
    def __init__(self):
        self.world = pygame.Surface((WIDTH, HEIGHT))
        self.best = load_best()
        self.started = False
        self.reset()

    def reset(self):
        self.track = Track()
        self.player = Player()
        self.bullets, self.obstacles, self.enemies = [], [], []
        self.coins, self.convoys, self.particles = [], [], []
        self.spawn_timer = 60
        self.enemy_spawn_timer = 200
        self.coin_spawn_timer = 90
        self.cross_timer = 40
        self.paused_for_upgrade = False
        self.game_over = False
        self.new_best = False
        self.frame = 0
        self.dist = 0.0         # how far the route has scrolled (pixels)
        self.kill_score = 0
        self.kills = 0
        self.shake = 0
        self.message = ""
        self.message_timer = 0
        self.message_color = WHITE
        self.cur_section = self.track.section_at(self.dist + HEIGHT - self.player.y)

    # -- route queries ------------------------------------------------------
    def road(self, y):
        """(center, width, section, in_intersection) of the road at screen row y."""
        D = self.dist + HEIGHT - y
        s = self.track.section_at(D)
        inter = any(a <= D <= a + IH for a in s.intersections)
        return s.center(D), s.width_at(D), s, inter

    def bounds(self, y):
        """Left and right edge of the road (the whole screen at intersections)."""
        c, wd, _, inter = self.road(y)
        if inter:
            return 0, WIDTH
        return c - wd / 2, c + wd / 2

    def visible_intersections(self):
        out = []
        i0 = self.track.index_at(self.dist - IH)
        i1 = self.track.index_at(self.dist + HEIGHT + IH)
        for s in self.track.sections[i0:i1 + 1]:
            for a in s.intersections:
                if a + IH > self.dist and a < self.dist + HEIGHT - 40:
                    out.append(a)
        return out

    # -- difficulty and score -------------------------------------------------
    @property
    def difficulty(self):
        return 1 + min(2.0, self.frame / 5400)      # +1 every 90 seconds, up to 3

    @property
    def hp_scale(self):
        return 1 + (self.difficulty - 1) * 0.5

    @property
    def fire_scale(self):
        return 1 + (self.difficulty - 1) * 0.3

    @property
    def score(self):
        return int(self.dist / 30) + self.kill_score

    # -- helpers ----------------------------------------------------------------
    def say(self, text, color=WHITE, frames=120):
        self.message = text
        self.message_color = color
        self.message_timer = frames

    def emit(self, x, y, color, n=8, speed=3.0, life=24, size=4):
        for _ in range(n):
            ang = random.uniform(0, math.tau)
            spd = random.uniform(0.3, 1.0) * speed
            self.particles.append(Particle(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                                           random.randint(max(2, life // 2), life), color,
                                           random.randint(2, size)))

    def explode(self, x, y, big=False):
        n = 18 if big else 10
        self.emit(x, y, ORANGE, n=n, speed=4)
        self.emit(x, y, YELLOW, n=n // 2, speed=3)
        self.emit(x, y, (90, 90, 95), n=n // 2, speed=2, life=40, size=6)

    def buy(self, key):
        status, msg = buy_upgrade(self.player, key)
        self.say(msg, GREEN if status == "ok" else (YELLOW if status == "poor" else RED), 100)

    def hurt_player(self, amount):
        p = self.player
        if p.invincible_timer > 0:
            return
        p.health -= amount
        p.invincible_timer = 45
        self.shake = 10
        self.emit(p.cx, p.cy, RED, n=8, speed=3)

    # -- spawning ----------------------------------------------------------
    def spawn(self):
        d = self.difficulty
        zone = self.cur_section.kind

        self.spawn_timer -= 1
        if self.spawn_timer <= 0:
            self.obstacles.append(Obstacle(self))
            low, high = (45, 90) if zone != "canyon" else (35, 70)
            self.spawn_timer = random.randint(low, high)

        self.enemy_spawn_timer -= 1
        if self.enemy_spawn_timer <= 0:
            weights = [45 / d, 25, 15 * d, 15 * (1 + 0.3 * (d - 1))]
            enemy_cls = random.choices(ENEMY_TYPES, weights=weights, k=1)[0]
            self.enemies.append(enemy_cls(self))
            self.enemy_spawn_timer = max(70, int(random.randint(160, 280) / d))

        self.coin_spawn_timer -= 1
        if self.coin_spawn_timer <= 0:
            self.coins.append(Coin(self))
            self.coin_spawn_timer = random.randint(70, 130)

        self.cross_timer -= 1
        if self.cross_timer <= 0 and len(self.convoys) < 2:
            self.cross_timer = random.randint(60, 120)
            bands = self.visible_intersections()
            if bands:
                a = random.choice(bands)
                upper = random.random() < 0.5
                car = Convoy(a + (IH * 0.70 if upper else IH * 0.30), 1 if upper else -1)
                if not any(abs(o.D - car.D) < 20 and abs(o.x - car.x) < 160 for o in self.convoys):
                    self.convoys.append(car)

    # -- update ------------------------------------------------------------
    def update(self, keys, mouse_pos, mouse_down):
        if self.message_timer > 0:
            self.message_timer -= 1
        if self.shake > 0:
            self.shake -= 1

        if not self.started or self.game_over or self.paused_for_upgrade:
            return

        self.frame += 1
        self.dist += SCROLL

        p = self.player
        p.handle_input(keys, mouse_pos, self)
        if mouse_down or keys[pygame.K_SPACE]:
            p.try_shoot(self)

        section = self.track.section_at(self.dist + HEIGHT - p.cy)
        if section is not self.cur_section:
            self.cur_section = section
            self.say(ZONE_MESSAGES[section.kind], WHITE, 140)

        self.spawn()

        for b in self.bullets:
            b.update()
        self.bullets = [b for b in self.bullets if not b.offscreen()]

        for o in self.obstacles:
            o.update(self)
        for e in self.enemies:
            e.update(self)
        for c in self.convoys:
            c.update(self)
        for c in self.coins:
            c.update(self)
        for part in self.particles:
            part.update()

        self._handle_collisions()

        self.obstacles = [o for o in self.obstacles if not o.offscreen() and not o.dead]
        self.enemies = [e for e in self.enemies if not e.offscreen() and not e.dead]
        self.convoys = [c for c in self.convoys if not c.offscreen() and not c.dead]
        self.coins = [c for c in self.coins if not c.offscreen()]
        self.particles = [q for q in self.particles if q.life > 0]

        if p.health <= 0:
            self.explode(p.cx, p.cy, big=True)
            self.game_over = True
            self.shake = 20
            if self.score > self.best:
                self.best = self.score
                self.new_best = True
                save_best(self.best)

    def _kill(self, thing, score, coins, big=False):
    thing.dead = True
    if big:                  # only enemies and convoys pass big=True
        self.kills += 1
    self.kill_score += score
    self.player.coins += coins
    self.explode(thing.x + (getattr(thing, "width", None) or thing.size) / 2,
                 thing.y + (getattr(thing, "height", None) or thing.size) / 2, big)

    def _handle_collisions(self):
        p = self.player
        pr = p.rect

        for b in self.bullets[:]:
            if b.owner != PLAYER_BULLET:
                continue
            hit = False
            for o in self.obstacles:
                if not o.dead and b.rect.colliderect(o.rect):
                    o.hp -= b.damage
                    hit = True
                    if o.hp <= 0:
                        self._kill(o, 10, random.randint(1, 3))
                    break
            if not hit:
                for e in self.enemies:
                    if not e.dead and b.rect.colliderect(e.rect):
                        e.hp -= b.damage
                        hit = True
                        if e.hp <= 0:
                            self._kill(e, e.score_value, random.randint(*e.coin_range), big=True)
                        break
            if not hit:
                for c in self.convoys:
                    if not c.dead and b.rect.colliderect(c.rect):
                        c.hp -= b.damage
                        hit = True
                        if c.hp <= 0:
                            self._kill(c, c.score_value, random.randint(*c.coin_range), big=True)
                        break
            if hit:
                self.bullets.remove(b)

        for b in self.bullets[:]:
            if b.owner == ENEMY_BULLET and b.rect.colliderect(pr):
                self.hurt_player(b.damage)
                self.bullets.remove(b)

        for o in self.obstacles:
            if not o.dead and o.rect.colliderect(pr):
                self.hurt_player(o.collision_damage)
                o.hp = 0
                o.dead = True
                self.explode(o.x + o.size / 2, o.y + o.size / 2)
        for e in self.enemies:
            if not e.dead and e.rect.colliderect(pr):
                self.hurt_player(e.collision_damage)
                e.hp = 0
                e.dead = True
                self.explode(e.cx, e.cy, big=True)
        for c in self.convoys:
            if not c.dead and c.rect.colliderect(pr):
                self.hurt_player(c.collision_damage)
                c.dead = True
                self.explode(c.x + c.width / 2, c.y + c.height / 2, big=True)

        for c in self.coins[:]:
            if c.rect.colliderect(pr):
                p.coins += 1
                self.coins.remove(c)

    # -- drawing -----------------------------------------------------------
    def draw_scenery(self, w, c0, c1):
        dist = self.dist
        track = self.track
        for i in range(c0, c1 + 1):
            Dc = i * CH + CH / 2
            s = track.section_at(Dc)
            ymid = dist + HEIGHT - Dc
            c, wd = s.center(Dc), s.width_at(Dc)
            for side in (-1, 1):
                edge = c + side * wd / 2
                if s.theme == "city":
                    bw = 60 + hrand(i, side, 1) * 80
                    r2 = hrand(i, side, 2)
                    bh = CH - 14
                    x = edge - 16 - bw if side < 0 else edge + 16
                    rect = pygame.Rect(int(x), int(ymid - bh / 2), int(bw), bh)
                    base = (60 + int(r2 * 40), 64 + int(r2 * 36), 82 + int(r2 * 40))
                    pygame.draw.rect(w, base, rect)
                    pygame.draw.rect(w, (30, 32, 40), rect, 2)
                    for cy in range(rect.y + 10, rect.bottom - 10, 20):
                        for cx in range(rect.x + 8, rect.right - 12, 18):
                            lit = (int(r2 * 1000) + cx * 7 + cy * 13) % 5 == 0
                            pygame.draw.rect(w, (240, 220, 120) if lit else (36, 40, 54), (cx, cy, 9, 11))
                elif s.theme == "grass":
                    for k in (1, 2):
                        tx = edge + side * (30 + hrand(i, side, k) * 100)
                        ty = ymid + (hrand(i, side, k + 5) - 0.5) * CH
                        rad = 11 + hrand(i, side, k + 9) * 9
                        pygame.draw.circle(w, (22, 52, 32), (int(tx), int(ty)), int(rad))
                        pygame.draw.circle(w, (34, 78, 46), (int(tx - 3), int(ty - 3)), int(rad * 0.6))
                else:
                    for k in (1, 2):
                        rw = 30 + hrand(i, side, k + 9) * 40
                        rh = 20 + hrand(i, side, k + 3) * 25
                        tx = edge + side * (rw / 2 + 10 + hrand(i, side, k) * 80)
                        ty = ymid + (hrand(i, side, k + 5) - 0.5) * CH
                        pygame.draw.ellipse(w, (112, 84, 54), (int(tx - rw / 2), int(ty - rh / 2), int(rw), int(rh)))
                        pygame.draw.ellipse(w, (134, 104, 68), (int(tx - rw / 2), int(ty - rh / 2), int(rw * 0.7), int(rh * 0.6)))

    def draw_road(self, w):
        dist = self.dist
        track = self.track
        ys = list(range(-STEP, HEIGHT + 2 * STEP, STEP))
        centers, widths = [], []
        for y in ys:
            D = dist + HEIGHT - y
            s = track.section_at(D)
            centers.append(s.center(D))
            widths.append(s.width_at(D))

        # Sides, asphalt and edge lines, one band at a time
        for i in range(len(ys) - 1):
            y0, y1 = ys[i], ys[i + 1]
            Dm = dist + HEIGHT - (y0 + y1) / 2
            s = track.section_at(Dm)
            th = THEMES[s.theme]
            side_col = th["side"] if int(Dm // 120) % 2 == 0 else th["side2"]
            pygame.draw.rect(w, side_col, (0, y0, WIDTH, y1 - y0 + 1))
            l0, r0 = centers[i] - widths[i] / 2, centers[i] + widths[i] / 2
            l1, r1 = centers[i + 1] - widths[i + 1] / 2, centers[i + 1] + widths[i + 1] / 2
            pygame.draw.polygon(w, th["road"], [(l0, y0), (r0, y0), (r1, y1 + 1), (l1, y1 + 1)])
            edge_col, edge_w = th["edge"]
            pygame.draw.line(w, edge_col, (l0 + 5, y0), (l1 + 5, y1 + 1), edge_w)
            pygame.draw.line(w, edge_col, (r0 - 5, y0), (r1 - 5, y1 + 1), edge_w)

        # Scenery beside the road
        self.draw_scenery(w, int((dist - 100) // CH), int((dist + HEIGHT + 100) // CH) + 1)

        # Solid double yellow line down the middle (highway and downtown)
        for i in range(len(ys) - 1):
            Dm = dist + HEIGHT - (ys[i] + ys[i + 1]) / 2
            if track.section_at(Dm).kind in ("highway", "downtown"):
                for off in (-3.5, 3.5):
                    pygame.draw.line(w, YELLOW, (centers[i] + off, ys[i]),
                                     (centers[i + 1] + off, ys[i + 1] + 1), 3)

        # Dashed lane lines
        k0 = int(dist // 44) - 1
        for k in range(k0, k0 + HEIGHT // 44 + 3):
            Da, Db = k * 44, k * 44 + 22
            ya, yb = dist + HEIGHT - Da, dist + HEIGHT - Db
            if yb > HEIGHT + 5 or ya < -5:
                continue
            s = track.section_at((Da + Db) / 2)
            if s.kind == "highway":
                fracs, col = (-0.25, 0.25), (200, 200, 205)
            elif s.kind == "canyon":
                fracs, col = (0.0,), YELLOW
            else:
                continue
            ca, wa = s.center(Da), s.width_at(Da)
            cb, wb = s.center(Db), s.width_at(Db)
            for f in fracs:
                pygame.draw.line(w, col, (ca + f * wa, ya), (cb + f * wb, yb), 4)

        # Intersections are painted over everything on the road
        i0 = track.index_at(dist - IH)
        i1 = track.index_at(dist + HEIGHT + IH)
        for s in track.sections[i0:i1 + 1]:
            for a in s.intersections:
                ytop = dist + HEIGHT - (a + IH)
                ybot = dist + HEIGHT - a
                if ybot < 0 or ytop > HEIGHT:
                    continue
                pygame.draw.rect(w, THEMES["city"]["road"], (0, int(ytop), WIDTH, IH))
                c, wd = s.center(a), s.width_at(a)
                x = c - wd / 2 + 8
                while x < c + wd / 2 - 14:
                    pygame.draw.rect(w, (225, 225, 230), (int(x), int(ytop + 6), 9, 16))
                    pygame.draw.rect(w, (225, 225, 230), (int(x), int(ybot - 22), 9, 16))
                    x += 16
                mid = int((ytop + ybot) / 2)
                for sx in range(0, WIDTH, 44):
                    pygame.draw.rect(w, (200, 200, 205), (sx, mid - 2, 22, 4))

    def draw(self, surf, mouse_pos):
        w = self.world
        self.draw_road(w)
        for o in self.obstacles:
            o.draw(w)
        for c in self.coins:
            c.draw(w)
        for c in self.convoys:
            c.draw(w)
        for e in self.enemies:
            e.draw(w)
        for b in self.bullets:
            b.draw(w)
        if not self.game_over:
            self.player.draw(w)
        for part in self.particles:
            part.draw(w)

        ox = oy = 0
        if self.shake > 0:
            s = min(self.shake, 8)
            ox, oy = random.randint(-s, s) // 2, random.randint(-s, s) // 2
        surf.fill(BLACK)
        surf.blit(w, (ox, oy))

        if self.started and not self.game_over and not self.paused_for_upgrade:
            self.draw_reticle(surf, mouse_pos)
        self.draw_hud(surf)
        if not self.started:
            self.draw_title(surf)
        if self.paused_for_upgrade:
            self.draw_upgrade_menu(surf)
        if self.game_over:
            self.draw_game_over(surf)

    def draw_reticle(self, surf, pos):
        x, y = int(pos[0]), int(pos[1])
        pygame.draw.circle(surf, WHITE, (x, y), 11, 2)
        pygame.draw.circle(surf, RED, (x, y), 2)
        for dx, dy in ((-17, 0), (6, 0), (0, -17), (0, 6)):
            pygame.draw.line(surf, WHITE, (x + dx, y + dy), (x + dx + (11 if dx else 0), y + dy + (11 if dy else 0)), 2)

    def draw_hud(self, surf):
        p = self.player
        pygame.draw.rect(surf, BLACK, (10, 10, 150, 16))
        pct = max(0, p.health / p.max_health)
        pygame.draw.rect(surf, GREEN, (10, 10, 150 * pct, 16))
        pygame.draw.rect(surf, WHITE, (10, 10, 150, 16), 2)

        def put(txt, col, x, y, right=False):
            img = font_small.render(txt, True, col)
            shade = font_small.render(txt, True, BLACK)
            if right:
                x = x - img.get_width()
            surf.blit(shade, (x + 1, y + 1))
            surf.blit(shade, (x - 1, y + 1))
            surf.blit(img, (x, y))

        put(f"Score: {self.score}", WHITE, 10, 32)
        put(f"Coins: {p.coins}", YELLOW, 10, 52)
        put(self.cur_section.name, (200, 205, 220), 10, 72)
        put("U: Upgrades", (200, 205, 220), WIDTH - 10, 12, right=True)
        put(f"Best: {self.best}", WHITE, WIDTH - 10, 32, right=True)
        if p.offroad:
            put("MUD: slowed", ORANGE, WIDTH - 10, 52, right=True)

        if not self.paused_for_upgrade and self.message_timer > 0:
            m = font_msg.render(self.message, True, self.message_color)
            shadow = font_msg.render(self.message, True, BLACK)
            pos = (WIDTH // 2 - m.get_width() // 2, 100)
            surf.blit(shadow, (pos[0] + 2, pos[1] + 2))
            surf.blit(m, pos)

    def draw_title(self, surf):
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 190))
        surf.blit(overlay, (0, 0))
        title = font_big.render("TANK WARS", True, YELLOW)
        surf.blit(title, (WIDTH // 2 - title.get_width() // 2, 160))
        lines = [
            "Drive: WASD or arrow keys",
            "Aim: mouse",
            "Fire: left click or Space",
            "Upgrades: U, then 1-8",
            "",
            "Dodge enemy fire, shoot what you can,",
            "collect coins and upgrade your tank.",
            "Leaving the road bogs you down in mud.",
            "",
            "Click or press SPACE to start",
        ]
        for i, line in enumerate(lines):
            txt = font_small.render(line, True, WHITE)
            surf.blit(txt, (WIDTH // 2 - txt.get_width() // 2, 236 + i * 26))

    def draw_upgrade_menu(self, surf):
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 185))
        surf.blit(overlay, (0, 0))

        title = font_big.render("UPGRADES", True, WHITE)
        surf.blit(title, (WIDTH // 2 - title.get_width() // 2, 26))

        coin_txt = font_small.render(f"Coins: {self.player.coins}", True, YELLOW)
        surf.blit(coin_txt, (WIDTH // 2 - coin_txt.get_width() // 2, 70))

        row_h = 52
        start_y = 100
        for i, u in enumerate(UPGRADES):
            level = getattr(self.player, f"{u['key']}_level")
            y = start_y + i * row_h
            box = pygame.Rect(40, y, WIDTH - 80, 42)
            maxed = level >= MAX_LEVEL
            box_color = (50, 60, 45) if maxed else (40, 44, 56)
            pygame.draw.rect(surf, box_color, box, border_radius=8)
            pygame.draw.rect(surf, WHITE, box, 2, border_radius=8)

            cost_str = "MAX" if maxed else f"{upgrade_cost(level, u['cost_base'])} coins"
            label = font_small.render(
                f"{i + 1}. {u['label']}  (Lv {level}/{MAX_LEVEL})  -  {cost_str}",
                True, WHITE
            )
            surf.blit(label, (box.x + 10, box.y + 12))

        if self.message_timer > 0:
            msg_surf = font_small.render(self.message, True, self.message_color)
            surf.blit(msg_surf, (WIDTH // 2 - msg_surf.get_width() // 2,
                                 start_y + len(UPGRADES) * row_h + 8))

        hint = font_small.render("Press 1-8 to buy, U to close", True, GRAY)
        surf.blit(hint, (WIDTH // 2 - hint.get_width() // 2,
                         start_y + len(UPGRADES) * row_h + 32))

    def draw_game_over(self, surf):
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 190))
        surf.blit(overlay, (0, 0))
        title = font_big.render("TANK DESTROYED", True, RED)
        surf.blit(title, (WIDTH // 2 - title.get_width() // 2, HEIGHT // 2 - 80))
        lines = [
            (f"Final Score: {self.score}", WHITE),
            (f"Enemies destroyed: {self.kills_total()}", WHITE),
            ("New best score!" if self.new_best else f"Best: {self.best}", YELLOW if self.new_best else WHITE),
            ("Press R to restart", GRAY),
        ]
        for i, (line, col) in enumerate(lines):
            txt = font_small.render(line, True, col)
            surf.blit(txt, (WIDTH // 2 - txt.get_width() // 2, HEIGHT // 2 - 20 + i * 30))

    def kills_total(self):
        return self.kills


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    pygame.mouse.set_visible(False)
    game = Game()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and not game.started:
                game.started = True
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE and not game.started:
                    game.started = True
                elif event.key == pygame.K_u and game.started and not game.game_over:
                    game.paused_for_upgrade = not game.paused_for_upgrade
                elif event.key == pygame.K_r and game.game_over:
                    game.reset()
                elif game.paused_for_upgrade and event.key in UPGRADE_KEYS:
                    idx = UPGRADE_KEYS.index(event.key)
                    if idx < len(UPGRADES):
                        game.buy(UPGRADES[idx]["key"])

        keys = pygame.key.get_pressed()
        mouse_pos = pygame.mouse.get_pos()
        mouse_down = pygame.mouse.get_pressed()[0] and game.started
        game.update(keys, mouse_pos, mouse_down)
        game.draw(screen, mouse_pos)
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
