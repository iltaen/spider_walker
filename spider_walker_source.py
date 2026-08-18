bl_info = {
    "name": "Spider Walker",
    "author": "iltaen",
    "version": (1, 0, 2),
    "blender": (5, 2, 0),
    "location": "Properties > Armature Data > Spider Walker",
    "description": (
        "Realtime procedural leg-walking for multi-legged (spider-type) "
        "rigs, with surface raycast collision and an action-bake operator."
    ),
    "category": "Animation",
}

# Procedural walking for multi-legged rigs.
# Per-leg rest-pose collision directions, adaptive step prediction,
# surface raycasting and a bake-to-action workflow.

import bpy
import math
import time
import uuid

from mathutils import Vector
from bpy.types import (
    Panel,
    Operator,
    PropertyGroup,
    UIList,
)
from bpy.props import (
    BoolProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
    CollectionProperty,
)


# ============================================================
# CONFIGURATION
# ============================================================

BODY_BONE_DEFAULT = "CTRL-body"

MAX_SIMULTANEOUS_STEPS = 2

STEP_COOLDOWN = 0.04

TIMER_INTERVAL = 0.01


# ============================================================
# RUNTIME
# ============================================================

runtime = {}

TIMER_TOKEN = uuid.uuid4().hex


# ============================================================
# DATA TYPES
# ============================================================

class SpiderLegItem(PropertyGroup):

    bone_name: StringProperty(
        name="Bone",
        default="",
    )

    enabled: BoolProperty(
        name="Use",
        default=True,
    )


class SpiderWalkerSettings(PropertyGroup):

    # --------------------------------------------------------
    # Rig
    # --------------------------------------------------------

    rig: PointerProperty(
        name="Armature",
        type=bpy.types.Object,
    )

    body_bone: StringProperty(
        name="Center Bone",
        default=BODY_BONE_DEFAULT,
    )

    legs: CollectionProperty(
        type=SpiderLegItem,
    )

    active_leg_index: IntProperty(
        default=0,
    )

    show_advanced_settings: BoolProperty(
        name="Advanced Settings",
        description="Show advanced procedural controls",
        default=False,
    )

    # --------------------------------------------------------
    # Procedural
    # --------------------------------------------------------

    enabled: BoolProperty(
        name="Procedural Walk",
        description="Enable realtime procedural walking",
        default=True,
        update=lambda self, context: on_enabled_toggle(self, context),
    )


    # --------------------------------------------------------
    # Surface collision
    # --------------------------------------------------------

    collision_collection: PointerProperty(
        name="Collision Collection",
        description="Collection containing surfaces used for foot raycasts",
        type=bpy.types.Collection,
    )

    surface_probe_distance: FloatProperty(
        name="Probe Distance",
        description="Maximum distance used to search for a walkable surface",
        default=5.00,
        min=0.1,
        max=50.0,
    )

    surface_offset: FloatProperty(
        name="Surface Offset",
        description="Offset of the IK foot target away from the surface",
        default=0.00,
        min=-1.0,
        max=1.0,
    )

    # --------------------------------------------------------
    # Step
    # --------------------------------------------------------

    step_distance: FloatProperty(
        name="Step Distance",
        description="Distance required to trigger a step",
        default=1.00,
        min=0.01,
        max=10.0,
    )

    step_speed: FloatProperty(
        name="Step Speed",
        description="Base foot movement speed",
        default=10.00,
        min=0.01,
        max=50.0,
    )

    min_step_time: FloatProperty(
        name="Min Step Time",
        description="Minimum duration of a step",
        default=0.10,
        min=0.01,
        max=5.0,
    )

    max_step_time: FloatProperty(
        name="Max Step Time",
        description="Maximum duration of a step",
        default=0.50,
        min=0.01,
        max=5.0,
    )

    step_height: FloatProperty(
        name="Step Height",
        description="Height of the foot arc",
        default=0.30,
        min=0.0,
        max=5.0,
    )

    # --------------------------------------------------------
    # User-facing prediction control
    # --------------------------------------------------------

    prediction_distance: FloatProperty(
        name="Prediction Distance",
        description=(
            "How far ahead of the body, in the direction of travel, a leg "
            "is placed at high speed. 0 disables prediction (legs only "
            "follow the body's current position)"
        ),
        default=2.00,
        min=0.0,
        soft_max=10.0,
    )

    speed_step_scale: FloatProperty(
        name="Speed Step Scale",
        description="Allows step distance to increase with body speed",
        default=0.35,
        min=0.0,
        max=2.0,
        options={'HIDDEN'},
    )

    max_speed_step_distance: FloatProperty(
        name="Max Speed Step Distance",
        description="Maximum adaptive step distance at high speed",
        default=1.30,
        min=0.05,
        max=10.0,
        options={'HIDDEN'},
    )

    # --------------------------------------------------------
    # Bake
    # --------------------------------------------------------

    bake_start: IntProperty(
        name="Start",
        description="Bake start frame",
        default=1,
        min=0,
    )

    bake_end: IntProperty(
        name="End",
        description="Bake end frame",
        default=250,
        min=1,
    )


# ============================================================
# RIG HELPERS
# ============================================================

def get_configured_rig(
    settings
):

    rig = settings.rig

    if rig is None:
        return None

    if rig.type != 'ARMATURE':
        return None

    if bpy.data.objects.get(rig.name) is None:
        return None

    return rig


def get_body_bone(
    armature,
    settings
):

    if not settings.body_bone:
        return None

    return armature.pose.bones.get(
        settings.body_bone
    )


def get_enabled_leg_names(
    settings
):

    result = []

    for item in settings.legs:

        if not item.enabled:
            continue

        if not item.bone_name:
            continue

        if item.bone_name == settings.body_bone:
            continue

        if item.bone_name in result:
            continue

        result.append(
            item.bone_name
        )

    return result


def count_collision_meshes(collection):
    """Return the number of visible mesh objects in a collision collection."""
    if collection is None:
        return 0

    count = 0

    try:
        objects = collection.all_objects
    except Exception:
        return 0

    for obj in objects:
        if obj.type != 'MESH':
            continue

        try:
            if obj.hide_viewport:
                continue
        except Exception:
            pass

        collision_armature = runtime.get("armature")
        if collision_armature is not None:
            if obj == collision_armature:
                continue

            if obj.parent == collision_armature:
                continue

        count += 1

    return count


def validate_step_settings(settings):
    """Validate values that can make procedural stepping unstable."""
    if settings.step_distance <= 0.0:
        raise RuntimeError("Step Distance must be greater than 0")

    if settings.step_speed <= 0.0:
        raise RuntimeError("Step Speed must be greater than 0")

    if settings.min_step_time <= 0.0:
        raise RuntimeError("Min Step Time must be greater than 0")

    if settings.max_step_time < settings.min_step_time:
        raise RuntimeError(
            "Max Step Time must be greater than or equal to Min Step Time"
        )

    if settings.prediction_distance < 0.0:
        raise RuntimeError("Prediction Distance cannot be negative")

    if settings.speed_step_scale < 0.0:
        raise RuntimeError("Internal speed step scale cannot be negative")

    # This is an internal adaptive-step limit and is hidden from the UI.
    # Older saved scenes may contain a smaller legacy value, so normalize it
    # automatically instead of showing an error the user cannot fix.
    if settings.max_speed_step_distance < settings.step_distance:
        settings.max_speed_step_distance = settings.step_distance


def validate_collision_settings(settings):
    """Validate the optional collision collection without breaking the walker."""
    collection = settings.collision_collection

    if collection is None:
        return

    if settings.surface_probe_distance <= 0.0:
        raise RuntimeError(
            "Probe Distance must be greater than 0"
        )

    mesh_count = count_collision_meshes(collection)

    if mesh_count == 0:
        print(
            f"[Spider Walker V7] Warning: collision collection '{collection.name}' "
            "contains no visible mesh objects; surface collision will be skipped."
        )


def validate_configuration(
    settings
):

    validate_step_settings(settings)
    validate_collision_settings(settings)

    armature = get_configured_rig(
        settings
    )

    if armature is None:

        raise RuntimeError(
            "No valid armature selected"
        )

    body = get_body_bone(
        armature,
        settings
    )

    if body is None:

        raise RuntimeError(
            f"Center bone '{settings.body_bone}' not found"
        )

    leg_names = get_enabled_leg_names(
        settings
    )

    if not leg_names:

        raise RuntimeError(
            "No enabled leg bones"
        )

    for name in leg_names:

        if (
            armature.pose.bones.get(name)
            is None
        ):

            raise RuntimeError(
                f"Leg bone '{name}' not found"
            )

    return (
        armature,
        body,
        leg_names
    )


# ============================================================
# WORLD SPACE
# ============================================================

def get_world_matrix(
    armature,
    bone
):

    return (
        armature.matrix_world
        @
        bone.matrix
    )


def get_world_position(
    armature,
    bone
):

    return get_world_matrix(
        armature,
        bone
    ).translation.copy()


def set_world_position(
    armature,
    bone,
    world_position
):

    matrix = bone.matrix.copy()

    local_position = (
        armature.matrix_world.inverted()
        @
        world_position
    )

    matrix.translation = local_position

    bone.matrix = matrix


# ============================================================
# SURFACE RAYCAST
# ============================================================

def calculate_initial_collision_down(offsets):
    """Calculate an initial support direction from rest-pose leg directions."""
    vectors = []

    for offset in offsets.values():
        if offset.length <= 1e-8:
            continue
        vectors.append(offset.normalized())

    if not vectors:
        return Vector((0.0, 0.0, -1.0))

    average = Vector((0.0, 0.0, 0.0))

    for vector in vectors:
        average += vector

    if average.length <= 1e-8:
        return Vector((0.0, 0.0, -1.0))

    return average.normalized()


def get_support_down_direction(armature, body):
    """Return the current support direction in world space."""
    local_direction = runtime.get(
        "collision_local_down",
        Vector((0.0, 0.0, -1.0))
    )

    body_matrix = get_world_matrix(
        armature,
        body
    )

    direction = (
        body_matrix.to_3x3()
        @ local_direction
    )

    if direction.length <= 1e-8:
        direction = Vector((0.0, 0.0, -1.0))

    return direction.normalized()


def update_support_direction(hit_normals):
    """Adapt the global support direction toward detected surface normals."""
    if not hit_normals:
        return

    average_normal = Vector((0.0, 0.0, 0.0))

    for normal in hit_normals:
        if normal.length > 1e-8:
            average_normal += normal.normalized()

    if average_normal.length <= 1e-8:
        return

    average_normal.normalize()

    current_up = -runtime.get(
        "collision_local_down",
        Vector((0.0, 0.0, -1.0))
    )

    # Convert the detected world normal back into the body's local space.
    armature = runtime.get("armature")
    body = runtime.get("body")

    if armature is None or body is None:
        return

    body_matrix = get_world_matrix(
        armature,
        body
    )

    local_up = (
        body_matrix.to_3x3().inverted()
        @ average_normal
    )

    if local_up.length <= 1e-8:
        return

    local_up.normalize()

    target_down = -local_up
    current_down = current_up.normalized() * -1.0

    # Blend rather than snapping to a new surface orientation.
    blend = 0.15
    blended = current_down.lerp(
        target_down,
        blend
    )

    if blended.length <= 1e-8:
        return

    runtime["collision_local_down"] = blended.normalized()


def raycast_surface(
    scene,
    collection,
    origin,
    direction,
    max_distance
):
    """Raycast against all mesh objects in the selected collection."""
    if collection is None:
        return None

    if max_distance <= 1e-6:
        return None

    direction = direction.copy()

    if direction.length <= 1e-8:
        return None

    direction.normalize()

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    except Exception:
        return None

    best_distance = float("inf")
    best_hit = None

    try:
        collection_pointer = collection.as_pointer()
    except Exception:
        collection_pointer = 0

    if runtime.get("collision_debug_collection") != collection_pointer:
        mesh_count = count_collision_meshes(collection)

        print(
            f"[Spider Walker V7] Collision collection: "
            f"{collection.name} | visible meshes: {mesh_count}"
        )

        runtime["collision_debug_collection"] = collection_pointer

    for obj in collection.all_objects:

        if obj.type != 'MESH':
            continue

        try:
            if obj.hide_viewport:
                continue
        except Exception:
            pass

        try:
            obj_eval = obj.evaluated_get(
                depsgraph
            )

            matrix_world = (
                obj_eval.matrix_world.copy()
            )

            matrix_inverse = (
                matrix_world.inverted()
            )

            local_origin = (
                matrix_inverse
                @ origin
            )

            local_direction = (
                matrix_inverse.to_3x3()
                @ direction
            )

            local_length = (
                local_direction.length
            )

            if local_length <= 1e-8:
                continue

            local_direction.normalize()

            hit, location, normal, face_index = (
                obj_eval.ray_cast(
                    local_origin,
                    local_direction,
                    distance=max_distance * local_length,
                    depsgraph=depsgraph
                )
            )

            if not hit:
                continue

            world_location = (
                matrix_world
                @ location
            )

            world_normal = (
                matrix_world.to_3x3()
                .inverted()
                .transposed()
                @ normal
            )

            if world_normal.length <= 1e-8:
                continue

            world_normal.normalize()

            distance = (
                world_location - origin
            ).length

            if distance < best_distance:
                best_distance = distance
                best_hit = (
                    world_location,
                    world_normal,
                    obj
                )

        except Exception as exc:
            print(
                f"[Spider Walker V7] "
                f"Raycast skipped for {obj.name}: {exc}"
            )
            continue

    return best_hit


def project_to_surface(
    scene,
    armature,
    body,
    position,
    settings
):
    """Project a predicted foot point onto the current support surface."""
    collection = settings.collision_collection

    support_down = get_support_down_direction(
        armature,
        body
    )

    support_up = -support_down

    if collection is None:
        return position.copy(), support_up

    probe = max(
        settings.surface_probe_distance,
        0.1
    )

    body_position = get_world_position(
        armature,
        body
    )

    # Build a probe column through the expected foot position.
    # The probe origin is placed on the body-height support plane.
    relative = position - body_position

    plane_offset = relative.dot(
        support_down
    )

    probe_point = (
        position
        - support_down * plane_offset
    )

    # Start above the support plane and cast toward the surface.
    origin = (
        probe_point
        - support_down * (probe * 0.5)
    )

    hit = raycast_surface(
        scene,
        collection,
        origin,
        support_down,
        probe * 2.0
    )

    if hit is None:
        return position.copy(), support_up

    hit_position, hit_normal, _ = hit

    if hit_normal.length <= 1e-8:
        hit_normal = support_up.copy()
    else:
        hit_normal.normalize()

    hit_position += (
        hit_normal
        * settings.surface_offset
    )

    return hit_position, hit_normal


def get_predicted_surface_target(
    scene,
    armature,
    body,
    offset,
    velocity,
    settings
):
    """Calculate a predicted foot target and optionally project it to a surface."""
    target = get_predicted_target(
        armature,
        body,
        offset,
        velocity,
        settings
    )

    return project_to_surface(
        scene,
        armature,
        body,
        target,
        settings
    )


# ============================================================
# REST OFFSETS
# ============================================================

def calculate_rest_offsets(
    armature,
    body_name,
    leg_names
):

    body_data = (
        armature.data.bones.get(
            body_name
        )
    )

    if body_data is None:

        raise RuntimeError(
            f"Center bone '{body_name}' not found"
        )

    body_rest_world = (
        armature.matrix_world
        @
        body_data.matrix_local
    )

    body_inverse = (
        body_rest_world.inverted()
    )

    offsets = {}

    for name in leg_names:

        bone_data = (
            armature.data.bones.get(
                name
            )
        )

        if bone_data is None:
            continue

        leg_rest_world = (
            armature.matrix_world
            @
            bone_data.matrix_local
        )

        offsets[name] = (
            body_inverse
            @
            leg_rest_world.translation
        )

    return offsets


# ============================================================
# LEG STATES
# ============================================================

def create_leg_states(
    armature,
    offsets,
    leg_names,
    planted_positions=None,
    planted_normals=None
):

    result = []

    for name in leg_names:

        bone = (
            armature.pose.bones.get(
                name
            )
        )

        if bone is None:
            continue

        if planted_positions is not None:

            position = (
                planted_positions[name].copy()
            )

        else:

            position = get_world_position(
                armature,
                bone
            )

        body = armature.pose.bones.get(
            runtime.get("body_name", "")
        )

        if body is not None:
            body_up = -get_support_down_direction(
                armature,
                body
            )
        else:
            body_up = Vector((0.0, 0.0, 1.0))

        normal = body_up.copy()

        if planted_normals is not None:
            normal = planted_normals.get(
                name,
                body_up
            ).copy()

        result.append({

            "name": name,

            "bone": bone,

            "offset": offsets[name],

            "planted": position.copy(),

            "start": position.copy(),

            "target": position.copy(),

            "normal": normal.copy(),

            "target_normal": normal.copy(),

            "stepping": False,

            "step_start_time": 0.0,

            "step_start_frame": 0,

            "step_duration": 0.0,

        })

    return result


# ============================================================
# CAPTURE CURRENT LEG POSITIONS
# ============================================================

def capture_leg_positions(
    scene,
    armature,
    body,
    leg_names,
    settings
):

    positions = {}
    normals = {}

    for name in leg_names:

        bone = armature.pose.bones.get(name)

        if bone is None:
            continue

        position = get_world_position(
            armature,
            bone
        )

        # Preserve the current foot state exactly during a runtime rebuild.
        # Surface collision must never teleport planted feet when its settings change.
        body_down = get_support_down_direction(
            armature,
            body
        )

        body_up = -body_down

        positions[name] = position.copy()
        normals[name] = body_up.copy()

    return positions, normals


# ============================================================
# PREDICTION
# ============================================================

def get_predicted_target(
    armature,
    body,
    offset,
    velocity,
    settings
):
    """Shift a leg's rest target ahead of the body in its direction of
    travel, so legs land in front of the body instead of trailing it.

    Model: by the time the upcoming step finishes, the body will have
    moved roughly `speed * step_duration` further along its current
    direction of travel. The target is shifted that far ahead so the leg
    is still under (or ahead of) the body when the step completes, capped
    at `settings.prediction_distance` -- the single user-facing control
    for how far forward legs reach at high speed.
    """

    body_matrix = get_world_matrix(
        armature,
        body
    )

    body_position = (
        body_matrix.translation.copy()
    )

    rest_target = (
        body_position
        +
        body_matrix.to_3x3()
        @
        offset
    )

    if settings.prediction_distance <= 0.0:
        return rest_target

    speed = velocity.length

    if speed <= 1e-6:
        return rest_target

    direction = velocity / speed

    # How far the body is expected to travel while this step is in the
    # air. Reuses the same adaptive-distance and duration model the step
    # system itself uses, so prediction stays consistent with actual
    # step timing instead of following an unrelated separate curve.
    estimated_step_distance = get_adaptive_step_distance(
        velocity,
        settings
    )

    estimated_duration = calculate_step_duration(
        estimated_step_distance,
        settings
    )

    # Lead distance grows directly with body speed (already ~0 when the
    # body is nearly still, since speed itself is ~0) and is capped by
    # the single user-facing distance control. No separate speed-ratio
    # gate here: an earlier version gated this against settings.step_speed
    # as a stand-in "reference speed", but step_speed is a foot-swing rate,
    # not the body's actual world-space speed, and a mismatch between the
    # two units could hold the gate at zero and make Prediction Distance
    # appear to do nothing no matter what it was set to.
    lead_distance = min(
        speed * estimated_duration,
        settings.prediction_distance
    )

    return rest_target + direction * lead_distance



# ============================================================
# STEP DURATION
# ============================================================

# ============================================================

def calculate_step_duration(
    distance,
    settings
):

    normalized = min(
        distance / 0.8,
        1.0
    )

    speed_multiplier = (
        0.55
        +
        normalized * 1.45
    )

    duration = (
        distance
        /
        settings.step_speed
        /
        speed_multiplier
    )

    duration = max(
        settings.min_step_time,
        duration
    )

    duration = min(
        settings.max_step_time,
        duration
    )

    return duration


# ============================================================
# START STEP
# ============================================================

def start_step(
    leg,
    target,
    target_normal,
    now,
    settings,
    current_frame=0
):

    if leg["stepping"]:
        return False

    delta = (
        target
        -
        leg["planted"]
    )

    distance = delta.length

    if (
        distance
        <
        settings.step_distance
    ):
        return False

    leg["start"] = (
        leg["planted"].copy()
    )

    leg["target"] = (
        target.copy()
    )

    leg["target_normal"] = (
        target_normal.copy()
    )

    leg["step_start_time"] = now

    leg["step_start_frame"] = (
        current_frame
    )

    leg["step_duration"] = (
        calculate_step_duration(
            distance,
            settings
        )
    )

    leg["stepping"] = True

    return True


# ============================================================
# UPDATE STEP
# ============================================================

def update_step(
    armature,
    leg,
    now,
    settings
):

    if not leg["stepping"]:
        return

    elapsed = (
        now
        -
        leg["step_start_time"]
    )

    duration = max(
        leg["step_duration"],
        0.001
    )

    t = (
        elapsed
        /
        duration
    )

    t = max(
        0.0,
        min(
            1.0,
            t
        )
    )

    t_smooth = (
        t
        *
        t
        *
        (3.0 - 2.0 * t)
    )

    position = (
        leg["start"].lerp(
            leg["target"],
            t_smooth
        )
    )

    normal = leg["normal"].lerp(
        leg["target_normal"],
        t_smooth
    )

    if normal.length > 1e-8:
        normal.normalize()

        position += (
            normal
            *
            math.sin(t * math.pi)
            *
            settings.step_height
        )

    set_world_position(
        armature,
        leg["bone"],
        position
    )

    if t >= 1.0:

        leg["planted"] = (
            leg["target"].copy()
        )

        leg["normal"] = (
            leg["target_normal"].copy()
        )

        leg["stepping"] = False

        set_world_position(
            armature,
            leg["bone"],
            leg["planted"]
        )


# ============================================================
# VELOCITY
# ============================================================

def update_velocity():

    armature = runtime.get(
        "armature"
    )

    body = runtime.get(
        "body"
    )

    if armature is None or body is None:
        return

    now = time.perf_counter()

    previous_time = runtime.get(
        "velocity_time",
        now
    )

    dt = (
        now
        -
        previous_time
    )

    if dt <= 0.0001:
        return

    current = get_world_position(
        armature,
        body
    )

    previous = runtime.get(
        "previous_body_position"
    )

    if previous is None:

        runtime[
            "previous_body_position"
        ] = current.copy()

        runtime[
            "velocity_time"
        ] = now

        return

    delta = (
        current
        -
        previous
    )

    velocity = (
        delta
        /
        dt
    )

    smoothing = 0.35

    old_velocity = runtime.get(
        "velocity",
        Vector((0.0, 0.0, 0.0))
    )

    runtime["velocity"] = (
        old_velocity
        *
        (1.0 - smoothing)
        +
        velocity
        *
        smoothing
    )

    runtime[
        "previous_body_position"
    ] = current.copy()

    runtime[
        "velocity_time"
    ] = now


# ============================================================
# RUNTIME CONFIGURATION SIGNATURE
# ============================================================

def get_configuration_signature(
    settings
):

    rig = settings.rig

    rig_pointer = (
        rig.as_pointer()
        if rig is not None
        else 0
    )

    legs = tuple(
        (
            item.bone_name,
            bool(item.enabled)
        )
        for item in settings.legs
    )

    return (
        rig_pointer,
        settings.body_bone,
        legs,
        settings.collision_collection.as_pointer()
        if settings.collision_collection is not None
        else 0,
        round(settings.surface_probe_distance, 4),
        round(settings.surface_offset, 4),
        round(settings.speed_step_scale, 4),
        round(settings.max_speed_step_distance, 4),
    )


# ============================================================
# REBUILD RUNTIME
# ============================================================

def rebuild_runtime(
    settings,
    preserve_positions=True
):

    try:

        (
            armature,
            body,
            leg_names
        ) = validate_configuration(
            settings
        )

    except Exception as e:

        runtime["configured"] = False

        runtime["configuration_error"] = str(e)

        return False

    offsets = calculate_rest_offsets(
        armature,
        settings.body_bone,
        leg_names
    )

    runtime["collision_local_down"] = calculate_initial_collision_down(
        offsets
    )

    planted_positions = None
    planted_normals = None

    if preserve_positions:

        (
            planted_positions,
            planted_normals
        ) = capture_leg_positions(
            bpy.context.scene,
            armature,
            body,
            leg_names,
            settings
        )

    legs = create_leg_states(
        armature,
        offsets,
        leg_names,
        planted_positions,
        planted_normals
    )

    now = time.perf_counter()

    runtime["configured"] = True

    runtime["configuration_error"] = ""

    runtime["armature"] = armature

    runtime["body"] = body

    runtime["body_name"] = settings.body_bone

    runtime["leg_names"] = leg_names

    runtime["offsets"] = offsets

    runtime["legs"] = legs

    runtime["gait_index"] = 0

    runtime["velocity"] = Vector(
        (0.0, 0.0, 0.0)
    )

    runtime[
        "previous_body_position"
    ] = get_world_position(
        armature,
        body
    )

    runtime[
        "velocity_time"
    ] = now

    runtime[
        "last_step_time"
    ] = -999999.0

    runtime[
        "configuration_signature"
    ] = get_configuration_signature(
        settings
    )

    runtime["collision_debug_collection"] = None

    return True



def get_adaptive_step_distance(
    velocity,
    settings
):
    """Increase the allowed step distance as body speed increases."""

    speed = velocity.length

    adaptive = (
        settings.step_distance
        +
        speed
        *
        settings.speed_step_scale
    )

    return min(
        adaptive,
        settings.max_speed_step_distance
    )

# ============================================================
# FIND NEXT LEG
# ============================================================

def find_next_leg(
    settings
):

    armature = runtime["armature"]
    body = runtime["body"]
    legs = runtime["legs"]
    velocity = runtime["velocity"]
    count = len(legs)

    if count == 0:
        return (None, None, None, None, False)

    # Process exactly one leg per gait turn.
    # Once the turn is evaluated, the queue advances even if
    # the leg does not need a step. This prevents repeated
    # target rescans on complex geometry.
    index = runtime["gait_index"] % count
    leg = legs[index]

    runtime["gait_index"] = (index + 1) % count

    if leg["stepping"]:
        return (index, leg, None, None, False)

    target, normal = get_predicted_surface_target(
        bpy.context.scene,
        armature,
        body,
        leg["offset"],
        velocity,
        settings
    )

    if settings.collision_collection is not None:
        update_support_direction([normal])

    delta = target - leg["planted"]

    required_distance = get_adaptive_step_distance(
        velocity,
        settings
    )

    if delta.length < required_distance:
        return (index, leg, target, normal, False)

    return (index, leg, target, normal, True)

# ============================================================
# REALTIME TIMER
# ============================================================

def _spider_timer_tick():
    scene = bpy.context.scene

    if scene is None:
        return TIMER_INTERVAL

    if (
        scene.get(
            "_SPIDER_WALKER_TIMER_TOKEN",
            ""
        )
        != TIMER_TOKEN
    ):

        return None

    settings = getattr(
        scene,
        "spider_walker",
        None
    )

    if settings is None:
        return None

    current_signature = (
        get_configuration_signature(
            settings
        )
    )

    if (
        runtime.get(
            "configuration_signature"
        )
        != current_signature
    ):

        try:
            success = rebuild_runtime(
                settings,
                preserve_positions=True
            )
        except Exception as exc:
            runtime["configured"] = False
            runtime["configuration_error"] = str(exc)
            print(
                f"[Spider Walker V7] Configuration error: {exc}"
            )
            success = False

        if not success:
            return TIMER_INTERVAL

    if not runtime.get(
        "configured",
        False
    ):

        return TIMER_INTERVAL

    if not settings.enabled:

        runtime[
            "previous_body_position"
        ] = get_world_position(
            runtime["armature"],
            runtime["body"]
        )

        runtime[
            "velocity"
        ] = Vector(
            (0.0, 0.0, 0.0)
        )

        runtime[
            "velocity_time"
        ] = time.perf_counter()

        return TIMER_INTERVAL

    try:

        update_velocity()

        now = time.perf_counter()

        for leg in runtime["legs"]:

            update_step(
                runtime["armature"],
                leg,
                now,
                settings
            )

        active_steps = sum(
            1
            for leg in runtime["legs"]
            if leg["stepping"]
        )

        if (
            active_steps
            <
            min(
                MAX_SIMULTANEOUS_STEPS,
                len(runtime["legs"])
            )
        ):

            if (
                now
                -
                runtime["last_step_time"]
                >=
                STEP_COOLDOWN
            ):

                (
                    index,
                    leg,
                    target,
                    normal,
                    should_step
                ) = find_next_leg(
                    settings
                )

                if (
                    leg is not None
                    and
                    should_step
                ):

                    started = start_step(
                        leg,
                        target,
                        normal,
                        now,
                        settings,
                        scene.frame_current
                    )

                    if started:
                        runtime[
                            "last_step_time"
                        ] = now

    except Exception as e:

        print(
            "[Spider Walker] Timer error:",
            e
        )

    return TIMER_INTERVAL


def spider_timer():
    """Outer timer callback registered with bpy.app.timers.

    Wraps _spider_timer_tick() in a broad try/except: if the tick function
    raises anything unexpected, Blender's timer system silently unregisters
    the offending callback and the walk simply stops with no visible error
    unless the system console happens to be open. Printing the traceback
    here and always returning a retry interval keeps the timer alive and
    keeps the failure visible.
    """
    try:
        return _spider_timer_tick()
    except Exception:
        import traceback
        traceback.print_exc()
        print(
            "[Spider Walker] Timer tick crashed (see traceback above); "
            "retrying."
        )
        return TIMER_INTERVAL


def start_timer():
    """Start the procedural-walk timer if it isn't already running."""
    if not bpy.app.timers.is_registered(spider_timer):
        bpy.app.timers.register(
            spider_timer,
            first_interval=TIMER_INTERVAL,
            persistent=True,
        )
        print("[Spider Walker] Timer started.")


def stop_timer():
    """Stop the procedural-walk timer if it is currently running."""
    if bpy.app.timers.is_registered(spider_timer):
        bpy.app.timers.unregister(spider_timer)
        print("[Spider Walker] Timer stopped.")


def sync_timer_with_settings():
    """Start or stop the timer to match the current scene's checkbox state.

    Called during register(), from the enabled-toggle update callback, and
    on file load. At install/enable time Blender may call register() with
    a restricted context (bpy.context is a _RestrictContext) where even
    reading .scene raises AttributeError rather than returning None, so
    this must not assume plain attribute access is safe.
    """
    try:
        scene = bpy.context.scene
    except AttributeError:
        return

    if scene is None:
        return

    settings = getattr(scene, "spider_walker", None)

    if settings is None:
        return

    scene["_SPIDER_WALKER_TIMER_TOKEN"] = TIMER_TOKEN

    if settings.enabled:
        start_timer()
    else:
        stop_timer()


def on_enabled_toggle(self, context):
    """update= callback for SpiderWalkerSettings.enabled."""
    sync_timer_with_settings()


@bpy.app.handlers.persistent
def _spider_walker_load_post(dummy):
    """Re-sync the timer whenever a .blend file is opened."""
    sync_timer_with_settings()


# ============================================================
# ACTION
# ============================================================

def get_current_action(
    armature
):

    if (
        armature.animation_data
        is None
    ):

        armature.animation_data_create()

    if (
        armature.animation_data.action
        is None
    ):

        armature.animation_data.action = (
            bpy.data.actions.new(
                name="Spider Animation"
            )
        )

    return (
        armature.animation_data.action
    )


# ============================================================
# CLEAR BAKE RANGE
# ============================================================

def clear_leg_keys(
    armature,
    leg_names,
    start_frame,
    end_frame
):

    action = None

    if (
        armature.animation_data
        is not None
    ):

        action = (
            armature.animation_data.action
        )

    if action is None:

        print(
            "[Spider Walker] No active Action"
        )

        return 0

    leg_paths = {
        f'pose.bones["{name}"].location'
        for name in leg_names
    }

    removed = 0

    # Blender 5.2 uses Action Layers and Channel Bags.
    for layer in action.layers:

        for strip in layer.strips:

            for channelbag in strip.channelbags:

                for fc in channelbag.fcurves:

                    if (
                        fc.data_path
                        not in leg_paths
                    ):
                        continue

                    indices_to_remove = []

                    for index, key in enumerate(
                        fc.keyframe_points
                    ):

                        frame = key.co.x

                        if (
                            start_frame
                            <=
                            frame
                            <=
                            end_frame
                        ):

                            indices_to_remove.append(
                                index
                            )

                    # Remove from the end so indices stay valid.
                    for index in reversed(
                        indices_to_remove
                    ):

                        if (
                            index
                            <
                            len(
                                fc.keyframe_points
                            )
                        ):

                            fc.keyframe_points.remove(
                                fc.keyframe_points[
                                    index
                                ]
                            )

                            removed += 1

                    fc.update()

    try:
        action.update()
    except AttributeError:
        pass

    print(
        f"[Spider Walker] "
        f"Removed {removed} leg keyframes "
        f"from {start_frame} to {end_frame}"
    )

    return removed


# ============================================================
# FRAME CALCULATION
# ============================================================

def step_frames(
    start,
    end
):

    start = int(
        round(start)
    )

    end = int(
        round(end)
    )

    if end <= start:

        end = start + 1

    duration = (
        end - start
    )

    # The apex occurs at approximately 2/3
    # of the total step duration.
    apex = int(
        round(
            start
            +
            duration
            *
            (2.0 / 3.0)
        )
    )

    apex = max(
        start + 1,
        apex
    )

    apex = min(
        end - 1,
        apex
    )

    return (
        start,
        apex,
        end
    )


# ============================================================
# BAKE EVENT
# ============================================================

def make_bake_event(
    leg,
    start_frame,
    end_frame
):

    return {

        "bone":
            leg["name"],

        "start":
            leg["start"].copy(),

        "target":
            leg["target"].copy(),

        "target_normal":
            leg.get(
                "target_normal",
                Vector((0.0, 0.0, 1.0))
            ).copy(),

        "start_frame":
            start_frame,

        "end_frame":
            end_frame,
    }


# ============================================================
# BAKE SIMULATION
# ============================================================

def simulate_bake(
    scene,
    armature,
    body,
    settings,
    leg_names,
    start_frame,
    end_frame
):

    fps = (
        scene.render.fps
        /
        scene.render.fps_base
    )

    offsets = calculate_rest_offsets(
        armature,
        settings.body_bone,
        leg_names
    )

    runtime["collision_local_down"] = calculate_initial_collision_down(
        offsets
    )

    # Evaluate the actual pose at the beginning
    # of the bake range.
    scene.frame_set(
        start_frame
    )

    bpy.context.view_layer.update()

    (
        initial_positions,
        initial_normals
    ) = capture_leg_positions(
        scene,
        armature,
        body,
        leg_names,
        settings
    )

    legs = create_leg_states(
        armature,
        offsets,
        leg_names,
        initial_positions,
        initial_normals
    )

    events = []

    gait_index = 0

    last_step_frame = -999999

    previous_body_position = None

    velocity = Vector(
        (0.0, 0.0, 0.0)
    )

    step_cooldown_frames = max(
        1,
        round(
            STEP_COOLDOWN * fps
        )
    )

    for frame in range(
        start_frame,
        end_frame + 1
    ):

        scene.frame_set(
            frame
        )

        bpy.context.view_layer.update()

        # ----------------------------------------------------
        # Body velocity
        # ----------------------------------------------------

        current_body_position = (
            get_world_position(
                armature,
                body
            )
        )

        if (
            previous_body_position
            is not None
        ):

            delta = (
                current_body_position
                -
                previous_body_position
            )

            velocity = (
                delta
                *
                fps
            )

        previous_body_position = (
            current_body_position.copy()
        )

        # ----------------------------------------------------
        # Finish existing steps
        # ----------------------------------------------------

        for leg in legs:

            if not leg["stepping"]:
                continue

            elapsed_frames = (
                frame
                -
                leg["step_start_frame"]
            )

            elapsed_seconds = (
                elapsed_frames
                /
                fps
            )

            if (
                elapsed_seconds
                >=
                leg["step_duration"]
            ):

                leg["planted"] = (
                    leg["target"].copy()
                )

                leg["normal"] = (
                    leg.get("target_normal", Vector((0.0, 0.0, 1.0))).copy()
                )

                leg["stepping"] = False

        # ----------------------------------------------------
        # Limit simultaneous steps
        # ----------------------------------------------------

        active_steps = sum(
            1
            for leg in legs
            if leg["stepping"]
        )

        if (
            active_steps
            >=
            min(
                MAX_SIMULTANEOUS_STEPS,
                len(legs)
            )
        ):
            continue

        if (
            frame
            -
            last_step_frame
            <
            step_cooldown_frames
        ):
            continue

        # ----------------------------------------------------
        # Evaluate exactly one leg in the gait queue.
        # ----------------------------------------------------

        count = len(legs)

        if count == 0:
            continue

        sequence_index = gait_index % count
        gait_index = (gait_index + 1) % count

        leg = legs[sequence_index]

        if leg["stepping"]:
            continue

        target, target_normal = get_predicted_surface_target(
            scene,
            armature,
            body,
            leg["offset"],
            velocity,
            settings
        )

        if settings.collision_collection is not None:
            update_support_direction([target_normal])

        delta = target - leg["planted"]
        distance = delta.length

        # The leg has now consumed its queue turn. If it does not
        # need a step, do not rescan it until the next full cycle.
        if distance < settings.step_distance:
            continue

        duration = calculate_step_duration(
            distance,
            settings
        )

        leg["start"] = leg["planted"].copy()
        leg["target"] = target.copy()
        leg["target_normal"] = target_normal.copy()
        leg["step_start_frame"] = frame
        leg["step_duration"] = duration
        leg["stepping"] = True

        end_float = frame + duration * fps

        f0, f1, f2 = step_frames(
            frame,
            end_float
        )

        if (
            f0 >= start_frame
            and
            f2 <= end_frame
        ):

            events.append(
                make_bake_event(
                    leg,
                    f0,
                    f2
                )
            )

        last_step_frame = frame

    return events


# ============================================================
# INSERT WORLD LOCATION KEY
# ============================================================

def insert_world_location_key(
    armature,
    bone,
    position,
    frame
):

    set_world_position(
        armature,
        bone,
        position
    )

    bone.keyframe_insert(
        data_path="location",
        frame=int(
            round(frame)
        ),
        group="Spider Legs"
    )


# ============================================================
# WRITE BAKE
# ============================================================

def write_bake(
    armature,
    settings,
    events
):

    action = get_current_action(
        armature
    )

    for event in events:

        bone = (
            armature.pose.bones.get(
                event["bone"]
            )
        )

        if bone is None:
            continue

        f0, f1, f2 = step_frames(
            event["start_frame"],
            event["end_frame"]
        )

        start = (
            event["start"].copy()
        )

        target = (
            event["target"].copy()
        )

        # ----------------------------------------------------
        # Start
        # ----------------------------------------------------

        insert_world_location_key(
            armature,
            bone,
            start,
            f0
        )

        # ----------------------------------------------------
        # Apex
        # ----------------------------------------------------

        apex = (
            start.lerp(
                target,
                0.5
            )
        )

        normal = event.get(
            "target_normal",
            Vector((0.0, 0.0, 1.0))
        )

        if normal.length > 1e-8:
            normal = normal.normalized()

        apex += (
            normal
            *
            settings.step_height
        )

        insert_world_location_key(
            armature,
            bone,
            apex,
            f1
        )

        # ----------------------------------------------------
        # Landing
        # ----------------------------------------------------

        insert_world_location_key(
            armature,
            bone,
            target,
            f2
        )

    return action


# ============================================================
# RESET PARAMETERS
# ============================================================

class SPIDER_OT_reset_parameters(
    Operator
):

    bl_idname = "spider.reset_parameters"

    bl_label = "Reset Parameters"

    bl_description = (
        "Reset walker parameters to their default values"
    )

    def execute(
        self,
        context
    ):

        settings = (
            context.scene.spider_walker
        )

        # Keep rig, center bone and leg list.
        # Reset configurable walker parameters.
        settings.enabled = True

        # Surface
        settings.collision_collection = None
        settings.surface_probe_distance = 5.00
        settings.surface_offset = 0.00

        # Main step controls
        settings.step_distance = 1.00
        settings.step_speed = 10.00
        settings.step_height = 0.30
        settings.prediction_distance = 2.00

        # Advanced step timing
        settings.min_step_time = 0.10
        settings.max_step_time = 0.50

        # Advanced adaptive step distance
        settings.speed_step_scale = 0.35
        settings.max_speed_step_distance = 1.30

        # Bake range
        settings.bake_start = 1
        settings.bake_end = 250

        # Keep the advanced section collapsed.
        settings.show_advanced_settings = False

        # Force runtime rebuild with reset values.
        runtime["configuration_signature"] = None
        runtime["configuration_error"] = ""

        self.report(
            {'INFO'},
            "Walker parameters reset to defaults"
        )

        return {'FINISHED'}


# ============================================================
# BAKE OPERATOR
# ============================================================

class SPIDER_OT_bake(
    Operator
):

    bl_idname = "spider.bake_legs"

    bl_label = "Bake Legs"

    bl_description = (
        "Bake selected procedural legs "
        "into the current Action"
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        settings = (
            scene.spider_walker
        )

        try:

            (
                armature,
                body,
                leg_names
            ) = validate_configuration(
                settings
            )

        except Exception as e:

            self.report(
                {'ERROR'},
                str(e)
            )

            return {'CANCELLED'}

        start_frame = int(
            settings.bake_start
        )

        end_frame = int(
            settings.bake_end
        )

        if end_frame <= start_frame:

            self.report(
                {'ERROR'},
                "Bake range must contain at least one frame"
            )

            return {'CANCELLED'}

        if scene.render.fps <= 0 or scene.render.fps_base <= 0:

            self.report(
                {'ERROR'},
                "Scene FPS settings are invalid"
            )

            return {'CANCELLED'}

        original_frame = (
            scene.frame_current
        )

        original_enabled = (
            settings.enabled
        )

        try:

            # Disable realtime during baking.
            settings.enabled = False

            # Remove only configured leg keys.
            clear_leg_keys(
                armature,
                leg_names,
                start_frame,
                end_frame
            )

            # Generate procedural step events.
            events = simulate_bake(
                scene,
                armature,
                body,
                settings,
                leg_names,
                start_frame,
                end_frame
            )

            # Write into the current Action.
            action = write_bake(
                armature,
                settings,
                events
            )

            scene.frame_set(
                original_frame
            )

            bpy.context.view_layer.update()

            # Keep procedural mode disabled after bake.
            settings.enabled = False

            print()
            print(
                "=========================================="
            )
            print(
                " SPIDER WALKER BAKE"
            )
            print(
                "=========================================="
            )
            print(
                f"Armature : {armature.name}"
            )
            print(
                f"Center   : {settings.body_bone}"
            )
            print(
                f"Legs     : {len(leg_names)}"
            )
            print(
                f"Range    : {start_frame} - {end_frame}"
            )
            print(
                f"Steps    : {len(events)}"
            )
            print(
                f"Action   : {action.name}"
            )
            print(
                "Body     : untouched"
            )
            print(
                "Keys     : 3 per step"
            )
            print(
                "Apex     : 2/3 of step duration"
            )
            print(
                "=========================================="
            )

            self.report(
                {'INFO'},
                (
                    f"Baked {len(events)} steps "
                    f"for {len(leg_names)} legs"
                )
            )

        except Exception as e:

            scene.frame_set(
                original_frame
            )

            settings.enabled = (
                original_enabled
            )

            print(
                "[Spider Walker Bake Error]",
                e
            )

            self.report(
                {'ERROR'},
                str(e)
            )

            return {'CANCELLED'}

        return {'FINISHED'}


# ============================================================
# CLEAR OPERATOR
# ============================================================

class SPIDER_OT_clear_range(
    Operator
):

    bl_idname = "spider.clear_baked_range"

    bl_label = "Clear Baked Range"

    bl_description = (
        "Remove keys of enabled leg bones "
        "inside the selected frame range"
    )

    def execute(
        self,
        context
    ):

        settings = (
            context.scene.spider_walker
        )

        try:

            (
                armature,
                body,
                leg_names
            ) = validate_configuration(
                settings
            )

            if (
                armature.animation_data is None
                or
                armature.animation_data.action is None
            ):
                self.report(
                    {'INFO'},
                    "No active Action; nothing to clear"
                )
                return {'FINISHED'}

            removed = clear_leg_keys(
                armature,
                leg_names,
                settings.bake_start,
                settings.bake_end
            )

        except Exception as e:

            self.report(
                {'ERROR'},
                str(e)
            )

            return {'CANCELLED'}

        self.report(
            {'INFO'},
            (
                f"Removed {removed} keys"
            )
        )

        return {'FINISHED'}


# ============================================================
# SET ACTIVE ARMATURE
# ============================================================

class SPIDER_OT_use_active_armature(
    Operator
):

    bl_idname = "spider.use_active_armature"

    bl_label = "Use Active Armature"

    bl_description = (
        "Use the currently selected armature"
    )

    def execute(
        self,
        context
    ):

        obj = context.object

        if (
            obj is None
            or
            obj.type != 'ARMATURE'
        ):

            self.report(
                {'ERROR'},
                "Select an armature"
            )

            return {'CANCELLED'}

        settings = (
            context.scene.spider_walker
        )

        settings.rig = obj

        # Automatically use the active bone if it
        # belongs to the selected armature.
        active_bone = (
            obj.data.bones.active
        )

        if active_bone is not None:

            if (
                not settings.body_bone
                or
                settings.body_bone
                not in obj.data.bones
            ):

                settings.body_bone = (
                    active_bone.name
                )

        return {'FINISHED'}


# ============================================================
# ADD SELECTED BONES
# ============================================================

class SPIDER_OT_add_selected(
    Operator
):

    bl_idname = "spider.add_selected_legs"

    bl_label = "Add Selected Bones"

    bl_description = (
        "Add selected pose bones to the leg list"
    )

    def execute(self, context):

        settings = context.scene.spider_walker

        armature = settings.rig

        # --------------------------------------------------------
        # Get the configured armature.
        # --------------------------------------------------------

        if armature is None:

            if (
                context.object is not None
                and
                context.object.type == 'ARMATURE'
            ):
                armature = context.object
                settings.rig = armature

            else:
                self.report(
                    {'ERROR'},
                    "No armature selected"
                )
                return {'CANCELLED'}

        # --------------------------------------------------------
        # Make sure we are working with the active armature.
        # --------------------------------------------------------

        if context.object != armature:

            self.report(
                {'ERROR'},
                "The configured armature must be the active object"
            )

            return {'CANCELLED'}

        # --------------------------------------------------------
        # Get selected pose bones.
        # Blender 5.2 compatible.
        # --------------------------------------------------------

        selected = bpy.context.selected_pose_bones

        if selected is None or len(selected) == 0:

            self.report(
                {'WARNING'},
                "No bones selected in Pose Mode"
            )

            return {'CANCELLED'}

        # --------------------------------------------------------
        # Existing leg names.
        # --------------------------------------------------------

        existing = {
            item.bone_name
            for item in settings.legs
        }

        body_name = settings.body_bone

        added = 0

        # --------------------------------------------------------
        # Add selected bones.
        # --------------------------------------------------------

        for pose_bone in selected:

            name = pose_bone.name

            # Never add the center bone as a leg.
            if name == body_name:
                continue

            # Don't add duplicates.
            if name in existing:
                continue

            item = settings.legs.add()

            item.bone_name = name
            item.enabled = True

            existing.add(name)

            added += 1

        # --------------------------------------------------------
        # Force runtime rebuild.
        # --------------------------------------------------------

        runtime["configuration_signature"] = None

        self.report(
            {'INFO'},
            f"Added {added} bone(s)"
        )

        return {'FINISHED'}

# ============================================================
# REMOVE LEG
# ============================================================

class SPIDER_OT_remove_leg(
    Operator
):

    bl_idname = "spider.remove_leg"

    bl_label = "Remove"

    bl_description = (
        "Remove selected leg from the list"
    )

    def execute(
        self,
        context
    ):

        settings = (
            context.scene.spider_walker
        )

        index = (
            settings.active_leg_index
        )

        if (
            index < 0
            or
            index >= len(settings.legs)
        ):

            return {'CANCELLED'}

        settings.legs.remove(
            index
        )

        settings.active_leg_index = min(
            index,
            len(settings.legs) - 1
        )

        return {'FINISHED'}


# ============================================================
# CLEAR LEG LIST
# ============================================================

class SPIDER_OT_clear_legs(
    Operator
):

    bl_idname = "spider.clear_legs"

    bl_label = "Clear List"

    bl_description = (
        "Remove all configured leg bones"
    )

    def execute(
        self,
        context
    ):

        settings = (
            context.scene.spider_walker
        )

        settings.legs.clear()

        settings.active_leg_index = 0

        return {'FINISHED'}


# ============================================================
# MOVE LEG UP
# ============================================================

class SPIDER_OT_move_leg_up(
    Operator
):

    bl_idname = "spider.move_leg_up"

    bl_label = "Move Up"

    def execute(
        self,
        context
    ):

        settings = (
            context.scene.spider_walker
        )

        index = (
            settings.active_leg_index
        )

        if index <= 0:
            return {'CANCELLED'}

        settings.legs.move(
            index,
            index - 1
        )

        settings.active_leg_index = (
            index - 1
        )

        return {'FINISHED'}


# ============================================================
# MOVE LEG DOWN
# ============================================================

class SPIDER_OT_move_leg_down(
    Operator
):

    bl_idname = "spider.move_leg_down"

    bl_label = "Move Down"

    def execute(
        self,
        context
    ):

        settings = (
            context.scene.spider_walker
        )

        index = (
            settings.active_leg_index
        )

        if (
            index < 0
            or
            index >= len(settings.legs) - 1
        ):

            return {'CANCELLED'}

        settings.legs.move(
            index,
            index + 1
        )

        settings.active_leg_index = (
            index + 1
        )

        return {'FINISHED'}


# ============================================================
# UI LIST
# ============================================================

class SPIDER_UL_legs(
    UIList
):

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index
    ):

        row = layout.row(
            align=True
        )

        row.prop(
            item,
            "enabled",
            text=""
        )

        row.label(
            text=item.bone_name,
            icon='BONE_DATA'
        )


# ============================================================
# UI PANEL
# ============================================================

class SPIDER_PT_walker(
    Panel
):

    bl_idname = "SPIDER_PT_walker"

    bl_label = "Spider Walker"

    bl_space_type = 'PROPERTIES'

    bl_region_type = 'WINDOW'

    bl_context = 'data'

    @classmethod
    def poll(
        cls,
        context
    ):

        return (
            context.object is not None
            and
            context.object.type == 'ARMATURE'
        )

    def draw(
        self,
        context
    ):

        layout = self.layout

        settings = (
            context.scene.spider_walker
        )

        active_armature = (
            context.object
            if context.object
            and context.object.type == 'ARMATURE'
            else None
        )

        # ====================================================
        # RIG
        # ====================================================

        box = layout.box()

        box.label(
            text="Rig Setup",
            icon='ARMATURE_DATA'
        )

        row = box.row(
            align=True
        )

        row.prop(
            settings,
            "rig",
            text="Armature"
        )

        if (
            active_armature is not None
            and
            settings.rig != active_armature
        ):

            row = box.row()

            row.operator(
                "spider.use_active_armature",
                text="Use Active Armature",
                icon='EYEDROPPER'
            )

        if settings.rig is not None:

            rig = settings.rig

            if rig.type == 'ARMATURE':

                box.prop_search(
                    settings,
                    "body_bone",
                    rig.data,
                    "bones",
                    text="Center Bone"
                )

        # ====================================================
        # LEGS
        # ====================================================

        box = layout.box()

        row = box.row()

        row.label(
            text=f"Legs ({len(settings.legs)})",
            icon='BONE_DATA'
        )

        row = box.row()

        row.template_list(
            "SPIDER_UL_legs",
            "",
            settings,
            "legs",
            settings,
            "active_leg_index",
            rows=6
        )

        col = row.column(
            align=True
        )

        col.operator(
            "spider.move_leg_up",
            text="",
            icon='TRIA_UP'
        )

        col.operator(
            "spider.move_leg_down",
            text="",
            icon='TRIA_DOWN'
        )

        col.separator()

        col.operator(
            "spider.remove_leg",
            text="",
            icon='X'
        )

        row = box.row(
            align=True
        )

        row.operator(
            "spider.add_selected_legs",
            text="Add Selected Bones",
            icon='ADD'
        )

        row.operator(
            "spider.clear_legs",
            text="Clear",
            icon='TRASH'
        )

        box.label(
            text="List order = gait order",
            icon='INFO'
        )

        # ====================================================
        # PROCEDURAL
        # ====================================================

        box = layout.box()

        row = box.row()

        row.prop(
            settings,
            "enabled",
            text="Enable procedural walk",
            toggle=True
        )

        # ====================================================
        # ====================================================
        # SURFACE
        # ====================================================

        box = layout.box()

        box.label(
            text="Surface Collision"
        )

        box.prop(
            settings,
            "collision_collection",
            text="Collection"
        )

        box.prop(
            settings,
            "surface_probe_distance",
            text="Probe Distance"
        )

        if settings.collision_collection is not None:
            mesh_count = count_collision_meshes(
                settings.collision_collection
            )

            if mesh_count == 0:
                warning = box.row()
                warning.alert = True
                warning.label(
                    text="Collection has no visible mesh objects",
                    icon='ERROR'
                )

        # ====================================================
        # STEP
        # ====================================================

        box = layout.box()

        step_header = box.row(align=True)

        step_header.label(
            text="Step",
            icon='CONSTRAINT_BONE'
        )

        reset_row = step_header.row(align=True)
        reset_row.alignment = 'RIGHT'

        reset_row.operator(
            "spider.reset_parameters",
            text="",
            icon='FILE_REFRESH'
        )

        box.prop(
            settings,
            "step_distance",
            text="Distance"
        )

        box.prop(
            settings,
            "step_speed",
            text="Speed"
        )

        box.prop(
            settings,
            "step_height",
            text="Height"
        )

        box.prop(
            settings,
            "prediction_distance",
            text="Prediction Distance"
        )

        # ====================================================
        # CONFIGURATION ERRORS
        # ====================================================

        if runtime.get("configuration_error"):
            status = layout.box()
            status.alert = True
            status.label(
                text=runtime["configuration_error"],
                icon='ERROR'
            )

        # ====================================================
        # ADVANCED SETTINGS
        # ====================================================

        advanced_header = layout.row(align=True)

        advanced_header.prop(
            settings,
            "show_advanced_settings",
            text="Advanced Settings",
            icon=(
                'TRIA_DOWN'
                if settings.show_advanced_settings
                else 'TRIA_RIGHT'
            ),
            emboss=True
        )

        if settings.show_advanced_settings:

            box = layout.box()

            box.label(
                text="Step Timing",
                icon='TIME'
            )

            box.prop(
                settings,
                "min_step_time",
                text="Min Step Time"
            )

            box.prop(
                settings,
                "max_step_time",
                text="Max Step Time"
            )

            box.separator()

            box.label(
                text="Surface",
                icon='SNAP_ON'
            )

            box.prop(
                settings,
                "surface_offset",
                text="Surface Offset"
            )

            box.separator()

            box.label(
                text="Adaptive Step Distance",
                icon='FORWARD'
            )

            box.prop(
                settings,
                "speed_step_scale",
                text="Speed Step Scale"
            )

            box.prop(
                settings,
                "max_speed_step_distance",
                text="Max Speed Step Distance"
            )

        # ====================================================
        # BAKE
        # ====================================================

        box = layout.box()

        box.label(
            text="Bake Legs",
            icon='ACTION'
        )

        row = box.row(
            align=True
        )

        row.prop(
            settings,
            "bake_start",
            text="Start"
        )

        row.prop(
            settings,
            "bake_end",
            text="End"
        )

        row = box.row()

        row.scale_y = 1.5

        row.operator(
            "spider.bake_legs",
            text="BAKE LEGS",
            icon='ACTION'
        )

        row = box.row()

        row.operator(
            "spider.clear_baked_range",
            text="Clear Baked Range",
            icon='TRASH'
        )


# ============================================================
# REGISTRATION
# ============================================================

CLASSES = (
    SpiderLegItem,
    SpiderWalkerSettings,
    SPIDER_UL_legs,
    SPIDER_OT_reset_parameters,
    SPIDER_OT_bake,
    SPIDER_OT_clear_range,
    SPIDER_OT_use_active_armature,
    SPIDER_OT_add_selected,
    SPIDER_OT_remove_leg,
    SPIDER_OT_clear_legs,
    SPIDER_OT_move_leg_up,
    SPIDER_OT_move_leg_down,
    SPIDER_PT_walker,
)


def unregister_classes():
    """Unregister all addon classes, ignoring ones not currently registered."""
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass


def set_default_rig(settings):
    """Convenience default: point the addon at the active armature on enable."""
    active_object = bpy.context.object

    if settings.rig is not None:
        return

    if active_object is None or active_object.type != 'ARMATURE':
        return

    settings.rig = active_object

    if settings.body_bone not in active_object.data.bones:
        active_bone = active_object.data.bones.active

        if active_bone is not None:
            settings.body_bone = active_bone.name


def register():
    global TIMER_TOKEN

    # A fresh token invalidates any timer instance left over from a
    # previous enable/disable cycle within the same Blender session.
    TIMER_TOKEN = uuid.uuid4().hex

    runtime.clear()
    runtime["configuration_signature"] = None
    runtime["configured"] = False
    runtime["configuration_error"] = ""

    for cls in CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Scene.spider_walker = PointerProperty(type=SpiderWalkerSettings)

    if _spider_walker_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_spider_walker_load_post)

    # Best-effort convenience defaults; never block registration on this.
    try:
        scene = bpy.context.scene
        if scene is not None:
            set_default_rig(scene.spider_walker)
    except Exception as exc:
        print(f"[Spider Walker] Skipped default setup: {exc}")

    # The timer only runs while "Procedural Walk" is checked, so its
    # start/stop is driven from here and from the checkbox itself
    # (SpiderWalkerSettings.enabled's update= callback), not left running
    # unconditionally in the background.
    sync_timer_with_settings()

    print("[Spider Walker] Addon registered.")


def unregister():
    if _spider_walker_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_spider_walker_load_post)

    stop_timer()
    runtime.clear()

    if hasattr(bpy.types.Scene, "spider_walker"):
        del bpy.types.Scene.spider_walker

    unregister_classes()

    print("[Spider Walker] Addon unregistered.")


if __name__ == "__main__":
    register()
