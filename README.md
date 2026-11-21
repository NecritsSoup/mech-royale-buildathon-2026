# Soup Sentinel — Mech Royale

A survival-first Mech Royale bot. Each match awards `16 - place` points;
kills have no direct score bonus. The controller therefore prioritizes staying
inside the zone, breaking line of sight, recovering health, and avoiding
projectiles. Combat is defensive.

The practice scorer uses 15 house bots acting as one squad: they share vision,
never fight each other, and hunt the entrant. Passive hiding alone therefore
cannot earn elimination-based placement gains. This first candidate needs an
official baseline run to assess its defensive combat and survival behavior.

## Strategy

- Move toward cover in the next safe circle before the shrink becomes urgent.
- Stay nearby when already safe; avoid unnecessary trips across open ground.
- Retreat and heal before pursuing incidental loot. Approach supply drops only
  when nearby and apparently uncontested.
- Predict projectile closest approaches and step away from charging laser lines.
- Keep energy reserves for escape, with a recharge interval between boost bursts.
- Prefer health, armor, repair, and mobility upgrades. Pick up nearby lightning
  for short-range defense; do not chase kills or start long-range fights.
- Check solid geometry before firing at remembered enemies behind walls.

Terrain, building prisms, courtyards, and bridge clearance come from the map
mounted by the server. A bounded A* planner supplies routes; local movement
checks account for the mech's current size. The bot uses the supplied Python
protocol wrapper, `mechfight.py`.

## Source and license

`mechfight.py` and `protocol.md` come from the official Builders Mech Royale
starter kit. The controller, geometry, physics, and check tooling were written
for this entry. Runtime dependencies are Python's standard library only.
Project code is released under the MIT License; see `LICENSE`.

## Submission

The repository root must contain these five files:

```
Dockerfile
bot.py
geometry.py
physics.py
mechfight.py
```

The image needs no package installation or build-time map collection. It uses a
pinned Python 3.13 base image and loads the server's map at runtime. The official
kit, arena data, and generated check output are not part of the submission.

## Local checks

Obtain and unpack the official `mechfight-kit`, then run with Python 3.13:

```sh
python3 check_local.py --kit /absolute/path/to/mechfight-kit --stress
MECHFIGHT_KIT=/absolute/path/to/mechfight-kit python3 -m unittest discover -s tests -v
```

In the original workspace, `check_local.py` also finds the official kit archive
at `../kits/mech-royale/mechfight-kit.tar.gz` and extracts it into ignored `work/`.

Validated locally on macOS with Python 3.13.18:

- 22 geometry, projectile, and strategy tests passed.
- Official recorded-stream checker: 300/300 observations answered within 50 ms,
  no stale actions, wrong ticks, or malformed lines. Slowest response: 3.1 ms.
- Synthetic dense-observation timing check: 15 enemies and 120 projectiles over
  100 observations; slowest response: 4.8 ms (rounded up).

These checks validate protocol handling, selected behaviors, and local timing.
The recorded stream does not react to our actions. Neither check simulates a
match or measures survival, placement, or leaderboard score. Docker and the
Linux match sandbox have not been tested locally; no Docker engine is installed.
Actual match evaluation is still required.
