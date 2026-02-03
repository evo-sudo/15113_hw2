import random
import sys
import pygame
import math

# ----------------------------
# Crossy Road–style mini-clone (difficulty + log hop animation)
# ----------------------------
# NEW in this version:
# - Difficulty curve: start slower + fewer cars/trains, ramps up with score
# - Log "weight" animation: when you first hop onto a log, the log dips briefly
#
# Controls:
#   Arrow keys / WASD: move
#   R: restart after death
#   Esc: quit

# ------------ Config ------------
TILE = 48
GRID_W = 11
SCREEN_W = GRID_W * TILE
SCREEN_H = 14 * TILE
FPS = 60

START_SAFE_ROWS = 6
LANE_LOOKAHEAD = 55

# Base auto-scroll (scaled by difficulty)
BASE_SCROLL_SPEED = TILE * 0.70  # slower start (was ~1.10)

# Base Speeds (px/sec) (scaled by difficulty)
CAR_SPEED_MIN = 120
CAR_SPEED_MAX = 170
LOG_SPEED_MIN = 90
LOG_SPEED_MAX = 130
TRAIN_SPEED = 600  # base, scaled by difficulty

# Base Spawn intervals (seconds) (scaled by difficulty)
CAR_SPAWN_MIN = 1.00
CAR_SPAWN_MAX = 1.60
LOG_SPAWN_MIN = 1.10
LOG_SPAWN_MAX = 1.70

# Train timing (seconds) (scaled by difficulty)
TRAIN_INTERVAL_MIN = 6.0
TRAIN_INTERVAL_MAX = 10.0
TRAIN_WARNING_TIME = 1.1

# Spacing so obstacles never overlap (px)
MIN_GAP = TILE * 0.75

# Log hop "weight" animation
LOG_DIP_DURATION = 0.18      # seconds
LOG_DIP_PIXELS = 7           # max dip amount (px)

# Colors
C_BG = (20, 20, 24)
C_TEXT = (240, 240, 240)

C_GRASS = (50, 150, 70)
C_ROAD = (60, 60, 70)
C_RIVER = (35, 90, 140)
C_RAIL = (45, 45, 45)
C_RAIL_TIE = (90, 70, 50)

C_TREE = (20, 90, 35)
C_CAR = (220, 70, 70)
C_LOG = (160, 110, 60)
C_TRAIN = (230, 230, 235)

C_WARNING = (230, 190, 40)
C_WARNING_RED = (240, 80, 60)

# Chicken colors
C_CHICK = (245, 245, 235)
C_CHICK_SHADOW = (230, 230, 215)
C_EYE = (25, 25, 25)
C_BEAK = (240, 180, 60)
C_COMB = (220, 70, 70)
C_DEAD = (250, 90, 90)


# ------------ Difficulty ------------
def difficulty(score: int) -> float:
    """
    Returns a multiplier applied to scroll speed, obstacle speeds,
    and spawn frequency (higher = faster and more frequent).

    - Start slow at ~0.60
    - Reach 1.00 by score ~20
    - Reach ~1.45 by score ~80
    """
    if score <= 0:
        return 0.60
    if score < 20:
        # 0.60 -> 1.00
        return 0.60 + (score / 20.0) * (1.00 - 0.60)
    if score < 80:
        # 1.00 -> 1.45
        return 1.00 + ((score - 20) / 60.0) * (1.45 - 1.00)
    return 1.45


# ------------ Helpers ------------
def rect_from_tile(col, row, cam_y):
    """
    World: row increases forward (up on screen).
    Screen: y increases downward.
    Flip world->screen so higher rows render higher.
    cam_y is camera position in world pixels (increasing forward).
    """
    x = col * TILE
    y = (SCREEN_H - TILE) - (row * TILE - cam_y)
    return pygame.Rect(x, y, TILE, TILE)


# ------------ Lane Types ------------
LANE_GRASS = "grass"
LANE_ROAD = "road"
LANE_RIVER = "river"
LANE_RAIL = "rail"


class Obstacle:
    def __init__(self, x, w, base_speed, direction):
        self.x = float(x)
        self.w = float(w)
        self.base_speed = float(base_speed)
        self.dir = int(direction)  # +1 right, -1 left

    def update(self, dt, speed_mult: float):
        self.x += self.dir * (self.base_speed * speed_mult) * dt


class Lane:
    """
    One horizontal lane at integer row (world coords).

    Road/River:
      - fixed base speed per lane (scaled by difficulty each frame) => no passing
      - enforce spacing at spawn + enforce no-overlap after update

    Grass:
      - safe-zone corridor (never trapped at the beginning)
      - after safe-zone: corridor connects to previous lane

    Rail:
      - trains + warning lights (interval scaled by difficulty)
    """
    def __init__(self, row, lane_type, rng, prev_open_cols=None):
        self.row = row
        self.type = lane_type
        self.rng = rng

        self.obstacles = []
        self.blocked_cols = set()

        self.direction = rng.choice([-1, 1])

        # Spawning (we store base interval; actual uses difficulty)
        self.spawn_timer = 0.0
        self.base_spawn_interval = 1.0

        # Lane base speed (actual uses difficulty)
        self.base_speed = 0.0

        # Train
        self.train_timer = rng.uniform(TRAIN_INTERVAL_MIN, TRAIN_INTERVAL_MAX)
        self.train_warning = 0.0
        self.train_active = False
        self.train_x = 0.0
        self.train_dir = rng.choice([-1, 1])

        if self.type == LANE_GRASS:
            self._init_grass(prev_open_cols)
        elif self.type == LANE_ROAD:
            self.base_spawn_interval = rng.uniform(CAR_SPAWN_MIN, CAR_SPAWN_MAX)
            self.base_speed = rng.uniform(CAR_SPEED_MIN, CAR_SPEED_MAX)
            self._seed_obstacles(count=self.rng.choice([1, 1, 2]))
        elif self.type == LANE_RIVER:
            self.base_spawn_interval = rng.uniform(LOG_SPAWN_MIN, LOG_SPAWN_MAX)
            self.base_speed = rng.uniform(LOG_SPEED_MIN, LOG_SPEED_MAX)
            self._seed_obstacles(count=self.rng.choice([1, 2]))
        elif self.type == LANE_RAIL:
            # start with a fresh interval; will be re-rolled with difficulty on use
            self.train_timer = rng.uniform(TRAIN_INTERVAL_MIN, TRAIN_INTERVAL_MAX)

    def is_blocked(self, col):
        return col in self.blocked_cols

    def open_cols(self):
        if self.type != LANE_GRASS:
            return set(range(GRID_W))
        return set(range(GRID_W)) - set(self.blocked_cols)

    # ----- Grass corridor logic -----
    def _init_grass(self, prev_open_cols):
        # SAFE-ZONE GUARANTEE: never trapped at the beginning
        if self.row < START_SAFE_ROWS:
            center = GRID_W // 2
            corridor = {max(0, center - 1), center, min(GRID_W - 1, center + 1)}

            # very light decoration only, never in corridor
            target_trees = self.rng.randint(0, 2)
            candidates = [c for c in range(GRID_W) if c not in corridor]
            self.rng.shuffle(candidates)
            for c in candidates[:target_trees]:
                self.blocked_cols.add(c)
            return

        # Connected corridor for the rest of the world
        if prev_open_cols and len(prev_open_cols) > 0:
            corridor_center = self.rng.choice(sorted(prev_open_cols))
        else:
            corridor_center = self.rng.randrange(GRID_W)

        corridor_width = self.rng.choice([2, 3])
        corridor = set()
        for dx in range(-(corridor_width // 2), (corridor_width // 2) + 1):
            corridor.add(max(0, min(GRID_W - 1, corridor_center + dx)))

        target_trees = self.rng.randint(2, 5)
        candidates = [c for c in range(GRID_W) if c not in corridor]
        self.rng.shuffle(candidates)

        for c in candidates:
            if len(self.blocked_cols) >= target_trees:
                break
            # keep at least 3 open columns total
            if GRID_W - (len(self.blocked_cols) + 1) < 3:
                break
            self.blocked_cols.add(c)

        # corridor always open
        self.blocked_cols -= corridor

    # ----- Spawning + spacing -----
    def _edge_spawn_x(self, w):
        if self.direction == 1:
            return -w - self.rng.uniform(0, TILE * 2)
        else:
            return SCREEN_W + self.rng.uniform(0, TILE * 2)

    def _can_spawn_with_gap(self, x, w):
        if not self.obstacles:
            return True

        if self.direction == 1:
            nearest = min(self.obstacles, key=lambda ob: ob.x)
            return (x + w) <= (nearest.x - MIN_GAP)
        else:
            nearest = max(self.obstacles, key=lambda ob: ob.x + ob.w)
            return x >= (nearest.x + nearest.w + MIN_GAP)

    def _enforce_no_overlap(self):
        if len(self.obstacles) <= 1:
            return

        self.obstacles.sort(key=lambda ob: ob.x)
        prev = self.obstacles[0]
        for i in range(1, len(self.obstacles)):
            cur = self.obstacles[i]
            min_x = prev.x + prev.w + MIN_GAP
            if cur.x < min_x:
                cur.x = min_x
            prev = cur

    def _seed_obstacles(self, count):
        for _ in range(count):
            self.spawn_obstacle()

    def spawn_obstacle(self):
        if self.type == LANE_ROAD:
            # Cars: SAME SIZE always
            w = 1 * TILE
            x = self._edge_spawn_x(w)
            if self._can_spawn_with_gap(x, w):
                self.obstacles.append(Obstacle(x, w, self.base_speed, self.direction))

        elif self.type == LANE_RIVER:
            # Logs: variable size; fixed speed + spacing prevents overlap/passing
            w_tiles = self.rng.choice([2, 2, 3])
            w = w_tiles * TILE
            x = self._edge_spawn_x(w)
            if self._can_spawn_with_gap(x, w):
                self.obstacles.append(Obstacle(x, w, self.base_speed, self.direction))

    # ----- Train logic -----
    def _roll_next_train_interval(self, diff: float):
        # Early diff<1 => LONGER intervals, later diff>1 => shorter intervals
        base = self.rng.uniform(TRAIN_INTERVAL_MIN, TRAIN_INTERVAL_MAX)
        return base / diff

    def spawn_train(self):
        self.train_active = True
        self.train_warning = 0.0
        self.train_x = (-TILE * 12) if self.train_dir == 1 else (SCREEN_W + TILE * 12)

    def _finish_train(self, diff: float):
        self.train_active = False
        self.train_timer = self._roll_next_train_interval(diff)
        if self.rng.random() < 0.5:
            self.train_dir *= -1

    def update(self, dt, diff: float):
        speed_mult = diff

        # move obstacles
        for ob in self.obstacles:
            ob.update(dt, speed_mult)

        # cull off-screen obstacles
        margin = TILE * 5
        left_bound = -margin
        right_bound = SCREEN_W + margin
        self.obstacles = [ob for ob in self.obstacles if (ob.x + ob.w) > left_bound and ob.x < right_bound]

        # spawn road/river obstacles
        if self.type in (LANE_ROAD, LANE_RIVER):
            # actual interval is longer early, shorter later
            actual_interval = self.base_spawn_interval / diff

            self.spawn_timer += dt
            if self.spawn_timer >= actual_interval:
                self.spawn_timer = 0.0
                # re-roll a base interval occasionally
                if self.type == LANE_ROAD:
                    self.base_spawn_interval = self.rng.uniform(CAR_SPAWN_MIN, CAR_SPAWN_MAX)
                else:
                    self.base_spawn_interval = self.rng.uniform(LOG_SPAWN_MIN, LOG_SPAWN_MAX)
                self.spawn_obstacle()

            # hard guarantee: no overlap ever
            self._enforce_no_overlap()

        # train logic
        if self.type == LANE_RAIL:
            if not self.train_active:
                self.train_timer -= dt
                if self.train_timer <= TRAIN_WARNING_TIME and self.train_warning <= 0:
                    self.train_warning = TRAIN_WARNING_TIME
                if self.train_timer <= 0:
                    self.spawn_train()
            else:
                self.train_x += self.train_dir * (TRAIN_SPEED * diff) * dt
                if self.train_dir == 1 and self.train_x > SCREEN_W + TILE * 8:
                    self._finish_train(diff)
                elif self.train_dir == -1 and self.train_x < -TILE * 12:
                    self._finish_train(diff)

            if self.train_warning > 0:
                self.train_warning -= dt
                if self.train_warning < 0:
                    self.train_warning = 0

    def draw(self, surf, cam_y, player=None):
        y = (SCREEN_H - TILE) - (self.row * TILE - cam_y)
        if y < -TILE or y > SCREEN_H:
            return

        if self.type == LANE_GRASS:
            bg = C_GRASS
        elif self.type == LANE_ROAD:
            bg = C_ROAD
        elif self.type == LANE_RIVER:
            bg = C_RIVER
        else:
            bg = C_RAIL

        pygame.draw.rect(surf, bg, pygame.Rect(0, int(y), SCREEN_W, TILE))

        # rail visuals + warning lights
        if self.type == LANE_RAIL:
            for i in range(0, SCREEN_W, TILE):
                pygame.draw.rect(
                    surf, C_RAIL_TIE,
                    pygame.Rect(i + TILE // 6, int(y) + TILE // 2, TILE // 2, TILE // 6)
                )
            if self.train_warning > 0:
                flash = ((pygame.time.get_ticks() // 120) % 2) == 0
                light_color = C_WARNING_RED if flash else C_WARNING
                cy = int(y) + TILE // 2
                pygame.draw.circle(surf, light_color, (18, cy), 10)
                pygame.draw.circle(surf, light_color, (SCREEN_W - 18, cy), 10)

        # trees
        if self.type == LANE_GRASS:
            for c in self.blocked_cols:
                r = rect_from_tile(c, self.row, cam_y)
                pygame.draw.rect(surf, C_TREE, r.inflate(-TILE // 6, -TILE // 6))

        # cars
        if self.type == LANE_ROAD:
            for ob in self.obstacles:
                r = pygame.Rect(int(ob.x), int(y) + TILE // 6, int(ob.w), TILE * 2 // 3)
                pygame.draw.rect(surf, C_CAR, r, border_radius=8)

        # logs (with dip animation if player just hopped on)
        if self.type == LANE_RIVER:
            for ob in self.obstacles:
                dip = 0
                if player is not None and player.log_under is ob and player.log_dip_t > 0:
                    # smooth "weight" dip: starts big and springs back
                    t = player.log_dip_t / LOG_DIP_DURATION  # 1 -> 0
                    # ease-out (more dip at start)
                    dip = int(LOG_DIP_PIXELS * (t * t))

                r = pygame.Rect(int(ob.x), int(y) + TILE // 4 + dip, int(ob.w), TILE // 2 - max(0, dip // 2))
                pygame.draw.rect(surf, C_LOG, r, border_radius=6)

        # train
        if self.type == LANE_RAIL and self.train_active:
            train_w = TILE * 16
            r = pygame.Rect(int(self.train_x), int(y) + TILE // 5, train_w, TILE * 3 // 5)
            pygame.draw.rect(surf, C_TRAIN, r, border_radius=4)


class World:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.lanes = {}
        self.max_generated_row = -1

        prev_open = set(range(GRID_W))
        for r in range(START_SAFE_ROWS):
            self._ensure_lane(r, forced_type=LANE_GRASS, prev_open_cols=prev_open)
            prev_open = self.lanes[r].open_cols()

        self.max_generated_row = START_SAFE_ROWS - 1
        self._generate_up_to(START_SAFE_ROWS + LANE_LOOKAHEAD)

    def _choose_next_lane_type(self, row):
        # Rail appears about as often as river
        recent = [self.lanes.get(row - i).type for i in range(1, 4) if (row - i) in self.lanes]
        river_streak = sum(1 for t in recent if t == LANE_RIVER)
        rail_streak = sum(1 for t in recent if t == LANE_RAIL)
        road_streak = sum(1 for t in recent if t == LANE_ROAD)

        if river_streak >= 2:
            weights = [(LANE_GRASS, 7), (LANE_ROAD, 5), (LANE_RAIL, 5), (LANE_RIVER, 2)]
        elif rail_streak >= 2:
            weights = [(LANE_GRASS, 7), (LANE_ROAD, 5), (LANE_RIVER, 5), (LANE_RAIL, 2)]
        elif road_streak >= 2:
            weights = [(LANE_GRASS, 7), (LANE_RIVER, 5), (LANE_RAIL, 5), (LANE_ROAD, 2)]
        else:
            weights = [(LANE_GRASS, 6), (LANE_ROAD, 5), (LANE_RIVER, 5), (LANE_RAIL, 5)]

        total = sum(w for _, w in weights)
        pick = self.rng.uniform(0, total)
        acc = 0
        for t, w in weights:
            acc += w
            if pick <= acc:
                return t
        return LANE_GRASS

    def _ensure_lane(self, row, forced_type=None, prev_open_cols=None):
        if row in self.lanes:
            return
        lane_type = forced_type if forced_type else self._choose_next_lane_type(row)
        self.lanes[row] = Lane(row, lane_type, self.rng, prev_open_cols=prev_open_cols)
        self.max_generated_row = max(self.max_generated_row, row)

    def _generate_up_to(self, target_row):
        for r in range(self.max_generated_row + 1, target_row + 1):
            prev_open = self.lanes[r - 1].open_cols() if (r - 1) in self.lanes else set(range(GRID_W))
            self._ensure_lane(r, prev_open_cols=prev_open)

    def update(self, dt, camera_row, diff: float):
        self._generate_up_to(camera_row + LANE_LOOKAHEAD)

        min_row = max(0, camera_row - 10)
        max_row = camera_row + LANE_LOOKAHEAD
        for r in range(min_row, max_row + 1):
            lane = self.lanes.get(r)
            if lane:
                lane.update(dt, diff)

        prune_before = camera_row - 30
        for r in [k for k in self.lanes.keys() if k < prune_before]:
            del self.lanes[r]

    def lane_at(self, row):
        self._ensure_lane(row, prev_open_cols=None)
        return self.lanes[row]

    def draw(self, surf, cam_y, player):
        top_world_row = (cam_y + (SCREEN_H - TILE)) // TILE + 3
        bottom_world_row = max(0, cam_y // TILE - 3)
        for r in range(int(bottom_world_row), int(top_world_row) + 1):
            lane = self.lanes.get(r)
            if lane:
                lane.draw(surf, cam_y, player=player)


class Player:
    def __init__(self):
        self.col = GRID_W // 2
        self.row = 1
        self.x_px = self.col * TILE + TILE / 2  # pixel-precise world x (center)

        self.dead = False
        self.max_row = self.row

        # Log riding / animation state
        self.was_on_log = False
        self.log_under = None
        self.log_dip_t = 0.0

    def _sync_col_from_x(self):
        self.col = int(self.x_px // TILE)

    def _snap_x_to_col(self):
        self.x_px = self.col * TILE + TILE / 2

    def try_move(self, dcol, drow, world):
        if self.dead:
            return

        new_col = self.col + dcol
        new_row = self.row + drow

        if not (0 <= new_col < GRID_W):
            return
        if new_row < 0:
            return

        lane = world.lane_at(new_row)
        if lane.type == LANE_GRASS and lane.is_blocked(new_col):
            return

        self.col = new_col
        self.row = new_row
        self.max_row = max(self.max_row, self.row)
        self._snap_x_to_col()

    def kill(self):
        self.dead = True

    def update(self, dt, world, cam_y):
        if self.dead:
            return

        lane = world.lane_at(self.row)

        # decrement dip timer
        if self.log_dip_t > 0:
            self.log_dip_t = max(0.0, self.log_dip_t - dt)

        # River: must be on a log; while on log, move with it (preserve offset)
        if lane.type == LANE_RIVER:
            on_log = False
            carry_dx = 0.0
            log_ref = None

            for ob in lane.obstacles:
                if ob.x <= self.x_px <= (ob.x + ob.w):
                    on_log = True
                    carry_dx = ob.dir * (ob.base_speed * difficulty(self.max_row)) * dt
                    log_ref = ob
                    break

            if not on_log:
                self.kill()
                return

            # Trigger "weight" dip only when you *first* land on a log
            if (not self.was_on_log) or (self.log_under is not log_ref):
                self.log_dip_t = LOG_DIP_DURATION

            self.log_under = log_ref
            self.was_on_log = True

            self.x_px += carry_dx
            if self.x_px < 0 or self.x_px > SCREEN_W:
                self.kill()
                return
            self._sync_col_from_x()

        else:
            # not river => reset log state
            self.was_on_log = False
            self.log_under = None

        # Road collision
        if lane.type == LANE_ROAD:
            for ob in lane.obstacles:
                if ob.x <= self.x_px <= (ob.x + ob.w):
                    self.kill()
                    return

        # Rail collision
        if lane.type == LANE_RAIL and lane.train_active:
            train_w = TILE * 16
            if lane.train_x <= self.x_px <= (lane.train_x + train_w):
                self.kill()
                return

        # Lose if you fall behind (off bottom of screen)
        pr = rect_from_tile(self.col, self.row, cam_y)
        if pr.top > SCREEN_H:
            self.kill()

    def draw(self, surf, cam_y):
        t = rect_from_tile(self.col, self.row, cam_y)
        r = t.inflate(-TILE // 6, -TILE // 6)

        if self.dead:
            if ((pygame.time.get_ticks() // 120) % 2) == 0:
                pygame.draw.ellipse(surf, C_DEAD, r)
            return

        # Optional tiny hop bounce when log dips
        bounce = 0
        if self.log_dip_t > 0:
            # small upward bounce as the log dips
            tnorm = self.log_dip_t / LOG_DIP_DURATION
            bounce = int(3 * math.sin((1 - tnorm) * math.pi))

        body = r.move(0, -bounce)
        pygame.draw.ellipse(surf, C_CHICK, body)

        head = pygame.Rect(0, 0, int(body.w * 0.55), int(body.h * 0.55))
        head.center = (int(body.centerx + body.w * 0.18), int(body.centery - body.h * 0.20))
        pygame.draw.ellipse(surf, C_CHICK, head)

        wing = pygame.Rect(0, 0, int(body.w * 0.55), int(body.h * 0.45))
        wing.center = (int(body.centerx - body.w * 0.05), int(body.centery + body.h * 0.05))
        pygame.draw.ellipse(surf, C_CHICK_SHADOW, wing)

        eye_center = (int(head.centerx + head.w * 0.15), int(head.centery - head.h * 0.05))
        pygame.draw.circle(surf, C_EYE, eye_center, max(2, TILE // 18))

        beak_tip = (int(head.right + head.w * 0.18), int(head.centery + head.h * 0.10))
        beak_top = (int(head.right), int(head.centery))
        beak_bot = (int(head.right), int(head.centery + head.h * 0.25))
        pygame.draw.polygon(surf, C_BEAK, [beak_top, beak_tip, beak_bot])

        comb_y = int(head.top + head.h * 0.15)
        for i in range(3):
            cx = int(head.left + head.w * (0.30 + 0.18 * i))
            pygame.draw.circle(surf, C_COMB, (cx, comb_y), max(2, TILE // 16))

        foot_y = body.bottom + 2
        foot_dx = max(3, TILE // 10)
        for sgn in (-1, 1):
            x0 = int(body.centerx + sgn * body.w * 0.18)
            pygame.draw.line(surf, C_BEAK, (x0, foot_y), (x0 + sgn * foot_dx, foot_y), 3)


class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Crossy-Style Hopper (Python/Pygame)")
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 28)
        self.big = pygame.font.SysFont(None, 56)
        self.reset()

    def reset(self):
        self.world = World(seed=random.randrange(1_000_000))
        self.player = Player()
        self.cam_y = 0.0
        self.camera_row = 0

    def draw_ui(self, diff):
        score = self.player.max_row
        txt = self.font.render(f"Score: {score}   Diff: {diff:.2f}", True, C_TEXT)
        self.screen.blit(txt, (10, 10))

        if self.player.dead:
            overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 140))
            self.screen.blit(overlay, (0, 0))
            msg = self.big.render("SPLAT!", True, C_TEXT)
            msg2 = self.font.render("Press R to restart  •  Esc to quit", True, C_TEXT)
            self.screen.blit(msg, (SCREEN_W // 2 - msg.get_width() // 2, SCREEN_H // 2 - 70))
            self.screen.blit(msg2, (SCREEN_W // 2 - msg2.get_width() // 2, SCREEN_H // 2))

    def run(self):
        while True:
            dt = self.clock.tick(FPS) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.quit()
                        sys.exit()

                    if event.key == pygame.K_r and self.player.dead:
                        self.reset()

                    if not self.player.dead:
                        if event.key in (pygame.K_LEFT, pygame.K_a):
                            self.player.try_move(-1, 0, self.world)
                        elif event.key in (pygame.K_RIGHT, pygame.K_d):
                            self.player.try_move(1, 0, self.world)
                        elif event.key in (pygame.K_UP, pygame.K_w):
                            self.player.try_move(0, 1, self.world)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            self.player.try_move(0, -1, self.world)

            diff = difficulty(self.player.max_row)

            # Auto-scroll forward (scaled by difficulty)
            if not self.player.dead:
                self.cam_y += (BASE_SCROLL_SPEED * diff) * dt

            self.camera_row = int(self.cam_y // TILE)

            # Update world + player (difficulty-aware)
            self.world.update(dt, self.camera_row, diff)
            self.player.update(dt, self.world, self.cam_y)

            # Render
            self.screen.fill(C_BG)
            self.world.draw(self.screen, self.cam_y, self.player)
            self.player.draw(self.screen, self.cam_y)
            self.draw_ui(diff)
            pygame.display.flip()


if __name__ == "__main__":
    Game().run()