"""Built-in sample session.yaml for new pulse skills."""

from __future__ import annotations


def render_session_template(skill_name: str) -> str:
    """Return a documented session.yaml template with the skill name filled in."""
    return f"""\
# Pulse session config
# Full reference: buddy_tools/pulse/SESSION_YAML.md
name: {skill_name}

pulse:
  tick_interval_s: 10
  conversation_check_s: 60
  min_speak_interval_s: 45
  mandatory_cue_max_defer_s: 30
  # silence_gated_only: true  # "keep them talking" — suppress reactive speech; pulses only

init:
  set:
    phase: live
    current_camera: 1
    switch_interval_s: 180
    min_switch_interval_s: 60
    tighten_step_s: 5
    narrator_muted: false
    last_camera_switch_at: "$now"
    last_conversation_pulse_at: "$now"

cameras:
  - {{ id: 1, label: "wide shot" }}
  - {{ id: 2, label: "close-up" }}
  - {{ id: 3, label: "overhead" }}

rules:
  - id: camera-switch
    when: elapsed_since(last_camera_switch_at) >= switch_interval_s
    once: false
    set:
      current_camera: "$rotate(cameras)"
      last_camera_switch_at: "$now"
      # switch_interval_s: "$clamp($sub(switch_interval_s, tighten_step_s), min_switch_interval_s)"
    cue: "Switch to camera {{current_camera}} — {{label}}."
    priority: mandatory

  # Example: tighten cadence after 30 minutes
  # - id: tighten-pace
  #   when: session_elapsed >= 1800
  #   once: true
  #   set:
  #     switch_interval_s: 120
  #     last_camera_switch_at: "$now"

# Optional conversational fill uses pulse.conversation_check_s (gates), not a rule here.

schedule: []
"""
