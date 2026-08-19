# Spider Walker

*Disclaimer: The add-on code was partially created and refactored using AI.*

A Blender 5.2 add-on that procedurally animates the legs of multi-legged
(spider-type) rigs in real time. Legs raycast down onto a surface, step in
turn to keep up with the body, and predict the direction of travel so they
land ahead of the body instead of trailing behind it. A bake operator turns
the live simulation into a regular keyframed Action when you're happy with
the result.

## Features

- Real-time procedural leg placement driven by the body's movement
- Per-leg round-robin gait so legs don't all step at once
- Surface raycasting so feet land on the ground/mesh instead of floating
- Speed-based prediction so legs reach ahead of the body when it's moving fast
- One-click bake to a keyframed Action for rendering or further editing

## Installation

1. Download `spider_walker_source.py` (or a `.zip` build of the add-on).
2. In Blender: **Edit → Preferences → Add-ons → Install...**
3. Select the file and confirm.
4. Enable the checkbox next to **Spider Walker** in the add-on list.

The panel appears in **Properties → Armature Data (the green running-man
icon) → Spider Walker**, and is only visible while an Armature object is
selected.

## Quick Start

1. Select your armature and open **Properties → Armature Data → Spider
   Walker**.
2. Under **Rig Setup**:
   - Set **Armature** (or click **Use Active Armature** if your armature is
     already selected).
   - Set **Center Bone** — the bone that represents the body/root of the
     creature. Leg placement is calculated relative to this bone's motion.
3. Under **Legs**:
   - Enter **Pose Mode**, select the leg controller bones (the bones that
     actually drive foot placement — typically IK targets or IK end
     bones), then click **Add Selected Bones**.
   - The order in the list is the gait order the legs will step in. Use the
     up/down arrows to reorder, the checkbox to temporarily disable a leg
     without removing it, and **X** to remove one.
4. **Important rig requirement:** the leg controller bones must **not** be
   children of the Center Bone in the armature's bone hierarchy. If a leg
   bone is parented (directly or indirectly) to the Center Bone, it will
   inherit the Center Bone's transform twice — once through the bone
   hierarchy and once through the add-on's own placement — producing
   incorrect or unstable leg positions. Keep leg controllers as siblings of
   the Center Bone, or parented to a separate root/deform bone instead.
   The add-on checks for this: **Add Selected Bones** skips and warns
   about any selected bone that's a descendant of the Center Bone, and the
   same check runs again before the simulation starts, surfacing a clear
   error in the panel if it's ever violated (e.g. after re-parenting a
   bone that was already added as a leg).
5. Under **Surface Collision**, set the **Collection** containing the
   ground/mesh the feet should walk on.
6. Check **Enable procedural walk** and move the armature (or play an
   animation that moves it) — the legs should start stepping and following
   the body.
7. When you're happy with the result, use **Bake Legs** to convert it into
   a normal keyframed Action.

## How It Works (short version)

Each frame, the add-on:

1. Reads the Center Bone's current world-space position and estimates the
   body's velocity.
2. For each leg (one leg per gait step, in list order), compares the leg's
   current planted position to a *predicted target* — the leg's rest
   position relative to the body, shifted forward in the body's direction
   of travel based on its speed.
3. If the leg has drifted far enough from that target, it raycasts down
   from the predicted target onto the Surface Collision collection to find
   a landing point, and starts a step: the foot lifts, arcs to the new
   target, and plants.
4. Legs step one at a time in list order, so the gait doesn't look like
   every leg moving in unison.

## Parameters

### Rig Setup

| Parameter | Description |
|---|---|
| **Armature** | The armature object to animate. |
| **Center Bone** | The body/root pose bone. All leg targets are calculated relative to this bone's world-space position and rotation. |

### Legs

A reorderable list of leg controller bones. The list order is the gait
order. Each entry can be temporarily disabled with its checkbox without
removing it from the list.

### Procedural

| Parameter | Description |
|---|---|
| **Enable procedural walk** | Turns the real-time simulation on or off. When off, legs stay where they last were and the background timer is stopped. |

### Surface Collision

| Parameter | Description |
|---|---|
| **Collection** | The collection of mesh objects feet raycast against to find a landing surface. This should contain your ground/terrain meshes — **not** the character's own body mesh, since the raycast checks every visible mesh in the collection with no exception for meshes parented to the rig. (The "Collection has no visible mesh objects" warning in the panel does ignore meshes parented to the armature when counting, purely so it doesn't false-trigger if your rig's own body happens to sit in the same collection — but the actual foot raycast does not apply that exclusion, so keep ground and character meshes in separate collections.) |
| **Probe Distance** | How far (in both directions) the add-on searches for a walkable surface from each predicted foot target. Too short and legs on sloped or uneven ground may fail to find the surface; too long is mostly harmless but slightly more expensive. |

### Step

| Parameter | Description |
|---|---|
| **Distance** | How far a leg's current planted position must drift from its predicted target before a new step is triggered. Smaller values make legs step more often with shorter strides; larger values make legs "hold on" longer before stepping. |
| **Speed** | Base speed of the foot's swing animation as it moves from its old position to its new one during a step. Higher values make steps snappier. |
| **Height** | How high the foot lifts off the ground mid-step (the arc height). |
| **Prediction Distance** | The single control for how far ahead of the body, in its current direction of travel, legs are placed as the body moves faster. `0` disables prediction entirely (legs simply follow the body's current position, so they'll always trail slightly behind during fast movement). Higher values let legs reach further ahead at speed. This value is a hard cap in world units — it does not scale with any other parameter. |

### Advanced Settings

| Parameter | Description |
|---|---|
| **Min Step Time** | The shortest a single step's swing animation is allowed to take, regardless of step distance. Prevents very short steps from looking like an instant snap. |
| **Max Step Time** | The longest a single step's swing animation is allowed to take. Mostly relevant at low **Speed** values with long step distances, where it prevents an individual step from taking an unnaturally long time to complete. |
| **Surface Offset** | Shifts the raycast-landed foot target away from the surface along the surface normal. Positive values lift the foot target slightly above the surface; negative values sink it slightly below. Useful for compensating for foot mesh thickness or IK target pivot placement. |
| **Speed Step Scale** | How much the step-trigger distance (see **Distance** above) grows with the body's current speed. `0` means the step-trigger distance never changes with speed; higher values make legs trigger new steps sooner (and take proportionally longer strides) the faster the body moves. |
| **Max Speed Step Distance** | A hard cap on the step-trigger distance, regardless of how fast the body is moving or how high **Speed Step Scale** is set. If this is set *lower* than **Distance**, it wins — steps stay short even when the body is standing still, and **Speed Step Scale** effectively has no visible effect until **Distance** is also lowered below this cap. |

### Bake Legs

| Parameter | Description |
|---|---|
| **Start / End** | The frame range to bake. |
| **Bake** | Runs the simulation across the given frame range and writes the resulting leg positions as keyframes on a new Action, so the walk cycle no longer depends on the add-on running live. |

## Requirements

- Blender 5.2 or newer.
- An armature with a body/root bone and independently posable leg
  controller bones that are **not** children of the body bone.
- A collection of mesh objects for the feet to walk on.

## Notes

- The add-on keeps its own runtime state outside of Blender's undo system.
  If you undo/redo a change to the settings, it automatically re-syncs on
  the next step so the simulation doesn't get stuck.
- If nothing appears to be moving, check the **Configuration Errors** box
  that appears in the panel when the current rig/leg setup is invalid
  (e.g. a missing bone or an empty leg list).
