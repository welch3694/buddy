"""Built-in sample session.yaml for new pulse skills."""

from __future__ import annotations


def render_session_template(skill_name: str) -> str:
    """Return a documented session.yaml template with the skill name filled in."""
    return f"""\
# Pulse session config — edit rules by hand or tune via update_pulse_config
name: {skill_name}

pulse:
  tick_interval_s: 10
  conversation_check_s: 60
  min_speak_interval_s: 45
  mandatory_cue_max_defer_s: 30

init:
  set:
    phase: live
    current_camera: 1
    narrator_muted: false

cameras:
  - {{ id: 1, label: "wide shot" }}
  - {{ id: 2, label: "close-up" }}
  - {{ id: 3, label: "overhead" }}

rules:
  # Mandatory camera-switch cue every 3 minutes
  - id: camera-switch
    when: elapsed_since(last_camera_switch_at) >= 180
    once: false
    set:
      current_camera: "$rotate(cameras)"
      last_camera_switch_at: "$now"
    cue: "Switch to camera {{current_camera}} — {{label}}."
    priority: mandatory

  # Example conversational pulse (no mandatory cue)
  - id: conversation-check
    when: elapsed_since(last_conversation_pulse_at) >= 60
    once: false
    set:
      last_conversation_pulse_at: "$now"
    cue: ""
    priority: conversational

schedule: []
"""
