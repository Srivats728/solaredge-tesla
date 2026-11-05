# SolarEdge // Tesla Solar Self-Consumption Optimizer

An ultra-minimalist, autonomous LAN-based solar charging manager for your **Tesla Model 3** powered directly by your **SolarEdge Inverter** over **Modbus TCP**, featuring a single-page pure monochrome dark web dashboard.

Designed specifically for **Level 1 (120V)** charging without requiring a grid consumption meter, maximizing free solar self-consumption while completely preventing grid draw.

---

## What Changed: The Foolproof Revamp

1. **No Developer Portals or Complex Fleet APIs Needed**:
   - Built with pluggable vehicle integrations.
   - **Tessie API**: Connect your Model 3 in 10 seconds using a single API key from `dash.tessie.com/settings/api`. Zero registered domains, EC public keys, or partner tokens required.
   - **Home Assistant**: Direct local LAN connection if you run HA.
   - **Local Simulator**: Built-in mock Model 3 for instant out-of-the-box demo & testing.
   - **Tesla Fleet API**: Optional direct developer connection for advanced users.
2. **Single-Page Tesla Monochrome Dashboard**:
   - Strict Tesla dark aesthetic (`#000000` / `#111111` / `#222222`).
   - Zero emojis, zero bloat, no multi-tab confusion or modal popups.
   - Live telemetry cards: Solar Generation, House Baseline, Net Solar Surplus, Charging Rate, and Battery Level with animated progress bar.
   - 3-button mode selector: `[AUTO (SOLAR)]`, `[FORCE 12A]`, `[STOP]`.
   - Real-time inline connection testers for both SolarEdge Inverter and Vehicle.
3. **Internally Beta-Tested (100% Automated Test Suite)**:
   - Exhaustively tested with 23 automated tests (`pytest tests/`) verifying SunSpec Modbus register decoding, scale factors, Level 1 math clamping (5A-12A), 120s start timer, 180s cloud debounce, and full day-in-the-life lifecycles.

---

## 1-Step Update / Install (Inside Debian 12 / 13 LXC)

### If already installed:
```bash
cd /opt/solaredge-tesla && git pull && systemctl restart solaredge-tesla
```

### Fresh install:
```bash
apt-get update && apt-get install -y git && git clone https://github.com/Srivats728/solaredge-tesla.git /opt/solaredge-tesla && bash /opt/solaredge-tesla/deploy/install_debian.sh
```

The Web Dashboard will be live immediately at:
```
http://<LXC-IP>:8080
```

---

## Charging Logic & Math

$$\text{Available Power for Car} = \text{Solar Generation} - \text{Household Baseline}$$

$$\text{Target Amps} = \left\lfloor \frac{\text{Available Power}}{120\text{ V}} \right\rfloor \quad (\text{clamped between } 5\text{A and } 12\text{A})$$

| Solar Output | Baseline House | Available for Car | Level 1 Current | Effective Car Power |
| :--- | :--- | :--- | :--- | :--- |
| **< 1,100 W** | 600 W | < 500 W | **0 A (Stopped)** | 0 W |
| **1,300 W** | 600 W | 700 W | **5 A** | 600 W |
| **1,500 W** | 600 W | 900 W | **7 A** | 840 W |
| **1,800 W** | 600 W | 1,200 W | **10 A** | 1,200 W |
| **>= 2,100 W** | 600 W | >= 1,500 W | **12 A (Max L1)** | 1,440 W |

- **Start Threshold**: Generation must stay **>= 1,700 W** for **120 seconds** before waking the vehicle and starting charging.
- **Stop Threshold**: If generation drops **< 1,100 W**, current immediately steps down to 5A. If it remains below 1,100W for **180 seconds** (passing cloud debounce), charging stops and vehicle enters deep sleep.
- **Anti-Vampire-Drain**: When solar is low or at night, the car is **never woken up**.

---

## Connecting Your Model 3 (Recommended: Tessie API)

1. Open `http://<LXC-IP>:8080`.
2. Under **Vehicle Integration**, ensure **Tessie API** is selected.
3. Paste your Tessie API key from **dash.tessie.com/settings/api**.
4. Click **Save Vehicle** and then **Test Connection**.
5. Your Model 3 will immediately connect, showing live battery % and status.

---

## Running the Automated Test Suite

To run the internal verification test suite:

```bash
pytest tests/ -v
```

All 23 unit, integration, and end-to-end tests will execute in under 4 seconds.
