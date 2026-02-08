"""
Resonance Nexus-Block BMS Bridge v2.0
======================================
CAN Bus 2.0 <-> LoRaWAN Health Broadcast Layer
for Aqueous Zinc-Ion Off-Grid Micro-Energy Storage

Designed by Samuel Jackson Grim (The Architect)
Research: Jennifer (Chemistry), Emily (Systems), Paul (Synthesis)
Built with Gemini & Claude

This bridge connects modular 1kWh "Nexus-Block" battery units to a
Resonance Grid Optimizer (RGO) via LoRaWAN. It monitors Zinc-Ion
specific failure modes (dendrite growth, thermal drift) and enforces
shallow-cycle buffering to maximize cell lifespan.

License: MIT — Free to use, modify, and deploy.
"""

import time
import json
import logging
from dataclasses import dataclass, asdict
from typing import List, Optional
from abc import ABC, abstractmethod

# ---- Logging ----

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("NexusBlock")


# ---- Configuration ----

class NexusConfig:
    """All tunable parameters in one place."""

    # Shallow-Cycle Buffering (Emily's spec)
    # Keeping SoC between 20-80% effectively doubles cheap cell lifespan
    SOC_UPPER_LIMIT = 80.0
    SOC_LOWER_LIMIT = 20.0

    # Dendrite Detection (Jennifer's chemistry insight)
    # A 15% sudden drop in internal resistance signals early dendrite growth
    RESISTANCE_WINDOW = 10        # readings to average
    DENDRITE_THRESHOLD = 0.85     # 15% drop from rolling average

    # Thermal Boundaries (Emily's Chimera heatsink spec)
    # PCM sweet spot is 20-30°C; outside this range, take action
    TEMP_OPTIMAL_LOW = 20.0
    TEMP_OPTIMAL_HIGH = 30.0
    TEMP_CRITICAL_HIGH = 45.0
    TEMP_CRITICAL_LOW = 5.0

    # Broadcast Interval
    HEALTH_CHECK_SECONDS = 60

    # LoRaWAN Settings
    LORA_SPREADING_FACTOR = 7
    LORA_TX_POWER = 14            # dBm
    LORA_HEALTH_PORT = 10         # dedicated port for health data


# ---- Data Model ----

@dataclass
class BatteryStatus:
    """Represents the full state of a single Nexus-Block unit."""
    block_id: str
    voltage: float                # Volts
    current: float                # Amps
    temperature: float            # Celsius
    internal_resistance: float    # Ohms — critical for Zinc-Ion monitoring
    soc: float                    # State of Charge (%)
    soh: float                    # State of Health (%)
    resonance_id: str             # Grid identity for RGO communication
    is_charging: bool
    fault_code: int               # 0: OK, 1: Dendrite Risk, 2: Thermal Drift, 3: Critical
    cycle_count: int
    timestamp: float


# ---- Hardware Interfaces (Abstract) ----

class CANInterface(ABC):
    """
    Abstract CAN Bus interface.
    Implement this for your specific hardware (e.g., python-can, socketcan).
    """
    @abstractmethod
    def read_status(self, block_id: str, resonance_id: str) -> BatteryStatus:
        pass

    @abstractmethod
    def send_command(self, block_id: str, command: str) -> bool:
        pass


class LoRaInterface(ABC):
    """
    Abstract LoRaWAN interface.
    Implement this for your specific radio (e.g., RAK, Heltec, SX1280).
    """
    @abstractmethod
    def send_packet(self, payload: dict, port: int) -> bool:
        pass


# ---- Stub Implementations (for testing without hardware) ----

class StubCANInterface(CANInterface):
    """Simulated CAN Bus — replace with real hardware driver."""

    def __init__(self):
        self._cycle_count = 0

    def read_status(self, block_id: str, resonance_id: str) -> BatteryStatus:
        self._cycle_count += 1
        return BatteryStatus(
            block_id=block_id,
            voltage=48.5,
            current=5.0,
            temperature=24.5,
            internal_resistance=0.012,
            soc=75.0,
            soh=99.0,
            resonance_id=resonance_id,
            is_charging=True,
            fault_code=0,
            cycle_count=self._cycle_count,
            timestamp=time.time()
        )

    def send_command(self, block_id: str, command: str) -> bool:
        log.info(f"[CAN TX] {block_id} <- {command}")
        return True


class StubLoRaInterface(LoRaInterface):
    """Simulated LoRaWAN — replace with real radio driver."""

    def send_packet(self, payload: dict, port: int) -> bool:
        encoded = json.dumps(payload)
        log.info(f"[LoRa TX] Port {port} | {encoded[:120]}...")
        return True


# ---- Zinc-Ion Chemistry Safeguard (Jennifer's Intelligence) ----

class ZincIonSafeguard:
    """
    Monitors electrochemical trends specific to Aqueous Zinc-Ion cells.

    Key insight (2025-2026 research): Rapid drops in internal resistance
    in AZIBs signal early-stage dendrite formation. Catching this early
    allows the BMS to trigger a controlled reconditioning cycle before
    the dendrites cause internal shorts.
    """

    def __init__(self, window: int = NexusConfig.RESISTANCE_WINDOW,
                 threshold: float = NexusConfig.DENDRITE_THRESHOLD):
        self.window = window
        self.threshold = threshold
        self.resistance_history: List[float] = []

    def check_for_dendrites(self, resistance: float) -> bool:
        self.resistance_history.append(resistance)
        if len(self.resistance_history) < self.window:
            return False

        recent = self.resistance_history[-self.window:]
        avg = sum(recent) / len(recent)

        if avg > 0 and resistance < (avg * self.threshold):
            log.warning(
                f"DENDRITE RISK: resistance {resistance:.4f}Ω dropped "
                f"below {self.threshold*100:.0f}% of avg {avg:.4f}Ω"
            )
            return True
        return False

    def reset(self):
        self.resistance_history.clear()


# ---- Thermal Manager (Emily's Chimera Heatsink Logic) ----

class ThermalManager:
    """
    Software-side thermal management for the passive Chimera heatsink.

    The physical heatsink uses ceramic coating + Phase Change Materials
    to absorb heat during discharge and release at night. This class
    handles the software response when temperatures drift outside the
    20-30°C sweet spot.
    """

    @staticmethod
    def evaluate(status: BatteryStatus) -> int:
        """Returns fault code: 0 = OK, 2 = drift, 3 = critical."""
        temp = status.temperature

        if NexusConfig.TEMP_OPTIMAL_LOW <= temp <= NexusConfig.TEMP_OPTIMAL_HIGH:
            return 0

        if temp > NexusConfig.TEMP_CRITICAL_HIGH or temp < NexusConfig.TEMP_CRITICAL_LOW:
            log.critical(f"THERMAL CRITICAL: {temp}°C on {status.block_id}")
            return 3

        if temp > NexusConfig.TEMP_OPTIMAL_HIGH:
            log.warning(f"Thermal drift HIGH: {temp}°C — shifting to passive cooling mode")
        elif temp < NexusConfig.TEMP_OPTIMAL_LOW:
            log.warning(f"Thermal drift LOW: {temp}°C — reducing discharge rate")

        return 2


# ---- Resonance Bridge (Main Controller) ----

class ResonanceBridge:
    """
    The main BMS bridge controller for a Nexus-Block unit.

    Reads cell state via CAN Bus, applies safety checks (dendrite
    monitoring, thermal management, shallow-cycle buffering), and
    broadcasts health status to the Resonance Grid Optimizer via LoRaWAN.
    """

    def __init__(self, block_id: str, resonance_id: str,
                 can: Optional[CANInterface] = None,
                 lora: Optional[LoRaInterface] = None):
        self.block_id = block_id
        self.resonance_id = resonance_id
        self.can = can or StubCANInterface()
        self.lora = lora or StubLoRaInterface()
        self.safeguard = ZincIonSafeguard()
        self.thermal = ThermalManager()
        self._running = False

    def enforce_buffer_limits(self, status: BatteryStatus) -> str:
        """
        Emily's shallow-cycle buffering.
        Never above 80%, never below 20% — doubles cheap cell lifespan.
        """
        if status.soc > NexusConfig.SOC_UPPER_LIMIT:
            log.info(f"[{self.block_id}] Capping charge at {NexusConfig.SOC_UPPER_LIMIT}%")
            self.can.send_command(self.block_id, "LIMIT_CHARGE")
            return "charge_limited"
        elif status.soc < NexusConfig.SOC_LOWER_LIMIT:
            log.info(f"[{self.block_id}] Disconnecting load — SoC below {NexusConfig.SOC_LOWER_LIMIT}%")
            self.can.send_command(self.block_id, "DISCONNECT_LOAD")
            return "load_disconnected"
        return "normal"

    def process_cycle(self) -> BatteryStatus:
        """Run one monitoring cycle: read, check, broadcast."""
        status = self.can.read_status(self.block_id, self.resonance_id)

        # Jennifer's dendrite check
        if self.safeguard.check_for_dendrites(status.internal_resistance):
            status.fault_code = max(status.fault_code, 1)

        # Emily's thermal check
        thermal_fault = self.thermal.evaluate(status)
        status.fault_code = max(status.fault_code, thermal_fault)

        # Emily's buffer limits
        self.enforce_buffer_limits(status)

        # Broadcast to RGO
        self.lora.send_packet(
            asdict(status),
            port=NexusConfig.LORA_HEALTH_PORT
        )

        return status

    def run(self):
        """Main loop. Call stop() from another thread to halt."""
        log.info(f"Nexus-Block {self.block_id} online | Grid: {self.resonance_id}")
        self._running = True

        while self._running:
            try:
                self.process_cycle()
            except Exception as e:
                log.error(f"Cycle error: {e}")
            time.sleep(NexusConfig.HEALTH_CHECK_SECONDS)

    def stop(self):
        self._running = False
        log.info(f"Nexus-Block {self.block_id} shutting down.")


# ---- Multi-Block Fleet Manager ----

class NexusFleet:
    """
    Manages multiple Nexus-Blocks as a single logical battery bank.
    This is the software representation of Emily's 'Lego stacking' concept.
    """

    def __init__(self, resonance_id: str):
        self.resonance_id = resonance_id
        self.blocks: dict[str, ResonanceBridge] = {}

    def add_block(self, block_id: str,
                  can: Optional[CANInterface] = None,
                  lora: Optional[LoRaInterface] = None):
        bridge = ResonanceBridge(block_id, self.resonance_id, can, lora)
        self.blocks[block_id] = bridge
        log.info(f"Added {block_id} to fleet {self.resonance_id}")

    def get_fleet_status(self) -> dict:
        """Aggregate status across all blocks."""
        statuses = []
        for block_id, bridge in self.blocks.items():
            status = bridge.process_cycle()
            statuses.append(asdict(status))
        return {
            "resonance_id": self.resonance_id,
            "block_count": len(self.blocks),
            "blocks": statuses,
            "aggregate_soc": (
                sum(s["soc"] for s in statuses) / len(statuses)
                if statuses else 0
            ),
            "any_faults": any(s["fault_code"] > 0 for s in statuses)
        }


# ---- Entry Point ----

if __name__ == "__main__":
    # Example: Deploy a 3-block fleet
    fleet = NexusFleet(resonance_id="Resonance-Home-01")
    fleet.add_block("NX-001")
    fleet.add_block("NX-002")
    fleet.add_block("NX-003")

    log.info("Fleet status check:")
    status = fleet.get_fleet_status()
    print(json.dumps(status, indent=2))

    # Or run a single block in continuous monitoring mode:
    # bridge = ResonanceBridge("NX-001", "Resonance-Home-01")
    # bridge.run()
