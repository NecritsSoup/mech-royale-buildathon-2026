"""Static arena collision, terrain and bounded grid navigation. Standard library only."""
from array import array
from collections import defaultdict
import heapq
import json
import math
from pathlib import Path
import sys
import time


def norm(x, y):
    length = math.hypot(x, y)
    return (x / length, y / length) if length > 1e-9 else (0.0, 0.0)


def point_inside(x, y, ring):
    inside = False
    j = len(ring) - 2
    for i in range(0, len(ring), 2):
        xi, yi, xj, yj = ring[i], ring[i + 1], ring[j], ring[j + 1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def edge_distance_sq(x, y, ring):
    best = math.inf
    j = len(ring) - 2
    for i in range(0, len(ring), 2):
        ax, ay, bx, by = ring[j], ring[j + 1], ring[i], ring[i + 1]
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length)) if length else 0
        best = min(best, (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2)
        j = i
    return best


class Prism:
    def __init__(self, data):
        self.ring, self.holes = data['ring'], data.get('holes', [])
        self.base, self.top = data['base'], data['top']
        self.x0, self.x1 = min(self.ring[::2]), max(self.ring[::2])
        self.y0, self.y1 = min(self.ring[1::2]), max(self.ring[1::2])

    def contains(self, x, y):
        return point_inside(x, y, self.ring) and not any(point_inside(x, y, h) for h in self.holes)

    def touches(self, x, y, radius):
        if x < self.x0 - radius or x > self.x1 + radius or y < self.y0 - radius or y > self.y1 + radius:
            return False
        if self.contains(x, y):
            return True
        return any(edge_distance_sq(x, y, ring) < radius * radius - 1e-7 for ring in [self.ring, *self.holes])

    def blocks_ray(self, a, b):
        dx, dy, dz = (b[i] - a[i] for i in range(3))
        if max(a[0], b[0]) < self.x0 or min(a[0], b[0]) > self.x1 or max(a[1], b[1]) < self.y0 or min(a[1], b[1]) > self.y1:
            return False
        if abs(dz) < 1e-9:
            if not self.base + 1e-5 < a[2] < self.top - 1e-5:
                return False
            low, high = 0.0, 1.0
        else:
            q, r = (self.base - a[2]) / dz, (self.top - a[2]) / dz
            low, high = max(0.0, min(q, r)), min(1.0, max(q, r))
            if low >= high:
                return False
        cuts = [low, high]
        for ring in [self.ring, *self.holes]:
            j = len(ring) - 2
            for i in range(0, len(ring), 2):
                ax, ay = ring[j], ring[j + 1]
                ex, ey = ring[i] - ax, ring[i + 1] - ay
                cross = dx * ey - dy * ex
                if abs(cross) > 1e-10:
                    rx, ry = ax - a[0], ay - a[1]
                    t, u = (rx * ey - ry * ex) / cross, (rx * dy - ry * dx) / cross
                    if low < t < high and -1e-8 <= u <= 1 + 1e-8:
                        cuts.append(t)
                j = i
        cuts.sort()
        return any(self.contains(a[0] + dx * ((p + q) / 2), a[1] + dy * ((p + q) / 2))
                   for p, q in zip(cuts, cuts[1:]) if q - p > 1e-8)


class World:
    BUCKET = 24.0

    def __init__(self, directory):
        directory = Path(directory)
        self.raw = json.loads((directory / 'v2/world.json').read_text())
        self.bounds, self.terrain = self.raw['bounds'], self.raw['terrain']
        self.prisms = [Prism(p) for p in self.raw['prisms']]
        self.buckets = defaultdict(list)
        for index, p in enumerate(self.prisms):
            for ix in range(math.floor(p.x0 / self.BUCKET), math.floor(p.x1 / self.BUCKET) + 1):
                for iy in range(math.floor(p.y0 / self.BUCKET), math.floor(p.y1 / self.BUCKET) + 1):
                    self.buckets[ix, iy].append(index)
        self.heights = array('h')
        self.heights.frombytes((directory / 'v2/terrain.i16').read_bytes())
        if sys.byteorder != 'little':
            self.heights.byteswap()
        if len(self.heights) != self.terrain['nx'] * self.terrain['ny']:
            raise ValueError('Terrain dimensions do not match')

    def height(self, x, y):
        t = self.terrain
        fx = max(0.0, min(t['nx'] - 1.000001, (x - t['minX']) / t['step']))
        fy = max(0.0, min(t['ny'] - 1.000001, (y - t['minY']) / t['step']))
        i, j = int(fx), int(fy)
        u, v, width = fx - i, fy - j, t['nx']
        a, b = self.heights[j * width + i], self.heights[j * width + i + 1]
        c, d = self.heights[(j + 1) * width + i], self.heights[(j + 1) * width + i + 1]
        return ((a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v) * t.get('scale', .01)

    def nearby(self, x, y, radius=0):
        found = set()
        for ix in range(math.floor((x - radius) / self.BUCKET), math.floor((x + radius) / self.BUCKET) + 1):
            for iy in range(math.floor((y - radius) / self.BUCKET), math.floor((y + radius) / self.BUCKET) + 1):
                found.update(self.buckets.get((ix, iy), ()))
        return found

    def body_clear(self, x, y, z, radius=3, height=9, step=1.2):
        b = self.bounds
        if not b['minX'] + radius < x < b['maxX'] - radius or not b['minY'] + radius < y < b['maxY'] - radius:
            return False
        for index in self.nearby(x, y, radius):
            p = self.prisms[index]
            if p.top > z + step and p.base < z + height - .05 and p.touches(x, y, radius):
                return False
        return True

    def ground_clear(self, x, y, radius=3, height=14, step=1.2, max_slope=50):
        z = self.height(x, y)
        slope = math.hypot((self.height(x + 1, y) - self.height(x - 1, y)) / 2,
                           (self.height(x, y + 1) - self.height(x, y - 1)) / 2)
        return slope <= math.tan(math.radians(max_slope)) and self.body_clear(x, y, z, radius, height, step)

    def walk_clear(self, start, end, radius=3, height=9, step=1.2, max_slope=50):
        distance = math.dist(start[:2], end[:2])
        count = max(1, math.ceil(distance / 2.0))
        previous = start[2]
        last_x, last_y = start[:2]
        for i in range(1, count + 1):
            f = i / count
            x, y = start[0] + (end[0] - start[0]) * f, start[1] + (end[1] - start[1]) * f
            z = self.height(x, y)
            # Roofs already below the feet support walking; higher roofs are walls.
            for index in self.nearby(x, y, radius):
                p = self.prisms[index]
                if z < p.top <= previous + step and p.touches(x, y, radius):
                    z = p.top
            if z - previous > step and z - previous > math.dist((x, y), (last_x, last_y)) * math.tan(math.radians(max_slope)):
                return False
            if not self.body_clear(x, y, z, radius, height, step):
                return False
            previous, last_x, last_y = z, x, y
        return True

    def line_clear(self, a, b):
        # Query the complete segment bounds: point sampling can skip a narrow
        # intermediate bucket when a ray passes close to a grid corner.
        candidates = set()
        for ix in range(math.floor(min(a[0], b[0]) / self.BUCKET), math.floor(max(a[0], b[0]) / self.BUCKET) + 1):
            for iy in range(math.floor(min(a[1], b[1]) / self.BUCKET), math.floor(max(a[1], b[1]) / self.BUCKET) + 1):
                candidates.update(self.buckets.get((ix, iy), ()))
        if any(self.prisms[index].blocks_ray(a, b) for index in candidates):
            return False
        steps = max(1, math.ceil(math.dist(a[:2], b[:2]) / 4))
        for i in range(1, steps):
            f = i / steps
            x, y, z = [a[j] + (b[j] - a[j]) * f for j in range(3)]
            if self.height(x, y) > z - .05:
                return False
        return True

    def cover_distance(self, x, y):
        return min((math.sqrt(edge_distance_sq(x, y, self.prisms[i].ring)) for i in self.nearby(x, y, 24)), default=40)


class Navigation:
    def __init__(self, world, config, step=8.0):
        self.world, self.step = world, step
        b = world.bounds
        self.x0, self.y0 = b['minX'] + 5, b['minY'] + 5
        self.width = int((b['maxX'] - self.x0 - 5) / step) + 1
        self.height = int((b['maxY'] - self.y0 - 5) / step) + 1
        self.mask = bytearray(self.width * self.height)
        self.z = [0.0] * len(self.mask)
        self.radius = config.get('radius', 2.5) + .35
        self.step_up = config.get('step_up', 1.2)
        self.max_slope = config.get('max_slope_deg', 50)
        for index in range(len(self.mask)):
            x, y = self.point(index)
            self.z[index] = world.height(x, y)
            self.mask[index] = world.ground_clear(x, y, self.radius, config.get('height', 9) + .2,
                                                  config.get('step_up', 1.2), config.get('max_slope_deg', 50))
        self.path, self.goal, self.planned_at = [], None, -100
        self.blocked_edges = set()

    def point(self, index):
        return self.x0 + index % self.width * self.step, self.y0 + index // self.width * self.step

    def nearest(self, point, max_rings=12):
        ix = max(0, min(self.width - 1, round((point[0] - self.x0) / self.step)))
        iy = max(0, min(self.height - 1, round((point[1] - self.y0) / self.step)))
        candidates = []
        for ring in range(max_rings + 1):
            for x in range(max(0, ix - ring), min(self.width - 1, ix + ring) + 1):
                for y in {iy - ring, iy + ring}:
                    if 0 <= y < self.height and self.mask[y * self.width + x]:
                        candidates.append(y * self.width + x)
            for y in range(max(0, iy - ring + 1), min(self.height - 1, iy + ring - 1) + 1):
                for x in {ix - ring, ix + ring}:
                    if 0 <= x < self.width and self.mask[y * self.width + x]:
                        candidates.append(y * self.width + x)
            if candidates:
                return min(candidates, key=lambda n: math.dist(self.point(n), point[:2]))
        return None

    def plan(self, start, goal):
        source, target = self.nearest(start), self.nearest(goal)
        if source is None or target is None:
            return []
        target_point = self.point(target)
        heap, costs, parents = [(0.0, source)], {source: 0.0}, {}
        closest, closest_h = source, math.dist(self.point(source), target_point)
        deadline = time.perf_counter() + .009
        expanded = 0
        while heap and expanded < 2200:
            _, current = heapq.heappop(heap)
            h = math.dist(self.point(current), target_point)
            if h < closest_h:
                closest, closest_h = current, h
            if current == target:
                closest = current
                break
            expanded += 1
            if expanded % 32 == 0 and time.perf_counter() > deadline:
                break
            x, y = current % self.width, current // self.width
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx, ny = x + dx, y + dy
                if not 0 <= nx < self.width or not 0 <= ny < self.height:
                    continue
                nxt = ny * self.width + nx
                if not self.mask[nxt] or (current, nxt) in self.blocked_edges:
                    continue
                if dx and dy and (not self.mask[y * self.width + nx] or not self.mask[ny * self.width + x]):
                    continue
                cost = costs[current] + self.step * (math.sqrt(2) if dx and dy else 1)
                if cost < costs.get(nxt, math.inf):
                    costs[nxt], parents[nxt] = cost, current
                    heapq.heappush(heap, (cost + math.dist(self.point(nxt), target_point), nxt))
        path = [closest]
        while path[-1] in parents:
            path.append(parents[path[-1]])
        path.reverse()
        return [self.point(index) for index in path]

    def direction(self, position, goal, now, radius, height):
        if math.dist(position[:2], goal[:2]) < 2:
            return (0.0, 0.0)
        if self.goal is None or math.dist(self.goal, goal[:2]) > 10 or now - self.planned_at > 2.0 or not self.path:
            self.path = self.plan(position, goal)
            self.goal, self.planned_at = tuple(goal[:2]), now
        while self.path and math.dist(self.path[0], position[:2]) < 4:
            self.path.pop(0)
        waypoint = tuple(goal[:2]) if not self.path else self.path[0]
        for candidate in self.path[:5]:
            if not self.world.walk_clear(position, candidate, radius, height, self.step_up, self.max_slope):
                # Cubes can make a previously valid low passage too small.
                node = self.nearest(candidate, max_rings=1)
                if node is not None and not self.world.ground_clear(*candidate, radius, height, self.step_up, self.max_slope):
                    self.mask[node] = 0
                    self.planned_at = -100
                else:
                    current = self.nearest(position, max_rings=1)
                    if current is not None and node is not None and current != node:
                        dx = abs(current % self.width-node % self.width)
                        dy = abs(current // self.width-node // self.width)
                        if max(dx, dy) <= 1:
                            self.blocked_edges.update(((current, node), (node, current)))
                            self.planned_at = -100
                break
            waypoint = candidate
        return norm(waypoint[0] - position[0], waypoint[1] - position[1])
