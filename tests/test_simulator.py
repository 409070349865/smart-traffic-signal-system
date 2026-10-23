"""
Automated Unit Tests for Traffic Simulator & SignalSync Algorithm Pipeline.
"""
import pytest

from app.traffic.simulator import Direction, SignalPhase, TrafficSimulator


def test_simulator_initial_state():
    sim = TrafficSimulator()
    state = sim.state
    assert state.cycle_number == 1
    assert state.active_direction in [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]
    assert state.current_phase == SignalPhase.GREEN
    assert state.cmu_status == "HEALTHY [ZERO CONFLICTS]"


def test_weather_clearance_scaling():
    sim = TrafficSimulator()
    # Default dry: yellow=3.0, all_red=2.0
    assert sim.state.effective_yellow == 3.0
    assert sim.state.effective_all_red == 2.0

    # Switch to rainy: adds +2.0s
    sim.set_weather("rainy")
    assert sim.state.effective_yellow == 5.0
    assert sim.state.effective_all_red == 4.0


def test_emergency_preemption():
    sim = TrafficSimulator()
    sim.trigger_ambulance(Direction.NORTH)
    assert sim.state.ambulance_active is True
    assert sim.state.ambulance_direction == Direction.NORTH
    assert sim.state.strobe_detected is True


def test_yellow_box_gridlock_toggle():
    sim = TrafficSimulator()
    assert sim.state.yellow_box_blocked is False
    blocked = sim.toggle_gridlock()
    assert blocked is True
    assert sim.state.yellow_box_blocked is True
    # Toggle back
    blocked_again = sim.toggle_gridlock()
    assert blocked_again is False


def test_sensor_fault_watchdog():
    sim = TrafficSimulator()
    sim.set_sensor_fault(Direction.EAST, True)
    assert sim.state.lanes[Direction.EAST].sensor_fault is True
