"""Small deterministic aiming and projectile-threat models."""
import math


def wrap(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


def intercept(origin, target, velocity, speed, limit=2.0):
    """Positive constant-velocity interception; no speed or weapon constants baked in."""
    r = [target[i] - origin[i] for i in range(3)]
    a = sum(v * v for v in velocity) - speed * speed
    b = 2 * sum(r[i] * velocity[i] for i in range(3))
    c = sum(v * v for v in r)
    roots = []
    if abs(a) < 1e-9:
        if abs(b) > 1e-9:
            roots = [-c / b]
    elif b * b - 4 * a * c >= 0:
        d = math.sqrt(b * b - 4 * a * c)
        roots = [(-b - d) / (2 * a), (-b + d) / (2 * a)]
    positive = [t for t in roots if 0 <= t <= limit]
    if not positive:
        return None
    t = min(positive)
    return [target[i] + velocity[i] * t for i in range(3)], t


def angles(origin, target):
    dx, dy, dz = [target[i] - origin[i] for i in range(3)]
    return [math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy))]


def projectile_danger(me, move, speed, projectiles, radius, height, mortar_gravity=22):
    """Penalize trajectories predicted to pass through our body in the next second."""
    center = [me['pos'][0], me['pos'][1], me['pos'][2] + height * .5]
    # Acceleration prevents a new direction from taking effect immediately.
    velocity = [me['vel'][i] * .35 + move[i] * speed * .65 for i in range(2)]
    penalty = 0.0
    for p in projectiles:
        rx, ry = p['pos'][0] - center[0], p['pos'][1] - center[1]
        vx, vy = p['vel'][0] - velocity[0], p['vel'][1] - velocity[1]
        vv = vx * vx + vy * vy
        if vv < 1e-8:
            continue
        t = -(rx * vx + ry * vy) / vv
        if not 0 < t < 1.1:
            continue
        gap = math.hypot(rx + vx * t, ry + vy * t)
        z = p['pos'][2] + p['vel'][2] * t
        if p['kind'] == 'mortar':
            z -= .5 * mortar_gravity * t * t
        if me['pos'][2] - 2 <= z <= me['pos'][2] + height + 2:
            hit_radius = radius + (7 if p['kind'] == 'mortar' else 1.0)
            if gap < hit_radius + 4:
                penalty += max(0.0, hit_radius + 4 - gap) * (12 if p['kind'] == 'mortar' else 8) / (.2 + t)
    return penalty
