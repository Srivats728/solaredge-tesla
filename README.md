# SolarEdge ↔ Tesla Solar Self-Consumption Optimizer ☀️🚗

An autonomous, LAN-based solar charging manager for your **Tesla Model 3** powered directly by your **SolarEdge Inverter** over **Modbus TCP**, with a built-in **Web Dashboard**.

Designed specifically for **Level 1 (120V)** charging without a grid consumption meter, maximizing free solar self-consumption while avoiding drawing power from the grid.

---

## ⚡ 1-Step Installation (Proxmox VE Shell)

Open the **Shell** on your Proxmox VE host (or inside an Alpine LXC) and paste this single command:

```bash
curl -sL https://raw.githubusercontent.com/Srivats728/solaredge-tesla/main/deploy/create_lxc.sh | bash
```

### What this command does automatically:
1. Downloads the latest Alpine Linux LXC template.
2. Creates an unprivileged, lightweight container (512 MB RAM, 1 vCPU, 2 GB disk).
3. Connects the container to your local network via DHCP.
4. Clones and installs all dependencies in an isolated Python virtual environment.
5. Starts the OpenRC background service and launches the **Web Dashboard on port 8080**.
6. Prints the exact web address to open in your browser (e.g., `http://192.168.40.xxx:8080`).

---

## 🖥️ Web Dashboard (Port 8080)

Once installed, open `http://<CONTAINER-IP>:8080` in your web browser:

![Dashboard Features](https://img.shields.io/badge/Web%20Dashboard-Port%208080-blue?style=for-the-badge)

- 📊 **Live Telemetry**: Real-time display of SolarEdge generation (Watts & kW), inverter status, and Tesla charging current (Amps), battery %, and voltage.
- 🚗 **1-Click Tesla Setup**: Generate official Tesla login links and complete authorization securely right from your browser without terminal commands.
- 🔍 **Modbus Connection Tester**: Run a diagnostic ping against `192.168.40.169:1502` and see live register responses with one click.
- ⚙️ **Web Settings**: Adjust start thresholds, house baseline estimates, min/max amps, and WhatsApp alerts directly with live saving.
- 🎛️ **Manual Mode Overrides**: Toggle between **Auto (Solar Only)**, **Force 12A (Continuous)**, and **Stop Charging**.
- 📜 **Live Logs**: View real-time service logs directly inside the web UI.

---

## 📐 Charging Logic & Math

$$\text{Available Power for Car} = \text{Solar Generation} - \text{Household Baseline}$$

$$\text{Target Amps} = \left\lfloor \frac{\text{Available Power}}{120\text{ V}} \right\rfloor \quad (\text{clamped between } 5\text{A and } 12\text{A})$$

| Solar Output | Baseline House | Available for Car | Level 1 Current | Effective Car Power |
| :--- | :--- | :--- | :--- | :--- |
| **< 1,100 W** | 600 W | < 500 W | **0 A (Stopped)** | 0 W |
| **1,300 W** | 600 W | 700 W | **5 A** | 600 W |
| **1,500 W** | 600 W | 900 W | **7 A** | 840 W |
| **1,800 W** | 600 W | 1,200 W | **10 A** | 1,200 W |
| **≥ 2,100 W** | 600 W | ≥ 1,500 W | **12 A (Max L1)** | 1,440 W |

- **Start Threshold**: Generation must stay **$\ge 1,700$ W** for **120 seconds** before waking the car and starting charging.
- **Stop Threshold**: If generation drops **$< 1,100$ W**, current immediately drops to 5A. If it stays under 1,100 W for **180 seconds** (passing cloud debounce), charging stops and the car enters deep sleep.
- **Anti-Vampire-Drain**: When solar is low or at night, the car is **never woken up**.

---

## 🛠️ SolarEdge Inverter Configuration Checklist

When you install your Wi-Fi antenna:
1. Connect to the inverter using the **SolarEdge SetApp** mobile app.
2. Go to **Communication** → **RS485** or **LAN / Wi-Fi**.
3. Under **Modbus TCP**:
   - Status: **Enabled**
   - Port: **1502** (default)
4. Confirm the inverter IP is `192.168.40.169` (recommended: reserve this in your router's DHCP).
5. Open the Web Dashboard → Click **Modbus Tester** tab → Click **Run Modbus TCP Test Now** to verify.

---

## 📱 WhatsApp Notifications Setup (CallMeBot)

If you'd like free WhatsApp alerts when charging starts, stops, or adjusts:
1. Add **`+34 910 60 41 44`** to your phone's WhatsApp contacts (name it "CallMeBot").
2. Send: **`I allow callmebot to send me messages`** to that contact in WhatsApp.
3. Wait for the bot to reply with your **API Key**.
4. In the Web Dashboard **Settings** tab, check **Enable WhatsApp Alerts** and enter your phone number and API key.

---

## 📂 Project Structure

```
solaredge-tesla/
├── web/
│   ├── app.py                # Flask Web Dashboard & REST API
│   └── templates/
│       └── index.html        # Modern responsive dark-mode UI
├── config.yaml               # Active parameters
├── config.example.yaml       # Template configuration
├── requirements.txt          # Python dependencies (teslapy, pymodbus, flask, etc.)
├── main.py                   # Daemon & web service entry point
├── solaredge.py              # SunSpec Modbus TCP client for SolarEdge
├── tesla_client.py           # TeslaPy wrapper with anti-vampire-drain logic
├── controller.py             # Charging algorithm, debouncing & dynamic amps
├── notifier.py               # WhatsApp alert handler (CallMeBot / Twilio)
├── test_modbus.py            # Modbus connection tester CLI
├── authenticate_tesla.py     # Interactive headless Tesla OAuth login CLI
└── deploy/
    ├── create_lxc.sh         # Proxmox VE 1-step turnkey installer
    ├── install_alpine.sh     # Alpine container setup script
    ├── solaredge-tesla.initd # OpenRC background service script
    ├── Dockerfile            # Container definition
    └── docker-compose.yml    # Docker Compose definition
```
